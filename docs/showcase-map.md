# Showcase map: what to show, where, and what it proves

The owner's walk-through guide for the demo business **Poernama Demo**. The seed (`backend/app/seed.py`, data in `backend/app/seed_data.py`) fills every page, till screen and bot tool with invented but believable café data, so any feature can be opened without setting anything up first. `docs/demo-script.md` is the 8-minute rehearsed path; this file is the map of everything else.

**Logins** (live and local): owner PIN `1234`; till staff Sari `2345`, Budi `3456`, manager Dewi `4567`. Owner phone on the live demo: 628115819400. Rudi is a deactivated cashier (never listed at the till).

**CP1 objectives** (numbered as in `FYP-supervisor-brief.md` §3): **O2a** function-calling tools · **O2b** RAG over receipts · **O2c** vision + confirmation before saving · **O2d** proactive alerts · **O2e** periodic summaries · **O2f** onboarding by Excel or stock-book photo · **O2h** owner-only bot · **O3a** dashboard with insights · **O3b** staff POS · **O4a** integrated system, one database.

What the history looks like (so you know what "normal" is): two months of sales, about 20 a day (weekends 26), one planted busy day yesterday (about 3× normal), today's sales up to the hour the seed ran, a slow growth trend, and ingredient costs up 75% on coffee drinks in the last 7 days (the margin alert). Dates move up to "today" every time the seed runs.

## 1. Dashboard (`/overview` and the sidebar)

