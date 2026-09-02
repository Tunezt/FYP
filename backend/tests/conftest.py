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


async def seed_books(session, business_id) -> None:
    """What registration gives every real business (M6-T1/M6-T3): the chart of
    accounts and the posting rules. Tests that sell, receive, waste or count
    stock need them, because the posting engine (M6-T4) writes the books in
    the same transaction and refuses to post without rules."""
    from app.services.accounts import ensure_standard_chart
    from app.services.posting_rules import ensure_standard_rules

    await ensure_standard_chart(session, business_id)
    await ensure_standard_rules(session, business_id)
