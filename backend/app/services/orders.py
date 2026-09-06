"""Order creation — the one write path for selling (roadmap M3-T3).

A sale is one order, N lines, M payments, N stock movements, all in the caller's
transaction: if anything fails — an item out of stock on the third line, payments
that do not add up — the caller rolls back and nothing was sold.

Per line the stock guard is the same atomic conditional UPDATE the one-item path
has always used (`current_stock >= qty`), so two kiosks racing for the last unit
still resolve at the database, line by line. `unit_cost_at_sale` snapshots the
item's cost at this moment so historical margin never moves.

Discounts, tax, service charge and rounding (M7-T4b) come from the pure pricing
engine in services/pricing, driven by the business's pricing_settings. A discount
needs the manager PIN when the settings say so; payments must equal the priced,
rounded total exactly; and what the sale reclassifies out of revenue is posted
from the same priced figures, so the order row and the journal cannot disagree.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import func, select

from app.models import (
    Item, ItemVariant, Modifier, ModifierGroup, Order, OrderLine, OrderLineModifier, Payment, RecipeLine,
    StockMovement,
)
from app.services.catalog import default_variant
from app.services.customers import require_customer
from app.services.points import (
    PointsInvalid, award_points_for_order, loyalty_config, redeem_points_for_payment, reverse_points_for_order,
)
from app.services.pricing import LineInput, price_order, pricing_config
from app.services.promos import CartLine, apply_promos, load_rules
from app.services.vouchers import check_voucher, redeem as redeem_voucher, reverse_for_order as reverse_voucher, voucher_by_code
from app.services.sales import ATOMIC_DECREMENT, InsufficientStock, ItemNotFound
from app.services.stock import record_movement
from app.services.units import convert_quantity, to_ledger_precision

TWO_PLACES = Decimal("0.01")


class VariantNotFound(Exception):
    pass


class RecipeUnitMismatch(Exception):
    """A recipe line is in a unit but the component item has no unit set."""

    def __init__(self, component_name: str):
        self.component_name = component_name
        super().__init__(component_name)


async def _active_recipe(session: AsyncSession, variant_id: uuid.UUID) -> list[RecipeLine]:
    return (
        await session.execute(
            select(RecipeLine).where(RecipeLine.variant_id == variant_id, RecipeLine.is_active.is_(True))
            .order_by(RecipeLine.created_at)
        )
    ).scalars().all()


class ModifierSelectionInvalid(Exception):
    """`code`: unknown (inactive or not this item's), required (a required
    group has nothing chosen), single (two choices in a single-select group),
    max (over the group's max_select)."""

    def __init__(self, code: str, group_name: str = ""):
        self.code = code
        self.group_name = group_name
        super().__init__(f"{code}:{group_name}")


async def _resolve_modifiers(session: AsyncSession, item: Item, modifier_ids: list[uuid.UUID]) -> list[Modifier]:
    """Validate a line's modifier choices against the item's active groups and
    return the Modifier rows (deduplicated, in the order given)."""
    groups = (
        await session.execute(
            select(ModifierGroup).where(ModifierGroup.item_id == item.id, ModifierGroup.is_active.is_(True))
        )
    ).scalars().all()
    by_group_id = {g.id: g for g in groups}
    chosen: list[Modifier] = []
    seen: set[uuid.UUID] = set()
    for mid in modifier_ids or []:
        if mid in seen:
            continue
        seen.add(mid)
        m = await session.get(Modifier, mid)
        if m is None or not m.is_active or m.group_id not in by_group_id:
            raise ModifierSelectionInvalid("unknown")
        chosen.append(m)
    counts: dict[uuid.UUID, int] = {}
    for m in chosen:
        counts[m.group_id] = counts.get(m.group_id, 0) + 1
    for g in groups:
        n = counts.get(g.id, 0)
        if g.is_required and n < max(1, g.min_select):
            raise ModifierSelectionInvalid("required", g.name)
        if g.selection == "single" and n > 1:
            raise ModifierSelectionInvalid("single", g.name)
        if g.max_select is not None and n > g.max_select:
            raise ModifierSelectionInvalid("max", g.name)
    return chosen


class PaymentMismatch(Exception):
    def __init__(self, total: Decimal, paid: Decimal):
        self.total = total
        self.paid = paid
        super().__init__(f"payments {paid} do not match order total {total}")


class EmptyOrder(Exception):
    pass


class DiscountNeedsManager(Exception):
    """The settings require a manager PIN before any discount and none was given."""


@dataclass
class OrderLineSpec:
    item_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal | None = None  # None → the variant's (or item's) current sell_price
    notes: str | None = None
    variant_id: uuid.UUID | None = None  # None → the item's default variant (M4-T1)
    modifier_ids: list[uuid.UUID] = field(default_factory=list)  # "extra shot, less sugar" (M4-T2)
    line_discount: Decimal = Decimal(0)  # rupiah off this line (M7-T4b); gated like the bill discount
    promo_id: uuid.UUID | None = None   # set by the engine on a bonus line it added (M8-T3); never from the API


@dataclass
class PaymentSpec:
    method: str
    amount: Decimal
    reference: str | None = None


@dataclass
class CreatedLine:
    line: OrderLine
    item_name: str
    remaining_stock: Decimal
    modifiers: list[OrderLineModifier] = field(default_factory=list)


@dataclass
class CreatedOrder:
    order: Order
    lines: list[CreatedLine] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)


