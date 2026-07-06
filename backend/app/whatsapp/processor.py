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


async def _handle_text(business: Business, text: str) -> tuple[str, str]:
    from app.ai.router import handle_text as route_text

    async with tenant_session(business.id) as session:
        routed = await route_text(session, business, text)
        return routed.intent, routed.reply


async def _handle_image(business: Business, message: dict) -> tuple[str, str | None]:
    # Vision path lands in Phase 3.
    return "vision", (
        "Fitur baca foto struk sedang disiapkan — sebentar lagi ya! "
        "(Receipt photo reading is coming shortly.)"
    )


async def _handle_document(business: Business, message: dict) -> tuple[str, str | None]:
    # Excel import path lands in Phase 3.
    return "document", (
        "Fitur impor Excel sedang disiapkan — sebentar lagi ya! "
        "(Excel import is coming shortly.)"
    )
