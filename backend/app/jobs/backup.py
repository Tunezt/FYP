"""Automated off-host database backup — cron entrypoint: `python -m app.jobs.backup`

Roadmap M15-T1. This project has already lost a database once, on 16 July, to a
free-tier project being reaped. Nothing else in the roadmap matters if that
happens to a café's real takings.

One run does five things, in this order, and records all of them:

  1. `pg_dump --format=custom` of the whole database to `<BACKUP_DIR>/daily/`
  2. verifies the file: a size floor, and a table of contents that actually
     contains every table this application depends on
  3. keeps the first successful dump of each calendar month in `<BACKUP_DIR>/monthly/`
  4. prunes to BACKUP_KEEP_DAILY dailies and BACKUP_KEEP_MONTHLY monthlies
  5. appends one JSON line per run — success or failure — to
     `<BACKUP_DIR>/backup-log.jsonl`

A failed run raises a high-severity `backup_failed` alert per business and
delivers it immediately through the approved WhatsApp template, and the process
exits non-zero so the scheduler sees it too. **A silent backup failure is the
same as no backup**, which is why the failure path is the part with tests.

Two decisions worth knowing:

*The dump needs an elevated role.* `DATABASE_URL` is the restricted `app_role`
(roadmap §1.2) and every business-scoped table forces RLS. `pg_dump` runs with
`row_security = off`, so a restricted role does not quietly produce an empty
dump — it fails outright. That is the good failure mode, and it is why
`BACKUP_DATABASE_URL` falls back to `MIGRATION_DATABASE_URL` and never to
`DATABASE_URL`.

*Off-host is checked, not trusted.* A backup on the database's own host is not a
backup. In production the destination must be an absolute path outside the
deployment tree, and the database must not be on this machine. In development
it defaults to `<repo>/.backups` — which is deliberately *not* off-host, and
which the same check refuses the moment ENVIRONMENT=production.

Scheduling (the schedule itself lands with M12-T4, which creates the Railway
project):

    Railway Cron   `0 19 * * *`  (= 02:00 WIB) running `python -m app.jobs.backup`
    Windows        scripts/schedule-backup.ps1 registers the equivalent daily task

Railway's Nixpacks image needs the Postgres 16 client tools on the cron service
for `pg_dump`/`pg_restore` to exist; set PG_BIN_DIR if they are not on PATH.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import engine, plain_session, tenant_session
from app.jobs.delivery import deliver_unsent
from app.models import Alert, Business

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jobs.backup")

REPO_ROOT = Path(__file__).resolve().parents[3]
DEV_BACKUP_DIR = REPO_ROOT / ".backups"

DUMP_PREFIX = "warung-pintar-"
DUMP_SUFFIX = ".dump"
PARTIAL_SUFFIX = ".partial"
DAILY = "daily"
MONTHLY = "monthly"
RUN_LOG = "backup-log.jsonl"

DUMP_TIMEOUT_SECONDS = 1800
LIST_TIMEOUT_SECONDS = 300
STALE_PARTIAL_HOURS = 24
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}

# Every one of these must appear in the dump's table of contents. A file that is
# missing one is not a backup of this application, whatever its size says.
REQUIRED_TABLES = (
    "businesses", "staff", "items", "orders", "order_lines", "payments",
    "stock_movements", "journal_entries", "journal_lines", "expenses",
)

BACKUP_ALERT_TYPE = "backup_failed"


class BackupError(RuntimeError):
    """The run failed. Operator-facing (log + alert details), never rendered in the UI."""


class BackupMisconfigured(BackupError):
    """The run could not even be attempted — wrong or missing configuration."""


# ── configuration ────────────────────────────────────────────────────────────

def resolve_binary(name: str) -> Path:
    """pg_dump / pg_restore, from PG_BIN_DIR, then PATH, then the Docker-less
    local cluster that scripts/local-pg.py unpacks."""
    exe = f"{name}.exe" if os.name == "nt" else name
    candidates: list[Path] = []
    configured = get_settings().pg_bin_dir
    if configured:
        candidates.append(Path(configured).expanduser() / exe)
    on_path = shutil.which(name)
    if on_path:
        candidates.append(Path(on_path))
    candidates.append(REPO_ROOT / ".pg16" / "install" / "bin" / exe)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise BackupMisconfigured(
        f"{name} not found. Install the Postgres 16 client tools or set PG_BIN_DIR."
    )


def source_url() -> str:
    """The URL the dump connects with. Never DATABASE_URL: see the module docstring."""
    settings = get_settings()
    url = settings.backup_database_url or settings.migration_database_url
    if not url:
        raise BackupMisconfigured(
            "no elevated database URL for the backup: set BACKUP_DATABASE_URL (or "
            "MIGRATION_DATABASE_URL). DATABASE_URL is the restricted app_role and "
            "pg_dump would fail against RLS rather than dump every tenant."
        )
    return url


def _host_of(url: str | None) -> str | None:
    if not url:
        return None
    return urlsplit(url.replace("postgresql+asyncpg://", "postgresql://")).hostname


def connection_env(url: str) -> dict[str, str]:
    """libpq environment for the client tools, so the password never lands in
    argv where `ps` can read it."""
    parts = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://"))
    if parts.scheme not in {"postgres", "postgresql"}:
        raise BackupMisconfigured(f"not a Postgres URL: {parts.scheme!r}")
    env = dict(os.environ)
    env["PGHOST"] = parts.hostname or "localhost"
    env["PGPORT"] = str(parts.port or 5432)
    if parts.username:
        env["PGUSER"] = unquote(parts.username)
    if parts.password:
        env["PGPASSWORD"] = unquote(parts.password)
    env["PGDATABASE"] = unquote(parts.path.lstrip("/")) or "postgres"
    sslmode = parse_qs(parts.query).get("sslmode")
    if sslmode:
        env["PGSSLMODE"] = sslmode[0]
    elif env["PGHOST"] not in LOCAL_HOSTS:
        env["PGSSLMODE"] = "require"
    return env


def check_destination(dest: Path, environment: str, db_url: str | None) -> None:
    """"Off the primary host" is the entire point of this task, so in production
    it is verified rather than assumed."""
    if environment != "production":
        return
    if not dest.is_absolute():
        raise BackupMisconfigured(f"BACKUP_DIR must be an absolute path in production, got {dest}")
    try:
        dest.resolve().relative_to(REPO_ROOT)
        inside_repo = True
    except ValueError:
        inside_repo = False
    if inside_repo:
        raise BackupMisconfigured(
            f"BACKUP_DIR {dest} is inside the deployment tree, which is replaced on every "
            "deploy. Point it at storage on a different host and account."
        )
    if _host_of(db_url) in LOCAL_HOSTS:
        raise BackupMisconfigured(
            "the database is on this same machine, so a local BACKUP_DIR is not off-host. "
            "Point BACKUP_DIR at another host, or the database at its real one."
        )


def destination() -> Path:
    settings = get_settings()
    if settings.backup_dir:
        dest = Path(settings.backup_dir).expanduser()
    elif settings.environment == "production":
        raise BackupMisconfigured(
            "BACKUP_DIR is unset. Production backups must be written off the database's "
            "host — there is no safe default for that."
        )
    else:
        dest = DEV_BACKUP_DIR
    check_destination(
        dest,
        settings.environment,
        settings.backup_database_url or settings.migration_database_url or settings.database_url,
    )
    dest.mkdir(parents=True, exist_ok=True)
    return dest


# ── the run ──────────────────────────────────────────────────────────────────

def dump(dest: Path, now: datetime) -> Path:
    """One custom-format dump of the whole database. Written under a `.partial`
    name and renamed only once pg_dump has exited cleanly, so a killed run can
    never leave something that looks like a finished backup."""
    daily = dest / DAILY
    daily.mkdir(parents=True, exist_ok=True)
    target = daily / f"{DUMP_PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}{DUMP_SUFFIX}"
    partial = target.with_suffix(PARTIAL_SUFFIX)
    partial.unlink(missing_ok=True)

    command = [str(resolve_binary("pg_dump")), "--format=custom", "--file", str(partial)]
    try:
        proc = subprocess.run(
            command,
            env=connection_env(source_url()),
            capture_output=True,
            text=True,
            timeout=DUMP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        partial.unlink(missing_ok=True)
        raise BackupError(f"pg_dump did not finish within {DUMP_TIMEOUT_SECONDS}s") from None
    if proc.returncode != 0:
        partial.unlink(missing_ok=True)
        raise BackupError(f"pg_dump exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}")

    partial.replace(target)
    return target


def verify(path: Path, min_bytes: int) -> tuple[int, int]:
    """Reads the dump back through pg_restore. Returns (bytes, tables in the TOC).

    This is the cheap half of "a backup you have never restored is not a backup";
    the other half is the restore drill, M15-T2.
    """
    size = path.stat().st_size
    if size < min_bytes:
        raise BackupError(f"dump is {size} bytes, under the {min_bytes}-byte floor — not a usable backup")

    try:
        proc = subprocess.run(
            [str(resolve_binary("pg_restore")), "--list", str(path)],
            capture_output=True,
            text=True,
            timeout=LIST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise BackupError("pg_restore --list did not finish") from None
    if proc.returncode != 0:
        raise BackupError(f"pg_restore could not read the dump: {(proc.stderr or '').strip()[:500]}")

    tables = toc_tables(proc.stdout)
    missing = [name for name in REQUIRED_TABLES if name not in tables]
    if missing:
        raise BackupError(f"dump has no table data for: {', '.join(missing)}")
    return size, len(tables)


def toc_tables(listing: str) -> set[str]:
    """Table names out of `pg_restore --list` output, whose data lines read
    `4321; 0 16456 TABLE DATA public orders app_role`."""
    marker = "TABLE DATA "
    names: set[str] = set()
    for line in listing.splitlines():
        if line.lstrip().startswith(";") or marker not in line:
            continue
        fields = line.split(marker, 1)[1].split()
        if len(fields) >= 2:
            names.add(fields[1])
    return names


def latest_dump(dest: Path, folder: str = DAILY) -> Path | None:
    """The newest finished dump, or None. Filenames are zero-padded timestamps,
    so newest is simply the last one in sort order. Used by the restore drill
    (M15-T2), which is the other half of this task."""
    candidates = sorted(
        p for p in (dest / folder).glob(f"{DUMP_PREFIX}*{DUMP_SUFFIX}") if p.is_file()
    )
    return candidates[-1] if candidates else None


def promote_monthly(dest: Path, dump_path: Path, now: datetime) -> Path | None:
    """The first successful dump of a calendar month is also kept as that
    month's copy. Returns it, or None when the month already has one."""
    monthly = dest / MONTHLY
    monthly.mkdir(parents=True, exist_ok=True)
    target = monthly / f"{DUMP_PREFIX}{now.strftime('%Y%m')}{DUMP_SUFFIX}"
    if target.exists():
        return None
    shutil.copy2(dump_path, target)
    return target