async def create_order(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    staff_id: uuid.UUID | None,
    lines: list[OrderLineSpec],
    payments: list[PaymentSpec],
    order_type: str = "takeaway",
    sold_at: datetime | None = None,
    bill_discount: Decimal = Decimal(0),
    manager_pin: str | None = None,
    customer_id: uuid.UUID | None = None,
    voucher_code: str | None = None,
) -> CreatedOrder:
    if not lines:
        raise EmptyOrder()
    if not payments:
        raise PaymentMismatch(Decimal(0), Decimal(0))
    sold_at = sold_at or datetime.now(timezone.utc)
    shift_id = await open_shift_id(session, staff_id)  # the cashier's open till, if any (M7-T1)
    customer = await require_customer(session, customer_id)  # must be this business's, and active (M8-T1)

    # 0. The discount gate (M7-T4b): when the settings say so, nobody discounts
    #    anything without the manager's PIN — checked before any stock moves.
    config = await pricing_config(session, business_id)
    bill_discount = Decimal(bill_discount or 0)
    if bill_discount > 0 or any(Decimal(l.line_discount or 0) > 0 for l in lines):
        if config.discount_requires_pin:
            if not manager_pin:
                raise DiscountNeedsManager()
            await verify_manager_pin(session, manager_pin)

    # 1. Price every line and take its stock, atomically, before any row exists.
    #    Price and cost come from the variant (explicit, else the item's default);
    #    stock is the parent item's until recipes arrive (M4-T4).
    priced: list[tuple] = []  # (spec, item, variant, modifiers, price, cost, line_total, remaining, consumed)
    # The cart is priced in two passes: the cashier's lines, then — once the
    # promo engine has seen them — any bonus lines a promo adds (M8-T3), which
    # take stock and cost exactly like a paid line.
    queue: list[OrderLineSpec] = list(lines)
    promo_result = None
    while queue:
        spec = queue.pop(0)
        item = await session.get(Item, spec.item_id)
        if item is None:
            raise ItemNotFound()
        if spec.variant_id is not None:
            variant = await session.get(ItemVariant, spec.variant_id)
            if variant is None or variant.item_id != item.id or not variant.is_active:
                raise VariantNotFound()
        else:
            variant = await default_variant(session, item.id)
        modifiers = await _resolve_modifiers(session, item, spec.modifier_ids)
        quantity = Decimal(spec.quantity)
        # Stock: a variant with a recipe is made to order — its components are
        # consumed (converted into each component's unit, guarded per component)
        # and the sold item's own stock is left alone. Otherwise the item itself
        # is decremented, as before. `consumed` = [(component item, qty in its unit)].
        consumed: list[tuple[Item, Decimal]] = []
        recipe = await _active_recipe(session, variant.id) if variant is not None else []
        if recipe:
            for rl in recipe:
                component = await session.get(Item, rl.component_item_id)
                if component is None:
                    raise ItemNotFound()
                needed = Decimal(rl.quantity) * quantity
                if rl.uom_id is not None and rl.uom_id != component.uom_id:
                    if component.uom_id is None:
                        raise RecipeUnitMismatch(component.name)
                    needed = await convert_quantity(session, needed, rl.uom_id, component.uom_id)
                needed = to_ledger_precision(needed)
                if needed <= 0:
                    continue
                left = (await session.execute(ATOMIC_DECREMENT, {"qty": needed, "item_id": component.id})).scalar_one_or_none()
                if left is None:
                    raise InsufficientStock(component.name, component.current_stock)
                component.current_stock = Decimal(left)
                consumed.append((component, needed))
            remaining = Decimal(item.current_stock)
        else:
            result = await session.execute(ATOMIC_DECREMENT, {"qty": quantity, "item_id": item.id})
            remaining = result.scalar_one_or_none()
            if remaining is None:
                raise InsufficientStock(item.name, item.current_stock)
        list_price = Decimal(variant.sell_price) if variant is not None else Decimal(item.sell_price)
        if consumed:
            # Made to order: cost of goods is what the recipe consumed at the
            # components' current moving-average costs (M4-T5), per unit sold.
            cost = (
                sum((Decimal(comp.cost_price) * needed for comp, needed in consumed), Decimal(0)) / quantity
            ).quantize(TWO_PLACES)
        else:
            cost = Decimal(variant.cost_price) if variant is not None else Decimal(item.cost_price)
        base = Decimal(spec.unit_price) if spec.unit_price is not None else list_price
        # The line's unit price is all-in: base (variant) plus every chosen modifier.
        price = (base + sum((Decimal(m.price_delta) for m in modifiers), Decimal(0))).quantize(TWO_PLACES)
        line_total = (price * quantity).quantize(TWO_PLACES)
        priced.append((spec, item, variant, modifiers, price, cost, line_total, Decimal(remaining), consumed))
        if not queue and promo_result is None:
            promo_result = await _evaluate_promos(session, business_id, priced, sold_at, bill_discount)
            queue.extend(
                OrderLineSpec(item_id=b.item_id, quantity=b.quantity, unit_price=b.unit_price, promo_id=b.promo_id,
                              notes=f"promo: {b.promo_name}")
                for b in promo_result.bonus_lines
            )

    # 2. Price the bill (M7-T4b): discounts, tax, service charge, rounding —
    #    one pure function, the same one the kiosk quotes from. PricingInvalid
    #    (a discount bigger than its line or the bill) propagates: nothing has
    #    been written yet and the stock decrements roll back with the caller.
    n_cart = len(lines)
    # Promo discount per priced line: the engine's figure for the cashier's lines,
    # the whole line for a bonus line (it is free).
    promo_per_line = list(promo_result.line_discounts) + [priced[i][6] for i in range(n_cart, len(priced))]
    line_inputs = [
        LineInput(unit_price=p[4], quantity=Decimal(p[0].quantity), line_discount=Decimal(p[0].line_discount or 0),
                  promo_discount=promo_per_line[i])
        for i, p in enumerate(priced)
    ]
    # A voucher (M8-T4) comes off what is left after discounts and promos. Its
    # validity is checked here; the use itself is taken after the order row
    # exists, with the atomic guard — a refused use rolls the whole sale back.
    voucher_quote = None
    if voucher_code:
        before_voucher = price_order(line_inputs, config, bill_discount=bill_discount, promo_bill_discount=promo_result.bill_discount)
        voucher_quote = check_voucher(await voucher_by_code(session, voucher_code), base=before_voucher.net, at=sold_at)
    bill = price_order(
        line_inputs, config, bill_discount=bill_discount, promo_bill_discount=promo_result.bill_discount,
        voucher_discount=voucher_quote.amount if voucher_quote is not None else Decimal(0),
    )
    subtotal, total = bill.subtotal, bill.total

    # Payments must cover the total exactly — a till does not close on a guess.
    paid = sum((Decimal(p.amount) for p in payments), Decimal(0)).quantize(TWO_PLACES)
    if paid != total:
        raise PaymentMismatch(total, paid)

    # 3. Write the order, its lines, payments and ledger rows.
    order = Order(
        shift_id=shift_id,
        business_id=business_id,
        staff_id=staff_id,
        order_type=order_type,
        status="completed",
        customer_id=customer.id if customer is not None else None,
        subtotal=subtotal,
        discount_total=bill.discount_total,
        promo_total=bill.promo_total,
        voucher_total=bill.voucher_total,
        tax_total=bill.tax_total,
        service_charge=bill.service_charge,
        rounding=bill.rounding,
        total=total,
        sold_at=sold_at,
    )
    session.add(order)
    await session.flush()

    created = CreatedOrder(order=order)
    for (spec, item, variant, modifiers, price, cost, _gross, remaining, consumed), pl in zip(priced, bill.lines):
        line = OrderLine(
            business_id=business_id,
            order_id=order.id,
            item_id=item.id,
            variant_id=variant.id if variant is not None else None,
            quantity=Decimal(spec.quantity),
            unit_price=price,
            line_discount=pl.line_discount,
            line_total=pl.line_total,
            unit_cost_at_sale=cost,
            notes=spec.notes,
        )
        session.add(line)
        await session.flush()
        if consumed:
            # Recipe-expanded: one ledger row per component, at the component's cost.
            for component, needed in consumed:
                await record_movement(
                    session, business_id=business_id, item_id=component.id, qty_delta=-needed,
                    reason="sale", source_type="sale", source_id=line.id,
                    unit_cost=component.cost_price, staff_id=staff_id, created_at=sold_at,
                )
        else:
            await record_movement(
                session,
                business_id=business_id,
                item_id=item.id,
                qty_delta=-Decimal(spec.quantity),
                reason="sale",
                source_type="sale",
                source_id=line.id,
                unit_cost=cost,
                staff_id=staff_id,
                created_at=sold_at,
            )
        # Snapshot the chosen modifiers: the receipt and old margins must not
        # change when the catalogue is edited later.
        snapshots = [
            OrderLineModifier(
                business_id=business_id, order_line_id=line.id, modifier_id=m.id,
                name=m.name, price_delta=Decimal(m.price_delta),
            )
            for m in modifiers
        ]
        session.add_all(snapshots)
        created.lines.append(CreatedLine(line=line, item_name=item.name, remaining_stock=remaining, modifiers=snapshots))

    if voucher_quote is not None:
        await redeem_voucher(
            session, business_id, voucher_quote.voucher, order_id=order.id, amount=voucher_quote.amount, at=sold_at,
            customer_id=customer.id if customer is not None else None,
        )

    # What each promo gave, by line (M8-T3): the receipt, the reversal and the
    # campaign report read these rows.
    from app.models import PromoApplication

    for app_ in promo_result.applications:
        line_row = None
        if app_.bonus_index is not None:
            line_row = created.lines[n_cart + app_.bonus_index].line
        elif app_.line_index is not None:
            line_row = created.lines[app_.line_index].line
        session.add(PromoApplication(
            business_id=business_id, order_id=order.id, promo_id=app_.promo_id,
            order_line_id=line_row.id if line_row is not None else None, amount=app_.amount,
            bonus_quantity=app_.bonus_quantity,
        ))
    await session.flush()

    for p in payments:
        payment = Payment(
            business_id=business_id,
            order_id=order.id,
            shift_id=shift_id,
            method=p.method,
            amount=Decimal(p.amount).quantize(TWO_PLACES),
            reference=p.reference,
        )
        session.add(payment)
        created.payments.append(payment)
    await session.flush()

    # 3b. Points (M8-T2). Paying with points spends them now, guarded — if the
    #     balance does not cover it the whole sale rolls back, stock included.
    loyalty = await loyalty_config(session, business_id)
    paid_in_points = sum((Decimal(p.amount) for p in payments if p.method == "points"), Decimal(0)).quantize(TWO_PLACES)
    if paid_in_points > 0:
        await redeem_points_for_payment(
            session, business_id, customer, order.id, rupiah=paid_in_points, staff_id=staff_id,
            created_at=sold_at, config=loyalty,
        )

    # 4. The books, in this same transaction (M6-T4): money in per method,
    #    reclassifications, and cost of goods. If this fails, the sale fails.
    from app.services.posting import post_event

    components: dict[str, Decimal] = {}
    for p in payments:
        key = f"payment:{p.method}"
        components[key] = components.get(key, Decimal(0)) + Decimal(p.amount)
    components.update(bill.fiscal_components())   # discount, tax, service charge ex-tax, rounding up/down
    components["cogs"] = sum(
        ((Decimal(cl.line.unit_cost_at_sale or 0) * Decimal(cl.line.quantity)) for cl in created.lines), Decimal(0)
    ).quantize(TWO_PLACES)
    await post_event(
        session, business_id, "OrderCompleted", components, source_type="order", source_id=order.id,
        memo=f"penjualan #{str(order.id)[-8:].upper()}", posted_at=sold_at, created_by=staff_id,
    )
    # Earn on what was paid with money, never on the part paid with points.
    if customer is not None and loyalty.is_active:
        await award_points_for_order(
            session, business_id, customer, order.id, eligible_amount=total - paid_in_points,
            staff_id=staff_id, created_at=sold_at, config=loyalty,
        )
    return created


