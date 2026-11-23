# Demo script — Poernama (rehearsed flow, ±8 minutes)

The demo runs on the live business **Poernama Demo** (invented data, never the real restaurant), owned by the developer's own WhatsApp number. Written for the FYP presentation of 26 Nov 2026; every figure below is what the seed produces.

## Before the demo (do this the evening before, ~15 min)

1. **Re-seed the live demo** so the dates move up to now, the alerts exist and today has sales (the owner runs this in PowerShell, with `DATABASE_URL` from `backend/.env.production`):
   `python -m app.seed --phone 0811… --name "Poernama Demo" --owner "Pemilik" --yes`
   The run ends with `Nightly pass: 2 alert(s) ready: ['anomaly', 'stockout_risk']`. Each run makes a new business id, so log in again afterwards.
2. **Send the staged receipt once, now.** From the demo number on WhatsApp send `docs/demo-receipt-toko-manis.jpg` (Toko Manis: Gula Aren 5 kg, Susu UHT 12 liter, Biji Arabica 2 kg, total Rp 684.000), then reply **YA**. This gives the receipt-history question (Act 2) something to find, and fills Keuangan → Nota. The seed itself never calls Gemini, so this one photo is the only setup that does.
3. The WhatsApp number is on two cafes (the family restaurant and the demo): the first message gets a list "Balas angka atau nama usahanya". Reply **demo**. `ganti toko` switches back.
4. Gemini: use the billed key (the free tier allows only a few requests a minute and a few a day, enough for rehearsal but not for a live room). Space the bot questions a few seconds apart.
5. Open: dashboard `poernama-zeta.vercel.app/login` (owner number + PIN `1234`; or the WhatsApp code), the kiosk tab (Settings → Layar kasir), and WhatsApp on the phone.

**Credentials:** owner PIN `1234`, staff Sari `2345`, Budi `3456`.

---

## Act 1 — the problem & the POS (2 min)

> "Ibu Diah runs a café. Her staff aren't going to learn an ERP. So the till is one screen."

1. Kiosk tab → tap **Sari** → PIN `2345` → the sale screen. If it says "Buka shift dulu", type the float (`200000`) and tap **Buka shift**: the till takes no money until a shift is open.
2. Sell 2× Es Kopi Susu. Point out: no stock number on it. It is **made to order**: the till takes the beans, milk and sugar out of stock instead.
3. The stock guard: add **Croissant** and keep tapping **+**. Croissant has 12 on the shelf, so the + greys out at 12 and the line says "Stok tidak cukup untuk menambah lagi". The screen is only the first line of defence: the same rule is an atomic SQL guard, so an order for 13 sent straight to the till API is refused with 409 "Stok tidak cukup — Croissant tersisa 12" (`tests/test_menu.py`, or the API call in Act 5). Tap **Kosongkan** afterwards.
4. Big targets, forced light theme for a bright counter.

## Act 2 — the owner never opens an app (3 min)

On WhatsApp (owner phone), send in sequence:

| Send | What it demonstrates |
|---|---|
| `stok es kopi susu berapa?` | made-to-order wording: "dibuat saat dipesan", about 300 porsi left (measured 298 after the evening-before receipt and the Act 1 sale), limited by Susu UHT. Not a frozen number. |
| `stok croissant berapa?` | a stocked item: 12 pcs. The number the till guard used. |
| `how much did I sell today?` | same pipeline, English: today has sales up to the current hour. |
| `bulan ini untung ga?` | multi-table aggregation (revenue, COGS, expenses, net) |
| `stok gula aren ternyata tinggal 3` | manual stock correction, confirmed back |
| *photo `docs/demo-receipt-toko-manis.jpg`* | immediate ack → vision parse → confirmation gate: "Ini yang aku baca… Balas YA" |
| `ya tapi gulanya 3 kg bukan 5` | correction flow: the revised read shows Gula Aren 3 kg and the **total recomputed to Rp 608.000** (it was 684.000) |
| `YA` | commits: expense + stock + receipt, all in one reply, with the corrected total |
| `pernah beli gula aren dari toko manis?` | RAG over the embedded receipt history (pgvector): answers from both receipts, the one sent the evening before (5 kg) and the one just confirmed (3 kg). The same photo saved twice is two purchases to the system: there is no duplicate-receipt check yet. |

## Act 3 — proactive, not just reactive (1 min)

1. Dashboard → **Peringatan**: two alerts already exist: the sales spike of the day before ("Penjualan <hari> <tanggal> Rp 1.447.000 — jauh di atas normal", against a normal of about Rp 475.000, z = 7) and **Croissant**: stock lasts about 5 days, next delivery usually 7. Es Kopi Susu and Americano do not appear: made-to-order items are never alerted on their own number.
2. One line of theory: the same nightly pass sends these to the owner's WhatsApp. The seed runs the rules but never sends (check the Railway cron `30 16 * * *` exists if the real send is shown).

## Act 4 — the dashboard (2 min)

1. Log in: owner phone number + owner PIN → Ringkasan: sales today, this month, the alert card (two alerts).
2. First-run tour fires (if reset), otherwise walk: today + trend chart, bulan ini net, **perlu perhatian**.
3. **Stok**: Es Kopi Susu and Americano say "dibuat saat dipesan" with "±N porsi" and "terbatas: <bahan>"; Croissant shows 12 pcs.
4. Penjualan → the Act 1 sale at the top of the history, recorded by Sari.
5. Keuangan → the receipt from Act 2 as an expense with the 🧾 provenance and thumbnail; P&L bars.

## Act 5 — the part graders ask about (1 min)

- Role separation: paste the POS token into an `/api/overview` call → 403 (or cite `tests/test_role_separation.py`).
- RLS: every query runs inside `SET LOCAL app.current_business_id` from the verified JWT; policies on every business table; cross-tenant tests in `tests/test_db_integration.py`.
- `request_logs`: raw query + classified intent + latency for every interaction, so the CP2 evaluation dataset accumulates by itself.

## Reset between rehearsals

Re-run the seed command from "Before the demo" (wipes and recreates the demo café, new business id), then re-send the staged receipt once and reply YA. Local rehearsal without the live site: `cd backend && python -m app.seed` creates **Poernama** (owner 0812-000-1111).

To re-trigger the first-run tour: `update businesses set onboarding_completed_at = null where name = 'Poernama Demo';`
