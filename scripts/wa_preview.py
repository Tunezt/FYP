"""Every WhatsApp message the bot sends, captured and shown as chat bubbles (fin-wa-format).

    cd backend
    ../.venv-or-yours/python.exe ../scripts/wa_preview.py capture before   # writes <out>/before.json
    ../.venv-or-yours/python.exe ../scripts/wa_preview.py capture after
    ../.venv-or-yours/python.exe ../scripts/wa_preview.py render           # writes <out>/preview.html

`capture` runs the real paths (process_webhook_payload for inbound chat, the
nightly delivery and summary jobs, the login code) against the LOCAL seeded
database, with the WhatsApp gateway's HTTP call replaced by a recorder: the text
recorded is exactly what would have been POSTed to the gateway, after every
formatting step. Nothing is sent. Re-seed (`python -m app.seed`) before each
capture so both runs start from the same data. Model answers use the cheap
Gemini chain below (about 25 calls a run); the receipt photo is read for real.

Never point this at the live database: it writes (a second cafe, stock edits,
a purchase order, receipts).
"""
from __future__ import annotations

import asyncio
import html
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
OUT = Path(os.environ.get("WA_PREVIEW_OUT", r"C:/Users/PF4B3/Documents/Side Project/FYP-showcase-evidence/wa-format"))

# Cheap models only, never the live bot's gemini-2.5-flash; gateway mode so templates go out as text.
os.environ.update({
    "GEMINI_FLASH_MODEL": "gemini-3.5-flash-lite",
    "GEMINI_PRO_MODEL": "gemini-3.5-flash-lite",
    "GEMINI_FALLBACK_MODELS": "gemini-3.1-flash-lite,gemini-3.5-flash",
    "WHATSAPP_PROVIDER": "gateway",
    "WHATSAPP_GATEWAY_URL": "http://capture.invalid",
    "WHATSAPP_GATEWAY_TOKEN": "preview-capture",
})
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

OWNER = "628120001111"   # the local seed's owner (also DEMO_OWNER_PHONE, patched below)
SECOND = "628120002222"  # a number on two cafes, for the shop picker
CUSTOMER = "628129990000"

_sent: list[str] = []


