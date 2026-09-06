"""Database invariants (docs/BUILD-ROADMAP.md §1, §2 and M0-T4 onward).

These run against the real local Postgres — INTEGRATION_DATABASE_URL, which
tests/conftest.py defaults to DATABASE_URL when that points at localhost. They
deliberately have no skip marker: an invariant that cannot be checked is not a
passing invariant, so an unreachable database fails loudly here instead of
silently going green (roadmap §2: "if they skip, you are flying blind").

A failure in this file is never flaky. Treat it as data corruption until proven
otherwise (roadmap §1.8, §3).
"""
import os
import re

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")

# Column names that carry rupiah or quantities. Anything matching must be exact
# `numeric`, never `real` / `double precision` (roadmap §1.3).
MONEY_OR_QUANTITY = re.compile(
    r"amount|price|total|cost|stock|quantity|qty|threshold|discount|charge|rounding|debit|credit|float|cash|variance"
)
# Columns that merely *name* a money thing without holding a number (`debit_code`
# on posting rules, `unit_price_written` flags, `rounding_mode` and
# `discount_requires_pin` on pricing settings) — never numeric by design. The
# invariant is unchanged: everything that does hold rupiah still has to be
# numeric, and KNOWN_MONEY_COLUMNS below proves the pattern still catches them.
NOT_A_NUMBER = re.compile(r"_(code|id|type|written|at|name|key|text|mode|pin)$")


def _is_money_column(column: str) -> bool:
    return bool(MONEY_OR_QUANTITY.search(column)) and not NOT_A_NUMBER.search(column)

# Every column that should be caught today. Guards against the pattern silently
# matching nothing (e.g. after a rename) and the test passing vacuously.
KNOWN_MONEY_COLUMNS = {
    ("items", "current_stock"),
    ("items", "cost_price"),
    ("items", "sell_price"),
    ("items", "reorder_threshold"),
    ("sales", "quantity"),
    ("sales", "unit_price"),
    ("sales", "total_price"),
    ("receipts", "total_amount"),
    ("expenses", "amount"),
    ("stock_movements", "qty_delta"),
    ("stock_movements", "unit_cost"),
    ("orders", "subtotal"),
    ("orders", "discount_total"),
    ("orders", "tax_total"),
    ("orders", "service_charge"),
    ("orders", "rounding"),
    ("orders", "total"),
    ("order_lines", "quantity"),
    ("order_lines", "unit_price"),
    ("order_lines", "line_discount"),
    ("order_lines", "line_total"),
    ("order_lines", "unit_cost_at_sale"),
    ("payments", "amount"),
    ("shifts", "opening_float"),
    ("shifts", "expected_cash"),
    ("shifts", "counted_cash"),
    ("shifts", "variance"),
    ("item_variants", "sell_price"),
    ("item_variants", "cost_price"),
    ("modifiers", "price_delta"),
    ("order_line_modifiers", "price_delta"),
    ("recipe_lines", "quantity"),
    ("purchase_orders", "subtotal"),
    ("po_lines", "quantity"),
    ("po_lines", "unit_cost"),
    ("po_lines", "line_total"),
    ("po_lines", "received_quantity"),
    ("goods_receipts", "subtotal"),
    ("goods_receipt_lines", "quantity"),
    ("goods_receipt_lines", "quantity_item_unit"),
    ("goods_receipt_lines", "unit_cost"),
    ("goods_receipt_lines", "unit_cost_item_unit"),
    ("goods_receipt_lines", "line_total"),
    ("journal_lines", "debit"),
    ("journal_lines", "credit"),
    ("pricing_settings", "service_charge_rate"),
    ("pricing_settings", "rounding_unit"),
    ("orders", "promo_total"),
    ("promo_conditions", "amount"),
    ("promo_conditions", "quantity"),
    ("promo_applications", "amount"),
    ("promo_applications", "bonus_quantity"),
    ("promos", "bonus_quantity"),
    ("orders", "voucher_total"),
    ("vouchers", "max_discount"),
    ("voucher_redemptions", "amount"),
}

