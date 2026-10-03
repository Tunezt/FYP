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

Open bills (bill-1). A dine-in table orders, eats and orders again before it
pays. Each *send* is a batch: its Bar and Dapur slips carry only that batch's
items (TAMBAHAN and "Tambahan n" from the second send on), and the front
printer gives a *nota* for the table, the new items with prices, the running
total and BELUM DIBAYAR. When a bill that was sent is paid, only the receipt
prints; whatever was still unsent goes out as one last batch of slips first.
A sent item that is cancelled gets a BATAL notice naming only that item, or its
slip is withdrawn if nothing has taken it and nothing else is on it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Business, Order, PrintDevice, PrintJob, Staff

# till-9: "pickup" is an online-delivery app's driver (GoFood, GrabFood, ShopeeFood).
SERVICE_LABEL = {"dine_in": "Makan di sini", "takeaway": "Bawa pulang", "pickup": "Ojol", "delivery": "Antar kurir kafe"}
STATION_TITLE = {"bar": "BAR", "kitchen": "DAPUR"}
STATION_PRINTER = {"bar": "front", "kitchen": "kitchen"}
TICKET_KIND = {"bar": "bar_ticket", "kitchen": "kitchen_ticket"}
CANCEL_KIND = {"bar": "bar_cancel", "kitchen": "kitchen_cancel"}
# A device that took a job and has not reported back within this long leaves
# the outcome unknown: the screen says so rather than guessing.
UNCERTAIN_AFTER = timedelta(seconds=90)
# A job nobody printed for this long is *held* (prt-8): no device may take it
# until a person decides, so a printer that comes back after an outage does not
# pour out old orders as if they were new work. The person either lets it
# through (it prints marked TERLAMBAT) or withdraws it.
HOLD_AFTER = timedelta(minutes=15)
# A bridge reports its printer about every 30 seconds; after this long without
# word the till stops trusting the last report.
DEVICE_SILENT_AFTER = timedelta(seconds=90)


class PrintInvalid(Exception):
    """code: not_found · state (the job is not in a state that allows this) ·
    stale (a device answered for a claim it no longer holds)."""

    def __init__(self, code: str, status: str | None = None):
        self.code, self.status = code, status


# ── Identity on paper ────────────────────────────────────────────────────────


def _label(order: Order) -> str:
    return f"{order.service_number:03d}" if order.service_number is not None else f"#{str(order.id)[-4:].upper()}"


def heading_blocks(order: Order, batch: int | None = None) -> list[dict]:
    """"MEJA 7" over "Pesanan 042" for dine-in with a table; otherwise
    "PESANAN 042". The batch and a driver reference follow, never replace.
    `batch` overrides the order's own (an open bill's later sends)."""
    batch = order.batch_no if batch is None else batch
    table = (order.table_label or "").strip()
    if table.lower().startswith("meja"):
        table = table[4:].strip()
    blocks: list[dict] = []
    if order.order_type == "dine_in" and table:
        blocks.append({"t": "banner", "text": f"MEJA {table.upper()}"})
        blocks.append({"t": "line", "text": f"Pesanan {_label(order)}", "style": "bold", "size": "large"})
    else:
        blocks.append({"t": "banner", "text": f"PESANAN {_label(order)}"})
    if batch:
        blocks.append({"t": "line", "text": f"Tambahan {batch}", "style": "bold", "size": "large"})
    if order.external_ref:
        blocks.append({"t": "line", "text": f"Driver: {order.external_ref}", "style": "bold"})
    return blocks


def ticket_ref(order: Order, station: str, batch: int | None = None) -> str:
    """The slip's own reference: service number, station letter, batch."""
    letter = {"bar": "B", "kitchen": "D", "nota": "N"}[station]
    return f"{_label(order).lstrip('#')}-{letter}{(order.batch_no if batch is None else batch) or 0}"


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


