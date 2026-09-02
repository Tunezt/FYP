# Vision path results

Evidence log for the receipt / stock-book photo path (roadmap M1). Every run is appended by
`scripts/vision-baseline.py`, which calls `app.ai.vision.parse_business_document` exactly as
the WhatsApp image handler does and records the model's **verbatim** output before any
parsing, so before/after comparisons are honest. Nothing here is edited by hand after a run.

Sample provenance: the three images in `docs/vision-test-samples/` are **AI-generated
photo-realistic stand-ins** (Phase 14, July 2026), not photographs of real documents. They
are labelled as such wherever quoted. M1-T2 adds real and generated images per category.

## Ground truth

Read from the images by eye before the baseline run (2026-09-03).

### `test-receipt-1-neat.png` — handwritten stock book, clean, flat, even light

Header "BUKU STOK WARUNG", date 25/05/2025. Columns: Nama Barang · Satuan · Jumlah ·
Harga Satuan · Total. Line 8 has a crossed-out "Kecap manis" and a rewritten one below.

| # | name | unit | qty | unit price | line total |
|---|---|---|---|---|---|
| 1 | Minyak goreng 2L | botol | 3 | 38.000 | 114.000 |
| 2 | Gula pasir 1kg | kg | 5 | 15.000 | 75.000 |
| 3 | Kopi Sachet | dus | 2 | 88.000 | 176.000 |
| 4 | Telur | kg | 1 | 27.000 | 27.000 |
| 5 | Tepung terigu 1kg | kg | 2 | 12.500 | 25.000 |
| 6 | Susu kental manis | kaleng | 3 | 11.000 | 33.000 |
| 7 | Mie instan | bungkus | 10 | 3.000 | 30.000 |
| 8 | Kecap manis | botol | 2 | 10.000 | 20.000 |
| 9 | Sabun cuci piring | pouch | 3 | 6.500 | 19.500 |
| 10 | Rokok Surya 16 | bungkus | 4 | 29.000 | 116.000 |

Total **645.500**. Expected: `stock_ledger`, 10 items, every field populated, no genuine
ambiguity (the strike-through is the only trap), confidence `high`, gate not triggered.

### `test-receipt-2-messy.png` — torn nota, coffee stain, cramped handwriting

Header "TGL 23/5", "NOTA", "Tuan Tn. Nur". Quantity and name run together; only a line
total per row; no unit prices. Genuine ambiguities: "2 RB beras" (RB could be a unit or
"ribu"), "¼ gula psr" (a fraction), "Kecap BH." (abbreviation), "Saos tiram" is legible.

| # | name (as written) | qty | line total |
|---|---|---|---|
| 1 | RB beras | 2 | 28.000 |
| 2 | gula psr | ¼ | 4.500 |
| 3 | teh celup | 1 | 11.000 |
| 4 | indomie | 2 | 6.500 |
| 5 | minyak goring | 1 | 14.000 |
| 6 | kecap BH. | 1 | 17.000 |
| 7 | saos tiram | 1 | 6.500 |

Total **87.500**, Bayar 100.000, Kembali 12.500 (payment lines, not items). Expected:
`receipt`, 7 items, confidence `medium` or `low` with the RB / ¼ / BH readings listed as
ambiguities, **gate triggered**. A confident silent write here would be the failure mode
the gate exists for.

### `test-receipt-3-medium.png` — notepad, strong glare band, angled

Header "Catatan warung", "tgl 26/5/25 senin", "Belanja stok :". Quantity then a single
price column (line totals; no unit prices shown). Right half of the page is over-exposed.

| # | name | qty | unit (if written) | line total |
|---|---|---|---|---|
| 1 | Beras medium 10kg | 1 | — | 140.000 |
| 2 | Minyak goreng 2L | 2 | — | 62.000 |
| 3 | Gula pasir 1kg | 2 | — | 28.000 |
| 4 | Tepung terigu | 1 | — | 12.000 |
| 5 | Telur ayam | 1 | tray | 27.000 |
| 6 | Mie instan | 10 | — | 15.000 |
| 7 | Kopi sachet | 20 | — | 18.000 |
| 8 | Teh celup | 1 | — | 6.000 |
| 9 | Susu kental manis | 2 | — | 13.000 |
| 10 | Gas 3kg | 1 | — | 22.000 |

Total **343.000**. Expected: `stock_ledger` or `receipt`, 10 items, `unit_price` 0 on every
line (not shown — the schema says 0, never a guess), `line_total` populated, units mostly
absent so the model must either leave them empty or infer (pcs / kg) — inference is a
soft error worth recording. Confidence `medium` is reasonable given the glare.

## Runs

### M1-T1 baseline (untuned prompt) — 2026-09-02 18:20 UTC

- vision model: `gemini-2.5-flash` (setting `GEMINI_PRO_MODEL`)
- prompt/schema: `backend/app/ai/vision.py` at `git rev-parse --short HEAD` = `7d06bdb`
- gate rule: `app.services.receipts.needs_confirmation` — confidence != high, or any ambiguity, or no items
- images: 3

#### `test-receipt-1-neat.png`

- size 2304 KB · image/png · latency 20799 ms · tokens in/out 479/527
- document_type `stock_ledger` · confidence **high** · 10 items · total_amount 645500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**

Raw model output (verbatim):

```json
{"document_type": "stock_ledger", "supplier": "", "date": "2025-05-25", "items": [{"name": "Minyak goreng 2L", "quantity": 3, "unit": "botol", "unit_price": 38000, "line_total": 114000}, {"name": "Gula pasir 1kg", "quantity": 5, "unit": "kg", "unit_price": 15000, "line_total": 75000}, {"name": "Kopi Sachet", "quantity": 2, "unit": "dus", "unit_price": 88000, "line_total": 176000}, {"name": "Telur", "quantity": 1, "unit": "kg", "unit_price": 27000, "line_total": 27000}, {"name": "Tepung terigu 1kg", "quantity": 2, "unit": "kg", "unit_price": 12500, "line_total": 25000}, {"name": "Susu kental manis", "quantity": 3, "unit": "kaleng", "unit_price": 11000, "line_total": 33000}, {"name": "Mie instan", "quantity": 10, "unit": "bungkus", "unit_price": 3000, "line_total": 30000}, {"name": "Kecap manis", "quantity": 2, "unit": "botol", "unit_price": 10000, "line_total": 20000}, {"name": "Sabun cuci piring", "quantity": 3, "unit": "pouch", "unit_price": 6500, "line_total": 19500}, {"name": "Rokok Surya 16", "quantity": 4, "unit": "bungkus", "unit_price": 29000, "line_total": 116000}], "total_amount": 645500, "confidence": "high", "ambiguities": []}
```

Parsed structure after `parse_business_document` defaults:

```json
{
  "document_type": "stock_ledger",
  "supplier": "",
  "date": "2025-05-25",
  "items": [
    {
      "name": "Minyak goreng 2L",
      "quantity": 3,
      "unit": "botol",
      "unit_price": 38000,
      "line_total": 114000
    },
    {
      "name": "Gula pasir 1kg",
      "quantity": 5,
      "unit": "kg",
      "unit_price": 15000,
      "line_total": 75000
    },
    {
      "name": "Kopi Sachet",
      "quantity": 2,
      "unit": "dus",
      "unit_price": 88000,
      "line_total": 176000
    },
    {
      "name": "Telur",
      "quantity": 1,
      "unit": "kg",
      "unit_price": 27000,
      "line_total": 27000
    },
    {
      "name": "Tepung terigu 1kg",
      "quantity": 2,
      "unit": "kg",
      "unit_price": 12500,
      "line_total": 25000
    },
    {
      "name": "Susu kental manis",
      "quantity": 3,
      "unit": "kaleng",
      "unit_price": 11000,
      "line_total": 33000
    },
    {
      "name": "Mie instan",
      "quantity": 10,
      "unit": "bungkus",
      "unit_price": 3000,
      "line_total": 30000
    },
    {
      "name": "Kecap manis",
      "quantity": 2,
      "unit": "botol",
      "unit_price": 10000,
      "line_total": 20000
    },
    {
      "name": "Sabun cuci piring",
      "quantity": 3,
      "unit": "pouch",
      "unit_price": 6500,
      "line_total": 19500
    },
    {
      "name": "Rokok Surya 16",
      "quantity": 4,
      "unit": "bungkus",
      "unit_price": 29000,
      "line_total": 116000
    }
  ],
  "total_amount": 645500,
  "confidence": "high",
  "ambiguities": []
}
```

#### `test-receipt-2-messy.png`

- size 2261 KB · image/png · latency 16529 ms · tokens in/out 479/571
- document_type `receipt` · confidence **medium** · 7 items · total_amount 87500 · ambiguities 3
- confirmation gate: **TRIGGERED**

Raw model output (verbatim):

```json
{
  "document_type": "receipt",
  "supplier": "",
  "date": "",
  "items": [
    {
      "name": "beras",
      "quantity": 2,
      "unit": "kg",
      "unit_price": 0,
      "line_total": 28000
    },
    {
      "name": "gula psr",
      "quantity": 0.25,
      "unit": "kg",
      "unit_price": 0,
      "line_total": 4500
    },
    {
      "name": "teh celup",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 11000
    },
    {
      "name": "indomie",
      "quantity": 2,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 6500
    },
    {
      "name": "minyak goring",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 14000
    },
    {
      "name": "kecap BH.",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 17000
    },
    {
      "name": "Saus tiran",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 6500
    }
  ],
  "total_amount": 87500,
  "confidence": "medium",
  "ambiguities": [
    "The year for the date '23/5' is not specified.",
    "The unit 'RB' for 'beras' is interpreted as 'kg' (kilogram), but could potentially mean 'renceng' (a bundle/sachet) or another unit.",
    "Units for 'gula psr', 'teh celup', 'indomie', 'minyak goring', 'kecap BH', and 'Saus tiran' are implied (kg for sugar, pcs for others) as they are not explicitly written."
  ]
}
```

