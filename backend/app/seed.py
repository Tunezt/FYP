"""Seed the demo café: `python -m app.seed`

Creates **Kopi Kenangan Senja** (fictional, Jakarta) with:
- owner + 2 staff (PINs below)
- 11 items: sellable drinks/food with stock in servings, plus raw materials in kg/liter
- 30 days of deterministic pseudo-random sales history (weekend bumps, one
  planted sales spike yesterday so anomaly detection has something to find)
- a few manual expenses so the P&L view isn't empty

Idempotent: re-running deletes and recreates the demo business (cascade).

Demo credentials:
  owner phone  +62 812-0000-1111  (OTP arrives via WhatsApp; in dev the code is logged)
  owner PIN    1234   | staff Sari PIN 2345 | staff Budi PIN 3456
"""
import asyncio
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select

from app.core.db import plain_session, tenant_session
from app.core.security import hash_pin
from app.models import Business, Expense, Item, Sale, Staff

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

        items = []
        for name, unit, stock, cost, sell, reorder, weight in ITEMS:
            item = Item(
                business_id=business_id, name=name, unit=unit,
                current_stock=Decimal(stock), cost_price=Decimal(cost),
                sell_price=Decimal(sell), reorder_threshold=Decimal(reorder),
            )
            session.add(item)
            items.append((item, weight))
        await session.flush()

        sellable = [(i, w) for i, w in items if w > 0]
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
                sold_at = day.replace(
                    hour=rng.randint(7, 20), minute=rng.randint(0, 59), second=0, microsecond=0
                )
                session.add(
                    Sale(
                        business_id=business_id, item_id=item.id, quantity=qty,
                        unit_price=item.sell_price,
                        total_price=item.sell_price * qty,
                        staff_id=rng.choice(staff_ids), sold_at=sold_at,
                    )
                )

        for category, description, amount, days_ago in EXPENSES:
            session.add(
                Expense(
                    business_id=business_id, amount=Decimal(amount), category=category,
                    description=description, source="manual",
                    occurred_at=now - timedelta(days=days_ago),
                )
            )

    print(f"Seeded 'Kopi Kenangan Senja' (business_id={business_id})")
    print(f"Owner phone {OWNER_PHONE} | owner PIN 1234 | Sari 2345 | Budi 3456")


if __name__ == "__main__":
    asyncio.run(seed())
