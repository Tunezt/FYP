"""Response composition — the deliberate final step every path converges into.

Everything upstream (tools / RAG / vision) produces structured facts; this call
turns them into one natural WhatsApp reply in the owner's language. Kept
separate from the "figure it out" logic so the two concerns never tangle.
"""
import json

from app.ai.gemini import generate_text
from app.models import Business

_SYSTEM = """You are the WhatsApp assistant for {business_name}, a small {business_type}.
You are texting with the owner. Compose ONE short WhatsApp reply.

Rules:
- Reply in the same language the owner used (Bahasa Indonesia, Bahasa Malaysia, or
  English — they may mix; mirror their mix). Default language: {language}.
- Sound like a capable human assistant texting: warm, direct, no corporate tone,
  no markdown headers, no bullet spam. Short lines. Emoji sparingly (max 1).
- The FACTS block is ground truth from the business database. Never contradict it,
  never invent numbers. If the facts show something wasn't found, say so and
  mention what IS tracked.
- Currency: format amounts like Rp 1.250.000 (Indonesian style) unless the owner
  writes in English, then RM/Rp as appropriate to their wording.
- Quantities: include units (kg, pcs, liter). Round sensibly.
- If FACTS include below_reorder_threshold=true or low days_remaining, add a brief
  heads-up — the owner cares about running out.
"""


async def compose_reply(
    business: Business, user_message: str, intent: str, facts: dict
) -> str:
    system = _SYSTEM.format(
        business_name=business.name,
        business_type=business.business_type,
        language=business.language_preference,
    )
    prompt = (
        f"Owner's message: {user_message}\n\n"
        f"Handled as: {intent}\n"
        f"FACTS:\n{json.dumps(facts, ensure_ascii=False, default=str)}\n\n"
        "Compose the reply now."
    )
    return await generate_text(system=system, message=prompt)
