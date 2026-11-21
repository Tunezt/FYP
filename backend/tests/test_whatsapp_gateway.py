"""wa-gw-1: the self-hosted WhatsApp Web gateway behind WHATSAPP_PROVIDER.

Inbound: the gateway's flat message becomes the Cloud API shape the processor
already handles. Outbound: send_text / templates / OTP go to the gateway's
/send when the provider is "gateway" and to Meta otherwise. The real gateway is
never reached here; HTTP is answered by httpx.MockTransport, and live proof
comes only from the owner's phone.
"""
import base64
import json
import uuid
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.whatsapp import client as wa
from app.whatsapp.gateway import GatewayMessageIn, to_cloud_payload
from app.whatsapp.processor import process_webhook_payload

TOKEN = "gw-test-token-0123456789"
GATEWAY_URL = "https://gateway.example.test"
http = TestClient(app)


@contextmanager
def provider(name: str, token: str = TOKEN, url: str = GATEWAY_URL):
    settings = get_settings()
    with (
        patch.object(settings, "whatsapp_provider", name),
        patch.object(settings, "whatsapp_gateway_token", token),
        patch.object(settings, "whatsapp_gateway_url", url),
    ):
        yield


@pytest.fixture
def captured(monkeypatch):
    """Every outbound HTTP request the client makes, answered 200."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "sent-1"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return requests


def _post(body: dict, token: str = TOKEN):
    return http.post("/webhooks/gateway", json=body, headers={"X-Gateway-Token": token})


# ── Inbound route ────────────────────────────────────────────────────────────

def test_route_is_closed_while_the_provider_is_meta():
    with provider("cloud"), patch("app.whatsapp.webhook.process_webhook_payload", new=AsyncMock()) as proc:
        resp = _post({"id": "m1", "from": "628111", "type": "text", "text": "halo"})
    assert resp.status_code == 404
    proc.assert_not_awaited()


@pytest.mark.parametrize("configured, sent", [(TOKEN, "wrong-token"), (TOKEN, ""), ("placeholder", "placeholder")])
def test_wrong_or_unset_token_is_refused(configured, sent):
    with provider("gateway", token=configured), \
            patch("app.whatsapp.webhook.process_webhook_payload", new=AsyncMock()) as proc:
        resp = _post({"id": "m1", "from": "628111", "type": "text", "text": "halo"}, token=sent)
    assert resp.status_code == 403
    proc.assert_not_awaited()


def test_text_becomes_the_cloud_api_shape():
    with provider("gateway"), patch("app.whatsapp.webhook.process_webhook_payload", new=AsyncMock()) as proc:
        resp = _post({"id": "m-text", "from": "6281234567890", "type": "text", "text": "stok kopi berapa?"})
    assert resp.status_code == 200
    proc.assert_awaited_once_with({"entry": [{"changes": [{"value": {"messages": [
        {"id": "m-text", "from": "6281234567890", "type": "text", "text": {"body": "stok kopi berapa?"}}
    ]}}]}]})


def test_photo_keeps_its_caption_and_its_bytes_reach_download_media():
    photo = b"\xff\xd8\xff fake jpeg"
    with provider("gateway"), patch("app.whatsapp.webhook.process_webhook_payload", new=AsyncMock()) as proc:
        resp = _post({
            "id": "m-photo", "from": "6281234567890", "type": "image", "caption": "menu",
            "mime_type": "image/jpeg", "media_b64": base64.b64encode(photo).decode(),
        })
        assert resp.status_code == 200
        message = proc.await_args.args[0]["entry"][0]["changes"][0]["value"]["messages"][0]
        assert message["type"] == "image"
        assert message["image"] == {"id": "m-photo", "mime_type": "image/jpeg", "caption": "menu"}
        import asyncio
        assert asyncio.run(wa.download_media("m-photo")) == (photo, "image/jpeg")
        with pytest.raises(RuntimeError):  # taken once, never served twice
            asyncio.run(wa.download_media("m-photo"))


def test_excel_document_keeps_its_filename():
    payload = to_cloud_payload(GatewayMessageIn.model_validate({
        "id": "m-doc", "from": "6281234567890", "type": "document", "filename": "stok.xlsx",
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "media_b64": base64.b64encode(b"PK xlsx").decode(),
    }))
    doc = payload["entry"][0]["changes"][0]["value"]["messages"][0]["document"]
    assert doc["id"] == "m-doc" and doc["filename"] == "stok.xlsx"
    assert wa._gateway_media.pop("m-doc")[0] == b"PK xlsx"


def test_broken_attachment_is_refused():
    with provider("gateway"), patch("app.whatsapp.webhook.process_webhook_payload", new=AsyncMock()) as proc:
        resp = _post({"id": "m-bad", "from": "628111", "type": "image", "media_b64": "not base64!!"})
    assert resp.status_code == 400
    proc.assert_not_awaited()


def test_media_stash_is_bounded():
    for i in range(wa._MEDIA_MAX + 5):
        wa.stash_media(f"bound-{i}", b"x", "image/jpeg")
    assert len(wa._gateway_media) == wa._MEDIA_MAX
    assert "bound-0" not in wa._gateway_media
    wa._gateway_media.clear()


async def test_unknown_sender_through_the_gateway_is_ignored(captured):
    """Real pipeline (DB owner lookup included): a plain message from an
    unregistered number gets no reply (the bot serves the owner only)."""
    unknown = "62" + str(uuid.uuid4().int)[:11]
    msg = GatewayMessageIn.model_validate(
        {"id": f"gw-{uuid.uuid4().hex}", "from": unknown, "type": "text", "text": "halo, buka jam berapa?"}
    )
    with provider("gateway"):
        await process_webhook_payload(to_cloud_payload(msg))
    assert captured == []


async def test_receipt_request_through_the_gateway_is_answered_once_even_when_redelivered(captured):
    """Real pipeline: a STRUK request from any number is answered via the
    gateway's /send, and the same message id delivered twice is answered once."""
    unknown = "62" + str(uuid.uuid4().int)[:11]
    msg = GatewayMessageIn.model_validate(
        {"id": f"gw-{uuid.uuid4().hex}", "from": unknown, "type": "text", "text": "STRUK 001"}
    )
    with provider("gateway"):
        await process_webhook_payload(to_cloud_payload(msg))
        await process_webhook_payload(to_cloud_payload(msg))
    assert len(captured) == 1
    sent = captured[0]
    assert str(sent.url) == f"{GATEWAY_URL}/send"
    assert sent.headers["Authorization"] == f"Bearer {TOKEN}"
    body = json.loads(sent.content)
    assert body["to"] == unknown and "tidak ditemukan" in body["text"]