# pg_catalog rather than information_schema: the latter only lists columns the
# connecting role has a privilege on, so a table created without grants would
# vanish from the check instead of failing it. relkind covers tables, partitions,
# views and materialized views — M3-T2 turns `sales` into a view and it must stay
# covered.
COLUMNS_SQL = text(
    """
    select c.relname as table_name, a.attname as column_name, t.typname as type_name
    from pg_attribute a
    join pg_class c on c.oid = a.attrelid
    join pg_namespace n on n.oid = c.relnamespace
    join pg_type t on t.oid = a.atttypid
    where n.oid = :schema_oid
      and c.relkind in ('r', 'p', 'v', 'm')
      and a.attnum > 0
      and not a.attisdropped
    order by 1, 2
    """
)


@pytest.fixture
async def conn():
    if not DB_URL:
        pytest.fail(
            "INTEGRATION_DATABASE_URL is unset and DATABASE_URL is not localhost — "
            "local Postgres is not configured. `python scripts/local-pg.py start` "
            "or `docker compose up -d` (roadmap §2)."
        )
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    async with engine.connect() as connection:
        yield connection
        await connection.rollback()
    await engine.dispose()


async def _float_offenders(conn, schema_oid: int) -> list[tuple[str, str, str]]:
    rows = (await conn.execute(COLUMNS_SQL, {"schema_oid": schema_oid})).all()
    return [
        (table, column, type_name)
        for table, column, type_name in rows
        if _is_money_column(column) and type_name != "numeric"
    ]


async def _public_oid(conn) -> int:
    return (await conn.execute(text("select 'public'::regnamespace::oid"))).scalar_one()


async def test_no_float_money(conn):
    """M0-T4: no money or quantity column may be real / double precision."""
    public_oid = await _public_oid(conn)
    rows = (await conn.execute(COLUMNS_SQL, {"schema_oid": public_oid})).all()
    matched = {(t, c) for t, c, _ in rows if _is_money_column(c)}
    missing = KNOWN_MONEY_COLUMNS - matched
    assert not missing, f"guard no longer sees known money columns: {sorted(missing)}"

    offenders = await _float_offenders(conn, public_oid)
    assert offenders == [], (
        "money/quantity columns must be numeric(12,2) / numeric(12,3), never float: "
        f"{offenders}"
    )


async def test_no_float_money_guard_detects_floats(conn):
    """Proves the guard would fail if someone added a float column: a probe
    table with two float columns and one numeric one, in a transaction that is
    rolled back, must be reported with exactly the two float columns."""
    await conn.execute(
        text(
            "create temp table money_guard_probe ("
            "  unit_price real,"
            "  quantity double precision,"
            "  amount numeric(12,2),"
            "  note text"
            ") on commit drop"
        )
    )
    temp_oid = (await conn.execute(text("select pg_my_temp_schema()"))).scalar_one()
    offenders = await _float_offenders(conn, temp_oid)
    assert sorted(offenders) == [
        ("money_guard_probe", "quantity", "float8"),
        ("money_guard_probe", "unit_price", "float4"),
    ]


# ── M0-T5: every business-scoped table carries tenant_isolation ──────────────

# By-design exceptions (roadmap §1.1): `businesses` IS the tenant and has no
# business_id; `login_otps` is keyed by phone and may predate any business. Both
# have RLS explicitly disabled in migration 0001. Do not extend this list without
# a roadmap-level decision.
RLS_ALLOWLIST = {"businesses", "login_otps"}

# Tables that must be covered today — guards against the scan matching nothing.
KNOWN_SCOPED_TABLES = {
    "staff", "items", "sales_legacy", "expenses", "receipts", "alerts",
    "metric_baselines", "request_logs", "pending_confirmations",
    "stock_movements", "orders", "order_lines", "payments", "item_variants",
    "modifier_groups", "modifiers", "order_line_modifiers", "uoms", "uom_conversions",
    "recipe_lines", "suppliers", "purchase_orders", "po_lines", "goods_receipts", "goods_receipt_lines",
    "accounts", "journal_entries", "journal_lines", "posting_rules",
}
# `sales` is a view since migration 0006 (M3-T2); policies cannot attach to a
# view, so its isolation rests on `security_invoker` — checked separately below
# and exercised end-to-end in test_db_integration.py.

