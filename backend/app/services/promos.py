"""The promo engine (roadmap M8-T3).

`apply_promos` is a **pure function**: the cart as priced so far, the open
promos, the moment of the sale in the business's own timezone — and it says
what each promo gives: a rupiah amount off a line or the bill, or bonus units
of an item to add to the order. Nothing is activated or expired by a job; a
promo applies exactly when every one of its conditions holds *at that moment*,
so a happy hour stops applying at 17:00:00 without anyone doing anything.

Rewards (`promos.kind`):

  percent_off   `value` (0.10 = 10%) off an item's lines, or off the bill when
                `item_id` is null
  amount_off    `value` rupiah off, per multiple bought (see `multiples`), or
                off the bill when `item_id` is null; never more than the line
  bonus_item    buy `multiples.quantity` of `item_id` (default 1), get
                `bonus_quantity` of `bonus_item_id` (default: the same item —
                a BOGO) at no charge, `max_per_order` times at most

Conditions (`promo_conditions.kind`, ANDed): `date_range` (`starts_at`
inclusive, `ends_at` exclusive, UTC instants), `day_of_week` (business-local,
0 = Monday), `time_window` (business-local, `time_end` exclusive, may cross
midnight), `min_spend` (net of the cashier's discounts, before promos), and
`multiples` (how many of `item_id` earn one application).

A bonus unit is added to the order as a real line at its list price with a
promo discount equal to that price: stock moves, cost of goods posts, revenue
is recognised gross and the give-away is posted to `4250 Diskon promo` — the
campaign's cost is on the books, not hidden in a lower revenue figure.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Promo, PromoApplication, PromoCondition

MONEY = Decimal("0.01")
QTY = Decimal("0.001")
PROMO_KINDS = ("percent_off", "amount_off", "bonus_item")
CONDITION_KINDS = ("date_range", "day_of_week", "time_window", "min_spend", "multiples")


def q(amount: Decimal) -> Decimal:
    return Decimal(amount).quantize(MONEY, rounding=ROUND_HALF_UP)


class PromoInvalid(Exception):
    """`code`: name, kind, value, item, bonus_item, bonus_quantity, max_per_order, condition, not_found."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# ── the pure engine ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PromoRule:
    """A promo with its conditions folded in, detached from the ORM so the
    engine stays pure and a test can state one in a literal."""

    id: uuid.UUID
    name: str
    kind: str
    value: Decimal = Decimal(0)
    item_id: uuid.UUID | None = None
    bonus_item_id: uuid.UUID | None = None
    bonus_quantity: Decimal = Decimal(1)
    max_per_order: int | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    days_of_week: frozenset[int] | None = None
    time_start: time | None = None
    time_end: time | None = None
    min_spend: Decimal = Decimal(0)
    buy_quantity: Decimal = Decimal(1)

    @classmethod
    def from_rows(cls, promo: Promo, conditions: list[PromoCondition]) -> "PromoRule":
        kw: dict = dict(
            id=promo.id, name=promo.name, kind=promo.kind, value=Decimal(promo.value), item_id=promo.item_id,
            bonus_item_id=promo.bonus_item_id, bonus_quantity=Decimal(promo.bonus_quantity),
            max_per_order=promo.max_per_order,
        )
        for c in conditions:
            if c.kind == "date_range":
                kw["starts_at"], kw["ends_at"] = c.starts_at, c.ends_at
            elif c.kind == "day_of_week":
                kw["days_of_week"] = frozenset(int(d) for d in (c.days_of_week or []))
            elif c.kind == "time_window":
                kw["time_start"], kw["time_end"] = c.time_start, c.time_end
            elif c.kind == "min_spend":
                kw["min_spend"] = Decimal(c.amount or 0)
            elif c.kind == "multiples":
                kw["buy_quantity"] = Decimal(c.quantity or 1)
        return cls(**kw)


@dataclass(frozen=True)
class CartLine:
    item_id: uuid.UUID
    unit_price: Decimal          # all-in, after modifiers
    quantity: Decimal
    line_discount: Decimal = Decimal(0)   # the cashier's, already gated


