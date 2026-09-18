"""Print endpoints (prt-4): printer devices pull, people recover.

Three audiences, three scopes:

* **A printer device** (scope `printer`, one token per printer: `front` or
  `kitchen`) pulls work: `POST /print/agent/claim` takes the oldest pending job
  for its printer, `POST /print/agent/jobs/{id}/result` says whether paper came
  out. The café's API runs in the cloud and cannot reach a printer on the café's
  wifi; pulling over HTTPS is what lets a printer or a small bridge beside it
  work behind any router. What sits on the other end is a hardware decision
  recorded in docs/printing.md, not something this module pretends to know.
* **The till** (scope `pos`) sees the queue with honest states and recovers:
  retry a failed job, reprint (marked CETAK ULANG), confirm paper that a person
  is holding, or print a job through the browser as a labelled manual fallback.
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
from pydantic import BaseModel, Field
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
    "receipt": "Struk", "bar_ticket": "Slip bar", "kitchen_ticket": "Slip dapur",
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
    status: str                       # pending · sending · uncertain · printed · failed · cancelled
    order_label: str                  # "Pesanan 042" / "Pesanan 042 · Tambahan 1"
    table_label: str | None = None
    attempts: int
    claimed_by: str | None = None
    error: str | None = None
    confirmed_by_person: bool = False
    created_at: datetime
    claimed_at: datetime | None = None
    printed_at: datetime | None = None
    reprint_of: uuid.UUID | None = None


class PrintJobDocumentOut(PrintJobOut):
    document: dict


class AgentClaimIn(BaseModel):
    device: str = Field(min_length=1, max_length=60)


class AgentResultIn(BaseModel):
    device: str = Field(min_length=1, max_length=60)
    ok: bool
    error: str | None = Field(default=None, max_length=300)


class AgentClaimOut(BaseModel):
    job: PrintJobDocumentOut | None


class BrowserResultIn(BaseModel):
    ok: bool
    error: str | None = Field(default=None, max_length=300)


class PrinterTokenOut(BaseModel):
    printer: Literal["front", "kitchen"]
    token: str
    claim_path: str
    result_path: str
    expires_at: datetime


def job_out(job: PrintJob, order: Order | None, model=PrintJobOut, now: datetime | None = None):
    extra = {"document": job.document} if model is PrintJobDocumentOut else {}
    return model(
        id=job.id, order_id=job.order_id, printer=job.printer, kind=job.kind, kind_label=KIND_LABEL.get(job.kind, job.kind),
        copy_kind=job.copy, status=printing.display_status(job, now), attempts=job.attempts,
        order_label=batch_label(order) if order is not None else "",
        table_label=order.table_label if order is not None and order.order_type == "dine_in" else None,
        claimed_by=job.claimed_by, error=job.error, confirmed_by_person=job.confirmed_by is not None,
        created_at=job.created_at, claimed_at=job.claimed_at, printed_at=job.printed_at, reprint_of=job.reprint_of,
        **extra,
    )


def _state_error(exc: printing.PrintInvalid) -> HTTPException:
    if exc.code == "not_found":
        return HTTPException(status_code=404, detail="Slip cetak tidak ditemukan")
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
async def agent_claim(payload: AgentClaimIn, ctx: PrinterCtx = Depends(printer_ctx)):
    """The oldest pending job for this token's printer, now held by this device.
    `job: null` when there is nothing to print."""
    job = await printing.claim_next(ctx.session, printer=ctx.printer, device=payload.device)
    if job is None:
        return AgentClaimOut(job=None)
    order = await ctx.session.get(Order, job.order_id)
    return AgentClaimOut(job=job_out(job, order, PrintJobDocumentOut))


@agent_router.post("/jobs/{job_id}/result", response_model=PrintJobOut)
async def agent_result(job_id: uuid.UUID, payload: AgentResultIn, ctx: PrinterCtx = Depends(printer_ctx)):
    job = (await ctx.session.execute(select(PrintJob).where(PrintJob.id == job_id))).scalar_one_or_none()
    if job is None or job.printer != ctx.printer:
        raise HTTPException(status_code=404, detail="Slip cetak tidak ditemukan")
    try:
        job = await printing.report_result(ctx.session, job_id=job_id, ok=payload.ok, device=payload.device, error=payload.error)
    except printing.PrintInvalid as exc:
        raise _state_error(exc)
    return job_out(job, await ctx.session.get(Order, job.order_id))


# ── The till ─────────────────────────────────────────────────────────────────


@pos_router.get("", response_model=list[PrintJobOut])
async def pos_print_jobs(
    ctx: PosCtx,
    printer: Literal["front", "kitchen"] | None = Query(default=None),
    scope: Literal["open", "recent"] = Query(default="open"),
):
    """`open`: what still needs a person's attention — waiting, being sent,
    uncertain, failed. `recent`: the last 12 hours of everything, newest first."""
    since = datetime.now(timezone.utc) - timedelta(hours=12)
    stmt = select(PrintJob).where(PrintJob.created_at >= since)
    if printer:
        stmt = stmt.where(PrintJob.printer == printer)
    if scope == "open":
        stmt = stmt.where(PrintJob.status.in_(("pending", "claimed", "failed"))).order_by(PrintJob.created_at, printing.PAPER_ORDER, PrintJob.id)
    else:
        stmt = stmt.order_by(PrintJob.created_at.desc(), PrintJob.id).limit(100)
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
    return await _act(ctx, job_id, printing.retry)


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
    return await _act(ctx, job_id, printing.report_result, ok=False, device=f"browser:{ctx.staff_id}",
                      error=payload.error or "tidak keluar lewat cetak browser")


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