def prune(dest: Path, keep_daily: int, keep_monthly: int, now: datetime | None = None) -> list[str]:
    """Keeps the newest `keep_*` of each kind — filenames are zero-padded
    timestamps, so newest-first is a reverse sort. Also clears `.partial` files
    a crashed run left behind."""
    moment = now or datetime.now(timezone.utc)
    removed: list[str] = []
    for folder_name, keep in ((DAILY, keep_daily), (MONTHLY, keep_monthly)):
        folder = dest / folder_name
        if not folder.is_dir():
            continue
        dumps = sorted(
            (p for p in folder.glob(f"{DUMP_PREFIX}*{DUMP_SUFFIX}") if p.is_file()), reverse=True
        )
        for old in dumps[keep:]:
            old.unlink()
            removed.append(f"{folder_name}/{old.name}")
        cutoff = moment - timedelta(hours=STALE_PARTIAL_HOURS)
        for stale in folder.glob(f"*{PARTIAL_SUFFIX}"):
            if datetime.fromtimestamp(stale.stat().st_mtime, timezone.utc) < cutoff:
                stale.unlink()
                removed.append(f"{folder_name}/{stale.name}")
    return removed


# ── the run log ──────────────────────────────────────────────────────────────

def append_run_log(dest: Path, record: dict) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with (dest / RUN_LOG).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def read_run_log(dest: Path) -> list[dict]:
    path = dest / RUN_LOG
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def last_success(dest: Path) -> str | None:
    """When the last dump that worked finished, ISO-8601, or None."""
    for record in reversed(read_run_log(dest)):
        if record.get("ok"):
            return record.get("finished_at")
    return None