# Plain tables and partitions only: policies do not attach to views. M3-T2, which
# turns `sales` into a view, owes the view its own explicit isolation test.
SCOPED_TABLES_SQL = text(
    """
    select c.relname as table_name,
           c.relrowsecurity as rls_enabled,
           c.relforcerowsecurity as rls_forced,
           p.polname is not null as has_policy,
           coalesce(p.polqual is not null, false) as has_using,
           coalesce(p.polwithcheck is not null, false) as has_with_check
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    left join pg_policy p on p.polrelid = c.oid and p.polname = 'tenant_isolation'
    where n.oid = :schema_oid
      and c.relkind in ('r', 'p')
      and exists (
        select 1 from pg_attribute a
        where a.attrelid = c.oid and a.attname = 'business_id' and not a.attisdropped
      )
    order by 1
    """
)


async def _rls_gaps(conn, schema_oid: int) -> dict[str, list[str]]:
    """table -> list of problems, for every business_id table not allowlisted."""
    rows = (await conn.execute(SCOPED_TABLES_SQL, {"schema_oid": schema_oid})).all()
    gaps: dict[str, list[str]] = {}
    for table, enabled, forced, has_policy, has_using, has_with_check in rows:
        if table in RLS_ALLOWLIST:
            continue
        problems = []
        if not has_policy:
            problems.append("no tenant_isolation policy")
        else:
            if not has_using:
                problems.append("policy has no USING clause")
            if not has_with_check:
                problems.append("policy has no WITH CHECK clause")
        if not enabled:
            problems.append("row level security not enabled")
        if not forced:
            problems.append("row level security not forced (table owner would bypass it)")
        if problems:
            gaps[table] = problems
    return gaps


async def test_all_scoped_tables_have_rls(conn):
    """M0-T5: every table with a business_id column has the full
    tenant_isolation template applied: policy with USING + WITH CHECK, RLS
    enabled, RLS forced."""
    public_oid = await _public_oid(conn)
    rows = (await conn.execute(SCOPED_TABLES_SQL, {"schema_oid": public_oid})).all()
    scanned = {row[0] for row in rows}
    missing = KNOWN_SCOPED_TABLES - scanned
    assert not missing, f"scan no longer sees known scoped tables: {sorted(missing)}"

    gaps = await _rls_gaps(conn, public_oid)
    assert gaps == {}, f"business-scoped tables without full tenant isolation: {gaps}"


async def test_rls_guard_detects_policyless_table(conn):
    """Proves the guard fails on a policy-less scoped table, and goes green once
    the exact template from migration 0001 is applied. Temp table, rolled back."""
    await conn.execute(
        text("create temp table rls_guard_probe (id int, business_id uuid not null) on commit drop")
    )
    temp_oid = (await conn.execute(text("select pg_my_temp_schema()"))).scalar_one()
    assert await _rls_gaps(conn, temp_oid) == {
        "rls_guard_probe": [
            "no tenant_isolation policy",
            "row level security not enabled",
            "row level security not forced (table owner would bypass it)",
        ]
    }

    # Half-applied template (policy but not forced) is still a gap.
    await conn.execute(text("alter table rls_guard_probe enable row level security"))
    await conn.execute(
        text(
            "create policy tenant_isolation on rls_guard_probe"
            " using (business_id = current_setting('app.current_business_id')::uuid)"
            " with check (business_id = current_setting('app.current_business_id')::uuid)"
        )
    )
    assert await _rls_gaps(conn, temp_oid) == {
        "rls_guard_probe": ["row level security not forced (table owner would bypass it)"]
    }

    await conn.execute(text("alter table rls_guard_probe force row level security"))
    assert await _rls_gaps(conn, temp_oid) == {}


async def test_scoped_views_are_security_invoker(conn):
    """M3-T2: a view over business-scoped tables must run as the caller
    (`security_invoker = true`), otherwise it executes as its owner — the
    migration role, which bypasses RLS — and leaks every tenant's rows."""
    public_oid = await _public_oid(conn)
    rows = (await conn.execute(text(
        """
        select c.relname, coalesce(array_to_string(c.reloptions, ','), '') as options
        from pg_class c
        where c.relnamespace = :schema_oid and c.relkind = 'v'
          and exists (select 1 from pg_attribute a
                      where a.attrelid = c.oid and a.attname = 'business_id' and not a.attisdropped)
        order by 1
        """
    ), {"schema_oid": public_oid})).all()
    assert "sales" in {name for name, _ in rows}, "the sales view is missing"
    unsafe = [name for name, options in rows if "security_invoker=true" not in options]
    assert unsafe == [], f"business-scoped views without security_invoker: {unsafe}"


