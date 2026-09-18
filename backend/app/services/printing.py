"""Printing (prt-3): what paper the café needs, routed to the right printer.

Two printers, fixed by the room:

  front    beside the cashier and barista: the customer's receipt, and a
           separate Bar slip with only the Bar items
  kitchen  15-20 metres back: the Dapur slip with only the Dapur items

A paid financial order produces, in its own transaction, one receipt job and
one preparation slip per station that has items. A station with nothing to
make gets no slip. A paid addition is its own financial order, so it gets its
own receipt and slips with **only its items**, headed with the original's
service number and its batch ("Pesanan 042 · Tambahan 1"); the original's
slips are never produced again as new work.

A job's `document` is frozen at creation, printer-neutral blocks that a
browser can draw and a print bridge can turn into ESC/POS. Nothing here speaks
to hardware and nothing here claims paper exists: a job is *printed* only when
a device reports it or a person confirms it.

Cancelling a paid order cannot un-print a slip. If a slip may already be on
paper (taken by a device, printed, or failed), a BATAL notice goes to the same
printer. A slip still waiting in the queue is withdrawn instead, because nobody
has seen it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Business, Order, PrintJob, Staff

SERVICE_LABEL = {"dine_in": "Makan di sini", "takeaway": "Bawa pulang", "pickup": "Ambil sendiri", "delivery": "Antar"}
STATION_TITLE = {"bar": "BAR", "kitchen": "DAPUR"}
STATION_PRINTER = {"bar": "front", "kitchen": "kitchen"}
TICKET_KIND = {"bar": "bar_ticket", "kitchen": "kitchen_ticket"}
CANCEL_KIND = {"bar": "bar_cancel", "kitchen": "kitchen_cancel"}
# A device that took a job and has not reported back within this long leaves
# the outcome unknown: the screen says so rather than guessing.
UNCERTAIN_AFTER = timedelta(seconds=90)


class PrintInvalid(Exception):
    """code: not_found · state (the job is not in a state that allows this)."""

    def __init__(self, code: str, status: str | None = None):
        self.code, self.status = code, status


# ── Identity on paper ────────────────────────────────────────────────────────


def _label(order: Order) -> str:
    return f"{order.service_number:03d}" if order.service_number is not None else f"#{str(order.id)[-4:].upper()}"


def heading_blocks(order: Order) -> list[dict]:
    """"MEJA 7" over "Pesanan 042" for dine-in with a table; otherwise
    "PESANAN 042". The batch and a driver reference follow, never replace."""
    table = (order.table_label or "").strip()
    if table.lower().startswith("meja"):
        table = table[4:].strip()
    blocks: list[dict] = []
    if order.order_type == "dine_in" and table:
        blocks.append({"t": "banner", "text": f"MEJA {table.upper()}"})
        blocks.append({"t": "line", "text": f"Pesanan {_label(order)}", "style": "bold", "size": "large"})
    else:
        blocks.append({"t": "banner", "text": f"PESANAN {_label(order)}"})
    if order.batch_no:
        blocks.append({"t": "line", "text": f"Tambahan {order.batch_no}", "style": "bold", "size": "large"})
    if order.external_ref:
        blocks.append({"t": "line", "text": f"Driver: {order.external_ref}", "style": "bold"})
    return blocks


def ticket_ref(order: Order, station: str) -> str:
    """The slip's own reference: service number, station letter, batch."""
    return f"{_label(order).lstrip('#')}-{'B' if station == 'bar' else 'D'}{order.batch_no or 0}"


async def _local(session: AsyncSession, business_id: uuid.UUID, at: datetime) -> datetime:
    business = await session.get(Business, business_id)
    return at.astimezone(ZoneInfo(business.timezone if business else "Asia/Jakarta"))


def _rp(amount) -> str:
    return "Rp " + f"{Decimal(amount):,.0f}".replace(",", ".")


# ── Documents ────────────────────────────────────────────────────────────────


async def _station_lines(session: AsyncSession, order: Order, station: str) -> list:
    """The order's own positive lines for one station. An item with no station
    set is sent to the front (bar), where someone can walk it back."""
    from app.services.kitchen import _lines_of
    from app.models import OrderLine

    stations = dict((await session.execute(
        select(OrderLine.id, OrderLine.prep_station).where(OrderLine.order_id == order.id, OrderLine.quantity > 0)
    )).all())
    out = []
    for line in await _lines_of(session, order):
        st = stations.get(line.line_id)
        routed = "bar" if st is None else st
        if routed == station:
            line.unassigned = st is None
            out.append(line)
    return out


