"""Database integration tests — RLS tenant isolation + the concurrent-sale race.

These need a real Postgres with the migration applied, so they run only when
INTEGRATION_DATABASE_URL is set (point it at a disposable database, run
`alembic upgrade head` against it first):

    INTEGRATION_DATABASE_URL=postgresql+asyncpg://... pytest tests/test_db_integration.py

They are skipped otherwise (no Postgres/Docker on the build machine — see
docs/progress.md Phase 0).
"""
import asyncio
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.models import (
    Account, Approval, Business, Item, ItemVariant, JournalEntry, JournalLine, Modifier, ModifierGroup, Order, OrderLine,
    OrderLineModifier, PostingRule,
    GoodsReceipt, GoodsReceiptLine, Payment, PoLine, PurchaseOrder, RecipeLine, Sale, Staff, StockMovement, Supplier,
    Uom, UomConversion,
)
from app.services.sales import InsufficientStock, record_sale

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DB_URL, reason="INTEGRATION_DATABASE_URL not set — no Postgres available"
)


@pytest.fixture
async def engine():
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _set_tenant(session: AsyncSession, business_id) -> None:
    await session.execute(
        text("select set_config('app.current_business_id', :bid, true)"),
        {"bid": str(business_id)},
    )


async def _make_business(session_factory, suffix: str):
    async with session_factory() as session:
        business = Business(
            name=f"RLS Test {suffix}",
            owner_phone=f"62999{uuid.uuid4().hex[:9]}",
        )
        session.add(business)
        await session.commit()
        return business


@pytest.fixture
async def two_tenants(session_factory):
    a = await _make_business(session_factory, "A")
    b = await _make_business(session_factory, "B")
    for biz in (a, b):  # the books every real business has (M6-T1/M6-T3)
        async with session_factory() as session:
            await _set_tenant(session, biz.id)
            await seed_books(session, biz.id)
            await session.commit()
    yield a, b
    async with session_factory() as session:
        for biz in (a, b):
            row = await session.get(Business, biz.id)
            if row:
                await session.delete(row)
        await session.commit()


async def test_rls_blocks_cross_tenant_reads(session_factory, two_tenants):
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(Item(business_id=a.id, name="Rahasia A", unit="kg", current_stock=Decimal(5)))
        await session.commit()

    # Tenant B sees nothing of A's items.
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        rows = (await session.execute(select(Item).where(Item.name == "Rahasia A"))).scalars().all()
        assert rows == []

    # Tenant A sees its own row.
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        rows = (await session.execute(select(Item).where(Item.name == "Rahasia A"))).scalars().all()
        assert len(rows) == 1


async def test_rls_blocks_cross_tenant_writes(session_factory, two_tenants):
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        # WITH CHECK policy: inserting a row claiming to belong to B while the
        # transaction is pinned to A must fail.
        session.add(Item(business_id=b.id, name="Smuggled", unit="kg"))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_no_context_means_no_rows(session_factory, two_tenants):
    a, _ = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(Item(business_id=a.id, name="Ctx Item", unit="pcs", current_stock=Decimal(1)))
        await session.commit()

    async with session_factory() as session:  # no set_config at all
        with pytest.raises(Exception):
            # current_setting('app.current_business_id') errors when unset —
            # queries fail closed, they don't leak.
            (await session.execute(select(Item))).scalars().all()


