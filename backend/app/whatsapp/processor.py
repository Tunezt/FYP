"""Inbound message processing — runs in the background after the webhook 200.

Pipeline per message:
  resolve business (sender phone vs businesses.owner_phone)
  → branch on message.type (text / image / document)
  → reply via the Cloud API
  → persist a request_logs row (raw query, classified intent, latency, status)
"""
import logging
import time
from collections import OrderedDict

from sqlalchemy import select

from app.core.db import plain_session, tenant_session
from app.models import Business, RequestLog
from app.whatsapp.client import normalize_phone, send_text

logger = logging.getLogger("processor")

# Meta redelivers webhooks it thinks failed; a small LRU of seen message ids
# keeps a retry from double-running a stock correction or double-replying.
_seen_ids: OrderedDict[str, None] = OrderedDict()
_SEEN_MAX = 2048


def _already_processed(message_id: str) -> bool:
    if message_id in _seen_ids:
        return True
    _seen_ids[message_id] = None
    if len(_seen_ids) > _SEEN_MAX:
        _seen_ids.popitem(last=False)
    return False


async def process_webhook_payload(payload: dict) -> None:
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            # Delivery/read receipts arrive on the same webhook — not messages.
            for message in value.get("messages", []):
                try:
                    await _process_message(message)
                except Exception:
                    logger.exception("Failed processing message %s", message.get("id"))


async def _resolve_business(sender: str) -> Business | None:
    async with plain_session() as session:
        return (
            await session.execute(
                select(Business).where(Business.owner_phone == normalize_phone(sender))
            )
        ).scalar_one_or_none()


async def _process_message(message: dict) -> None:
    message_id = message.get("id", "")
    if message_id and _already_processed(message_id):
        logger.info("Skipping duplicate webhook delivery %s", message_id)
        return

    sender = message.get("from", "")
    msg_type = message.get("type", "")
    business = await _resolve_business(sender)

    if business is None:
        # Unknown number: they just messaged us, so the 24h session window is
        # open and a free-form reply is allowed. No DB log (request_logs RLS
        # requires a business_id).
        logger.info("Message from unregistered number %s (type=%s)", sender, msg_type)
        await send_text(
            sender,
            "Nomor ini belum terdaftar di Warung Pintar. "
            "Daftar dulu lewat dashboard ya! / This number isn't registered yet.",
        )
        return

    start = time.perf_counter()
    raw_query: str | None = None
    intent = "unsupported"
    status = "ok"
    reply: str | None = None

    try:
        if msg_type == "text":
            raw_query = message.get("text", {}).get("body", "")
            intent, reply = await _handle_text(business, raw_query)
        elif msg_type == "image":
            intent, reply = await _handle_image(business, message)
        elif msg_type == "document":
            intent, reply = await _handle_document(business, message)
        else:
            reply = (
                "Maaf, aku baru bisa baca teks, foto struk/nota, dan file Excel. "
                "(I can handle text, receipt photos, and Excel files.)"
            )
        if reply:
            await send_text(sender, reply)
    except Exception:
        status = "error"
        logger.exception("Error handling %s message from %s", msg_type, sender)
        try:
            await send_text(
                sender,
                "Waduh, ada gangguan sebentar — coba kirim lagi ya. "
                "(Something went wrong, please try again.)",
            )
        except Exception:
            logger.exception("Failed to send error reply")

    latency_ms = int((time.perf_counter() - start) * 1000)
    try:
        async with tenant_session(business.id) as session:
            session.add(
                RequestLog(
                    business_id=business.id,
                    channel="whatsapp",
                    path=msg_type,
                    raw_query=raw_query,
                    classified_intent=intent,
                    latency_ms=latency_ms,
                    status=status,
                )
            )
    except Exception:
        logger.exception("Failed to write request log")
    logger.info(
        "whatsapp %s business=%s intent=%s status=%s latency=%dms",
        msg_type,
        business.id,
        intent,
        status,
        latency_ms,
    )


def _confirmation_prompt(parsed: dict) -> str:
    from app.services.receipts import summarize_parse

    header = (
        "Ini yang aku baca dari fotonya 👀"
        if parsed.get("document_type") == "stock_ledger"
        else "Ini yang aku baca dari notanya 👀"
    )
    return (
        f"{header}\n\n{summarize_parse(parsed)}\n\n"
        "Sudah benar? Balas *YA* untuk simpan, atau kasih tau bagian yang salah."
    )