def _item_blocks(lines) -> list[dict]:
    blocks = []
    for l in lines:
        blocks.append({
            "t": "item", "qty": _qty(l.quantity), "name": l.item_name or l.name, "size": l.size,
            "modifiers": list(l.modifiers), "notes": l.notes,
            "flag": "TUJUAN BELUM DIATUR" if getattr(l, "unassigned", False) else None,
        })
    return blocks


def _qty(q) -> str:
    q = Decimal(q)
    return str(int(q)) if q == q.to_integral_value() else f"{q.normalize()}"


async def render_ticket(session: AsyncSession, order: Order, station: str, *, cancel: bool = False) -> dict | None:
    """A preparation slip: no prices, no payment, no customer details — the
    table or number, the service, the time, the reference, and exactly what to
    make with every choice written out."""
    lines = await _station_lines(session, order, station)
    if not lines:
        return None
    when = await _local(session, order.business_id, order.sold_at)
    blocks: list[dict] = [{"t": "title", "text": STATION_TITLE[station]}]
    if cancel:
        blocks.append({"t": "label", "text": "BATAL"})
    elif order.batch_no:
        blocks.append({"t": "label", "text": "TAMBAHAN"})
    blocks += heading_blocks(order)
    blocks.append({"t": "kv", "left": SERVICE_LABEL.get(order.order_type, order.order_type), "right": when.strftime("%H.%M")})
    blocks.append({"t": "kv", "left": f"Ref {ticket_ref(order, station)}", "right": when.strftime("%d/%m")})
    blocks.append({"t": "rule"})
    if cancel:
        blocks.append({"t": "text", "text": "JANGAN DIBUAT / HENTIKAN:", "style": "bold"})
    blocks += _item_blocks(lines)
    note = (order.cart or {}).get("note")
    if note and not cancel:
        blocks += [{"t": "rule"}, {"t": "note", "text": f"Catatan pesanan: {note}"}]
    if cancel:
        blocks += [{"t": "rule"}, {"t": "text", "text": "Pesanan ini dibatalkan setelah slip dicetak. Konfirmasi ke kasir.", "style": "bold"}]
    return {"v": 1, "kind": CANCEL_KIND[station] if cancel else TICKET_KIND[station], "station": station, "blocks": blocks}


async def render_receipt(session: AsyncSession, order: Order) -> dict:
    """The customer's receipt: everything bought, with prices and payment."""
    from app.services.orders import load_receipt, order_number
    from app.services.pricing import pricing_config

    data = await load_receipt(session, business_id=order.business_id, order_id=order.id)
    business = await session.get(Business, order.business_id)
    config = await pricing_config(session, order.business_id)
    when = await _local(session, order.business_id, order.sold_at)
    blocks: list[dict] = [{"t": "title", "text": (business.name if business else "").upper()}]
    blocks += heading_blocks(order)
    blocks.append({"t": "kv", "left": SERVICE_LABEL.get(order.order_type, order.order_type), "right": when.strftime("%d/%m/%Y %H.%M")})
    blocks.append({"t": "kv", "left": f"Struk #{order_number(order.id)}", "right": data["staff_name"] or ""})
    blocks.append({"t": "rule"})
    from app.services.kitchen import _lines_of

    sized = {l.line_id: l for l in await _lines_of(session, order)}
    for entry in data["lines"]:
        line = entry["line"]
        if Decimal(line.quantity) <= 0:
            continue
        k = sized.get(line.id)
        blocks.append({
            "t": "item_priced", "qty": _qty(line.quantity), "name": entry["item_name"], "size": k.size if k else None,
            "modifiers": [m.name + (f" +{_rp(m.price_delta)}" if Decimal(m.price_delta) else "") for m in entry["modifiers"]],
            "notes": line.notes, "amount": _rp(line.line_total),
        })
    blocks.append({"t": "rule"})
    for label, value, sign in (
        ("Subtotal", order.subtotal, ""), ("Diskon", order.discount_total, "-"), ("Promo", order.promo_total, "-"),
        ("Voucher", order.voucher_total, "-"), ("Service", order.service_charge, ""),
        ("Ongkos kirim", order.delivery_fee, ""), ("Pajak (termasuk)" if config.tax_inclusive else "Pajak", order.tax_total, ""),
        ("Pembulatan", order.rounding, ""),
    ):
        if Decimal(value or 0) != 0 and (label != "Subtotal" or Decimal(order.subtotal) != Decimal(order.total)):
            blocks.append({"t": "kv", "left": label, "right": f"{sign}{_rp(value)}"})
    blocks.append({"t": "total", "left": "TOTAL", "right": _rp(order.total)})
    for p in data["payments"]:
        if Decimal(p.amount) > 0:
            blocks.append({"t": "kv", "left": "Tunai" if p.method == "cash" else p.method.upper(), "right": _rp(p.amount)})
    blocks += [{"t": "rule"}, {"t": "text", "text": "Sebutkan nomor pesanan saat mengambil. Terima kasih!", "align": "center"}]
    return {"v": 1, "kind": "receipt", "blocks": blocks}


