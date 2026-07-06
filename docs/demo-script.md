# Demo script — Warung Pintar (rehearsed flow, ±8 minutes)

## Prerequisites (one-time, ~30 min once credentials exist)

1. Supabase project → set `DATABASE_URL` → `alembic upgrade head` → create private `receipts` bucket.
2. Google AI Studio key → `GOOGLE_API_KEY`.
3. Meta App + WhatsApp test number → tokens in `.env`, **both templates approved** (`docs/whatsapp-templates.md`), demo phone allow-listed, webhook registered via tunnel/Railway URL.
4. `python -m app.seed` — creates **Kopi Kenangan Senja** with 30 days of history and a planted sales spike *yesterday*.
5. Backend on Railway (or `uvicorn app.main:app`), frontend on Vercel (or `npm run dev`), one tablet/laptop tab for the POS, phone with WhatsApp for the owner number **+62 812-0000-1111**.
6. Night before the demo: run `python -m app.jobs.nightly` once so alerts exist and a template message has been delivered.

**Demo credentials:** owner PIN `1234`, staff Sari `2345`, Budi `3456`.

---

## Act 1 — the problem & the POS (2 min)

> "Ibu Ratna runs a café. Her staff aren't going to learn an ERP. So the till is one screen."

1. Open the pairing link (Settings → Layar kasir → generate, or reuse the kiosk tab).
2. Tap **Sari** → PIN `2345` → sale screen boots instantly.
3. Sell 2× Es Kopi Susu → success flash shows total + remaining stock.
4. Point out: big targets, forced light theme for a bright counter, insufficient-stock is impossible (sell more than remaining to show the 409 message — atomic SQL guard, not a UI check).

## Act 2 — the owner never opens an app (3 min)

On WhatsApp (owner phone), send in sequence:

| Send | What it demonstrates |
|---|---|
| `stok es kopi susu berapa?` | function calling → live number (reflects the sale from Act 1!) |
| `how much did I sell today?` | same pipeline, English — language handled in prompts, one pipeline |
| `bulan ini untung ga?` | multi-table aggregation (revenue, COGS, expenses, net) |
| `stok gula aren ternyata tinggal 3` | manual stock correction, confirmed back |
| *photo of a supplier receipt* | immediate ack → Gemini Pro vision parse → **confirmation gate**: "Ini yang aku baca… Balas YA" |
| `ya tapi gulanya 3 kg bukan 2` | correction flow — revised parse, re-asks |
| `YA` | commits: expense + stock + receipt, all in one reply |
| `pernah beli gula dari toko sinar?` | RAG over embedded receipt history (pgvector) |

## Act 3 — proactive, not just reactive (1 min)

1. Show the **template WhatsApp alert** delivered by last night's cron (stok menipis / anomali).
2. One line of theory: free-form messages die outside Meta's 24-hour window — this is a pre-approved Utility template, which is why it can arrive unprompted.

## Act 4 — the dashboard (2 min)

1. Log in: phone number → OTP arrives on WhatsApp (Authentication template) → Ringkasan.
2. First-run tour fires (if reset) — otherwise walk: today + trend chart, bulan ini net, **perlu perhatian** (the sale from Act 1 may have pushed something below 3 days).
3. Penjualan → the Act 1 sale at the top of the paginated history, recorded by Sari.
4. Keuangan → the receipt from Act 2 as an expense with 🧾 provenance + thumbnail; P&L bars.
5. Peringatan → the anomaly from the planted spike; hover a **?** HelpTip — "explanations everywhere, zero manual".

## Act 5 — the part graders ask about (1 min)

- Role separation: paste the POS token into an `/api/overview` call → 403 (or just cite `tests/test_role_separation.py` — 66 tests).
- RLS: every query runs inside `SET LOCAL app.current_business_id` from the verified JWT; policies on all 9 business tables; cross-tenant read/write/no-context tests in `tests/test_db_integration.py`.
- `request_logs`: raw query + classified intent + latency for every interaction → the CP2 evaluation dataset accumulates by itself.

## Reset between rehearsals

```bash
cd backend && python -m app.seed        # wipes + recreates the demo café
```
To re-trigger the first-run tour: `update businesses set onboarding_completed_at = null where owner_phone = '628120001111';`