async def test_rls_isolates_stock_movements(session_factory, two_tenants):
    """M2-T1 / roadmap §2: business A cannot read B's stock_movements rows, and
    cannot write a row claiming to be B's."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        item = Item(business_id=a.id, name="Ledger Item A", unit="kg", current_stock=Decimal(3))
        session.add(item)
        await session.flush()
        session.add(
            StockMovement(
                business_id=a.id, item_id=item.id, qty_delta=Decimal(3),
                reason="purchase", source_type="test", unit_cost=Decimal("12500.00"),
            )
        )
        await session.commit()
        item_id = item.id

    async with session_factory() as session:  # B sees nothing of A's ledger
        await _set_tenant(session, b.id)
        rows = (await session.execute(select(StockMovement).where(StockMovement.item_id == item_id))).scalars().all()
        assert rows == []

    async with session_factory() as session:  # A sees its own row, with exact numbers
        await _set_tenant(session, a.id)
        row = (await session.execute(select(StockMovement).where(StockMovement.item_id == item_id))).scalar_one()
        assert row.qty_delta == Decimal("3.000")
        assert row.unit_cost == Decimal("12500.00")
        assert row.reason == "purchase"

    async with session_factory() as session:  # A cannot smuggle a row into B's ledger
        await _set_tenant(session, a.id)
        session.add(
            StockMovement(business_id=b.id, item_id=item_id, qty_delta=Decimal(1), reason="correction")
        )
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_orders_lines_and_payments(session_factory, two_tenants):
    """M3-T1 / roadmap §2: an order with a line and a split payment belongs to A
    alone — B reads none of the three tables, and A cannot write into B's."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        item = Item(business_id=a.id, name="Order Item A", unit="cup", current_stock=Decimal(9),
                    cost_price=Decimal("8000"), sell_price=Decimal("22000"))
        session.add(item)
        await session.flush()
        order = Order(business_id=a.id, order_type="dine_in", subtotal=Decimal("44000"), total=Decimal("44000"))
        session.add(order)
        await session.flush()
        session.add_all([
            OrderLine(business_id=a.id, order_id=order.id, item_id=item.id, quantity=Decimal(2),
                      unit_price=Decimal("22000"), line_total=Decimal("44000"), unit_cost_at_sale=Decimal("8000")),
            Payment(business_id=a.id, order_id=order.id, method="cash", amount=Decimal("20000")),
            Payment(business_id=a.id, order_id=order.id, method="qris", amount=Decimal("24000"), reference="QR-1"),
        ])
        await session.commit()
        order_id, item_id = order.id, item.id

    async with session_factory() as session:  # B sees nothing
        await _set_tenant(session, b.id)
        for model in (Order, OrderLine, Payment):
            rows = (await session.execute(select(model))).scalars().all()
            assert [r for r in rows if r.business_id == a.id] == []
        assert await session.get(Order, order_id) is None

    async with session_factory() as session:  # A sees it all, with exact numbers
        await _set_tenant(session, a.id)
        order = await session.get(Order, order_id)
        assert order.total == Decimal("44000.00")
        lines = (await session.execute(select(OrderLine).where(OrderLine.order_id == order_id))).scalars().all()
        assert len(lines) == 1 and lines[0].unit_cost_at_sale == Decimal("8000.00")
        paid = (await session.execute(select(Payment).where(Payment.order_id == order_id))).scalars().all()
        assert sorted(p.amount for p in paid) == [Decimal("20000.00"), Decimal("24000.00")]

    for smuggled in (
        lambda oid: Order(business_id=b.id, order_type="takeaway"),
        lambda oid: OrderLine(business_id=b.id, order_id=oid, item_id=item_id, quantity=Decimal(1),
                              unit_price=Decimal(1), line_total=Decimal(1)),
        lambda oid: Payment(business_id=b.id, order_id=oid, method="cash", amount=Decimal(1)),
    ):
        async with session_factory() as session:  # A cannot write B's rows
            await _set_tenant(session, a.id)
            session.add(smuggled(order_id))
            with pytest.raises(Exception):
                await session.commit()


