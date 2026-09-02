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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")

# Column names that carry rupiah or quantities. Anything matching must be exact
# `numeric`, never `real` / `double precision` (roadmap §1.3).
MONEY_OR_QUANTITY = re.compile(r"amount|price|total|cost|stock|quantity|threshold")

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
        if MONEY_OR_QUANTITY.search(column) and type_name != "numeric"
    ]


async def _public_oid(conn) -> int:
    return (await conn.execute(text("select 'public'::regnamespace::oid"))).scalar_one()


async def test_no_float_money(conn):
    """M0-T4: no money or quantity column may be real / double precision."""
    public_oid = await _public_oid(conn)
    rows = (await conn.execute(COLUMNS_SQL, {"schema_oid": public_oid})).all()
    matched = {(t, c) for t, c, _ in rows if MONEY_OR_QUANTITY.search(c)}
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