# ── Outbound routing ─────────────────────────────────────────────────────────

async def test_meta_stays_the_default_route(captured):
    settings = get_settings()
    with provider("cloud"), patch.object(settings, "whatsapp_access_token", "EAAG-real-looking"), \
            patch.object(settings, "whatsapp_phone_number_id", "1420306994492662"):
        await wa.send_text("+62 812-3456-7890", "halo")
    assert len(captured) == 1
    assert str(captured[0].url) == f"{wa.GRAPH_BASE}/1420306994492662/messages"
    assert json.loads(captured[0].content)["text"]["body"] == "halo"


async def test_gateway_text_goes_to_the_gateway_with_digits(captured):
    with provider("gateway"):
        await wa.send_text("+62 812-3456-7890", "halo")
    assert str(captured[0].url) == f"{GATEWAY_URL}/send"
    assert json.loads(captured[0].content) == {"to": "6281234567890", "text": "halo"}


async def test_a_hidden_number_is_answered_on_its_whatsapp_id(captured):
    with provider("gateway"):
        await wa.send_text("123456789012345@lid", "halo")
    assert json.loads(captured[0].content)["to"] == "123456789012345@lid"


async def test_nightly_alert_template_becomes_its_approved_wording_as_text(captured):
    settings = get_settings()
    with provider("gateway"):
        await wa.send_template("6281234567890", settings.whatsapp_alert_template,
                               ["stok menipis", "Kopi Senja", "Arabica tinggal ±2 hari lagi"], language="id")
        await wa.send_template("6281234567890", settings.whatsapp_alert_template,
                               ["low stock", "Kopi Senja", "Arabica 2 days left"], language="en")
    texts = [json.loads(r.content)["text"] for r in captured]
    assert texts[0] == "⚠️ Peringatan stok menipis untuk Kopi Senja: Arabica tinggal ±2 hari lagi. Balas pesan ini untuk detail."
    assert texts[1] == "⚠️ low stock alert for Kopi Senja: Arabica 2 days left. Reply to this message for details."


async def test_login_code_becomes_plain_text_on_the_gateway(captured):
    with provider("gateway"):
        await wa.send_otp_template("6281234567890", "482913")
    assert json.loads(captured[0].content)["text"] == "482913 adalah kode verifikasi Anda."


async def test_gateway_without_credentials_only_logs(captured, caplog):
    with provider("gateway", token="placeholder"), caplog.at_level("INFO", logger="whatsapp"):
        await wa.send_text("6281234567890", "halo")
    with provider("gateway", url=""):
        await wa.send_text("6281234567890", "halo")
    assert captured == []
    assert "DRY-RUN outbound WhatsApp gateway" in caplog.text


async def test_gateway_refusal_raises(monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda r: httpx.Response(503, json={"error": "not connected"})), **kw))
    with provider("gateway"), pytest.raises(httpx.HTTPStatusError):
        await wa.send_text("6281234567890", "halo")