async def test_every_item_has_exactly_one_default_variant(conn):
    """M4-T1: single-variant products keep working only if every item has its
    default variant, with prices equal to the item's. Checked per tenant."""
    business_ids = (await conn.execute(text("select id from businesses"))).scalars().all()
    problems: list[tuple] = []
    checked = 0
    for business_id in business_ids:
        await conn.rollback()
        await conn.execute(
            text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)}
        )
        rows = (await conn.execute(text(
            """
            select i.name,
                   count(v.id) filter (where v.is_default) as defaults,
                   bool_and(v.sell_price = i.sell_price and v.cost_price = i.cost_price) filter (where v.is_default) as prices_match
            from items i left join item_variants v on v.item_id = i.id
            group by i.id, i.name
            """
        ))).all()
        checked += len(rows)
        problems += [(str(business_id)[:8], name, defaults, ok) for name, defaults, ok in rows if defaults != 1 or ok is not True]
    await conn.rollback()
    assert checked > 0, "no items — run `python -m app.seed` first"
    assert problems == [], f"items without exactly one price-matching default variant: {problems}"


# ── M2-T3: the stock ledger reconciles with the cached projection ─────────────
#
# For every item, SUM(stock_movements.qty_delta) == items.current_stock. This is
# the early-warning system for everything downstream (roadmap M2-T3): a failure
# here means some path changed stock without writing its ledger row, or wrote a
# row without changing stock. A failure is a STOP CONDITION (roadmap §3).
#
# Runs as app_role, iterating tenants and pinning each in turn — exactly how a
# nightly job would do it — so it needs no elevated role and cannot be fooled by
# RLS hiding rows: with no tenant pinned the query fails closed rather than
# returning an empty, trivially-reconciled set.

RECONCILE_SQL = text(
    """
    select i.id, i.name, i.current_stock,
           coalesce(sum(m.qty_delta), 0) as ledger
    from items i
    left join stock_movements m on m.item_id = i.id
    group by i.id, i.name, i.current_stock
    having i.current_stock <> coalesce(sum(m.qty_delta), 0)
    order by i.name
    """
)


async def _set_tenant(conn, business_id) -> None:
    await conn.execute(
        text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)}
    )


async def _reconciliation_gaps(conn) -> dict[str, list[tuple[str, str, str]]]:
    """business_id -> [(item name, current_stock, ledger sum)] for every item
    whose cached stock differs from its ledger. Each tenant in its own
    transaction so SET LOCAL scoping is exact."""
    business_ids = (await conn.execute(text("select id from businesses order by created_at"))).scalars().all()
    gaps: dict[str, list[tuple[str, str, str]]] = {}
    for business_id in business_ids:
        await conn.rollback()  # fresh transaction → fresh SET LOCAL
        await _set_tenant(conn, business_id)
        rows = (await conn.execute(RECONCILE_SQL)).all()
        if rows:
            gaps[str(business_id)] = [(name, str(stock), str(ledger)) for _, name, stock, ledger in rows]
    await conn.rollback()
    return gaps


async def test_stock_ledger_reconciles_with_current_stock(conn):
    """M2-T3: after seeding (and whatever the other tests left behind), every
    item in every business has SUM(qty_delta) == current_stock."""
    n_items = 0
    for business_id in (await conn.execute(text("select id from businesses"))).scalars().all():
        await conn.rollback()
        await _set_tenant(conn, business_id)
        n_items += (await conn.execute(text("select count(*) from items"))).scalar_one()
    assert n_items > 0, "no items in any business — run `python -m app.seed` first"

    gaps = await _reconciliation_gaps(conn)
    assert gaps == {}, f"items whose cached stock differs from the ledger: {gaps}"


