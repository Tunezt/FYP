"""Inbound message processing — runs in the background after the webhook 200.

Pipeline per message:
  resolve business (sender phone vs businesses.owner_phone / extra_owner_numbers;
  a number on several cafes picks one, remembered: wa-gw-5)
  → branch on message.type (text / image / document)
  → reply via the Cloud API
  → persist a request_logs row (raw query, classified intent, latency, status)
"""
import logging
import time
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.ai.gemini import QuotaExhausted
from app.core.db import plain_session, tenant_session
from app.models import Business, ProcessedMessage, RequestLog
from app.whatsapp.client import normalize_phone, send_text

logger = logging.getLogger("processor")

# fin-n2-photo-before-pick: a photo from a number on several cafes arrives before
# a cafe is picked. One per number is kept (the latest) and read right after the
# pick; it expires after 15 minutes. In memory: a restart inside those minutes
# loses it, and the owner just resends.
_PHOTO_WAIT_SECONDS = 15 * 60
_held_photos: dict[str, tuple[float, dict]] = {}


def _take_held_photo(phone: str) -> tuple[float, dict] | None:
    held = _held_photos.pop(phone, None)
    if held is None or time.monotonic() - held[0] > _PHOTO_WAIT_SECONDS:
        return None
    return held


async def _already_processed(message_id: str) -> bool:
    """Meta and the gateway redeliver a webhook they think failed. Each message id
    is claimed in processed_messages before it is handled (p2-12); a redelivery,
    even after a restart, finds the id taken and is skipped. One atomic insert, so
    two deliveries racing each other cannot both win."""
    async with plain_session() as session:
        claimed = (
            await session.execute(
                insert(ProcessedMessage)
                .values(message_id=message_id)
                .on_conflict_do_nothing(index_elements=["message_id"])
                .returning(ProcessedMessage.message_id)
            )
        ).scalar_one_or_none()
    # shortcut: rows are never pruned (one short row per inbound message); add a nightly delete past 30 days if it grows.
    return claimed is None


async def process_webhook_payload(payload: dict) -> None:
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                try:
                    await _process_message(message)
                except Exception:
                    logger.exception("Failed processing message %s", message.get("id"))
            # Delivery receipts arrive on the same webhook. Meta answers 200 when
            # it *accepts* a reply; whether it reached the phone is only known
            # here, so a failure must reach the log or it is invisible.
            for status in value.get("statuses", []):
                _log_status(status)


def _log_status(status: dict) -> None:
    state = status.get("status", "?")
    recipient = status.get("recipient_id", "?")
    message_id = status.get("id", "?")
    if state == "failed":
        reasons = "; ".join(
            f"{e.get('code')} {e.get('title', '')}: "
            f"{(e.get('error_data') or {}).get('details') or e.get('message', '')}".strip()
            for e in status.get("errors", [])
        )
        logger.warning("Delivery FAILED to %s (%s): %s", recipient, message_id, reasons or "no reason given")
    else:
        logger.info("Delivery %s to %s (%s)", state, recipient, message_id)


