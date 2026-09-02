"""Run the vision sample set through the REAL vision path, score it against the
manifest's ground truth, and append everything to docs/vision-results.md
(roadmap M1-T1 / M1-T2 / M1-T3).

    cd backend && ./.venv/Scripts/python.exe ../scripts/vision-baseline.py [--label "..."] [--only substring]

It calls `app.ai.vision.parse_business_document` exactly as the WhatsApp image
handler does, and wraps the google-genai `generate_content` call so the model's
raw text is captured before JSON parsing. Nothing is tuned here; the prompt and
schema come from app/ai/vision.py unchanged. Results are appended, never
overwritten, so before/after runs sit side by side.

Scoring (per image, against docs/vision-test-samples/manifest.json):
  * line match   — a true line counts as matched when a parsed item's name is
                   similar (difflib ≥ 0.6 after lower-casing) AND the quantity is
                   equal AND (when the document shows it) the line total is equal
  * total ok     — parsed total_amount equals the document total
  * fabricated   — parsed unit_price > 0 on a document that shows no unit prices AND
                   not equal to line_total ÷ quantity (that case is "derived": reported,
                   not an error — it is arithmetic, not a guess)
  * invented     — parsed unit where the document writes none, other than '' or the
                   generic 'pcs'
  * field errors — unmatched true lines + extra parsed lines + fabricated +
                   invented + (0 if total ok else 1)
  * SILENT ERROR — gate NOT triggered while field errors > 0: a wrong number that
                   would have been written without asking. This is the metric that
                   matters (roadmap M1-T3, M9-T6).
For the unreadable control, any parsed item with the gate open is a silent error.
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import mimetypes
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
SAMPLES = REPO / "docs" / "vision-test-samples"
MANIFEST = SAMPLES / "manifest.json"
RESULTS = REPO / "docs" / "vision-results.md"
RUNS = REPO / "docs" / "vision-runs"

sys.path.insert(0, str(BACKEND))

from app.ai import gemini  # noqa: E402
from app.ai.vision import parse_business_document  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services.receipts import needs_confirmation  # noqa: E402


# ── running ─────────────────────────────────────────────────────────────────

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


# ── scoring ─────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().replace(".", "").split())


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def score(result: dict, truth: dict) -> dict:
    parsed = result["parsed"] or {}
    items = list(parsed.get("items") or [])
    true_items = truth["items"]
    gate = bool(result["gate_triggered"]) if result["parsed"] is not None else True

    if truth["category"] == "unreadable_control":
        fabricated_items = len(items)
        silent = (not gate) and fabricated_items > 0
        return {
            "matched": 0, "true": len(true_items), "extra": fabricated_items,
            "total_ok": False, "fabricated_prices": 0, "invented_units": 0,
            "field_errors": fabricated_items, "gate": gate, "silent_error": silent,
            "control_ok": gate or fabricated_items == 0,
        }

    used: set[int] = set()
    matched = 0
    for t in true_items:
        best, best_i = 0.0, -1
        for i, p in enumerate(items):
            if i in used:
                continue
            r = difflib.SequenceMatcher(None, _norm(t["name"]), _norm(p.get("name"))).ratio()
            if r > best:
                best, best_i = r, i
        if best_i < 0 or best < 0.6:
            continue
        p = items[best_i]
        qty_ok = abs(_num(p.get("quantity")) - float(t["qty"])) < 1e-6
        total_ok = True if not t["line_total"] else abs(_num(p.get("line_total")) - t["line_total"]) < 0.5
        if qty_ok and total_ok:
            matched += 1
            used.add(best_i)
    extra = max(0, len(items) - len(used))
    total_ok = abs(_num(parsed.get("total_amount")) - truth["total"]) < 0.5
    # Unit prices on a document that shows none: line_total ÷ quantity is
    # arithmetic (derived, informational); anything else is fabricated (error).
    fabricated = derived = 0
    if not truth["shows_unit_price"]:
        for p in items:
            up, lt, q = _num(p.get("unit_price")), _num(p.get("line_total")), _num(p.get("quantity"))
            if up > 0:
                if lt > 0 and q > 0 and abs(up * q - lt) <= 1:
                    derived += 1
                else:
                    fabricated += 1
    # Units on a document that writes none: '' and the generic 'pcs' are
    # acceptable; anything else ('kg', 'liter', 'bottle') is invented (error).
    invented = 0
    if not truth["shows_unit"]:
        written = {_norm(t["unit"]) for t in true_items if t["unit"]} | {"", "pcs"}
        invented = sum(1 for p in items if _norm(p.get("unit")) not in written)
    field_errors = (len(true_items) - matched) + extra + fabricated + invented + (0 if total_ok else 1)
    return {
        "matched": matched, "true": len(true_items), "extra": extra, "total_ok": total_ok,
        "fabricated_prices": fabricated, "derived_prices": derived, "invented_units": invented,
        "field_errors": field_errors, "gate": gate, "silent_error": (not gate) and field_errors > 0,
        "control_ok": None,
    }


# ── rendering ───────────────────────────────────────────────────────────────

def render(result: dict, truth: dict | None, sc: dict | None, include_raw: bool = True) -> str:
    parsed = result["parsed"]
    lines = [f"#### `{result['file']}`" + (f" — {truth['category']}" if truth else ""), ""]
    if truth:
        lines.append(f"- provenance: {truth['provenance']}")
    lines.append(
        f"- size {result['bytes'] // 1024 if result.get('bytes') else '?'} KB · {result.get('mime_type') or '?'} · latency {result['latency_ms']} ms"
        + (f" · tokens in/out {result['prompt_tokens']}/{result['output_tokens']}"
           if result["prompt_tokens"] is not None else "")
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
    if sc:
        if truth and truth["category"] == "unreadable_control":
            lines.append(
                f"- score: {sc['extra']} fabricated items · control {'OK' if sc['control_ok'] else 'FAILED'}"
                + (" · **SILENT ERROR**" if sc["silent_error"] else "")
            )
        else:
            lines.append(
                f"- score: lines {sc['matched']}/{sc['true']} · extra {sc['extra']} · total {'ok' if sc['total_ok'] else 'WRONG'}"
                f" · fabricated unit prices {sc['fabricated_prices']} (derived {sc.get('derived_prices', 0)})"
                f" · invented units {sc['invented_units']} · field errors {sc['field_errors']}"
                + (" · **SILENT ERROR**" if sc["silent_error"] else "")
            )
    if include_raw:
        lines += ["", "Raw model output (verbatim):", "", "```json",
                  (result["raw_text"] or "<no text returned>").strip(), "```"]
    lines.append("")
    return "\n".join(lines)


def summary_table(rows: list[tuple[dict, dict]]) -> str:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for sc, truth in rows:
        by_cat[truth["category"]].append(sc)
    out = ["| category | n | lines matched | totals exact | gate triggered | silent errors |",
           "|---|---|---|---|---|---|"]
    tot = {"n": 0, "m": 0, "t": 0, "tok": 0, "g": 0, "s": 0}
    for cat, scs in by_cat.items():
        n = len(scs)
        m = sum(s["matched"] for s in scs); t = sum(s["true"] for s in scs)
        tok = sum(1 for s in scs if s["total_ok"]); g = sum(1 for s in scs if s["gate"])
        se = sum(1 for s in scs if s["silent_error"])
        if cat == "unreadable_control":
            lines_cell = "n/a (" + ("no fabricated items" if all(s["extra"] == 0 for s in scs) else
                                    f"{sum(s['extra'] for s in scs)} fabricated items") + ")"
            tot_cell = "n/a"
        else:
            lines_cell = f"{m}/{t} ({100 * m / t:.0f}%)" if t else "n/a"
            tot_cell = f"{tok}/{n}"
            tot["m"] += m; tot["t"] += t; tot["tok"] += tok
        out.append(f"| {cat} | {n} | {lines_cell} | {tot_cell} | {g}/{n} | **{se}** |")
        tot["n"] += n; tot["g"] += g; tot["s"] += se
    scored_n = tot["n"] - len(by_cat.get("unreadable_control", []))
    out.append(
        f"| **all** | {tot['n']} | {tot['m']}/{tot['t']} ({100 * tot['m'] / tot['t']:.0f}%) | "
        f"{tot['tok']}/{scored_n} | {tot['g']}/{tot['n']} | **{tot['s']}** |"
    )
    return "\n".join(out)


# ── main ────────────────────────────────────────────────────────────────────

async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--only", default=None, help="substring filter on file name")
    ap.add_argument("--rescore", default=None, metavar="RUN_JSON",
                    help="re-apply the current scoring rules to a saved docs/vision-runs/*.json "
                         "(no model calls); appends a summary + per-image scores only")
    ap.add_argument("--normalize", action="store_true",
                    help="with --rescore: pass each saved parse through app.ai.vision.normalize_parse "
                         "first, to estimate the server-side guards' effect on old outputs")
    args = ap.parse_args()

    settings = get_settings()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))["images"] if MANIFEST.exists() else None

    if args.rescore:
        run = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        by_name = {m["file"].split("/")[-1]: m for m in (manifest or [])}
        scored = []
        sections = []
        for result in run["results"]:
            truth = by_name.get(result["file"])
            if not truth:
                continue
            if args.normalize and result.get("parsed") is not None:
                from app.ai.vision import normalize_parse
                result = {**result, "parsed": normalize_parse(json.loads(json.dumps(result["parsed"])))}
                result["gate_triggered"] = needs_confirmation(result["parsed"])
            sc = score(result, truth)
            scored.append((sc, truth))
            sections.append(render(result, truth, sc, include_raw=False))
        body = "\n".join([
            "", f"### Re-score of “{run['label']}”{' + normalize_parse' if args.normalize else ''} — "
                f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", "",
            f"- source run: `{Path(args.rescore).name}` (model `{run.get('model')}`, prompt at `{run.get('commit')}`)",
            "- no model calls: the saved verbatim outputs re-scored with the scoring rules in this script's docstring"
            + (" after passing through the server-side guards (`normalize_parse`)" if args.normalize else ""),
            "", "**Per-category summary:**", "", summary_table(scored), "",
        ]) + "\n".join(sections)
        with RESULTS.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        print(f"re-scored {len(scored)} results into {RESULTS}")
        return
    if manifest:
        targets = [(SAMPLES / m["file"], m) for m in manifest]
    else:
        targets = [(p, None) for p in sorted(SAMPLES.iterdir())
                   if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
    if args.only:
        targets = [(p, m) for p, m in targets if args.only in p.name]
    if not targets:
        sys.exit("no images selected")

    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=REPO).stdout.strip()
    header = [
        "",
        f"### {args.label} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"- vision model: `{settings.gemini_pro_model}` (setting `GEMINI_PRO_MODEL`)",
        f"- prompt/schema: `backend/app/ai/vision.py` at commit `{head}`",
        "- gate rule: `app.services.receipts.needs_confirmation` — confidence != high, or any ambiguity, or no items",
        f"- images: {len(targets)}" + (" (scored against manifest.json)" if manifest else ""),
        "",
    ]
    sections: list[str] = []
    scored: list[tuple[dict, dict]] = []
    results: list[dict] = []
    scores: list[dict | None] = []
    for path, truth in targets:
        print(f"-> {path.name} ...", end=" ", flush=True)
        result = await run_one(path)
        sc = score(result, truth) if truth else None
        results.append(result)
        scores.append(sc)
        if sc:
            scored.append((sc, truth))
        print(f"{result['latency_ms']} ms" + (f" · errors {sc['field_errors']}" if sc else "")
              + (" · SILENT ERROR" if sc and sc["silent_error"] else ""))
        sections.append(render(result, truth, sc))

    body = "\n".join(header)
    if scored:
        body += "\n**Per-category summary** (sample counts stated; scoring rules in the script docstring):\n\n"
        body += summary_table(scored) + "\n\n"
    body += "\n".join(sections)
    with RESULTS.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    # Machine-readable copy of the same run, so a scoring fix can be re-applied to
    # old model outputs without spending another 18 model calls.
    RUNS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = "".join(ch if ch.isalnum() else "-" for ch in args.label.lower()).strip("-")[:40]
    dump = RUNS / f"{stamp}-{slug}.json"
    dump.write_text(json.dumps({
        "label": args.label, "model": settings.gemini_pro_model, "commit": head,
        "results": [{**r, "score": s} for r, s in zip(results, scores)],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"appended {len(targets)} results to {RESULTS}; raw run saved to {dump}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
