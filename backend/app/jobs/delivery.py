"""Getting an alert onto the owner's phone — the one place that does it.

Split out of `app/jobs/nightly.py` (roadmap M15-T1) because the backup job
needs the same path: a failed backup that only appears in a log file is not
"an alert the owner actually sees". Behaviour is unchanged from the nightly's
own delivery, and the nightly still calls it.

Delivery MUST use the approved Utility TEMPLATE: both callers run unprompted
from cron, almost certainly outside the 24-hour session window, where Meta
rejects free-form messages (roadmap §1.9).
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Alert, Business
from app.whatsapp.client import send_template

logger = logging.getLogger("jobs.delivery")

MAX_DETAIL_CHARS = 550  # keep well inside template-parameter limits

ALERT_KIND_LABEL = {
    "anomaly": "anomali penjualan", "low_stock": "stok menipis", "margin_drop": "margin turun",
    "stockout_risk": "stok habis sebelum kiriman", "void_rate": "pembatalan kasir", "supplier_price": "harga supplier berubah",
    "backup_failed": "backup gagal",
}


async def deliver_unsent(session: AsyncSession, business: Business) -> int:
    """One template send carrying every alert not yet sent for this business.
    Marks them sent. Returns how many went out. The session must already be
    scoped to the business."""
    unsent = (
        (
            await session.execute(
                select(Alert)
                .where(Alert.is_sent.is_(False))
                .order_by(Alert.severity.desc(), Alert.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not unsent:
        return 0

    # One template send per run: {{1}} kind, {{2}} business, {{3}} detail.
    kinds = sorted({ALERT_KIND_LABEL.get(a.type, a.type) for a in unsent})
    detail_lines: list[str] = []
    for alert in unsent:
        line = alert.message
        if sum(len(x) + 2 for x in detail_lines) + len(line) > MAX_DETAIL_CHARS:
            detail_lines.append(f"(+{len(unsent) - len(detail_lines)} peringatan lain)")
            break
        detail_lines.append(line)

    settings = get_settings()
    await send_template(
        business.owner_phone,
        settings.whatsapp_alert_template,
        [" & ".join(kinds), business.name, "; ".join(detail_lines)],
        language=business.language_preference,
    )
    for alert in unsent:
        alert.is_sent = True
    return len(unsent)