async def _resolve_business(sender: str, text: str | None = None) -> tuple[Business | None, str | None, str | None]:
    """(business, header, reply). The cafe whose owner_phone, or one of whose
    extra owner numbers (wa-gw-4), is the sender; header None.

    A number on several cafes (wa-gw-5) answers from the one it picked, and the
    header names it on every reply. With no pick yet, or after "ganti toko",
    `reply` is the numbered list and business is None; a number from that list
    picks, and `reply` confirms it."""
    from app.services.owner_numbers import (
        SWITCH_COMMANDS,
        choose_business,
        chosen_business,
        owner_number_filter,
        mark_asked,
        pick_from_reply,
        was_asked,
    )

    phone = normalize_phone(sender)
    async with plain_session() as session:
        found = list(
            (
                await session.execute(
                    select(Business).where(owner_number_filter(phone)).order_by(Business.name, Business.id)
                )
            ).scalars().all()
        )
        if len(found) <= 1:
            return (found[0] if found else None), None, None
        said = " ".join((text or "").lower().split()).rstrip(".")
        current = chosen_business(found, phone)
        if said in SWITCH_COMMANDS:
            choose_business(found, phone, None)
            current = None
        elif current is None and (picked := pick_from_reply(found, said)) is not None:
            choose_business(found, phone, picked)
            return None, None, f"Oke, sekarang pakai data *{picked.name}* ✅\nKetik *ganti toko* kapan saja untuk pindah."
        if current is None:
            listing = "\n".join(f"{i}. {b.name}" for i, b in enumerate(found, 1))
            # Only a reply to a list we already showed can be "unclear"; the first message never is.
            unclear = "Belum jelas yang mana. " if said and said not in SWITCH_COMMANDS and was_asked(found, phone) else ""
            mark_asked(found, phone)
            return None, None, (
                f"{unclear}Nomor ini terhubung ke beberapa usaha. Mau pakai data yang mana?\n\n"
                f"{listing}\n\nBalas angka atau nama usahanya. Ketik *ganti toko* kapan saja untuk pindah.\n"
                "_(This number reaches several businesses: reply with a number or the name; type *switch business* to change.)_"
            )
        return current, f"🏪 {current.name}", None


