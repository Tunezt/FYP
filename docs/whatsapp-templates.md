# WhatsApp message templates — submit both for review on day one

Meta template review can take 24h+. Both templates below are final and ready to paste
into **Meta Business Manager → WhatsApp → Message Templates → Create Template** the
moment the Meta App + WhatsApp product exist. Until they are approved, Phase 5
(proactive alerts) and the dashboard OTP flow cannot be verified live.

> **Status (24 Sep 2026): owner login does not use WhatsApp.** The Meta app exists, but
> `login_otp` cannot be created until Meta verifies the business (findings at the end of
> this file), so the dashboard logs in with the owner's phone and PIN (`/auth/login-pin`).
> `business_alert` is still unsubmitted; alerts show in the dashboard meanwhile. The
> assistant itself (owner messages the number, it replies) needs neither template.

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

---

## Findings from the real Meta account (24 Sep 2026)

Portfolio **Poernama** (unverified), app **Poernama**, test WABA `1400425638345398`,
test number `+1 555 175 2562`, phone number ID `1420306994492662`.

**`login_otp` cannot be created: Authentication templates are gated.** WhatsApp Manager
refuses with *"This WhatsApp Business account does not have permission to create message
template"*. Creating Utility templates on the same account works, so it is the category,
not the account: Meta gates the Authentication category behind **business verification**
(and, per third-party reports, messaging volume history). An unverified portfolio cannot
send OTPs, so **owner login cannot depend on WhatsApp until verification completes**.

**`business_alert` is classified Marketing, not Utility.** Three wordings were rejected by
the classifier before submission ("Peringatan ... untuk ...", "Pemberitahuan akun ...",
"Laporan usaha ..."), each with *"Category does not match ... This message template will be
rejected"*. The content is genuinely an account notification, but a body that is mostly
free-text variables reads as promotional to their classifier. Options when this is picked
up again: the pre-approved Template library, accepting the Marketing category (higher cost,
user-mutable), or requesting a category review in Business Support Home.

**Nothing is blocked by the alert template.** Alerts are still written and shown in the
dashboard; only WhatsApp delivery waits. Login is the real dependency.