def _slip(order: Order, station: str, lines, *, when: datetime, batch: int, cancel: bool = False,
          cancel_text: str = "Pesanan ini dibatalkan setelah slip dicetak. Konfirmasi ke kasir.",
          reason: str | None = None) -> dict:
    blocks: list[dict] = [{"t": "title", "text": STATION_TITLE[station]}]
    if cancel:
        blocks.append({"t": "label", "text": "BATAL"})
    elif batch:
        blocks.append({"t": "label", "text": "TAMBAHAN"})
    blocks += heading_blocks(order, batch)
    blocks.append({"t": "kv", "left": SERVICE_LABEL.get(order.order_type, order.order_type), "right": when.strftime("%H.%M")})
    blocks.append({"t": "kv", "left": f"Ref {ticket_ref(order, station, batch)}", "right": when.strftime("%d/%m")})
    blocks.append({"t": "rule"})
    if cancel:
        blocks.append({"t": "text", "text": "JANGAN DIBUAT / HENTIKAN:", "style": "bold"})
    blocks += _item_blocks(lines)
    note = (order.cart or {}).get("note")
    if note and not cancel:
        blocks += [{"t": "rule"}, {"t": "note", "text": f"Catatan pesanan: {note}"}]
    if cancel:
        blocks += [{"t": "rule"}, {"t": "text", "text": cancel_text, "style": "bold"}]
        if reason:
            blocks.append({"t": "note", "text": f"Alasan: {reason}"})
    return {"v": 1, "kind": CANCEL_KIND[station] if cancel else TICKET_KIND[station], "station": station, "blocks": blocks}


async def render_ticket(session: AsyncSession, order: Order, station: str, *, cancel: bool = False) -> dict | None:
    """A preparation slip: no prices, no payment, no customer details — the
    table or number, the service, the time, the reference, and exactly what to
    make with every choice written out."""
    lines = await _station_lines(session, order, station)
    if not lines:
        return None
    when = await _local(session, order.business_id, order.sold_at)
    return _slip(order, station, lines, when=when, batch=order.batch_no or 0, cancel=cancel)


async def render_receipt(session: AsyncSession, order: Order) -> dict:
    """The customer's receipt: everything bought, with prices and payment.

    till-12 (owner's feedback, 2 Oct 2026), laid out the way established cafés
    print theirs: the café's logo (its signage lettering, when turned on) or
    name, the address and contacts; the order number large between double
    rules — what the customer listens for; the details as label and value;
    each item on its own line with "2 x @15.000" and the amount; the count of
    items; Subtotal, the tax by its own name ("PB1 10%"), the TOTAL set apart
    between double rules; the payment and change; the café's own closing line;
    thanks; the WhatsApp QR when live; the internal reference small at the foot."""
    from app.services.business_profile import tax_line_label
    from app.services.orders import load_receipt, order_number
    from app.services.pricing import pricing_config

    data = await load_receipt(session, business_id=order.business_id, order_id=order.id)
    business = await session.get(Business, order.business_id)
    config = await pricing_config(session, order.business_id)
    when = await _local(session, order.business_id, order.sold_at)
    blocks: list[dict] = brand_blocks(business)
    blocks.append({"t": "rule", "style": "double"})
    blocks += heading_blocks(order)
    blocks.append({"t": "rule", "style": "double"})
    blocks.append({"t": "kv", "left": "Tanggal", "right": when.strftime("%d/%m/%Y %H.%M")})
    blocks.append({"t": "kv", "left": "Jenis", "right": SERVICE_LABEL.get(order.order_type, order.order_type)})
    if data["staff_name"]:
        blocks.append({"t": "kv", "left": "Kasir", "right": data["staff_name"]})
    if data.get("customer_name"):
        blocks.append({"t": "kv", "left": "Pelanggan", "right": data["customer_name"]})
    blocks.append({"t": "rule"})
    from app.services.kitchen import _lines_of

    sized = {l.line_id: l for l in await _lines_of(session, order)}
    count = Decimal(0)
    for entry in data["lines"]:
        line = entry["line"]
        if Decimal(line.quantity) <= 0:
            continue
        count += Decimal(line.quantity)
        k = sized.get(line.id)
        blocks.append({
            "t": "item_priced", "qty": _qty(line.quantity), "name": entry["item_name"], "size": k.size if k else None,
            "unit_price": _num(line.unit_price),
            "modifiers": [m.name + (f" +{_rp(m.price_delta)}" if Decimal(m.price_delta) else "") for m in entry["modifiers"]],
            "notes": line.notes, "amount": _num(line.line_total),
        })
    blocks.append({"t": "rule"})
    blocks.append({"t": "kv", "left": "Total item", "right": _qty(count)})
    tax_label = tax_line_label(config.tax_label, config.tax_rate, config.tax_inclusive)
    for label, value, sign in (
        ("Subtotal", order.subtotal, ""), ("Diskon", order.discount_total, "-"), ("Promo", order.promo_total, "-"),
        ("Voucher", order.voucher_total, "-"), ("Service", order.service_charge, ""),
        ("Ongkos kirim", order.delivery_fee, ""), (tax_label, order.tax_total, ""),
        ("Pembulatan", order.rounding, ""),
    ):
        if Decimal(value or 0) != 0 and (label != "Subtotal" or Decimal(order.subtotal) != Decimal(order.total)):
            blocks.append({"t": "kv", "left": label, "right": f"{sign}{_rp(value)}"})
    blocks += [
        {"t": "rule", "style": "double"},
        {"t": "total", "left": "TOTAL", "right": _rp(order.total)},
        {"t": "rule", "style": "double"},
    ]
    blocks += payment_blocks(data["payments"])
    blocks.append({"t": "rule"})
    if order.order_type != "dine_in":
        blocks.append({"t": "text", "text": "Sebutkan nomor pesanan saat mengambil.", "align": "center"})
    if business is not None and (business.receipt_footer or "").strip():
        blocks.append({"t": "text", "text": business.receipt_footer.strip(), "align": "center"})
    blocks.append({"t": "text", "text": "Terima kasih!", "align": "center", "style": "bold"})
    # till-7, route A: once the bot's number is live, the paper carries the QR
    # that opens WhatsApp with "STRUK <code>" ready, so the customer can keep
    # the receipt on their phone too.
    from app.services.receipt_delivery import ensure_receipt_code, whatsapp_link, whatsapp_live

    if whatsapp_live():
        code = await ensure_receipt_code(session, order)
        blocks += [
            {"t": "qr", "data": whatsapp_link(code)},
            {"t": "text", "text": "Simpan struk di WhatsApp: pindai, lalu kirim pesannya", "align": "center"},
        ]
    blocks.append({"t": "text", "text": f"Ref {order_number(order.id)}", "align": "center"})
    return {"v": 1, "kind": "receipt", "blocks": blocks}


