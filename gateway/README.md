# Poernama WhatsApp gateway

Links the bot's WhatsApp number as a *linked device* (WhatsApp Web, through the open-source
[Baileys](https://github.com/WhiskeySockets/Baileys) library) and bridges it to the backend, while
Meta's Cloud API is blocked on business verification. **Unofficial: WhatsApp can ban a number that
uses it.** The backend only uses it with `WHATSAPP_PROVIDER=gateway`; Meta stays the default.

```
phone -> WhatsApp -> gateway --POST /webhooks/gateway (X-Gateway-Token)--> backend
backend --POST <gateway>/send {to, text} (Bearer token)--> gateway -> WhatsApp -> phone
```

Photos and Excel files travel inside the webhook (base64, up to 15 MB). Templates do not exist
here, so the nightly alert and login codes arrive as plain text.

## Railway service

| Setting | Value |
|---|---|
| Root Directory | `/gateway` |
| Watch Paths | `/gateway/**` (backend pushes then don't restart the WhatsApp link) |
| Volume | mounted at `/data` (the linked session lives in `/data/auth`) |
| Public domain | port `8080` |

Variables:

| Gateway | Backend |
|---|---|
| `PORT=8080` | `WHATSAPP_PROVIDER=gateway` |
| `WA_NUMBER=6285128056588` | `WHATSAPP_GATEWAY_URL=https://<gateway domain>` |
| `BACKEND_WEBHOOK_URL=https://<backend>/webhooks/gateway` | `WHATSAPP_GATEWAY_TOKEN=<same token>` |
| `GATEWAY_TOKEN=<64 random characters>` | |

## Linking the number

1. Deploy. The log prints `PAIRING CODE for 62…: ABCD-EFGH`.
2. On the bot's phone: WhatsApp > Linked devices > Link a device > *Link with phone number
   instead* > type the code. It expires quickly; if it does, restart the deployment for a new one.
3. The log prints `WhatsApp connected as …`. The link survives restarts (volume).

If the phone unlinks the device, the gateway clears the session and prints a new code.

## Checks

`npm test` (pure message handling). `GET /health` answers `{"ok":true,"connected":true|false}`.

## Back to Meta

Set `WHATSAPP_PROVIDER=cloud` on the backend (the gateway route then answers 404) and remove
this service.