@dataclass
class BackupResult:
    ok: bool
    started_at: datetime
    finished_at: datetime
    path: str | None = None
    bytes: int = 0
    tables: int = 0
    monthly: str | None = None
    pruned: list[str] = field(default_factory=list)
    error: str | None = None
    last_success_before: str | None = None

    def as_record(self) -> dict:
        return {
            "ok": self.ok,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "path": self.path,
            "bytes": self.bytes,
            "tables": self.tables,
            "monthly": self.monthly,
            "pruned": self.pruned,
            "error": self.error,
        }


def run_backup(now: datetime | None = None) -> BackupResult:
    """Takes the dump and records the run. Filesystem and subprocess only — no
    database session, so it still reports when the app's own connection is the
    broken thing. Never raises: the failure *is* the output."""
    started = now or datetime.now(timezone.utc)
    settings = get_settings()
    dest: Path | None = None
    previous: str | None = None
    try:
        dest = destination()
        previous = last_success(dest)
        path = dump(dest, started)
        size, tables = verify(path, settings.backup_min_bytes)
        monthly = promote_monthly(dest, path, started)
        pruned = prune(dest, settings.backup_keep_daily, settings.backup_keep_monthly, started)
        result = BackupResult(
            ok=True,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            path=str(path),
            bytes=size,
            tables=tables,
            monthly=str(monthly) if monthly else None,
            pruned=pruned,
            last_success_before=previous,
        )
    except Exception as exc:  # noqa: BLE001 — a backup job that crashes silently is the bug
        result = BackupResult(
            ok=False,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            error=f"{type(exc).__name__}: {exc}",
            last_success_before=previous,
        )
        logger.error("Backup FAILED: %s", result.error)

    if dest is not None:
        try:
            append_run_log(dest, result.as_record())
        except OSError:
            logger.exception("could not write the backup run log to %s", dest)
    return result


