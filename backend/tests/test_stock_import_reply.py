"""fin-p1-7: a committed Excel stock import is never reported as a failure when the
AI composer is down. The AI and the file are mocked."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.whatsapp import processor

FACTS = {"saved": True, "items_created": ["A"], "items_updated": ["B", "C"], "total_rows": 3}


@asynccontextmanager
async def _session(_id):
    yield object()


def _run(compose):
    biz = SimpleNamespace(id="b")
    msg = {"document": {"id": "m", "filename": "stok.xlsx"}}
    with (
        patch("app.whatsapp.client.download_media", new=AsyncMock(return_value=(b"x", "m"))),
        patch("app.services.stock_import.parse_stock_template", return_value=([{}], ["w"])),
        patch("app.services.stock_import.apply_stock_template", new=AsyncMock(return_value=dict(FACTS))),
        patch.object(processor, "tenant_session", _session),
        patch("app.ai.composer.compose_reply", new=compose),
    ):
        return asyncio.run(processor._handle_document(biz, msg))


def test_a_down_composer_still_confirms_the_saved_import():
    _intent, reply = _run(AsyncMock(side_effect=RuntimeError("503")))
    assert "tersimpan" in reply and "2 barang diperbarui" in reply and "1 barang baru" in reply
    assert "gangguan" not in reply


def test_a_working_composer_reply_is_used():
    _intent, reply = _run(AsyncMock(return_value="Sip, stok masuk"))
    assert reply == "Sip, stok masuk"
