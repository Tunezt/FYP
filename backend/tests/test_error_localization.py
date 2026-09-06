"""Every user-facing HTTPException detail must be Indonesian — the frontend
shows `e.detail` directly to owners/staff, so an English string here leaks
straight into the UI (session-2 Part 2 regression guard).

Heuristic: each detail string literal (including f-string literal fragments)
must contain at least one common Indonesian marker word. Word-boundary matched,
so English words containing substrings ("valid", "yang" in "Yangtze") can't
sneak a pass. If this test fails on a legitimately non-user-facing string,
prefer localizing it anyway — consistency is cheaper than an allowlist.
"""
import ast
import re
from pathlib import Path

BACKEND_APP = Path(__file__).resolve().parent.parent / "app"

SCANNED_FILES = [
    BACKEND_APP / "api" / "auth.py",
    BACKEND_APP / "api" / "pos.py",
    BACKEND_APP / "api" / "dashboard.py",
    BACKEND_APP / "api" / "menu.py",
    BACKEND_APP / "whatsapp" / "webhook.py",
    BACKEND_APP / "core" / "deps.py",
]

INDONESIAN_MARKERS = [
    "tidak", "belum", "sudah", "salah", "silakan", "khusus", "ditemukan",
    "minta", "coba", "lagi", "kedaluwarsa", "percobaan", "verifikasi",
    "dikonfigurasi", "dikenali", "cocok", "sah", "usaha", "kasir", "pemilik",
    "stok", "kode", "tautan", "peringatan", "staf", "barang", "terdaftar",
    "dinonaktifkan", "berlaku", "masuk", "tersisa", "nomor", "fitur", "akun",
]

_MARKER_RE = re.compile(
    r"\b(" + "|".join(INDONESIAN_MARKERS) + r")\b", re.IGNORECASE
)


def _detail_literals(tree: ast.AST) -> list[str]:
    """Literal text of every `detail=` keyword on an HTTPException call —
    plain strings whole, f-strings as their joined literal fragments."""
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "HTTPException"):
            continue
        for kw in node.keywords:
            if kw.arg != "detail":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                found.append(kw.value.value)
            elif isinstance(kw.value, ast.JoinedStr):
                literal = "".join(
                    part.value
                    for part in kw.value.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
                found.append(literal)
    return found


def test_every_http_exception_detail_is_indonesian():
    offenders: list[str] = []
    total = 0
    for path in SCANNED_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for detail in _detail_literals(tree):
            total += 1
            if not _MARKER_RE.search(detail):
                offenders.append(f"{path.name}: {detail!r}")
    assert total >= 15, "scan looks broken — far fewer details found than exist"
    assert not offenders, (
        "HTTPException detail(s) without Indonesian text (frontend shows these "
        "verbatim to users):\n  " + "\n  ".join(offenders)
    )