# ── telling the owner ────────────────────────────────────────────────────────

def alert_rule_key(result: BackupResult, business: Business) -> str:
    """`backup:<business-local day>` — one alert per day, so a retry after a
    failure does not say it twice."""
    tz = ZoneInfo(business.timezone or "Asia/Jakarta")
    return f"backup:{result.started_at.astimezone(tz).date().isoformat()}"


def alert_message(result: BackupResult, business: Business) -> str:
    tz = ZoneInfo(business.timezone or "Asia/Jakarta")
    when = result.started_at.astimezone(tz).strftime("%d/%m %H:%M")
    if result.last_success_before:
        previous = datetime.fromisoformat(result.last_success_before).astimezone(tz)
        tail = f"Backup terakhir yang berhasil: {previous.strftime('%d/%m %H:%M')}."
    else:
        tail = "Belum pernah ada backup yang berhasil."
    return (
        f"Backup otomatis database GAGAL pada {when}. {tail} "
        "Data usaha belum tersalin ke luar server, jadi kalau server bermasalah hari ini "
        "datanya tidak bisa dikembalikan. Silakan hubungi teknisi."
    )


async def raise_backup_alert(session, business: Business, result: BackupResult) -> bool:
    """Writes tonight's `backup_failed` alert for one business, unless it is
    already there. Returns whether it wrote one. The session must already be
    scoped to the business.

    Deliberately *not* routed through `alert_policy` (M10-T2): that suppresses a
    subject already alerted within the last seven days, which is exactly the
    wrong behaviour here — a backup that has been failing for six nights has to
    be said again on the seventh. The only dedup is the local day, so a retry
    after a failed run does not say it twice.
    """
    key = alert_rule_key(result, business)
    already = await session.scalar(select(Alert.id).where(Alert.rule_key == key).limit(1))
    if already is not None:
        return False
    session.add(
        Alert(
            business_id=business.id,
            type=BACKUP_ALERT_TYPE,
            severity="high",
            message=alert_message(result, business),
            rule_key=key,
            details={
                "error": result.error,
                "last_success_before": result.last_success_before,
            },
        )
    )
    await session.flush()
    return True


async def report_failure(result: BackupResult) -> int:
    """Tells every business the backup failed, and delivers it now rather than
    waiting for the nightly job. Returns how many alerts went out."""
    async with plain_session() as session:
        businesses = (
            (await session.execute(select(Business).order_by(Business.created_at))).scalars().all()
        )

    delivered = 0
    for business in businesses:
        try:
            async with tenant_session(business.id) as session:
                await raise_backup_alert(session, business, result)
                delivered += await deliver_unsent(session, business)
        except Exception:
            # One tenant's failure must not stop the others being told.
            logger.exception("could not raise the backup alert for business %s", business.id)
    return delivered


async def main() -> None:
    result = await asyncio.to_thread(run_backup)
    if result.ok:
        logger.info(
            "Backup ok: %s (%s bytes, %d tables%s%s)",
            result.path,
            f"{result.bytes:,}",
            result.tables,
            f", monthly {Path(result.monthly).name}" if result.monthly else "",
            f", pruned {len(result.pruned)}" if result.pruned else "",
        )
    else:
        await report_failure(result)
    await engine.dispose()
    if not result.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