# ── Reversals (roadmap M3-T4) ───────────────────────────────────────────────
#
# A void (wrong order, never handed over) and a refund (customer brought it
# back) both write REVERSING rows: a negative-quantity line per original line,
# a negative payment per original payment, and a positive stock movement per
# line. The original rows are never touched; the order's `status` is the only
# field that changes, and it is a state transition the enum exists for. In the
# `sales` view the reversal nets the original to zero, which is the accounting
# effect readers should see.

from sqlalchemy import select, text as sql_text  # noqa: E402

from app.core.security import verify_pin  # noqa: E402
from app.models import Staff  # noqa: E402
from app.services.shifts import open_shift_id


class OrderNotFound(Exception):
    pass


class OrderNotReversible(Exception):
    def __init__(self, status: str):
        self.status = status
        super().__init__(f"order is {status}")


class ManagerPinRejected(Exception):
    pass


RESTOCK = sql_text(
    """
    update items
    set current_stock = current_stock + :qty, updated_at = now()
    where id = :item_id
    returning current_stock
    """
)


async def verify_manager_pin(session: AsyncSession, pin: str) -> Staff:
    """The owner's PIN is the manager PIN (the schema has owner/staff roles
    only). Any active owner-role staff of the pinned business may authorise."""
    owners = (
        await session.execute(select(Staff).where(Staff.role == "owner", Staff.is_active.is_(True)))
    ).scalars().all()
    for owner in owners:
        if verify_pin(pin, owner.pin_hash):
            return owner
    raise ManagerPinRejected()


