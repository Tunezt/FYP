"""M15-T1 — automated backup, off the primary host.

The half of this that matters is the failure path: a backup that breaks quietly
is the same as no backup. So these tests spend most of their effort on what
happens when the dump does not work — a missing binary, the wrong database
role, a truncated file, a destination that is not actually off-host — and on
proving the owner is told.

Needs the local Postgres (roadmap §2). No skip marker: an unreachable DB fails.
"""
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.jobs import backup as backup_job
from app.jobs.backup import (
    BackupError,
    BackupMisconfigured,
    BackupResult,
    REQUIRED_TABLES,
    alert_message,
    alert_rule_key,
    append_run_log,
    check_destination,
    connection_env,
    destination,
    last_success,
    prune,
    raise_backup_alert,
    read_run_log,
    resolve_binary,
    run_backup,
    source_url,
    toc_tables,
    verify,
)
from app.models import Alert, Business

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 9, 6, 19, 0, tzinfo=timezone.utc)  # 02:00 WIB, the cron slot


# ── settings plumbing ────────────────────────────────────────────────────────

def settings_with(**overrides) -> Settings:
    """A copy of the real settings with fields replaced — the job reads settings
    through `get_settings()`, so tests swap that rather than mutate the cache."""
    base = get_settings()
    return base.model_copy(update=overrides)


@pytest.fixture
def configure(monkeypatch):
    def apply(**overrides) -> Settings:
        settings = settings_with(**overrides)
        monkeypatch.setattr(backup_job, "get_settings", lambda: settings)
        return settings

    return apply


@pytest.fixture
def dest(tmp_path) -> Path:
    return tmp_path / "offsite"


def _dump_files(dest: Path, folder: str = "daily") -> list[Path]:
    return sorted((dest / folder).glob("warung-pintar-*.dump"))


# ── the happy path ───────────────────────────────────────────────────────────

def test_a_run_dumps_verifies_promotes_and_logs(configure, dest):
    configure(backup_dir=str(dest), environment="development")

    result = run_backup(NOW)

    assert result.ok, result.error
    dumps = _dump_files(dest)
    assert len(dumps) == 1
    assert dumps[0].name == "warung-pintar-20260906T190000Z.dump"
    assert result.bytes == dumps[0].stat().st_size > get_settings().backup_min_bytes
    # Every table the application depends on is in the archive, not just some of them.
    assert result.tables >= len(REQUIRED_TABLES)
    # The first success of a calendar month is kept as that month's copy.
    assert _dump_files(dest, "monthly") == [dest / "monthly" / "warung-pintar-202609.dump"]
    # Nothing half-written is left behind under a name that looks finished.
    assert list((dest / "daily").glob("*.partial")) == []

    records = read_run_log(dest)
    assert len(records) == 1
    assert records[0]["ok"] is True
    assert records[0]["tables"] == result.tables
    assert records[0]["error"] is None
    assert last_success(dest) == result.finished_at.isoformat()


def test_the_dump_restores_its_table_of_contents(configure, dest):
    """`pg_restore --list` has to be able to read the file back, and the listing
    has to name the tables. This is the cheap half of a restore test; the drill
    that actually restores is M15-T2."""
    configure(backup_dir=str(dest), environment="development")
    result = run_backup(NOW)
    assert result.ok, result.error

    listing = subprocess.run(
        [str(resolve_binary("pg_restore")), "--list", result.path],
        capture_output=True, text=True, timeout=120,
    )
    assert listing.returncode == 0
    tables = toc_tables(listing.stdout)
    assert set(REQUIRED_TABLES) <= tables


def test_the_month_keeps_its_first_dump_not_its_latest(configure, dest):
    configure(backup_dir=str(dest), environment="development")
    first = run_backup(NOW)
    second = run_backup(NOW + timedelta(days=1))

    assert first.ok and second.ok
    assert first.monthly is not None
    assert second.monthly is None, "the month already had a copy"
    assert len(_dump_files(dest)) == 2
    assert len(_dump_files(dest, "monthly")) == 1
    assert len(read_run_log(dest)) == 2


# ── retention ────────────────────────────────────────────────────────────────