Parsed structure after `parse_business_document` defaults:

```json
{
  "document_type": "receipt",
  "supplier": "",
  "date": "",
  "items": [
    {
      "name": "beras",
      "quantity": 2,
      "unit": "kg",
      "unit_price": 0,
      "line_total": 28000
    },
    {
      "name": "gula psr",
      "quantity": 0.25,
      "unit": "kg",
      "unit_price": 0,
      "line_total": 4500
    },
    {
      "name": "teh celup",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 11000
    },
    {
      "name": "indomie",
      "quantity": 2,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 6500
    },
    {
      "name": "minyak goring",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 14000
    },
    {
      "name": "kecap BH.",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 17000
    },
    {
      "name": "Saus tiran",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 6500
    }
  ],
  "total_amount": 87500,
  "confidence": "medium",
  "ambiguities": [
    "The year for the date '23/5' is not specified.",
    "The unit 'RB' for 'beras' is interpreted as 'kg' (kilogram), but could potentially mean 'renceng' (a bundle/sachet) or another unit.",
    "Units for 'gula psr', 'teh celup', 'indomie', 'minyak goring', 'kecap BH', and 'Saus tiran' are implied (kg for sugar, pcs for others) as they are not explicitly written."
  ]
}
```

#### `test-receipt-3-medium.png`

- size 2171 KB · image/png · latency 29961 ms · tokens in/out 479/683
- document_type `receipt` · confidence **high** · 10 items · total_amount 343000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**

Raw model output (verbatim):

```json
{
  "document_type": "receipt",
  "supplier": "",
  "date": "2025-05-26",
  "items": [
    {
      "name": "Beras medium 10kg",
      "quantity": 1,
      "unit": "pack",
      "unit_price": 140000,
      "line_total": 140000
    },
    {
      "name": "Minyak goreng 2L",
      "quantity": 2,
      "unit": "bottle",
      "unit_price": 31000,
      "line_total": 62000
    },
    {
      "name": "Gula pasir 1kg",
      "quantity": 2,
      "unit": "pack",
      "unit_price": 14000,
      "line_total": 28000
    },
    {
      "name": "Tepung terigu",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 12000,
      "line_total": 12000
    },
    {
      "name": "Telur ayam",
      "quantity": 1,
      "unit": "tray",
      "unit_price": 27000,
      "line_total": 27000
    },
    {
      "name": "Mie instan",
      "quantity": 10,
      "unit": "pcs",
      "unit_price": 1500,
      "line_total": 15000
    },
    {
      "name": "Kopi sachet",
      "quantity": 20,
      "unit": "sachet",
      "unit_price": 900,
      "line_total": 18000
    },
    {
      "name": "Teh celup",
      "quantity": 1,
      "unit": "box",
      "unit_price": 6000,
      "line_total": 6000
    },
    {
      "name": "Susu kental manis",
      "quantity": 2,
      "unit": "can",
      "unit_price": 6500,
      "line_total": 13000
    },
    {
      "name": "Gas 3kg",
      "quantity": 1,
      "unit": "cylinder",
      "unit_price": 22000,
      "line_total": 22000
    }
  ],
  "total_amount": 343000,
  "confidence": "high",
  "ambiguities": []
}
```

Parsed structure after `parse_business_document` defaults:

```json
{
  "document_type": "receipt",
  "supplier": "",
  "date": "2025-05-26",
  "items": [
    {
      "name": "Beras medium 10kg",
      "quantity": 1,
      "unit": "pack",
      "unit_price": 140000,
      "line_total": 140000
    },
    {
      "name": "Minyak goreng 2L",
      "quantity": 2,
      "unit": "bottle",
      "unit_price": 31000,
      "line_total": 62000
    },
    {
      "name": "Gula pasir 1kg",
      "quantity": 2,
      "unit": "pack",
      "unit_price": 14000,
      "line_total": 28000
    },
    {
      "name": "Tepung terigu",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 12000,
      "line_total": 12000
    },
    {
      "name": "Telur ayam",
      "quantity": 1,
      "unit": "tray",
      "unit_price": 27000,
      "line_total": 27000
    },
    {
      "name": "Mie instan",
      "quantity": 10,
      "unit": "pcs",
      "unit_price": 1500,
      "line_total": 15000
    },
    {
      "name": "Kopi sachet",
      "quantity": 20,
      "unit": "sachet",
      "unit_price": 900,
      "line_total": 18000
    },
    {
      "name": "Teh celup",
      "quantity": 1,
      "unit": "box",
      "unit_price": 6000,
      "line_total": 6000
    },
    {
      "name": "Susu kental manis",
      "quantity": 2,
      "unit": "can",
      "unit_price": 6500,
      "line_total": 13000
    },
    {
      "name": "Gas 3kg",
      "quantity": 1,
      "unit": "cylinder",
      "unit_price": 22000,
      "line_total": 22000
    }
  ],
  "total_amount": 343000,
  "confidence": "high",
  "ambiguities": []
}
```

### Assessment of the M1-T1 baseline (written by hand after the run, 2026-09-03)

Scored against the ground truth above. "Field errors" counts name, quantity, unit,
unit_price and line_total cells that differ from the document; inferred values the
document does not show count as errors even when arithmetically plausible.

| image | type | items found / true | total | confidence | ambiguities | gate | field errors | verdict |
|---|---|---|---|---|---|---|---|---|
| 1 neat | stock_ledger ✓ | 10 / 10 | 645.500 ✓ | high | 0 | open (auto-commit) | 0 / 50 | **correct, and the strike-through trap was handled** |
| 2 messy | receipt ✓ | 7 / 7 | 87.500 ✓ | medium | 3 | **triggered** | units: 7 inferred (kg/pcs); names: "Saus tiran" for "saos tiram" | **safe** — every guess is disclosed in `ambiguities`, owner is asked |
| 3 glare | receipt (arguable) | 10 / 10 | 343.000 ✓ | **high** | **0** | **open (auto-commit)** | 9 of 10 `unit_price` values computed (line_total ÷ qty) although the page shows none; 9 of 10 units invented in English ("pack", "bottle", "pcs", "sachet") although the page writes only "tray" once | **unsafe** — confident silent guesses |

What the baseline says:

1. **Legible input is extracted exactly.** Image 1 is 50/50 fields with correct totals and
   the crossed-out line correctly ignored. Nothing to tune for the easy case.
2. **The gate works when the model admits doubt.** Image 2 is the designed behaviour: the
   model listed the "RB" unit, the missing year and the implied units, dropped to `medium`,
   and the parse was parked for confirmation rather than written.
3. **The gate cannot see doubt the model does not admit — and it does not admit it on
   image 3.** The system prompt says `unit_price` is "0 if not shown" and `confidence=high`
   is "a promise that nothing needs the owner's double-checking". On image 3 the model broke
   both: it divided line totals by quantities to fabricate unit prices, translated units it
   never saw, and still claimed `high` with zero ambiguities. Under the current gate rule
   this parse would have been **committed silently** with nine invented units and nine
   derived prices. This is the exact failure M1-T3's done-criterion targets ("the
   confirmation gate triggers on every low-confidence read").
4. **Not deterministic across runs.** Phase 14 (July 2026, same model name, same prompt)
   recorded image 3 with `unit_price` 0 on every line — the honest behaviour. Today's run
   fabricated them. Temperature is already 0.0; the variance is on Google's side (model
   revision behind the `gemini-2.5-flash` alias). Any tuning in M1-T3 must be measured over
   repeated runs, not one, and the gate must not rely on the model's self-reported
   confidence alone.
5. **Latency** 17–30 s per image on 2.2 MB PNGs (479 prompt tokens each, so the image is
   tokenised at a fixed cost; the wall time is the model, not upload). Worth noting for the
   WhatsApp UX (the ack message before parsing already exists) and for M1-T2's larger set.

Candidate levers for M1-T3 (recorded now so the tuning is planned, **not applied here**):
- Server-side rule: if any `unit_price > 0` while the document shows no unit-price column
  is undetectable, but `unit_price * quantity == line_total` for every line **and** the
  model reports no unit-price ambiguity is a cheap heuristic for "derived, not read".
- Server-side rule: units outside an Indonesian vocabulary (kg, gram, liter, pcs, bungkus,
  dus, botol, kaleng, sachet, renceng, tray, ikat, pack…) or in English when the document is
  Indonesian → force `medium`.
- Prompt: require `unit` to be empty when not written, and forbid computing `unit_price`.
- Prompt: ask for a per-item `read_confidence` so a single doubtful line downgrades the whole
  document — the current schema only has one document-level signal.

### M1-T2 expanded set, untuned prompt — 2026-09-02 18:27 UTC

- vision model: `gemini-2.5-flash` (setting `GEMINI_PRO_MODEL`)
- prompt/schema: `backend/app/ai/vision.py` at commit `86707d7`
- gate rule: `app.services.receipts.needs_confirmation` — confidence != high, or any ambiguity, or no items
- images: 18 (scored against manifest.json)

**Per-category summary** (sample counts stated; scoring rules in the script docstring):

| category | n | lines matched | totals exact | gate triggered | silent errors |
|---|---|---|---|---|---|
| handwritten_photo | 3 | 27/27 (100%) | 2/3 | 1/3 | **2** |
| thermal_printed | 4 | 30/30 (100%) | 4/4 | 0/4 | **1** |
| handwritten_font | 4 | 26/26 (100%) | 4/4 | 1/4 | **2** |
| blurred | 3 | 28/28 (100%) | 2/3 | 0/3 | **2** |
| angled | 3 | 23/23 (100%) | 2/3 | 1/3 | **0** |
| unreadable_control | 1 | n/a (no fabricated items) | n/a | 1/1 | **0** |
| **all** | 18 | 134/134 (100%) | 14/17 | 4/18 | **7** |

