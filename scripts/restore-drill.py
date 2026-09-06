"""Restore drill — proves the backup is a backup (roadmap M15-T2).

A backup you have never restored is not a backup. This takes the newest dump
that app.jobs.backup produced, restores it into a throwaway database, and then
does the three things that separate "a file exists" from "the cafe could be
back on its feet":

  1. restores it, mirroring the real sequence: create the database, apply
     scripts/db-bootstrap.sql so `app_role` exists with its grants, pg_restore
  2. checks the restored schema is the revision this code expects, and that
     `alembic upgrade head` against it is a clean no-op
  3. runs backend/tests/test_invariants.py against the restored database, as
     `app_role`, so RLS is enforced exactly as it is in production
  4. counts every table on both sides and reports the difference

Then it drops the scratch database and prints how long each phase took, because
the number the runbook needs is measured, not estimated.

Usage (backend virtualenv — it imports the app's settings):

    backend/.venv/Scripts/python.exe scripts/restore-drill.py
    ... --dump path/to/warung-pintar-20260906T020000Z.dump
    ... --target-url postgresql://postgres:postgres@localhost:5432/postgres
    ... --keep          # leave the scratch database behind to poke at
    ... --json          # machine-readable result on stdout

Exit code is 0 only if every phase passed.

The drill restores somewhere that is *not* production — that is the point of
it. `--target-url` names the cluster the scratch database is created on; it
defaults to the same cluster the dump came from, which is right for local
development and wrong for a live one, so a production source refuses unless
`--allow-same-cluster` says otherwise.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
BOOTSTRAP_SQL = REPO_ROOT / "scripts" / "db-bootstrap.sql"

sys.path.insert(0, str(BACKEND))

# The app's settings read `.env` relative to the working directory, and every
# command in this repo runs from backend/. Match that before importing them,
# and keep the caller's directory so a relative --dump still means what they typed.
ORIGINAL_CWD = Path.cwd()
if ORIGINAL_CWD != BACKEND:
    os.chdir(BACKEND)

try:
    import asyncpg
    from app.core.config import get_settings
    from app.jobs.backup import (
        BackupMisconfigured,
        connection_env,
        destination,
        latest_dump,
        resolve_binary,
        source_url,
    )
except ImportError as exc:  # pragma: no cover - a wrong interpreter, not a code path
    raise SystemExit(
        f"{exc}\n\nRun this with the backend virtualenv:\n"
        r"    backend\.venv\Scripts\python.exe scripts\restore-drill.py"
    ) from exc


def venv_python() -> str:
    """The interpreter alembic and pytest must run under."""
    for candidate in (BACKEND / ".venv/Scripts/python.exe", BACKEND / ".venv/bin/python"):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def sync_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def with_database(url: str, database: str) -> str:
    """Same cluster and credentials, different database. The scheme is left
    alone — alembic needs the `+asyncpg` driver on it, and the client tools
    never see this form."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def database_of(url: str) -> str:
    return urlsplit(url).path.lstrip("/") or "postgres"


def same_cluster(one: str, other: str) -> bool:
    """Host and port, ignoring which database and which driver."""
    a, b = urlsplit(sync_url(one)), urlsplit(sync_url(other))
    return (a.hostname, a.port or 5432) == (b.hostname, b.port or 5432)