@dataclass
class Reversal:
    order: Order
    reversing_lines: list[OrderLine]
    reversing_payments: list[Payment]
    restocked: dict[uuid.UUID, Decimal]  # item_id -> stock after restock


async def _reverse(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    order_id: uuid.UUID,
    staff_id: uuid.UUID | None,
    manager: Staff,
    kind: str,            # "void" | "refund"
    restock: bool,
    note: str | None,
) -> Reversal:
    order = await session.get(Order, order_id)
    if order is None or order.business_id != business_id:
        raise OrderNotFound()
    if order.status != "completed":
        raise OrderNotReversible(order.status)

    originals = (
        await session.execute(
            select(OrderLine).where(OrderLine.order_id == order_id, OrderLine.quantity > 0).order_by(OrderLine.created_at)
        )
    ).scalars().all()
    payments = (await session.execute(select(Payment).where(Payment.order_id == order_id))).scalars().all()

    now = datetime.now(timezone.utc)
    reason = "sale_void" if kind == "void" else "refund"
    tag = f"{kind} oleh {manager.name}" + (f": {note}" if note else "")
    reversal = Reversal(order=order, reversing_lines=[], reversing_payments=[], restocked={})

    for line in originals:
        reversing = OrderLine(
            business_id=business_id,
            order_id=order_id,
            item_id=line.item_id,
            variant_id=line.variant_id,
            quantity=-line.quantity,
            unit_price=line.unit_price,
            line_discount=-line.line_discount,
            line_total=-line.line_total,
            unit_cost_at_sale=line.unit_cost_at_sale,
            notes=tag,
            created_at=now,
        )
        session.add(reversing)
        await session.flush()
        reversal.reversing_lines.append(reversing)
        # The reversing line itemises the same modifiers (the receipt of a void
        # shows what was undone).
        for snap in (await session.execute(select(OrderLineModifier).where(OrderLineModifier.order_line_id == line.id))).scalars():
            session.add(OrderLineModifier(
                business_id=business_id, order_line_id=reversing.id, modifier_id=snap.modifier_id,
                name=snap.name, price_delta=snap.price_delta,
            ))
        if restock:
            # Put back exactly what the sale took: the item itself, or — for a
            # made-to-order variant — every component the recipe consumed.
            taken = (
                await session.execute(
                    select(StockMovement).where(
                        StockMovement.source_type == "sale", StockMovement.source_id == line.id,
                        StockMovement.reason == "sale",
                    )
                )
            ).scalars().all()
            for m in taken:
                back = -Decimal(m.qty_delta)
                after = (await session.execute(RESTOCK, {"qty": back, "item_id": m.item_id})).scalar_one()
                reversal.restocked[m.item_id] = Decimal(after)
                await record_movement(
                    session,
                    business_id=business_id,
                    item_id=m.item_id,
                    qty_delta=back,
                    reason=reason,
                    source_type="sale",
                    source_id=reversing.id,
                    unit_cost=m.unit_cost,
                    staff_id=staff_id,
                    created_at=now,
                )

    shift_id = await open_shift_id(session, staff_id)  # the refund leaves the acting cashier's till (M7-T1)
    for p in payments:
        if p.amount <= 0:
            continue
        reversing_payment = Payment(
            business_id=business_id,
            order_id=order_id,
            shift_id=shift_id,
            method=p.method,
            amount=-p.amount,
            reference=f"{kind}:{p.id}",
            created_at=now,
        )
        session.add(reversing_payment)
        reversal.reversing_payments.append(reversing_payment)

    order.status = "voided" if kind == "void" else "refunded"
    await session.flush()
    # Points (M8-T2): what this order earned is taken back, what it spent returns.
    await reverse_points_for_order(session, business_id, order_id, staff_id=staff_id, created_at=now, memo=tag)
    # Vouchers (M8-T4): the use goes back to the code.
    await reverse_voucher(session, business_id, order_id)

    # The books (M6-T4): a void flips the sale's entry; a refund posts returns
    # per method (and inventory back when restocked). Same transaction.
    from app.services.posting import post_event, reverse_event

    if kind == "void":
        await reverse_event(
            session, business_id, "OrderVoided", source_type="order", source_id=order_id,
            original_event_type="OrderCompleted", memo=tag, posted_at=now, created_by=staff_id,
        )
    else:
        components: dict[str, Decimal] = {}
        for p in reversal.reversing_payments:
            key = f"refund:{p.method}"
            components[key] = components.get(key, Decimal(0)) + (-Decimal(p.amount))
        if restock:
            components["cogs_reversal"] = sum(
                ((Decimal(l.unit_cost_at_sale or 0) * Decimal(-l.quantity)) for l in reversal.reversing_lines), Decimal(0)
            ).quantize(TWO_PLACES)
        # Undo what the sale reclassified (M7-T4b) — read back from the sale's
        # own journal lines, never re-derived from today's settings, so a refund
        # after a rate change still undoes exactly what was posted.
        for component, amount in (await _fiscal_lines_of_sale(session, order_id)).items():
            components[f"{component}_reversal"] = amount
        await post_event(
            session, business_id, "OrderRefunded", components, source_type="order", source_id=order_id,
            memo=tag, posted_at=now, created_by=staff_id,
        )
    return reversal


