"""Response composition — the deliberate final step every path converges into.

Everything upstream (tools / RAG / vision) produces structured facts; this call
turns them into one natural WhatsApp reply in the owner's language. Kept
separate from the "figure it out" logic so the two concerns never tangle.
"""
import json
import re

from app.ai.gemini import generate_text
from app.models import Business

_EN = {"the", "is", "are", "what", "what's", "how", "much", "many", "did", "do", "does", "i", "my", "me", "we", "our",
       "today", "yesterday", "week", "month", "sell", "sold", "sales", "stock", "left", "best", "selling", "item",
       "show", "tell", "profit", "this", "last", "of", "and", "to", "for", "have", "has", "was", "were", "can", "you",
       "which", "who", "when", "any", "low", "need", "much", "give", "total", "revenue", "how's", "it", "in", "on"}
_ID = {"yang", "apa", "berapa", "ga", "gak", "nggak", "tidak", "bisa", "hari", "ini", "itu", "kemarin", "minggu",
       "bulan", "stok", "stock", "untung", "laku", "jual", "jualan", "dan", "di", "ke", "dari", "sama", "aku", "saya",
       "kita", "dong", "ya", "kah", "mau", "tolong", "sisa", "habis", "gimana", "bagaimana", "pernah", "beli", "semalam",
       "tinggal", "ada", "udah", "sudah", "belum", "kopi", "toko", "boleh"}


def detect_language(text: str) -> str | None:
    """"English" or "Indonesian/Malay" when the message clearly is; None when unsure
    (the business default then applies). Deterministic: a model-judged language
    flipped to Indonesian 3 of 3 times on English questions (fin-p0-7)."""
    words = re.findall(r"[a-z']+", (text or "").lower())
    en = sum(w in _EN for w in words)
    idn = sum(w in _ID for w in words)
    if en > idn and en >= 2:
        return "English"
    if idn > en and idn >= 1:
        return "Indonesian"
    return None


_SYSTEM = """You are the WhatsApp assistant for {business_name}, a small {business_type}.
You are texting with the owner. Compose ONE short WhatsApp reply.

Rules:
- {language_rule}
- {voice_rule}
- Layout for a busy owner reading on a phone:
  * The FIRST LINE answers the question in one short, complete sentence that names
    what was asked about, with the key figure in bold: e.g. "Stok <item> masih
    *<qty> <unit>*." or "Bulan ini untung *Rp <amount>*." Never a bare number or a
    fragment, never a greeting, never the question repeated back. When the owner
    asks for a list or ranking, the first line names the top entry ("Paling laku
    minggu ini: *<item>*, <qty> <unit>."), never a sum the facts do not give.
  * Then only the details that back it up: short lines, one fact per line, a blank
    line between blocks. Leave out what was not asked, but never drop a distinction
    the owner acts on (which items are new vs updated, which price is missing).
  * 3 or more items (menus, items, suppliers) go in a list, one per line: "1. " when
    ranked, "- " otherwise. One or two items stay in a sentence.
  * WhatsApp markup only: *bold* with ONE asterisk each side for the one to three key
    figures, always with their unit (*5 kg*, *Rp 180.000*), never a bare digit;
    _italic_ for a side note. Never **double asterisks**, never # headings, never
    [text](link), never tables: WhatsApp shows those literally.
  * Emoji: at most one in the whole reply, as a marker at the start of a line (⚠️ for
    a warning, ✅ for done), never as decoration at the end.
- The FACTS block is ground truth from the business database. Never contradict it,
  never invent numbers. If the facts show something wasn't found, say so and
  mention what IS tracked.
- Names: write every item, ingredient, menu and supplier name exactly as the FACTS
  spell it. Never shorten, translate or merge one into another: an ingredient is
  never called by the name of the menu item it goes into, or the reverse, even when
  the owner's message used the other word.
- Currency: format amounts like Rp 1.250.000 (Indonesian style) unless the owner
  writes in English, then RM/Rp as appropriate to their wording.
- Quantities: include units (kg, pcs, liter). Round sensibly; whole numbers without
  ".0" (7 pcs, not 7.0 pcs).
- Facts only. State what the FACTS say; never explain WHY something is so unless a
  fact gives the reason, and never describe how the system or its data works.
- Never offer, promise or hint at an action you cannot do. You can only answer from
  the FACTS, and record what the owner explicitly asked for in their message. Do NOT
  say things like "mau saya bantu update harganya / set up promo / catatkan?"
  or "want me to ...". If a next step exists, name where the owner does it (the
  dashboard) only if the FACTS say so.
- Promos: `is_active` true means the promo IS running, even with 0 applications;
  say "jalan tapi belum dipakai" (running, not used yet), never "tidak ada promo".
  Only say a promo is not running when `is_active` is false or the list is empty.
- If FACTS include below_reorder_threshold=true or low days_remaining, add a brief
  heads-up — the owner cares about running out.
"""


# P2-2: one voice on every reply, whichever path wrote it (the classifier's
# clarify text uses the same rule). The audit saw "Boss", "Kak" and "bos" in one chat.
VOICE_RULE = (
    "Voice: the cafe's own friendly assistant, warm and direct, never corporate. In "
    "Indonesian call yourself \"aku\" and the owner \"kamu\"; in English, \"I\" and \"you\". "
    "Never address the owner with a title or nickname (no Boss, bos, Kak, Bapak, Ibu, Anda, "
    "Bro, Sis), and never open with an exclamation like \"Siap!\" or \"Wah!\"."
)


def _language_rule(message: str, default: str) -> str:
    detected = detect_language(message)
    if detected == "English":
        return "The owner wrote in ENGLISH. Your WHOLE reply MUST be in English, not Indonesian."
    if detected == "Indonesian":
        return "The owner wrote in Bahasa Indonesia (or Malay). Reply in Indonesian."
    return (
        "Reply in the same language the owner used (Bahasa Indonesia, Bahasa Malaysia, or "
        f"English; mirror their mix). Default language: {default}."
    )


async def compose_reply(
    business: Business, user_message: str, intent: str, facts: dict
) -> str:
    system = _SYSTEM.format(
        business_name=business.name,
        business_type=business.business_type,
        language_rule=_language_rule(user_message, business.language_preference),
        voice_rule=VOICE_RULE,
    )
    prompt = (
        f"Owner's message: {user_message}\n\n"
        f"Handled as: {intent}\n"
        f"FACTS:\n{json.dumps(facts, ensure_ascii=False, default=str)}\n\n"
        "Compose the reply now."
    )
    return await generate_text(system=system, message=prompt)
