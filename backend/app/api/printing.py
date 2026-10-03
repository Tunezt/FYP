"""Print endpoints (prt-4): printer devices pull, people recover.

Three audiences, three scopes:

* **A printer device** (scope `printer`, one token per printer: `front` or
  `kitchen`) pulls work: `POST /print/agent/claim` takes the oldest pending job
  for its printer, `POST /print/agent/jobs/{id}/result` says whether paper came
  out. The café's API runs in the cloud and cannot reach a printer on the café's
  wifi; pulling over HTTPS is what lets a printer or a small bridge beside it
  work behind any router. What sits on the other end is a hardware decision
  recorded in docs/printing.md, not something this module pretends to know.
  The print bridge (bridge/print_bridge.py, prt-8) also uses: `release` (took
  it, sent nothing), `held` (what am I still holding, after a restart),
  `heartbeat` (what my printer says), and results bound to the claim's attempt.
* **The till** (scope `pos`) sees the queue with honest states and recovers:
  retry a failed job, reprint (marked CETAK ULANG), confirm paper that a person
  is holding, print a job through the browser as a labelled manual fallback,
  and decide about a *held* job (too old to print as fresh work): let it
  through marked TERLAMBAT, or withdraw it.
* **The owner** (scope `owner`) issues a printer device's token. Re-pairing the
  till (M15-T8) raises the business's pairing generation and so also retires
  every printer token issued before it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.core.db import SessionLocal, set_tenant
from app.core.deps import OwnerCtx, PosCtx
from app.core.security import create_token, decode_token
from app.models import Business, Order, PrintJob
from app.services import printing
from app.services.service_numbers import batch_label

agent_router = APIRouter(prefix="/print/agent", tags=["print-agent"])
pos_router = APIRouter(prefix="/pos/print-jobs", tags=["pos"])
owner_router = APIRouter(prefix="/api/printers", tags=["dashboard"])

PRINTER_TOKEN_DAYS = 365
KIND_LABEL = {
    "receipt": "Struk", "nota": "Nota meja", "bar_ticket": "Slip bar", "kitchen_ticket": "Slip dapur",
    "bar_cancel": "Batal (bar)", "kitchen_cancel": "Batal (dapur)",
}
_STATE_ERRORS = {
    "pending": "Slip ini masih menunggu printer",
    "claimed": "Slip ini sedang dikirim ke printer — tunggu hasilnya atau cetak ulang",
    "printed": "Slip ini sudah tercetak",
    "failed": "Slip ini gagal dicetak — coba lagi atau cetak ulang",
    "cancelled": "Slip ini sudah dibatalkan karena pesanannya dibatalkan",
}


class PrintJobOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    printer: Literal["front", "kitchen"]
    kind: str
    kind_label: str
    copy_kind: Literal["original", "reprint"]   # the job row's `copy`
    status: str                       # pending · held · sending · uncertain · delivered · printed · failed · cancelled
    order_label: str                  # "Pesanan 042" / "Pesanan 042 · Tambahan 1"
    table_label: str | None = None
    attempts: int
    claimed_by: str | None = None
    error: str | None = None
    confirmed_by_person: bool = False
    evidence: str | None = None       # printer_status · bytes_delivered (prt-8)
    released: bool = False            # a person let a held job through: it prints marked TERLAMBAT
    withdrawn_by_person: bool = False
    created_at: datetime
    claimed_at: datetime | None = None
    printed_at: datetime | None = None
    reprint_of: uuid.UUID | None = None


class PrintJobDocumentOut(PrintJobOut):
    document: dict


class AgentClaimIn(BaseModel):
    device: str = Field(min_length=1, max_length=60)


class AgentResultIn(BaseModel):
    """`ok` is the original form (prt-4). A bridge sends `outcome` instead, with
    the `attempt` it was given at claim time and how it knows (`evidence`)."""

    device: str = Field(min_length=1, max_length=60)
    ok: bool | None = None
    outcome: Literal["printed", "failed", "uncertain"] | None = None
    attempt: int | None = Field(default=None, ge=1)
    evidence: Literal["printer_status", "bytes_delivered"] | None = None
    error: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _one_answer(self):
        if self.outcome is None and self.ok is None:
            raise ValueError("Isi ok atau outcome")
        if self.outcome is not None and self.ok is not None and (self.outcome == "printed") != self.ok:
            raise ValueError("ok dan outcome saling bertentangan")
        if self.evidence is not None and (self.outcome or ("printed" if self.ok else "failed")) != "printed":
            raise ValueError("evidence hanya untuk hasil tercetak")
        return self


class AgentReleaseIn(BaseModel):
    device: str = Field(min_length=1, max_length=60)
    attempt: int = Field(ge=1)
    error: str | None = Field(default=None, max_length=300)


class AgentHeldOut(BaseModel):
    class Held(BaseModel):
        id: uuid.UUID
        attempt: int
        claimed_at: datetime | None

    jobs: list[Held]


class AgentHeartbeatIn(BaseModel):
    device: str = Field(min_length=1, max_length=60)
    state: Literal[printing.DEVICE_STATES]  # type: ignore[valid-type]
    detail: str | None = Field(default=None, max_length=200)
    version: str | None = Field(default=None, max_length=40)
    # The answer to a test ticket the owner asked for, when there was one.
    test_result: Literal[printing.TEST_RESULTS] | None = None  # type: ignore[valid-type]
    test_detail: str | None = Field(default=None, max_length=300)


class AgentHeartbeatOut(BaseModel):
    ok: bool = True
    test_print: bool = False          # print a test ticket now and report it back


class PrintDeviceOut(BaseModel):
    printer: Literal["front", "kitchen"]
    device: str
    state: str
    detail: str | None
    version: str | None
    last_seen_at: datetime
    silent: bool                     # nothing heard for longer than a bridge's heartbeat allows
    test_state: str | None = None    # waiting · printed · delivered · uncertain · failed · not_picked_up
    test_detail: str | None = None
    test_at: datetime | None = None


class PrintRoutingOut(BaseModel):
    """Where each product's preparation ticket goes. Set per product by the
    owner, never guessed from its name (prt-2)."""

    class Unmapped(BaseModel):
        id: uuid.UUID
        name: str

    bar: int
    kitchen: int
    none: int
    unmapped: list[Unmapped]


class AgentClaimOut(BaseModel):
    job: PrintJobDocumentOut | None
    test_print: bool = False


class BrowserResultIn(BaseModel):
    ok: bool
    error: str | None = Field(default=None, max_length=300)


class PrinterTokenOut(BaseModel):
    printer: Literal["front", "kitchen"]
    token: str
    claim_path: str
    result_path: str
    expires_at: datetime


def job_out(job: PrintJob, order: Order | None, model=PrintJobOut, now: datetime | None = None, document: dict | None = None):
    extra = {"document": document if document is not None else job.document} if model is PrintJobDocumentOut else {}
    return model(
        id=job.id, order_id=job.order_id, printer=job.printer, kind=job.kind, kind_label=KIND_LABEL.get(job.kind, job.kind),
        copy_kind=job.copy, status=printing.display_status(job, now), attempts=job.attempts,
        order_label=batch_label(order) if order is not None else "",
        table_label=order.table_label if order is not None and order.order_type == "dine_in" else None,
        claimed_by=job.claimed_by, error=job.error, confirmed_by_person=job.confirmed_by is not None,
        evidence=job.evidence, released=job.released_at is not None, withdrawn_by_person=job.withdrawn_by is not None,
        created_at=job.created_at, claimed_at=job.claimed_at, printed_at=job.printed_at, reprint_of=job.reprint_of,
        **extra,
    )


def _state_error(exc: printing.PrintInvalid) -> HTTPException:
    if exc.code == "not_found":
        return HTTPException(status_code=404, detail="Slip cetak tidak ditemukan")
    if exc.code == "stale":
        return HTTPException(status_code=409, detail="Laporan ini untuk percobaan cetak yang sudah lewat atau dari perangkat lain — diabaikan")
    return HTTPException(status_code=409, detail=_STATE_ERRORS.get(exc.status or "", "Slip ini tidak bisa diubah sekarang"))


# ── Printer devices ──────────────────────────────────────────────────────────


class PrinterCtx:
    def __init__(self, business_id: uuid.UUID, printer: str, session):
        self.business_id, self.printer, self.session = business_id, printer, session


async def printer_ctx(request: Request):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Perangkat printer belum terdaftar")
    try:
        claims = decode_token(header.removeprefix("Bearer "))
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token printer tidak berlaku — buat token baru di dashboard")
    if claims.get("scope") != "printer" or claims.get("printer") not in ("front", "kitchen"):
        raise HTTPException(status_code=403, detail="Fitur ini khusus perangkat printer")
    business_id = uuid.UUID(claims["business_id"])
    request.state.business_id = business_id
    async with SessionLocal() as session:
        await set_tenant(session, business_id)
        current = (await session.execute(select(Business.pairing_generation).where(Business.id == business_id))).scalar_one_or_none()
        if current is None or int(current) != int(claims.get("gen", 1)):
            raise HTTPException(status_code=401, detail="Token printer sudah dicabut — buat token baru di dashboard")
        try:
            yield PrinterCtx(business_id, claims["printer"], session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@agent_router.post("/claim", response_model=AgentClaimOut)
async def agent_claim(payload: AgentClaimIn, ctx: PrinterCtx = Depends(printer_ctx, scope="function")):
    """The oldest pending job for this token's printer, now held by this device.
    `job: null` when there is nothing to print."""
    device = await printing.device_for(ctx.session, printer=ctx.printer, device=payload.device)
    test = printing.test_pending(device)
    job = await printing.claim_next(ctx.session, printer=ctx.printer, device=payload.device)
    if job is None:
        return AgentClaimOut(job=None, test_print=test)
    order = await ctx.session.get(Order, job.order_id)
    return AgentClaimOut(job=job_out(job, order, PrintJobDocumentOut, document=await printing.device_document(ctx.session, job)),
                         test_print=test)


async def _own_job(ctx: PrinterCtx, job_id: uuid.UUID) -> None:
    job = (await ctx.session.execute(select(PrintJob).where(PrintJob.id == job_id))).scalar_one_or_none()
    if job is None or job.printer != ctx.printer:
        raise HTTPException(status_code=404, detail="Slip cetak tidak ditemukan")


@agent_router.post("/jobs/{job_id}/result", response_model=PrintJobOut)
async def agent_result(job_id: uuid.UUID, payload: AgentResultIn, ctx: PrinterCtx = Depends(printer_ctx, scope="function")):
    """Printed, failed, or uncertain — about the claim this device holds now
    (`attempt` from the claim). An answer about an earlier claim is refused."""
    await _own_job(ctx, job_id)
    try:
        job = await printing.report_result(ctx.session, job_id=job_id, ok=payload.ok, outcome=payload.outcome,
                                           device=payload.device, attempt=payload.attempt, evidence=payload.evidence,
                                           error=payload.error)
    except printing.PrintInvalid as exc:
        raise _state_error(exc)
    return job_out(job, await ctx.session.get(Order, job.order_id))


@agent_router.post("/jobs/{job_id}/release", response_model=PrintJobOut)
async def agent_release(job_id: uuid.UUID, payload: AgentReleaseIn, ctx: PrinterCtx = Depends(printer_ctx, scope="function")):
    """The device took the job and sent nothing to the printer: back in the queue."""
    await _own_job(ctx, job_id)
    try:
        job = await printing.release(ctx.session, job_id=job_id, device=payload.device, attempt=payload.attempt, error=payload.error)
    except printing.PrintInvalid as exc:
        raise _state_error(exc)
    return job_out(job, await ctx.session.get(Order, job.order_id))


@agent_router.post("/held", response_model=AgentHeldOut)
async def agent_held(payload: AgentClaimIn, ctx: PrinterCtx = Depends(printer_ctx, scope="function")):
    """What this device has taken and not answered for, so a restarted bridge
    can release what it never sent."""
    jobs = await printing.held_by(ctx.session, printer=ctx.printer, device=payload.device)
    return AgentHeldOut(jobs=[AgentHeldOut.Held(id=j.id, attempt=j.attempts, claimed_at=j.claimed_at) for j in jobs])


@agent_router.post("/heartbeat", response_model=AgentHeartbeatOut)
async def agent_heartbeat(payload: AgentHeartbeatIn, ctx: PrinterCtx = Depends(printer_ctx, scope="function")):
    """The bridge's last look at its printer, for the till to show — and the
    channel for the owner's setup test: the answer comes up, the request goes
    back down."""
    device = await printing.record_heartbeat(
        ctx.session, business_id=ctx.business_id, printer=ctx.printer, device=payload.device, state=payload.state,
        detail=payload.detail, version=payload.version, test_result=payload.test_result, test_detail=payload.test_detail)
    return AgentHeartbeatOut(test_print=printing.test_pending(device))


# ── The till ─────────────────────────────────────────────────────────────────


@pos_router.get("", response_model=list[PrintJobOut])
async def pos_print_jobs(
    ctx: PosCtx,
    printer: Literal["front", "kitchen"] | None = Query(default=None),
    scope: Literal["open", "recent"] = Query(default="open"),
):
    """`open`: everything not yet settled — waiting, held, being sent,
    uncertain, failed — **however old**: a job a printer could still take must
    never drop off the cashier's screen (prt-8). `recent`: the last 12 hours of
    everything, newest first."""
    stmt = select(PrintJob)
    if printer:
        stmt = stmt.where(PrintJob.printer == printer)
    if scope == "open":
        stmt = stmt.where(PrintJob.status.in_(("pending", "claimed", "failed"))).order_by(PrintJob.created_at, printing.PAPER_ORDER, PrintJob.id)
    else:
        since = datetime.now(timezone.utc) - timedelta(hours=12)
        stmt = stmt.where(PrintJob.created_at >= since).order_by(PrintJob.created_at.desc(), PrintJob.id).limit(100)
    jobs = (await ctx.session.execute(stmt)).scalars().all()
    orders = {o.id: o for o in (await ctx.session.execute(select(Order).where(Order.id.in_({j.order_id for j in jobs})))).scalars()} if jobs else {}
    now = datetime.now(timezone.utc)
    return [job_out(j, orders.get(j.order_id), now=now) for j in jobs]


@pos_router.get("/{job_id}", response_model=PrintJobDocumentOut)
async def pos_print_job(job_id: uuid.UUID, ctx: PosCtx):
    job = (await ctx.session.execute(select(PrintJob).where(PrintJob.id == job_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Slip cetak tidak ditemukan")
    return job_out(job, await ctx.session.get(Order, job.order_id), PrintJobDocumentOut)


async def _act(ctx, job_id: uuid.UUID, fn, **kwargs) -> PrintJobOut:
    try:
        job = await fn(ctx.session, job_id=job_id, **kwargs)
    except printing.PrintInvalid as exc:
        raise _state_error(exc)
    return job_out(job, await ctx.session.get(Order, job.order_id))


@pos_router.post("/{job_id}/retry", response_model=PrintJobOut)
async def pos_retry(job_id: uuid.UUID, ctx: PosCtx):
    """A failed job goes back in the queue. Uncertain ones do not: see reprint."""
    return await _act(ctx, job_id, printing.retry, staff_id=ctx.staff_id)


@pos_router.post("/{job_id}/release", response_model=PrintJobOut)
async def pos_let_through(job_id: uuid.UUID, ctx: PosCtx):
    """A held (old) job may print after all; it will say TERLAMBAT on paper."""
    return await _act(ctx, job_id, printing.let_through, staff_id=ctx.staff_id)


@pos_router.post("/{job_id}/withdraw", response_model=PrintJobOut)
async def pos_withdraw(job_id: uuid.UUID, ctx: PosCtx):
    """A held or failed job is not needed any more. Recorded as this person's call."""
    return await _act(ctx, job_id, printing.withdraw, staff_id=ctx.staff_id)


