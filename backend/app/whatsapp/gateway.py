"""Inbound side of the self-hosted WhatsApp Web gateway (wa-gw-1).

The gateway (/gateway, Baileys) posts one flat JSON object per message. This
turns it into the Cloud API message shape `processor._process_message` already
handles, so the gateway reuses the whole pipeline: STRUK receipts, owner
lookup, the duplicate-id LRU, text / photo / Excel handling and request logs.

Gateways do not sign webhooks the way Meta does, so the route is protected by a
shared token (WHATSAPP_GATEWAY_TOKEN) and is closed unless the provider is
"gateway".
"""
import base64
import binascii
from typing import Literal

from pydantic import BaseModel, Field

from app.whatsapp.client import stash_media


class GatewayMessageIn(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    # Phone digits, or "<id>@lid" when WhatsApp hides the sender's number.
    from_: str = Field(alias="from", min_length=1, max_length=100)
    type: Literal["text", "image", "document", "unsupported"]
    text: str | None = None
    caption: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    media_b64: str | None = None


class GatewayMediaInvalid(ValueError):
    pass


def to_cloud_payload(msg: GatewayMessageIn) -> dict:
    """The Cloud API webhook envelope around one normalised message."""
    message: dict = {"id": msg.id, "from": msg.from_, "type": msg.type}
    if msg.type == "text":
        message["text"] = {"body": msg.text or ""}
    elif msg.type in ("image", "document"):
        media: dict = {"id": msg.id, "mime_type": msg.mime_type or "application/octet-stream"}
        if msg.type == "image":
            media["caption"] = msg.caption or ""
        else:
            media["filename"] = msg.filename or ""
        if msg.media_b64:
            try:
                data = base64.b64decode(msg.media_b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise GatewayMediaInvalid(str(exc)) from exc
            stash_media(msg.id, data, media["mime_type"])
        message[msg.type] = media
    return {"entry": [{"changes": [{"value": {"messages": [message]}}]}]}