# ── The queue ────────────────────────────────────────────────────────────────


async def _add_job(session, order: Order, *, printer: str, kind: str, document: dict, key: str, staff_id=None,
                   copy: str = "original", reprint_of: uuid.UUID | None = None) -> PrintJob:
    existing = (await session.execute(select(PrintJob).where(PrintJob.dedupe_key == key))).scalar_one_or_none()
    if existing is not None:
        return existing
    job = PrintJob(business_id=order.business_id, order_id=order.id, printer=printer, kind=kind, copy=copy,
                   reprint_of=reprint_of, dedupe_key=key, document=document, created_by=staff_id)
    session.add(job)
    await session.flush()
    return job


async def enqueue_for_paid_order(session: AsyncSession, order: Order, *, staff_id: uuid.UUID | None = None) -> list[PrintJob]:
    """Called inside the payment's transaction. One receipt, one slip per
    station with items. Keyed on the financial order, so no retry can add a
    second copy. A backdated paper sale was served long ago and prints nothing."""
    if order.entry_source != "live":
        return []
    jobs = [await _add_job(session, order, printer="front", kind="receipt", document=await render_receipt(session, order),
                           key=f"{order.id}:receipt", staff_id=staff_id)]
    for station in ("bar", "kitchen"):
        doc = await render_ticket(session, order, station)
        if doc is not None:
            jobs.append(await _add_job(session, order, printer=STATION_PRINTER[station], kind=TICKET_KIND[station],
                                       document=doc, key=f"{order.id}:{TICKET_KIND[station]}", staff_id=staff_id))
    return jobs


async def on_order_reversed(session: AsyncSession, order: Order, *, staff_id: uuid.UUID | None = None) -> list[PrintJob]:
    """A paid order was voided or refunded. For each station slip: withdraw it
    if nothing has taken it yet; otherwise send a BATAL notice to that printer,
    because paper may already be on the pass."""
    notices: list[PrintJob] = []
    for station in ("bar", "kitchen"):
        original = (await session.execute(
            select(PrintJob).where(PrintJob.order_id == order.id, PrintJob.kind == TICKET_KIND[station], PrintJob.copy == "original")
        )).scalar_one_or_none()
        if original is None:
            continue
        withdrawn = await session.execute(
            update(PrintJob).where(PrintJob.id == original.id, PrintJob.status == "pending")
            .values(status="cancelled", updated_at=func.now()).execution_options(synchronize_session=False)
        )
        if withdrawn.rowcount == 1:
            await session.refresh(original)
            continue
        doc = await render_ticket(session, order, station, cancel=True)
        if doc is not None:
            notices.append(await _add_job(session, order, printer=STATION_PRINTER[station], kind=CANCEL_KIND[station],
                                          document=doc, key=f"{order.id}:{CANCEL_KIND[station]}", staff_id=staff_id))
    return notices


def display_status(job: PrintJob, now: datetime | None = None) -> str:
    """What the screen may honestly say: pending · uncertain · printed · failed · cancelled."""
    moment = now or datetime.now(timezone.utc)
    if job.status == "claimed":
        return "uncertain" if job.claimed_at is None or moment - job.claimed_at >= UNCERTAIN_AFTER else "sending"
    return job.status


# Jobs written in one transaction share its `now()`, so within a moment the
# paper comes out in this order: the customer's copy first, then the slips.
PAPER_ORDER = case(
    {"receipt": 0, "bar_ticket": 1, "kitchen_ticket": 1, "bar_cancel": 2, "kitchen_cancel": 2},
    value=PrintJob.kind, else_=3,
)


async def claim_next(session: AsyncSession, *, printer: str, device: str, now: datetime | None = None) -> PrintJob | None:
    """A printer device asks for work. Oldest pending job for that printer, taken
    with `for update skip locked` so two devices never take the same job."""
    moment = now or datetime.now(timezone.utc)
    job = (await session.execute(
        select(PrintJob).where(PrintJob.printer == printer, PrintJob.status == "pending")
        .order_by(PrintJob.created_at, PAPER_ORDER, PrintJob.id).limit(1).with_for_update(skip_locked=True)
    )).scalar_one_or_none()
    if job is None:
        return None
    job.status, job.claimed_at, job.claimed_by = "claimed", moment, device[:60]
    job.attempts = (job.attempts or 0) + 1
    job.updated_at = moment
    await session.flush()
    return job