#### `test-receipt-1-neat.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size 2304 KB · image/png · latency 15048 ms · tokens in/out 479/527
- document_type `stock_ledger` · confidence **high** · 10 items · total_amount 645500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 1 · **SILENT ERROR**

Raw model output (verbatim):

```json
{"document_type": "stock_ledger", "supplier": "", "date": "2025-05-25", "items": [{"name": "Minyak goreng 2L", "quantity": 3, "unit": "botol", "unit_price": 38000, "line_total": 114000}, {"name": "Gula pasir 1kg", "quantity": 5, "unit": "kg", "unit_price": 15000, "line_total": 75000}, {"name": "Kopi Sachet", "quantity": 2, "unit": "dus", "unit_price": 88000, "line_total": 176000}, {"name": "Telur", "quantity": 1, "unit": "kg", "unit_price": 27000, "line_total": 27000}, {"name": "Tepung terigu 1kg", "quantity": 2, "unit": "kg", "unit_price": 12500, "line_total": 25000}, {"name": "Susu kental manis", "quantity": 3, "unit": "kaleng", "unit_price": 11000, "line_total": 33000}, {"name": "Mie instan", "quantity": 10, "unit": "bungkus", "unit_price": 3000, "line_total": 30000}, {"name": "Kecap manis", "quantity": 2, "unit": "botol", "unit_price": 10000, "line_total": 20000}, {"name": "Sabun cuci piring", "quantity": 3, "unit": "pouch", "unit_price": 6500, "line_total": 19500}, {"name": "Rokok Surya 16", "quantity": 4, "unit": "bungkus", "unit_price": 29000, "line_total": 116000}], "total_amount": 645500, "confidence": "high", "ambiguities": []}
```

#### `test-receipt-2-messy.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size 2261 KB · image/png · latency 39964 ms · tokens in/out 479/571
- document_type `receipt` · confidence **medium** · 7 items · total_amount 87500 · ambiguities 3
- confirmation gate: **TRIGGERED**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 · invented units 7 · field errors 7

Raw model output (verbatim):

```json
{
  "document_type": "receipt",
  "supplier": "",
  "date": "",
  "items": [
    {
      "name": "beras",
      "quantity": 2,
      "unit": "kg",
      "unit_price": 0,
      "line_total": 28000
    },
    {
      "name": "gula psr",
      "quantity": 0.25,
      "unit": "kg",
      "unit_price": 0,
      "line_total": 4500
    },
    {
      "name": "teh celup",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 11000
    },
    {
      "name": "indomie",
      "quantity": 2,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 6500
    },
    {
      "name": "minyak goring",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 14000
    },
    {
      "name": "kecap BH.",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 17000
    },
    {
      "name": "Saus tiran",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 0,
      "line_total": 6500
    }
  ],
  "total_amount": 87500,
  "confidence": "medium",
  "ambiguities": [
    "The year for the date '23/5' is not specified.",
    "The unit 'RB' for 'beras' is interpreted as 'kg' (kilogram), but could potentially mean 'renceng' (a bundle/sachet) or another unit.",
    "Units for 'gula psr', 'teh celup', 'indomie', 'minyak goring', 'kecap BH', and 'Saus tiran' are implied (kg for sugar, pcs for others) as they are not explicitly written."
  ]
}
```

#### `test-receipt-3-medium.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size 2171 KB · image/png · latency 45710 ms · tokens in/out 479/683
- document_type `receipt` · confidence **high** · 10 items · total_amount 343000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 10 · invented units 9 · field errors 19 · **SILENT ERROR**

Raw model output (verbatim):

```json
{
  "document_type": "receipt",
  "supplier": "",
  "date": "2025-05-26",
  "items": [
    {
      "name": "Beras medium 10kg",
      "quantity": 1,
      "unit": "pack",
      "unit_price": 140000,
      "line_total": 140000
    },
    {
      "name": "Minyak goreng 2L",
      "quantity": 2,
      "unit": "bottle",
      "unit_price": 31000,
      "line_total": 62000
    },
    {
      "name": "Gula pasir 1kg",
      "quantity": 2,
      "unit": "pack",
      "unit_price": 14000,
      "line_total": 28000
    },
    {
      "name": "Tepung terigu",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 12000,
      "line_total": 12000
    },
    {
      "name": "Telur ayam",
      "quantity": 1,
      "unit": "tray",
      "unit_price": 27000,
      "line_total": 27000
    },
    {
      "name": "Mie instan",
      "quantity": 10,
      "unit": "pcs",
      "unit_price": 1500,
      "line_total": 15000
    },
    {
      "name": "Kopi sachet",
      "quantity": 20,
      "unit": "sachet",
      "unit_price": 900,
      "line_total": 18000
    },
    {
      "name": "Teh celup",
      "quantity": 1,
      "unit": "box",
      "unit_price": 6000,
      "line_total": 6000
    },
    {
      "name": "Susu kental manis",
      "quantity": 2,
      "unit": "can",
      "unit_price": 6500,
      "line_total": 13000
    },
    {
      "name": "Gas 3kg",
      "quantity": 1,
      "unit": "cylinder",
      "unit_price": 22000,
      "line_total": 22000
    }
  ],
  "total_amount": 343000,
  "confidence": "high",
  "ambiguities": []
}
```

#### `thermal-1-clean.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), clean
- size 100 KB · image/jpeg · latency 16478 ms · tokens in/out 479/435
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 · invented units 0 · field errors 0

Raw model output (verbatim):

```json
{"document_type": "receipt", "supplier": "TOKO SUMBER REJEKI", "date": "2026-08-14", "items": [{"name": "Indomie Goreng", "quantity": 5, "unit": "pcs", "unit_price": 3500, "line_total": 17500}, {"name": "Aqua 600ml", "quantity": 12, "unit": "pcs", "unit_price": 3000, "line_total": 36000}, {"name": "Kopi Kapal Api", "quantity": 10, "unit": "pcs", "unit_price": 1500, "line_total": 15000}, {"name": "Gula Pasir 1kg", "quantity": 2, "unit": "pcs", "unit_price": 17000, "line_total": 34000}, {"name": "Minyak Goreng 2L", "quantity": 1, "unit": "pcs", "unit_price": 36000, "line_total": 36000}, {"name": "Telur 1kg", "quantity": 2, "unit": "pcs", "unit_price": 28000, "line_total": 56000}, {"name": "Sabun Lifebuoy", "quantity": 3, "unit": "pcs", "unit_price": 4500, "line_total": 13500}, {"name": "Rokok Sampoerna", "quantity": 2, "unit": "pcs", "unit_price": 32000, "line_total": 64000}], "total_amount": 272000, "confidence": "high", "ambiguities": []}
```

#### `thermal-2-faded.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), faded ink
- size 72 KB · image/jpeg · latency 26557 ms · tokens in/out 479/435
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 · invented units 3 · field errors 3 · **SILENT ERROR**

Raw model output (verbatim):

```json
{"document_type": "receipt", "supplier": "TOKO SUMBER REJEKI", "date": "2026-08-14", "items": [{"name": "Indomie Goreng", "quantity": 5, "unit": "pcs", "unit_price": 3500, "line_total": 17500}, {"name": "Aqua 600ml", "quantity": 12, "unit": "pcs", "unit_price": 3000, "line_total": 36000}, {"name": "Kopi Kapal Api", "quantity": 10, "unit": "pcs", "unit_price": 1500, "line_total": 15000}, {"name": "Gula Pasir 1kg", "quantity": 2, "unit": "kg", "unit_price": 17000, "line_total": 34000}, {"name": "Minyak Goreng 2L", "quantity": 1, "unit": "liter", "unit_price": 36000, "line_total": 36000}, {"name": "Telur 1kg", "quantity": 2, "unit": "kg", "unit_price": 28000, "line_total": 56000}, {"name": "Sabun Lifebuoy", "quantity": 3, "unit": "pcs", "unit_price": 4500, "line_total": 13500}, {"name": "Rokok Sampoerna", "quantity": 2, "unit": "pcs", "unit_price": 32000, "line_total": 64000}], "total_amount": 272000, "confidence": "high", "ambiguities": []}
```

#### `thermal-3-noisy-rotated.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), sensor noise + 2.5° rotation
- size 185 KB · image/jpeg · latency 21951 ms · tokens in/out 479/343
- document_type `receipt` · confidence **high** · 6 items · total_amount 237000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 · invented units 0 · field errors 0

Raw model output (verbatim):

```json
{"document_type": "receipt", "supplier": "GROSIR BAROKAH", "date": "2026-08-20", "items": [{"name": "Beras 5kg", "quantity": 1, "unit": "pcs", "unit_price": 72000, "line_total": 72000}, {"name": "Susu UHT 1L", "quantity": 4, "unit": "pcs", "unit_price": 18000, "line_total": 72000}, {"name": "Teh Celup Sariwangi", "quantity": 2, "unit": "pcs", "unit_price": 7500, "line_total": 15000}, {"name": "Kecap Bango 600ml", "quantity": 1, "unit": "pcs", "unit_price": 24000, "line_total": 24000}, {"name": "Mie Sedaap", "quantity": 10, "unit": "pcs", "unit_price": 3200, "line_total": 32000}, {"name": "Gas 3kg", "quantity": 1, "unit": "pcs", "unit_price": 22000, "line_total": 22000}], "total_amount": 237000, "confidence": "high", "ambiguities": []}
```

#### `thermal-4-lowres.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), low resolution (45%)
- size 80 KB · image/jpeg · latency 20267 ms · tokens in/out 479/575
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 · invented units 0 · field errors 0

Raw model output (verbatim):