async def test_rls_isolates_item_variants(session_factory, two_tenants):
    """M4-T1 / roadmap §2: B cannot read A's variants; A cannot write B's."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        item = Item(business_id=a.id, name="Variant Item A", unit="cup", sell_price=Decimal("20000"))
        session.add(item)
        await session.flush()
        session.add(ItemVariant(business_id=a.id, item_id=item.id, name="Large", sell_price=Decimal("25000"), is_default=True))
        await session.commit()
        item_id = item.id

    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert (await session.execute(select(ItemVariant).where(ItemVariant.item_id == item_id))).scalars().all() == []

    async with session_factory() as session:
        await _set_tenant(session, a.id)
        row = (await session.execute(select(ItemVariant).where(ItemVariant.item_id == item_id))).scalar_one()
        assert row.sell_price == Decimal("25000.00")
        session.add(ItemVariant(business_id=b.id, item_id=item_id, name="Smuggled", sell_price=Decimal(1)))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_modifier_tables(session_factory, two_tenants):
    """M4-T2 / roadmap §2: modifier_groups, modifiers and order_line_modifiers
    are invisible across tenants and cannot be written as another tenant."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        item = Item(business_id=a.id, name="Mod Item A", unit="cup", current_stock=Decimal(2), sell_price=Decimal("10000"))
        session.add(item)
        await session.flush()
        group = ModifierGroup(business_id=a.id, item_id=item.id, name="Gula")
        session.add(group)
        await session.flush()
        mod = Modifier(business_id=a.id, group_id=group.id, name="Sedikit")
        order = Order(business_id=a.id, subtotal=Decimal("10000"), total=Decimal("10000"))
        session.add_all([mod, order])
        await session.flush()
        line = OrderLine(business_id=a.id, order_id=order.id, item_id=item.id, quantity=Decimal(1),
                         unit_price=Decimal("10000"), line_total=Decimal("10000"))
        session.add(line)
        await session.flush()
        session.add(OrderLineModifier(business_id=a.id, order_line_id=line.id, modifier_id=mod.id, name="Sedikit", price_delta=Decimal(0)))
        await session.commit()
        ids = {"item": item.id, "group": group.id, "mod": mod.id, "line": line.id}

    async with session_factory() as session:  # B sees none of A's rows in any of the three
        await _set_tenant(session, b.id)
        for model in (ModifierGroup, Modifier, OrderLineModifier):
            rows = (await session.execute(select(model))).scalars().all()
            assert all(r.business_id != a.id for r in rows)
        assert await session.get(ModifierGroup, ids["group"]) is None

    for smuggled in (  # A cannot write rows claiming B, in any of the three (WITH CHECK)
        lambda: ModifierGroup(business_id=b.id, item_id=ids["item"], name="X"),
        lambda: Modifier(business_id=b.id, group_id=ids["group"], name="X"),
        lambda: OrderLineModifier(business_id=b.id, order_line_id=ids["line"], name="X", price_delta=Decimal(0)),
    ):
        async with session_factory() as session:
            await _set_tenant(session, a.id)
            session.add(smuggled())
            with pytest.raises(Exception):
                await session.commit()