async def _job(session: AsyncSession, job_id: uuid.UUID, lock: bool = True) -> PrintJob:
    stmt = select(PrintJob).where(PrintJob.id == job_id)
    job = (await session.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
    if job is None:
        raise PrintInvalid("not_found")
    return job


async def report_result(session: AsyncSession, *, job_id: uuid.UUID, ok: bool, device: str, error: str | None = None,
                        now: datetime | None = None) -> PrintJob:
    """The device says what happened. Only a job it holds can be answered, and
    an answer arriving twice is the same answer."""
    moment = now or datetime.now(timezone.utc)
    job = await _job(session, job_id)
    target = "printed" if ok else "failed"
    if job.status == target:
        return job
    if job.status != "claimed":
        raise PrintInvalid("state", job.status)
    if ok:
        job.status, job.printed_at, job.error = "printed", moment, None
    else:
        job.status, job.failed_at, job.error = "failed", moment, (error or "printer melaporkan gagal")[:300]
    job.updated_at = moment
    await session.flush()
    return job


async def retry(session: AsyncSession, *, job_id: uuid.UUID, now: datetime | None = None) -> PrintJob:
    """Put a job the printer said it could not print back in the queue. Only a
    *failed* job: an uncertain one may be on paper, so it takes a marked reprint
    or a person's confirmation instead."""
    job = await _job(session, job_id)
    if job.status == "pending":
        return job
    if job.status != "failed":
        raise PrintInvalid("state", job.status)
    job.status, job.claimed_at, job.claimed_by, job.updated_at = "pending", None, None, now or datetime.now(timezone.utc)
    await session.flush()
    return job


async def reprint(session: AsyncSession, *, job_id: uuid.UUID, staff_id: uuid.UUID | None) -> PrintJob:
    """A new job carrying the same document, labelled CETAK ULANG on paper so
    nobody at the pass mistakes it for new work."""
    source = await _job(session, job_id)
    original_id = source.reprint_of or source.id
    original = await _job(session, original_id)
    order = await session.get(Order, original.order_id)
    n = (await session.execute(select(func.count(PrintJob.id)).where(PrintJob.reprint_of == original_id))).scalar_one() + 1
    staff = await session.get(Staff, staff_id) if staff_id else None
    stamp = await _local(session, original.business_id, datetime.now(timezone.utc))
    doc = dict(original.document)
    blocks = list(doc.get("blocks", []))
    insert_at = 1 if blocks and blocks[0].get("t") == "title" else 0
    blocks[insert_at:insert_at] = [
        {"t": "label", "text": "CETAK ULANG"},
        {"t": "text", "text": f"Cetak ulang ke-{n} · {stamp.strftime('%H.%M')}{' · ' + staff.name if staff else ''}", "align": "center"},
    ]
    doc["blocks"], doc["reprint"] = blocks, n
    return await _add_job(session, order, printer=original.printer, kind=original.kind, document=doc,
                          key=f"{original_id}:reprint:{n}", staff_id=staff_id, copy="reprint", reprint_of=original_id)


async def confirm_printed(session: AsyncSession, *, job_id: uuid.UUID, staff_id: uuid.UUID | None,
                          now: datetime | None = None) -> PrintJob:
    """A person holding the paper says it is there. Recorded as theirs: this is
    the only way a browser-printed job becomes *printed*."""
    job = await _job(session, job_id)
    if job.status == "printed":
        return job
    if job.status == "cancelled":
        raise PrintInvalid("state", job.status)
    moment = now or datetime.now(timezone.utc)
    job.status, job.printed_at, job.confirmed_by, job.updated_at = "printed", moment, staff_id, moment
    await session.flush()
    return job


async def jobs_for_orders(session: AsyncSession, order_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[PrintJob]]:
    if not order_ids:
        return {}
    rows = (await session.execute(
        select(PrintJob).where(PrintJob.order_id.in_(order_ids)).order_by(PrintJob.created_at, PAPER_ORDER, PrintJob.id)
    )).scalars().all()
    out: dict[uuid.UUID, list[PrintJob]] = {}
    for j in rows:
        out.setdefault(j.order_id, []).append(j)
    return out