```json
{
  "document_type": "receipt",
  "supplier": "TOKO SUMBER REJEKI",
  "date": "2026-08-14",
  "items": [
    {
      "name": "Indomie Goreng",
      "quantity": 5,
      "unit": "pcs",
      "unit_price": 3500,
      "line_total": 17500
    },
    {
      "name": "Aqua 600ml",
      "quantity": 12,
      "unit": "pcs",
      "unit_price": 3000,
      "line_total": 36000
    },
    {
      "name": "Kopi Kapal Api",
      "quantity": 10,
      "unit": "pcs",
      "unit_price": 1500,
      "line_total": 15000
    },
    {
      "name": "Gula Pasir 1kg",
      "quantity": 2,
      "unit": "pcs",
      "unit_price": 17000,
      "line_total": 34000
    },
    {
      "name": "Minyak Goreng 2L",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 36000,
      "line_total": 36000
    },
    {
      "name": "Telur 1kg",
      "quantity": 2,
      "unit": "pcs",
      "unit_price": 28000,
      "line_total": 56000
    },
    {
      "name": "Sabun Lifebuoy",
      "quantity": 3,
      "unit": "pcs",
      "unit_price": 4500,
      "line_total": 13500
    },
    {
      "name": "Rokok Sampoerna",
      "quantity": 2,
      "unit": "pcs",
      "unit_price": 32000,
      "line_total": 64000
    }
  ],
  "total_amount": 272000,
  "confidence": "high",
  "ambiguities": []
}
```

#### `hand-1-segoe-print.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Segoe Print
- size 183 KB · image/jpeg · latency 12164 ms · tokens in/out 479/413
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 · invented units 0 · field errors 0

Raw model output (verbatim):

```json
{"document_type": "receipt", "supplier": "", "date": "", "items": [{"name": "Beras", "quantity": 10, "unit": "kg", "unit_price": 14000, "line_total": 140000}, {"name": "Minyak goreng", "quantity": 3, "unit": "liter", "unit_price": 19000, "line_total": 57000}, {"name": "Gula", "quantity": 2, "unit": "kg", "unit_price": 16000, "line_total": 32000}, {"name": "Telur", "quantity": 1, "unit": "kg", "unit_price": 28000, "line_total": 28000}, {"name": "Bawang merah", "quantity": 1, "unit": "kg", "unit_price": 35000, "line_total": 35000}, {"name": "Cabai rawit", "quantity": 0.5, "unit": "kg", "unit_price": 60000, "line_total": 30000}, {"name": "Tahu", "quantity": 10, "unit": "pcs", "unit_price": 1000, "line_total": 10000}], "total_amount": 332000, "confidence": "high", "ambiguities": ["The date 'tgl 12/8/26' is ambiguous regarding the order of day/month and the century of the year (e.g., 2026-08-12 or 2012-08-26)."]}
```

#### `hand-2-ink-free.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Ink Free
- size 177 KB · image/jpeg · latency 12290 ms · tokens in/out 479/491
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 · invented units 0 · field errors 0

Raw model output (verbatim):

```json
{
  "document_type": "receipt",
  "supplier": "",
  "date": "2026-08-12",
  "items": [
    {
      "name": "Beras",
      "quantity": 10,
      "unit": "kg",
      "unit_price": 14000,
      "line_total": 140000
    },
    {
      "name": "Minyak goreng",
      "quantity": 3,
      "unit": "liter",
      "unit_price": 19000,
      "line_total": 57000
    },
    {
      "name": "Gula",
      "quantity": 2,
      "unit": "kg",
      "unit_price": 16000,
      "line_total": 32000
    },
    {
      "name": "Telur",
      "quantity": 1,
      "unit": "kg",
      "unit_price": 28000,
      "line_total": 28000
    },
    {
      "name": "Bawang merah",
      "quantity": 1,
      "unit": "kg",
      "unit_price": 35000,
      "line_total": 35000
    },
    {
      "name": "Cabai rawit",
      "quantity": 0.5,
      "unit": "kg",
      "unit_price": 60000,
      "line_total": 30000
    },
    {
      "name": "Tahu",
      "quantity": 10,
      "unit": "pcs",
      "unit_price": 1000,
      "line_total": 10000
    }
  ],
  "total_amount": 332000,
  "confidence": "high",
  "ambiguities": []
}
```

#### `hand-3-segoe-script.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Segoe Script
- size 128 KB · image/jpeg · latency 19511 ms · tokens in/out 479/323
- document_type `receipt` · confidence **high** · 6 items · total_amount 84500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 6 · invented units 6 · field errors 12 · **SILENT ERROR**

Raw model output (verbatim):

```json
{"document_type": "receipt", "supplier": "warung bu Sri", "date": "2026-09-03", "items": [{"name": "Kopi sachet", "quantity": 2, "unit": "pcs", "unit_price": 10000, "line_total": 20000}, {"name": "Teh celup", "quantity": 1, "unit": "pcs", "unit_price": 6500, "line_total": 6500}, {"name": "Mie instan", "quantity": 3, "unit": "pcs", "unit_price": 3000, "line_total": 9000}, {"name": "Minyak goreng", "quantity": 1, "unit": "pcs", "unit_price": 18000, "line_total": 18000}, {"name": "Sabun cuci", "quantity": 2, "unit": "pcs", "unit_price": 4500, "line_total": 9000}, {"name": "Gas 3kg", "quantity": 1, "unit": "pcs", "unit_price": 22000, "line_total": 22000}], "total_amount": 84500, "confidence": "high", "ambiguities": []}
```

#### `hand-4-bradley.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Bradley Hand
- size 117 KB · image/jpeg · latency 11859 ms · tokens in/out 479/323
- document_type `receipt` · confidence **high** · 6 items · total_amount 84500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 6 · invented units 6 · field errors 12 · **SILENT ERROR**

Raw model output (verbatim):

```json
{"document_type": "receipt", "supplier": "warung bu Sri", "date": "2026-09-03", "items": [{"name": "Kopi sachet", "quantity": 2, "unit": "pcs", "unit_price": 10000, "line_total": 20000}, {"name": "Teh celup", "quantity": 1, "unit": "pcs", "unit_price": 6500, "line_total": 6500}, {"name": "Mie instan", "quantity": 3, "unit": "pcs", "unit_price": 3000, "line_total": 9000}, {"name": "Minyak goreng", "quantity": 1, "unit": "pcs", "unit_price": 18000, "line_total": 18000}, {"name": "Sabun cuci", "quantity": 2, "unit": "pcs", "unit_price": 4500, "line_total": 9000}, {"name": "Gas 3kg", "quantity": 1, "unit": "pcs", "unit_price": 22000, "line_total": 22000}], "total_amount": 84500, "confidence": "high", "ambiguities": []}
```

#### `blur-1-neat-r3.5.jpg` — blurred

- provenance: generated: Gaussian blur of test-receipt-1-neat.png (r=3.5)
- size 98 KB · image/jpeg · latency 13802 ms · tokens in/out 479/527
- document_type `stock_ledger` · confidence **high** · 10 items · total_amount 645500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 1 · **SILENT ERROR**

Raw model output (verbatim):

```json
{"document_type": "stock_ledger", "supplier": "", "date": "2018-05-28", "items": [{"name": "Minyak goreng 2L", "quantity": 3, "unit": "botol", "unit_price": 38000, "line_total": 114000}, {"name": "Gula pasir 1kg", "quantity": 5, "unit": "kg", "unit_price": 15000, "line_total": 75000}, {"name": "Kopi Sachet", "quantity": 2, "unit": "dus", "unit_price": 88000, "line_total": 176000}, {"name": "Telur", "quantity": 1, "unit": "kg", "unit_price": 27000, "line_total": 27000}, {"name": "Tepung terigu 1kg", "quantity": 2, "unit": "kg", "unit_price": 12500, "line_total": 25000}, {"name": "Susu kental manis", "quantity": 3, "unit": "kaleng", "unit_price": 11000, "line_total": 33000}, {"name": "Mie instan", "quantity": 10, "unit": "bungkus", "unit_price": 3000, "line_total": 30000}, {"name": "Kecap manis", "quantity": 2, "unit": "botol", "unit_price": 10000, "line_total": 20000}, {"name": "Sabun cuci piring", "quantity": 3, "unit": "pouch", "unit_price": 6500, "line_total": 19500}, {"name": "Rokok Surya 16", "quantity": 4, "unit": "bungkus", "unit_price": 29000, "line_total": 116000}], "total_amount": 645500, "confidence": "high", "ambiguities": []}
```

#### `blur-2-medium-r3.jpg` — blurred

- provenance: generated: Gaussian blur of test-receipt-3-medium.png (r=3.0)
- size 102 KB · image/jpeg · latency 20264 ms · tokens in/out 479/683
- document_type `receipt` · confidence **high** · 10 items · total_amount 343000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 10 · invented units 9 · field errors 19 · **SILENT ERROR**

Raw model output (verbatim):

```json
{
  "document_type": "receipt",
  "supplier": "",
  "date": "2023-05-26",
  "items": [
    {
      "name": "Beras medium 10kg",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 140000,
      "line_total": 140000
    },
    {
      "name": "Minyak goreng 2L",
      "quantity": 2,
      "unit": "pcs",
      "unit_price": 31000,
      "line_total": 62000
    },
    {
      "name": "Gula pasir 1kg",
      "quantity": 2,
      "unit": "pcs",
      "unit_price": 14000,
      "line_total": 28000
    },
    {
      "name": "Tepung terigu",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 12000,
      "line_total": 12000
    },
    {
      "name": "Telur ayam",
      "quantity": 1,
      "unit": "tray",
      "unit_price": 27000,
      "line_total": 27000
    },
    {
      "name": "Mie instan",
      "quantity": 10,
      "unit": "pcs",
      "unit_price": 1500,
      "line_total": 15000
    },
    {
      "name": "Kopi sachet",
      "quantity": 20,
      "unit": "sachet",
      "unit_price": 900,
      "line_total": 18000
    },
    {
      "name": "Teh celup",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 6000,
      "line_total": 6000
    },
    {
      "name": "Susu kental manis",
      "quantity": 2,
      "unit": "pcs",
      "unit_price": 6500,
      "line_total": 13000
    },
    {
      "name": "Gas 3kg",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 22000,
      "line_total": 22000
    }
  ],
  "total_amount": 343000,
  "confidence": "high",
  "ambiguities": []
}
```

