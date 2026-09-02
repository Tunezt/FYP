"""Shared pytest wiring.

`tests/test_db_integration.py` (RLS isolation + the concurrent-sale race) runs only when
INTEGRATION_DATABASE_URL is set. With the local Postgres from `docker compose up -d` /
`scripts/local-pg.py`, that is simply DATABASE_URL — so default it here. The default is
applied **only** when DATABASE_URL points at localhost: a Supabase or production URL in
.env never gets the integration tests run against it by accident.

After M0-T1 those four tests must never skip (roadmap §2). If they do, local Postgres is
down: `python scripts/local-pg.py status` / `docker compose ps`.
"""
import os
from urllib.parse import urlsplit

from app.core.config import get_settings

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

if not os.getenv("INTEGRATION_DATABASE_URL"):
    _url = get_settings().database_url
    if urlsplit(_url).hostname in _LOCAL_HOSTS:
        os.environ["INTEGRATION_DATABASE_URL"] = _url