FISCAL_COMPONENTS = ("discount", "promo", "voucher", "tax", "service_charge", "rounding_up", "rounding_down")


async def _evaluate_promos(session: AsyncSession, business_id: uuid.UUID, priced: list[tuple], sold_at: datetime, bill_discount: Decimal):
    """Run the pure promo engine over the priced cart (M8-T3). List prices for
    bonus items not in the cart are looked up here so the engine stays pure."""
    from app.models import Business

    rules = await load_rules(session)
    cart = [CartLine(item_id=p[1].id, unit_price=p[4], quantity=Decimal(p[0].quantity), line_discount=Decimal(p[0].line_discount or 0)) for p in priced]
    price_of: dict[uuid.UUID, Decimal] = {}
    for r in rules:
        for iid in (r.bonus_item_id, r.item_id):
            if iid is not None and iid not in price_of:
                item = await session.get(Item, iid)
                if item is not None:
                    variant = await default_variant(session, item.id)
                    price_of[iid] = Decimal(variant.sell_price) if variant is not None else Decimal(item.sell_price)
    business = await session.get(Business, business_id)
    tz = business.timezone if business is not None else "Asia/Jakarta"
    return apply_promos(cart, rules, at_utc=sold_at, tz=tz, bill_discount=Decimal(bill_discount or 0), price_of=price_of)