async def _process_message(message: dict, held_since: float | None = None) -> None:
    """`held_since` is set when a photo held before the cafe pick is read again."""
    message_id = message.get("id", "")
    if held_since is None and message_id and await _already_processed(message_id):
        logger.info("Skipping duplicate webhook delivery %s", message_id)
        return

    sender = message.get("from", "")
    msg_type = message.get("type", "")

    # till-7, route A: "STRUK <code>" asks for a receipt, from any number — a
    # customer, who is registered nowhere, or an owner. Answered before the
    # owner lookup so a customer never hears "belum terdaftar" for it.
    if msg_type == "text":
        from app.whatsapp.receipts import answer_receipt_request, receipt_code_in

        code = receipt_code_in(message.get("text", {}).get("body", ""))
        if code is not None:
            intent = await answer_receipt_request(sender, code, send_text)
            logger.info("whatsapp receipt request: %s", intent)
            return

    text = message.get("text", {}).get("body", "") if msg_type == "text" else None
    business, header, pick_reply = await _resolve_business(sender, text)

    if pick_reply is not None:
        # wa-gw-5: a number on several cafes picking one; no cafe's data is read.
        phone = normalize_phone(sender)
        if msg_type == "image":
            _held_photos[phone] = (held_since if held_since is not None else time.monotonic(), message)
            if held_since is not None:
                return  # still no cafe after a reply: the list was just sent, keep waiting quietly
            pick_reply += (
                "\n\n📷 Fotonya kusimpan dulu, kubaca begitu kamu pilih.\n"
                "_(I kept your photo and will read it as soon as you pick.)_"
            )
        await send_text(sender, pick_reply)
        logger.info("Asked %s which cafe to use (type=%s)", sender, msg_type)
        if msg_type == "text" and (held := _take_held_photo(phone)) is not None:
            # A real pick reads the photo now; an unclear reply parks it again, same clock.
            await _process_message(held[1], held_since=held[0])
        return

    if business is None:
        # Unknown number: no reply at all (owner's decision 2026-11-22). The bot
        # serves the owner only; customers ask the café's own staffed number,
        # and a bot that never writes to strangers keeps the number's ban risk
        # low. No DB log (request_logs RLS requires a business_id).
        logger.info("Ignored message from unregistered number %s (type=%s)", sender, msg_type)
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
                "Maaf, aku baru bisa baca teks, foto struk/nota, dan file Excel.\n"
                "_(I can handle text, receipt photos, and Excel files.)_"
            )
        if reply:
            await send_text(sender, f"{header}\n{reply}" if header else reply)
    except QuotaExhausted as exc:
        # wa-gw-8: every AI model is out of free quota — say so, never "ada gangguan".
        status = "quota"
        logger.warning("AI quota exhausted (daily=%s) for %s message", exc.daily, msg_type)
        try:
            await send_text(sender, quota_reply(exc))
        except Exception:
            logger.exception("Failed to send quota reply")
    except Exception:
        status = "error"
        logger.exception("Error handling %s message from %s", msg_type, sender)
        try:
            await send_text(
                sender,
                "Waduh, ada gangguan sebentar. Coba kirim lagi ya.\n"
                "_(Something went wrong, please try again.)_",
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


def quota_reply(exc: QuotaExhausted) -> str:
    """The owner-facing text when the free AI quota is used up (Indonesian + English)."""
    if exc.daily and exc.resets_at is not None:
        wib = exc.resets_at.astimezone(timezone(timedelta(hours=7)))
        at = f"{wib:%H.%M} WIB ({wib + timedelta(hours=1):%H.%M} WITA)"
        return (
            "Maaf, asisten sudah kena batas pemakaian AI gratis untuk hari ini, jadi belum bisa menjawab.\n"
            f"Biasanya aktif lagi sekitar jam *{at}*. Kirim lagi setelah itu ya, data kamu aman 🙏\n\n"
            f"_(The assistant has hit today's free AI usage limit. It resets around {at}; "
            "please try again after that.)_"
        )
    return (
        "Asisten lagi terlalu ramai. Coba kirim lagi sekitar *1 menit* lagi ya 🙏\n"
        "_(The assistant is busy right now, please try again in about a minute.)_"
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
                elif pending.kind == "menu_draft":
                    # Menu photo (M5-T5): new products become items with variants.
                    from app.services.menu_draft import confirm_menu_draft

                    facts = await confirm_menu_draft(session, business, payload["draft"])
                    await discard_pending(session, pending)
                    reply = await compose_reply(business, text, "vision_menu_confirm", facts)
                    return "vision_menu_confirm", reply
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
                if payload["parsed"].get("image_sha256"):
                    revision["parsed"] = {**revision["parsed"], "image_sha256": payload["parsed"]["image_sha256"]}
                if pending.kind == "menu_draft":
                    from app.services.menu_draft import build_menu_draft, menu_draft_summary

                    draft = await build_menu_draft(session, business, revision["parsed"])
                    await create_pending(
                        session, business, revision["parsed"], payload["image_path"],
                        kind="menu_draft", extra={"draft": draft},
                    )
                    return "vision_revise", "Oke, ini versi barunya 📋\n\n" + menu_draft_summary(draft)
                if pending.kind == "goods_receipt":
                    from app.services.invoice_draft import build_draft, draft_summary

                    draft = await build_draft(session, business, revision["parsed"])
                    await create_pending(
                        session, business, revision["parsed"], payload["image_path"],
                        kind="goods_receipt", extra={"draft": draft},
                    )
                    from app.services.receipts import with_duplicate_note

                    return "vision_revise", await with_duplicate_note(
                        session, revision["parsed"], _draft_prompt(draft)
                    )
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
    # To whoever sent it: with extra owner numbers (wa-gw-4) that is not always the owner.
    await send_text(
        message.get("from") or business.owner_phone, "Fotonya sudah kuterima, lagi kubaca dulu ya… 🧾"
    )

    image_bytes, mime_type = await download_media(media_id)
    image_path = await upload_receipt_image(business.id, image_bytes, mime_type)

    # A photo captioned "menu" is the onboarding path (M5-T5): products and
    # prices become a draft catalogue, reviewed and confirmed like an invoice.
    caption = str(message.get("image", {}).get("caption") or "").lower()
    if "menu" in caption:
        return await _handle_menu_photo(business, image_bytes, mime_type, image_path)

    from app.services.receipts import image_hash, with_duplicate_note

    parsed = {**await parse_business_document(image_bytes, mime_type), "image_sha256": image_hash(image_bytes)}

    if parsed.get("document_type") == "other" or not parsed.get("items"):
        return (
            "vision",
            "Hmm, aku nggak nemu daftar barang di fotonya: kayaknya bukan nota atau buku stok.\n"
            "Coba foto ulang yang lebih jelas ya 🙏\n\n"
            "Kalau ini foto daftar menu, kirim ulang dengan keterangan *menu*.",
        )

    # Never write stock from a photo without confirmation (roadmap M5-T4): every
    # stock-affecting photo is parked, whatever the model's confidence. The gate
    # (`needs_confirmation`) now only decides whether the prompt flags doubt.
    async with tenant_session(business.id) as session:
        if parsed.get("document_type") == "receipt":
            from app.services.invoice_draft import build_draft

            draft = await build_draft(session, business, parsed)
            await create_pending(session, business, parsed, image_path, kind="goods_receipt", extra={"draft": draft})
            return "vision", await with_duplicate_note(session, parsed, _draft_prompt(draft))

        await create_pending(session, business, parsed, image_path)
        prompt = _confirmation_prompt(parsed)
        if needs_confirmation(parsed):
            prompt = "⚠️ Ada bagian yang kurang jelas, tolong dicek.\n\n" + prompt
        return "vision", prompt


def _draft_prompt(draft: dict) -> str:
    from app.services.invoice_draft import draft_summary

    return "Ini nota belanja yang kubaca, kucocokkan dengan daftar barangmu 🧾\n\n" + draft_summary(draft)


async def _handle_menu_photo(business: Business, image_bytes: bytes, mime_type: str, image_path: str) -> tuple[str, str]:
    from app.ai.vision import parse_menu_photo
    from app.services.menu_draft import build_menu_draft, menu_draft_summary
    from app.services.receipts import create_pending

    parsed = await parse_menu_photo(image_bytes, mime_type)
    if not parsed.get("is_menu") or not parsed.get("products"):
        return "vision_menu", "Hmm, aku nggak nemu daftar produk dan harga di foto ini. Coba foto papan menunya lebih dekat ya 🙏"
    async with tenant_session(business.id) as session:
        draft = await build_menu_draft(session, business, parsed)
        await create_pending(session, business, parsed, image_path, kind="menu_draft", extra={"draft": draft})
        return "vision_menu", "Ini menu yang kubaca dari fotonya 📋\n\n" + menu_draft_summary(draft)


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
            "File itu belum bisa kubaca.\n"
            "Kirim file Excel (.xlsx) pakai template stok ya. Templatnya bisa diunduh dari dashboard 📄",
        )

    data, _mime = await download_media(doc.get("id", ""))
    try:
        rows, warnings = parse_stock_template(data)
    except StockTemplateError:
        return (
            "document",
            "Excelnya kebuka, tapi aku nggak nemu kolom *nama* dan *jumlah*.\n"
            "Pakai template dari dashboard ya, atau pastikan baris pertama berisi "
            "judul kolom.",
        )

    async with tenant_session(business.id) as session:
        facts = await apply_stock_template(session, business, rows)
    facts["warnings"] = warnings
    # The import is already committed here. If the AI is down the owner must still be
    # told it worked (fin-p1-7: a 503 made "Waduh, ada gangguan" hide a saved import).
    try:
        reply = await compose_reply(business, "(owner sent a stock template)", "document", facts)
    except Exception:
        logger.exception("compose failed after the stock import was saved; sending the fixed reply")
        reply = stock_import_reply(facts, warnings)
    return "document", reply


def stock_import_reply(facts: dict, warnings: list) -> str:
    """Fixed Indonesian confirmation of a committed stock import (no AI)."""
    made, updated = len(facts.get("items_created", [])), len(facts.get("items_updated", []))
    text = f"✅ *Stok dari Excel sudah tersimpan*\n- {updated} barang diperbarui\n- {made} barang baru"
    if warnings:
        text += f"\n\n⚠️ Ada {len(warnings)} baris yang perlu dicek."
    return text
