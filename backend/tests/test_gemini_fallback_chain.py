"""wa-gw-8: the free Gemini tier gives each model its own small quota, so a 429
moves down the chain; when everything is out the owner is told plainly.
The model is mocked: these tests never touch the live key."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from google.genai import errors

from app.ai import gemini
from app.whatsapp.processor import quota_reply

DAY = "429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProjectPerModel-FreeTier retryDelay': '30s'"
MINUTE = "429 RESOURCE_EXHAUSTED GenerateRequestsPerMinutePerProjectPerModel-FreeTier retryDelay': '5s'"


def _api(text, code=429):
    e = errors.ClientError(code, {"error": {"code": code, "message": text, "status": "X"}})
    return e


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    gemini._cooldown.clear()
    monkeypatch.setenv("GEMINI_FLASH_MODEL", "m-a")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "m-b, m-c, m-a")
    from app.core.config import get_settings

    get_settings.cache_clear()
    slept = []

    clock = [1000.0]
    monkeypatch.setattr(gemini.time, "monotonic", lambda: clock[0])

    async def fake_sleep(s):
        slept.append(s)
        clock[0] += s

    monkeypatch.setattr(gemini.asyncio, "sleep", fake_sleep)
    yield slept
    gemini._cooldown.clear()
    get_settings.cache_clear()


def test_chain_is_primary_then_fallbacks_without_duplicates():
    assert gemini.text_chain() == ["m-a", "m-b", "m-c"]


def test_daily_429_falls_to_next_model_and_skips_the_dead_one_next_time():
    calls = []

    async def call(model):
        calls.append(model)
        if model == "m-a":
            raise _api(DAY)
        return model

    assert asyncio.run(gemini._run(gemini.text_chain(), call)) == "m-b"
    assert asyncio.run(gemini._run(gemini.text_chain(), call)) == "m-b"
    assert calls == ["m-a", "m-b", "m-b"]  # m-a is not asked again today


def test_per_minute_429_waits_once_when_everything_is_limited(_reset):
    state = {"n": 0}

    async def call(model):
        state["n"] += 1
        if state["n"] <= 3:
            raise _api(MINUTE)
        return "ok"

    assert asyncio.run(gemini._run(gemini.text_chain(), call)) == "ok"
    assert _reset and _reset[0] <= gemini.MAX_MINUTE_WAIT + 1


def test_all_daily_raises_quota_exhausted_with_reset_time():
    async def call(model):
        raise _api(DAY)

    with pytest.raises(gemini.QuotaExhausted) as ei:
        asyncio.run(gemini._run(gemini.text_chain(), call))
    assert ei.value.daily and ei.value.resets_at > datetime.now(timezone.utc)


def test_other_errors_are_not_swallowed():
    async def call(model):
        raise _api("bad request", 400)

    with pytest.raises(errors.ClientError):
        asyncio.run(gemini._run(gemini.text_chain(), call))


def test_retired_model_is_skipped():
    async def call(model):
        if model == "m-a":
            raise _api("not found", 404)
        return model

    assert asyncio.run(gemini._run(gemini.text_chain(), call)) == "m-b"


def test_daily_reset_is_midnight_pacific():
    # 2026-11-23 23:00 UTC is 15:00 PST: the next midnight PST is 08:00 UTC on the 24th.
    assert gemini.next_daily_reset(datetime(2026, 11, 23, 23, 0, tzinfo=timezone.utc)) == datetime(
        2026, 11, 24, 8, 0, tzinfo=timezone.utc
    )


def test_owner_quota_message_is_plain_and_bilingual():
    msg = quota_reply(gemini.QuotaExhausted(True, datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)))
    assert "15.00 WIB" in msg and "16.00 WITA" in msg
    assert "batas pemakaian" in msg and "usage limit" in msg
    assert "gangguan" not in msg
    assert "1 menit" in quota_reply(gemini.QuotaExhausted(False))


def test_retry_delay_is_read_from_both_message_styles():
    assert gemini._retry_seconds(Exception("... Please retry in 55.04s.")) == 55.04
    assert gemini._retry_seconds(Exception("{'retryDelay': '30s'}")) == 30.0
    assert gemini._retry_seconds(Exception("nothing")) is None


def test_overloaded_models_never_read_as_a_daily_quota():
    # Live walk 24 Nov: one model out of daily quota + the rest 503 told the owner
    # "today's limit is used up" although only 1 model was. It is "busy", not daily.
    async def call(model):
        raise _api(DAY) if model == "m-a" else _api("high demand", 503)

    with pytest.raises(gemini.QuotaExhausted) as ei:
        asyncio.run(gemini._run(gemini.text_chain(), call))
    assert not ei.value.daily
    assert "1 menit" in quota_reply(ei.value)


def test_a_brief_503_is_retried_once_not_parked_for_minutes():
    state = {"n": 0}

    async def call(model):
        state["n"] += 1
        if state["n"] <= 3:  # whole chain overloaded on the first pass only
            raise _api("high demand", 503)
        return "ok"

    assert asyncio.run(gemini._run(gemini.text_chain(), call)) == "ok"
