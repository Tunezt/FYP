"""WhatsApp Cloud API webhook.

GET  — Meta's verification handshake (echo hub.challenge when the verify token
       matches).
POST — validate X-Hub-Signature-256 (HMAC-SHA256 of the *raw* body with the app
       secret) BEFORE trusting anything, then ack 200 immediately and process
       in the background — Meta retries webhooks that answer slowly, and the
       LLM round-trip is far slower than its patience.
"""
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response

from app.core.config import get_settings, is_placeholder
from app.whatsapp.processor import process_webhook_payload

logger = logging.getLogger("webhook")

router = APIRouter(prefix="/webhooks", tags=["whatsapp"])


@router.get("/whatsapp")
async def verify_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
):
    settings = get_settings()
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verify token mismatch")


def _signature_valid(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


@router.post("/whatsapp")
async def receive_webhook(request: Request, background: BackgroundTasks):
    settings = get_settings()
    raw = await request.body()

    if is_placeholder(settings.whatsapp_app_secret):
        # No Meta app configured (local/dev simulation). Never allowed in prod.
        if settings.environment == "production":
            raise HTTPException(status_code=500, detail="WHATSAPP_APP_SECRET not configured")
        logger.warning("Signature validation skipped — placeholder app secret (dev only)")
    elif not _signature_valid(raw, request.headers.get("X-Hub-Signature-256"), settings.whatsapp_app_secret):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Ack fast; the real work (LLM, DB, reply) happens after the response.
    background.add_task(process_webhook_payload, payload)
    return {"status": "received"}