async def _fiscal_lines_of_sale(session: AsyncSession, order_id: uuid.UUID) -> dict[str, Decimal]:
    """component -> amount the sale's OrderCompleted entry posted for it (the
    debit side; every component is one balanced Dr/Cr pair)."""
    from app.models import JournalEntry, JournalLine

    entry = (
        await session.execute(
            select(JournalEntry).where(
                JournalEntry.source_type == "order", JournalEntry.source_id == order_id,
                JournalEntry.event_type == "OrderCompleted",
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        return {}
    rows = (
        await session.execute(
            select(JournalLine.memo, func.sum(JournalLine.debit))
            .where(JournalLine.entry_id == entry.id, JournalLine.memo.in_(FISCAL_COMPONENTS))
            .group_by(JournalLine.memo)
        )
    ).all()
    return {memo: Decimal(amount).quantize(TWO_PLACES) for memo, amount in rows if Decimal(amount) > 0}


async def load_receipt(session: AsyncSession, *, business_id: uuid.UUID, order_id: uuid.UUID) -> dict:
    """Everything a printed receipt needs, in one shape (M4-T2)."""
    order = await session.get(Order, order_id)
    if order is None or order.business_id != business_id:
        raise OrderNotFound()
    staff_name = None
    if order.staff_id is not None:
        staff = await session.get(Staff, order.staff_id)
        staff_name = staff.name if staff else None
    customer_name = None
    if order.customer_id is not None:
        from app.models import Customer
        customer = await session.get(Customer, order.customer_id)
        customer_name = customer.name if customer else None
    lines = (
        await session.execute(
            select(OrderLine).where(OrderLine.order_id == order_id).order_by(OrderLine.created_at, OrderLine.id)
        )
    ).scalars().all()
    line_ids = [l.id for l in lines]
    mods_by_line: dict[uuid.UUID, list[OrderLineModifier]] = {}
    if line_ids:
        for snap in (await session.execute(
            select(OrderLineModifier).where(OrderLineModifier.order_line_id.in_(line_ids)).order_by(OrderLineModifier.created_at)
        )).scalars():
            mods_by_line.setdefault(snap.order_line_id, []).append(snap)
    item_names = {}
    variant_names = {}
    for l in lines:
        if l.item_id not in item_names:
            item = await session.get(Item, l.item_id)
            item_names[l.item_id] = item.name if item else "?"
        if l.variant_id and l.variant_id not in variant_names:
            v = await session.get(ItemVariant, l.variant_id)
            variant_names[l.variant_id] = v.name if v else None
    payments = (await session.execute(select(Payment).where(Payment.order_id == order_id).order_by(Payment.created_at))).scalars().all()
    return {
        "order": order,
        "staff_name": staff_name,
        "customer_name": customer_name,
        "lines": [
            {
                "line": l,
                "item_name": item_names[l.item_id],
                "variant_name": variant_names.get(l.variant_id) if l.variant_id else None,
                "modifiers": mods_by_line.get(l.id, []),
            }
            for l in lines
        ],
        "payments": payments,
    }


async def void_order(
    session: AsyncSession, *, business_id: uuid.UUID, order_id: uuid.UUID,
    staff_id: uuid.UUID | None, manager_pin: str, note: str | None = None,
) -> Reversal:
    """The order never happened: everything reversed, stock back on the shelf."""
    manager = await verify_manager_pin(session, manager_pin)
    return await _reverse(session, business_id=business_id, order_id=order_id, staff_id=staff_id,
                          manager=manager, kind="void", restock=True, note=note)


async def refund_order(
    session: AsyncSession, *, business_id: uuid.UUID, order_id: uuid.UUID,
    staff_id: uuid.UUID | None, manager_pin: str, restock: bool = True, note: str | None = None,
) -> Reversal:
    """Money back. `restock=False` when the goods are not coming back (eaten,
    spoiled): revenue and payments reverse, stock does not — no movement row."""
    manager = await verify_manager_pin(session, manager_pin)
    return await _reverse(session, business_id=business_id, order_id=order_id, staff_id=staff_id,
                          manager=manager, kind="refund", restock=restock, note=note)
