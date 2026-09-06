"""M15-T2 — the restore drill.

A backup you have never restored is not a backup, and a drill that can only
say PASS is not a drill. So this runs the real thing end to end against a real
dump, and then proves it fails on a dump that is not one.

Needs the local Postgres (roadmap §2). No skip marker: an unreachable DB fails.
"""
import asyncio
import importlib.util
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.config import Settings, get_settings
from app.jobs import backup as backup_job
from app.jobs.backup import latest_dump, run_backup, source_url

DRILL_PATH = Path(__file__).resolve().parents[2] / "scripts" / "restore-drill.py"
NOW = datetime(2026, 9, 6, 19, 0, tzinfo=timezone.utc)


def _load_drill():
    """scripts/ is not a package, so the drill is loaded by path. It has to be
    in sys.modules before it executes or its dataclasses cannot resolve their
    own module."""
    spec = importlib.util.spec_from_file_location("restore_drill", DRILL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


drill_module = _load_drill()


@pytest.fixture
def configure(monkeypatch):
    def apply(**overrides) -> Settings:
        settings = get_settings().model_copy(update=overrides)
        monkeypatch.setattr(backup_job, "get_settings", lambda: settings)
        return settings

    return apply


# ── URL handling ─────────────────────────────────────────────────────────────

def test_switching_database_keeps_the_driver():
    """alembic runs through SQLAlchemy and needs `+asyncpg` on the scheme; the
    client tools and asyncpg itself need it gone. Losing it here made the whole
    schema-revision phase fail with a missing psycopg2."""
    switched = drill_module.with_database(
        "postgresql+asyncpg://postgres:pw@localhost:5432/warung_pintar", "wp_drill_x"
    )
    assert switched == "postgresql+asyncpg://postgres:pw@localhost:5432/wp_drill_x"
    assert drill_module.database_of(switched) == "wp_drill_x"


def test_the_asyncpg_dsn_drops_the_driver_and_the_query():
    assert drill_module.asyncpg_url(
        "postgresql+asyncpg://u:p@host:5432/db?sslmode=require"
    ) == "postgresql://u:p@host:5432/db"


def test_a_missing_database_name_falls_back_to_postgres():
    assert drill_module.database_of("postgresql://u:p@host:5432/") == "postgres"


def test_same_cluster_ignores_the_database_and_the_driver():
    live = "postgresql+asyncpg://u:p@db.supabase.co:5432/postgres"
    assert drill_module.same_cluster(live, "postgresql://u:p@db.supabase.co:5432/scratch")
    assert not drill_module.same_cluster(live, "postgresql://u:p@localhost:5432/scratch")


def test_alembic_revisions_are_read_past_the_log_lines():
    assert drill_module._revision(
        "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\n"
        "INFO  [alembic.runtime.migration] Will assume transactional DDL.\n"
        "0029 (head)\n"
    ) == "0029"
    assert drill_module._revision("") is None


def test_the_pytest_summary_line_is_what_gets_reported():
    assert drill_module._last_line("....\n\n12 passed in 1.98s\n") == "12 passed in 1.98s"


# ── the drill itself ─────────────────────────────────────────────────────────

@pytest.fixture
def a_real_dump(configure, tmp_path) -> Path:
    configure(backup_dir=str(tmp_path / "offsite"), environment="development")
    result = run_backup(NOW)
    assert result.ok, result.error
    dump = latest_dump(tmp_path / "offsite")
    assert dump is not None
    return dump


def _run(dump: Path):
    settings = get_settings()
    return asyncio.run(
        drill_module.drill(
            dump=dump,
            admin_url=source_url(),
            app_role_url=settings.database_url,
            source=source_url(),
            keep=False,
        )
    )


def test_the_drill_restores_checks_and_passes(a_real_dump):
    """The deliverable: a dump goes into a scratch database, the schema is the
    revision this code expects, `test_invariants.py` passes against it as
    app_role with RLS enforced, and every table that has rows in the source has
    rows in the restore."""
    result = _run(a_real_dump)

    failed = [f"{p.name}: {p.detail}" for p in result.phases if not p.ok]
    assert result.ok, "phases failed: " + " | ".join(failed)
    names = [p.name for p in result.phases]
    assert names == [
        "create scratch database", "pg_restore", "schema revision",
        "invariants", "row counts", "drop scratch database",
    ]

    by_name = {p.name: p for p in result.phases}
    assert "passed" in by_name["invariants"].detail
    # Whatever the head revision is today, the restore must be at it.
    restored_at, expected = re.fullmatch(
        r"restored at (\S+), code expects (\S+)", by_name["schema revision"].detail
    ).groups()
    assert restored_at == expected

    restored = {table: here for table, here, _ in result.counts}
    for table in ("businesses", "orders", "order_lines", "journal_lines", "stock_movements"):
        assert restored.get(table, 0) > 0, f"{table} came back empty"
    assert result.seconds > 0


def test_the_drill_fails_on_something_that_is_not_a_dump(tmp_path):
    """A drill that cannot fail proves nothing."""
    junk = tmp_path / "warung-pintar-20260906T000000Z.dump"
    junk.write_bytes(b"0" * 20_000)

    result = _run(junk)

    assert result.ok is False
    by_name = {p.name: p for p in result.phases}
    assert by_name["pg_restore"].ok is False
    assert by_name["pg_restore"].detail
    # The scratch database is still cleaned up after a failure.
    assert by_name["drop scratch database"].ok is True
    # And nothing downstream is reported as having passed.
    assert "schema revision" not in by_name and "invariants" not in by_name