#### `blur-3-thermal-r2.2.jpg` — blurred

- provenance: generated: Gaussian blur of thermal-1-clean (r=2.2)
- size 37 KB · image/jpeg · latency 25233 ms · tokens in/out 479/575
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 · invented units 0 · field errors 0

Raw model output (verbatim):

```json
{
  "document_type": "receipt",
  "supplier": "TOKO SUMBER REJEKI",
  "date": "2026-08-14",
  "items": [
    {
      "name": "Indomie Goreng",
      "quantity": 5,
      "unit": "pcs",
      "unit_price": 3500,
      "line_total": 17500
    },
    {
      "name": "Aqua 600ml",
      "quantity": 12,
      "unit": "pcs",
      "unit_price": 3000,
      "line_total": 36000
    },
    {
      "name": "Kopi Kapal Api",
      "quantity": 10,
      "unit": "pcs",
      "unit_price": 1500,
      "line_total": 15000
    },
    {
      "name": "Gula Pasir 1kg",
      "quantity": 2,
      "unit": "pcs",
      "unit_price": 17000,
      "line_total": 34000
    },
    {
      "name": "Minyak Goreng 2L",
      "quantity": 1,
      "unit": "pcs",
      "unit_price": 36000,
      "line_total": 36000
    },
    {
      "name": "Telur 1kg",
      "quantity": 2,
      "unit": "pcs",
      "unit_price": 28000,
      "line_total": 56000
    },
    {
      "name": "Sabun Lifebuoy",
      "quantity": 3,
      "unit": "pcs",
      "unit_price": 4500,
      "line_total": 13500
    },
    {
      "name": "Rokok Sampoerna",
      "quantity": 2,
      "unit": "pcs",
      "unit_price": 32000,
      "line_total": 64000
    }
  ],
  "total_amount": 272000,
  "confidence": "high",
  "ambiguities": []
}
```

#### `angle-1-neat.jpg` — angled

- provenance: generated: perspective warp of test-receipt-1-neat.png
- size 245 KB · image/jpeg · latency 21550 ms · tokens in/out 479/555
- document_type `stock_ledger` · confidence **medium** · 10 items · total_amount 645500 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: lines 10/10 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 1

Raw model output (verbatim):

```json
{"document_type": "stock_ledger", "supplier": "", "date": "2025-05-25", "items": [{"name": "Minyak goreng 2L", "quantity": 3, "unit": "botol", "unit_price": 38000, "line_total": 114000}, {"name": "Gula pasir 1kg", "quantity": 5, "unit": "kg", "unit_price": 15000, "line_total": 75000}, {"name": "Kopi Sachet", "quantity": 2, "unit": "dus", "unit_price": 88000, "line_total": 176000}, {"name": "Telur", "quantity": 1, "unit": "kg", "unit_price": 27000, "line_total": 27000}, {"name": "Tepung terigu 1kg", "quantity": 2, "unit": "kg", "unit_price": 12500, "line_total": 25000}, {"name": "Susu kental manis", "quantity": 3, "unit": "kaleng", "unit_price": 11000, "line_total": 33000}, {"name": "Mie instan", "quantity": 10, "unit": "bungkus", "unit_price": 3000, "line_total": 30000}, {"name": "Kecap manis", "quantity": 2, "unit": "botol", "unit_price": 10000, "line_total": 20000}, {"name": "Sabun cuci piring", "quantity": 3, "unit": "pouch", "unit_price": 6500, "line_total": 19500}, {"name": "Rokok Surya 16", "quantity": 4, "unit": "bungkus", "unit_price": 29000, "line_total": 116000}], "total_amount": 645500, "confidence": "medium", "ambiguities": ["Total amount (645,500) does not match the sum of line items (635,500)."]}
```

#### `angle-2-thermal.jpg` — angled

- provenance: generated: perspective warp of a clean render of thermal B
- size 77 KB · image/jpeg · latency 12770 ms · tokens in/out 479/343
- document_type `receipt` · confidence **high** · 6 items · total_amount 237000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 · invented units 0 · field errors 0

Raw model output (verbatim):

```json
{"document_type": "receipt", "supplier": "GROSIR BAROKAH", "date": "2026-08-20", "items": [{"name": "Beras 5kg", "quantity": 1, "unit": "pcs", "unit_price": 72000, "line_total": 72000}, {"name": "Susu UHT 1L", "quantity": 4, "unit": "pcs", "unit_price": 18000, "line_total": 72000}, {"name": "Teh Celup Sariwangi", "quantity": 2, "unit": "pcs", "unit_price": 7500, "line_total": 15000}, {"name": "Kecap Bango 600ml", "quantity": 1, "unit": "pcs", "unit_price": 24000, "line_total": 24000}, {"name": "Mie Sedaap", "quantity": 10, "unit": "pcs", "unit_price": 3200, "line_total": 32000}, {"name": "Gas 3kg", "quantity": 1, "unit": "pcs", "unit_price": 22000, "line_total": 22000}], "total_amount": 237000, "confidence": "high", "ambiguities": []}
```

#### `angle-3-hand-segoe.jpg` — angled

- provenance: generated: perspective warp of hand-1-segoe-print
- size 161 KB · image/jpeg · latency 43114 ms · tokens in/out 479/366
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 · invented units 0 · field errors 0

Raw model output (verbatim):

```json
{"document_type": "receipt", "supplier": "", "date": "2026-08-12", "items": [{"name": "Beras", "quantity": 10, "unit": "kg", "unit_price": 14000, "line_total": 140000}, {"name": "Minyak goreng", "quantity": 3, "unit": "liter", "unit_price": 19000, "line_total": 57000}, {"name": "Gula", "quantity": 2, "unit": "kg", "unit_price": 16000, "line_total": 32000}, {"name": "Telur", "quantity": 1, "unit": "kg", "unit_price": 28000, "line_total": 28000}, {"name": "Bawang merah", "quantity": 1, "unit": "kg", "unit_price": 35000, "line_total": 35000}, {"name": "Cabai rawit", "quantity": 0.5, "unit": "kg", "unit_price": 60000, "line_total": 30000}, {"name": "Tahu", "quantity": 10, "unit": "pcs", "unit_price": 1000, "line_total": 10000}], "total_amount": 332000, "confidence": "high", "ambiguities": []}
```

#### `control-unreadable.jpg` — unreadable_control

- provenance: generated: thermal B blurred (r=11) and noised past legibility — deliberately unreadable
- size 213 KB · image/jpeg · latency 8188 ms · tokens in/out 479/54
- document_type `other` · confidence **low** · 0 items · total_amount 0 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: 0 fabricated items · control OK

Raw model output (verbatim):

```json
{"document_type": "other", "supplier": "", "date": "", "items": [], "total_amount": 0, "confidence": "low", "ambiguities": ["The entire document is unreadable due to extreme blurriness."]}
```

### M1-T2 expanded set, untuned prompt, run 2 — 2026-09-02 18:35 UTC

- vision model: `gemini-2.5-flash` (setting `GEMINI_PRO_MODEL`)
- prompt/schema: `backend/app/ai/vision.py` at commit `86707d7`
- gate rule: `app.services.receipts.needs_confirmation` — confidence != high, or any ambiguity, or no items
- images: 18 (scored against manifest.json)

**Per-category summary** (sample counts stated; scoring rules in the script docstring):

| category | n | lines matched | totals exact | gate triggered | silent errors |
|---|---|---|---|---|---|
| handwritten_photo | 3 | 0/27 (0%) | 0/3 | 3/3 | **0** |
| thermal_printed | 4 | 0/30 (0%) | 0/4 | 4/4 | **0** |
| handwritten_font | 4 | 0/26 (0%) | 0/4 | 4/4 | **0** |
| blurred | 3 | 0/28 (0%) | 0/3 | 3/3 | **0** |
| angled | 3 | 0/23 (0%) | 0/3 | 3/3 | **0** |
| unreadable_control | 1 | n/a (no fabricated items) | n/a | 1/1 | **0** |
| **all** | 18 | 0/134 (0%) | 0/17 | 18/18 | **0** |

#### `test-receipt-1-neat.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size 2304 KB · image/png · latency 1423 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 57.899050393s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.5-flash'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '57s'}]}}`
- score: lines 0/10 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 11

Raw model output (verbatim):

```json
<no text returned>
```

#### `test-receipt-2-messy.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size 2261 KB · image/png · latency 9556 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 48.528474716s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.5-flash'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '48s'}]}}`
- score: lines 0/7 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 8

Raw model output (verbatim):

```json
<no text returned>
```

#### `test-receipt-3-medium.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size 2171 KB · image/png · latency 2281 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 45.890507192s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.5-flash'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '45s'}]}}`
- score: lines 0/10 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 11

Raw model output (verbatim):

```json
<no text returned>
```

#### `thermal-1-clean.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), clean
- size 100 KB · image/jpeg · latency 293 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 45.597984271s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.5-flash'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '45s'}]}}`
- score: lines 0/8 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 9

Raw model output (verbatim):

```json
<no text returned>
```

#### `thermal-2-faded.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), faded ink
- size 72 KB · image/jpeg · latency 294 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 45.319105635s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'model': 'gemini-2.5-flash', 'location': 'global'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '45s'}]}}`
- score: lines 0/8 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 9

Raw model output (verbatim):

```json
<no text returned>
```

#### `thermal-3-noisy-rotated.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), sensor noise + 2.5° rotation
- size 185 KB · image/jpeg · latency 844 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 44.461133851s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.5-flash'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '44s'}]}}`
- score: lines 0/6 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 7

Raw model output (verbatim):

```json
<no text returned>
```

#### `thermal-4-lowres.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), low resolution (45%)
- size 80 KB · image/jpeg · latency 283 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 44.192330243s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.5-flash'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '44s'}]}}`
- score: lines 0/8 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 9

