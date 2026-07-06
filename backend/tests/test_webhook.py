import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

client = TestClient(app)


def test_verification_handshake_success():
    settings = get_settings()
    resp = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": settings.whatsapp_verify_token,
            "hub.challenge": "424242",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "424242"


def test_verification_handshake_wrong_token():
    resp = client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
    )
    assert resp.status_code == 403


def _signed_post(body: dict, secret: str):
    raw = json.dumps(body).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/whatsapp",
        content=raw,
        headers={"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"},
    )


def test_valid_signature_accepted_and_processing_scheduled():
    settings = get_settings()
    with (
        patch.object(settings, "whatsapp_app_secret", "test-secret"),
        patch("app.whatsapp.webhook.process_webhook_payload", new=AsyncMock()) as processor,
    ):
        resp = _signed_post({"entry": []}, "test-secret")
        assert resp.status_code == 200
        processor.assert_awaited_once_with({"entry": []})


def test_invalid_signature_rejected():
    settings = get_settings()
    with (
        patch.object(settings, "whatsapp_app_secret", "test-secret"),
        patch("app.whatsapp.webhook.process_webhook_payload", new=AsyncMock()) as processor,
    ):
        raw = b'{"entry": []}'
        sig = hmac.new(b"attacker-secret", raw, hashlib.sha256).hexdigest()
        resp = client.post(
            "/webhooks/whatsapp",
            content=raw,
            headers={"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"},
        )
        assert resp.status_code == 403
        processor.assert_not_awaited()


def test_missing_signature_rejected():
    settings = get_settings()
    with patch.object(settings, "whatsapp_app_secret", "test-secret"):
        resp = client.post("/webhooks/whatsapp", json={"entry": []})
        assert resp.status_code == 403


def test_placeholder_secret_rejected_in_production():
    settings = get_settings()
    with (
        patch.object(settings, "whatsapp_app_secret", "placeholder"),
        patch.object(settings, "environment", "production"),
    ):
        resp = client.post("/webhooks/whatsapp", json={"entry": []})
        assert resp.status_code == 500