def asyncpg_url(url: str) -> str:
    """asyncpg wants a plain postgresql:// DSN with no driver suffix and no
    query string it cannot read."""
    parts = urlsplit(sync_url(url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


@dataclass
class Phase:
    name: str
    seconds: float
    ok: bool
    detail: str = ""


@dataclass
class Drill:
    dump: Path
    scratch: str
    phases: list[Phase] = field(default_factory=list)
    counts: list[tuple[str, int, int]] = field(default_factory=list)
    ok: bool = True

    def record(self, name: str, started: float, ok: bool, detail: str = "") -> None:
        self.phases.append(Phase(name, time.perf_counter() - started, ok, detail))
        if not ok:
            self.ok = False

    @property
    def seconds(self) -> float:
        return sum(p.seconds for p in self.phases)


# -- the steps ----------------------------------------------------------------

async def create_scratch(admin_url: str, name: str) -> None:
    conn = await asyncpg.connect(asyncpg_url(with_database(admin_url, "postgres")))
    try:
        await conn.execute(f'create database "{name}"')
    finally:
        await conn.close()


async def drop_scratch(admin_url: str, name: str) -> None:
    conn = await asyncpg.connect(asyncpg_url(with_database(admin_url, "postgres")))
    try:
        await conn.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity where datname = $1", name
        )
        await conn.execute(f'drop database if exists "{name}"')
    finally:
        await conn.close()


async def apply_bootstrap(url: str) -> None:
    conn = await asyncpg.connect(asyncpg_url(url))
    try:
        await conn.execute(BOOTSTRAP_SQL.read_text(encoding="utf-8"))
    finally:
        await conn.close()


async def table_counts(url: str) -> dict[str, int]:
    """Real counts, not planner estimates, read with the elevated role so RLS
    does not hide the very rows the drill is checking for."""
    conn = await asyncpg.connect(asyncpg_url(url))
    try:
        names = [
            row["table_name"]
            for row in await conn.fetch(
                "select table_name from information_schema.tables "
                "where table_schema = 'public' and table_type = 'BASE TABLE' "
                "order by table_name"
            )
        ]
        return {name: await conn.fetchval(f'select count(*) from "{name}"') for name in names}
    finally:
        await conn.close()


def run(command: list[str], env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        env={**os.environ, **(env or {})},
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )


def restore(dump: Path, url: str) -> subprocess.CompletedProcess:
    """`--no-owner` because the dump's owner need not exist on the target;
    `--single-transaction` so a broken dump leaves nothing half-restored and
    any error at all fails the drill instead of scrolling past as a warning."""
    return run(
        [
            str(resolve_binary("pg_restore")),
            "--dbname", database_of(url),   # host, user and password come from the environment
            "--no-owner",
            "--single-transaction",
            str(dump),
        ],
        env=connection_env(url),
    )


def alembic(args: list[str], url: str) -> subprocess.CompletedProcess:
    return run([venv_python(), "-m", "alembic", *args],
               env={"MIGRATION_DATABASE_URL": url, "DATABASE_URL": url}, cwd=BACKEND)


def invariants(app_url: str) -> subprocess.CompletedProcess:
    return run(
        [venv_python(), "-m", "pytest", "tests/test_invariants.py", "-q", "--no-header"],
        env={"INTEGRATION_DATABASE_URL": app_url},
        cwd=BACKEND,
    )


# -- the drill ----------------------------------------------------------------

async def drill(dump: Path, admin_url: str, app_role_url: str, source: str, keep: bool) -> Drill:
    scratch = f"wp_drill_{datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S')}"
    result = Drill(dump=dump, scratch=scratch)
    scratch_admin = with_database(admin_url, scratch)
    scratch_app = with_database(app_role_url, scratch)

    started = time.perf_counter()
    try:
        await create_scratch(admin_url, scratch)
        await apply_bootstrap(scratch_admin)
        result.record("create scratch database", started, True, scratch)
    except Exception as exc:
        result.record("create scratch database", started, False, f"{type(exc).__name__}: {exc}")
        return result

    try:
        started = time.perf_counter()
        proc = restore(dump, scratch_admin)
        result.record(
            "pg_restore", started, proc.returncode == 0,
            f"{dump.stat().st_size:,} bytes" if proc.returncode == 0 else (proc.stderr or "").strip()[:600],
        )

        if result.ok:
            started = time.perf_counter()
            current = alembic(["current"], scratch_admin)
            heads = alembic(["heads"], scratch_admin)
            at = _revision(current.stdout)
            want = _revision(heads.stdout)
            matched = bool(at) and at == want
            detail = f"restored at {at or '?'}, code expects {want or '?'}"
            if matched:
                upgrade = alembic(["upgrade", "head"], scratch_admin)
                matched = upgrade.returncode == 0
                if not matched:
                    detail = (upgrade.stderr or "").strip()[:600]
            result.record("schema revision", started, matched, detail)

        if result.ok:
            started = time.perf_counter()
            proc = invariants(scratch_app)
            summary = _last_line(proc.stdout)
            result.record("invariants", started, proc.returncode == 0,
                          summary if proc.returncode == 0 else (proc.stdout or "")[-900:])

        started = time.perf_counter()
        restored = await table_counts(scratch_admin)
        live = await table_counts(source)
        emptied = []
        for table in sorted(set(restored) | set(live)):
            here, there = restored.get(table, 0), live.get(table, 0)
            result.counts.append((table, here, there))
            if there > 0 and here == 0:
                emptied.append(table)
        result.record(
            "row counts", started, not emptied,
            "restored empty but populated in the source: " + ", ".join(emptied) if emptied
            else f"{len(restored)} tables, {sum(restored.values()):,} rows",
        )
    finally:
        if keep:
            print(f"\n[keep] scratch database left in place: {scratch}")
        else:
            started = time.perf_counter()
            try:
                await drop_scratch(admin_url, scratch)
                result.record("drop scratch database", started, True)
            except Exception as exc:
                result.record("drop scratch database", started, False, f"{type(exc).__name__}: {exc}")

    return result


def _revision(output: str) -> str | None:
    """First token of alembic's `current` / `heads` output, e.g. `0029 (head)`."""
    for line in output.splitlines():
        token = line.strip().split(" ", 1)[0]
        if token and token[0].isalnum() and not line.startswith("INFO"):
            return token
    return None


def _last_line(output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


# -- reporting ----------------------------------------------------------------

def report(result: Drill, show_all_counts: bool) -> None:
    print()
    print(f"Restore drill  ->  {result.dump.name}")
    print(f"scratch database: {result.scratch}")
    print()
    width = max(len(p.name) for p in result.phases)
    for phase in result.phases:
        mark = "ok  " if phase.ok else "FAIL"
        print(f"  {mark}  {phase.name.ljust(width)}  {phase.seconds:7.2f}s  {phase.detail}")
    print(f"  {'':4}  {'total'.ljust(width)}  {result.seconds:7.2f}s")

    interesting = [row for row in result.counts if show_all_counts or row[1] or row[2]]
    if interesting:
        print()
        print("  table                          restored       source     delta")
        for table, here, there in interesting:
            delta = here - there
            flag = "" if delta == 0 else "   <-- differs"
            print(f"  {table.ljust(28)} {here:>10,} {there:>12,} {delta:>9,}{flag}")
        print()
        print("  A delta is normal: the source keeps trading after the dump is taken.")
        print("  Restored zero against a populated source is not, and fails the drill.")

    print()
    print("PASS" if result.ok else "FAIL")


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore the newest backup into a scratch database and check it.")
    parser.add_argument("--dump", type=Path, help="dump to restore (default: newest in BACKUP_DIR/daily)")
    parser.add_argument("--target-url", help="cluster to create the scratch database on (default: the source cluster)")
    parser.add_argument("--keep", action="store_true", help="leave the scratch database behind")
    parser.add_argument("--allow-same-cluster", action="store_true", help="permit a production source and target on one cluster")
    parser.add_argument("--all-counts", action="store_true", help="list every table, including the empty ones")
    parser.add_argument("--json", action="store_true", help="print the result as JSON as well")
    args = parser.parse_args()

    settings = get_settings()
    given = args.dump
    if given is not None and not given.is_absolute():
        given = ORIGINAL_CWD / given
    try:
        source = source_url()
        dump = given or latest_dump(destination())
    except BackupMisconfigured as exc:
        print(f"FAIL  {exc}")
        return 1
    if dump is None:
        print("FAIL  no dump to restore. Run `python -m app.jobs.backup` first.")
        return 1
    if not dump.is_file():
        print(f"FAIL  no such dump: {dump}")
        return 1

    admin_url = args.target_url or source
    if settings.environment == "production" and same_cluster(admin_url, source) and not args.allow_same_cluster:
        print(
            "FAIL  the drill would create its scratch database on the production cluster.\n"
            "      Pass --target-url pointing at somewhere else (that is the point of a drill),\n"
            "      or --allow-same-cluster if you really mean it."
        )
        return 1

    app_role_url = settings.database_url
    result = asyncio.run(drill(dump, admin_url, app_role_url, source, args.keep))
    report(result, args.all_counts)
    if args.json:
        print(json.dumps({
            "ok": result.ok,
            "dump": str(result.dump),
            "scratch": result.scratch,
            "seconds": round(result.seconds, 2),
            "phases": [{"name": p.name, "seconds": round(p.seconds, 2), "ok": p.ok, "detail": p.detail} for p in result.phases],
            "counts": [{"table": t, "restored": r, "source": s} for t, r, s in result.counts],
        }, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
