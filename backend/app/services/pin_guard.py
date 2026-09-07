"""PIN brute-force protection (roadmap M15-T12).

A 4-digit PIN is 10,000 combinations. The threat is not a stranger on the
internet — it is the person holding the tablet all shift, who wants to void
their own sales and keep the cash, and who can script the pad and have the
owner's PIN inside an hour. The pairing link is a bearer URL too, so anyone who
has ever seen it can replay `/pos/login` from their own phone.

**The design is shaped by one constraint: a café cannot have its till locked
during a rush.** A guard that protects the money by stopping trade is a denial
of service the owner switches off within a week, and then nothing is protected.
So this is an escalating *cooldown*, not a lock:

  * failures are counted per subject in a rolling window (`WINDOW`), and a gap
    longer than the window starts the count again from zero;
  * the first few failures cost nothing at all — a busy till that fat-fingers a
    PIN twice is never slowed down;
  * a correct PIN deletes the counter immediately;
  * the owner can clear any cooldown from the dashboard and can see the attempts;
  * every failure is written to `request_logs`, so a pattern is visible after
    the fact even when no cooldown was ever reached.

Three subjects, counted separately because they are three different questions:

  `pos_login`   one staff member's own PIN. The roadmap's thresholds: 5 → 30s,
                10 → 2 min, 20 → 15 min.
  `pos_device`  a whole device generation (M15-T8), which catches somebody
                spraying five guesses each across ten staff and never tripping
                any single person's counter. Deliberately far more forgiving,
                because three cashiers mistyping in one rush must not slow the
                till down.
  `manager_pin` one cashier's attempts at *somebody else's* manager PIN. Same
                thresholds as `pos_login`, counted apart, because this is the
                gate on voids, refunds and discounts — the ways money leaves.

Nothing here raises by itself. Callers ask `check` before verifying and call
`record_failure` or `clear` after, so the HTTP layer owns the status code and
the Indonesian wording.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PinAttempt, RequestLog

SCOPES = ("pos_login", "pos_device", "manager_pin")

# A gap longer than this and the count starts again: yesterday's fumbling must
# not add to today's.
WINDOW = timedelta(minutes=15)

# (failures at or above, cooldown). Ordered hardest first; the first match wins.
LADDER: dict[str, tuple[tuple[int, timedelta], ...]] = {
    "pos_login": ((20, timedelta(minutes=15)), (10, timedelta(minutes=2)), (5, timedelta(seconds=30))),
    "manager_pin": ((20, timedelta(minutes=15)), (10, timedelta(minutes=2)), (5, timedelta(seconds=30))),
    # Four times the per-person numbers: one shared tablet carries every
    # cashier's mistakes, and a rush must not trip it.
    "pos_device": ((80, timedelta(minutes=15)), (40, timedelta(minutes=2)), (20, timedelta(seconds=30))),
}


@dataclass(frozen=True)
class Cooldown:
    """In force now. `seconds` is what the message tells the cashier to wait."""

    scope: str
    subject: str
    until: datetime
    failures: int

    @property
    def seconds(self) -> int:
        return max(1, int((self.until - datetime.now(timezone.utc)).total_seconds() + 0.999))


def staff_subject(staff_id: uuid.UUID | str) -> str:
    return f"staff:{staff_id}"


def device_subject(generation: int) -> str:
    """Keyed by the pairing generation, not the token: re-pairing (M15-T8)
    retires the links *and* forgives whatever the old device had accumulated,
    which is the same "everything before now is void" the owner just asked for."""
    return f"device:gen{int(generation)}"


def _cooldown_for(scope: str, failures: int) -> timedelta | None:
    for threshold, penalty in LADDER[scope]:
        if failures >= threshold:
            return penalty
    return None


async def _row(session: AsyncSession, business_id: uuid.UUID, scope: str, subject: str) -> PinAttempt | None:
    return (
        await session.execute(
            select(PinAttempt).where(
                PinAttempt.business_id == business_id,
                PinAttempt.scope == scope,
                PinAttempt.subject == subject,
            )
        )
    ).scalar_one_or_none()


async def check(
    session: AsyncSession, business_id: uuid.UUID, scope: str, subject: str, *,
    path: str | None = None, now: datetime | None = None,
) -> Cooldown | None:
    """The cooldown in force for this subject, or None. Ask before verifying.

    A request that arrives *during* a cooldown is itself another attempt at the
    PIN — one we did not bother checking — so passing `path` counts and logs it.
    That is what makes the ladder escalate under a script: without it, hammering
    sits on the mildest penalty forever and the log shows five lines instead of
    the hundred that actually happened. Somebody who reads "wait 30 seconds" and
    waits is never counted."""
    assert scope in SCOPES, scope
    moment = now or datetime.now(timezone.utc)
    row = await _row(session, business_id, scope, subject)
    if row is None or row.locked_until is None or row.locked_until <= moment:
        return None
    if path is not None:
        escalated = await record_failure(business_id, scope, subject, path=path, now=moment)
        if escalated is not None:
            return escalated
    return Cooldown(scope=scope, subject=subject, until=row.locked_until, failures=row.failures)


async def record_failure(
    business_id: uuid.UUID, scope: str, subject: str, *,
    path: str, now: datetime | None = None,
) -> Cooldown | None:
    """Count one wrong PIN and return the cooldown it earned, if any.

    **Takes no session, deliberately.** Every path that counts a failure then
    raises, and raising rolls the caller's transaction back — which would
    discard the count and leave the guard doing nothing at all. `verify_manager_pin`
    is called deep inside the order transaction, so there is no version of this
    that works on the caller's session. It opens its own, commits, and is
    therefore unaffected by whatever the request does next.

    Always writes a `request_logs` row: a pattern of failures matters even when
    no single subject ever reached a threshold, and that is the record the owner
    or an auditor reads after the fact."""
    from app.core.db import tenant_session

    assert scope in SCOPES, scope
    moment = now or datetime.now(timezone.utc)
    async with tenant_session(business_id) as session:
        return await _record(session, business_id, scope, subject, path=path, moment=moment)


async def _record(
    session: AsyncSession, business_id: uuid.UUID, scope: str, subject: str, *,
    path: str, moment: datetime,
) -> Cooldown | None:
    row = await _row(session, business_id, scope, subject)
    if row is None:
        row = PinAttempt(business_id=business_id, scope=scope, subject=subject, failures=0,
                         first_failed_at=moment, last_failed_at=moment)
        session.add(row)
    elif moment - row.last_failed_at > WINDOW:
        # Outside the window: this is a fresh run of bad luck, not the old one.
        row.failures = 0
        row.first_failed_at = moment
        row.locked_until = None

    row.failures += 1
    row.last_failed_at = moment
    penalty = _cooldown_for(scope, row.failures)
    if penalty is not None:
        row.locked_until = moment + penalty

    session.add(RequestLog(
        business_id=business_id, channel="pos" if scope != "manager_pin" else "override",
        path=path, classified_intent=f"{scope}:{subject}", latency_ms=0,
        status="pin_locked" if penalty is not None else "pin_failed", created_at=moment,
    ))
    await session.flush()
    return (
        Cooldown(scope=scope, subject=subject, until=row.locked_until, failures=row.failures)
        if penalty is not None and row.locked_until is not None
        else None
    )


async def clear(session: AsyncSession, business_id: uuid.UUID, scope: str, subject: str) -> None:
    """A correct PIN forgives everything that came before it. Nobody who knows
    the PIN should be paying for somebody else's guessing."""
    row = await _row(session, business_id, scope, subject)
    if row is not None:
        await session.delete(row)
        await session.flush()


async def active(session: AsyncSession, *, now: datetime | None = None) -> list[PinAttempt]:
    """Everything currently being counted, newest first — what the owner sees.
    Rows whose window has expired are not listed: they are history, and the
    history the owner wants is in `request_logs`."""
    moment = now or datetime.now(timezone.utc)
    rows = (
        await session.execute(
            select(PinAttempt).where(PinAttempt.last_failed_at > moment - WINDOW)
            .order_by(PinAttempt.last_failed_at.desc())
        )
    ).scalars().all()
    return list(rows)
