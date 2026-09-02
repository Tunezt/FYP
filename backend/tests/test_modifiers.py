"""M4-T2 — modifiers: "extra shot, less sugar" persists on the line, prices
correctly, and prints on the receipt (the receipt payload the kiosk prints).

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dashboard import add_modifier, add_modifier_group, edit_modifier, list_modifier_groups
from app.api.pos import pos_create_order, pos_items, pos_receipt
from app.core.security import hash_pin
from app.models import Business, Item, OrderLine, OrderLineModifier, Staff
from app.schemas.dashboard import ModifierCreateIn, ModifierGroupCreateIn, ModifierUpdateIn
from app.schemas.pos import OrderIn
from app.services.catalog import ModifierInvalid, create_modifier, create_modifier_group, ensure_default_variant
from app.services.orders import ModifierSelectionInvalid, OrderLineSpec, PaymentSpec, create_order, void_order
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


@pytest.fixture
async def engine():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _set_tenant(session, business_id):
    await session.execute(
        text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)}
    )


@pytest.fixture
async def cafe(session_factory):
    """Latte 22.000 with groups: Gula (single, required: Normal/Sedikit/Tanpa, free)
    and Tambahan (multi, optional, max 2: Extra shot +5.000, Susu oat +6.000, Sirup +3.000).
    Plus Teh with its own group so cross-item choices can be rejected."""
    async with session_factory() as s:
        biz = Business(name="Modifier Test", owner_phone=f"62992{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        staff = Staff(business_id=bid, name="Kasir", pin_hash=hash_pin("1111"))
        latte = Item(business_id=bid, name="Latte", unit="cup", current_stock=Decimal(10), cost_price=Decimal(8000), sell_price=Decimal(22000))
        teh = Item(business_id=bid, name="Teh", unit="cup", current_stock=Decimal(10), cost_price=Decimal(2000), sell_price=Decimal(8000))
        s.add_all([owner, staff, latte, teh])
        await s.flush()
        for it in (latte, teh):
            await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
        gula = await create_modifier_group(s, latte, name="Gula", selection="single", is_required=True)
        normal = await create_modifier(s, gula, name="Normal", is_default=True)
        sedikit = await create_modifier(s, gula, name="Sedikit gula")
        tanpa = await create_modifier(s, gula, name="Tanpa gula")
        tambahan = await create_modifier_group(s, latte, name="Tambahan", selection="multi", max_select=2)
        shot = await create_modifier(s, tambahan, name="Extra shot", price_delta=Decimal(5000))
        oat = await create_modifier(s, tambahan, name="Susu oat", price_delta=Decimal(6000))
        sirup = await create_modifier(s, tambahan, name="Sirup", price_delta=Decimal(3000))
        teh_group = await create_modifier_group(s, teh, name="Es", selection="single")
        es = await create_modifier(s, teh_group, name="Pakai es")
        await s.commit()
        ids = {"bid": bid, "staff": staff.id, "latte": latte.id, "teh": teh.id, "gula": gula.id,
               "normal": normal.id, "sedikit": sedikit.id, "tanpa": tanpa.id, "tambahan": tambahan.id,
               "shot": shot.id, "oat": oat.id, "sirup": sirup.id, "es": es.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_extra_shot_less_sugar_persists_and_prices(session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        created = await create_order(
            s, business_id=c["bid"], staff_id=c["staff"],
            lines=[OrderLineSpec(item_id=c["latte"], quantity=Decimal(2), modifier_ids=[c["shot"], c["sedikit"]])],
            payments=[PaymentSpec(method="cash", amount=Decimal(54000))],  # (22.000 + 5.000 + 0) × 2
        )
        await s.commit()
        line_id = created.lines[0].line.id
        assert created.order.total == Decimal("54000.00")

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        line = await s.get(OrderLine, line_id)
        assert line.unit_price == Decimal("27000.00") and line.line_total == Decimal("54000.00")
        assert line.unit_cost_at_sale == Decimal("8000.00")  # modifiers carry no cost yet
        snaps = (await s.execute(select(OrderLineModifier).where(OrderLineModifier.order_line_id == line_id).order_by(OrderLineModifier.name))).scalars().all()
        assert [(m.name, m.price_delta, m.modifier_id) for m in snaps] == [
            ("Extra shot", Decimal("5000.00"), c["shot"]),
            ("Sedikit gula", Decimal("0.00"), c["sedikit"]),
        ]


async def test_snapshot_survives_catalogue_edits(session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        created = await create_order(
            s, business_id=c["bid"], staff_id=c["staff"],
            lines=[OrderLineSpec(item_id=c["latte"], quantity=Decimal(1), modifier_ids=[c["shot"], c["normal"]])],
            payments=[PaymentSpec(method="qris", amount=Decimal(27000))],
        )
        await s.commit()
        line_id = created.lines[0].line.id
    async with session_factory() as s:  # the owner reprices and renames the modifier afterwards
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        await edit_modifier(c["shot"], ModifierUpdateIn(name="Double shot", price_delta=Decimal(8000)), ctx)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        snap = (await s.execute(select(OrderLineModifier).where(OrderLineModifier.order_line_id == line_id, OrderLineModifier.modifier_id == c["shot"]))).scalar_one()
        assert (snap.name, snap.price_delta) == ("Extra shot", Decimal("5000.00"))
        assert (await s.get(OrderLine, line_id)).unit_price == Decimal("27000.00")


@pytest.mark.parametrize("choice,code", [
    ("none", "required"),            # Gula is required
    ("two_sugars", "single"),        # Gula is single-select
    ("three_extras", "max"),         # Tambahan max 2
    ("foreign", "unknown"),          # Teh's modifier on a Latte line
])
async def test_selection_rules(session_factory, cafe, choice, code):
    c = cafe
    picks = {
        "none": [c["shot"]],
        "two_sugars": [c["normal"], c["tanpa"]],
        "three_extras": [c["normal"], c["shot"], c["oat"], c["sirup"]],
        "foreign": [c["normal"], c["es"]],
    }[choice]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(ModifierSelectionInvalid) as exc:
            await create_order(
                s, business_id=c["bid"], staff_id=c["staff"],
                lines=[OrderLineSpec(item_id=c["latte"], quantity=Decimal(1), modifier_ids=picks)],
                payments=[PaymentSpec(method="cash", amount=Decimal(1))],
            )
        assert exc.value.code == code
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Item, c["latte"])).current_stock == Decimal("10.000")  # nothing sold


async def test_receipt_prints_modifiers_and_pos_lists_groups(session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["staff"])
        items = await pos_items(ctx)
        latte = next(i for i in items if i.id == c["latte"])
        assert [g.name for g in latte.modifier_groups] == ["Gula", "Tambahan"]
        gula = latte.modifier_groups[0]
        assert gula.selection == "single" and gula.is_required and gula.min_select == 1 and gula.max_select == 1
        assert [(m.name, m.price_delta, m.is_default) for m in gula.modifiers][0] == ("Normal", Decimal("0.00"), True)

        with pytest.raises(HTTPException) as exc:  # Indonesian 422 for a missing required group
            await pos_create_order(OrderIn.model_validate({
                "lines": [{"item_id": str(c["latte"]), "quantity": "1", "modifier_ids": [str(c["shot"])]}],
                "payments": [{"method": "cash", "amount": "27000"}],
            }), ctx)
        assert exc.value.status_code == 422 and "Gula" in exc.value.detail and "wajib" in exc.value.detail
        await s.rollback()

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["staff"])
        out = await pos_create_order(OrderIn.model_validate({
            "lines": [{"item_id": str(c["latte"]), "quantity": "1", "modifier_ids": [str(c["sedikit"]), str(c["shot"]), str(c["oat"])],
                       "notes": "dibungkus"}],
            "payments": [{"method": "cash", "amount": "33000"}],
        }), ctx)
        await s.commit()
        assert out.total == Decimal("33000.00")
        assert sorted((m.name, m.price_delta) for m in out.lines[0].modifiers) == [
            ("Extra shot", Decimal("5000.00")), ("Sedikit gula", Decimal("0.00")), ("Susu oat", Decimal("6000.00"))]
        order_id = out.id

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["staff"])
        receipt = await pos_receipt(order_id, ctx)
    assert receipt.business_name == "Modifier Test" and receipt.staff_name == "Kasir"
    assert receipt.number == str(order_id)[-8:].upper()
    line = receipt.lines[0]
    assert line.name == "Latte" and line.variant == "Standar" and line.notes == "dibungkus"
    assert line.unit_price == Decimal("33000.00") and line.line_total == Decimal("33000.00")
    assert [(m.name, m.price_delta) for m in line.modifiers] == [
        ("Sedikit gula", Decimal("0.00")), ("Extra shot", Decimal("5000.00")), ("Susu oat", Decimal("6000.00"))]
    assert receipt.total == Decimal("33000.00") and [p.method for p in receipt.payments] == ["cash"]


async def test_void_copies_modifiers_onto_the_reversing_line(session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        created = await create_order(
            s, business_id=c["bid"], staff_id=c["staff"],
            lines=[OrderLineSpec(item_id=c["latte"], quantity=Decimal(1), modifier_ids=[c["tanpa"], c["shot"]])],
            payments=[PaymentSpec(method="cash", amount=Decimal(27000))],
        )
        await s.commit()
        order_id = created.order.id
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await void_order(s, business_id=c["bid"], order_id=order_id, staff_id=c["staff"], manager_pin="1234")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["staff"])
        receipt = await pos_receipt(order_id, ctx)
        assert receipt.status == "voided" and len(receipt.lines) == 2
        assert [l.quantity for l in receipt.lines] == [Decimal("1.000"), Decimal("-1.000")]
        assert [sorted(m.name for m in l.modifiers) for l in receipt.lines] == [["Extra shot", "Tanpa gula"]] * 2
        assert sum(l.line_total for l in receipt.lines) == Decimal(0)


async def test_owner_endpoints_and_group_rules(session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        group = await add_modifier_group(c["latte"], ModifierGroupCreateIn(name="Ukuran es", selection="single", is_required=True), ctx)
        assert (group.min_select, group.max_select, group.is_required) == (1, 1, True)  # normalised for single+required
        mod = await add_modifier(group.id, ModifierCreateIn(name="Banyak es"), ctx)
        assert mod.price_delta == Decimal("0.00")
        with pytest.raises(HTTPException) as exc:
            await add_modifier(group.id, ModifierCreateIn(name="banyak ES"), ctx)
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException) as exc:
            await add_modifier_group(c["latte"], ModifierGroupCreateIn(name="Salah", selection="multi", min_select=3, max_select=2), ctx)
        assert exc.value.status_code == 422
        groups = await list_modifier_groups(c["latte"], ctx)
        assert [g.name for g in groups] == ["Gula", "Tambahan", "Ukuran es"]
        assert [m.name for m in groups[1].modifiers] == ["Extra shot", "Sirup", "Susu oat"]
        await s.commit()


async def test_blank_names_rejected(session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        item = await s.get(Item, c["latte"])
        with pytest.raises(ModifierInvalid):
            await create_modifier_group(s, item, name=" ")
