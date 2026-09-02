"""Docker-less local Postgres 16 + pgvector for Warung Pintar (Windows x64).

The roadmap's primary path is `docker compose up -d` (see docker-compose.yml). This is the
fallback for a machine with no Docker, no WSL and no admin rights: it fetches the
`pgserver` wheel from PyPI — which bundles a stock Postgres 16.2 build plus the pgvector
extension — unpacks the binaries, and runs a cluster that matches the compose service
exactly: superuser `postgres`/`postgres`, database `warung_pintar`, port 5432, and
scripts/db-bootstrap.sql applied so the restricted `app_role` exists.

Usage (any Python 3.10+, e.g. the backend venv):

    python scripts/local-pg.py start     # download on first run, initdb, start, bootstrap
    python scripts/local-pg.py stop
    python scripts/local-pg.py status
    python scripts/local-pg.py psql [args...]   # psql as the superuser
    python scripts/local-pg.py reset     # stop and delete the cluster (data loss, asks first)

Everything lives under <repo>/.pg16/ (git-ignored) — nothing is installed system-wide,
nothing needs admin, and deleting that folder removes it entirely. It is deliberately
*not* under %LOCALAPPDATA%: the Microsoft Store build of Python silently redirects writes
there into a per-app sandbox, and the Postgres loader then cannot find its own DLLs.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PGSERVER_SPEC = "pgserver==0.1.4"       # PyPI wheel that bundles Postgres 16.2 + pgvector
PGSERVER_PY = "3.12"                    # wheel tag to fetch — the binaries are Python-agnostic
PORT = 5432
DB_NAME = "warung_pintar"
SUPERUSER = "postgres"
SUPERUSER_PASSWORD = "postgres"

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP_SQL = REPO_ROOT / "scripts" / "db-bootstrap.sql"
HOME = REPO_ROOT / ".pg16"
INSTALL = HOME / "install"
DATA = HOME / "data"
LOG = HOME / "postgres.log"
BIN = INSTALL / "bin"


def _exe(name: str) -> Path:
    return BIN / (f"{name}.exe" if os.name == "nt" else name)


def _run(args: list, **kw) -> subprocess.CompletedProcess:
    env = {**os.environ, "PGPASSWORD": SUPERUSER_PASSWORD}
    return subprocess.run([str(a) for a in args], env=env, **kw)


def ensure_install() -> None:
    if _exe("postgres").exists():
        return
    if os.name != "nt":
        sys.exit("This fallback is for Windows. On macOS/Linux use `docker compose up -d`.")
    print(f"-> downloading {PGSERVER_SPEC} (Postgres 16 + pgvector) ...")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [sys.executable, "-m", "pip", "download", PGSERVER_SPEC, "--no-deps",
             "--only-binary=:all:", "--python-version", PGSERVER_PY,
             "--platform", "win_amd64", "-q", "-d", tmp],
            check=True,
        )
        wheel = next(Path(tmp).glob("pgserver-*.whl"))
        INSTALL.mkdir(parents=True, exist_ok=True)
        prefix = "pgserver/pginstall/"
        with zipfile.ZipFile(wheel) as zf:
            for member in zf.namelist():
                if member.startswith(prefix) and not member.endswith("/"):
                    target = INSTALL / member[len(prefix):]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    out = subprocess.run([str(_exe("postgres")), "--version"], capture_output=True, text=True)
    print(f"-> installed {out.stdout.strip()} into {INSTALL}")


def ensure_cluster() -> None:
    if (DATA / "PG_VERSION").exists():
        return
    print(f"-> initialising cluster in {DATA} ...")
    DATA.parent.mkdir(parents=True, exist_ok=True)
    pwfile = HOME / ".pwfile"
    pwfile.write_text(SUPERUSER_PASSWORD + "\n")
    try:
        _run([_exe("initdb"), "-D", DATA, "-U", SUPERUSER, "--auth=scram-sha-256",
              f"--pwfile={pwfile}", "-E", "UTF8", "--locale=C"], check=True)
    finally:
        pwfile.unlink(missing_ok=True)


def is_running() -> bool:
    return _run([_exe("pg_ctl"), "-D", DATA, "status"], capture_output=True).returncode == 0


def psql(*args: str, db: str = "postgres", check: bool = True, capture: bool = False):
    return _run([_exe("psql"), "-v", "ON_ERROR_STOP=1", "-h", "localhost", "-p", str(PORT),
                 "-U", SUPERUSER, "-d", db, *args],
                check=check, capture_output=capture, text=True)


def bootstrap() -> None:
    exists = psql("-tAc", f"select 1 from pg_database where datname = '{DB_NAME}'",
                  capture=True).stdout.strip()
    if exists != "1":
        psql("-c", f'create database "{DB_NAME}"')
    psql("-q", "-f", str(BOOTSTRAP_SQL), db=DB_NAME)


def start() -> None:
    ensure_install()
    ensure_cluster()
    if is_running():
        print("-> already running")
    else:
        # The server pg_ctl spawns must NOT inherit this process's stdio: if it did,
        # anything piping our output (a shell pipeline, an IDE task runner) would
        # block until Postgres exits. Its own output goes to LOG anyway.
        _run([_exe("pg_ctl"), "-D", DATA, "-l", LOG, "-o", f"-p {PORT}", "-w", "start"],
             check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
             stderr=subprocess.DEVNULL)
        print("-> server started")
    bootstrap()
    print(f"""
Postgres 16 + pgvector is up on localhost:{PORT}  (log: {LOG})

  DATABASE_URL=postgresql+asyncpg://app_role:app_role@localhost:{PORT}/{DB_NAME}
  MIGRATION_DATABASE_URL=postgresql+asyncpg://{SUPERUSER}:{SUPERUSER_PASSWORD}@localhost:{PORT}/{DB_NAME}
""")


def stop() -> None:
    if not is_running():
        print("-> not running")
        return
    _run([_exe("pg_ctl"), "-D", DATA, "-m", "fast", "-w", "stop"], check=True)


def status() -> None:
    if not _exe("postgres").exists():
        print("not installed - run: python scripts/local-pg.py start")
    elif is_running():
        print(f"running on localhost:{PORT}  data={DATA}")
    else:
        print(f"stopped  data={DATA}")


def reset() -> None:
    if input(f"Delete the whole cluster at {DATA}? [y/N] ").strip().lower() != "y":
        return
    stop()
    shutil.rmtree(DATA, ignore_errors=True)
    print("-> cluster deleted; `start` will create a fresh one")


def main(argv: list) -> None:
    cmd = argv[0] if argv else "start"
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    elif cmd == "status":
        status()
    elif cmd == "reset":
        reset()
    elif cmd == "psql":
        raise SystemExit(psql(*argv[1:], db=DB_NAME, check=False).returncode)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