@pos_router.post("/{job_id}/reprint", response_model=PrintJobOut, status_code=201)
async def pos_reprint(job_id: uuid.UUID, ctx: PosCtx):
    return await _act(ctx, job_id, printing.reprint, staff_id=ctx.staff_id)


@pos_router.post("/{job_id}/confirm", response_model=PrintJobOut)
async def pos_confirm(job_id: uuid.UUID, ctx: PosCtx):
    """A person has the paper in hand."""
    return await _act(ctx, job_id, printing.confirm_printed, staff_id=ctx.staff_id)


@pos_router.post("/{job_id}/browser", response_model=PrintJobDocumentOut)
async def pos_browser_claim(job_id: uuid.UUID, ctx: PosCtx):
    """Manual fallback: this tablet takes the job to print through the browser's
    own print dialog. The job becomes *sending*, never *printed*: a closed print
    dialog proves nothing, so the till must ask the person afterwards."""
    job = (await ctx.session.execute(select(PrintJob).where(PrintJob.id == job_id).with_for_update())).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Slip cetak tidak ditemukan")
    if job.status == "failed":
        job = await printing.retry(ctx.session, job_id=job_id)
    if job.status != "pending":
        raise HTTPException(status_code=409, detail=_STATE_ERRORS.get(job.status, "Slip ini tidak bisa dicetak sekarang"))
    moment = datetime.now(timezone.utc)
    job.status, job.claimed_at, job.claimed_by = "claimed", moment, f"browser:{ctx.staff_id}"[:60]
    job.attempts = (job.attempts or 0) + 1
    job.updated_at = moment
    await ctx.session.flush()
    return job_out(job, await ctx.session.get(Order, job.order_id), PrintJobDocumentOut)