@dataclass(frozen=True)
class BonusLine:
    promo_id: uuid.UUID
    promo_name: str
    item_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal          # list price — the promo discount equals the whole line


@dataclass(frozen=True)
class Application:
    promo_id: uuid.UUID
    promo_name: str
    line_index: int | None       # index into the input lines (None = the bill, or a bonus line — see bonus_index)
    amount: Decimal              # rupiah given away
    bonus_quantity: Decimal = Decimal(0)
    bonus_index: int | None = None   # index into `bonus_lines` when the reward is a bonus line


@dataclass
class PromoResult:
    line_discounts: list[Decimal]            # aligned with the input lines
    bill_discount: Decimal = Decimal("0.00")
    bonus_lines: list[BonusLine] = field(default_factory=list)
    applications: list[Application] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return q(sum(self.line_discounts, Decimal(0)) + self.bill_discount
                 + sum((b.unit_price * b.quantity for b in self.bonus_lines), Decimal(0)))


def is_open(rule: PromoRule, at_utc: datetime, local: datetime) -> bool:
    """Every time-shaped condition holds at this moment. `local` is `at_utc`
    in the business's timezone."""
    if rule.starts_at is not None and at_utc < rule.starts_at:
        return False
    if rule.ends_at is not None and at_utc >= rule.ends_at:
        return False
    if rule.days_of_week is not None and local.weekday() not in rule.days_of_week:
        return False
    if rule.time_start is not None and rule.time_end is not None:
        t = local.time().replace(tzinfo=None)
        if rule.time_start <= rule.time_end:
            if not (rule.time_start <= t < rule.time_end):
                return False
        else:  # crosses midnight: 22:00–02:00
            if not (t >= rule.time_start or t < rule.time_end):
                return False
    return True


def _times(qty: Decimal, buy: Decimal, cap: int | None) -> int:
    if buy <= 0:
        return 0
    n = int((qty / buy).to_integral_value(rounding=ROUND_FLOOR))
    return min(n, cap) if cap is not None else n


def apply_promos(
    lines: list[CartLine],
    rules: list[PromoRule],
    *,
    at_utc: datetime,
    tz: str,
    bill_discount: Decimal = Decimal(0),
    price_of: dict[uuid.UUID, Decimal] | None = None,
) -> PromoResult:
    """Evaluate every open promo against the cart. `price_of` supplies list
    prices for bonus items that are not already in the cart. Item-level
    rewards go first, then bill-level ones on what remains; a line is never
    discounted below zero."""
    local = at_utc.astimezone(ZoneInfo(tz))
    price_of = dict(price_of or {})
    for l in lines:
        price_of.setdefault(l.item_id, l.unit_price)
    net_lines = [q(l.unit_price * l.quantity - l.line_discount) for l in lines]
    net = q(sum(net_lines, Decimal(0)) - Decimal(bill_discount or 0))
    result = PromoResult(line_discounts=[Decimal("0.00") for _ in lines])
    remaining = list(net_lines)

    open_rules = [r for r in rules if is_open(r, at_utc, local) and net >= r.min_spend]
    # Item-level first, in a stable order (name, id) so two tills agree.
    open_rules.sort(key=lambda r: (r.item_id is None, r.name, str(r.id)))

    for rule in open_rules:
        if rule.item_id is not None:
            idxs = [i for i, l in enumerate(lines) if l.item_id == rule.item_id]
            if not idxs:
                continue
            bought = sum((lines[i].quantity for i in idxs), Decimal(0))
            times = _times(bought, rule.buy_quantity, rule.max_per_order)
            if times <= 0:
                continue
            if rule.kind == "bonus_item":
                bonus_item = rule.bonus_item_id or rule.item_id
                unit_price = q(price_of.get(bonus_item, Decimal(0)))
                qty = (rule.bonus_quantity * times).quantize(QTY)
                result.bonus_lines.append(BonusLine(rule.id, rule.name, bonus_item, qty, unit_price))
                result.applications.append(Application(
                    rule.id, rule.name, None, q(unit_price * qty), bonus_quantity=qty,
                    bonus_index=len(result.bonus_lines) - 1,
                ))
            else:
                if rule.kind == "percent_off":
                    want = q(sum((remaining[i] for i in idxs), Decimal(0)) * rule.value)
                else:
                    want = q(rule.value * times)
                # Spread across the item's lines, largest first, never below zero.
                for i in sorted(idxs, key=lambda i: -remaining[i]):
                    if want <= 0:
                        break
                    take = min(want, remaining[i])
                    if take <= 0:
                        continue
                    result.line_discounts[i] = q(result.line_discounts[i] + take)
                    remaining[i] = q(remaining[i] - take)
                    result.applications.append(Application(rule.id, rule.name, i, take))
                    want = q(want - take)
        else:
            base = q(sum(remaining, Decimal(0)) - Decimal(bill_discount or 0) - result.bill_discount)
            if base <= 0:
                continue
            if rule.kind == "percent_off":
                want = q(base * rule.value)
            elif rule.kind == "amount_off":
                want = q(rule.value)
            else:
                continue  # a bill-level bonus has no item to give
            take = min(want, base)
            if take <= 0:
                continue
            result.bill_discount = q(result.bill_discount + take)
            result.applications.append(Application(rule.id, rule.name, None, take))
    return result