def _touch_dumps(folder: Path, stamps: list[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for stamp in stamps:
        (folder / f"warung-pintar-{stamp}.dump").write_bytes(b"x")


def test_retention_keeps_thirty_dailies_and_six_monthlies(dest):
    _touch_dumps(dest / "daily", [f"202608{day:02d}T020000Z" for day in range(1, 32)])  # 31
    _touch_dumps(dest / "monthly", [f"2026{month:02d}" for month in range(1, 9)])       # 8

    removed = prune(dest, keep_daily=30, keep_monthly=6, now=NOW)

    dailies = [p.name for p in _dump_files(dest)]
    monthlies = [p.name for p in _dump_files(dest, "monthly")]
    assert len(dailies) == 30 and len(monthlies) == 6
    # The oldest go, the newest stay.
    assert "warung-pintar-20260801T020000Z.dump" not in dailies
    assert "warung-pintar-20260831T020000Z.dump" in dailies
    assert monthlies == [f"warung-pintar-2026{month:02d}.dump" for month in range(3, 9)]
    assert set(removed) == {
        "daily/warung-pintar-20260801T020000Z.dump",
        "monthly/warung-pintar-202601.dump",
        "monthly/warung-pintar-202602.dump",
    }


def test_retention_leaves_a_short_history_alone(dest):
    _touch_dumps(dest / "daily", ["20260901T020000Z", "20260902T020000Z"])
    assert prune(dest, keep_daily=30, keep_monthly=6, now=NOW) == []
    assert len(_dump_files(dest)) == 2


def test_a_crashed_run_does_not_leave_partials_forever(dest):
    daily = dest / "daily"
    daily.mkdir(parents=True)
    stale = daily / "warung-pintar-20260901T020000Z.partial"
    fresh = daily / "warung-pintar-20260906T020000Z.partial"
    for path in (stale, fresh):
        path.write_bytes(b"half a dump")
    old = (NOW - timedelta(hours=48)).timestamp()
    os.utime(stale, (old, old))
    now_ts = NOW.timestamp()
    os.utime(fresh, (now_ts, now_ts))

    removed = prune(dest, keep_daily=30, keep_monthly=6, now=NOW)

    assert removed == ["daily/warung-pintar-20260901T020000Z.partial"]
    assert not stale.exists() and fresh.exists()


# ── the failure path ─────────────────────────────────────────────────────────

def test_a_missing_pg_dump_fails_the_run_and_is_logged(configure, dest, tmp_path, monkeypatch):
    empty = tmp_path / "no-postgres-here"
    empty.mkdir()
    configure(backup_dir=str(dest), environment="development", pg_bin_dir=str(empty))
    # pg_dump is looked up in PG_BIN_DIR, then PATH, then the local cluster —
    # hide all three, which is what a Railway image without the client tools is.
    monkeypatch.setattr(backup_job.shutil, "which", lambda name: None)
    monkeypatch.setattr(backup_job, "REPO_ROOT", empty)

    result = run_backup(NOW)

    assert result.ok is False
    assert "pg_dump not found" in result.error
    assert _dump_files(dest) == []
    records = read_run_log(dest)
    assert len(records) == 1 and records[0]["ok"] is False
    assert "pg_dump not found" in records[0]["error"]


def test_a_failed_run_remembers_the_last_good_one(configure, dest):
    configure(backup_dir=str(dest), environment="development")
    good = run_backup(NOW)
    assert good.ok

    append_run_log(dest, {"ok": False, "finished_at": "later", "error": "boom"})
    failure = BackupResult(
        ok=False, started_at=NOW, finished_at=NOW, error="pg_dump exited 1",
        last_success_before=last_success(dest),
    )
    assert failure.last_success_before == good.finished_at.isoformat()


def test_a_truncated_dump_is_not_accepted(configure, dest):
    configure(backup_dir=str(dest), environment="development")
    result = run_backup(NOW)
    assert result.ok

    path = Path(result.path)
    path.write_bytes(path.read_bytes()[:100])
    with pytest.raises(BackupError, match="under the .* floor"):
        verify(path, get_settings().backup_min_bytes)


def test_a_corrupt_dump_is_not_accepted(configure, dest, tmp_path):
    configure(backup_dir=str(dest), environment="development")
    junk = tmp_path / "not-a-dump.dump"
    junk.write_bytes(b"0" * 20_000)
    with pytest.raises(BackupError, match="could not read the dump"):
        verify(junk, get_settings().backup_min_bytes)


def test_a_dump_missing_a_table_is_not_accepted(configure, dest, monkeypatch):
    """The size floor and a readable header are not enough — the archive has to
    contain the application's tables."""
    configure(backup_dir=str(dest), environment="development")
    result = run_backup(NOW)
    assert result.ok

    complete = subprocess.run(
        [str(resolve_binary("pg_restore")), "--list", result.path],
        capture_output=True, text=True, timeout=120,
    ).stdout
    without_orders = "\n".join(
        line for line in complete.splitlines() if "TABLE DATA public orders " not in line
    )
    monkeypatch.setattr(
        backup_job.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, without_orders, ""),
    )
    with pytest.raises(BackupError, match="no table data for: orders"):
        verify(Path(result.path), get_settings().backup_min_bytes)


def test_the_restricted_role_cannot_take_the_backup(configure, dest):
    """pg_dump runs with `row_security = off`, so `app_role` fails outright
    instead of quietly writing a dump with no tenant's rows in it. That failure
    mode is the reason BACKUP_DATABASE_URL never falls back to DATABASE_URL.

    This also guards roadmap §1.2 from the other side: if it ever passes,
    DATABASE_URL has been pointed at a role with BYPASSRLS.
    """
    settings = get_settings()
    configure(
        backup_dir=str(dest), environment="development",
        backup_database_url=settings.database_url,
    )

    result = run_backup(NOW)

    assert result.ok is False, "DATABASE_URL dumped the whole database — it is not app_role"
    assert "row-level security" in result.error or "permission denied" in result.error
    assert _dump_files(dest) == []


def test_the_backup_url_never_falls_back_to_the_restricted_one(configure):
    configure(backup_database_url=None, migration_database_url=None)
    with pytest.raises(BackupMisconfigured, match="BACKUP_DATABASE_URL"):
        source_url()


# ── off the primary host ─────────────────────────────────────────────────────

def test_production_needs_a_destination(configure):
    configure(backup_dir=None, environment="production")
    with pytest.raises(BackupMisconfigured, match="BACKUP_DIR is unset"):
        destination()


def test_production_refuses_the_deployment_tree(configure):
    inside = backup_job.REPO_ROOT / ".backups"
    with pytest.raises(BackupMisconfigured, match="inside the deployment tree"):
        check_destination(inside, "production", "postgresql+asyncpg://u:p@db.supabase.co:5432/postgres")


def test_production_refuses_a_local_destination_for_a_local_database(configure, tmp_path):
    with pytest.raises(BackupMisconfigured, match="not off-host"):
        check_destination(tmp_path, "production", "postgresql+asyncpg://u:p@localhost:5432/warung_pintar")


def test_production_accepts_a_real_off_host_destination(tmp_path):
    check_destination(tmp_path, "production", "postgresql+asyncpg://u:p@db.supabase.co:5432/postgres")


def test_development_uses_the_repo_default_and_is_not_checked(configure):
    """The development default is deliberately *not* off-host. The check exists
    so that turning ENVIRONMENT=production refuses it."""
    configure(backup_dir=None, environment="development")
    assert destination() == backup_job.DEV_BACKUP_DIR
    with pytest.raises(BackupMisconfigured):
        check_destination(backup_job.DEV_BACKUP_DIR, "production", "postgresql://u:p@db.supabase.co:5432/x")


# ── connection handling ──────────────────────────────────────────────────────

def test_the_password_stays_out_of_the_command_line():
    env = connection_env("postgresql+asyncpg://app.ref:%40Secret300706@aws-1.pooler.supabase.com:5432/postgres")
    assert env["PGHOST"] == "aws-1.pooler.supabase.com"
    assert env["PGPORT"] == "5432"
    assert env["PGUSER"] == "app.ref"
    assert env["PGPASSWORD"] == "@Secret300706"   # percent-decoded, as libpq wants it
    assert env["PGDATABASE"] == "postgres"
    assert env["PGSSLMODE"] == "require"          # a remote database is never dumped in clear


def test_a_local_database_is_not_forced_onto_tls():
    env = connection_env("postgresql+asyncpg://postgres:postgres@localhost:5432/warung_pintar")
    assert "PGSSLMODE" not in env
    assert env["PGDATABASE"] == "warung_pintar"


def test_an_explicit_sslmode_wins():
    env = connection_env("postgresql://u:p@host:5432/db?sslmode=verify-full")
    assert env["PGSSLMODE"] == "verify-full"


def test_a_url_that_is_not_postgres_is_refused():
    with pytest.raises(BackupMisconfigured, match="not a Postgres URL"):
        connection_env("mysql://u:p@host/db")


def test_toc_parsing_reads_table_names_and_ignores_comments():
    listing = "\n".join([
        ";",
        "; Archive created at 2026-09-06 02:00:00",
        ";     dbname: warung_pintar",
        "216; 1259 16456 TABLE public orders app_role",
        "4321; 0 16456 TABLE DATA public orders app_role",
        "4322; 0 16460 TABLE DATA public order_lines app_role",
        "; 4323; 0 16461 TABLE DATA public commented_out app_role",
    ])
    assert toc_tables(listing) == {"orders", "order_lines"}


# ── telling the owner ────────────────────────────────────────────────────────

@pytest.fixture
async def engine():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def business(session_factory):
    """One café, with the books a real registration gives it, removed again on
    the way out so the invariant suite does not inherit test tenants."""
    async with session_factory() as session:
        row = Business(
            name="Warung Backup",
            owner_phone=f"628{uuid.uuid4().int % 10**10:010d}",
            timezone="Asia/Jakarta",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    async with session_factory() as session:
        await _scoped(session, row.id)
        await seed_books(session, row.id)
        await session.commit()

    yield row

    async with session_factory() as session:
        stale = await session.get(Business, row.id)
        if stale:
            await session.delete(stale)
        await session.commit()


async def _scoped(session, business_id) -> None:
    await session.execute(
        text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)}
    )