| Feature | Where to see it | What the supervisor should notice | Proves |
|---|---|---|---|
| Ringkasan: today, month profit, trend chart (4 ranges), low stock, alert card | Dashboard → Ringkasan; tap Hari Ini / Minggu Ini / Bulan Ini / Tahun Ini | Yesterday's spike on the chart; "vs periode sama bulan lalu" sets 1-N Nov against 1-N Oct (fin-fair-compare); today is set against yesterday up to the same hour; Bubuk Matcha listed under Stok menipis; the alert card shows the four unread alerts | O3a |
| Penjualan: 7 / 30 / 90-day chart, history by day, filters | Dashboard → Penjualan | Each sale says who rang it and which regular bought ("oleh Dewi · Andi Wijaya"); daily "Pesanan 042" numbers; yesterday 55+ transactions | O3a, O3b |
| Void / refund from the dashboard | Penjualan → "Batalkan atau kembalikan transaksi" | Needs the owner's or manager's PIN; leaves a reversing row, never deletes | O3a, O4a |
| Stok: value, items needing attention, made-to-order | Dashboard → Stok | Es Kopi Susu / Cappuccino / Matcha / Teh Tarik / Nasi Goreng say "dibuat saat dipesan" with "±N porsi · terbatas: <bahan>"; Croissant and Pisang Goreng show pcs and a per-day rate; Bubuk Matcha is under minimum | O3a, O2d |
| Item sheet: sizes, options, recipe | Stok → tap an item (Es Kopi Susu, Cappuccino, Matcha Latte) | Standar/Large prices, option groups (Gula, Tambahan), recipe per size (a Large uses more beans and milk) | O3a |
| Keuangan: profit card + 6-month chart | Dashboard → Keuangan (top) | September, October, November bars; November so far | O3a |
| Shift & kas laci | Keuangan → Shift & kas laci | Yesterday: **Sari −Rp 5.000** ("kurang 5rb, mungkin kembalian"); two days ago **Budi −Rp 20.000**; a week ago Sari +Rp 2.000; Dewi always pas; cash taken out of a drawer ("Kas keluar · Es batu") | O3b, O4a |
| Otorisasi manajer | Keuangan → Otorisasi manajer | A trail of cancelled sales, nearly all **requested by Budi**, each approved by Dewi or the owner with a reason; one refund by Sari | O3a, O3b |
| Pengeluaran + Nota terakhir | Keuangan → bottom half | Four photographed receipts with thumbnails (Toko Supply ×2, Grosir Barokah, Pak Udin Sayur) saved as 🧾 expenses, beside manual ones (rent, electricity, flyers) | O2c, O3a |
| Pelanggan | Dashboard → Pelanggan | 8 customers: Andi (82 visits, points spent once), Rina, Bayu, Maya (birthday this week), a brand-new one (Wulan), and **Hendra, last seen 29 Oct** (a lapsed regular); open one to see the points ledger (earned, redeemed, a birthday gift, a correction) | O3a |
| Promo | Dashboard → Promo | Four promos: BOGO Kopi Arabica (afternoons, used ~20×), Happy Hour Matcha 15%, Jumat Nasi Goreng 10%, and one that ended (shows "mati", can be switched on); each with times used and cost | O3a |
| Voucher | Promo → Voucher | `SELAMAT-DATANG` used 3 of 100; a batch of ten single-use `PRNM-…` codes, three already used | O3a |
| Peringatan | Dashboard → Peringatan | **Perlu ditindak (4):** Biji Arabica from Toko Supply +13.8% (Rp 145.000 → 165.000), Croissant runs out before the next delivery, Budi's cancelled sales, margin drop. **Sudah dibaca (4):** the busy-day spike and three older ones. One of every alert type; tap "beres" to move one across | O2d |
| Pengaturan → Profil usaha | Settings → Profil usaha | Business day starts 00.00, "Wajib buka shift" on | O3b |
| Pengaturan → WhatsApp asisten | Settings → WhatsApp asisten | The owner's number; add a second number here (not pre-filled: a made-up number would receive real alerts) | O2h |
| Pengaturan → Data di struk | Settings → Data di struk | Wordmark logo, address, phone, Instagram, footer line "WiFi: Poernama-Tamu…", receipt choice "tanya dulu" | O3b |
| Pengaturan → Pajak & service | Settings → Pajak & service | PB1 10% on top, service charge by order type, rounding, discount needs a PIN | O3b |
| Pengaturan → Program poin | Settings → Program poin | On: Rp 1.000 = 1 poin, 1 poin = Rp 100, minimum 10 | O3a |
| Pengaturan → Staf kasir | Settings → Staf kasir | Owner, Sari, Budi, **Dewi (manajer, "bisa setujui batal, refund & diskon")**, Rudi (nonaktif) | O3b |
| Pengaturan → Layar kasir | Settings → Layar kasir | "Buat tautan kasir" makes the tablet link; one-tablet lock switch | O3b |
| Pengaturan → Printer | Settings → Printer | Print destinations are set for every product ("Bar 7 · Dapur 3 · Semua produk sudah punya tujuan"). The bridge's own status (siap / kertas habis) comes from a running bridge: **it is not seeded** (see "What the seed cannot show") | O3b |
| Pengaturan → Menu QR | Settings → Menu QR | "Buat tautan menu QR" gives the table link; the menu it opens is in section 2 | O3b |
| Pengaturan → Template stok / Impor katalog | Settings → bottom | Downloads of the Excel templates (stock, catalogue) and the catalogue import | O2f |

## 2. Till, kitchen and guest menu (Settings → Layar kasir → link; kitchen and QR menu links come from the same page)

| Feature | Where to see it | What the supervisor should notice | Proves |
|---|---|---|---|
| Staff door | `/kasir` or the till link → "Siapa yang jaga?" | Four active staff, PIN pad; Rudi is not there | O3b |
| Shift gate | Pick Sari `2345` → "Buka shift dulu" | The till takes no money until the float is counted (type 200000) | O3b |
| Sell screen | Pesanan baru | Categories (Kopi, Non-kopi, Makanan, Camilan, Dessert); "ada pilihan" on items with sizes | O3b |
| Item options | Tap Cappuccino | Size (required), extras, note; "dibuat saat dipesan" | O3b |
| Stock guard | Tap Croissant, keep pressing + | Stops at 12: "Stok tidak cukup untuk menambah lagi" | O3b, O4a |
| Pesanan aktif | Pesanan aktif tab | **Two guests ordered from the table QR and wait for payment** (Bu Ratna, Meja 4; Mas Eko, Meja 7), marked "Pesanan QR · Belum dibayar" | O3b |
| Riwayat transaksi | Riwayat transaksi tab | Today's sales so far with numbers, cashier and a search box; open one to void/refund with a PIN | O3b |
| Kitchen display | `/kitchen/<link>`, sign in as Dewi `4567` | Tickets in Baru / Disiapkan / Siap diambil, by table or order number; "Sudah diserahkan" lists the done ones | O3b |
| Guest QR menu | `/menu/<link>` from Settings → Menu QR | Menu by category with prices from, "pilih ukuran"; no stock, cost or dashboard | O3b |

