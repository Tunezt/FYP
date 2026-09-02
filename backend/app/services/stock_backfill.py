"""Backfill of `stock_movements` for data that predates the ledger (roadmap M2-T4).

Shared by migration 0004 and by the test that proves it, so both run the exact
same statements. Every statement is scoped to `backfill_items`: items that have
**no ledger row at all** when the backfill starts. Items that already have history
(anything written through services/stock.py since M2-T2) are never touched, which
makes the backfill idempotent and safe to re-run.

What is reconstructed, and from what:

  1. sales            → one `sale` row per historical sale (−quantity, staff, sold_at).
                        unit_cost is NULL: the cost at the time is unknown, and a guess
                        would silently corrupt margin history (roadmap M2-T4).
  2. receipt photos   → one `purchase` row per parsed line whose name matches an
                        item of the same business (+quantity, receipt time). unit_cost
                        is the written unit price, NULL when the photo showed none.
                        Stock-book photos (`document_type = stock_ledger`) are absolute
                        counts whose prior state is unknown — not reconstructible,
                        skipped.
  3. opening balance  → one `opname` row per item carrying whatever remains so that
                        SUM(qty_delta) == items.current_stock, dated at the item's
                        creation. This is the item's first known count.

Backfilled rows carry `source_type` values prefixed `backfill_` so the migration's
downgrade can remove exactly them and nothing written live.
"""

BACKFILL_STATEMENTS: list[str] = [
    # Items with no ledger history at all — the only ones we may touch.
    """
    create temp table backfill_items on commit drop as
    select i.id, i.business_id, i.current_stock, i.created_at
    from items i
    where not exists (select 1 from stock_movements m where m.item_id = i.id)
    """,
    # 1. Historical sales.
    """
    insert into stock_movements
      (business_id, item_id, qty_delta, reason, source_type, source_id, unit_cost, staff_id, created_at)
    select s.business_id, s.item_id, -s.quantity, 'sale', 'backfill_sale', s.id, null, s.staff_id, s.sold_at
    from sales s
    join backfill_items b on b.id = s.item_id
    """,
    # 2. Purchases from receipt photos (not stock-book counts).
    """
    insert into stock_movements
      (business_id, item_id, qty_delta, reason, source_type, source_id, unit_cost, staff_id, created_at)
    select r.business_id, b.id,
           (elem->>'quantity')::numeric(12,3), 'purchase', 'backfill_receipt', r.id,
           case when (elem->>'unit_price') ~ '^[0-9]+(\\.[0-9]+)?$' and (elem->>'unit_price')::numeric > 0
                then (elem->>'unit_price')::numeric(12,2) end,
           null, r.created_at
    from receipts r
    cross join lateral jsonb_array_elements(coalesce(r.parsed_data->'items', '[]'::jsonb)) elem
    join items i on i.business_id = r.business_id
                and lower(i.name) = lower(trim(elem->>'name'))
    join backfill_items b on b.id = i.id
    where coalesce(r.parsed_data->>'document_type', 'receipt') = 'receipt'
      and (elem->>'quantity') ~ '^[0-9]+(\\.[0-9]+)?$'
      and (elem->>'quantity')::numeric > 0
    """,
    # 3. Opening balance: whatever is left so the ledger meets the cached figure.
    """
    insert into stock_movements
      (business_id, item_id, qty_delta, reason, source_type, source_id, unit_cost, staff_id, created_at)
    select b.business_id, b.id,
           b.current_stock - coalesce(sum(m.qty_delta), 0), 'opname', 'backfill_opening', null, null, null,
           b.created_at
    from backfill_items b
    left join stock_movements m on m.item_id = b.id
    group by b.id, b.business_id, b.current_stock, b.created_at
    having b.current_stock - coalesce(sum(m.qty_delta), 0) <> 0
    """,
]

ROLLBACK_STATEMENTS: list[str] = [
    """
    delete from stock_movements
    where source_type in ('backfill_sale', 'backfill_receipt', 'backfill_opening')
    """,
]