async def _handle_text(business: Business, text: str) -> tuple[str, str]:
    from app.ai.composer import compose_reply
    from app.ai.router import handle_text as route_text
    from app.ai.vision import revise_parse
    from app.services.receipts import (
        classify_reply_keyword,
        commit_parse,
        create_pending,
        discard_pending,
        get_active_pending,
    )

    async with tenant_session(business.id) as session:
        pending = await get_active_pending(session, business.id)

        if pending is not None:
            verdict = classify_reply_keyword(text)
            payload = pending.payload

            if verdict == "confirm":
                if pending.kind == "goods_receipt":
                    # Supplier invoice (M5-T4): the matched lines become a goods
                    # receipt; questions were skipped and are named in the reply.
                    from app.services.invoice_draft import confirm_draft

                    facts = await confirm_draft(
                        session, business, payload["draft"], payload["image_path"], payload["parsed"]
                    )
                else:
                    facts = await commit_parse(
                        session, business, payload["parsed"], payload["image_path"]
                    )
                await discard_pending(session, pending)
                reply = await compose_reply(business, text, "vision_confirm", facts)
                return "vision_confirm", reply

            if verdict == "deny":
                await discard_pending(session, pending)
                return (
                    "vision_deny",
                    "Oke, aku batalkan — nggak ada yang disimpan. "
                    "Kirim ulang fotonya kalau mau coba lagi 👍",
                )

            # Anything else: maybe a correction, maybe an unrelated question.
            revision = await revise_parse(payload["parsed"], text)
            if not revision["unrelated"]:
                if pending.kind == "goods_receipt":
                    from app.services.invoice_draft import build_draft, draft_summary

                    draft = await build_draft(session, business, revision["parsed"])
                    await create_pending(
                        session, business, revision["parsed"], payload["image_path"],
                        kind="goods_receipt", extra={"draft": draft},
                    )
                    return "vision_revise", _draft_prompt(draft)
                await create_pending(session, business, revision["parsed"], payload["image_path"])
                return "vision_revise", _confirmation_prompt(revision["parsed"])
            # Unrelated → keep the pending and answer normally below.

        routed = await route_text(session, business, text)
        return routed.intent, routed.reply


async def _handle_image(business: Business, message: dict) -> tuple[str, str | None]:
    from app.ai.composer import compose_reply
    from app.ai.vision import parse_business_document
    from app.services.receipts import commit_parse, create_pending, needs_confirmation
    from app.whatsapp.client import download_media
    from app.whatsapp.storage import upload_receipt_image

    media_id = message.get("image", {}).get("id")
    if not media_id:
        return "vision", "Fotonya nggak kebaca — coba kirim ulang ya."

    # Immediate ack — vision parsing takes far longer than a text round-trip.
    await send_text(
        business.owner_phone, "Fotonya sudah kuterima, lagi kubaca dulu ya… 🧾"
    )

    image_bytes, mime_type = await download_media(media_id)
    image_path = await upload_receipt_image(business.id, image_bytes, mime_type)
    parsed = await parse_business_document(image_bytes, mime_type)

    if parsed.get("document_type") == "other" or not parsed.get("items"):
        return (
            "vision",
            "Hmm, itu kayaknya bukan nota atau buku stok — aku nggak nemu daftar "
            "barang di fotonya. Coba foto ulang yang lebih jelas ya 🙏",
        )

    # Never write stock from a photo without confirmation (roadmap M5-T4): every
    # stock-affecting photo is parked, whatever the model's confidence. The gate
    # (`needs_confirmation`) now only decides whether the prompt flags doubt.
    async with tenant_session(business.id) as session:
        if parsed.get("document_type") == "receipt":
            from app.services.invoice_draft import build_draft

            draft = await build_draft(session, business, parsed)
            await create_pending(session, business, parsed, image_path, kind="goods_receipt", extra={"draft": draft})
            return "vision", _draft_prompt(draft)

        await create_pending(session, business, parsed, image_path)
        prompt = _confirmation_prompt(parsed)
        if needs_confirmation(parsed):
            prompt = "⚠️ Ada bagian yang kurang jelas, tolong dicek.\n\n" + prompt
        return "vision", prompt


def _draft_prompt(draft: dict) -> str:
    from app.services.invoice_draft import draft_summary

    return "Ini nota belanja yang kubaca, kucocokkan dengan daftar barangmu 🧾\n\n" + draft_summary(draft)


_XLSX_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}


async def _handle_document(business: Business, message: dict) -> tuple[str, str | None]:
    from app.ai.composer import compose_reply
    from app.services.stock_import import (
        StockTemplateError,
        apply_stock_template,
        parse_stock_template,
    )
    from app.whatsapp.client import download_media

    doc = message.get("document", {})
    filename = (doc.get("filename") or "").lower()
    if doc.get("mime_type") not in _XLSX_MIMES and not filename.endswith((".xlsx", ".xls")):
        return (
            "document",
            "File itu belum bisa kubaca — kirim file Excel (.xlsx) pakai template "
            "stok ya. Templatnya bisa diunduh dari dashboard 📄",
        )

    data, _mime = await download_media(doc.get("id", ""))
    try:
        rows, warnings = parse_stock_template(data)
    except StockTemplateError:
        return (
            "document",
            "Excelnya kebuka, tapi aku nggak nemu kolom *nama* dan *jumlah*. "
            "Pakai template dari dashboard ya, atau pastikan baris pertama berisi "
            "judul kolom.",
        )

    async with tenant_session(business.id) as session:
        facts = await apply_stock_template(session, business, rows)
    facts["warnings"] = warnings
    reply = await compose_reply(business, "(owner sent a stock template)", "document", facts)
    return "document", reply