async def test_stock_ledger_reconciles_after_a_simulated_day(conn):
    """M2-T3: a day of sales (including one rejected for insufficient stock), an
    owner correction, and a void all leave the ledger and the cache equal."""
    import uuid
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.security import hash_pin
    from app.models import Business, Item, Staff, StockMovement
    from app.services.sales import InsufficientStock, record_sale
    from app.services.stock import add_stock, open_item_stock, set_absolute_stock

    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    business_id = None
    try:
        async with factory() as s:
            biz = Business(name="Reconcile Day", owner_phone=f"62997{uuid.uuid4().hex[:9]}")
            s.add(biz)
            await s.commit()
            business_id = biz.id

        async with factory() as s:
            await _set_tenant(s, business_id)
            from tests.conftest import seed_books
            await seed_books(s, business_id)
            staff = Staff(business_id=business_id, name="Kasir", pin_hash=hash_pin("2222"))
            s.add(staff)
            await s.flush()
            kopi = Item(business_id=business_id, name="Kopi", unit="cup", current_stock=Decimal(10),
                        cost_price=Decimal(7000), sell_price=Decimal(20000))
            roti = Item(business_id=business_id, name="Roti", unit="pcs", current_stock=Decimal(2),
                        cost_price=Decimal(9000), sell_price=Decimal(24000))
            s.add_all([kopi, roti])
            await s.flush()
            await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
            await open_item_stock(s, roti, unit_cost=roti.cost_price)
            await s.commit()
            staff_id, kopi_id, roti_id = staff.id, kopi.id, roti.id

        # A day of sales: 6 coffees, 2 breads, then a third bread that must be rejected.
        sale_ids = []
        for item_id, qty in [(kopi_id, 2), (kopi_id, 1), (roti_id, 1), (kopi_id, 3), (roti_id, 1)]:
            async with factory() as s:
                await _set_tenant(s, business_id)
                rec = await record_sale(s, business_id=business_id, staff_id=staff_id,
                                        item_id=item_id, quantity=Decimal(qty))
                await s.commit()
                sale_ids.append((rec.line.id, item_id, Decimal(qty)))
        async with factory() as s:
            await _set_tenant(s, business_id)
            with pytest.raises(InsufficientStock):
                await record_sale(s, business_id=business_id, staff_id=staff_id,
                                  item_id=roti_id, quantity=Decimal(1))
            await s.rollback()

        # Owner correction: the count says 5 coffees, not 4.
        async with factory() as s:
            await _set_tenant(s, business_id)
            item = await s.get(Item, kopi_id)
            await set_absolute_stock(s, item, Decimal(5), reason="correction", source_type="whatsapp")
            await s.commit()

        # Void the 3-coffee sale: a reversing row, nothing deleted (M3-T4 builds the
        # endpoint; the ledger shape is fixed here).
        void_sale_id, void_item, void_qty = sale_ids[3]
        async with factory() as s:
            await _set_tenant(s, business_id)
            item = await s.get(Item, void_item)
            await add_stock(s, item, void_qty, reason="sale_void", source_type="sale",
                            source_id=void_sale_id, unit_cost=item.cost_price)
            await s.commit()

        async with factory() as s:
            await _set_tenant(s, business_id)
            kopi = await s.get(Item, kopi_id)
            roti = await s.get(Item, roti_id)
            assert kopi.current_stock == Decimal("8.000")   # 10 −2 −1 −3 → corrected to 5 → +3 void
            assert roti.current_stock == Decimal("0.000")   # 2 −1 −1, third rejected
            n_rows = (await s.execute(select(func.count(StockMovement.id)))).scalar_one()
            assert n_rows == 2 + 5 + 1 + 1  # openings, sales, correction, void; no row for the rejection

        gaps = await _reconciliation_gaps(conn)
        assert str(business_id) not in gaps, gaps.get(str(business_id))
        assert gaps == {}, gaps
    finally:
        if business_id is not None:
            async with factory() as s:
                row = await s.get(Business, business_id)
                if row:
                    await s.delete(row)
                await s.commit()
        await engine.dispose()


BALANCE_SHEET_SQL = text(
    """
    select coalesce(sum(case when a.type = 'asset'     then l.debit - l.credit else 0 end), 0) as assets,
           coalesce(sum(case when a.type = 'liability' then l.credit - l.debit else 0 end), 0) as liabilities,
           coalesce(sum(case when a.type = 'equity'    then l.credit - l.debit else 0 end), 0) as equity,
           coalesce(sum(case when a.type = 'revenue'   then l.credit - l.debit else 0 end), 0)
         - coalesce(sum(case when a.type = 'expense'   then l.debit - l.credit else 0 end), 0) as earnings,
           count(*) as n_lines
    from journal_lines l
    join accounts a on a.id = l.account_id
    """
)