async def test_rls_isolates_uoms(session_factory, two_tenants):
    """M4-T3 / roadmap §2: units and conversions are per tenant."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        kg = Uom(business_id=a.id, code="kg", name="kilogram")
        g = Uom(business_id=a.id, code="g", name="gram")
        session.add_all([kg, g])
        await session.flush()
        session.add(UomConversion(business_id=a.id, from_uom_id=kg.id, to_uom_id=g.id, factor=Decimal(1000)))
        await session.commit()
        ids = {"kg": kg.id, "g": g.id}

    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert all(u.business_id != a.id for u in (await session.execute(select(Uom))).scalars())
        assert await session.get(UomConversion, ids["kg"]) is None
        assert (await session.execute(select(UomConversion).where(UomConversion.from_uom_id == ids["kg"]))).scalars().all() == []

    for smuggled in (
        lambda: Uom(business_id=b.id, code="lb", name="pound"),
        lambda: UomConversion(business_id=b.id, from_uom_id=ids["g"], to_uom_id=ids["kg"], factor=Decimal("0.001")),
    ):
        async with session_factory() as session:
            await _set_tenant(session, a.id)
            session.add(smuggled())
            with pytest.raises(Exception):
                await session.commit()


async def test_rls_isolates_recipe_lines(session_factory, two_tenants):
    """M4-T4 / roadmap §2."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        latte = Item(business_id=a.id, name="Recipe Latte A", unit="cup", sell_price=Decimal("20000"))
        beans = Item(business_id=a.id, name="Recipe Beans A", unit="kg", current_stock=Decimal(1))
        session.add_all([latte, beans])
        await session.flush()
        variant = ItemVariant(business_id=a.id, item_id=latte.id, name="Standar", is_default=True, sell_price=Decimal("20000"))
        session.add(variant)
        await session.flush()
        session.add(RecipeLine(business_id=a.id, variant_id=variant.id, component_item_id=beans.id, quantity=Decimal("0.018")))
        await session.commit()
        ids = {"variant": variant.id, "beans": beans.id}

    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert (await session.execute(select(RecipeLine).where(RecipeLine.variant_id == ids["variant"]))).scalars().all() == []

    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(RecipeLine(business_id=b.id, variant_id=ids["variant"], component_item_id=ids["beans"], quantity=Decimal(1)))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_approvals(session_factory, two_tenants):
    """M15-T7 / roadmap §2. The override audit trail names who authorised a void
    and what it was worth — a cross-tenant read here would leak another café's
    staff and its takings."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        boss = Staff(business_id=a.id, name="Manajer A", role="manager", pin_hash=hash_pin("4321"))
        session.add(boss)
        await session.flush()
        session.add(Approval(
            business_id=a.id, action="void", approved_by=boss.id, approver_role="manager",
            amount=Decimal("55000.00"), note="salah pesan",
        ))
        await session.commit()
        ids = {"boss": boss.id}

    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert (await session.execute(select(Approval).where(Approval.note == "salah pesan"))).scalars().all() == []

    async with session_factory() as session:
        await _set_tenant(session, a.id)
        row = (await session.execute(select(Approval).where(Approval.note == "salah pesan"))).scalar_one()
        assert row.approver_role == "manager" and row.amount == Decimal("55000.00")
        session.add(Approval(
            business_id=b.id, action="void", approved_by=ids["boss"], approver_role="manager", note="Smuggled",
        ))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_suppliers(session_factory, two_tenants):
    """M5-T1 / roadmap §2."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(Supplier(business_id=a.id, name="Supplier A", phone="0811"))
        await session.commit()
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert (await session.execute(select(Supplier).where(Supplier.name == "Supplier A"))).scalars().all() == []
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        assert (await session.execute(select(Supplier).where(Supplier.name == "Supplier A"))).scalar_one().phone == "0811"
        session.add(Supplier(business_id=b.id, name="Smuggled"))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_purchase_orders(session_factory, two_tenants):
    """M5-T2 / roadmap §2."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        supplier = Supplier(business_id=a.id, name="PO Supplier A")
        item = Item(business_id=a.id, name="PO Item A", unit="kg")
        session.add_all([supplier, item])
        await session.flush()
        po = PurchaseOrder(business_id=a.id, supplier_id=supplier.id, number=1)
        session.add(po)
        await session.flush()
        session.add(PoLine(business_id=a.id, po_id=po.id, item_id=item.id, quantity=Decimal(3), unit_cost=Decimal(1000), line_total=Decimal(3000)))
        await session.commit()
        ids = {"po": po.id, "item": item.id}
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert await session.get(PurchaseOrder, ids["po"]) is None
        assert (await session.execute(select(PoLine).where(PoLine.po_id == ids["po"]))).scalars().all() == []
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(PoLine(business_id=b.id, po_id=ids["po"], item_id=ids["item"], quantity=Decimal(1)))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_goods_receipts(session_factory, two_tenants):
    """M5-T3 / roadmap §2."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        item = Item(business_id=a.id, name="GR Item A", unit="kg")
        session.add(item)
        await session.flush()
        gr = GoodsReceipt(business_id=a.id, number=1)
        session.add(gr)
        await session.flush()
        session.add(GoodsReceiptLine(business_id=a.id, receipt_id=gr.id, item_id=item.id, quantity=Decimal(1), quantity_item_unit=Decimal(1)))
        await session.commit()
        ids = {"gr": gr.id, "item": item.id}
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert await session.get(GoodsReceipt, ids["gr"]) is None
        assert (await session.execute(select(GoodsReceiptLine).where(GoodsReceiptLine.receipt_id == ids["gr"]))).scalars().all() == []
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(GoodsReceiptLine(business_id=b.id, receipt_id=ids["gr"], item_id=ids["item"], quantity=Decimal(1), quantity_item_unit=Decimal(1)))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_accounts(session_factory, two_tenants):
    """M6-T1 / roadmap §2."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(Account(business_id=a.id, code="8100", name="Akun khusus A", type="asset"))
        await session.commit()
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert all(r.business_id != a.id for r in (await session.execute(select(Account))).scalars())
        assert (await session.execute(select(Account).where(Account.code == "8100"))).scalars().all() == []
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(Account(business_id=b.id, code="8200", name="Smuggled", type="asset"))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_journal(session_factory, two_tenants):
    """M6-T2 / roadmap §2."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        kas = (await session.execute(select(Account).where(Account.code == "1100"))).scalar_one()
        sales = (await session.execute(select(Account).where(Account.code == "4100"))).scalar_one()
        entry = JournalEntry(business_id=a.id, entry_no=1)
        session.add(entry)
        await session.flush()
        session.add_all([
            JournalLine(business_id=a.id, entry_id=entry.id, account_id=kas.id, debit=Decimal(100)),
            JournalLine(business_id=a.id, entry_id=entry.id, account_id=sales.id, credit=Decimal(100)),
        ])
        await session.commit()
        ids = {"entry": entry.id, "kas": kas.id}
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert await session.get(JournalEntry, ids["entry"]) is None
        assert (await session.execute(select(JournalLine).where(JournalLine.entry_id == ids["entry"]))).scalars().all() == []
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(JournalLine(business_id=b.id, entry_id=ids["entry"], account_id=ids["kas"], debit=Decimal(1)))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_posting_rules(session_factory, two_tenants):
    """M6-T3 / roadmap §2."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(PostingRule(business_id=a.id, event_type="CustomEvent", component="x", debit_code="5100", credit_code="1300"))
        await session.commit()
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert all(r.business_id != a.id for r in (await session.execute(select(PostingRule))).scalars())
        assert (await session.execute(select(PostingRule).where(PostingRule.event_type == "CustomEvent"))).scalars().all() == []
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(PostingRule(business_id=b.id, event_type="X", component="y", debit_code="1", credit_code="2"))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_sales_view(session_factory, two_tenants):
    """M3-T2: `sales` is a security_invoker view over order_lines ⨝ orders. A
    sale recorded by A is visible to A through the view with the original
    shape, invisible to B, and fails closed with no tenant context."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        staff = Staff(business_id=a.id, name="Viewer", pin_hash=hash_pin("0000"))
        item = Item(business_id=a.id, name="View Item A", unit="pcs", current_stock=Decimal(4),
                    cost_price=Decimal("5000"), sell_price=Decimal("12000"))
        session.add_all([staff, item])
        await session.commit()
        staff_id, item_id = staff.id, item.id

    async with session_factory() as session:
        await _set_tenant(session, a.id)
        recorded = await record_sale(session, business_id=a.id, staff_id=staff_id, item_id=item_id,
                                     quantity=Decimal(3))
        await session.commit()
        line_id = recorded.line.id

    async with session_factory() as session:  # A: old shape, exact numbers, id = line id
        await _set_tenant(session, a.id)
        sale = (await session.execute(select(Sale).where(Sale.item_id == item_id))).scalar_one()
        assert sale.id == line_id
        assert sale.quantity == Decimal("3.000") and sale.unit_price == Decimal("12000.00")
        assert sale.total_price == Decimal("36000.00") and sale.staff_id == staff_id

    async with session_factory() as session:  # B: nothing
        await _set_tenant(session, b.id)
        assert (await session.execute(select(Sale).where(Sale.item_id == item_id))).scalars().all() == []

    async with session_factory() as session:  # no context: fails closed, never leaks
        with pytest.raises(Exception):
            (await session.execute(select(Sale))).scalars().all()

    async with session_factory() as session:  # the view is read-only
        await _set_tenant(session, a.id)
        session.add(Sale(business_id=a.id, item_id=item_id, quantity=Decimal(1), unit_price=Decimal(1),
                         total_price=Decimal(1), staff_id=staff_id))
        with pytest.raises(Exception):
            await session.commit()