def _failure(started: datetime, last_ok: str | None = None) -> BackupResult:
    return BackupResult(
        ok=False, started_at=started, finished_at=started,
        error="BackupError: pg_dump exited 1: could not connect to server",
        last_success_before=last_ok,
    )


async def test_a_failed_backup_alerts_the_owner_once_a_day(session_factory, business):
    result = _failure(NOW, last_ok="2026-09-05T19:00:12+00:00")

    async with session_factory() as session:
        await _scoped(session, business.id)
        assert await raise_backup_alert(session, business, result) is True
        # A retry the same day must not say it twice.
        assert await raise_backup_alert(session, business, result) is False
        await session.commit()

    async with session_factory() as session:
        await _scoped(session, business.id)
        alerts = (
            (await session.execute(select(Alert).where(Alert.business_id == business.id)))
            .scalars().all()
        )
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.type == "backup_failed"
    assert alert.severity == "high"
    assert alert.rule_key == "backup:2026-09-07"        # 02:00 WIB is already the 7th
    assert alert.details["last_success_before"] == "2026-09-05T19:00:12+00:00"
    assert alert.is_sent is False


async def test_a_backup_failing_every_night_is_said_every_night(session_factory, business):
    """The M10-T2 policy suppresses a subject alerted in the last seven days.
    Backups are exempt on purpose: six silent nights is how a café loses a week."""
    async with session_factory() as session:
        await _scoped(session, business.id)
        for day in range(3):
            assert await raise_backup_alert(session, business, _failure(NOW + timedelta(days=day)))
        await session.commit()

    async with session_factory() as session:
        await _scoped(session, business.id)
        alerts = (
            (await session.execute(select(Alert).where(Alert.business_id == business.id)))
            .scalars().all()
        )
    assert len(alerts) == 3
    assert {a.rule_key for a in alerts} == {
        "backup:2026-09-07", "backup:2026-09-08", "backup:2026-09-09"
    }