# ── loading and owner CRUD ──────────────────────────────────────────────────


async def load_rules(session: AsyncSession, *, include_inactive: bool = False) -> list[PromoRule]:
    stmt = select(Promo).order_by(Promo.name, Promo.id)
    if not include_inactive:
        stmt = stmt.where(Promo.is_active.is_(True))
    promos = (await session.execute(stmt)).scalars().all()
    if not promos:
        return []
    conds = (
        await session.execute(select(PromoCondition).where(PromoCondition.promo_id.in_([p.id for p in promos])))
    ).scalars().all()
    by_promo: dict[uuid.UUID, list[PromoCondition]] = {}
    for c in conds:
        by_promo.setdefault(c.promo_id, []).append(c)
    return [PromoRule.from_rows(p, by_promo.get(p.id, [])) for p in promos]


def _validate_condition(kind: str, spec: dict) -> dict:
    if kind not in CONDITION_KINDS:
        raise PromoInvalid("condition")
    out: dict = {"kind": kind}
    if kind == "date_range":
        out["starts_at"], out["ends_at"] = spec.get("starts_at"), spec.get("ends_at")
        if out["starts_at"] is None and out["ends_at"] is None:
            raise PromoInvalid("condition")
        if out["starts_at"] and out["ends_at"] and out["ends_at"] <= out["starts_at"]:
            raise PromoInvalid("condition")
    elif kind == "day_of_week":
        days = sorted({int(d) for d in (spec.get("days_of_week") or [])})
        if not days or any(d < 0 or d > 6 for d in days):
            raise PromoInvalid("condition")
        out["days_of_week"] = days
    elif kind == "time_window":
        ts, te = spec.get("time_start"), spec.get("time_end")
        if ts is None or te is None or ts == te:
            raise PromoInvalid("condition")
        out["time_start"], out["time_end"] = ts, te
    elif kind == "min_spend":
        amount = Decimal(spec.get("amount") or 0)
        if amount <= 0:
            raise PromoInvalid("condition")
        out["amount"] = q(amount)
    elif kind == "multiples":
        qty = Decimal(spec.get("quantity") or 0)
        if qty <= 0:
            raise PromoInvalid("condition")
        out["quantity"] = qty.quantize(QTY)
    return out


