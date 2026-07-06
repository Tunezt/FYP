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

## Webhooks

| Method | Path | Notes |
|---|---|---|
| GET | `/webhooks/whatsapp` | Meta verification handshake |
| POST | `/webhooks/whatsapp` | HMAC-validated; 200 immediately; processing in background (text → classify/tools/RAG, image → vision + confirmation gate, document → Excel import) |

## Jobs

`python -m app.jobs.nightly` — Railway cron (suggested `30 16 * * *` UTC = 23:30 WIB):
baseline refresh → z-score anomalies → velocity sweep → one Utility-template alert
message per business with unsent alerts.
