"""M6-T3 — posting rules are data, not if statements.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dashboard import edit_posting_rule, list_posting_rules
from app.models import Business, PostingRule
from app.schemas.dashboard import PostingRuleUpdateIn
from app.services.accounts import STANDARD_CHART, ensure_standard_chart
from app.services.posting_rules import (
    EVENT_TYPES,
    STANDARD_RULES,
    PostingRuleInvalid,
    ensure_standard_rules,
    pick_rule,
    rules_for,
    update_rule,
)

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


def test_every_rule_references_an_account_in_the_chart_and_covers_the_catalogue():
    codes = {c for c, _, _ in STANDARD_CHART}
    for event_type, component, debit, credit, _ in STANDARD_RULES:
        if component == "reversal":
            assert debit is None and credit is None
            continue
        assert debit in codes and credit in codes and debit != credit, (event_type, component)
    keys = [(e, c) for e, c, *_ in STANDARD_RULES]
    assert len(keys) == len(set(keys))
    # The events the roadmap names for the seed: sale, discount, tax, COGS, void, refund,
    # goods receipt, supplier payment, waste, opname variance, expense.
    for event, component in (
        ("OrderCompleted", "payment:cash"), ("OrderCompleted", "discount"), ("OrderCompleted", "tax"),
        ("OrderCompleted", "cogs"), ("OrderVoided", "reversal"), ("OrderRefunded", "refund:cash"),
        ("GoodsReceived", "inventory"), ("SupplierPaid", "payment:cash"), ("StockWasted", "waste"),
        ("StockCounted", "variance_loss"), ("StockCounted", "variance_gain"), ("ExpenseIncurred", "expense:*"),
    ):
        assert (event, component) in keys
    assert "ShiftClosed" in EVENT_TYPES and "PointsEarned" in EVENT_TYPES


def test_pick_rule_falls_back_to_the_wildcard():
    rules = {"expense:*": "fallback", "expense:gaji": "gaji", "cogs": "cogs"}
    assert pick_rule(rules, "expense:gaji") == "gaji"
    assert pick_rule(rules, "expense:parkir") == "fallback"
    assert pick_rule(rules, "cogs") == "cogs"
    assert pick_rule(rules, "tax") is None


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
async def shop(session_factory):
    async with session_factory() as s:
        biz = Business(name="Rules Test", owner_phone=f"62980{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await ensure_standard_chart(s, bid)
        await ensure_standard_rules(s, bid)
        await s.commit()
    yield bid
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_rules_are_seeded_idempotently_and_looked_up_as_data(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        await ensure_standard_rules(s, shop)
        assert (await s.execute(select(func.count(PostingRule.id)))).scalar_one() == len(STANDARD_RULES)
        sale = await rules_for(s, "OrderCompleted")
        assert (sale["payment:cash"].debit_code, sale["payment:cash"].credit_code) == ("1100", "4100")
        assert (sale["cogs"].debit_code, sale["cogs"].credit_code) == ("5100", "1300")
        assert pick_rule(await rules_for(s, "ExpenseIncurred"), "expense:parkir").debit_code == "5900"
        assert (await rules_for(s, "OrderVoided"))["reversal"].debit_code is None


async def test_owner_can_repoint_or_disable_a_rule_and_rules_stay_valid(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        qris = (await rules_for(s, "OrderCompleted"))["payment:qris"]
        await update_rule(s, qris, debit_code="1110")            # QRIS settles straight into the bank
        assert (await rules_for(s, "OrderCompleted"))["payment:qris"].debit_code == "1110"
        with pytest.raises(PostingRuleInvalid) as exc:
            await update_rule(s, qris, debit_code="9999")
        assert exc.value.code == "account"
        with pytest.raises(PostingRuleInvalid) as exc:
            await update_rule(s, qris, debit_code="4100")          # same as credit
        assert exc.value.code == "same"
        void = (await rules_for(s, "OrderVoided"))["reversal"]
        with pytest.raises(PostingRuleInvalid) as exc:
            await update_rule(s, void, debit_code="1100", credit_code="4100")
        assert exc.value.code == "reversal"
        service = (await rules_for(s, "OrderCompleted"))["service_charge"]
        await update_rule(s, service, is_active=False)
        assert "service_charge" not in await rules_for(s, "OrderCompleted")
        await s.commit()


async def test_owner_endpoints(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        ctx = SimpleNamespace(session=s, business_id=shop, staff_id=None)
        rows = await list_posting_rules(ctx, event_type="OrderCompleted")
        assert len(rows) == 13 and all(r.is_system for r in rows)   # 7 payment methods, discount, tax, service charge, cogs, rounding up/down (M7-T4b)
        cash = next(r for r in rows if r.component == "payment:cash")
        out = await edit_posting_rule(cash.id, PostingRuleUpdateIn(description="Kas laci"), ctx)
        assert out.description == "Kas laci"
        with pytest.raises(HTTPException) as exc:
            await edit_posting_rule(cash.id, PostingRuleUpdateIn(debit_code="7777"), ctx)
        assert exc.value.status_code == 404
        assert len(await list_posting_rules(ctx, event_type=None)) == len(STANDARD_RULES)
        await s.commit()