def _num(amount) -> str:
    """Money without "Rp", for item lines: "15.000"."""
    return f"{Decimal(amount):,.0f}".replace(",", ".")


def brand_blocks(business: Business | None) -> list[dict]:
    """The top of anything the customer takes away (till-12): the café's logo
    when it has one turned on — the name rides along for a printer that cannot
    draw it — else the name; then the address and contacts."""
    name = (business.name if business else "").upper()
    logo = business.receipt_logo if business is not None else None
    if isinstance(logo, dict) and logo.get("bits"):
        blocks: list[dict] = [{"t": "logo", "text": name, "width": logo.get("width"), "height": logo.get("height"),
                               "bits": logo.get("bits")}]
    else:
        blocks = [{"t": "title", "text": name}]
    return blocks + _cafe_details(business)


PAYMENT_LABEL = {"cash": "Tunai", "qris": "QRIS", "points": "Poin", "transfer": "Transfer", "card": "Kartu", "ewallet": "E-wallet",
                 "other": "Lainnya"}
# till-9: an ojol order the customer paid inside the app.
APP_PAID_REFERENCE = "ojol"


def payment_label(p) -> str:
    """A payment's name on a receipt: an ojol order paid in the app says so."""
    if p.method == "other" and getattr(p, "reference", None) == APP_PAID_REFERENCE:
        return "Dibayar aplikasi"
    return PAYMENT_LABEL.get(p.method, str(p.method).upper())


def payment_blocks(payments) -> list[dict]:
    """Each payment by name; for cash the customer handed over more than the
    bill, what they gave and what they got back (till-5a)."""
    out: list[dict] = []
    for p in payments:
        if Decimal(p.amount) <= 0:
            continue
        out.append({"t": "kv", "left": payment_label(p), "right": _rp(p.amount)})
        tendered = getattr(p, "tendered", None)
        if p.method == "cash" and tendered is not None and Decimal(tendered) > Decimal(p.amount):
            out.append({"t": "kv", "left": "Diterima", "right": _rp(tendered)})
            out.append({"t": "kv", "left": "Kembali", "right": _rp(Decimal(tendered) - Decimal(p.amount))})
    return out


