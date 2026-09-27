"""Delivery receipts reach the log.

Meta answers 200 when it accepts an outbound message; whether the message
reached the phone arrives later as a `statuses` webhook. Before wa-2 those were
dropped unread, so a reply that Meta accepted and then failed to deliver left
no trace at all (seen live on 27 Sep 2026). These tests pin that a failure is
logged with Meta's code and reason, a success is logged quietly, and a receipt
is never mistaken for an incoming message.
"""
import logging
from unittest.mock import AsyncMock, patch

from app.whatsapp.processor import process_webhook_payload


def _payload(status: dict) -> dict:
    return {"entry": [{"changes": [{"value": {"messaging_product": "whatsapp", "statuses": [status]}}]}]}


async def test_a_failed_delivery_is_logged_with_metas_code_and_reason(caplog):
    status = {
        "id": "wamid.FAILED1",
        "status": "failed",
        "recipient_id": "628115819400",
        "errors": [
            {
                "code": 131026,
                "title": "Message undeliverable",
                "message": "Message undeliverable",
                "error_data": {"details": "Recipient is not a valid WhatsApp user"},
            }
        ],
    }
    with caplog.at_level(logging.INFO, logger="processor"):
        await process_webhook_payload(_payload(status))

    failures = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(failures) == 1
    line = failures[0].getMessage()
    assert "628115819400" in line and "wamid.FAILED1" in line
    assert "131026" in line and "Message undeliverable" in line
    assert "Recipient is not a valid WhatsApp user" in line


async def test_a_failure_without_details_still_names_the_code(caplog):
    status = {"id": "wamid.F2", "status": "failed", "recipient_id": "1", "errors": [{"code": 130497, "title": "Restricted"}]}
    with caplog.at_level(logging.WARNING, logger="processor"):
        await process_webhook_payload(_payload(status))
    assert "130497 Restricted" in caplog.records[-1].getMessage()


async def test_a_successful_delivery_is_an_info_line_not_a_warning(caplog):
    status = {"id": "wamid.OK", "status": "delivered", "recipient_id": "628115819400"}
    with caplog.at_level(logging.INFO, logger="processor"):
        await process_webhook_payload(_payload(status))

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Delivery delivered to 628115819400" in r.getMessage() for r in caplog.records)


async def test_a_receipt_is_never_processed_as_an_incoming_message():
    status = {"id": "wamid.S", "status": "sent", "recipient_id": "628115819400"}
    with patch("app.whatsapp.processor._process_message", new=AsyncMock()) as handle:
        await process_webhook_payload(_payload(status))
    handle.assert_not_awaited()