## 3. WhatsApp assistant: the 15 tools

Send from 628115819400 (reply **demo** if the number also owns another café). Every question below was sent through the real path on `gemini-3.5-flash-lite` against this seed; the reply column is what came back (section 6).

| Tool | Send | What the supervisor should notice | Proves |
|---|---|---|---|
| get_stock | `stok bubuk matcha berapa?` | A raw material, in kg, under its minimum | O2a |
| get_stock (made to order) | `stok es kopi susu berapa?` | "dibuat saat dipesan", about 200 porsi, limited by Susu UHT (about 300 once the staged receipt is saved) | O2a |
| get_sales_summary | `penjualan minggu ini berapa?` | Same number as Penjualan | O2a, O4a |
| compare_periods | `bandingkan penjualan bulan ini dengan bulan lalu` | A full October to compare against | O2a |
| get_profit | `bulan ini untung ga?` / `how much profit this month?` | Revenue, cost of goods, expenses (rent, receipts), net; English question gets an English answer | O2a |
| get_low_stock | `apa yang hampir habis?` | Bubuk Matcha; Croissant's days left | O2a, O2d |
| correct_stock | `stok gula aren ternyata tinggal 3` | A confirmed correction, written to the stock ledger | O2a |
| record_expense | `catat pengeluaran parkir 15000` | Lands in Keuangan → Pengeluaran | O2a |
| search_history (RAG) | `pernah beli teh hitam dari grosir barokah?` | Answers from the stored receipt (12 Nov, 1 kg, Rp 90.000); the receipt vectors are in the seed, so this works without sending a photo first | O2b |
| get_purchase_history | `belanja dari toko supply bulan ini totalnya berapa?` | Totals per supplier from receipts and goods receipts | O2a |
| get_supplier_prices | `harga biji arabica dari toko supply naik berapa persen?` | Price history Rp 140.000 → 145.000 → **165.000** | O2a, O2d |
| get_recipe_cost | `modal satu es kopi susu berapa?` | Cost from the recipe (beans, milk, sugar), not a typed number | O2a |
| get_shift_summary | `ada selisih kas di shift kemarin? siapa kasirnya?` | Sari short Rp 5.000, Dewi and Budi pas | O2a |
| get_customer_summary | `siapa pelanggan yang paling sering datang? berapa poinnya?` | Andi Wijaya, visits and points | O2a |
| get_promo_performance | `promo apa yang paling sering dipakai bulan ini?` | The Kopi Arabica BOGO and its count | O2a |
| draft_purchase_order | `buatkan draft pesanan biji arabica 5 kg ke toko supply` | A draft PO for Toko Supply at the latest price (Rp 165.000/kg, subtotal Rp 825.000). The reply says it is a draft only, to send to the supplier yourself (the dashboard has no purchase-order page: the PO list is the API only), and to photo the nota when the goods arrive (fin-po-link) | O2a |

Purchase orders (no dashboard page; asked by WhatsApp or the API): 6 in every status: received (2), partially received (cups: 600 of 1.000 arrived), ordered (milk, due tomorrow), cancelled (eggs, "telur kosong"), draft (beans at the new price).

## 4. WhatsApp: receipts and proactive messages