Raw model output (verbatim):

```json
<no text returned>
```

#### `hand-1-segoe-print.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Segoe Print
- size 183 KB · image/jpeg · latency 385 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 43.75415776s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.5-flash'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '43s'}]}}`
- score: lines 0/7 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 8

Raw model output (verbatim):

```json
<no text returned>
```

#### `hand-2-ink-free.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Ink Free
- size 177 KB · image/jpeg · latency 212 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 43.542149179s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'model': 'gemini-2.5-flash', 'location': 'global'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '43s'}]}}`
- score: lines 0/7 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 8

Raw model output (verbatim):

```json
<no text returned>
```

#### `hand-3-segoe-script.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Segoe Script
- size 128 KB · image/jpeg · latency 212 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 43.334047689s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.5-flash'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '43s'}]}}`
- score: lines 0/6 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 7

Raw model output (verbatim):

```json
<no text returned>
```

#### `hand-4-bradley.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Bradley Hand
- size 117 KB · image/jpeg · latency 245 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 43.116874902s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.5-flash'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '43s'}]}}`
- score: lines 0/6 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 7

Raw model output (verbatim):

```json
<no text returned>
```

#### `blur-1-neat-r3.5.jpg` — blurred

- provenance: generated: Gaussian blur of test-receipt-1-neat.png (r=3.5)
- size 98 KB · image/jpeg · latency 832 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 42.379073925s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'model': 'gemini-2.5-flash', 'location': 'global'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '42s'}]}}`
- score: lines 0/10 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 11

Raw model output (verbatim):

```json
<no text returned>
```

#### `blur-2-medium-r3.jpg` — blurred

- provenance: generated: Gaussian blur of test-receipt-3-medium.png (r=3.0)
- size 102 KB · image/jpeg · latency 586 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 41.665510428s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'model': 'gemini-2.5-flash', 'location': 'global'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '41s'}]}}`
- score: lines 0/10 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 11

Raw model output (verbatim):

```json
<no text returned>
```

#### `blur-3-thermal-r2.2.jpg` — blurred

- provenance: generated: Gaussian blur of thermal-1-clean (r=2.2)
- size 37 KB · image/jpeg · latency 222 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 41.443256767s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.5-flash'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '41s'}]}}`
- score: lines 0/8 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 9

Raw model output (verbatim):

```json
<no text returned>
```

#### `angle-1-neat.jpg` — angled

- provenance: generated: perspective warp of test-receipt-1-neat.png
- size 245 KB · image/jpeg · latency 333 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 41.145126496s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.5-flash'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '41s'}]}}`
- score: lines 0/10 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 11

Raw model output (verbatim):

```json
<no text returned>
```

#### `angle-2-thermal.jpg` — angled

- provenance: generated: perspective warp of a clean render of thermal B
- size 77 KB · image/jpeg · latency 253 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 40.869769105s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.5-flash'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '40s'}]}}`
- score: lines 0/6 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 7

Raw model output (verbatim):

```json
<no text returned>
```

#### `angle-3-hand-segoe.jpg` — angled

- provenance: generated: perspective warp of hand-1-segoe-print
- size 161 KB · image/jpeg · latency 422 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 40.481812025s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.5-flash'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '40s'}]}}`
- score: lines 0/7 · extra 0 · total WRONG · fabricated unit prices 0 · invented units 0 · field errors 8

Raw model output (verbatim):

```json
<no text returned>
```

#### `control-unreadable.jpg` — unreadable_control

- provenance: generated: thermal B blurred (r=11) and noised past legibility — deliberately unreadable
- size 213 KB · image/jpeg · latency 504 ms
- **ERROR:** `ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 39.999518389s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'model': 'gemini-2.5-flash', 'location': 'global'}, 'quotaValue': '20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '39s'}]}}`
- score: 0 fabricated items · control OK

Raw model output (verbatim):

```json
<no text returned>
```

### Assessment of the M1-T2 expanded set (written by hand after run 1, 2026-09-03)

**Sample set: 18 images, 15 generated + 3 AI-generated photo-realistic stand-ins from Phase 14.
No real photographs of real documents exist in the set yet.** Per-category counts are stated
in every table. `handwritten_font` images are rendered with Windows handwriting fonts and are
much easier than human handwriting; treat them as a lower bound on difficulty, not a
measurement of the real thing. The three Phase-14 photos are the only handwriting-like input.

**Run 2 in the section above is void**: every call returned `429 RESOURCE_EXHAUSTED`. The
Google free tier allows **20 `generate_content` requests per day per model** on this project
(`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, `gemini-2.5-flash`), and M1-T1 + run 1
used 21. This caps every future measurement at about one 18-image pass per day until billing
is enabled on the Google Cloud project. The 200–800 ms latencies in run 2 are the rejections.

**Correction to run 1's automatic scoring.** The manifest computed the neat page's total from
its lines (635.500), but the generated page *writes* 645.500 — the AI-generated document has
an arithmetic slip. The model transcribed the written figure, which is correct behaviour.
`manifest.json` now carries `written_total` for that page (and `lines_sum` for every page),
so `test-receipt-1-neat.png` and `blur-1-neat-r3.5.jpg` are **not** errors. Corrected table:

| category | n | lines matched | totals exact | gate triggered | silent errors (corrected) |
|---|---|---|---|---|---|
| handwritten_photo | 3 | 27/27 (100%) | 3/3 | 1/3 | **1** — glare page: 10 fabricated unit prices, 9 invented units |
| thermal_printed | 4 | 30/30 (100%) | 4/4 | 0/4 | **1** — faded: units `kg`/`liter`/`kg` read off product names on 3 lines |
| handwritten_font | 4 | 26/26 (100%) | 4/4 | 1/4 | **2** — both line-total-only notes: 6 fabricated unit prices + 6 `pcs` each |
| blurred | 3 | 28/28 (100%) | 3/3 | 0/3 | **1** — blurred glare page: same as its original (10 + 9) |
| angled | 3 | 23/23 (100%) | 3/3 | 1/3 | **0** |
| unreadable_control | 1 | n/a — 0 fabricated items, `document_type=other`, `low` | n/a | 1/1 | **0** |
| **all** | 18 | 134/134 (100%) | 17/17 | 4/18 | **5 / 18 (28%)** |

What the expanded set says:

1. **Transcription is not the problem on this set.** 134/134 lines and 17/17 totals, through
   blur r=3.5, 45% downscale, 2.5° rotation + noise, faded ink and perspective warp. The
   control was refused correctly. The scoring was strict (name similarity + exact quantity +
   exact line total), so this is not a lenient pass.
2. **Every silent error is a field the document does not show.** All five are the model
   filling `unit_price` (line total ÷ quantity) and `unit` (`pcs`, or a unit read from the
   product name) on documents that write neither, then claiming `high` with no ambiguity —
   the M1-T1 finding, now reproduced on 4 more images across 3 categories. Zero silent errors
   came from misreading. The failure is a schema/prompt design problem, not a vision problem.
3. **The model notices arithmetic when asked nothing about it.** On `angle-1-neat.jpg` it
   listed "Total amount (645,500) does not match the sum of line items (635,500)" as an
   ambiguity and dropped to `medium` — but on the same page un-warped it stayed `high`. A
   server-side sum check would make that deterministic.
4. **Gate trigger rate is 4/18.** Three were right to trigger (messy nota, control, the
   angled sum mismatch); one (`hand-1-segoe-print`) triggered on a date-format doubt, which
   is a harmless ask. The gate never triggered wrongly on a clean page.

Implications carried into M1-T3 (planned, not applied here):
- Ask the model *whether each field was written* (`unit_written`, `unit_price_written`
  per item) instead of instructing "0 if not shown", which it ignores; then zero the field
  server-side when not written. A guess that is never stored cannot be a silent error.
- Add `sum(line_total) == total_amount` as a server-side ambiguity when both are present.
- Keep the gate rule as is on top of those; re-measure the whole set, once, when quota
  allows, and compare against this table.

### Re-score of “M1-T2 expanded set, untuned prompt (run 1, reconstructed from vision-results.md)” — 2026-09-02 18:44 UTC

- source run: `20260902T180000Z-m1-t2-expanded-set-untuned-run-1-reconstructed.json` (model `gemini-2.5-flash`, prompt at `eb506c1`)
- no model calls: the saved verbatim outputs re-scored with the scoring rules in this script's docstring

**Per-category summary:**

| category | n | lines matched | totals exact | gate triggered | silent errors |
|---|---|---|---|---|---|
| handwritten_photo | 3 | 27/27 (100%) | 3/3 | 1/3 | **1** |
| thermal_printed | 4 | 30/30 (100%) | 4/4 | 0/4 | **1** |
| handwritten_font | 4 | 26/26 (100%) | 4/4 | 1/4 | **0** |
| blurred | 3 | 28/28 (100%) | 2/3 | 0/3 | **2** |
| angled | 3 | 23/23 (100%) | 2/3 | 1/3 | **0** |
| unreadable_control | 1 | n/a (no fabricated items) | n/a | 1/1 | **0** |
| **all** | 18 | 134/134 (100%) | 15/17 | 4/18 | **4** |
#### `test-receipt-1-neat.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size ? KB · ? · latency 15048 ms
- document_type `stock_ledger` · confidence **high** · 10 items · total_amount 645500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `test-receipt-2-messy.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size ? KB · ? · latency 39964 ms
- document_type `receipt` · confidence **medium** · 7 items · total_amount 87500 · ambiguities 3
- confirmation gate: **TRIGGERED**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 2 · field errors 2

#### `test-receipt-3-medium.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size ? KB · ? · latency 45710 ms
- document_type `receipt` · confidence **high** · 10 items · total_amount 343000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 10) · invented units 7 · field errors 7 · **SILENT ERROR**