@pos_router.post("/{job_id}/browser-result", response_model=PrintJobOut)
async def pos_browser_result(job_id: uuid.UUID, payload: BrowserResultIn, ctx: PosCtx):
    """What the person said after the print dialog: the paper is there (a
    person's confirmation, recorded as theirs), or it is not (failed)."""
    if payload.ok:
        return await _act(ctx, job_id, printing.confirm_printed, staff_id=ctx.staff_id)
    job = (await ctx.session.execute(select(PrintJob).where(PrintJob.id == job_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Slip cetak tidak ditemukan")
    if job.status == "claimed" and not (job.claimed_by or "").startswith("browser:"):
        # A printer device holds it; only that device can say it failed.
        raise HTTPException(status_code=409, detail=_STATE_ERRORS["claimed"])
    # Taken through this tablet's browser: whoever is at the till answers for it.
    return await _act(ctx, job_id, printing.report_result, ok=False, device=job.claimed_by or f"browser:{ctx.staff_id}",
                      attempt=job.attempts, error=payload.error or "tidak keluar lewat cetak browser")


# ── Printer devices as the till sees them ────────────────────────────────────

printers_pos_router = APIRouter(prefix="/pos/printers", tags=["pos"])


async def _devices(session) -> list[PrintDeviceOut]:
    now = datetime.now(timezone.utc)
    return [
        PrintDeviceOut(printer=d.printer, device=d.device, state=d.state, detail=d.detail, version=d.version,
                       last_seen_at=d.last_seen_at, silent=now - d.last_seen_at > printing.DEVICE_SILENT_AFTER,
                       test_state=printing.test_state(d, now), test_detail=d.test_detail,
                       test_at=d.test_result_at or d.test_requested_at)
        for d in await printing.devices(session)
    ]


@printers_pos_router.get("", response_model=list[PrintDeviceOut])
async def pos_printers(ctx: PosCtx):
    """Each bridge worker's last report on its printer, and whether it is still reporting."""
    return await _devices(ctx.session)


# ── The owner ────────────────────────────────────────────────────────────────


@owner_router.post("/{printer}/token", response_model=PrinterTokenOut)
async def owner_printer_token(printer: Literal["front", "kitchen"], ctx: OwnerCtx):
    """A long-lived token for one printer device. It can pull that printer's
    jobs and report on them, and nothing else."""
    business = await ctx.session.get(Business, ctx.business_id)
    token = create_token(business_id=str(ctx.business_id), scope="printer", ttl_minutes=PRINTER_TOKEN_DAYS * 24 * 60,
                         generation=business.pairing_generation, extra={"printer": printer})
    claims = decode_token(token)
    return PrinterTokenOut(
        printer=printer, token=token, claim_path="/print/agent/claim", result_path="/print/agent/jobs/{job_id}/result",
        expires_at=datetime.fromtimestamp(claims["exp"], tz=timezone.utc),
    )


@owner_router.get("/devices", response_model=list[PrintDeviceOut])
async def owner_printer_devices(ctx: OwnerCtx):
    """For setting up a bridge: has it reported, and what does it see?"""
    return await _devices(ctx.session)


@owner_router.post("/{printer}/test", response_model=list[PrintDeviceOut])
async def owner_printer_test(printer: Literal["front", "kitchen"], ctx: OwnerCtx):
    """Ask this printer for a test ticket. The bridge prints it on its next
    poll (a second or two) and reports what happened. Refused when no bridge is
    reporting for that printer: there would be nobody to print it."""
    asked = await printing.request_test(ctx.session, business_id=ctx.business_id, printer=printer)
    if not asked:
        raise HTTPException(status_code=409, detail="Belum ada print bridge yang melapor untuk printer ini — "
                                                    "jalankan bridge di tablet dulu")
    return await _devices(ctx.session)


@owner_router.get("/routing", response_model=PrintRoutingOut)
async def owner_print_routing(ctx: OwnerCtx):
    """Which products go to which station, and which have never been told.
    An unmapped product still prints — on the Bar slip, flagged TUJUAN BELUM
    DIATUR — so nothing is silently dropped, but it should be assigned."""
    from app.models import Item

    # Only what a customer can order: ingredients (sell_price 0) never reach a
    # preparation slip, and the till hides them too.
    rows = (await ctx.session.execute(
        select(Item.id, Item.name, Item.prep_station).where(Item.sell_price > 0).order_by(Item.name)
    )).all()
    counts = {"bar": 0, "kitchen": 0, "none": 0}
    unmapped = []
    for item_id, name, station in rows:
        if station in counts:
            counts[station] += 1
        else:
            unmapped.append(PrintRoutingOut.Unmapped(id=item_id, name=name))
    return PrintRoutingOut(**counts, unmapped=unmapped)
