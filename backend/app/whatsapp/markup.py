"""One pass over every outgoing WhatsApp text, right before it is sent (fin-wa-format).

Answers are model-written, and a model that was told to use WhatsApp markup
still writes Markdown now and then. WhatsApp shows Markdown literally: the owner
sees **Rp 1.250.000** with the stars, "# Ringkasan" with the hash, a table as a
row of pipes. This turns it into WhatsApp's own markup, and writes Rupiah the
Indonesian way (Rp 1.250.000). Fixed texts pass through unchanged, because they
already are WhatsApp markup; running it twice changes nothing.

WhatsApp's markup: *bold*, _italic_, ~strike~, ```mono```, `inline`,
"- " / "* " bullets, "1. " lists, "> " quote.
"""
import re

_FENCE = re.compile(r"```.*?```", re.S)

_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*[*•]\s+|^\s+-\s+")  # "+ " is left alone: receipts use it for modifiers
_NUMBERED = re.compile(r"^\s*(\d{1,3})[.)]\s+")

_BOLD3 = re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*")
_BOLD2 = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_BOLD_UNDER = re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)")
_STRIKE2 = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_RUPIAH = re.compile(r"\bRp\.?\s*(\d[\d.,]*\d|\d)")


def _rupiah_number(n: str) -> str:
    """'1250000' / '1,250,000' / '1.250.000,00' -> '1.250.000'. Anything else (a
    decimal like '1,5' in "Rp 1,5 juta") is left as written."""
    if m := re.fullmatch(r"(\d{1,3}(?:\.\d{3})+)(?:,(\d{1,2}))?", n):
        whole, cents = m[1], m[2]
    elif m := re.fullmatch(r"(\d{1,3}(?:,\d{3})+)(?:\.(\d{1,2}))?", n):
        whole, cents = m[1].replace(",", "."), m[2]
    elif re.fullmatch(r"\d+", n):
        whole, cents = f"{int(n):,}".replace(",", "."), None
    else:
        return n
    if cents and int(cents):
        return f"{whole},{cents}"
    return whole


def _line(line: str) -> str:
    if _TABLE_ROW.match(line):
        if _TABLE_RULE.match(line):
            return None
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        line = " · ".join(c for c in cells if c)
    if _HR.match(line):
        return ""
    line = _BOLD3.sub(r"*\1*", line)
    line = _BOLD2.sub(r"*\1*", line)
    line = _BOLD_UNDER.sub(r"*\1*", line)
    line = _STRIKE2.sub(r"~\1~", line)
    line = _LINK.sub(lambda m: m[2] if m[1].strip() == m[2] else f"{m[1]}: {m[2]}", line)
    if m := _HEADING.match(line):
        title = m[1].strip("*").strip()
        line = f"*{title}*"
    line = _BULLET.sub("- ", line, count=1)
    line = _NUMBERED.sub(r"\1. ", line, count=1)
    line = _RUPIAH.sub(lambda m: "Rp " + _rupiah_number(m[1]), line)
    return line.rstrip()


def _segment(text: str) -> str:
    return "\n".join(x for x in (_line(l) for l in text.split("\n")) if x is not None)


def to_whatsapp(text: str) -> str:
    """The text as WhatsApp should show it. Code fences are left untouched."""
    if not text:
        return text
    pieces = _FENCE.split(text)
    fences = _FENCE.findall(text)
    out = _segment(pieces[0])
    for fence, piece in zip(fences, pieces[1:]):
        out += fence + _segment(piece)
    return re.sub(r"\n{3,}", "\n\n", out).strip("\n")
