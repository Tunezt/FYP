# API contract (summary)

Auth: `Authorization: Bearer <JWT>` — scopes: `owner` (dashboard), `pos` (kiosk),
`pos-pairing` (kiosk boot only), `register` (one-shot, post-OTP). Wrong scope → 403,
missing/invalid → 401. Interactive docs at `/docs` (FastAPI/OpenAPI).

## Public / auth

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | healthcheck |
| POST | `/auth/request-otp` | `{phone}` → WhatsApp Authentication-template OTP (dev: code logged) |
| POST | `/auth/verify-otp` | `{phone, code}` → owner JWT **or** `registration_token` |
| POST | `/auth/register` | registration token + business profile + owner PIN → owner JWT |

## Owner (scope=owner)

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/auth/staff` | list / create (name + 4-digit PIN) |
| POST | `/auth/staff/{id}/deactivate` | owner row protected |
| POST | `/auth/pos-pairing` | long-lived kiosk pairing token → `/pos/{token}` |
| GET | `/api/overview` | today, month P&L, alert + low-stock counts |
| GET | `/api/sales-trend?days=7..90` | zero-filled daily series, business-local days |
| GET | `/api/sales?page=&page_size=` | paginated, item+staff names joined |
| GET/POST | `/api/items` | inventory incl. batched velocity / create |
| PATCH | `/api/items/{id}` | partial update |
| GET | `/api/expenses?page=` | paginated |
| GET | `/api/pnl?months=1..12` | monthly revenue/expenses/net |
| GET | `/api/alerts?limit=` · POST `/api/alerts/{id}/ack` | |
| GET | `/api/receipts?page=` | paginated, short-lived signed image URLs |
| GET/PATCH | `/api/business` · POST `/api/business/complete-onboarding` | |
| GET | `/api/stock-template` | template-stok.xlsx download |

## POS

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/pos/business/{pairing_token}` | pairing token in path | business name + active staff |
| POST | `/pos/login` | pairing token in body | `{staff_id, pin}` → pos JWT |
| GET | `/pos/items` | pos | |
| POST | `/pos/sales` | pos | atomic decrement; 409 on insufficient stock; triggers velocity check |
| POST | `/pos/orders` | pos | multi-line order + payments (cash/qris/transfer/card/ewallet/other); payments must equal total (422); per-line atomic stock guard, all-or-nothing (409); writes order, lines with `unit_cost_at_sale`, payments, stock movements |
| POST | `/pos/orders/{id}/void` | pos | `{manager_pin, note?}` — owner PIN; reversing lines, payments and `sale_void` stock movements; original untouched; 403 wrong PIN, 409 already reversed |
| POST | `/pos/orders/{id}/refund` | pos | `{manager_pin, restock?, note?}` — like void with `refund` movements; `restock=false` reverses money only |
| GET | `/pos/orders/{id}/receipt` | pos | printable receipt: lines with size + modifiers as sold (snapshots), payments, totals; voided orders include reversing lines |
| GET | `/api/items/{id}/variants` · POST same · PATCH `/api/variants/{id}` | owner | sizes/options with own prices; exactly one default per item, default mirrors the item's prices both ways |
| GET | `/api/uoms` · POST same · GET/POST `/api/uom-conversions` | owner | units of measure per business (standard set seeded at registration) and conversion factors; items carry `uom_id` |
| GET/POST | `/api/suppliers` · PATCH `/api/suppliers/{id}` · GET `/api/suppliers/{id}/history` | owner | supplier contact details (deactivate, never delete); history derived from receipt photos linked by supplier name (never auto-created) |
| GET/POST | `/api/purchase-orders` · GET/PATCH `/api/purchase-orders/{id}` · POST/PATCH/DELETE `.../lines[/{line_id}]` · POST `.../order` · POST `.../cancel` | owner | draft → ordered → partially_received → received, or cancelled (only while nothing received); lines editable only in draft; a PO never touches stock (receiving is M5-T3) |
| GET/POST | `/api/goods-receipts` · GET `/api/goods-receipts/{id}` | owner | receive goods (from a PO line or free): stock in + `purchase` ledger row + moving-average cost per line, quantities/costs converted to the item's unit; partial advances the PO to partially_received, complete → received; over-receipt 409 unless `allow_over_receipt` |
| GET/POST | `/api/accounts` · PATCH `/api/accounts/{id}` | owner | chart of accounts: 26 seeded Indonesian SME accounts (system, renameable, never deactivated) plus merchant accounts; types asset/liability/equity/revenue/expense |
| GET | `/api/posting-rules` · PATCH `/api/posting-rules/{id}` | owner | which accounts each event component debits/credits (data, seeded per business, editable: re-point to another account code, deactivate) |
| POST | `/api/catalog-import` | owner | raw .xlsx body (≤ 5 MB): Barang / Varian / Pilihan / Satuan / Konversi / Resep sheets; validated in full first — any bad row → 422 naming every bad row, nothing written; else counts |
| GET | `/api/catalog-template` | owner | multi-sheet starter workbook for the catalogue import |
| GET | `/api/variants/{id}/recipe` · POST same (upsert a component) · PATCH `/api/recipe-lines/{id}` | owner | recipe keyed on the variant: component item, quantity, unit; selling a variant with a recipe consumes components (converted to their units) instead of its own stock |
| GET | `/api/items/{id}/modifier-groups` · POST same · PATCH `/api/modifier-groups/{id}` · POST `/api/modifier-groups/{id}/modifiers` · PATCH `/api/modifiers/{id}` | owner | single/multi select, required/optional (min/max); modifiers priced or free |

## Webhooks

| Method | Path | Notes |
|---|---|---|
| GET | `/webhooks/whatsapp` | Meta verification handshake |
| POST | `/webhooks/whatsapp` | HMAC-validated; 200 immediately; processing in background (text → classify/tools/RAG, image → vision + confirmation gate, document → Excel import) |

## Jobs

`python -m app.jobs.nightly` — Railway cron (suggested `30 16 * * *` UTC = 23:30 WIB):
baseline refresh → z-score anomalies → velocity sweep → one Utility-template alert
message per business with unsent alerts.
