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
