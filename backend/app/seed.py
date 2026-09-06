"""Seed the demo café: `python -m app.seed`

Creates **Kopi Kenangan Senja** (fictional, Jakarta) with:
- owner + 2 staff (PINs below)
- 11 items: sellable drinks/food with stock in servings, plus raw materials in kg/liter
- 30 days of deterministic pseudo-random sales history (weekend bumps, one
  planted sales spike yesterday so anomaly detection has something to find)
- a few manual expenses so the P&L view isn't empty

Idempotent: re-running deletes and recreates the demo business (cascade).

Demo credentials:
  owner phone  +62 812-000-1111  (OTP arrives via WhatsApp; in dev the code is logged)
  owner PIN    1234   | staff Sari PIN 2345 | staff Budi PIN 3456
"""
import asyncio
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from app.core.db import plain_session, tenant_session
from app.core.security import hash_pin
from app.models import Business, Item, Order, OrderLine, Payment, Shift, Staff
from app.services.catalog import (
    create_modifier, create_modifier_group, create_variant, ensure_default_variant, set_recipe_line,
)
from app.services.accounts import ensure_standard_chart
from app.services.posting_rules import ensure_standard_rules
from app.services.points import award_points_for_order, ensure_loyalty_settings
from app.services.pricing import ensure_pricing_settings
from app.services.customers import create_customer
from app.services.promos import create_promo
from app.services.shifts import post_variance
from app.services.stock import record_movement
from app.services.suppliers import create_supplier
from app.services.units import ensure_standard_uoms

# Suppliers (M5-T1): (name, phone, address)
SUPPLIERS = [
    ("Toko Manis", "0812-3456-7890", "Pasar Anyar Blok C-4"),
    ("Grosir Barokah", "0813-9876-5432", "Jl. Melati No. 12"),
    ("Pak Udin Sayur", "0857-1122-3344", "Pasar pagi"),
]

# Recipes (M4-T4): (item, variant name, [(component item, quantity, uom code)])
# A large latte uses more beans and milk than a regular one.
RECIPES = [
    ("Es Kopi Susu", "Standar", [("Biji Arabica", 18, "g"), ("Susu UHT", 120, "ml"), ("Gula Aren", 15, "g")]),
    ("Es Kopi Susu", "Large", [("Biji Arabica", 24, "g"), ("Susu UHT", 180, "ml"), ("Gula Aren", 20, "g")]),
    ("Americano", "Standar", [("Biji Arabica", 18, "g")]),
    ("Americano", "Large", [("Biji Arabica", 24, "g")]),
]

# Modifier groups (M4-T2): (item, group name, selection, required, [(modifier, price_delta, default)])
MODIFIERS = [
    ("Es Kopi Susu", "Gula", "single", True,
     [("Normal", 0, True), ("Sedikit gula", 0, False), ("Tanpa gula", 0, False)]),
    ("Es Kopi Susu", "Tambahan", "multi", False,
     [("Extra shot", 5000, False), ("Susu oat", 6000, False)]),
    ("Matcha Latte", "Tambahan", "multi", False,
     [("Extra matcha", 4000, False), ("Susu oat", 6000, False)]),
    ("Americano", "Suhu", "single", True,
     [("Panas", 0, True), ("Dingin", 0, False)]),
]

# Sizes for the drinks that have them (M4-T1): (item name, variant name, sell, cost)
VARIANTS = [
    ("Es Kopi Susu", "Large", 27000, 10000),
    ("Matcha Latte", "Large", 33000, 13000),
    ("Americano", "Large", 22000, 7500),
]

OWNER_PHONE = "628120001111"

ITEMS = [
    # (name, unit, stock, cost, sell, reorder_threshold, popularity weight)
    ("Es Kopi Susu", "cup", 48, 8000, 22000, 20, 10),
    ("Kopi Arabica (cup)", "cup", 35, 7000, 20000, 15, 7),
    ("Americano", "cup", 40, 6000, 18000, 15, 5),
    ("Matcha Latte", "cup", 25, 11000, 28000, 10, 4),
    ("Teh Tarik", "cup", 30, 4000, 15000, 10, 4),
    ("Roti Bakar Coklat", "pcs", 18, 9000, 24000, 8, 3),
    ("Croissant", "pcs", 12, 12000, 28000, 6, 3),
    ("Nasi Goreng Senja", "porsi", 15, 15000, 35000, 6, 2),
    # Raw materials — restocked via receipts, queried by kg on WhatsApp
    ("Biji Arabica", "kg", 8, 145000, 0, 3, 0),
    ("Gula Aren", "kg", 5, 38000, 0, 2, 0),
    ("Susu UHT", "liter", 24, 17000, 0, 10, 0),
]