async def test_balance_sheet_balances(conn):
    """M6-T5: in every business, assets = liabilities + equity + current earnings —
    read straight off the journal and through the statement service. A posted
    day of this test's own goes in first, so the check is never vacuous (the
    seed writes history without journal entries)."""
    import uuid
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.security import hash_pin
    from app.models import Business, Item, Staff
    from app.services.orders import OrderLineSpec, PaymentSpec, create_order, refund_order
    from app.services.receiving import GrLineSpec, receive_goods
    from app.services.statements import balance_sheet
    from app.services.stock import open_item_stock, set_absolute_stock
    from app.services.units import consume_stock

    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    business_id = None
    try:
        async with factory() as s:
            biz = Business(name="Neraca Day", owner_phone=f"62996{uuid.uuid4().hex[:9]}")
            s.add(biz)
            await s.commit()
            business_id = biz.id
        async with factory() as s:
            await _set_tenant(s, business_id)
            from tests.conftest import seed_books
            await seed_books(s, business_id)
            owner = Staff(business_id=business_id, name="Owner", role="owner", pin_hash=hash_pin("1234"))
            kopi = Item(business_id=business_id, name="Kopi", unit="cup", current_stock=Decimal(10),
                        cost_price=Decimal(7000), sell_price=Decimal(20000))
            s.add_all([owner, kopi])
            await s.flush()
            await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
            created = await create_order(s, business_id=business_id, staff_id=owner.id,
                                         lines=[OrderLineSpec(item_id=kopi.id, quantity=Decimal(3))],
                                         payments=[PaymentSpec(method="cash", amount=Decimal(40000)),
                                                   PaymentSpec(method="qris", amount=Decimal(20000))])
            await receive_goods(s, business_id, lines=[GrLineSpec(item_id=kopi.id, quantity=Decimal(5), unit_cost=Decimal(7500))])
            await consume_stock(s, kopi, Decimal(1), reason="waste", source_type="test")
            await set_absolute_stock(s, kopi, Decimal(9), reason="opname", source_type="test")
            await refund_order(s, business_id=business_id, order_id=created.order.id, staff_id=owner.id,
                               manager_pin="1234", note="test", restock=True)
            await s.commit()

        n_lines_total = 0
        for bid in (await conn.execute(text("select id from businesses order by created_at"))).scalars().all():
            await conn.rollback()
            await _set_tenant(conn, bid)
            assets, liabilities, equity, earnings, n_lines = (await conn.execute(BALANCE_SHEET_SQL)).one()
            n_lines_total += n_lines
            assert Decimal(assets) == Decimal(liabilities) + Decimal(equity) + Decimal(earnings), (
                f"business {bid}: assets {assets} != liabilities {liabilities} + equity {equity} + earnings {earnings}"
            )
            async with factory() as s:
                await _set_tenant(s, bid)
                sheet = await balance_sheet(s)
            assert sheet.balances, f"business {bid}: statement service says the sheet does not balance"
            assert sheet.assets_total == Decimal(assets) and sheet.current_earnings == Decimal(earnings)
        await conn.rollback()
        assert n_lines_total > 0, "no journal lines anywhere — the check ran vacuously"
    finally:
        if business_id is not None:
            async with factory() as s:
                row = await s.get(Business, business_id)
                if row:
                    await s.delete(row)
                await s.commit()
        await engine.dispose()


async def test_every_business_has_every_standard_posting_rule(conn):
    """M7-T2: a rule added to services/posting_rules.STANDARD_RULES must reach
    businesses that already exist (a backfill migration, as 0016 and 0018 do),
    or the posting engine will refuse their events. Checked per tenant."""
    from app.services.posting_rules import STANDARD_RULES

    expected = {(e, c) for e, c, *_ in STANDARD_RULES}
    business_ids = (await conn.execute(text("select id from businesses"))).scalars().all()
    assert business_ids, "no businesses — run `python -m app.seed` first"
    missing: dict[str, list[tuple[str, str]]] = {}
    for business_id in business_ids:
        await conn.rollback()
        await _set_tenant(conn, business_id)
        have = set((await conn.execute(text("select event_type, component from posting_rules"))).all())
        gap = sorted(expected - have)
        if gap:
            missing[str(business_id)] = gap
    await conn.rollback()
    assert missing == {}, f"businesses missing standard posting rules: {missing}"