def _cafe_details(business: Business | None) -> list[dict]:
    """Address, then phone and Instagram on one line, under the name."""
    if business is None:
        return []
    out: list[dict] = []
    if (business.address or "").strip():
        out.append({"t": "text", "text": business.address.strip(), "align": "center"})
    contact = [c for c in ((business.contact_phone or "").strip(), (business.instagram or "").strip()) if c]
    if contact:
        out.append({"t": "text", "text": " · ".join(
            c if not c.startswith("@") else f"IG {c}" for c in contact
        ), "align": "center"})
    return out


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
    # till-5b: a café that asks first prints the customer's receipt only when
    # the cashier taps Kertas (enqueue_receipt); its slips always print.
    business = await session.get(Business, order.business_id)
    ask = business is not None and business.receipt_mode == "ask"
    jobs = [] if ask else [await enqueue_receipt(session, order, staff_id=staff_id)]
    if (order.cart or {}).get("batches"):
        # An open bill (bill-1): every item already went out on its batch's
        # slips, the last of them just before payment. Only the receipt is new.
        return jobs
    for station in ("bar", "kitchen"):
        doc = await render_ticket(session, order, station)
        if doc is not None:
            jobs.append(await _add_job(session, order, printer=STATION_PRINTER[station], kind=TICKET_KIND[station],
                                       document=doc, key=f"{order.id}:{TICKET_KIND[station]}", staff_id=staff_id))
    return jobs


async def enqueue_receipt(session: AsyncSession, order: Order, *, staff_id: uuid.UUID | None = None) -> PrintJob:
    """The customer's receipt, once per financial order whoever asks twice."""
    return await _add_job(session, order, printer="front", kind="receipt", document=await render_receipt(session, order),
                          key=f"{order.id}:receipt", staff_id=staff_id)


async def on_order_reversed(session: AsyncSession, order: Order, *, staff_id: uuid.UUID | None = None) -> list[PrintJob]:
    """A paid order was voided or refunded. For each station slip: withdraw it
    if nothing has taken it yet; otherwise send a BATAL notice to that printer,
    because paper may already be on the pass."""
    if (order.cart or {}).get("batches"):
        # An open bill has one slip per send and station: cancel item by item.
        return await cancel_cart_lines(
            session, order, [(l, Decimal(l["quantity"])) for l in order.cart.get("lines", []) if l.get("sent_batch")], [],
            key="reversed", staff_id=staff_id,
        )
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


def is_held(job: PrintJob, now: datetime | None = None) -> bool:
    """Waiting so long that it must not print as fresh work without a person's say."""
    moment = now or datetime.now(timezone.utc)
    return job.status == "pending" and job.released_at is None and job.created_at is not None \
        and moment - job.created_at >= HOLD_AFTER


def display_status(job: PrintJob, now: datetime | None = None) -> str:
    """What the screen may honestly say: pending · held · sending · uncertain ·
    delivered · printed · failed · cancelled.

    *delivered*: a device handed the bytes to the printer but could not ask it
    whether it finished (prt-8). *printed*: the printer confirmed it processed
    the job through the cut, or a person holds the paper, or an older device
    said so without evidence."""
    moment = now or datetime.now(timezone.utc)
    if job.status == "claimed":
        if job.uncertain_at is not None:
            return "uncertain"
        return "uncertain" if job.claimed_at is None or moment - job.claimed_at >= UNCERTAIN_AFTER else "sending"
    if is_held(job, moment):
        return "held"
    if job.status == "printed" and job.evidence == "bytes_delivered" and job.confirmed_by is None:
        return "delivered"
    return job.status


# Jobs written in one transaction share its `now()`, so within a moment the
# paper comes out in this order: the customer's copy first, then the slips
# (Bar before Dapur, so lists read the same way every time), then notices.
PAPER_ORDER = case(
    {"receipt": 0, "nota": 0, "bar_ticket": 1, "kitchen_ticket": 2, "bar_cancel": 3, "kitchen_cancel": 4},
    value=PrintJob.kind, else_=5,
)


async def claim_next(session: AsyncSession, *, printer: str, device: str, now: datetime | None = None) -> PrintJob | None:
    """A printer device asks for work. Oldest pending job for that printer, taken
    with `for update skip locked` so two devices never take the same job."""
    moment = now or datetime.now(timezone.utc)
    job = (await session.execute(
        select(PrintJob).where(
            PrintJob.printer == printer, PrintJob.status == "pending",
            or_(PrintJob.released_at.is_not(None), PrintJob.created_at > moment - HOLD_AFTER),
        )
        .order_by(PrintJob.created_at, PAPER_ORDER, PrintJob.id).limit(1).with_for_update(skip_locked=True)
    )).scalar_one_or_none()
    if job is None:
        return None
    job.status, job.claimed_at, job.claimed_by = "claimed", moment, device[:60]
    job.attempts = (job.attempts or 0) + 1
    job.error, job.uncertain_at, job.evidence = None, None, None
    job.updated_at = moment
    await session.flush()
    return job


