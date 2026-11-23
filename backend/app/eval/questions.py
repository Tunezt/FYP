"""The evaluation question set (roadmap M9-T6).

Real owner messages: code-switched Indonesian, Malay and English, with the
abbreviations and typos people actually type on a phone ("brp", "sy", "yg",
"kat", "tak", "hw much"). Every question names what a correct system should do:

  kind        "data"  — answerable from the business data; `intent` and `args`
                        are what the fixed tool set should be called with, and
                        the ground-truth figure is computed from the metric
                        registry with those args at run time
              "oos"   — out of scope; the only correct behaviour is a refusal
                        (or a clarification with no figure in it)
  figure      which key in the tool's facts carries the figure to check

The set is data, not code: adding a question is adding a row.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Question:
    text: str
    lang: str                      # id | ms | en | mixed
    kind: str                      # data | oos
    intent: str | None = None      # expected tool for `data`
    args: dict = field(default_factory=dict)
    figure: str | None = None      # key in the tool facts that carries the figure
    metric: str | None = None      # registry metric that defines the truth
    metric_kw: dict = field(default_factory=dict)
    note: str = ""


QUESTIONS: list[Question] = [
    # ── sales ───────────────────────────────────────────────────────────────
    Question("berapa penjualan hari ini?", "id", "data", "get_sales_summary", {"period": "today"}, "revenue", "revenue", {"period": "today"}),
    Question("jualan hari ni brp bos", "mixed", "data", "get_sales_summary", {"period": "today"}, "revenue", "revenue", {"period": "today"}, "id/ms mix, abbreviation"),
    Question("omzet kemarin brp ya", "id", "data", "get_sales_summary", {"period": "yesterday"}, "revenue", "revenue", {"period": "yesterday"}),
    Question("hw much did we sell this week", "en", "data", "get_sales_summary", {"period": "this_week"}, "revenue", "revenue", {"period": "this_week"}, "typo"),
    Question("penjualan minggu ini vs minggu lalu gmn", "id", "data", "compare_periods", {"period_a": "this_week", "period_b": "last_week"}, "period_a.revenue", "revenue", {"period": "this_week"}),
    Question("bulan ni jualan berapa banyak?", "ms", "data", "get_sales_summary", {"period": "this_month"}, "revenue", "revenue", {"period": "this_month"}),
    Question("total sales last 30 days pls", "en", "data", "get_sales_summary", {"period": "last_30_days"}, "revenue", "revenue", {"period": "last_30_days"}),
    # ── profit ──────────────────────────────────────────────────────────────
    Question("untung ga bulan ini?", "id", "data", "get_profit", {"period": "this_month"}, "revenue", "revenue", {"period": "this_month"}, "profit tool; revenue is the checked figure"),
    Question("am i making money this month", "en", "data", "get_profit", {"period": "this_month"}, "recorded_expenses", "expense_total", {"period": "this_month"}),
    Question("pengeluaran bulan ini total brp", "id", "data", "get_profit", {"period": "this_month"}, "recorded_expenses", "expense_total", {"period": "this_month"}),
    # ── stock ───────────────────────────────────────────────────────────────
    Question("stok kopi arabica tinggal brp", "id", "data", "get_stock", {"item_name": "arabica"}, "items.*.current_stock", "stock_on_hand", {"item": "Kopi Arabica"}),
    Question("baki stok gula aren berapa", "ms", "data", "get_stock", {"item_name": "gula aren"}, "items.*.current_stock", "stock_on_hand", {"item": "Gula Aren"}),
    Question("how many croissant left?", "en", "data", "get_stock", {"item_name": "croissant"}, "items.*.current_stock", "stock_on_hand", {"item": "Croissant"}),
    Question("stok apa aja yg mau habis", "id", "data", "get_low_stock", {}, None, None, {}, "list answer: no single figure"),
    Question("mana yg perlu restock", "id", "data", "get_low_stock", {}, None, None, {}),
    # ── suppliers, recipes ──────────────────────────────────────────────────
    Question("harga beli terakhir susu uht brp", "id", "data", "get_supplier_prices", {"item_name": "susu"}, None, None, {}, "list answer"),
    Question("modal es kopi susu per gelas berapa", "id", "data", "get_recipe_cost", {"item_name": "es kopi susu"}, "cost_per_unit", "recipe_cost", {"item": "Es Kopi Susu"}),
    # ── till, customers, promos ─────────────────────────────────────────────
    Question("kas kemarin ada selisih ga", "id", "data", "get_shift_summary", {"period": "yesterday"}, "total_variance", "cash_variance", {"period": "yesterday"}),
    Question("andi udah belanja brp bulan ini", "id", "data", "get_customer_summary", {"customer": "andi", "period": "this_month"}, "customers.0.period_spend", None, {}, "truth from the customer_summary metric"),
    Question("promo bogo laku ga bulan ini", "id", "data", "get_promo_performance", {"period": "this_month"}, None, None, {}, "list answer"),
    # ── out of scope ────────────────────────────────────────────────────────
    Question("penjualan bulan depan kira2 brp?", "id", "oos", note="forecast"),
    Question("besok hujan ga? mau stok es", "id", "oos", note="weather"),
    Question("pajak umkm sy tahun ini brp yg hrs dibayar", "id", "oos", note="tax law"),
    Question("harga kopi di warung sebelah brp", "id", "oos", note="competitor"),
    Question("budi absen brp kali bulan ini", "id", "oos", note="attendance — deliberately out of scope"),
    Question("berapa banyak jualan bulan depan agaknya", "ms", "oos", note="forecast"),
    Question("what will my profit be next year", "en", "oos", note="forecast"),
    Question("should i raise my prices?", "en", "oos", note="opinion"),
    Question("berapa gaji kasir yg wajar di jakarta", "id", "oos", note="outside knowledge"),
    Question("kurs dollar hari ini brp", "id", "oos", note="outside data"),
]

DATA_QUESTIONS = [q for q in QUESTIONS if q.kind == "data"]
OOS_QUESTIONS = [q for q in QUESTIONS if q.kind == "oos"]


# -- M9-T7: the RAG set (search_history over receipt history) ----------------
#
# Ten receipts are inserted for the run (and rolled back after it); each
# question names which of them contain the answer. Retrieval precision is then
# measured against that, not against opinion. Twelve questions have no answer on
# purpose: the right behaviour is to return nothing.


@dataclass(frozen=True)
class RagReceipt:
    key: str
    supplier: str
    day: str                                       # YYYY-MM-DD
    items: tuple[tuple[str, str, int, int], ...]   # name, unit, quantity, line total (Rp)


@dataclass(frozen=True)
class RagQuestion:
    text: str
    lang: str
    relevant: frozenset[str]                       # receipt keys that answer it; empty = no answer exists


RAG_RECEIPTS: list[RagReceipt] = [
    RagReceipt("r1", "Toko Sinar Jaya", "2026-10-02", (("gula pasir", "kg", 10, 140000), ("susu UHT", "liter", 24, 456000))),
    RagReceipt("r2", "CV Kopi Nusantara", "2026-10-05", (("kopi arabica", "kg", 5, 750000), ("kopi robusta", "kg", 5, 400000))),
    RagReceipt("r3", "UD Maju Makmur", "2026-10-09", (("tepung terigu", "kg", 25, 300000), ("telur", "kg", 10, 280000))),
    RagReceipt("r4", "Toko Sinar Jaya", "2026-10-16", (("gula aren", "kg", 5, 125000), ("susu UHT", "liter", 24, 456000))),
    RagReceipt("r5", "PT Dairy Fresh", "2026-10-20", (("susu segar", "liter", 30, 540000), ("krimer", "kg", 5, 200000))),
    RagReceipt("r6", "CV Kopi Nusantara", "2026-10-26", (("kopi arabica", "kg", 5, 765000),)),
    RagReceipt("r7", "Pasar Induk", "2026-10-28", (("selada", "kg", 3, 60000), ("tomat", "kg", 4, 80000))),
    RagReceipt("r8", "UD Maju Makmur", "2026-11-03", (("telur", "kg", 10, 290000), ("mentega", "kg", 5, 250000))),
    RagReceipt("r9", "Toko Kemasan Prima", "2026-11-08", (("gelas plastik", "pcs", 1000, 350000), ("sedotan", "pcs", 2000, 120000))),
    RagReceipt("r10", "PT Dairy Fresh", "2026-11-12", (("susu segar", "liter", 30, 540000), ("keju", "kg", 2, 180000))),
]

RAG_QUESTIONS: list[RagQuestion] = [
    RagQuestion("kapan terakhir beli susu uht", "id", frozenset({"r1", "r4"})),
    RagQuestion("pernah beli gula aren?", "id", frozenset({"r4"})),
    RagQuestion("biasanya beli arabica di mana", "id", frozenset({"r2", "r6"})),
    RagQuestion("beli telur dari supplier mana aja", "id", frozenset({"r3", "r8"})),
    RagQuestion("have I bought cheese before", "en", frozenset({"r10"})),
    RagQuestion("nota dari Dairy Fresh ada ga", "id", frozenset({"r5", "r10"})),
    RagQuestion("kapan terakhir beli gelas plastik", "id", frozenset({"r9"})),
    RagQuestion("pernah beli tepung terigu?", "id", frozenset({"r3"})),
    RagQuestion("harga beli mentega terakhir brp", "id", frozenset({"r8"})),
    RagQuestion("beli sayur di mana ya", "id", frozenset({"r7"})),
    RagQuestion("pernah beli daging sapi?", "id", frozenset()),
    RagQuestion("did i ever buy caviar", "en", frozenset()),
    # never bought / never used: the right answer is "no receipt", whatever the cut-off
    RagQuestion("pernah beli ikan salmon?", "id", frozenset()),
    RagQuestion("nota dari Toko Bangunan Jaya ada ga", "id", frozenset()),
    RagQuestion("did we ever buy printer paper", "en", frozenset()),
    RagQuestion("kapan terakhir beli beras", "id", frozenset()),
    RagQuestion("beli minyak goreng di mana", "id", frozenset()),
    RagQuestion("pernah order dari PT Samudra Frozen?", "id", frozenset()),
    RagQuestion("have I bought vanilla syrup before", "en", frozenset()),
    RagQuestion("harga beli ayam terakhir brp", "id", frozenset()),
    RagQuestion("nota dari CV Sumber Rejeki ada ga", "id", frozenset()),
    RagQuestion("pernah beli teh celup?", "id", frozenset()),
]
