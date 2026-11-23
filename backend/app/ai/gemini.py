"""Thin async wrapper around the google-genai SDK.

One Google AI API key covers everything: Flash for classification/tools/reply
composition and receipt vision, gemini-embedding-001 for embeddings. Model names
are env-configurable (Google's naming shifts).

wa-gw-8: the key is on the FREE tier, where every model has its own small
requests-per-minute and requests-per-day quota. Every generation call walks a
chain (GEMINI_FLASH_MODEL, then GEMINI_FALLBACK_MODELS): a 429 moves on to the
next model, so the quotas add up. A model that just said 429 is skipped until
its quota is back. When the whole chain is out, `QuotaExhausted` is raised and
the WhatsApp layer tells the owner plainly (never the generic "ada gangguan").
"""
import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from google import genai
from google.genai import errors, types

from app.core.config import get_settings

logger = logging.getLogger("gemini")

# Pinned on purpose (wa-gw-7): google-genai 3.x defaults to "v1", where function
# calling is refused with a 400 — the live bot died on that.
API_VERSION = "v1beta"
EMBEDDING_DIMS = 768  # matches vector(768) on receipts.embedding

MAX_MINUTE_WAIT = 20  # seconds: a per-minute 429 shorter than this is waited out once


class QuotaExhausted(Exception):
    """Every model in the chain is rate limited. `daily` = the daily quota is
    gone (back at `resets_at`); otherwise it is a per-minute limit (try again soon)."""

    def __init__(self, daily: bool, resets_at: datetime | None = None):
        super().__init__("Gemini quota exhausted (daily)" if daily else "Gemini rate limit (per minute)")
        self.daily = daily
        self.resets_at = resets_at


def next_daily_reset(now: datetime | None = None) -> datetime:
    """Free-tier daily quotas reset at midnight Pacific time (UTC)."""
    now = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        pt = ZoneInfo("America/Los_Angeles")
        local = now.astimezone(pt)
        nxt = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return nxt.astimezone(timezone.utc)
    except Exception:  # no tz database (some Windows installs): assume PST, UTC-8
        pst = now.astimezone(timezone(timedelta(hours=-8)))
        nxt = (pst + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return nxt.astimezone(timezone.utc)


@lru_cache
def _client() -> genai.Client:
    return genai.Client(
        api_key=get_settings().google_api_key,
        http_options=types.HttpOptions(api_version=API_VERSION, timeout=20_000),  # a hung model must fall through, not stall the chat
    )


def text_chain() -> list[str]:
    s = get_settings()
    return _dedupe([s.gemini_flash_model, *s.gemini_fallback_models.split(",")])


def vision_chain() -> list[str]:
    s = get_settings()
    return _dedupe([s.gemini_pro_model, *s.gemini_fallback_models.split(",")])


def _dedupe(names: list[str]) -> list[str]:
    out: list[str] = []
    for n in (x.strip() for x in names):
        if n and n not in out:
            out.append(n)
    return out


_cooldown: dict[str, tuple[float, bool]] = {}  # model -> (monotonic time it is usable again, daily?)


def _retry_seconds(err: Exception) -> float | None:
    m = re.search(r"retryDelay'?\"?\s*:\s*'?\"?(\d+(?:\.\d+)?)s|retry in (\d+(?:\.\d+)?)s", str(err))
    return float(m.group(1) or m.group(2)) if m else None


async def _run(models: list[str], call):
    """Run `call(model)` down the chain; see the module docstring."""
    for attempt in (0, 1):
        waits: list[float] = []
        all_daily = True
        for model in models:
            until, daily = _cooldown.get(model, (0.0, False))
            now = time.monotonic()
            if until > now:
                waits.append(until - now)
                all_daily = all_daily and daily
                continue
            try:
                return await call(model)
            except errors.APIError as e:
                code = getattr(e, "code", None)
                if code == 429:
                    daily = "PerDay" in str(e) or "per day" in str(e).lower()
                    delay = _retry_seconds(e) or 60.0
                    secs = (next_daily_reset() - datetime.now(timezone.utc)).total_seconds() if daily else delay
                    _cooldown[model] = (time.monotonic() + secs, daily)
                    waits.append(secs)
                    all_daily = all_daily and daily
                    logger.warning("gemini %s: 429 (%s), trying the next model", model, "daily" if daily else "per-minute")
                elif code in (404, 500, 503, 504) and len(models) > 1:
                    # Model retired or overloaded: skip it for a while, never let it kill the chain.
                    # 503/500 answer at once and are brief: no cooldown, so the chain's one retry asks again.
                    # A 504 cost a full timeout: skip that model for a minute. 404 = gone for good.
                    pause = {404: 300, 504: 60}.get(code)
                    if pause:
                        _cooldown[model] = (time.monotonic() + pause, False)
                    all_daily = False
                    logger.warning("gemini %s: %s, trying the next model", model, code)
                else:
                    raise
        shortest = min(waits) if waits else 0
        if attempt == 0 and not all_daily and shortest <= MAX_MINUTE_WAIT:
            await asyncio.sleep(shortest + 1)  # a per-minute limit: wait it out once
            continue
        break
    raise QuotaExhausted(all_daily, next_daily_reset() if all_daily else None)


async def force_tool_call(
    *,
    system: str,
    message: str,
    tools: list[types.FunctionDeclaration],
) -> types.FunctionCall | None:
    """One Flash call, forced to pick exactly one function (mode=ANY). The
    chosen function IS the intent classification — routing and argument
    extraction in a single round-trip."""
    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=[types.Tool(function_declarations=tools)],
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="ANY")
        ),
        temperature=0.0,
    )
    response = await _run(
        text_chain(),
        lambda model: _client().aio.models.generate_content(
            model=model, contents=message, config=config
        ),
    )
    calls = response.function_calls
    return calls[0] if calls else None


async def generate_text(*, system: str, message: str, temperature: float = 0.4) -> str:
    config = types.GenerateContentConfig(system_instruction=system, temperature=temperature)
    response = await _run(
        text_chain(),
        lambda model: _client().aio.models.generate_content(
            model=model, contents=message, config=config
        ),
    )
    return (response.text or "").strip()


async def generate_json_from_image(
    *, system: str, prompt: str, image_bytes: bytes, mime_type: str, response_schema: dict
) -> dict:
    """Receipt/ledger/menu parsing: vision with structured output. Every model
    in the chain reads images."""
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=response_schema,
        temperature=0.0,
    )
    contents = [types.Part.from_bytes(data=image_bytes, mime_type=mime_type), prompt]
    response = await _run(
        vision_chain(),
        lambda model: _client().aio.models.generate_content(
            model=model, contents=contents, config=config
        ),
    )
    import json

    return json.loads(response.text or "{}")


async def embed_text(text: str) -> list[float]:
    settings = get_settings()
    result = await _run(
        [settings.gemini_embedding_model],
        lambda model: _client().aio.models.embed_content(
            model=model,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMS),
        ),
    )
    return list(result.embeddings[0].values)
