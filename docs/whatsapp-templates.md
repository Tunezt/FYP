# WhatsApp message templates — submit both for review on day one

Meta template review can take 24h+. Both templates below are final and ready to paste
into **Meta Business Manager → WhatsApp → Message Templates → Create Template** the
moment the Meta App + WhatsApp product exist. Until they are approved, Phase 5
(proactive alerts) and the dashboard OTP flow cannot be verified live.

> **Status: SUBMISSION BLOCKED — no Meta developer account/App credentials provisioned
> yet.** Tracked in `docs/progress.md`. Everything below is prepared so submission is a
> five-minute task once credentials exist.

---

## 1. `login_otp` — Authentication category (more urgent: needed by the dashboard login flow)

Authentication templates are Meta's purpose-built OTP category and deliver regardless of
the 24-hour session window — required because a brand-new owner has never messaged the
business number, so a free-form OTP would be rejected.

- **Name:** `login_otp`
- **Category:** Authentication
- **Languages:** `id` (Indonesian), `en` (English) — submit both
- **Code delivery:** Copy code
- **Body (Meta auto-generates for Authentication):** `{{1}} adalah kode verifikasi Anda.` / `{{1}} is your verification code.`
- **Footer (optional, recommended):** expiry note, 10 minutes.

Backend env: `WHATSAPP_OTP_TEMPLATE=login_otp`.

## 2. `business_alert` — Utility category (needed by Phase 5 nightly alerts)

- **Name:** `business_alert`
- **Category:** Utility
- **Languages:** `id`, `en`
- **Body:**
  - `id`: `⚠️ Peringatan {{1}} untuk {{2}}: {{3}}. Balas pesan ini untuk detail.`
  - `en`: `⚠️ {{1}} alert for {{2}}: {{3}}. Reply to this message for details.`
- **Sample values (required by review):** `{{1}} = stok menipis`, `{{2}} = Kopi Kenangan Senja`, `{{3}} = Arabica tinggal ±2 hari lagi`

Variables: `{{1}}` alert kind, `{{2}}` business name, `{{3}}` one-line detail. Replying
opens a 24-hour session window, so the follow-up conversation is free-form.

Backend env: `WHATSAPP_ALERT_TEMPLATE=business_alert`.

---

## Webhook setup reminder (same Meta dashboard visit)

- Callback URL: `https://<railway-or-tunnel>/webhooks/whatsapp`
- Verify token: value of `WHATSAPP_VERIFY_TOKEN`
- Subscribe to the **`messages`** field
- Allow-list up to 5 recipient phone numbers on the free test number (owner demo phone included)