async def test_the_alert_reaches_the_phone_as_a_template(session_factory, business, monkeypatch):
    """Delivery is the approved Utility template, because this runs from cron,
    outside the 24-hour session window (roadmap §1.9)."""
    from app.jobs import delivery

    sent: list[tuple] = []

    async def fake_send_template(to, template, params, language=None):
        sent.append((to, template, params, language))

    monkeypatch.setattr(delivery, "send_template", fake_send_template)

    async with session_factory() as session:
        await _scoped(session, business.id)
        await raise_backup_alert(session, business, _failure(NOW))
        delivered = await delivery.deliver_unsent(session, business)
        await session.commit()

    assert delivered == 1
    assert len(sent) == 1
    to, template, params, language = sent[0]
    assert to == business.owner_phone
    assert template == get_settings().whatsapp_alert_template
    assert params[0] == "backup gagal"
    assert params[1] == business.name
    assert "Backup otomatis database GAGAL" in params[2]

    async with session_factory() as session:
        await _scoped(session, business.id)
        alert = (
            await session.execute(select(Alert).where(Alert.business_id == business.id))
        ).scalars().one()
    assert alert.is_sent is True


def test_the_message_is_indonesian_and_names_the_last_good_backup():
    business = Business(name="Kopi Senja", owner_phone="628123", timezone="Asia/Jakarta")
    with_history = alert_message(_failure(NOW, last_ok="2026-09-05T19:00:00+00:00"), business)
    assert "Backup otomatis database GAGAL pada 07/09 02:00" in with_history
    assert "Backup terakhir yang berhasil: 06/09 02:00." in with_history
    assert "hubungi teknisi" in with_history

    never = alert_message(_failure(NOW), business)
    assert "Belum pernah ada backup yang berhasil." in never


def test_the_alert_day_follows_the_business_timezone():
    """02:00 Jakarta on the 7th is 19:00 UTC on the 6th. The owner's day is the
    one that decides whether this is a new alert."""
    jakarta = Business(name="A", owner_phone="1", timezone="Asia/Jakarta")
    utc = Business(name="B", owner_phone="2", timezone="UTC")
    assert alert_rule_key(_failure(NOW), jakarta) == "backup:2026-09-07"
    assert alert_rule_key(_failure(NOW), utc) == "backup:2026-09-06"
