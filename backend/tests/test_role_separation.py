"""Role separation is enforced with real permission checks, not hidden UI:
every wrong-scope token gets an explicit 403 from the auth dependency BEFORE
any query runs (so these tests need no database).

Matrix: {pos, owner, pairing, register, garbage, none} × {owner routes, pos routes}.
"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.security import create_pairing_token, create_registration_token, create_token
from app.main import app

client = TestClient(app)

BID = "11111111-1111-1111-1111-111111111111"
SID = "22222222-2222-2222-2222-222222222222"

OWNER_ROUTES = [
    ("GET", "/api/overview"),
    ("GET", "/api/sales"),
    ("GET", "/api/items"),
    ("GET", "/api/pnl"),
    ("GET", "/api/alerts"),
    ("GET", "/auth/staff"),
    ("POST", "/auth/pos-pairing"),
    ("POST", "/auth/menu-link"),
]

POS_ROUTES = [
    ("GET", "/pos/items"),
    ("POST", "/pos/sales"),
    ("GET", "/pos/tickets"),
    ("GET", "/pos/kitchen"),
]


def _request(method: str, path: str, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.request(method, path, headers=headers, json={} if method == "POST" else None)


def pos_token() -> str:
    return create_token(business_id=BID, scope="pos", staff_id=SID)


def owner_token() -> str:
    return create_token(business_id=BID, scope="owner")


def test_pos_token_rejected_on_every_owner_route():
    token = pos_token()
    for method, path in OWNER_ROUTES:
        resp = _request(method, path, token)
        assert resp.status_code == 403, f"{method} {path} → {resp.status_code}"
        assert "owner" in resp.json()["detail"]


def test_owner_token_rejected_on_pos_routes():
    token = owner_token()
    for method, path in POS_ROUTES:
        resp = _request(method, path, token)
        assert resp.status_code == 403, f"{method} {path} → {resp.status_code}"


def test_pairing_token_grants_no_data_access():
    # The kiosk URL token may only boot the kiosk — never read or write data.
    token = create_pairing_token(BID)
    for method, path in OWNER_ROUTES + POS_ROUTES:
        resp = _request(method, path, token)
        assert resp.status_code == 403, f"{method} {path} → {resp.status_code}"


def test_registration_token_grants_no_data_access():
    token = create_registration_token("628123456789")
    for method, path in OWNER_ROUTES + POS_ROUTES:
        resp = _request(method, path, token)
        assert resp.status_code == 403, f"{method} {path} → {resp.status_code}"


def test_missing_and_garbage_tokens_are_401():
    for method, path in OWNER_ROUTES + POS_ROUTES:
        assert _request(method, path, None).status_code == 401
        assert _request(method, path, "not-a-jwt").status_code == 401


def test_expired_owner_token_is_401():
    token = create_token(business_id=BID, scope="owner", ttl_minutes=-1)
    assert _request("GET", "/api/overview", token).status_code == 401


async def test_whatsapp_from_unregistered_number_never_reaches_business_logic():
    """WhatsApp owner-only commands: senders that don't match businesses.
    owner_phone get a polite rejection and the router is never invoked."""
    from app.whatsapp import processor

    message = {
        "id": "wamid.role-sep-test",
        "from": "60000000000",
        "type": "text",
        "text": {"body": "berapa penjualan hari ini?"},
    }
    with (
        patch.object(processor, "_resolve_business", new=AsyncMock(return_value=None)),
        patch.object(processor, "send_text", new=AsyncMock()) as sender,
        patch.object(processor, "_handle_text", new=AsyncMock()) as handler,
    ):
        await processor._process_message(message)

    handler.assert_not_awaited()
    sender.assert_awaited_once()
    assert "belum terdaftar" in sender.await_args.args[1]
