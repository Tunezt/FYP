"""A receipt on the customer's WhatsApp, route A (till-7; decisions 4 and 5).

After paying, the customer scans a QR the till shows (or that is printed at the
foot of the paper receipt): `wa.me/<bot>?text=STRUK <code>`. WhatsApp opens
with the message ready; they tap send; the bot answers with the receipt.

* Free: the customer wrote first, so the reply is a service message inside
  the 24-hour window. No template, no approval; consent is the customer's own
  message.
* One bot number serves every café, so the code says whose receipt it is
  (receipt_delivery.py) and is unguessable: "STRUK 001" finds nothing.
* Lookups are rate-limited per sender, so the code space cannot be walked
  from one phone.
* The customer's number is not stored. The lookup is logged in `request_logs`
  only under the café that owns the receipt, without the number.
"""
from __future__ import annotations

import logging
import re
import time
from collections import deque
from decimal import Decimal

from app.core.db import tenant_session
from app.models import RequestLog

logger = logging.getLogger("whatsapp.receipts")

# "STRUK <anything>" is a receipt request, so a mistyped code is told "tidak
# ditemukan" rather than "belum terdaftar"; whether the code is real is decided
# by the lookup.
STRUK_RE = re.compile(r"^\s*struk\s+(\S{1,60})\s*$", re.IGNORECASE)

# A sender may look up this many receipts in this many seconds. A real
# customer sends one; a script walking codes is stopped long before it matters.
RATE_LIMIT = 5
RATE_WINDOW = 600
_recent: dict[str, deque[float]] = {}

NOT_FOUND = (
    "Struk dengan kode itu tidak ditemukan. Pindai ulang kode QR dari kasir, "
    "lalu kirim pesannya tanpa diubah ya. / Receipt not found."
)
TOO_MANY = "Terlalu banyak permintaan struk dari nomor ini — coba lagi beberapa menit lagi ya. / Too many requests."


def receipt_code_in(text: str | None) -> str | None:
    match = STRUK_RE.match(text or "")
    return match.group(1) if match else None


def _allowed(sender: str, now: float | None = None) -> bool:
    moment = time.monotonic() if now is None else now
    seen = _recent.setdefault(sender, deque())
    while seen and moment - seen[0] > RATE_WINDOW:
        seen.popleft()
    if len(seen) >= RATE_LIMIT:
        return False
    seen.append(moment)
    return True


def _rp(amount) -> str:
    return "Rp " + f"{Decimal(amount):,.0f}".replace(",", ".")


def receipt_text(view, web_url: str | None) -> str:
    """The receipt for a chat window: short lines, WhatsApp's own *bold* and
    _italic_, no column alignment (the 48-column layout is for paper)."""
    from app.services.business_profile import tax_line_label
    from app.services.printing import payment_label

    head = f"MEJA {view.table_label.upper()} · " if view.order_type == "dine_in" and view.table_label else ""
    out: list[str] = [f"*{view.business_name.upper()}*"]
    if view.business_address:
        out.append(view.business_address)
    contact = " · ".join(c for c in (view.business_phone, f"IG {view.business_instagram}" if view.business_instagram else None) if c)
    if contact:
        out.append(contact)
    out.append("")
    out.append(f"*{head}PESANAN {view.order_no}*" + (f" · Tambahan {view.batch_no}" if view.batch_no else ""))
    out.append(view.sold_at.strftime("%d/%m/%Y %H.%M") + (" · DIBATALKAN" if view.status == "voided" else " · DIKEMBALIKAN" if view.status == "refunded" else ""))
    out.append("")
    for line in view.lines:
        if Decimal(line.quantity) <= 0:
            continue
        qty = f"{Decimal(line.quantity).normalize():f}"
        name = f"{line.name} · {line.size}" if line.size else line.name
        out.append(f"{qty}× {name} — {_rp(line.line_total)}")
        for m in line.modifiers:
            out.append(f"   + {m.name}" + (f" ({_rp(m.price_delta)})" if Decimal(m.price_delta) else ""))
        if line.notes:
            out.append(f"   _{line.notes}_")
    out.append("")
    for label, value, sign in (
        ("Diskon", view.discount_total, "-"), ("Promo", view.promo_total, "-"), ("Voucher", view.voucher_total, "-"),
        ("Service", view.service_charge, ""), ("Ongkos kirim", view.delivery_fee, ""),
        (tax_line_label(view.tax_label, view.tax_rate, view.tax_inclusive), view.tax_total, ""),
        ("Pembulatan", view.rounding, ""),
    ):
        if Decimal(value or 0) != 0:
            out.append(f"{label}: {sign}{_rp(value)}")
    out.append(f"*Total: {_rp(view.total)}*")
    for p in view.payments:
        if Decimal(p.amount) <= 0:
            continue
        text = f"{payment_label(p)} {_rp(p.amount)}"
        if p.method == "cash" and p.tendered and Decimal(p.tendered) > Decimal(p.amount):
            text += f" · diterima {_rp(p.tendered)} · kembali {_rp(Decimal(p.tendered) - Decimal(p.amount))}"
        out.append(text)
    out.append("")
    if web_url:
        out.append(f"Struk lengkap: {web_url}")
    out.append(f"Ref {view.number}. Terima kasih sudah mampir! 🙏")
    return "\n".join(out)


async def answer_receipt_request(sender: str, code: str, send) -> str:
    """Look the code up and reply. Returns the intent for the caller's log line.
    `send` is the client's send_text, passed in so the processor's own import
    (and its tests' patch) is the one used."""
    from app.api.pos import receipt_view
    from app.core.config import get_settings
    from app.services.receipt_delivery import find_order_by_code, normalize_code, web_path

    start = time.perf_counter()
    if not _allowed(sender):
        await send(sender, TOO_MANY)
        return "receipt_rate_limited"
    found = await find_order_by_code(code)
    if found is None:
        await send(sender, NOT_FOUND)
        return "receipt_not_found"
    business_id, order_id = found
    async with tenant_session(business_id) as session:
        view = await receipt_view(session, business_id, order_id)
    from zoneinfo import ZoneInfo

    from app.models import Business
    from app.core.db import plain_session

    async with plain_session() as session:
        business = await session.get(Business, business_id)
    view = view.model_copy(update={"sold_at": view.sold_at.astimezone(ZoneInfo(business.timezone if business else "Asia/Jakarta"))})
    origin = (get_settings().frontend_origin or "").rstrip("/")
    await send(sender, receipt_text(view, f"{origin}{web_path(normalize_code(code))}" if origin else None))
    try:
        async with tenant_session(business_id) as session:
            session.add(RequestLog(
                business_id=business_id, channel="whatsapp", path="receipt", raw_query=f"STRUK {normalize_code(code)}",
                classified_intent="receipt_lookup", latency_ms=int((time.perf_counter() - start) * 1000), status="ok",
            ))
    except Exception:
        logger.exception("Failed to write the receipt request log")
    return "receipt_lookup"
