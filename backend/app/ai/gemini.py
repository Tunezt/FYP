"""Thin async wrapper around the google-genai SDK.

One Google AI API key covers everything: Flash for classification/tools/reply
composition, Pro for receipt vision, gemini-embedding-001 for embeddings.
Model names are env-configurable (Google's naming shifts).
"""
import logging
from functools import lru_cache

from google import genai
from google.genai import types

from app.core.config import get_settings

logger = logging.getLogger("gemini")

EMBEDDING_DIMS = 768  # matches vector(768) on receipts.embedding


@lru_cache
def _client() -> genai.Client:
    return genai.Client(api_key=get_settings().google_api_key)


async def force_tool_call(
    *,
    system: str,
    message: str,
    tools: list[types.FunctionDeclaration],
) -> types.FunctionCall | None:
    """One Flash call, forced to pick exactly one function (mode=ANY). The
    chosen function IS the intent classification — routing and argument
    extraction in a single round-trip."""
    settings = get_settings()
    response = await _client().aio.models.generate_content(
        model=settings.gemini_flash_model,
        contents=message,
        config=types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=tools)],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY")
            ),
            temperature=0.0,
        ),
    )
    calls = response.function_calls
    return calls[0] if calls else None


async def generate_text(*, system: str, message: str, temperature: float = 0.4) -> str:
    settings = get_settings()
    response = await _client().aio.models.generate_content(
        model=settings.gemini_flash_model,
        contents=message,
        config=types.GenerateContentConfig(system_instruction=system, temperature=temperature),
    )
    return (response.text or "").strip()


async def generate_json_from_image(
    *, system: str, prompt: str, image_bytes: bytes, mime_type: str, response_schema: dict
) -> dict:
    """Gemini Pro vision with structured output — receipt/ledger parsing."""
    settings = get_settings()
    response = await _client().aio.models.generate_content(
        model=settings.gemini_pro_model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0.0,
        ),
    )
    import json

    return json.loads(response.text or "{}")


async def embed_text(text: str) -> list[float]:
    settings = get_settings()
    result = await _client().aio.models.embed_content(
        model=settings.gemini_embedding_model,
        contents=text,
        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMS),
    )
    return list(result.embeddings[0].values)
