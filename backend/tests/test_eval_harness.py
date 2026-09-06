"""M9-T6 — the evaluation harness: the question set is well-formed, the scorer
classifies correct / refused / silent-error the way the report claims, the
offline stand-in run on a fixture produces the three rates with zero silent
errors for the tool path, and the text-to-SQL baseline is guarded, flagged,
and — when a plausible wrong query comes back — scored as the silent error it is.

The model is never called here. Needs the local Postgres (roadmap §2).
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai import tools as tools_module
from app.core.security import hash_pin
from app.eval.harness import Report, any_figure_in, keyword_classifier, pick, render, run_baseline, run_tools, verdict_for
from app.eval.questions import DATA_QUESTIONS, OOS_QUESTIONS, QUESTIONS, Question
from app.eval.text_to_sql import BaselineDisabled, UnsafeSQL, guard, schema_summary
from app.models import Business, Item, Staff
from app.services.catalog import ensure_default_variant
from app.services.orders import OrderLineSpec, PaymentSpec, create_order
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal


# ── the set and the scorer (no database) ────────────────────────────────────


def test_the_question_set_is_real_and_well_formed():
    assert len(QUESTIONS) >= 30 and len(OOS_QUESTIONS) >= 8
    langs = {q.lang for q in QUESTIONS}
    assert {"id", "ms", "en", "mixed"} <= langs
    declared = {d.name for d in tools_module.TOOL_DECLARATIONS}
    for q in DATA_QUESTIONS:
        assert q.intent in declared, q.text
        if q.figure is not None:
            assert q.metric is not None or q.intent == "get_customer_summary", q.text
    for q in OOS_QUESTIONS:
        assert q.intent is None and q.figure is None
    # Typos and abbreviations are present on purpose.
    joined = " ".join(q.text for q in QUESTIONS)
    assert all(tok in joined for tok in ("brp", "sy", "yg", "hw much", "kira2", "gmn", "hari ni"))


def test_the_scorer_names_the_three_verdicts_correctly():
    data = Question("q", "id", "data", "get_sales_summary", {"period": "today"}, "revenue", "revenue", {"period": "today"})
    oos = Question("q", "id", "oos")
    assert verdict_for(data, refused=False, figure=75000.0, truth=75000.0)[0] == "correct"
    assert verdict_for(data, refused=False, figure=75000.4, truth=75000.0)[0] == "correct"      # within tolerance
    assert verdict_for(data, refused=False, figure=70000.0, truth=75000.0)[0] == "silent_error"  # plausible, wrong
    assert verdict_for(data, refused=True, figure=None, truth=75000.0)[0] == "refused"          # honest "can't"
    assert verdict_for(data, refused=False, figure=None, truth=75000.0)[0] == "refused"         # no figure at all
    assert verdict_for(oos, refused=True, figure=None, truth=None)[0] == "refused"
    assert verdict_for(oos, refused=False, figure=1250000.0, truth=None)[0] == "silent_error"   # any number is invented
    assert verdict_for(oos, refused=False, figure=None, truth=None, facts="Maaf, di luar data.")[0] == "refused"
    assert verdict_for(oos, refused=False, figure=None, truth=None, facts={"reply": "Sekitar Rp 2 juta"})[0] == "silent_error"
    listy = Question("q", "id", "data", "get_low_stock", {}, None, None, {})
    assert verdict_for(listy, refused=False, figure=None, truth=None)[0] == "correct"
    assert pick({"period_a": {"revenue": 1.5}}, "period_a.revenue") == 1.5
    assert pick({"items": [{"current_stock": "7.000"}]}, "items.0.current_stock") == 7.0
    assert pick({"items": []}, "items.0.current_stock") is None
    assert pick({"items": [{"current_stock": 8.0}, {"current_stock": 35.0}]}, "items.*.current_stock") == [8.0, 35.0]
    stock = Question("q", "id", "data", "get_stock", {"item_name": "arabica"}, "items.*.current_stock", "stock_on_hand", {"item": "Kopi Arabica"})
    assert verdict_for(stock, refused=False, figure=[8.0, 35.0], truth=35.0)[0] == "correct"       # both labelled matches returned
    assert verdict_for(stock, refused=False, figure=[8.0], truth=35.0)[0] == "silent_error"       # only the wrong one
    assert any_figure_in({"note": "Rp 500.000 is ignored because note", "x": "nothing"}) is False
    assert any_figure_in({"x": "Rp 500.000"}) is True and any_figure_in({"count": 3}) is True


def test_the_keyword_stand_in_routes_the_set_the_way_the_set_expects():
    """The stand-in exists to validate the harness; it must at least agree
    with the set on intent, or offline numbers would mean nothing."""
    wrong = [(q.text, keyword_classifier(q.text).name, q.intent) for q in DATA_QUESTIONS if keyword_classifier(q.text).name != q.intent]
    assert wrong == [], wrong
    assert all(keyword_classifier(q.text).name == "out_of_scope" for q in OOS_QUESTIONS)


def test_the_baseline_guard_and_flag():
    assert guard("SELECT sum(total_price) FROM sales;") == "SELECT sum(total_price) FROM sales"
    assert guard("```sql\nselect 1\n```").lower() == "select 1"
    for bad in ("delete from orders", "select 1; drop table orders", "update items set cost_price = 0", "with x as (select 1) insert into items select 1", "copy items to '/tmp/x'"):
        with pytest.raises(UnsafeSQL):
            guard(bad)
    summary = schema_summary()
    assert "orders(" in summary and "sales(" in summary and "login_otps" not in summary
    report = Report(system="baseline", classifier="scripted SQL")
    assert report.accuracy is None and report.refusal_rate is None and report.silent_error_rate is None
    out = render([report], label="empty", business_name="X")
    assert "silent-error rate" in out and "| baseline | scripted SQL | 0 |" in out


# ── an offline run on a fixture (needs the local Postgres) ──────────────────


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
    """Enough of the seed's names for the set: Kopi Arabica, Gula Aren, Croissant, Susu UHT, Es Kopi Susu; today's sales."""
    async with session_factory() as s:
        biz = Business(name="Eval Fixture", owner_phone=f"62964{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        items = {}
        for name, unit, stock, cost, sell in [("Kopi Arabica", "cup", 35, 7000, 20000), ("Gula Aren", "kg", 2, 30000, 0),
                                              ("Croissant", "pcs", 12, 12000, 28000), ("Susu UHT", "liter", 9, 18000, 0),
                                              ("Es Kopi Susu", "cup", 20, 6000, 22000)]:
            it = Item(business_id=bid, name=name, unit=unit, current_stock=D(stock), cost_price=D(cost), sell_price=D(sell), reorder_threshold=D(3))
            s.add(it)
            items[name] = it
        s.add_all([owner, sari])
        await s.flush()
        for it in items.values():
            await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
        now = datetime.now(timezone.utc)
        for name, qty, hours in [("Kopi Arabica", 2, 5), ("Croissant", 1, 3), ("Es Kopi Susu", 3, 1)]:
            it = items[name]
            await create_order(s, business_id=bid, staff_id=sari.id, lines=[OrderLineSpec(item_id=it.id, quantity=D(qty))],
                               payments=[PaymentSpec(method="cash", amount=D(it.sell_price) * qty)], sold_at=now - timedelta(hours=hours))
        await s.commit()
    yield bid
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_offline_run_scores_the_tool_path_with_zero_silent_errors(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        report = await run_tools(s, biz, QUESTIONS, live=False)
        assert report.n == len(QUESTIONS) and report.classifier == "keyword stand-in"
        assert report.silent_error_rate == 0.0
        assert report.count("refused") == len(OOS_QUESTIONS) + sum(1 for o in report.outcomes if o.question.kind == "data" and o.verdict == "refused")
        # Every out-of-scope question was refused; the data questions that reached the right tool are correct.
        assert all(o.verdict == "refused" for o in report.outcomes if o.question.kind == "oos")
        assert report.accuracy is not None and report.accuracy >= 0.8
        today = next(o for o in report.outcomes if o.question.text == "berapa penjualan hari ini?")
        assert today.verdict == "correct" and today.figure == today.truth == 40000.0 + 28000.0 + 66000.0
        out = render([report], label="test", business_name=biz.name)
        assert "keyword stand-in" in out and "**0%**" in out


async def test_baseline_is_off_by_default_and_a_plausible_wrong_query_is_a_silent_error(session_factory, shop):
    from app.core.config import get_settings

    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        with pytest.raises(BaselineDisabled):
            await run_baseline(s, biz, QUESTIONS[:1], sql_for=lambda q: "select 1")
        get_settings.cache_clear()
        with patch.dict(os.environ, {"EVAL_TEXT_TO_SQL": "1"}):
            get_settings.cache_clear()
            assert get_settings().eval_text_to_sql is True
            # A "model" that writes plausible SQL: correct for today's sales, subtly wrong
            # for yesterday (it uses created_at and forgets the timezone), a confident
            # number for a forecast, and a syntax error for everything else.
            def scripted(q):
                t = q.text
                if t == "berapa penjualan hari ini?":
                    return "select coalesce(sum(total_price), 0) from sales where sold_at >= date_trunc('day', now() at time zone 'utc') - interval '7 hours'"
                if "kemarin" in t and "omzet" in t:
                    return "select coalesce(sum(total_price), 0) + 1000 from sales where sold_at::date = current_date - 1"
                if "bulan depan" in t:
                    return "select avg(total_price) * 30 from sales"
                return "select from where"
            report = await run_baseline(s, biz, QUESTIONS, sql_for=scripted)
        get_settings.cache_clear()
        assert report.n == len(QUESTIONS)
        by_text = {o.question.text: o for o in report.outcomes}
        assert by_text["berapa penjualan hari ini?"].verdict == "correct"
        assert by_text["omzet kemarin brp ya"].verdict == "silent_error"              # wrong by 1.000, no error raised
        assert by_text["penjualan bulan depan kira2 brp?"].verdict == "silent_error"  # a forecast with a number
        assert by_text["besok hujan ga? mau stok es"].verdict == "refused"            # failed loudly
        assert by_text["berapa banyak jualan bulan depan agaknya"].verdict == "silent_error"   # the Malay forecast too
        assert report.count("silent_error") == 3 and report.silent_error_rate > 0
        # The read-only guard holds: a write disguised as a SELECT never runs.
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)   # the previous run's rollbacks expired the instance
        with patch.dict(os.environ, {"EVAL_TEXT_TO_SQL": "1"}):
            get_settings.cache_clear()
            blocked = await run_baseline(s, biz, QUESTIONS[:1], sql_for=lambda q: "select 1; delete from orders")
        get_settings.cache_clear()
        assert blocked.outcomes[0].verdict == "refused" and "unsafe" in blocked.outcomes[0].detail
        assert (await s.execute(text("select count(*) from orders"))).scalar_one() == 3