#### `thermal-1-clean.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), clean
- size ? KB · ? · latency 16478 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `thermal-2-faded.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), faded ink
- size ? KB · ? · latency 26557 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 3 · field errors 3 · **SILENT ERROR**

#### `thermal-3-noisy-rotated.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), sensor noise + 2.5° rotation
- size ? KB · ? · latency 21951 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 237000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `thermal-4-lowres.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), low resolution (45%)
- size ? KB · ? · latency 20267 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `hand-1-segoe-print.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Segoe Print
- size ? KB · ? · latency 12164 ms
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `hand-2-ink-free.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Ink Free
- size ? KB · ? · latency 12290 ms
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `hand-3-segoe-script.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Segoe Script
- size ? KB · ? · latency 19511 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 84500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 6) · invented units 0 · field errors 0

#### `hand-4-bradley.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Bradley Hand
- size ? KB · ? · latency 11859 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 84500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 6) · invented units 0 · field errors 0

#### `blur-1-neat-r3.5.jpg` — blurred

- provenance: generated: Gaussian blur of test-receipt-1-neat.png (r=3.5)
- size ? KB · ? · latency 13802 ms
- document_type `stock_ledger` · confidence **high** · 10 items · total_amount 645500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total WRONG · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 1 · **SILENT ERROR**

#### `blur-2-medium-r3.jpg` — blurred

- provenance: generated: Gaussian blur of test-receipt-3-medium.png (r=3.0)
- size ? KB · ? · latency 20264 ms
- document_type `receipt` · confidence **high** · 10 items · total_amount 343000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 10) · invented units 1 · field errors 1 · **SILENT ERROR**

#### `blur-3-thermal-r2.2.jpg` — blurred

- provenance: generated: Gaussian blur of thermal-1-clean (r=2.2)
- size ? KB · ? · latency 25233 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `angle-1-neat.jpg` — angled

- provenance: generated: perspective warp of test-receipt-1-neat.png
- size ? KB · ? · latency 21550 ms
- document_type `stock_ledger` · confidence **medium** · 10 items · total_amount 645500 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: lines 10/10 · extra 0 · total WRONG · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 1

#### `angle-2-thermal.jpg` — angled

- provenance: generated: perspective warp of a clean render of thermal B
- size ? KB · ? · latency 12770 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 237000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `angle-3-hand-segoe.jpg` — angled

- provenance: generated: perspective warp of hand-1-segoe-print
- size ? KB · ? · latency 43114 ms
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `control-unreadable.jpg` — unreadable_control

- provenance: generated: thermal B blurred (r=11) and noised past legibility — deliberately unreadable
- size ? KB · ? · latency 8188 ms
- document_type `other` · confidence **low** · 0 items · total_amount 0 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: 0 fabricated items · control OK

### Re-score of “M1-T2 expanded set, untuned prompt (run 1, reconstructed from vision-results.md)” + normalize_parse — 2026-09-02 18:44 UTC

- source run: `20260902T180000Z-m1-t2-expanded-set-untuned-run-1-reconstructed.json` (model `gemini-2.5-flash`, prompt at `eb506c1`)
- no model calls: the saved verbatim outputs re-scored with the scoring rules in this script's docstring after passing through the server-side guards (`normalize_parse`)

**Per-category summary:**

| category | n | lines matched | totals exact | gate triggered | silent errors |
|---|---|---|---|---|---|
| handwritten_photo | 3 | 27/27 (100%) | 3/3 | 2/3 | **1** |
| thermal_printed | 4 | 30/30 (100%) | 4/4 | 0/4 | **1** |
| handwritten_font | 4 | 26/26 (100%) | 4/4 | 1/4 | **0** |
| blurred | 3 | 28/28 (100%) | 2/3 | 1/3 | **1** |
| angled | 3 | 23/23 (100%) | 2/3 | 1/3 | **0** |
| unreadable_control | 1 | n/a (no fabricated items) | n/a | 1/1 | **0** |
| **all** | 18 | 134/134 (100%) | 15/17 | 6/18 | **3** |
#### `test-receipt-1-neat.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size ? KB · ? · latency 15048 ms
- document_type `stock_ledger` · confidence **high** · 10 items · total_amount 645500 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `test-receipt-2-messy.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size ? KB · ? · latency 39964 ms
- document_type `receipt` · confidence **medium** · 7 items · total_amount 87500 · ambiguities 3
- confirmation gate: **TRIGGERED**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 2 · field errors 2

#### `test-receipt-3-medium.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size ? KB · ? · latency 45710 ms
- document_type `receipt` · confidence **high** · 10 items · total_amount 343000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 10) · invented units 7 · field errors 7 · **SILENT ERROR**

#### `thermal-1-clean.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), clean
- size ? KB · ? · latency 16478 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `thermal-2-faded.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), faded ink
- size ? KB · ? · latency 26557 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 3 · field errors 3 · **SILENT ERROR**

#### `thermal-3-noisy-rotated.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), sensor noise + 2.5° rotation
- size ? KB · ? · latency 21951 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 237000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `thermal-4-lowres.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), low resolution (45%)
- size ? KB · ? · latency 20267 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `hand-1-segoe-print.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Segoe Print
- size ? KB · ? · latency 12164 ms
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `hand-2-ink-free.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Ink Free
- size ? KB · ? · latency 12290 ms
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `hand-3-segoe-script.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Segoe Script
- size ? KB · ? · latency 19511 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 84500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 6) · invented units 0 · field errors 0

#### `hand-4-bradley.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Bradley Hand
- size ? KB · ? · latency 11859 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 84500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 6) · invented units 0 · field errors 0

#### `blur-1-neat-r3.5.jpg` — blurred

- provenance: generated: Gaussian blur of test-receipt-1-neat.png (r=3.5)
- size ? KB · ? · latency 13802 ms
- document_type `stock_ledger` · confidence **high** · 10 items · total_amount 645500 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: lines 10/10 · extra 0 · total WRONG · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 1

#### `blur-2-medium-r3.jpg` — blurred

- provenance: generated: Gaussian blur of test-receipt-3-medium.png (r=3.0)
- size ? KB · ? · latency 20264 ms
- document_type `receipt` · confidence **high** · 10 items · total_amount 343000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 10) · invented units 1 · field errors 1 · **SILENT ERROR**

#### `blur-3-thermal-r2.2.jpg` — blurred

- provenance: generated: Gaussian blur of thermal-1-clean (r=2.2)
- size ? KB · ? · latency 25233 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `angle-1-neat.jpg` — angled

- provenance: generated: perspective warp of test-receipt-1-neat.png
- size ? KB · ? · latency 21550 ms
- document_type `stock_ledger` · confidence **medium** · 10 items · total_amount 645500 · ambiguities 2
- confirmation gate: **TRIGGERED**
- score: lines 10/10 · extra 0 · total WRONG · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 1

#### `angle-2-thermal.jpg` — angled

- provenance: generated: perspective warp of a clean render of thermal B
- size ? KB · ? · latency 12770 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 237000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `angle-3-hand-segoe.jpg` — angled

- provenance: generated: perspective warp of hand-1-segoe-print
- size ? KB · ? · latency 43114 ms
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `control-unreadable.jpg` — unreadable_control

- provenance: generated: thermal B blurred (r=11) and noised past legibility — deliberately unreadable
- size ? KB · ? · latency 8188 ms
- document_type `other` · confidence **low** · 0 items · total_amount 0 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: 0 fabricated items · control OK

### Re-score of “M1-T2 expanded set, untuned prompt (run 1, reconstructed from vision-results.md)” — 2026-09-02 18:45 UTC

- source run: `20260902T180000Z-m1-t2-expanded-set-untuned-run-1-reconstructed.json` (model `gemini-2.5-flash`, prompt at `eb506c1`)
- no model calls: the saved verbatim outputs re-scored with the scoring rules in this script's docstring

**Per-category summary:**

| category | n | lines matched | totals exact | gate triggered | silent errors |
|---|---|---|---|---|---|
| handwritten_photo | 3 | 27/27 (100%) | 3/3 | 1/3 | **1** |
| thermal_printed | 4 | 30/30 (100%) | 4/4 | 0/4 | **1** |
| handwritten_font | 4 | 26/26 (100%) | 4/4 | 1/4 | **0** |
| blurred | 3 | 28/28 (100%) | 3/3 | 0/3 | **1** |
| angled | 3 | 23/23 (100%) | 3/3 | 1/3 | **0** |
| unreadable_control | 1 | n/a (no fabricated items) | n/a | 1/1 | **0** |
| **all** | 18 | 134/134 (100%) | 17/17 | 4/18 | **3** |
#### `test-receipt-1-neat.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size ? KB · ? · latency 15048 ms
- document_type `stock_ledger` · confidence **high** · 10 items · total_amount 645500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `test-receipt-2-messy.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size ? KB · ? · latency 39964 ms
- document_type `receipt` · confidence **medium** · 7 items · total_amount 87500 · ambiguities 3
- confirmation gate: **TRIGGERED**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 2 · field errors 2

#### `test-receipt-3-medium.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size ? KB · ? · latency 45710 ms
- document_type `receipt` · confidence **high** · 10 items · total_amount 343000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 10) · invented units 7 · field errors 7 · **SILENT ERROR**

#### `thermal-1-clean.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), clean
- size ? KB · ? · latency 16478 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `thermal-2-faded.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), faded ink
- size ? KB · ? · latency 26557 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 3 · field errors 3 · **SILENT ERROR**

#### `thermal-3-noisy-rotated.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), sensor noise + 2.5° rotation
- size ? KB · ? · latency 21951 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 237000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `thermal-4-lowres.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), low resolution (45%)
- size ? KB · ? · latency 20267 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `hand-1-segoe-print.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Segoe Print
- size ? KB · ? · latency 12164 ms
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `hand-2-ink-free.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Ink Free
- size ? KB · ? · latency 12290 ms
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `hand-3-segoe-script.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Segoe Script
- size ? KB · ? · latency 19511 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 84500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 6) · invented units 0 · field errors 0

