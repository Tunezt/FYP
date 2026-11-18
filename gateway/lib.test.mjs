import assert from 'node:assert/strict'
import test from 'node:test'
import { bearerOk, describe, toJid } from './lib.mjs'

const msg = (message, key = {}) => ({ key: { id: 'ABC', remoteJid: '6281234567890@s.whatsapp.net', ...key }, message })

test('text from a phone number', () => {
  assert.deepEqual(describe(msg({ conversation: 'halo' })), { id: 'ABC', from: '6281234567890', type: 'text', text: 'halo' })
  assert.equal(describe(msg({ extendedTextMessage: { text: 'stok kopi?' } })).text, 'stok kopi?')
})

test('a hidden number uses the alt jid, else the private id', () => {
  const lid = { remoteJid: '123456789012345@lid' }
  assert.equal(describe(msg({ conversation: 'x' }, { ...lid, remoteJidAlt: '6281234567890@s.whatsapp.net' })).from, '6281234567890')
  assert.equal(describe(msg({ conversation: 'x' }, lid)).from, '123456789012345@lid')
})

test('photo and excel keep caption, filename, type and size', () => {
  assert.deepEqual(describe(msg({ imageMessage: { caption: 'menu', mimetype: 'image/jpeg', fileLength: 2048 } })),
    { id: 'ABC', from: '6281234567890', type: 'image', caption: 'menu', mime_type: 'image/jpeg', size: 2048 })
  const doc = describe(msg({ documentWithCaptionMessage: { message: { documentMessage: { fileName: 'stok.xlsx', mimetype: 'application/vnd.ms-excel' } } } }))
  assert.equal(doc.type, 'document')
  assert.equal(doc.filename, 'stok.xlsx')
})

test('voice notes are passed on as unsupported, so the bot can say what it reads', () => {
  assert.equal(describe(msg({ audioMessage: { seconds: 3 } })).type, 'unsupported')
})

test('own messages, groups, status, reactions and deletes are ignored', () => {
  assert.equal(describe(msg({ conversation: 'x' }, { fromMe: true })), null)
  assert.equal(describe(msg({ conversation: 'x' }, { remoteJid: '1203630@g.us' })), null)
  assert.equal(describe(msg({ conversation: 'x' }, { remoteJid: 'status@broadcast' })), null)
  assert.equal(describe(msg({ reactionMessage: { text: '👍' } })), null)
  assert.equal(describe(msg({ protocolMessage: { type: 0 } })), null)
  assert.equal(describe({ key: {}, message: { conversation: 'x' } }), null)
})

test('bearer token check', () => {
  assert.equal(bearerOk('Bearer s3cret-token', 's3cret-token'), true)
  assert.equal(bearerOk('Bearer wrong', 's3cret-token'), false)
  assert.equal(bearerOk(undefined, 's3cret-token'), false)
})

test('jid for sending', () => {
  assert.equal(toJid('+62 812-3456-7890'), '6281234567890@s.whatsapp.net')
  assert.equal(toJid('123456789012345@lid'), '123456789012345@lid')
})