async def test_concurrent_sale_of_last_unit(session_factory, two_tenants):
    """Two staff sell the last unit at the same moment: exactly one succeeds,
    stock never goes negative (the brief's atomic UPDATE guarantee)."""
    a, _ = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        staff = Staff(business_id=a.id, name="Racer", pin_hash=hash_pin("0000"))
        item = Item(
            business_id=a.id, name="Last Croissant", unit="pcs",
            current_stock=Decimal(1), sell_price=Decimal(28000),
        )
        session.add_all([staff, item])
        await session.commit()
        staff_id, item_id = staff.id, item.id

    async def try_sell():
        async with session_factory() as session:
            await _set_tenant(session, a.id)
            try:
                await record_sale(
                    session,
                    business_id=a.id,
                    staff_id=staff_id,
                    item_id=item_id,
                    quantity=Decimal(1),
                )
                await session.commit()
                return "sold"
            except InsufficientStock:
                await session.rollback()
                return "rejected"

    results = await asyncio.gather(try_sell(), try_sell())
    assert sorted(results) == ["rejected", "sold"]

    async with session_factory() as session:
        await _set_tenant(session, a.id)
        item = await session.get(Item, item_id)
        assert item.current_stock == Decimal(0)
        sales = (await session.execute(select(Sale).where(Sale.item_id == item_id))).scalars().all()
        assert len(sales) == 1


