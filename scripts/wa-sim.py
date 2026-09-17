"""Talk to the WhatsApp assistant locally, without Meta.

Runs the real inbound pipeline (intent routing → tools → database → composed
reply) in-process, exactly as a webhook delivery would, and prints the reply
that would have been sent back on WhatsApp. Needs the local database up and
seeded, and GOOGLE_API_KEY in backend/.env (free tier: ~5 messages a minute).

    cd backend
    .venv/Scripts/python.exe ../scripts/wa-sim.py "stok es kopi susu berapa?"
    .venv/Scripts/python.exe ../scripts/wa-sim.py          # interactive: type, Enter; empty line quits

--from <phone> sends as another number (default: the seeded owner, 628120001111).

Text messages only: receipt photos and Excel files arrive on WhatsApp as media
ids that have to be downloaded from Meta, so they cannot be simulated here.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

if sys.platform == "win32":  # asyncpg needs the selector loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.whatsapp import processor  # noqa: E402

_replies: list[str] = []


async def _capture(to: str, body: str) -> None:
    _replies.append(body)


processor.send_text = _capture  # the reply is printed instead of sent


async def send(sender: str, text: str) -> None:
    _replies.clear()
    started = time.perf_counter()
    await processor.process_webhook_payload(
        {"entry": [{"changes": [{"value": {"messages": [{
            "from": sender,
            "id": f"wamid.sim.{time.time_ns()}",
            "type": "text",
            "text": {"body": text},
        }]}}]}]}
    )
    took = time.perf_counter() - started
    print(f"\nKamu    > {text}")
    for reply in _replies or ["(tidak ada balasan)"]:
        print(f"Asisten < {reply}")
    print(f"          ({took:.1f} dtk)")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("message", nargs="*", help="message text; omit for interactive mode")
    ap.add_argument("--from", dest="sender", default="628120001111")
    args = ap.parse_args()

    if args.message:
        await send(args.sender, " ".join(args.message))
        return
    print("Simulasi WhatsApp — ketik pesan, Enter untuk kirim. Baris kosong untuk keluar.")
    while True:
        try:
            text = input("\nKamu    > ").strip()
        except EOFError:
            break
        if not text:
            break
        await send(args.sender, text)


if __name__ == "__main__":
    asyncio.run(main())
