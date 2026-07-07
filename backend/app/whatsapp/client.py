"""Thin async client for the WhatsApp Cloud API (Meta Graph API, direct — no Twilio).

Dry-run behavior: with placeholder credentials (no real Meta app configured),
outbound sends are logged instead of attempted, so the whole inbound pipeline
can be exercised locally by POSTing simulated webhook payloads and reading the
log. Flip to real sends automatically once WHATSAPP_ACCESS_TOKEN is set.
"""
import logging
import re

import httpx

from app.core.config import get_settings, is_placeholder

logger = logging.getLogger("whatsapp")

GRAPH_BASE = "https://graph.facebook.com/v21.0"


def normalize_phone(phone: str) -> str:
    """Meta's wa_id format: country code + number, digits only ('6281234567890')."""
    return re.sub(r"\D", "", phone)


def to_international_phone(digits: str) -> str:
    """Converts a user-typed *local* number to the international wa_id format
    stored in businesses.owner_phone. Without this, a real owner typing their
    number the natural local way (leading 0 — exactly what the login form's
    own placeholder "0812 3456 7890" tells them to do) would never match
    their existing account and would be silently routed into registration
    every time.

    Heuristic (this product targets Indonesia + Malaysia only, per
    language_preference id/ms/en): already-international numbers (62.../
    60...) pass through; a leading 01 is Malaysia's local mobile prefix; any
    other leading 0 is treated as Indonesian.
    """
    if digits.startswith("62") or digits.startswith("60"):
        return digits
    if digits.startswith("01"):
        return "60" + digits[1:]
    if digits.startswith("0"):
        return "62" + digits[1:]
    return digits


def _dry_run() -> bool:
    return is_placeholder(get_settings().whatsapp_access_token)


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
