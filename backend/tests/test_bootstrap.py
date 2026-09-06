"""M15-T3 — a clean production bootstrap, and a demo seed that refuses to touch
a real cafe.

"Empty and usable" is two claims, and the second is the one worth testing: a
business with no posting rules cannot ring up its first sale, however tidy it
looks. So this bootstraps one and then sells something through it.

Needs the local Postgres (roadmap §2). No skip marker: an unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.config as config_module
import app.seed as seed_module
from app.bootstrap import BootstrapError, bootstrap
from app.core.config import ProductionRefusal, get_settings
from app.models import (
    Account, Alert, Business, Customer, Expense, Item, JournalLine, LoyaltySettings, Order,
    PostingRule, PricingSettings, Staff, StockMovement, Supplier, Uom,
)
from app.services.accounts import STANDARD_CHART
from app.services.catalog import ensure_default_variant
from app.services.orders import OrderLineSpec, PaymentSpec, create_order
from app.services.posting_rules import STANDARD_RULES
from app.services.stock import open_item_stock

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal


@pytest.fixture(autouse=True)
async def dispose_app_engine():
    """`bootstrap()` is a CLI entrypoint and connects through the app's own
    engine. pytest-asyncio gives each test its own event loop, which strands
    that pool's asyncpg connections in the previous one, so it is disposed
    between tests (the same trap M11-T1 hit)."""
    yield
    from app.core.db import engine as app_engine

    await app_engine.dispose()


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


async def _scoped(session, business_id) -> None:
    await session.execute(
        text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)}
    )


def _phone() -> str:
    return f"08{uuid.uuid4().int % 10**10:010d}"


@pytest.fixture
async def bootstrapped(session_factory):
    """A business made the way a real one is made, torn down afterwards."""
    result = await bootstrap(
        name="Kopi Bu Tini", owner_name="Bu Tini", phone=_phone(), pin="4821"
    )
    yield result
    async with session_factory() as session:
        stale = await session.get(Business, result.business_id)
        if stale:
            await session.delete(stale)
        await session.commit()


# ── what bootstrap creates ───────────────────────────────────────────────────

async def test_bootstrap_creates_one_business_with_its_books(session_factory, bootstrapped):
    assert bootstrapped.accounts == len(STANDARD_CHART)
    assert bootstrapped.posting_rules == len(STANDARD_RULES)
    assert bootstrapped.uoms > 0

    async with session_factory() as session:
        await _scoped(session, bootstrapped.business_id)
        business = await session.get(Business, bootstrapped.business_id)
        assert business.name == "Kopi Bu Tini"
        assert business.timezone == "Asia/Jakarta"
        assert business.language_preference == "id"

        async def count(model):
            return await session.scalar(
                select(func.count()).select_from(model).where(model.business_id == business.id)
            )

        # The owner can log in and use the till.
        owners = (await session.execute(select(Staff).where(Staff.business_id == business.id))).scalars().all()
        assert len(owners) == 1
        assert owners[0].role == "owner" and owners[0].name == "Bu Tini"
        assert owners[0].pin_hash and owners[0].pin_hash != "4821"

        # The books exist, in full.
        assert await count(Account) == len(STANDARD_CHART)
        assert await count(PostingRule) == len(STANDARD_RULES)
        assert await count(Uom) == bootstrapped.uoms
        assert await count(PricingSettings) == 1
        assert await count(LoyaltySettings) == 1


async def test_bootstrap_creates_no_data(session_factory, bootstrapped):
    """"And nothing else": not one invented sale, product or customer."""
    async with session_factory() as session:
        await _scoped(session, bootstrapped.business_id)
        for model in (Item, Order, Customer, Supplier, Expense, StockMovement, JournalLine, Alert):
            count = await session.scalar(
                select(func.count()).select_from(model).where(model.business_id == bootstrapped.business_id)
            )
            assert count == 0, f"{model.__tablename__} is not empty"


async def test_the_bootstrapped_business_can_take_its_first_sale(session_factory, bootstrapped):
    """Usable, not just tidy. The posting engine refuses events from a business
    without rules, so this is the claim that a business straight out of
    bootstrap actually trades."""
    business_id = bootstrapped.business_id
    async with session_factory() as session:
        await _scoped(session, business_id)
        staff = (await session.execute(select(Staff).where(Staff.business_id == business_id))).scalars().one()
        kopi = Item(business_id=business_id, name="Kopi Tubruk", unit="cup",
                    current_stock=D(50), cost_price=D(4000), sell_price=D(12000))
        session.add(kopi)
        await session.flush()
        await open_item_stock(session, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(session, kopi)

        created = await create_order(
            session, business_id=business_id, staff_id=staff.id,
            lines=[OrderLineSpec(item_id=kopi.id, quantity=D(2))],
            payments=[PaymentSpec(method="cash", amount=D(24000))],
        )
        order_id = created.order.id
        await session.commit()

    async with session_factory() as session:
        await _scoped(session, business_id)
        assert (await session.get(Order, order_id)).total == D("24000.00")
        assert (await session.get(Item, kopi.id)).current_stock == D("48.000")
        debits, credits = (
            await session.execute(
                select(func.coalesce(func.sum(JournalLine.debit), 0),
                       func.coalesce(func.sum(JournalLine.credit), 0))
                .where(JournalLine.business_id == business_id)
            )
        ).one()
        assert debits == credits > 0


async def test_bootstrap_normalises_a_locally_typed_number(session_factory):
    """The owner types their number the way the login form's own placeholder
    tells them to. Stored the way WhatsApp addresses it, or they would never
    match their own account."""
    local = f"08{uuid.uuid4().int % 10**10:010d}"
    result = await bootstrap(name="Warung Uji", owner_name="Uji", phone=local, pin="1234")
    try:
        assert result.owner_phone == "62" + local[1:]
    finally:
        async with session_factory() as session:
            await session.delete(await session.get(Business, result.business_id))
            await session.commit()


async def test_bootstrap_refuses_a_phone_that_already_has_a_business(bootstrapped):
    with pytest.raises(BootstrapError, match="already has a business"):
        await bootstrap(
            name="Second Cafe", owner_name="Someone Else",
            phone=bootstrapped.owner_phone, pin="9999",
        )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"name": "  "}, "needs a name"),
        ({"owner_name": ""}, "owner needs a name"),
        ({"pin": "12"}, "4 to 8 digits"),
        ({"pin": "abcd"}, "4 to 8 digits"),
        ({"phone": "12"}, "phone number"),
    ],
)
async def test_bootstrap_refuses_incomplete_input(kwargs, message):
    args = {"name": "Warung", "owner_name": "Owner", "phone": _phone(), "pin": "1234", **kwargs}
    with pytest.raises(BootstrapError, match=message):
        await bootstrap(**args)


# ── the seed refuses to touch a real cafe ────────────────────────────────────

def _settings(monkeypatch, **overrides):
    settings = get_settings().model_copy(update=overrides)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(seed_module, "get_settings", lambda: settings)
    return settings


def test_seed_refuses_in_production(monkeypatch):
    _settings(monkeypatch, environment="production")
    with pytest.raises(ProductionRefusal, match="ENVIRONMENT=production"):
        seed_module.guard_demo_data()


def test_seed_refuses_a_database_that_is_not_on_this_machine(monkeypatch):
    """ENVIRONMENT is a self-declared flag, and a real cafe's database is remote
    long before anyone remembers to set it."""
    _settings(
        monkeypatch, environment="development",
        database_url="postgresql+asyncpg://app_role:pw@db.supabase.co:5432/postgres",
    )
    with pytest.raises(ProductionRefusal, match="not this machine"):
        seed_module.guard_demo_data()
    seed_module.guard_demo_data(confirm=True)   # deliberate is allowed


def test_seed_runs_against_a_local_database(monkeypatch):
    _settings(
        monkeypatch, environment="development",
        database_url="postgresql+asyncpg://app_role:pw@localhost:5432/warung_pintar",
    )
    seed_module.guard_demo_data()   # no exception: this is the development default


def test_production_refusal_names_the_entrypoint(monkeypatch):
    _settings(monkeypatch, environment="production")
    with pytest.raises(ProductionRefusal, match="app.bootstrap"):
        config_module.refuse_in_production("app.seed")