#### `hand-4-bradley.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Bradley Hand
- size ? KB · ? · latency 11859 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 84500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 6) · invented units 0 · field errors 0

#### `blur-1-neat-r3.5.jpg` — blurred

- provenance: generated: Gaussian blur of test-receipt-1-neat.png (r=3.5)
- size ? KB · ? · latency 13802 ms
- document_type `stock_ledger` · confidence **high** · 10 items · total_amount 645500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `blur-2-medium-r3.jpg` — blurred

- provenance: generated: Gaussian blur of test-receipt-3-medium.png (r=3.0)
- size ? KB · ? · latency 20264 ms
- document_type `receipt` · confidence **high** · 10 items · total_amount 343000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 10) · invented units 1 · field errors 1 · **SILENT ERROR**

#### `blur-3-thermal-r2.2.jpg` — blurred

- provenance: generated: Gaussian blur of thermal-1-clean (r=2.2)
- size ? KB · ? · latency 25233 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `angle-1-neat.jpg` — angled

- provenance: generated: perspective warp of test-receipt-1-neat.png
- size ? KB · ? · latency 21550 ms
- document_type `stock_ledger` · confidence **medium** · 10 items · total_amount 645500 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `angle-2-thermal.jpg` — angled

- provenance: generated: perspective warp of a clean render of thermal B
- size ? KB · ? · latency 12770 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 237000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `angle-3-hand-segoe.jpg` — angled

- provenance: generated: perspective warp of hand-1-segoe-print
- size ? KB · ? · latency 43114 ms
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `control-unreadable.jpg` — unreadable_control

- provenance: generated: thermal B blurred (r=11) and noised past legibility — deliberately unreadable
- size ? KB · ? · latency 8188 ms
- document_type `other` · confidence **low** · 0 items · total_amount 0 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: 0 fabricated items · control OK

### Re-score of “M1-T2 expanded set, untuned prompt (run 1, reconstructed from vision-results.md)” + normalize_parse — 2026-09-02 18:45 UTC

- source run: `20260902T180000Z-m1-t2-expanded-set-untuned-run-1-reconstructed.json` (model `gemini-2.5-flash`, prompt at `eb506c1`)
- no model calls: the saved verbatim outputs re-scored with the scoring rules in this script's docstring after passing through the server-side guards (`normalize_parse`)

**Per-category summary:**

| category | n | lines matched | totals exact | gate triggered | silent errors |
|---|---|---|---|---|---|
| handwritten_photo | 3 | 27/27 (100%) | 3/3 | 2/3 | **1** |
| thermal_printed | 4 | 30/30 (100%) | 4/4 | 0/4 | **1** |
| handwritten_font | 4 | 26/26 (100%) | 4/4 | 1/4 | **0** |
| blurred | 3 | 28/28 (100%) | 3/3 | 1/3 | **1** |
| angled | 3 | 23/23 (100%) | 3/3 | 1/3 | **0** |
| unreadable_control | 1 | n/a (no fabricated items) | n/a | 1/1 | **0** |
| **all** | 18 | 134/134 (100%) | 17/17 | 6/18 | **3** |
#### `test-receipt-1-neat.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size ? KB · ? · latency 15048 ms
- document_type `stock_ledger` · confidence **high** · 10 items · total_amount 645500 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `test-receipt-2-messy.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size ? KB · ? · latency 39964 ms
- document_type `receipt` · confidence **medium** · 7 items · total_amount 87500 · ambiguities 3
- confirmation gate: **TRIGGERED**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 2 · field errors 2

#### `test-receipt-3-medium.png` — handwritten_photo

- provenance: AI-generated photo-realistic stand-in (Phase 14, July 2026)
- size ? KB · ? · latency 45710 ms
- document_type `receipt` · confidence **high** · 10 items · total_amount 343000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 10) · invented units 7 · field errors 7 · **SILENT ERROR**

#### `thermal-1-clean.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), clean
- size ? KB · ? · latency 16478 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `thermal-2-faded.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), faded ink
- size ? KB · ? · latency 26557 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 3 · field errors 3 · **SILENT ERROR**

#### `thermal-3-noisy-rotated.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), sensor noise + 2.5° rotation
- size ? KB · ? · latency 21951 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 237000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `thermal-4-lowres.jpg` — thermal_printed

- provenance: generated: rendered receipt (Consolas), low resolution (45%)
- size ? KB · ? · latency 20267 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `hand-1-segoe-print.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Segoe Print
- size ? KB · ? · latency 12164 ms
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `hand-2-ink-free.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Ink Free
- size ? KB · ? · latency 12290 ms
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `hand-3-segoe-script.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Segoe Script
- size ? KB · ? · latency 19511 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 84500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 6) · invented units 0 · field errors 0

#### `hand-4-bradley.jpg` — handwritten_font

- provenance: generated: rendered with a handwriting-style font, NOT human handwriting — Bradley Hand
- size ? KB · ? · latency 11859 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 84500 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 6) · invented units 0 · field errors 0

#### `blur-1-neat-r3.5.jpg` — blurred

- provenance: generated: Gaussian blur of test-receipt-1-neat.png (r=3.5)
- size ? KB · ? · latency 13802 ms
- document_type `stock_ledger` · confidence **high** · 10 items · total_amount 645500 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `blur-2-medium-r3.jpg` — blurred

- provenance: generated: Gaussian blur of test-receipt-3-medium.png (r=3.0)
- size ? KB · ? · latency 20264 ms
- document_type `receipt` · confidence **high** · 10 items · total_amount 343000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 10) · invented units 1 · field errors 1 · **SILENT ERROR**

#### `blur-3-thermal-r2.2.jpg` — blurred

- provenance: generated: Gaussian blur of thermal-1-clean (r=2.2)
- size ? KB · ? · latency 25233 ms
- document_type `receipt` · confidence **high** · 8 items · total_amount 272000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 8/8 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `angle-1-neat.jpg` — angled

- provenance: generated: perspective warp of test-receipt-1-neat.png
- size ? KB · ? · latency 21550 ms
- document_type `stock_ledger` · confidence **medium** · 10 items · total_amount 645500 · ambiguities 2
- confirmation gate: **TRIGGERED**
- score: lines 10/10 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `angle-2-thermal.jpg` — angled

- provenance: generated: perspective warp of a clean render of thermal B
- size ? KB · ? · latency 12770 ms
- document_type `receipt` · confidence **high** · 6 items · total_amount 237000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 6/6 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `angle-3-hand-segoe.jpg` — angled

- provenance: generated: perspective warp of hand-1-segoe-print
- size ? KB · ? · latency 43114 ms
- document_type `receipt` · confidence **high** · 7 items · total_amount 332000 · ambiguities 0
- confirmation gate: **not triggered (auto-commit)**
- score: lines 7/7 · extra 0 · total ok · fabricated unit prices 0 (derived 0) · invented units 0 · field errors 0

#### `control-unreadable.jpg` — unreadable_control

- provenance: generated: thermal B blurred (r=11) and noised past legibility — deliberately unreadable
- size ? KB · ? · latency 8188 ms
- document_type `other` · confidence **low** · 0 items · total_amount 0 · ambiguities 1
- confirmation gate: **TRIGGERED**
- score: 0 fabricated items · control OK

### M1-T3 — tuning applied, before / after (part 1: offline, 2026-09-03)

**What changed (commit after `eb506c1`):**

1. **Schema + prompt** (`backend/app/ai/vision.py`): each item now carries `unit_written` and
   `unit_price_written`. The prompt tells the model to copy a unit only when one is written
   next to the quantity, never to infer one from the product name ("Minyak goreng 2L" ×1 is
   one item, not two liters), and that a unit price may be derived from line total ÷
   quantity only with `unit_price_written=false`.
2. **Server-side guards** (`normalize_parse`, applied to every fresh parse and every
   revision): a unit the model did not see written is dropped (commit falls back to
   `pcs`); a unit price with no written basis and no line total is zeroed; a line whose
   quantity × unit price contradicts its line total, or a written total that contradicts the
   sum of the lines, becomes an explicit ambiguity — which the unchanged gate rule turns
   into a question to the owner. 11 pure-function tests in
   `backend/tests/test_vision_normalize.py`, including legacy payloads without the flags.
3. **Scoring refinement** (`scripts/vision-baseline.py`): a unit price equal to line total ÷
   quantity on a document that shows no unit prices is now *derived* (reported, not an
   error) — it is arithmetic, not a guess, and the resulting cost per unit is correct. `''`
   and the generic `pcs` are acceptable where no unit is written. `--rescore` re-applies
   the rules to a saved run; `--normalize` additionally runs the saved outputs through the
   new guards. The first two "Re-score" sections above predate the manifest fix for the
   blurred/angled copies of the neat page; the last two are current.

**Before (run 1, untuned prompt, refined scoring):** 134/134 lines, 17/17 totals, gate 4/18,
**3 silent errors** — all invented units: the glare page (7: `pack`, `bottle`, `sachet`…),
its blurred copy (1), and the faded thermal receipt (3: `kg`/`liter`/`kg` read off product
names). Zero fabricated prices under the refined rule (every derived price was consistent).

**After, offline estimate (same outputs + `normalize_parse`):** gate 6/18, **3 silent
errors** — unchanged, and expectedly so: the old outputs carry no `unit_written` flag, so the
guard treats their units as written. The sum check did fire deterministically on all three
copies of the neat page (645.500 written vs 635.500 summed), which the model itself only
caught on the angled copy.

**After, live (new schema):** **NOT YET RUN.** The Google free tier allows 20 requests/day
per model and today's 20 were spent on the baseline. The three remaining silent errors are
exactly the class the `unit_written` flag targets, so the live number is the test of whether
the model honours the flag. The done-criterion — "the confirmation gate triggers on every
low-confidence read" — is therefore **not claimed**. Next run: the same 18 images,
`scripts/vision-baseline.py --label "M1-T3 after (schema flags + guards)"`, once quota
resets or billing is enabled; then this section gets the live table.