class _Recorder:
    """Stands in for httpx.AsyncClient inside app.whatsapp.client."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None, **k):
        _sent.append(json["text"])

        class _Resp:
            status_code = 200
            text = "ok"

            def raise_for_status(self):
                return None

        return _Resp()


def _payload(sender: str, message: dict) -> dict:
    message = {"from": sender, "id": f"wamid.preview.{time.time_ns()}", **message}
    return {"entry": [{"changes": [{"value": {"messages": [message]}}]}]}


async def capture(label: str) -> None:
    from sqlalchemy import select

    from app.ai.gemini import QuotaExhausted
    from app.core.config import get_settings
    from app.core.db import plain_session, tenant_session
    from app.jobs import delivery, summary
    from app.models import Business, Order
    from app.whatsapp import client, processor

    from types import SimpleNamespace

    client.httpx = SimpleNamespace(AsyncClient=_Recorder)  # only the WhatsApp client's HTTP, not Gemini's
    delivery.DEMO_OWNER_PHONE = summary.DEMO_OWNER_PHONE = "none"

    steps: list[dict] = []

    # WA_PREVIEW_ONLY=alert,summary re-shoots just those kinds (no AI calls) into the existing file.
    only = {k for k in os.environ.get("WA_PREVIEW_ONLY", "").split(",") if k}

    async def step(kind: str, title: str, said: str | None, run, pace: float = 0) -> None:
        if only and kind not in only:
            return
        _sent.clear()
        try:
            await run()
        except Exception as exc:  # recorded, never hidden
            _sent.append(f"[preview error: {type(exc).__name__}: {exc}]")
        steps.append({"kind": kind, "title": title, "said": said, "sent": list(_sent)})
        print(f"- {title}: {len(_sent)} message(s)")
        if pace:
            await asyncio.sleep(pace)

    def text(sender: str, body: str):
        return lambda: processor.process_webhook_payload(_payload(sender, {"type": "text", "text": {"body": body}}))

    # A second cafe on SECOND's number, so the picker shows (local DB only).
    # Made by bootstrap, like a real signup: a bare row lacks the books every business must have.
    async with plain_session() as session:
        other = (await session.execute(select(Business).where(Business.owner_phone == SECOND))).scalar_one_or_none()
    if other is None:
        from app.bootstrap import bootstrap
        await bootstrap(name="Warung Bu Diah", owner_name="Diah", phone=SECOND, pin="5678")
    async with plain_session() as session:
        poernama = (await session.execute(select(Business).where(Business.owner_phone == OWNER))).scalar_one()
        poernama.extra_owner_numbers = [{"phone": SECOND, "name": "Diah"}]
        poernama.active_owner_numbers = []
        await session.commit()
        business_id = poernama.id

    model = 25  # seconds between model-using steps (free-tier per-minute limits)

    await step("picker", "Pilih usaha (nomor di 2 kafe)", "halo", text(SECOND, "halo"))
    await step("picker", "Pilih usaha: terpilih", "poernama", text(SECOND, "poernama"))
    await step("answer", "Jawaban dengan nama usaha di atas", "stok croissant berapa?", text(SECOND, "stok croissant berapa?"), model)
    await step("picker", "Ganti toko", "ganti toko", text(SECOND, "ganti toko"))

    for said in (
        "stok es kopi susu berapa?",
        "how much did I sell today?",
        "bulan ini untung ga?",
        "apa 5 menu paling laku minggu ini?",
        "stok gula aren ternyata tinggal 3",
        "pernah beli gula aren dari toko supply?",
        "buatkan PO ke Toko Supply: gula aren 5 kg, susu uht 12 liter",
        "halo",
        "cuaca besok gimana?",
    ):
        await step("answer", "Asisten pemilik", said, text(OWNER, said), model)

    photo = (ROOT / "docs" / "demo-receipt-toko-supply.jpg").read_bytes()

    def send_photo():
        media = f"preview-photo-{time.time_ns()}"
        client.stash_media(media, photo, "image/jpeg")
        return processor.process_webhook_payload(_payload(OWNER, {"type": "image", "image": {"id": media}}))

    await step("receipt", "Foto nota: dibaca, minta YA", "(foto nota Toko Supply)", send_photo, model)
    await step("receipt", "Foto nota: koreksi", "ya tapi gulanya 3 kg bukan 5", text(OWNER, "ya tapi gulanya 3 kg bukan 5"), model)
    await step("receipt", "Foto nota: disimpan", "YA", text(OWNER, "YA"), model)
    await step("receipt", "Foto nota yang sama lagi: peringatan dobel", "(foto nota yang sama)", send_photo, model)
    await step("receipt", "Foto nota: batal", "batal", text(OWNER, "batal"))

    import pandas as pd

    buf = io.BytesIO()
    pd.DataFrame({"Nama Barang": ["Biji Arabica", "Gula Aren", "Teh Melati", ""], "Jumlah": [8, 5, 3, 2],
                  "Satuan": ["kg", "kg", "kg", "kg"]}).to_excel(buf, index=False, sheet_name="Stok")

    def send_excel():
        media = f"preview-xlsx-{time.time_ns()}"
        client.stash_media(media, buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return processor.process_webhook_payload(_payload(OWNER, {"type": "document", "document": {
            "id": media, "filename": "stok.xlsx",
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}}))

    await step("excel", "Import stok dari Excel", "(file stok.xlsx)", send_excel, model)
    await step("excel", "File yang bukan Excel", "(file nota.pdf)", lambda: processor.process_webhook_payload(
        _payload(OWNER, {"type": "document", "document": {"id": "x", "filename": "nota.pdf", "mime_type": "application/pdf"}})))

    real_handle_text = processor._handle_text

    async def quota_daily(business, said):
        raise QuotaExhausted(daily=True, resets_at=datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc))

    async def quota_minute(business, said):
        raise QuotaExhausted(daily=False)

    async def broken(business, said):
        raise RuntimeError("preview: simulated failure")

    for fake, title in ((quota_daily, "Batas AI harian habis"), (quota_minute, "AI lagi ramai"), (broken, "Ada gangguan")):
        processor._handle_text = fake
        await step("error", title, "laporan hari ini", text(OWNER, "laporan hari ini"))
    processor._handle_text = real_handle_text
    await step("error", "Jenis pesan yang belum didukung", "(pesan suara)", lambda: processor.process_webhook_payload(
        _payload(OWNER, {"type": "audio", "audio": {"id": "a"}})))

    async def nightly_alerts():
        async with tenant_session(business_id) as session:
            business = await session.get(Business, business_id)
            await delivery.deliver_unsent(session, business)
            await session.rollback()  # keep them unsent for the next capture

    await step("alert", "Peringatan malam", None, nightly_alerts)

    async def daily_weekly():
        async with tenant_session(business_id) as session:
            business = await session.get(Business, business_id)
            monday_evening = datetime(2026, 11, 23, 14, 0, tzinfo=timezone.utc)  # Senin 21.00 WIB
            await summary.send_summary(session, business, now=monday_evening)

    await step("summary", "Ringkasan harian + mingguan (Senin)", None, daily_weekly)
    await step("otp", "Kode login", None, lambda: client.send_otp_template(OWNER, "482913"))

    from app.services.receipt_delivery import ensure_receipt_code

    async with tenant_session(business_id) as session:
        order = (await session.execute(
            select(Order).where(Order.status == "completed").order_by(Order.created_at.desc()).limit(1))).scalar_one_or_none()
        code = await ensure_receipt_code(session, order) if order else None
    if code:
        await step("struk", "Struk untuk pelanggan (STRUK <kode>)", f"STRUK {code}", text(CUSTOMER, f"STRUK {code}"))
    await step("struk", "Kode struk salah", "STRUK ABC123", text(CUSTOMER, "STRUK ABC123"))

    OUT.mkdir(parents=True, exist_ok=True)
    if only:
        fresh = {(s["title"], s["said"]): s for s in steps}
        old = json.loads((OUT / f"{label}.json").read_text(encoding="utf-8"))
        steps = [fresh.get((s["title"], s["said"]), s) for s in old]
    (OUT / f"{label}.json").write_text(json.dumps(steps, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT / (label + '.json')}")


# ── render ───────────────────────────────────────────────────────────────────

def _inline(s: str) -> str:
    """WhatsApp's inline markup to HTML. Pairs need a non-space just inside, like WhatsApp;
    a doubled **x** or a # heading stays literal, as it does on the phone."""
    s = html.escape(s)
    s = re.sub(r"```(.+?)```", r"<code>\1</code>", s)
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
    s = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])", r"<i>\1</i>", s)
    s = re.sub(r"(?<![\w~])~(?=\S)([^~\n]+?)(?<=\S)~(?![\w~])", r"<s>\1</s>", s)
    return re.sub(r"(https?://\S+)", r'<span class="link">\1</span>', s)