# ── M7-T3: every closed shift's variance is on the books ─────────────────────
#
# A counted-short till is money that left without a sale. If the shift row
# records it but the ledger does not, the books quietly drift from the drawer
# and every statement downstream is wrong by that amount. So: for every closed
# shift with a non-zero variance there is exactly one ShiftClosed entry naming
# it as its source, for |variance|, and a shift that counted exactly posts
# nothing. Iterates tenants as app_role, like the reconciliation checks above.

SHIFT_VARIANCE_SQL = text(
    """
    select s.id,
           s.variance,
           coalesce(sum(l.debit + l.credit) filter (where l.id is not null), 0) / 2 as posted,
           count(distinct e.id) as entries
    from shifts s
    left join journal_entries e
           on e.source_type = 'shift' and e.source_id = s.id and e.event_type = 'ShiftClosed'
    left join journal_lines l on l.entry_id = e.id
    where s.status = 'closed'
    group by s.id, s.variance
    """
)


async def test_closed_shift_variance_is_posted(conn):
    """M7-T3: shift rows and the ledger agree about the drawer, in every business."""
    from decimal import Decimal

    business_ids = (await conn.execute(text("select id from businesses"))).scalars().all()
    assert business_ids, "no businesses — run `python -m app.seed` first"
    problems: dict[str, list[str]] = {}
    n_closed = n_with_variance = 0
    for business_id in business_ids:
        await conn.rollback()
        await _set_tenant(conn, business_id)
        for shift_id, variance, posted, entries in (await conn.execute(SHIFT_VARIANCE_SQL)).all():
            n_closed += 1
            variance, posted = Decimal(variance or 0), Decimal(posted or 0)
            if variance == 0:
                if entries:
                    problems.setdefault(str(business_id), []).append(f"{shift_id}: counted exact but posted {entries} entries")
                continue
            n_with_variance += 1
            if entries != 1 or posted != abs(variance):
                problems.setdefault(str(business_id), []).append(
                    f"{shift_id}: variance {variance} but {entries} entries totalling {posted}"
                )
    await conn.rollback()
    assert problems == {}, f"closed shifts whose variance is not on the books: {problems}"
    assert n_closed > 0, "no closed shifts anywhere — the check ran vacuously"
    assert n_with_variance > 0, "no closed shift had a variance — the check proved nothing"



# ── M8-T2: the points ledger reconciles with the cached balance ──────────────
#
# Same shape as the stock check: for every customer, SUM(points_movements.
# points_delta) == customers.points_balance. A failure means some path moved
# points without writing its row, or wrote a row without moving the cache.

POINTS_RECONCILE_SQL = text(
    """
    select c.id, c.name, c.points_balance, coalesce(sum(m.points_delta), 0) as ledger
    from customers c
    left join points_movements m on m.customer_id = c.id
    group by c.id, c.name, c.points_balance
    having c.points_balance <> coalesce(sum(m.points_delta), 0)
    order by c.name
    """
)


async def test_points_ledger_reconciles_with_cached_balance(conn):
    """M8-T2: after seeding (and whatever the other tests left behind), every
    customer in every business has SUM(points_delta) == points_balance."""
    n_customers = n_with_points = 0
    gaps: dict[str, list[tuple[str, str, str]]] = {}
    for business_id in (await conn.execute(text("select id from businesses order by created_at"))).scalars().all():
        await conn.rollback()
        await _set_tenant(conn, business_id)
        n_customers += (await conn.execute(text("select count(*) from customers"))).scalar_one()
        n_with_points += (await conn.execute(text("select count(*) from customers where points_balance <> 0"))).scalar_one()
        rows = (await conn.execute(POINTS_RECONCILE_SQL)).all()
        if rows:
            gaps[str(business_id)] = [(name, str(bal), str(ledger)) for _, name, bal, ledger in rows]
    await conn.rollback()
    assert n_customers > 0, "no customers in any business — run `python -m app.seed` first"
    assert n_with_points > 0, "no customer has any points — the check proved nothing"
    assert gaps == {}, f"customers whose cached balance differs from the ledger: {gaps}"
