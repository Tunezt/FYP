// Poernama WhatsApp gateway (wa-gw-2): links one WhatsApp number as a "linked device"
// (WhatsApp Web, via Baileys) and bridges it to the backend.
//   inbound:  each message -> POST BACKEND_WEBHOOK_URL with header X-Gateway-Token
//   outbound: POST /send {to, text} with "Authorization: Bearer <GATEWAY_TOKEN>"
// Unofficial: WhatsApp may ban a number that uses it. Meta's Cloud API stays the
// backend's default; this runs only while WHATSAPP_PROVIDER=gateway.
import { rmSync } from 'node:fs'
import http from 'node:http'
import { setTimeout as sleep } from 'node:timers/promises'
import makeWASocket, { DisconnectReason, downloadMediaMessage, useMultiFileAuthState } from '@whiskeysockets/baileys'
import pino from 'pino'
import { bearerOk, describe, toJid } from './lib.mjs'

const { PORT = '8080', GATEWAY_TOKEN = '', BACKEND_WEBHOOK_URL = '', WA_NUMBER = '', AUTH_DIR = '/data/auth' } = process.env
const NUMBER = WA_NUMBER.replace(/\D/g, '')
const MAX_MEDIA = 15 * 1024 * 1024

if (GATEWAY_TOKEN.length < 24 || !BACKEND_WEBHOOK_URL.startsWith('http') || NUMBER.length < 8) {
  console.error('Set GATEWAY_TOKEN (24+ characters), BACKEND_WEBHOOK_URL and WA_NUMBER (e.g. 6285128056588).')
  process.exit(1)
}

const logger = pino({ level: 'warn' })
let sock
let connected = false

async function connect () {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)
  // markOnlineOnConnect false: the phone keeps getting its notifications.
  sock = makeWASocket({ auth: state, logger, markOnlineOnConnect: false, syncFullHistory: false })
  const me = sock
  let codeAsked = false

  me.ev.on('creds.update', saveCreds)

  me.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
    if (qr && !me.authState.creds.registered && !codeAsked) {
      codeAsked = true
      try {
        const code = await me.requestPairingCode(NUMBER)
        console.log(`PAIRING CODE for ${NUMBER}: ${code.slice(0, 4)}-${code.slice(4)}`)
        console.log('On that phone: WhatsApp > Linked devices > Link a device > Link with phone number instead > type the code.')
      } catch (e) {
        console.error('Could not get a pairing code:', e.message)
      }
    }
    if (connection === 'open') {
      connected = true
      console.log(`WhatsApp connected as ${me.user?.id}`)
    }
    if (connection === 'close') {
      connected = false
      const status = lastDisconnect?.error?.output?.statusCode
      if (status === DisconnectReason.loggedOut) {
        // Unlinked from the phone: the saved session is dead. Start over with a new code.
        console.error('Logged out from the phone. Clearing the saved session; a new pairing code follows.')
        rmSync(AUTH_DIR, { recursive: true, force: true })
      } else {
        console.log(`Connection closed (${status ?? 'no code'}), reconnecting.`)
      }
      await sleep(3000)
      connect().catch(e => { console.error('Reconnect failed:', e); process.exit(1) })
    }
  })

  me.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return
    for (const m of messages) {
      const body = describe(m)
      if (!body) continue
      if (body.type === 'image' || body.type === 'document') {
        try {
          if (body.size > MAX_MEDIA) throw new Error(`too large (${body.size} bytes)`)
          const buf = await downloadMediaMessage(m, 'buffer', {}, { logger, reuploadRequest: me.updateMediaMessage })
          body.media_b64 = buf.toString('base64')
        } catch (e) {
          // Forwarded without bytes: the backend answers that it could not read it.
          console.error(`Media download failed for ${body.id}: ${e.message}`)
        }
      }
      delete body.size
      me.readMessages([m.key]).catch(() => {})
      forward(body)
    }
  })
}

async function forward (body) {
  for (let attempt = 1; attempt <= 5; attempt++) {
    try {
      const r = await fetch(BACKEND_WEBHOOK_URL, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-gateway-token': GATEWAY_TOKEN },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(30_000)
      })
      if (r.ok) return
      const detail = (await r.text()).slice(0, 300)
      if (r.status < 500) return console.error(`Backend refused ${body.id}: ${r.status} ${detail}`)
      console.error(`Backend error for ${body.id} (try ${attempt}): ${r.status} ${detail}`)
    } catch (e) {
      console.error(`Backend unreachable for ${body.id} (try ${attempt}): ${e.message}`)
    }
    await sleep(2000 * attempt)
  }
  console.error(`Gave up forwarding ${body.id}`)
}

function reply (res, status, obj) {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(obj))
}

http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') return reply(res, 200, { ok: true, connected })
  if (req.method !== 'POST' || req.url !== '/send') return reply(res, 404, { error: 'not found' })
  if (!bearerOk(req.headers.authorization, GATEWAY_TOKEN)) return reply(res, 401, { error: 'unauthorized' })

  let raw = ''
  for await (const chunk of req) {
    raw += chunk
    if (raw.length > 64 * 1024) return reply(res, 413, { error: 'too large' })
  }
  let to, text
  try { ({ to, text } = JSON.parse(raw)) } catch { return reply(res, 400, { error: 'bad json' }) }
  if (typeof to !== 'string' || typeof text !== 'string' || !to || !text) return reply(res, 400, { error: 'to and text required' })
  if (!connected) return reply(res, 503, { error: 'whatsapp not connected' })
  try {
    const sent = await sock.sendMessage(toJid(to), { text })
    reply(res, 200, { id: sent?.key?.id ?? null })
  } catch (e) {
    console.error(`Send to ${to} failed: ${e.message}`)
    reply(res, 502, { error: e.message })
  }
}).listen(Number(PORT), () => console.log(`Gateway listening on ${PORT}`))

connect().catch(e => { console.error('Start failed:', e); process.exit(1) })
