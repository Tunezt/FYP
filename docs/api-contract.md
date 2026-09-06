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
| GET/POST | `/auth/staff` · PATCH `/auth/staff/{id}` | list / create (name + 4-digit PIN + `role`) / promote-demote. `role` is `staff` or `manager` (M15-T7) — `owner` is refused with 422, and the owner's own row cannot be re-roled (400). A manager approves voids, refunds and discounts at the till and gets no more than a cashier anywhere else: POS login issues `scope="pos"`, and owner scope comes only from the OTP flow |
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
| GET/PATCH | `/api/business` · POST `/api/business/complete-onboarding` | | the profile, plus `day_start_hour` (M15-T4, 0..23, PATCH outside that range → 422): the hour the business day starts. 0 is the plain calendar day; 4 puts a bill settled at 00:15 under the night before, everywhere a day is computed — the metric layer, the trend and P&L endpoints, the nightly job's baselines and alert dedup, and the dashboard's day headers |
| GET | `/api/customers?q=&page=&page_size=&include_inactive=` · POST `/api/customers` · PATCH `/api/customers/{id}` | owner | customers (M8-T1): name, phone (normalised to `62…` digits, unique per business, the WhatsApp identity), address, birthday, notes, `is_active`; each row carries derived `visits`, `total_spent` (completed orders), `last_visit` |
| GET | `/api/promos?include_inactive=` · POST `/api/promos` · PATCH `/api/promos/{id}` | owner | the promo engine (M8-T3): `{name, kind: percent_off|amount_off|bonus_item, value, item_id?, bonus_item_id?, bonus_quantity, max_per_order?, is_active, conditions: [{kind: date_range|day_of_week|time_window|min_spend|multiples, …}]}`; conditions are ANDed at the moment of sale in the business's timezone, so a promo starts and stops by itself; PATCH with `conditions` replaces the set; each row carries `applications` and `given_away` |
| GET | `/api/vouchers?q=&batch_id=&include_inactive=&limit=` · POST `/api/vouchers` · PATCH `/api/vouchers/{id}` | owner | vouchers (M8-T4): POST makes one code (`code`) or a batch (`count`, `prefix`, `batch_name`) of `percent_off` (with optional `max_discount`) or `amount_off` codes with `min_spend`, `starts_at`/`expires_at`, `max_uses` (default 1); PATCH toggles `is_active`, moves `expires_at`, raises `max_uses` (never below `uses`) |
| GET/PATCH | `/api/loyalty-settings` | owner | the points programme (M8-T2); takes effect on the next sale |
| GET | `/api/approvals?limit=&action=discount|void|refund` | owner | the override audit trail (M15-T7), newest first: `{action, approved_by, approver_name, approver_role, requested_by, requested_by_name, amount, note, order_id, created_at}`. One row per manager authorisation — a void, a refund, or a discount that needed a PIN. `approver_role` is the role held **when it was approved**, not today's; an unknown `action` is a 422 |
| GET | `/api/customers/{id}/points?limit=` · POST `/api/customers/{id}/points/adjust` | owner | the customer's points ledger newest first, and a manual adjustment `{points_delta, notes?}` — a new row, never an edit; down cannot pass zero (409) |
| GET | `/api/metrics` · `/api/metrics/{name}?period=|since=&until=&item_id=&limit=` | owner | the metric layer (M9-T1): the catalogue (name, Indonesian + English description, unit, grains, dimensions) and one metric's value over a named period in the business's timezone or an explicit range (instant metrics ignore the window); unknown name → 404, bad period/range/dimension → 422. This is the only place these numbers are computed; since M9-T3 `/api/overview`, `/api/sales-trend`, `/api/pnl` and `/api/items` are shaped from it (same figures as the assistant's tools) |
| GET/PATCH | `/api/pricing-settings` | owner | how a bill is built (M7-T4): `tax_rate` (fraction), `tax_inclusive`, `service_charge_rate`, `service_before_tax`, `rounding_unit`, `rounding_mode` (nearest/up/down), `discount_requires_pin`; takes effect on the next sale, nothing already sold is repriced |
| GET | `/api/stock-template` | template-stok.xlsx download |

## POS

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/pos/business/{pairing_token}` | pairing token in path | business name + active staff |
| POST | `/pos/login` | pairing token in body | `{staff_id, pin}` → pos JWT |
| GET | `/pos/items` | pos | |
| POST | `/pos/sales` | pos | atomic decrement; 409 on insufficient stock; triggers velocity check |
| POST | `/pos/orders` | pos | multi-line order + payments (cash/qris/transfer/card/ewallet/other); per-line atomic stock guard, all-or-nothing (409); writes order, lines with `unit_cost_at_sale`, payments, stock movements. M7-T4b: lines take `line_discount` (Rp), the body takes `bill_discount` and `manager_pin`; M8-T1: optional `customer_id` (404 if unknown or inactive); M8-T2: a payment with `method: points` spends `amount / point_value` whole points of that customer (409 when the balance does not cover it — the whole sale rolls back; 422 without a customer, for a fraction of a point, below `min_redeem_points`, or with the programme off), the sale earns `floor(paid_with_money / rupiah_per_point)` points, and the response carries `points_earned` / `points_redeemed`; M8-T3: open promos apply automatically — bonus items are added as real lines (stock, cost of goods) at list price with the whole line as promo discount, `promo_total` is on the response and the receipt (`promo_names`), and the cost posts to `4250 Diskon promo`; M8-T4: optional `voucher_code` — validated (404-shaped 422 with the reason) then the use is taken with an atomic conditional UPDATE so a single-use code redeemed by two tills at once succeeds exactly once (409 for the loser, whole sale rolled back); `voucher_total` on the response and receipt (`voucher_code`), posted as `voucher` to 4250; any discount needs the PIN when `pricing_settings.discount_requires_pin` (403 otherwise); the bill is priced by `services/pricing` (tax, service charge, rounding) and payments must equal the **rounded** total (422); response carries `discount_total`, `service_charge`, `tax_total`, `rounding` |
| GET | `/pos/customers?q=` · POST `/pos/customers` | pos | find a customer by name or phone to attach to the order, or quick-add one (`{name, phone?}`; a phone already registered is a 409) (M8-T1) |
| GET | `/pos/loyalty` | pos | the points programme (M8-T2): `is_active`, `rupiah_per_point`, `point_value`, `min_redeem_points` — so the kiosk knows whether to offer "pakai poin"; `/pos/customers` rows carry `points_balance` and `points_value` |
| POST | `/pos/quote` | pos | price the cart without selling it (M7-T4b): same lines shape and `bill_discount`, takes an optional `voucher_code` and returns `voucher_total` + `voucher_code` or `voucher_error` (why it cannot be used, in Indonesian; the quote still prices without it) (M8-T4); returns subtotal / discount / `promo_total` + `promos[]` (what applied; bonus lines come back as extra `lines` with `is_bonus` and `promo_name`, M8-T3) / service charge / tax (`tax_inclusive` says whether it is contained) / rounding / total plus `discount_requires_pin` so the kiosk asks for the PIN — the same pure function the sale uses, so screen and ledger agree |
| POST | `/pos/orders/{id}/void` | pos | `{manager_pin, note?}` — owner PIN; reversing lines, payments and `sale_void` stock movements; original untouched; 403 wrong PIN, 409 already reversed |
| POST | `/pos/orders/{id}/refund` | pos | `{manager_pin, restock?, note?}` — like void with `refund` movements; `restock=false` reverses money only |
| GET | `/pos/orders/{id}/receipt` | pos | printable receipt: lines with size + modifiers as sold (snapshots), payments, totals incl. `discount_total`, `service_charge`, `tax_total` (+ `tax_inclusive`), `rounding`; voided orders include reversing lines |
| GET | `/pos/shift` · POST `/pos/shift/open` · POST `/pos/shift/close` | pos | the cashier's till session (M7-T1): open with `{opening_float}` (one open shift per cashier, 409 otherwise); read it with live `expected_cash` = float + cash payments − cash refunds + cash in − cash out attributed to the shift (M7-T3); close with `{counted_cash, notes?}` → expected, counted and variance written once, and a non-zero variance posts `ShiftClosed` (`variance_short` / `variance_over`) in the same transaction — if the ledger refuses, the shift stays open |
| GET | `/pos/suppliers` · POST `/pos/cash` · GET `/pos/cash` | pos | cash in and out (M7-T2): `{kind: cash_in|petty_cash|supplier_payment|bank_drop, amount, reason, via?, category?, supplier_id?}` — posts to the ledger (petty cash through the expense writer) and is stamped with the cashier's open shift; GET lists the open shift's movements |
| GET | `/api/items/{id}/variants` · POST same · PATCH `/api/variants/{id}` | owner | sizes/options with own prices; exactly one default per item, default mirrors the item's prices both ways |
| GET | `/api/uoms` · POST same · GET/POST `/api/uom-conversions` | owner | units of measure per business (standard set seeded at registration) and conversion factors; items carry `uom_id` |
| GET/POST | `/api/suppliers` · PATCH `/api/suppliers/{id}` · GET `/api/suppliers/{id}/history` | owner | supplier contact details (deactivate, never delete); history derived from receipt photos linked by supplier name (never auto-created) |
| GET/POST | `/api/purchase-orders` · GET/PATCH `/api/purchase-orders/{id}` · POST/PATCH/DELETE `.../lines[/{line_id}]` · POST `.../order` · POST `.../cancel` | owner | draft → ordered → partially_received → received, or cancelled (only while nothing received); lines editable only in draft; a PO never touches stock (receiving is M5-T3) |
| GET/POST | `/api/goods-receipts` · GET `/api/goods-receipts/{id}` | owner | receive goods (from a PO line or free): stock in + `purchase` ledger row + moving-average cost per line, quantities/costs converted to the item's unit; partial advances the PO to partially_received, complete → received; over-receipt 409 unless `allow_over_receipt` |
| GET/POST | `/api/accounts` · PATCH `/api/accounts/{id}` | owner | chart of accounts: 26 seeded Indonesian SME accounts (system, renameable, never deactivated) plus merchant accounts; types asset/liability/equity/revenue/expense |
| GET | `/api/posting-rules` · PATCH `/api/posting-rules/{id}` | owner | which accounts each event component debits/credits (data, seeded per business, editable: re-point to another account code, deactivate) |
| GET | `/api/shifts?limit=` | owner | shifts newest first with staff name, float, cash taken / handed back, cash in / out, expected, counted, variance |
| GET | `/api/cash-movements?limit=` | owner | cash movements newest first with staff, supplier, direction |
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