async def test_rls_isolates_shifts(session_factory, two_tenants):
    """M7-T1 / roadmap §2."""
    from app.core.security import hash_pin
    from app.models import Shift, Staff

    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        cashier = Staff(business_id=a.id, name="Kasir A", pin_hash=hash_pin("1111"))
        session.add(cashier)
        await session.flush()
        shift = Shift(business_id=a.id, staff_id=cashier.id, opening_float=Decimal(50000))
        session.add(shift)
        await session.commit()
        ids = {"shift": shift.id, "cashier": cashier.id}
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert await session.get(Shift, ids["shift"]) is None
        assert (await session.execute(select(Shift))).scalars().all() == []
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(Shift(business_id=b.id, staff_id=ids["cashier"], opening_float=Decimal(1)))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_cash_movements(session_factory, two_tenants):
    """M7-T2 / roadmap §2."""
    from app.models import CashMovement

    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        row = CashMovement(business_id=a.id, kind="bank_drop", via="cash", amount=Decimal(10000), reason="setor")
        session.add(row)
        await session.commit()
        row_id = row.id
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert await session.get(CashMovement, row_id) is None
        assert (await session.execute(select(CashMovement))).scalars().all() == []
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(CashMovement(business_id=b.id, kind="bank_drop", via="cash", amount=Decimal(1), reason="smuggled"))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_customers(session_factory, two_tenants):
    """M8-T1 / roadmap §2: a customer, and the same phone, in two businesses."""
    from app.models import Customer

    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        row = Customer(business_id=a.id, name="Andi", phone="6281200009999")
        session.add(row)
        await session.commit()
        row_id = row.id
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert await session.get(Customer, row_id) is None
        assert (await session.execute(select(Customer))).scalars().all() == []
        # The phone is a key per business, not globally: B may have its own Andi.
        session.add(Customer(business_id=b.id, name="Andi (B)", phone="6281200009999"))
        await session.commit()
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(Customer(business_id=b.id, name="smuggled", phone=None))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_points_and_loyalty_settings(session_factory, two_tenants):
    """M8-T2 / roadmap §2."""
    from app.models import Customer, LoyaltySettings, PointsMovement

    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        cust = Customer(business_id=a.id, name="Andi")
        session.add(cust)
        await session.flush()
        row = PointsMovement(business_id=a.id, customer_id=cust.id, points_delta=5, reason="adjust")
        session.add(row)
        await session.commit()
        row_id, cust_id = row.id, cust.id
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert await session.get(PointsMovement, row_id) is None
        assert (await session.execute(select(PointsMovement))).scalars().all() == []
        assert (await session.execute(select(LoyaltySettings))).scalars().all() == []   # B has no row yet: nothing leaks
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(PointsMovement(business_id=b.id, customer_id=cust_id, points_delta=1, reason="adjust"))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_promos(session_factory, two_tenants):
    """M8-T3 / roadmap §2: promos, their conditions and applications."""
    from decimal import Decimal as _D

    from app.models import Promo, PromoCondition

    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        promo = Promo(business_id=a.id, name="Semua 10%", kind="percent_off", value=_D("0.10"))
        session.add(promo)
        await session.flush()
        session.add(PromoCondition(business_id=a.id, promo_id=promo.id, kind="min_spend", amount=_D(10000)))
        await session.commit()
        promo_id = promo.id
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert await session.get(Promo, promo_id) is None
        assert (await session.execute(select(PromoCondition))).scalars().all() == []
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(Promo(business_id=b.id, name="smuggled", kind="amount_off", value=_D(1)))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_isolates_vouchers(session_factory, two_tenants):
    """M8-T4 / roadmap §2: codes are per business — B may reuse A's code."""
    from decimal import Decimal as _D

    from app.models import Voucher

    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        row = Voucher(business_id=a.id, code="HEMAT5", kind="amount_off", value=_D(5000))
        session.add(row)
        await session.commit()
        row_id = row.id
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        assert await session.get(Voucher, row_id) is None
        session.add(Voucher(business_id=b.id, code="HEMAT5", kind="amount_off", value=_D(1000)))
        await session.commit()
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(Voucher(business_id=b.id, code="SMUGGLED", kind="amount_off", value=_D(1)))
        with pytest.raises(Exception):
            await session.commit()