async def _job(session: AsyncSession, job_id: uuid.UUID, lock: bool = True) -> PrintJob:
    stmt = select(PrintJob).where(PrintJob.id == job_id)
    job = (await session.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
    if job is None:
        raise PrintInvalid("not_found")
    return job


def _holds(job: PrintJob, device: str, attempt: int | None) -> bool:
    """Is this answer about the claim the job is on now? The device must be the
    one that took it, and the attempt (every claim adds one) must be this one:
    a late answer about an earlier try, even from the same device after it took
    the job again, is not about this one. An answer that names no attempt (the
    prt-4 form) is only unambiguous while the job has been taken once."""
    if job.claimed_by != device[:60]:
        return False
    return attempt == job.attempts if attempt is not None else (job.attempts or 0) <= 1


async def report_result(session: AsyncSession, *, job_id: uuid.UUID, ok: bool | None = None, device: str,
                        error: str | None = None, attempt: int | None = None, outcome: str | None = None,
                        evidence: str | None = None, now: datetime | None = None) -> PrintJob:
    """The device says what happened: printed, failed, or *uncertain* (paper
    may or may not exist; the job stays taken and the till offers a marked
    reprint). Only the device holding the job, about its current attempt, can
    answer, and an answer arriving twice is the same answer."""
    moment = now or datetime.now(timezone.utc)
    outcome = outcome or ("printed" if ok else "failed")
    job = await _job(session, job_id)
    if not _holds(job, device, attempt):
        raise PrintInvalid("stale", job.status)
    if outcome == "uncertain":
        if job.status != "claimed":
            raise PrintInvalid("state", job.status)
        if job.uncertain_at is None:
            job.uncertain_at, job.error = moment, (error or "printer tidak memastikan slip tercetak")[:300]
            job.updated_at = moment
            await session.flush()
        return job
    target = "printed" if outcome == "printed" else "failed"
    if job.status == target:
        return job
    if job.status != "claimed":
        raise PrintInvalid("state", job.status)
    if target == "printed":
        job.status, job.printed_at, job.error, job.evidence = "printed", moment, None, evidence
    else:
        job.status, job.failed_at, job.error = "failed", moment, (error or "printer melaporkan gagal")[:300]
    job.updated_at = moment
    await session.flush()
    return job


async def release(session: AsyncSession, *, job_id: uuid.UUID, device: str, attempt: int, error: str | None = None,
                  now: datetime | None = None) -> PrintJob:
    """The device took the job and sent **nothing** to the printer (unreachable,
    out of paper, cover open before it started). No paper can exist, so the
    job goes back in the queue unmarked, with the reason for the till to show.
    A device that may have sent bytes reports *uncertain* instead."""
    moment = now or datetime.now(timezone.utc)
    job = await _job(session, job_id)
    if job.status == "pending" and job.claimed_by is None:
        return job                                    # already released: same answer twice
    if not _holds(job, device, attempt):
        raise PrintInvalid("stale", job.status)
    if job.status != "claimed" or job.uncertain_at is not None:
        raise PrintInvalid("state", job.status)
    job.status, job.claimed_at, job.claimed_by = "pending", None, None
    job.error = (error or "belum terkirim ke printer")[:300]
    job.updated_at = moment
    await session.flush()
    return job


async def held_by(session: AsyncSession, *, printer: str, device: str) -> list[PrintJob]:
    """Jobs this device has taken and not answered for, so a restarted bridge
    can release the ones it never sent. Jobs it already called uncertain are
    not offered back: they may be on paper."""
    return list((await session.execute(
        select(PrintJob).where(PrintJob.printer == printer, PrintJob.status == "claimed",
                               PrintJob.claimed_by == device[:60], PrintJob.uncertain_at.is_(None))
        .order_by(PrintJob.claimed_at)
    )).scalars().all())


async def let_through(session: AsyncSession, *, job_id: uuid.UUID, staff_id: uuid.UUID | None,
                      now: datetime | None = None) -> PrintJob:
    """A person lets a held job print. It keeps its identity (so a later
    cancellation still finds it) and prints marked TERLAMBAT."""
    moment = now or datetime.now(timezone.utc)
    job = await _job(session, job_id)
    if job.status != "pending":
        raise PrintInvalid("state", job.status)
    if job.released_at is None:
        job.released_at, job.released_by, job.updated_at = moment, staff_id, moment
        await session.flush()
    return job


async def withdraw(session: AsyncSession, *, job_id: uuid.UUID, staff_id: uuid.UUID | None,
                   now: datetime | None = None) -> PrintJob:
    """A person decides a held or failed job is not needed any more. Neither can
    be on paper (held: nothing took it; failed: the device said nothing came
    out), so it is withdrawn like an untouched slip. Nothing is deleted."""
    moment = now or datetime.now(timezone.utc)
    job = await _job(session, job_id)
    if job.status == "cancelled" and job.withdrawn_by is not None:
        return job
    if not (job.status == "failed" or is_held(job, moment)):
        raise PrintInvalid("state", job.status)
    job.status, job.withdrawn_by, job.updated_at = "cancelled", staff_id, moment
    await session.flush()
    return job


async def device_document(session: AsyncSession, job: PrintJob) -> dict:
    """The document as a device should print it now. A job a person let through
    after it was held says so on paper, with when it was made, so nobody at the
    pass mistakes a late slip for a new order. The stored document is untouched."""
    doc = job.document
    if job.released_at is None:
        return doc
    made = await _local(session, job.business_id, job.created_at)
    let = await _local(session, job.business_id, job.released_at)
    staff = await session.get(Staff, job.released_by) if job.released_by else None
    blocks = list(doc.get("blocks", []))
    insert_at = 1 if blocks and blocks[0].get("t") == "title" else 0
    blocks[insert_at:insert_at] = [
        {"t": "label", "text": "TERLAMBAT"},
        {"t": "text", "text": f"Dibuat {made.strftime('%d/%m %H.%M')} · dicetak {let.strftime('%H.%M')}"
                              f"{' · ' + staff.name if staff else ''}", "align": "center"},
    ]
    return {**doc, "blocks": blocks, "late": True}


DEVICE_STATES = ("ready", "reachable", "paper_low", "paper_out", "cover_open", "offline", "error", "unknown")


async def record_heartbeat(session: AsyncSession, *, business_id: uuid.UUID, printer: str, device: str, state: str,
                           detail: str | None, version: str | None, test_result: str | None = None,
                           test_detail: str | None = None) -> PrintDevice | None:
    """What a bridge worker last saw. It also carries the answer to a test
    ticket the owner asked for, which is the only way that answer comes back."""
    from sqlalchemy.dialects.postgresql import insert

    values = {"business_id": business_id, "printer": printer, "device": device[:60], "state": state,
              "detail": detail, "version": version}
    update = {"state": state, "detail": detail, "version": version, "last_seen_at": func.now()}
    if test_result is not None:
        if test_result not in TEST_RESULTS:
            raise PrintInvalid("state", test_result)
        values |= {"test_result": test_result, "test_detail": test_detail, "test_result_at": func.now()}
        update |= {"test_result": test_result, "test_detail": test_detail, "test_result_at": func.now()}
    stmt = insert(PrintDevice).values(**values)
    await session.execute(stmt.on_conflict_do_update(
        index_elements=[PrintDevice.business_id, PrintDevice.printer, PrintDevice.device], set_=update,
    ))
    await session.flush()
    return await device_for(session, printer=printer, device=device)


TEST_RESULTS = ("printed", "delivered", "uncertain", "failed")
# A test ticket asked for and not picked up within this long is stale: the
# bridge was not running, and the owner should be told that rather than left
# waiting for paper that comes out an hour later.
TEST_EXPIRES_AFTER = timedelta(minutes=10)


async def request_test(session: AsyncSession, *, business_id: uuid.UUID, printer: str,
                       now: datetime | None = None) -> list[PrintDevice]:
    """The owner asks one printer for a test ticket. It goes to whichever
    bridge workers are reporting for that printer; with none, there is nobody
    to print it and the caller says so."""
    moment = now or datetime.now(timezone.utc)
    rows = [d for d in await devices(session) if d.printer == printer]
    for row in rows:
        row.test_requested_at, row.test_result, row.test_detail, row.test_result_at = moment, None, None, None
    await session.flush()
    return rows


def test_pending(device: PrintDevice | None, now: datetime | None = None) -> bool:
    """Is this device being asked for a test ticket right now?"""
    if device is None or device.test_requested_at is None or device.test_result_at is not None:
        return False
    return (now or datetime.now(timezone.utc)) - device.test_requested_at < TEST_EXPIRES_AFTER


def test_state(device: PrintDevice, now: datetime | None = None) -> str | None:
    """waiting · printed · delivered · uncertain · failed · not_picked_up · None."""
    if device.test_requested_at is None:
        return None
    if device.test_result is not None:
        return device.test_result
    return "waiting" if test_pending(device, now) else "not_picked_up"


async def device_for(session: AsyncSession, *, printer: str, device: str) -> PrintDevice | None:
    return (await session.execute(
        select(PrintDevice).where(PrintDevice.printer == printer, PrintDevice.device == device[:60])
    )).scalar_one_or_none()


async def devices(session: AsyncSession) -> list[PrintDevice]:
    """Bridges heard from in the last week (an old install's row stays in the
    table; it just stops being shown)."""
    since = datetime.now(timezone.utc) - timedelta(days=7)
    return list((await session.execute(
        select(PrintDevice).where(PrintDevice.last_seen_at >= since)
        .order_by(PrintDevice.printer, PrintDevice.last_seen_at.desc())
    )).scalars().all())


async def retry(session: AsyncSession, *, job_id: uuid.UUID, staff_id: uuid.UUID | None = None,
                now: datetime | None = None) -> PrintJob:
    """Put a job the printer said it could not print back in the queue. Only a
    *failed* job: an uncertain one may be on paper, so it takes a marked reprint
    or a person's confirmation instead. A person retrying an old job has
    decided it should still print, so it is let through (marked TERLAMBAT)
    rather than held again."""
    moment = now or datetime.now(timezone.utc)
    job = await _job(session, job_id)
    if job.status == "pending":
        return job
    if job.status != "failed":
        raise PrintInvalid("state", job.status)
    job.status, job.claimed_at, job.claimed_by, job.updated_at = "pending", None, None, moment
    if job.released_at is None and job.created_at is not None and moment - job.created_at >= HOLD_AFTER:
        job.released_at, job.released_by = moment, staff_id
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


# ── Open bills: sends and cancelled sent items (bill-1) ──────────────────────


def cart_station(line: dict) -> str | None:
    """Where a cart line is made: bar, kitchen, or None (nothing to make). An
    item nobody assigned goes to the front, flagged, like a paid order's."""
    st = line.get("prep_station")
    if st is None:
        return "bar"
    return None if st == "none" else st


def _cart_view(line: dict, quantity: Decimal | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        quantity=Decimal(line["quantity"]) if quantity is None else quantity, item_name=line["item_name"], name=line["item_name"],
        size=line.get("variant_name"), modifiers=list(line.get("modifier_names", [])), notes=line.get("notes"),
        unassigned=line.get("prep_station") is None,
    )


async def render_nota(session: AsyncSession, order: Order, lines: list[dict], *, batch: int, when: datetime,
                      staff_name: str | None) -> dict:
    """What the staff put on the table after a send: the new items with their
    prices and the bill so far. Not a receipt: nothing has been paid."""
    business = await session.get(Business, order.business_id)
    blocks: list[dict] = brand_blocks(business)   # till-12: the same top as the receipt
    blocks.append({"t": "rule", "style": "double"})
    if batch:
        blocks.append({"t": "label", "text": "TAMBAHAN"})
    blocks += heading_blocks(order, batch)
    blocks.append({"t": "kv", "left": SERVICE_LABEL.get(order.order_type, order.order_type), "right": when.strftime("%d/%m/%Y %H.%M")})
    blocks.append({"t": "kv", "left": f"Nota {ticket_ref(order, 'nota', batch)}", "right": staff_name or ""})
    blocks.append({"t": "rule"})
    for l in lines:
        names = list(l.get("modifier_names", []))
        prices = list(l.get("modifier_prices", []) or ["0"] * len(names))
        blocks.append({
            "t": "item_priced", "qty": _qty(l["quantity"]), "name": l["item_name"], "size": l.get("variant_name"),
            "modifiers": [n + (f" +{_rp(p)}" if Decimal(p) else "") for n, p in zip(names, prices)],
            "notes": l.get("notes"), "amount": _rp(l["line_total"]),
        })
    blocks.append({"t": "rule"})
    blocks.append({"t": "kv", "left": "Pesanan ini", "right": _rp(sum((Decimal(l["line_total"]) for l in lines), Decimal(0)))})
    blocks.append({"t": "total", "left": "TOTAL SEMENTARA", "right": _rp(order.total)})
    blocks += [
        {"t": "label", "text": "BELUM DIBAYAR"},
        {"t": "text", "text": "Bayar di kasir sebelum pulang. Terima kasih!", "align": "center"},
    ]
    return {"v": 1, "kind": "nota", "blocks": blocks}


def send_key(order: Order, n: int, kind: str) -> str:
    return f"{order.id}:send:{n}:{kind}"


async def enqueue_for_send(session: AsyncSession, order: Order, *, n: int, lines: list[dict], staff_id: uuid.UUID | None,
                           nota: bool = True) -> list[PrintJob]:
    """Batch `n` of an open bill went out: one slip per station that has items
    in it, and the table's nota. Keyed on the batch, so a retry adds nothing."""
    batch = n - 1
    when = await _local(session, order.business_id, datetime.now(timezone.utc))
    jobs: list[PrintJob] = []
    if nota:
        staff = await session.get(Staff, staff_id) if staff_id else None
        doc = await render_nota(session, order, lines, batch=batch, when=when, staff_name=staff.name if staff else None)
        jobs.append(await _add_job(session, order, printer="front", kind="nota", document=doc,
                                   key=send_key(order, n, "nota"), staff_id=staff_id))
    for station in ("bar", "kitchen"):
        mine = [_cart_view(l) for l in lines if cart_station(l) == station]
        if mine:
            jobs.append(await _add_job(session, order, printer=STATION_PRINTER[station], kind=TICKET_KIND[station],
                                       document=_slip(order, station, mine, when=when, batch=batch),
                                       key=send_key(order, n, TICKET_KIND[station]), staff_id=staff_id))
    return jobs


async def cancel_cart_lines(session: AsyncSession, order: Order, removed: list[tuple[dict, Decimal]], remaining: list[dict], *,
                            key: str, staff_id: uuid.UUID | None, reason: str | None = None) -> list[PrintJob]:
    """Sent items that will not be made. Per station and batch: a slip nothing
    has taken, with nothing left on it, is withdrawn; otherwise the station
    gets one BATAL notice naming exactly the cancelled items and quantities."""
    notices: list[PrintJob] = []
    when = await _local(session, order.business_id, datetime.now(timezone.utc))
    for station in ("bar", "kitchen"):
        by_batch: dict[int, list[tuple[dict, Decimal]]] = {}
        for line, qty in removed:
            if line.get("sent_batch") and cart_station(line) == station:
                by_batch.setdefault(int(line["sent_batch"]), []).append((line, qty))
        tell: list[SimpleNamespace] = []
        for n, items in sorted(by_batch.items()):
            slip = (await session.execute(
                select(PrintJob).where(PrintJob.dedupe_key == send_key(order, n, TICKET_KIND[station]))
            )).scalar_one_or_none()
            if slip is None:
                continue          # nothing was ever owed to paper for this batch
            still_on_it = any(r.get("sent_batch") == n and cart_station(r) == station for r in remaining)
            if not still_on_it:
                withdrawn = await session.execute(
                    update(PrintJob).where(PrintJob.id == slip.id, PrintJob.status == "pending")
                    .values(status="cancelled", updated_at=func.now()).execution_options(synchronize_session=False)
                )
                if withdrawn.rowcount == 1:
                    await session.refresh(slip)
                    continue
            tell += [_cart_view(line, qty) for line, qty in items]
        if tell:
            doc = _slip(order, station, tell, when=when, batch=0, cancel=True, reason=reason,
                        cancel_text="Item di atas dibatalkan setelah slip dicetak. Konfirmasi ke kasir.")
            notices.append(await _add_job(session, order, printer=STATION_PRINTER[station], kind=CANCEL_KIND[station],
                                          document=doc, key=f"{order.id}:{key}:{CANCEL_KIND[station]}", staff_id=staff_id))
    return notices