| Feature | Where | What the supervisor should notice | Proves |
|---|---|---|---|
| Receipt photo → read-back → YA | Send `docs/demo-receipt-toko-supply.jpg` | "Ini yang aku baca… Balas YA"; supplier, 3 lines, Rp 684.000 | O2c |
| Correction | Reply `ya tapi gulanya 3 kg bukan 5` | Total recomputed to Rp 608.000 | O2c |
| Saved | Reply `YA` | Expense + stock + receipt in one reply; it appears under Nota terakhir | O2c, O4a |
| Duplicate warning | Send the same photo again | "⚠️ Nota ini mirip nota Toko Supply 20 Nov Rp 684.000 yang sudah tersimpan…" | O2c |
| Pending YA | Send a photo and wait | The read-back waits up to 60 minutes; it is a short-lived state, so it cannot be seeded | O2c |
| Daily summary | Sent every night after the alerts | "📊 Ringkasan hari ini… Dibanding kemarin… Terlaris…" | O2e |
| Weekly summary | Every Monday | "📅 Ringkasan minggu lalu…" | O2e |
| Alert messages | Nightly 23:30 WIB | The four unread alerts above go to the owner's WhatsApp on the next run (the seed itself sends nothing) | O2d |
| Stock-book photo / Excel | Settings → Template stok, then send the file | Onboarding path | O2f |

The five alert rules and the data that makes each one fire (all present at once, re-checked on three different seed days by `tests/test_showcase_seed.py`): **anomaly** (yesterday ≈ 3× the 30-day normal) · **supplier_price** (Biji Arabica +13.8% in a goods receipt from yesterday) · **stockout_risk** (Croissant ≈ 3-5 days left, delivery gap 7) · **void_rate** (Budi: more than 10% of his sales cancelled, others near 0%) · **margin_drop** (gross margin of the last 7 days about 12 points under the 30 days before).

## 5. What the seed cannot show (and why)

- **Printer bridge status.** "Siap / kertas habis / tidak melapor" is the running print bridge's own report and goes stale within 90 seconds. A seeded "ready" would be a stub reported as working, so it is left out; the dashboard and till then show no printer warning.
- **A pending YA** lives 60 minutes, and a **receipt-link / QR receipt** needs a paid order; both appear when you do them live.
- **Receipt photos on live** are static files shipped with the dashboard (`frontend/public/demo-receipts`), not objects in Supabase Storage; a photo you send yourself goes to the real bucket.
- **WhatsApp sending.** The seed never messages anyone. The unread alerts are sent by the next nightly run (`NIGHTLY_BUSINESS_IDS=628115819400`).
- **Search vectors** for the four seeded receipts were made once by `scripts/demo_receipt_embeddings.py` (4 embedding calls) and are stored in `backend/app/seed_receipt_embeddings.json`. Editing a nota in `seed_data.py` needs that script re-run (a test fails until it is).
- **The first-run tour** shows only after `update businesses set onboarding_completed_at = null where name = 'Poernama Demo';`.

## 6. Evidence from the build run (24 Nov 2026, fresh local database, same code as the push)

- **Screens:** 8 dashboard pages and 11 Settings sections at 1366 px and 390 px (38 loads): 0 console errors, 0 HTTP ≥ 400, no horizontal overflow, no card left loading (`scripts/showcase_shots.py`); the till at 1024 px in 10 screens (staff door, shift gate, sell, item options, cart, active QR orders, history, QR menu, kitchen: `scripts/showcase_pos_shots.py`). Every page was opened and looked at; none empty. Folder: `C:/Users/PF4B3/Documents/Side Project/FYP-showcase-evidence/shots/`.
- **Bot:** 17 questions through `process_webhook_payload` on `gemini-3.5-flash-lite` (0 calls on `gemini-2.5-flash`), covering all 15 tools; each answer matched the database. Examples: "Harga biji Arabica dari Toko Supply naik 13,8% dari Rp 145.000 jadi Rp 165.000"; "Ada selisih Rp 5.000 di shift Sari kemarin… Shift Dewi dan Budi nihil"; "Pernah… 12 Nov 2026 ada beli 1 kg Teh Hitam seharga Rp 90.000 dari Grosir Barokah".
- **Gates:** alembic up / down -1 / up, seed (`Nightly pass: 5 alert(s) ready: ['anomaly', 'margin_drop', 'stockout_risk', 'supplier_price', 'void_rate']`), `npm run build`, pytest 881 tests in 4 parts: 880 passed first time, the one failure is a load-sensitive print-probe test that passes alone.
- **Re-seed time:** about 18 s on a local database (two months of sales are written in batches); expect a few minutes against the live database.
