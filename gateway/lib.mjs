// Pure helpers for the gateway, kept apart from the socket so node:test can check them.
import { timingSafeEqual } from 'node:crypto'
import { getContentType, isPnUser, jidDecode, jidNormalizedUser, normalizeMessageContent } from '@whiskeysockets/baileys'

const UNSUPPORTED = new Set([
  'audioMessage', 'videoMessage', 'ptvMessage', 'stickerMessage',
  'contactMessage', 'contactsArrayMessage', 'locationMessage', 'liveLocationMessage',
])

// One incoming WhatsApp message -> the flat body the backend's /webhooks/gateway takes,
// or null for anything that is not a person writing to us (own messages, groups, status,
// channels, reactions, edits, receipts). `size` is the attachment size, dropped before posting.
export function describe (m) {
  const key = m?.key ?? {}
  const chat = key.remoteJid ?? ''
  if (key.fromMe || !key.id || !chat.endsWith('@s.whatsapp.net') && !chat.endsWith('@lid')) return null

  // WhatsApp may hide the number behind a private id (@lid); the alt jid then carries it.
  const pn = [key.remoteJidAlt, chat].find(j => isPnUser(j))
  const from = pn ? jidDecode(pn).user : jidNormalizedUser(chat)

  const content = normalizeMessageContent(m.message)
  const kind = getContentType(content)
  const base = { id: key.id, from }
  if (kind === 'conversation') return { ...base, type: 'text', text: content.conversation }
  if (kind === 'extendedTextMessage') return { ...base, type: 'text', text: content.extendedTextMessage.text ?? '' }
  if (kind === 'imageMessage') {
    const img = content.imageMessage
    return { ...base, type: 'image', caption: img.caption ?? '', mime_type: img.mimetype ?? 'image/jpeg', size: Number(img.fileLength ?? 0) }
  }
  if (kind === 'documentMessage') {
    const doc = content.documentMessage
    return { ...base, type: 'document', filename: doc.fileName ?? '', mime_type: doc.mimetype ?? 'application/octet-stream', size: Number(doc.fileLength ?? 0) }
  }
  if (UNSUPPORTED.has(kind)) return { ...base, type: 'unsupported' }
  return null
}

export function bearerOk (header, token) {
  const given = Buffer.from(String(header ?? ''))
  const want = Buffer.from(`Bearer ${token}`)
  return given.length === want.length && timingSafeEqual(given, want)
}

// Digits -> a phone jid; a full jid (e.g. "…@lid") is used as given.
export function toJid (to) {
  return to.includes('@') ? to : `${to.replace(/\D/g, '')}@s.whatsapp.net`
}
