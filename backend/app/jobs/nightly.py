"""Nightly job — Railway Cron entrypoint: `python -m app.jobs.nightly`

Per business (explicit loop, each inside its own tenant-scoped transaction —
never a cross-tenant query):
  1. refresh 30-day metric baselines (the cached rolling mean/stddev)
  2. stock-velocity sweep over every item (the same check a sale triggers)
  3. the exception rules over the registry (M10-T1): margin drop, stock-out
     before the next likely delivery, a cashier's void rate, a supplier price
     move, takings and expense anomalies (the z-score rule) — written through
     the alert policy (M10-T2): dedup, same-as-last-week suppression, at most
     five new alerts a night
  4. deliver unsent alerts via the approved WhatsApp Utility TEMPLATE

Delivery MUST use the template: this job runs unprompted, almost certainly
outside the 24-hour session window, and Meta rejects free-form messages there.

Suggested Railway cron schedule: `30 16 * * *` (= 23:30 WIB) so "today" is a
nearly-complete business day for Indonesian tenants.
"""
import asyncio
import logging

from sqlalchemy import select

from app.core.db import engine, plain_session, tenant_session
from app.models import Alert, Business, Item
from app.jobs.delivery import deliver_unsent
from app.jobs.rules import run_rules
from app.services.anomaly import refresh_baselines
from app.services.velocity import check_low_stock_for_item

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jobs.nightly")


async def nightly_pass(session, business: Business, now=None) -> list[Alert]:
    """Everything the night does except sending: baselines, the stock sweep,
    the rules through the policy. Returns the alerts written tonight. Kept
    separate so a test can run a simulated week of it."""
    await refresh_baselines(session, business)
    item_ids = (await session.execute(select(Item.id))).scalars().all()
    for item_id in item_ids:
        await check_low_stock_for_item(session, business.id, item_id)
    written = await run_rules(session, business, now)
    if written:
        logger.info("business=%s rules fired: %s", business.id, [a.type for a in written])
    return written


async def process_business(business: Business) -> int:
    """Runs the full nightly pass for one business; returns alerts delivered."""
    async with tenant_session(business.id) as session:
        await nightly_pass(session, business)
        return await deliver_unsent(session, business)


async def main() -> None:
    async with plain_session() as session:
        businesses = (
            (await session.execute(select(Business).order_by(Business.created_at)))
            .scalars()
            .all()
        )
    logger.info("Nightly run: %d business(es)", len(businesses))

    delivered_total = 0
    for business in businesses:
        try:
            delivered_total += await process_business(business)
        except Exception:
            # One tenant's failure must not stop the sweep for the others.
            logger.exception("Nightly pass failed for business %s", business.id)

    logger.info("Nightly run complete: %d alert(s) delivered", delivered_total)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