EXPENSES = [
    ("bahan baku", "Belanja biji kopi + gula mingguan", 1450000, 6),
    ("operasional", "Listrik & air bulan ini", 850000, 12),
    ("bahan baku", "Susu UHT 2 dus", 408000, 3),
    ("operasional", "Gas 3kg x4", 88000, 9),
    ("lainnya", "Service mesin espresso", 350000, 18),
]


async def seed() -> None:
    rng = random.Random(42)
    now = datetime.now(timezone.utc)

    async with plain_session() as session:
        existing = (
            await session.execute(select(Business).where(Business.owner_phone == OWNER_PHONE))
        ).scalar_one_or_none()
        if existing:
            await session.delete(existing)  # cascades to all business data
            await session.flush()
        business = Business(
            name="Kopi Kenangan Senja",
            business_type="cafe",
            owner_phone=OWNER_PHONE,
            language_preference="id",
            timezone="Asia/Jakarta",
            onboarding_completed_at=now - timedelta(days=31),
        )
        session.add(business)
        await session.flush()
        business_id = business.id

    async with tenant_session(business_id) as session:
        owner = Staff(
            business_id=business_id, name="Ibu Ratna", role="owner",
            phone=OWNER_PHONE, pin_hash=hash_pin("1234"),
        )
        sari = Staff(business_id=business_id, name="Sari", pin_hash=hash_pin("2345"))
        budi = Staff(business_id=business_id, name="Budi", pin_hash=hash_pin("3456"))
        session.add_all([owner, sari, budi])
        await session.flush()
        staff_ids = [owner.id, sari.id, budi.id]

        # Units of measure (M4-T3): the standard set, and each item linked to its unit.
        uoms = await ensure_standard_uoms(session, business_id)
        await ensure_standard_chart(session, business_id)  # M6-T1
        await ensure_standard_rules(session, business_id)  # M6-T3
        await ensure_pricing_settings(session, business_id)  # M7-T4
        loyalty = await ensure_loyalty_settings(session, business_id)  # M8-T2: on, 1 poin / Rp 1.000, poin = Rp 100
        loyalty.is_active, loyalty.rupiah_per_point, loyalty.point_value, loyalty.min_redeem_points = True, Decimal(1000), Decimal(100), 10
        await session.flush()
        # Customers (M8-T1): two regulars; their history orders below earn points (M8-T2).
        andi = await create_customer(session, business_id, name="Andi Wijaya", phone="0812-3456-7890", address="Jl. Melati 3", notes="suka kopi susu, gula sedikit")
        rina = await create_customer(session, business_id, name="Rina Kartika", phone="0813-2222-3333")
        regulars = [andi, andi, andi, rina]  # Andi comes three times as often
        for sname, sphone, saddress in SUPPLIERS:  # M5-T1
            await create_supplier(session, business_id, name=sname, phone=sphone, address=saddress)

        items = []
        for name, unit, stock, cost, sell, reorder, weight in ITEMS:
            item = Item(
                business_id=business_id, name=name, unit=unit,
                current_stock=Decimal(stock), cost_price=Decimal(cost),
                sell_price=Decimal(sell), reorder_threshold=Decimal(reorder),
                uom_id=uoms[unit].id if unit in uoms else None,
            )
            session.add(item)
            items.append((item, weight))
        await session.flush()

        # Every item gets its default "Standar" variant; a few drinks get a Large.
        defaults = {}
        for item, _w in items:
            defaults[item.id] = (await ensure_default_variant(session, item)).id
        larges = {}
        by_name = {item.name: item for item, _w in items}
        for item_name, vname, sell, cost in VARIANTS:
            v = await create_variant(session, by_name[item_name], name=vname,
                                     sell_price=Decimal(sell), cost_price=Decimal(cost))
            larges[by_name[item_name].id] = v
        for item_name, gname, selection, required, choices in MODIFIERS:
            group = await create_modifier_group(session, by_name[item_name], name=gname,
                                                selection=selection, is_required=required)
            for order, (mname, delta, is_default) in enumerate(choices):
                await create_modifier(session, group, name=mname, price_delta=Decimal(delta),
                                      is_default=is_default, sort_order=order)
        # Recipes: the drinks are made to order from the raw materials.
        variant_by = {}
        for item, _w in items:
            variant_by[(item.name, "Standar")] = defaults[item.id]
        for item_id, v in larges.items():
            variant_by[(next(i.name for i, _w in items if i.id == item_id), v.name)] = v.id
        from app.models import ItemVariant
        for item_name, vname, components in RECIPES:
            variant = await session.get(ItemVariant, variant_by[(item_name, vname)])
            for comp_name, qty, code in components:
                await set_recipe_line(session, variant, by_name[comp_name], quantity=Decimal(qty), uom_id=uoms[code].id)

        sellable = [(i, w) for i, w in items if w > 0]
        sold_per_item: dict = {}  # item.id -> total quantity sold in the history
        sale_movements: list[tuple] = []  # (item, qty, sold_at, line, staff_id, unit_cost) — ledgered after the loop
        day_books: dict = {}  # local date -> (posted_at, {component: amount}) — journalled per day after the loop
        yesterday_till: list[tuple] = []  # (order, payment) Sari rang up yesterday — her closed shift (M7-T1)
        for day_offset in range(30, 0, -1):
            day = now - timedelta(days=day_offset)
            weekend = day.weekday() >= 5
            base = 26 if weekend else 18
            if day_offset == 1:
                base = 55  # planted anomaly: yesterday was unusually busy
            n_sales = base + rng.randint(-4, 4)
            for _ in range(n_sales):
                item, _w = rng.choices(sellable, weights=[w for _, w in sellable])[0]
                qty = Decimal(rng.choices([1, 1, 1, 2], weights=[6, 6, 6, 2])[0])
                # Business hours in the café's own timezone (07:00–20:59 WIB),
                # stored as UTC — not UTC-hour times that read as 3 AM sales.
                sold_at = day.astimezone(ZoneInfo("Asia/Jakarta")).replace(
                    hour=rng.randint(7, 20), minute=rng.randint(0, 59), second=0, microsecond=0
                ).astimezone(timezone.utc)
                # Order model (M3-T2): one order, one line, one payment per sale.
                # Cash dominates a warung; QRIS is the common alternative.
                # One in four drinks with a Large size sells as Large (M4-T1).
                large = larges.get(item.id)
                variant_id, unit_price, unit_cost = defaults[item.id], item.sell_price, item.cost_price
                if large is not None and rng.random() < 0.25:
                    variant_id, unit_price, unit_cost = large.id, large.sell_price, large.cost_price
                total = unit_price * qty
                staff_id = rng.choice(staff_ids)
                regular = rng.choice(regulars) if rng.random() < 0.18 else None   # about one sale in six is a known customer
                order = Order(
                    business_id=business_id, staff_id=staff_id, order_type="takeaway",
                    status="completed", subtotal=total, total=total, sold_at=sold_at,
                    created_at=sold_at, customer_id=regular.id if regular is not None else None,
                )
                session.add(order)
                await session.flush()
                if regular is not None:
                    await award_points_for_order(session, business_id, regular, order.id, eligible_amount=total,
                                                 staff_id=staff_id, created_at=sold_at)
                line = OrderLine(
                    business_id=business_id, order_id=order.id, item_id=item.id, variant_id=variant_id,
                    quantity=qty, unit_price=unit_price, line_total=total,
                    unit_cost_at_sale=unit_cost, created_at=sold_at,
                )
                session.add(line)
                method = rng.choices(["cash", "qris"], weights=[7, 3])[0]
                payment = Payment(
                    business_id=business_id, order_id=order.id, method=method,
                    amount=total, created_at=sold_at,
                )
                session.add(payment)
                if day_offset == 1 and staff_id == sari.id:
                    yesterday_till.append((order, payment))
                close_of_day = sold_at.astimezone(ZoneInfo("Asia/Jakarta")).replace(hour=21, minute=0).astimezone(timezone.utc)
                _, components = day_books.setdefault(close_of_day.date(), (close_of_day, {}))
                components[f"payment:{method}"] = components.get(f"payment:{method}", Decimal(0)) + total
                components["cogs"] = components.get("cogs", Decimal(0)) + unit_cost * qty
                sold_per_item[item.id] = sold_per_item.get(item.id, Decimal(0)) + qty
                sale_movements.append((item, qty, sold_at, line, staff_id, unit_cost))
        await session.flush()

        # Stock ledger (M2-T2/M2-T3): the demo café opened 31 days ago with
        # enough of everything to cover the history, then sold it one row at a
        # time, so SUM(qty_delta) per item equals today's current_stock.
        opened_at = now - timedelta(days=31)
        for item, _w in items:
            await record_movement(
                session, business_id=business_id, item_id=item.id,
                qty_delta=Decimal(item.current_stock) + sold_per_item.get(item.id, Decimal(0)),
                reason="opname", source_type="seed", unit_cost=item.cost_price,
                created_at=opened_at,
            )
        for item, qty, sold_at, line, staff_id, unit_cost in sale_movements:
            await record_movement(
                session, business_id=business_id, item_id=item.id, qty_delta=-qty,
                reason="sale", source_type="sale", source_id=line.id,
                unit_cost=unit_cost, staff_id=staff_id, created_at=sold_at,
            )

        # Shifts (M7-T1): yesterday Sari opened with a 200.000 float, rang up
        # her sales, and counted 5.000 short at close. Today's till is not open
        # yet — the kiosk opens it. The 5.000 short is posted (M7-T3) like a
        # real close would post it, so the books match the till report.
        yesterday = (now - timedelta(days=1)).astimezone(ZoneInfo("Asia/Jakarta"))
        opened_at = yesterday.replace(hour=7, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        closed_at = yesterday.replace(hour=21, minute=5, second=0, microsecond=0).astimezone(timezone.utc)
        cash_taken = sum((p.amount for _o, p in yesterday_till if p.method == "cash"), Decimal(0))
        expected = Decimal(200000) + cash_taken
        shift = Shift(
            business_id=business_id, staff_id=sari.id, status="closed", opening_float=Decimal(200000),
            opened_at=opened_at, closed_at=closed_at, closed_by=owner.id,
            expected_cash=expected, counted_cash=expected - Decimal(5000), variance=Decimal(-5000),
            notes="kurang 5rb, mungkin kembalian", created_at=opened_at, updated_at=closed_at,
        )
        session.add(shift)
        await session.flush()
        for order, payment in yesterday_till:
            order.shift_id = shift.id
            payment.shift_id = shift.id
        await post_variance(session, shift)

        # Promos (M8-T3): a weekday afternoon BOGO on the Kopi Arabica cup, and a
        # Friday 10% off Nasi Goreng. Both evaluate themselves at the till.
        from datetime import time as _time
        arabica = next(i for i, _w in items if i.name.startswith("Kopi Arabica"))
        nasgor = next(i for i, _w in items if i.name.startswith("Nasi Goreng"))
        await create_promo(
            session, business_id, name="Beli 1 gratis 1 Kopi Arabica (sore)", kind="bonus_item", item_id=arabica.id,
            max_per_order=2,
            conditions=[
                {"kind": "day_of_week", "days_of_week": [0, 1, 2, 3, 4]},
                {"kind": "time_window", "time_start": _time(14, 0), "time_end": _time(17, 0)},
            ],
        )
        await create_promo(
            session, business_id, name="Jumat Nasi Goreng 10%", kind="percent_off", value=Decimal("0.10"), item_id=nasgor.id,
            conditions=[{"kind": "day_of_week", "days_of_week": [4]}],
        )

        # Books (M6-T4/M6-T5): the opening stock is capitalised as owner's
        # capital and the history's sales post through the engine, one summary
        # entry per day, so the statements show a real month.
        from app.services.ledger import LineSpec, post_entry
        from app.services.posting import post_event

        opening_value = sum(
            ((Decimal(item.current_stock) + sold_per_item.get(item.id, Decimal(0))) * Decimal(item.cost_price) for item, _w in items),
            Decimal(0),
        )
        await post_entry(
            session, business_id, lines=[LineSpec("1300", debit=opening_value), LineSpec("3100", credit=opening_value)],
            memo="Modal awal: persediaan pembukaan", source_type="seed", event_type="OpeningBalance", posted_at=opened_at,
        )
        for _day, (posted_at, components) in sorted(day_books.items()):
            await post_event(session, business_id, "OrderCompleted", components, source_type="seed",
                             memo=f"penjualan {_day.isoformat()}", posted_at=posted_at)

        # Expenses go through the one writer (M6-T6), so they are on the P&L.
        from app.services.expenses import record_expense

        for category, description, amount, days_ago in EXPENSES:
            await record_expense(
                session, business_id, amount=Decimal(amount), category=category,
                description=description, source="manual", occurred_at=now - timedelta(days=days_ago),
            )

    print(f"Seeded 'Kopi Kenangan Senja' (business_id={business_id})")
    print(f"Owner phone {OWNER_PHONE} | owner PIN 1234 | Sari 2345 | Budi 3456")


if __name__ == "__main__":
    asyncio.run(seed())
