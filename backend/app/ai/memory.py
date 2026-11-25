"""Short conversation memory for the WhatsApp assistant (p2-9).

Every question used to be read on its own, so a follow-up that leans on the one
before ("kalau minggu lalu?", "kalau croissant?", "yang tadi berapa?") had
nothing to lean on. The last few turns of one owner number with one cafe are
kept here and shown to the classifier, which fills in what the follow-up leaves
out (the metric, the item, the period). The answer itself still comes from the
tools and the database; memory only helps pick the tool and its arguments.

Kept in memory, per owner number and cafe, for 30 minutes: a chat that pauses
longer starts fresh, and a restart forgets (the owner just asks in full).
"""
import json
import time
from collections import deque

MAX_TURNS = 3
TTL_SECONDS = 30 * 60

_chats: dict[str, deque] = {}


def chat_key(phone: str, business_id) -> str:
    return f"{phone}:{business_id}"


def recent(key: str) -> list[dict]:
    """The remembered turns, oldest first, that are still fresh."""
    now = time.monotonic()
    return [t for t in _chats.get(key, ()) if now - t["at"] < TTL_SECONDS]


def remember(key: str, said: str, tool: str, args: dict, reply: str) -> None:
    turns = _chats.setdefault(key, deque(maxlen=MAX_TURNS))
    first_line = next((line for line in (reply or "").splitlines() if line.strip()), "")
    turns.append({"at": time.monotonic(), "said": said, "tool": tool, "args": args, "reply": first_line[:160]})


def with_history(message: str, turns: list[dict]) -> str:
    """The classifier's input: the earlier turns, then the current message."""
    if not turns:
        return message
    lines = ["Earlier in this chat (oldest first):"]
    for t in turns:
        lines.append(f"- Owner: {t['said']}")
        lines.append(f"  Handled as: {t['tool']} {json.dumps(t['args'], ensure_ascii=False, default=str)}")
        if t["reply"]:
            lines.append(f"  Answer began: {t['reply']}")
    lines += ["", f"Current message: {message}"]
    return "\n".join(lines)