def _bubble(text: str) -> str:
    out: list[str] = []
    for line in text.split("\n"):
        if m := re.match(r"^[-*] (.*)", line):
            out.append(f'<div class="li"><span class="dot">•</span><span>{_inline(m[1])}</span></div>')
        elif m := re.match(r"^(\d+)\. (.*)", line):
            out.append(f'<div class="li"><span class="dot">{m[1]}.</span><span>{_inline(m[2])}</span></div>')
        elif line.startswith("> "):
            out.append(f'<div class="quote">{_inline(line[2:])}</div>')
        elif not line.strip():
            out.append('<div class="gap"></div>')
        else:
            out.append(f"<div>{_inline(line)}</div>")
    return "".join(out)


def render() -> None:
    runs = {label: json.loads((OUT / f"{label}.json").read_text(encoding="utf-8"))
            for label in ("before", "after") if (OUT / f"{label}.json").exists()}
    labels = {"before": "Sebelum", "after": "Sesudah"}
    sections = []
    for label, steps in runs.items():
        chats = []
        for s in steps:
            parts = [f'<div class="title">{html.escape(s["title"])}</div>']
            if s["said"]:
                parts.append(f'<div class="msg out"><div class="bub">{html.escape(s["said"])}</div></div>')
            for body in s["sent"] or ["(tidak ada balasan)"]:
                literal = " flag" if re.search(r"\*\*|^#{1,6} |\]\(", body, re.M) else ""
                parts.append(f'<div class="msg in"><div class="bub{literal}">{_bubble(body)}</div></div>')
            chats.append(f'<section class="chat" data-kind="{s["kind"]}">{"".join(parts)}</section>')
        sections.append(f'<div class="run" id="{label}"><h1>{labels[label]}</h1>{"".join(chats)}</div>')
    page = f"""<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Pesan WhatsApp Poernama</title>
<style>
:root{{--bg:#efeae2;--in:#fff;--out:#d9fdd3;--ink:#111b21;--muted:#667781;--link:#027eb5;--flag:#c62828}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.38 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}}
.runs{{display:flex;gap:24px;justify-content:center;padding:16px;flex-wrap:wrap}}
.run{{width:390px;max-width:100%}}h1{{font-size:15px;margin:0 0 8px;padding:10px 14px;background:#008069;color:#fff;border-radius:8px}}
.chat{{margin:0 0 18px}}.title{{text-align:center;margin:6px auto;font-size:12px;color:var(--muted);background:#fff9;border-radius:6px;padding:3px 8px;width:fit-content}}
.msg{{display:flex;margin:3px 0}}.msg.out{{justify-content:flex-end}}
.bub{{max-width:86%;padding:6px 9px 7px;border-radius:8px;box-shadow:0 1px .5px #0b141a22;word-wrap:break-word;overflow-wrap:anywhere}}
.in .bub{{background:var(--in);border-top-left-radius:0}}.out .bub{{background:var(--out);border-top-right-radius:0}}
.bub.flag{{outline:2px solid var(--flag)}}.gap{{height:.7em}}.li{{display:flex;gap:6px}}.dot{{flex:none;min-width:12px}}
.quote{{border-left:3px solid #00a884;padding-left:7px;color:#3b4a54}}code{{font-family:Consolas,monospace;font-size:13px;background:#0001;padding:0 3px;border-radius:3px}}
.link{{color:var(--link)}}
body.only-before #after,body.only-after #before{{display:none}}
</style></head><body><div class="runs">{"".join(sections)}</div>
<script>const q=new URLSearchParams(location.search),v=q.get('v'),k=q.get('k');if(v)document.body.classList.add('only-'+v);if(k)document.querySelectorAll('.chat').forEach(c=>{{if(!k.split(',').includes(c.dataset.kind))c.style.display='none'}})</script>
</body></html>"""
    (OUT / "preview.html").write_text(page, encoding="utf-8")
    print(f"wrote {OUT / 'preview.html'}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "capture":
        asyncio.run(capture(sys.argv[2]))
    elif len(sys.argv) == 2 and sys.argv[1] == "render":
        render()
    else:
        sys.exit(__doc__)