async def create_promo(
    session: AsyncSession, business_id: uuid.UUID, *, name: str, kind: str, value: Decimal = Decimal(0),
    item_id: uuid.UUID | None = None, bonus_item_id: uuid.UUID | None = None, bonus_quantity: Decimal = Decimal(1),
    max_per_order: int | None = None, conditions: list[dict] | None = None, is_active: bool = True,
) -> Promo:
    from app.models import Item

    name = (name or "").strip()
    if not name:
        raise PromoInvalid("name")
    if kind not in PROMO_KINDS:
        raise PromoInvalid("kind")
    value = Decimal(value or 0)
    if value < 0 or (kind == "percent_off" and value > 1) or (kind != "bonus_item" and value <= 0):
        raise PromoInvalid("value")
    if kind == "bonus_item" and item_id is None:
        raise PromoInvalid("item")
    for which, iid in (("item", item_id), ("bonus_item", bonus_item_id)):
        if iid is not None and await session.get(Item, iid) is None:
            raise PromoInvalid(which)
    bonus_quantity = Decimal(bonus_quantity or 1)
    if bonus_quantity <= 0:
        raise PromoInvalid("bonus_quantity")
    if max_per_order is not None and max_per_order <= 0:
        raise PromoInvalid("max_per_order")
    # Validate every condition before anything is written: a promo with a bad
    # rule set must not exist half-made, even for the length of a transaction.
    validated = [_validate_condition(spec.get("kind", ""), spec) for spec in (conditions or [])]
    promo = Promo(
        business_id=business_id, name=name, kind=kind, value=value, item_id=item_id, bonus_item_id=bonus_item_id,
        bonus_quantity=bonus_quantity.quantize(QTY), max_per_order=max_per_order, is_active=is_active,
    )
    session.add(promo)
    await session.flush()
    for fields in validated:
        session.add(PromoCondition(business_id=business_id, promo_id=promo.id, **fields))
    await session.flush()
    return promo


async def update_promo(session: AsyncSession, promo: Promo, *, conditions: list[dict] | None = None, **changes) -> Promo:
    """Edits the reward and, when `conditions` is given, replaces the condition
    set — a promo's conditions are one thing, not a list to patch by index."""
    if changes.get("name") is not None:
        name = changes["name"].strip()
        if not name:
            raise PromoInvalid("name")
        promo.name = name
    if changes.get("is_active") is not None:
        promo.is_active = bool(changes["is_active"])
    if changes.get("value") is not None:
        value = Decimal(changes["value"])
        if value < 0 or (promo.kind == "percent_off" and value > 1):
            raise PromoInvalid("value")
        promo.value = value
    if changes.get("max_per_order") is not None:
        if changes["max_per_order"] <= 0:
            raise PromoInvalid("max_per_order")
        promo.max_per_order = changes["max_per_order"]
    if changes.get("bonus_quantity") is not None:
        if Decimal(changes["bonus_quantity"]) <= 0:
            raise PromoInvalid("bonus_quantity")
        promo.bonus_quantity = Decimal(changes["bonus_quantity"]).quantize(QTY)
    if conditions is not None:
        validated = [_validate_condition(spec.get("kind", ""), spec) for spec in conditions]
        for old in (await session.execute(select(PromoCondition).where(PromoCondition.promo_id == promo.id))).scalars():
            await session.delete(old)   # configuration, not business data: a promo's rule set is replaced whole
        await session.flush()
        for fields in validated:
            session.add(PromoCondition(business_id=promo.business_id, promo_id=promo.id, **fields))
    promo.updated_at = datetime.now(tz=ZoneInfo("UTC"))
    await session.flush()
    return promo


async def promo_view(session: AsyncSession, promo: Promo) -> dict:
    conds = (await session.execute(select(PromoCondition).where(PromoCondition.promo_id == promo.id).order_by(PromoCondition.created_at))).scalars().all()
    return {
        "id": promo.id, "name": promo.name, "kind": promo.kind, "value": promo.value, "item_id": promo.item_id,
        "bonus_item_id": promo.bonus_item_id, "bonus_quantity": promo.bonus_quantity, "max_per_order": promo.max_per_order,
        "is_active": promo.is_active, "created_at": promo.created_at,
        "conditions": [
            {
                "kind": c.kind, "starts_at": c.starts_at, "ends_at": c.ends_at, "days_of_week": c.days_of_week,
                "time_start": c.time_start, "time_end": c.time_end, "amount": c.amount, "quantity": c.quantity,
            }
            for c in conds
        ],
    }


async def applications_of_order(session: AsyncSession, order_id: uuid.UUID) -> list[PromoApplication]:
    return (
        await session.execute(select(PromoApplication).where(PromoApplication.order_id == order_id).order_by(PromoApplication.created_at))
    ).scalars().all()


def local_date(at_utc: datetime, tz: str) -> date:
    return at_utc.astimezone(ZoneInfo(tz)).date()
