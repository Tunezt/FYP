"""Thin async client for the WhatsApp Cloud API (Meta Graph API, direct — no Twilio).

Dry-run behavior: with placeholder credentials (no real Meta app configured),
outbound sends are logged instead of attempted, so the whole inbound pipeline
can be exercised locally by POSTing simulated webhook payloads and reading the
log. Flip to real sends automatically once WHATSAPP_ACCESS_TOKEN is set.
"""
import logging
import re

import httpx

from app.core.config import get_settings

logger = logging.getLogger("whatsapp")

GRAPH_BASE = "https://graph.facebook.com/v21.0"


def normalize_phone(phone: str) -> str:
    """Meta's wa_id format: country code + number, digits only ('6281234567890')."""
    return re.sub(r"\D", "", phone)


def _dry_run() -> bool:
    return get_settings().whatsapp_access_token == "placeholder"


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_settings().whatsapp_access_token}"}


async def send_text(to: str, body: str) -> None:
    """Free-form service message — only valid inside an open 24-hour session
    window (i.e. as a reply to an inbound message). Never use for proactive
    sends; those must go through send_template."""
    payload = {
        "messaging_product": "whatsapp",
        "to": normalize_phone(to),
        "type": "text",
        "text": {"body": body[:4096]},
    }
    await _post_message(payload, log_hint=f"text -> {to}: {body[:200]}")


async def send_template(to: str, template_name: str, body_params: list[str],
                        language: str | None = None) -> None:
    """Template message — works outside the 24-hour window. Used for the
    Utility alert template and the Authentication OTP template."""
    settings = get_settings()
    payload = {
        "messaging_product": "whatsapp",
        "to": normalize_phone(to),
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language or settings.whatsapp_template_language},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in body_params],
                }
            ],
        },
    }
    await _post_message(payload, log_hint=f"template {template_name} -> {to}: {body_params}")


async def send_otp_template(to: str, code: str) -> None:
    """Authentication templates have a fixed structure: one body parameter (the
    code) plus a copy-code button carrying the same value."""
    settings = get_settings()
    payload = {
        "messaging_product": "whatsapp",
        "to": normalize_phone(to),
        "type": "template",
        "template": {
            "name": settings.whatsapp_otp_template,
            "language": {"code": settings.whatsapp_template_language},
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": code}]},
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": [{"type": "text", "text": code}],
                },
            ],
        },
    }
    await _post_message(payload, log_hint=f"OTP template -> {to} (code hidden)")


async def _post_message(payload: dict, log_hint: str) -> None:
    settings = get_settings()
    if _dry_run():
        logger.info("[DRY-RUN outbound WhatsApp] %s", log_hint)
        return
    url = f"{GRAPH_BASE}/{settings.whatsapp_phone_number_id}/messages"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=_headers())
        if resp.status_code >= 400:
            logger.error("WhatsApp send failed %s: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()


async def download_media(media_id: str) -> tuple[bytes, str]:
    """Two-step Meta media fetch: resolve the media URL, then download it with
    the same bearer token. Returns (bytes, mime_type)."""
    async with httpx.AsyncClient(timeout=30) as client:
        meta = await client.get(f"{GRAPH_BASE}/{media_id}", headers=_headers())
        meta.raise_for_status()
        info = meta.json()
        blob = await client.get(info["url"], headers=_headers())
        blob.raise_for_status()
        return blob.content, info.get("mime_type", "application/octet-stream")
