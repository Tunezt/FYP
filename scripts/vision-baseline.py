"""Run every image in docs/vision-test-samples/ through the REAL vision path and
append the results to docs/vision-results.md (roadmap M1-T1 / M1-T2 / M1-T3).

    cd backend && ./.venv/Scripts/python.exe ../scripts/vision-baseline.py [--label "baseline"]

It calls `app.ai.vision.parse_business_document` exactly as the WhatsApp image
handler does, and wraps the google-genai `generate_content` call so the
model's raw text is captured before JSON parsing. Nothing is tuned here; the
prompt and schema come from app/ai/vision.py unchanged. Results are appended,
never overwritten, so before/after runs sit side by side.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
SAMPLES = REPO / "docs" / "vision-test-samples"
RESULTS = REPO / "docs" / "vision-results.md"

sys.path.insert(0, str(BACKEND))

from app.ai import gemini  # noqa: E402
from app.ai.vision import parse_business_document  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services.receipts import needs_confirmation  # noqa: E402


async def run_one(path: Path) -> dict:
    image_bytes = path.read_bytes()
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    captured: dict = {}

    client = gemini._client()
    real_generate = client.aio.models.generate_content

    async def recording_generate(*args, **kwargs):
        response = await real_generate(*args, **kwargs)
        captured["raw_text"] = response.text
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            captured["prompt_tokens"] = getattr(usage, "prompt_token_count", None)
            captured["output_tokens"] = getattr(usage, "candidates_token_count", None)
        return response

    client.aio.models.generate_content = recording_generate  # type: ignore[method-assign]
    started = time.perf_counter()
    error: str | None = None
    parsed: dict | None = None
    try:
        parsed = await parse_business_document(image_bytes, mime_type)
    except Exception as exc:  # record failures, do not hide them
        error = f"{type(exc).__name__}: {exc}"
    finally:
        client.aio.models.generate_content = real_generate  # type: ignore[method-assign]
    latency_ms = int((time.perf_counter() - started) * 1000)

    return {
        "file": path.name,
        "bytes": len(image_bytes),
        "mime_type": mime_type,
        "latency_ms": latency_ms,
        "raw_text": captured.get("raw_text"),
        "prompt_tokens": captured.get("prompt_tokens"),
        "output_tokens": captured.get("output_tokens"),
        "parsed": parsed,
        "error": error,
        "gate_triggered": needs_confirmation(parsed) if parsed is not None else None,
    }


def render(result: dict) -> str:
    parsed = result["parsed"]
    lines = [f"#### `{result['file']}`", ""]
    lines.append(
        f"- size {result['bytes'] // 1024} KB · {result['mime_type']} · latency {result['latency_ms']} ms"
        + (
            f" · tokens in/out {result['prompt_tokens']}/{result['output_tokens']}"
            if result["prompt_tokens"] is not None
            else ""
        )
    )
    if result["error"]:
        lines.append(f"- **ERROR:** `{result['error']}`")
    if parsed is not None:
        lines.append(
            f"- document_type `{parsed.get('document_type')}` · confidence **{parsed.get('confidence')}** · "
            f"{len(parsed.get('items', []))} items · total_amount {parsed.get('total_amount')} · "
            f"ambiguities {len(parsed.get('ambiguities', []))}"
        )
        lines.append(
            f"- confirmation gate: **{'TRIGGERED' if result['gate_triggered'] else 'not triggered (auto-commit)'}**"
        )
    lines.append("")
    lines.append("Raw model output (verbatim):")
    lines.append("")
    lines.append("```json")
    lines.append((result["raw_text"] or "<no text returned>").strip())
    lines.append("```")
    if parsed is not None:
        lines.append("")
        lines.append("Parsed structure after `parse_business_document` defaults:")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(parsed, ensure_ascii=False, indent=2))
        lines.append("```")
    lines.append("")
    return "\n".join(lines)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    args = ap.parse_args()

    settings = get_settings()
    images = sorted(p for p in SAMPLES.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
    if not images:
        sys.exit(f"no images in {SAMPLES}")

    header = [
        "",
        f"### {args.label} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"- vision model: `{settings.gemini_pro_model}` (setting `GEMINI_PRO_MODEL`)",
        f"- prompt/schema: `backend/app/ai/vision.py` at `git rev-parse --short HEAD` = "
        f"`{__import__('subprocess').run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True, cwd=REPO).stdout.strip()}`",
        f"- gate rule: `app.services.receipts.needs_confirmation` — confidence != high, or any ambiguity, or no items",
        f"- images: {len(images)}",
        "",
    ]
    sections = [render(await run_one(p)) for p in images]
    with RESULTS.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(header) + "\n" + "\n".join(sections))
    print(f"appended {len(images)} results to {RESULTS}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
