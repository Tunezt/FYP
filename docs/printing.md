# Printing and open bills: two printers, one queue, and what is still a hardware decision

**Status: PILOT** (21 September 2026). The software is finished and tested against simulated
printers; nothing has printed on a physical IW-J300H, and the bridge has not yet run on the
cashier tablet. `docs/printing-test-log.md` is the record of what has been tested where.

Written 18 September 2026 with the `prt-*` and `bill-*` tasks; updated 19 September 2026 with
prt-8 (the print bridge, held jobs, results bound to the claim) and 21 September 2026 with prt-9
(the hardware is chosen: two Iware IW-J300H over Wi-Fi, with the bridge on the cashier tablet). It separates what the software
does and has been tested to do from what depends on hardware that has not been bought or
tested.

**Internet and Wi-Fi, in one place.** The till, the API and the bridge all need the internet:
jobs are created in the cloud and the bridge fetches them over HTTPS. The bridge and **both**
printers must be on the same local network (the café's main Wi-Fi or Ethernet, not a guest
network that isolates devices). The printers themselves never need the internet. If the café's
internet drops, sales stop at the till (no offline mode yet); if only the bridge loses it, jobs
wait on the server and print when it reconnects, or are held for a person's decision if that took
more than 15 minutes.

## The room

| Where | Who | Paper |
|---|---|---|
| Front counter | cashier and barista, side by side | **FRONT printer** (Iware IW-J300H, Wi-Fi): the customer's receipt, the table's nota, and a **separate** Bar slip |
| Kitchen, 10–15 m behind through walls | chef, no tablet | **KITCHEN printer** (Iware IW-J300H, Wi-Fi): the Dapur slip |

There is one tablet, the cashier's: an Android Samsung Galaxy Tab A9/A9+ running Chrome. It also
runs the print bridge (below), so no other computer is needed. Nobody needs a barista screen or a chef screen. Nobody
carries the tablet to the tables. The kitchen screen still exists (`/kitchen/{token}`) but is
no longer linked from the till or the dashboard (owner's decision, 18 Sept 2026).

## How an order moves

**Dine-in: an open bill per table, paid when the table leaves.**

1. The cashier (or a guest, from the QR menu) puts items on the table's bill. A table has one
   open bill: "Meja 7", "meja7" and "7" are the same table. Opening a second one points the
   cashier at the first ("Tambahkan ke tagihan itu").
2. **Kirim ke dapur/bar** sends whatever is new: a Bar slip, a Dapur slip (only for stations
   that have items), and a **nota** on the front printer. Staff take the nota to the table as
   the confirmation. It lists the new items with prices, the bill so far, and BELUM DIBAYAR.
3. More rounds: each send prints **only its new items**, labelled `TAMBAHAN` and "Tambahan 1",
   "Tambahan 2" ...
4. Sent items are locked. Taking one off is **batalkan** with a reason (no PIN; the cashier's
   call). The station gets a `BATAL` slip naming exactly that item and the reason, unless its
   slip was still waiting in the queue with nothing else on it, in which case the slip is
   withdrawn.
5. **Bayar** when the table leaves. Anything added but not yet sent goes out as one last batch
   of slips (no nota), then the ordinary sale runs and **only the receipt** prints. Stock, cost
   of goods, payments and the journal are written at this moment, once.

**Takeaway (and pickup, delivery): pay first, then it is made.** Payment prints the receipt,
the Bar slip and the Dapur slip together. Nothing changed here.

**QR menu.** A dine-in guest's order joins the table's open bill (or starts it). The items wait
as *Belum dikirim · QR · name* until the cashier sends them. The guest's page says only
*Menunggu konfirmasi kasir*, then *Pesanan dikirim ke dapur/bar*, then *Lunas*. The guest can
order another round from the same page while the bill is open.

## Which product goes to which printer

Set by the owner **per product** (Stok → edit → *Disiapkan di*: Bar, Dapur, or *Tanpa
persiapan*), never guessed from the product's name. Pengaturan → Printer lists how many products
go to each station and names every product that has not been assigned, with one-tap buttons to
assign it. An unassigned product is not dropped: its line prints on the Bar slip at the front,
flagged `[TUJUAN BELUM DIATUR]`, where someone can walk it back. Ingredients (anything with no
selling price) are not asked about, because they never reach a ticket.

## What gets printed, and when

Preparation slips carry the table in large type for dine-in ("MEJA 7") or the order number
("PESANAN 042"), then "Pesanan 042", service type, time, a slip reference (`042-D0`: number,
station, batch; `042-N1` for the second nota), and each item with quantity, explicit size, every
modifier and the note. They do **not** carry prices, payment, or the customer's name or phone.

| Situation | Paper |
|---|---|
| A table's bill is sent | nota (front) + Bar slip (if any) + Dapur slip (if any), with only the new items |
| A table's bill is paid, everything already sent | receipt only |
| A table's bill is paid with unsent items on it | slips for those items (`TAMBAHAN`, no nota), then the receipt |
| A dine-in order paid without ever being sent, a takeaway, a pickup, a delivery | receipt + Bar slip (if any) + Dapur slip (if any) |
| Addition to an already paid order (API only; the till now adds to the open bill instead) | its own receipt and slips with only the new items, `TAMBAHAN` |
| A sent item is cancelled | `BATAL` slip for that station with the item, quantity and reason; or its untouched slip is withdrawn |
| A sent bill is cancelled (reason required) | `BATAL` per station for what may be on paper; untouched slips withdrawn |
| A paid order is voided or refunded | the same rule per slip: withdrawn if untouched, otherwise `BATAL` |
| Reprint | a new job with the same content, labelled `CETAK ULANG` with the count, time and staff name |
| Send or payment retried, double-tapped, or replayed | nothing new: every job has a unique `dedupe_key` (per order and send) |
| Backdated paper sale | nothing (it was served when it happened) |

Within one moment the front printer gives the customer's paper first (receipt or nota), then
the Bar slip.

## Job states, and what the till is allowed to say

| Stored | Shown | Meaning |
|---|---|---|
| `pending`, < 15 min | Menunggu printer | nobody has taken it (or a bridge took it and put it back unsent: the reason is shown) |
| `pending`, ≥ 15 min, not let through | **Tertahan** | waited so long that no printer may take it until a person decides (prt-8) |
| `claimed`, < 90 s | Sedang dikirim | a device took it |
| `claimed`, ≥ 90 s, or reported uncertain | **Belum pasti tercetak** | a device took it and never reported back, **or** said paper may or may not exist (connection cut mid-job, no confirmation): shown at once, with the reason |
| `printed`, evidence `bytes_delivered` | Terkirim ke printer | the printer accepted the bytes; it could not be asked whether it finished |
| `printed` | Tercetak | the printer confirmed it processed the job through the cut (evidence `printer_status`), **or** a person confirmed they are holding it, or an older device said so |
| `failed` | Gagal cetak | a device reported it could not print (nothing reached the printer) |
| `cancelled` | Ditarik | withdrawn before anything took it, by a cancellation or by a person |

Unresolved jobs stay on *Perlu perhatian* **however old they are**. Until prt-8 the 12-hour
history window also hid them, so a 13-hour-old slip vanished from the till while a printer could
still take it. *12 jam terakhir* is still limited to 12 hours.

Recovery at the till (header printer icon → *Antrean cetak*, or in the order's detail, where
each send's slips and nota are listed separately):

- **Coba lagi**: failed jobs only; back to the queue. Retrying a job older than 15 minutes
  counts as letting it through (it prints marked TERLAMBAT).
- **Cetak ulang**: marked reprint. This is the answer to *uncertain*; the job is never silently
  re-queued, because that could print the same work twice without the label.
- **Kertas sudah ada**: a person confirms an uncertain job; recorded with their staff id.
- **Cetak sekarang (terlambat)**: a *held* job may print after all. It keeps its identity (a later
  cancellation still finds it), and the device prints it with a **TERLAMBAT** label and "Dibuat
  dd/mm hh.mm · dicetak hh.mm · name". The stored document is not changed.
- **Tidak perlu dicetak**: a held or failed job is withdrawn (`cancelled`, recorded with the staff
  id). Not offered for a job a printer may have printed.
- **Cetak manual** (front printer only): the browser fallback below.

**When the bridge stops, the till says so.** A bridge that has been killed, frozen by Android or
unplugged cannot report its own failure, so the server decides: no heartbeat for 90 seconds and
that printer counts as silent. The kasir screen then carries a warning across the top —
*Printer dapur: bridge di tablet tidak melapor sejak 18.20. Slip tidak akan keluar sampai bridge
dijalankan lagi* — not only inside the queue sheet.

Above the list, each printer's last report from its print bridge: *Siap*, *Kertas habis*,
*Tutup printer terbuka*, *Tidak terjangkau*, or *Tidak melapor sejak hh.mm* when the bridge
itself has gone quiet for 90 seconds. These count towards the header's "perlu dicek".

**Exactly-once physical printing is not possible without device support.** A printer that
prints and then loses its connection before reporting looks identical to one that never
printed. The software's guarantee is narrower and honest: every job is persisted, no retry
creates a duplicate job, and every copy after the first is labelled.

Printing never affects the money: a dead printer cannot roll back or repeat a send or a
payment.

## Stock and open bills

Stock is recorded when the bill is paid (owner's decision), and the database never lets a
counted item go below zero. Food on an open bill is eaten before it is paid for, so the till
refuses to **send** more of a counted item than the books hold, after counting what other open
bills have already sent ("Stok Roti tidak cukup untuk dikirim — tersisa 10 setelah pesanan meja
lain"). Made-to-order drinks take their ingredients at payment, as always.

**Known gap:** a pay-now sale can still take a counted item that an unpaid table was promised.
That table's payment is then refused ("Stok tidak cukup") until the stock is corrected in the
dashboard. This only happens when the books disagree with the shelf.

## Printer device API (built, tested with simulated devices)

A printer device authenticates with a token issued by the owner (Pengaturan → Printer → *Buat
token perangkat*). A token names one printer (`front` or `kitchen`), lasts a year, and dies
when the owner disconnects all till devices (the pairing generation rises).

```
POST /print/agent/claim
Authorization: Bearer <printer token>
{"device": "dapur-1"}
→ 200 {"job": null}                                  nothing to print
→ 200 {"job": {"id": "...", "attempts": 1, "kind": "kitchen_ticket", "copy_kind": "original",
              "order_label": "Pesanan 042", "document": {"v": 1, "blocks": [...]}, ...}}

POST /print/agent/jobs/{id}/result
{"device": "dapur-1", "attempt": 1, "outcome": "printed", "evidence": "printer_status"}
{"device": "dapur-1", "attempt": 1, "outcome": "uncertain", "error": "koneksi putus setelah 312 byte"}
{"device": "dapur-1", "attempt": 1, "outcome": "failed", "error": "..."}
{"device": "dapur-1", "ok": true}                    the prt-4 form, still accepted (see below)

POST /print/agent/jobs/{id}/release                  took it, sent NOTHING to the printer
{"device": "dapur-1", "attempt": 1, "error": "Belum terkirim: Kertas habis"}

POST /print/agent/held                               what this device holds unanswered (after a restart)
{"device": "dapur-1"}  → {"jobs": [{"id": "...", "attempt": 1, "claimed_at": "..."}]}

POST /print/agent/heartbeat                          what the printer looks like, for the till
{"device": "dapur-1", "state": "paper_out", "detail": "Kertas habis", "version": "1.0.0"}
```

`kind` is one of `receipt`, `nota`, `bar_ticket`, `kitchen_ticket`, `bar_cancel`,
`kitchen_cancel`. Claiming takes the oldest pending job for that printer with `FOR UPDATE SKIP
LOCKED`, so two devices polling at once never take the same job. **Held jobs (pending ≥ 15
minutes and not let through) are never claimed.** A device token cannot open the till, and a till
token cannot claim jobs.

**Answers are bound to the claim** (prt-8). Every claim adds one to `attempts`. A result or
release must come from the device named in the claim, about the current attempt. Otherwise it is
refused with 409 and changes nothing. That covers a late answer from an earlier attempt, including
from the same device after it took the job again, and an answer from another device on the same
token. An answer without `attempt` (the prt-4 form) is accepted only while the job has been
claimed once, because after that it is ambiguous. An answer arriving twice is the same answer.
`release` is refused once a device has called the job uncertain: paper may exist, so it cannot go
back as if nothing happened. `held` leaves out jobs already called uncertain for the same reason.

`document.blocks` is printer-neutral: `title`, `label` (inverse, e.g. `TAMBAHAN`/`BATAL`/`CETAK
ULANG`/`BELUM DIBAYAR`), `banner` (large heading), `line`, `kv` (left/right), `rule`, `item`
(qty, name, size, modifiers, notes, flag), `item_priced`, `total`, `text`, `note`, `qr` (till-7,
`GS ( k`). Whatever sits on the device side maps these to its printer's commands.

till-12 additions, each of which an older bridge still prints sensibly:

- `logo` `{text, width, height, bits}`: the café's logo as a 1-bit bitmap (`bits` = base64 rows of
  `width/8` bytes, MSB first, 1 = ink), sent with `GS v 0`, centred. Poernama's is its signage
  wordmark, 384 × 81 dots, made by `scripts/receipt_logo.py` from `frontend/components/Wordmark.tsx`
  into `backend/app/assets/receipt_logo.json` (a test fails if the two drift). A bitmap that does
  not add up prints `text` (the name) instead; an older bridge prints `text` too. **`GS v 0` has
  not yet been seen on the café's IW-J300H** — check it on the first real receipt.
- `rule` with `style: "double"`: a line of `=`; older bridges print `-`.
- `item_priced` with `unit_price`: the name on its own line, then `  2 x @15.000 … 30.000`; without
  `unit_price` (older documents, the nota) the one-line form.

The receipt reads: logo or name · address · contacts · `=====` · order number · `=====` · Tanggal,
Jenis, Kasir, Pelanggan · items · Total item · Subtotal, discounts, tax ("PB1 10%") · `=====` ·
TOTAL · `=====` · payment and change · "Sebutkan nomor pesanan…" (not for dine-in) · the café's
own closing line (`businesses.receipt_footer`) · Terima kasih! · WhatsApp QR when live · Ref. The
owner turns the logo on/off and writes the closing line in Pengaturan → Data di struk.

## What the café's setup constrains

- The API runs in the cloud (Railway), the pages on Vercel. **The cloud cannot open a connection
  to a printer on the café's wifi.** Anything unattended must *pull* from the API over HTTPS.
- The till is one Android tablet running Chrome. A web page **cannot** open a raw TCP connection
  to a network printer (port 9100). **Web Bluetooth** can reach a nearby Bluetooth printer from
  Chrome on Android, but only while the page is open and only after the person pairs it, and
  15–20 m through a wall to the kitchen is beyond reliable Bluetooth range.

## Required printer capabilities

- 80 mm thermal paper (58 mm works; set `paper_mm: 58` in the bridge and long names wrap)
- Auto-cutter accepting `GS V 66 n` (so the front printer's receipt or nota and the Bar slip come
  out **separately**)
- ESC/POS over a **raw TCP port** on the network (usually 9100) for the bridge, or a cloud-print
  protocol if the café chooses option A instead
- Strongly preferred: answers `DLE EOT 1/2/4` (paper/cover/offline status) and `GS r 1` over that
  network port. Without them the till can say only *Terkirim ke printer*, not *Tercetak*, and
  cannot say why a printer stopped
- **Kitchen printer:** Ethernet or Wi-Fi (not Bluetooth), heat- and grease-tolerant, a fixed IP
- **Front printer:** Wi-Fi or Ethernet with a fixed IP (USB/Bluetooth would need a different
  bridge transport, not built)
- Optional: buzzer or light for the kitchen printer, so a new slip is noticed

## Connection options

| | How unattended printing works | Status |
|---|---|---|
| **B. Local print bridge** (`bridge/print_bridge.py`, prt-8/prt-9) — **chosen** | a small program on the cashier tablet polls `/print/agent/claim` with the two tokens and sends ESC/POS over TCP (port configurable) to both Wi-Fi printers, then reports | **Built.** Tested against simulated printers and the real API (37 tests, plus live end-to-end runs). **Not yet run on any physical printer, and not yet run on the tablet.** |
| **A. Cloud-print printers** (e.g. Epson "Server Direct Print", Star "CloudPRNT") | the printer itself polls a URL over HTTPS | **Not built.** It needs an adapter for that vendor's protocol, and a protocol adapter cannot honestly be tested without the printer. Only relevant if the café buys such printers and wants no bridge device. |
| **C. Android POS terminal with a built-in printer** (Sunmi / iMin) at the front | the terminal's print SDK in a wrapper app | **Not built** (native wrapper is out of scope). The kitchen would still use B. |
| **D. Browser print** | *Cetak manual*: Chrome's print dialog, then "Apakah kertasnya keluar?" | Built (prt-4). Manual fallback for the front printer only. |

**Where the bridge runs (decided 21 September 2026).** The tablet's Chrome page cannot open a raw
TCP connection, and the cloud cannot reach into the restaurant network, so the bridge runs **on
the cashier tablet itself**, beside Chrome: it is already on, charging and on the right Wi-Fi, and
the owner wants no extra hardware. It is plain Python 3.9+ with no packages.

Two ways to host it on Android, for two different stages:

| | Termux | A dedicated Android companion app |
|---|---|---|
| Ready now | **Yes**, runs the exact tested code | **No**, must be written and built |
| Chrome in front, screen off | usually, with wake lock and Samsung battery settings | yes, that is what a foreground service is for |
| Android 12+ phantom-process killing | **not guaranteed**; switched off once from a PC over adb | not affected |
| After a reboot | Termux:Boot, if set up | its own boot receiver |
| After a force-stop | manual restart (Android allows nothing else) | manual restart |

The plan: Termux for bring-up and for the tablet tests in `bridge/README.md`; if it does not
survive a real service (screen off, Chrome in front, overnight), build the companion app. **Until
those tests pass on the tablet, unattended background printing is not claimed.** The bridge now
measures being frozen by Android and reports it to the till (*bridge sempat berhenti N menit*),
so this is decided on evidence.

Setup, Samsung battery settings, autostart, the probe and the acceptance checklist are in
`bridge/README.md`.

**How the bridge keeps its promises** (all tested with simulated printers):

- One worker per printer, each with its own token and thread. A dead kitchen printer never
  holds up the front printer. Jobs for one printer go one at a time, oldest first, one paper cut
  per job, so receipt/nota and Bar slip are separate pieces of paper.
- It checks the printer before taking work (`DLE EOT` status: offline, cover open, paper out,
  error). If the printer is down, it takes nothing and reports the state; the till shows it.
- **Confirmed failure vs uncertain.** If nothing reached the printer (it became unreachable, or
  the cover opened, before the first byte), the job is **released** and prints unmarked when the
  printer returns. If any byte may have reached it and the outcome is not confirmed (connection
  cut mid-job, no `GS r` answer in time), it reports **uncertain** and never resends; the
  cashier's marked reprint is the answer. A document that cannot be rendered is **failed**.
- **Printed needs evidence.** With `status: gs_r` the bridge asks `GS r 1` after the cut. The
  printer answers only after processing everything before it, and that answer is recorded as
  `printer_status`. Printers that cannot answer run with `status: none`, and a job they accept
  is shown as *Terkirim ke printer*, never *Tercetak*.
- **Restarts and lost answers.** A journal on disk, fsynced before the first byte of a job goes
  out, tells a restarted bridge "never sent" (release) from "may be on paper" (uncertain). If the
  answer to a claim is lost, `held` finds the job and it is released, never printed twice. A lost
  result is re-sent from the journal.
- **Old work is held**, not printed as new, after an outage longer than 15 minutes (above).

## The Iware IW-J300H: advertised, and what is actually verified

Two of them, both 80 mm, over the restaurant Wi-Fi. The seller advertises 80 mm thermal printing,
an automatic cutter, ESC/POS, Wi-Fi + LAN + USB + Bluetooth + serial, and a kitchen alarm. Iware
publishes no command manual for this model, so **none of the protocol details are verified**:

| What the bridge needs to know | Profile default (`"model": "iware-iw-j300h"`) | How it is verified |
|---|---|---|
| Which TCP port carries ESC/POS | 9100 | `bridge/probe_printer.py` reports the ports that answer |
| Whether it answers `DLE EOT` (paper, cover, offline) | assumed yes | the probe |
| Whether it answers `GS r` after a job (the only honest basis for *Tercetak*) | assumed yes | the probe |
| Which cut command its cutter takes | `GS V 66` (partial) | the probe prints one labelled slip per candidate |
| Which command sounds the alarm | **none; the buzzer stays off** | the probe prints one labelled slip per candidate, and the bridge refuses a buzzer command that is not marked verified |
| Wi-Fi station mode and a fixed IP | assumed | the printer's self-test page and its own configuration tool |

`--check` prints an UNVERIFIED line for these printers until the probe has run. If the printers
turn out not to answer status requests, nothing breaks: jobs are then shown as *Terkirim ke
printer* rather than *Tercetak*, which is the honest wording for "the bytes arrived and nobody
can say more".

**Also still unknown until the hardware is here:** whether the restaurant Wi-Fi reaches the
kitchen printer through 10–15 m of walls (the IW-J300H has Ethernet as a fallback), and how many
simultaneous connections the printer accepts. **No printer model is claimed to be compatible
until it passes the checklist in `bridge/README.md`.**

**Limits no software can remove.** Paper that came out faded, crooked or unread looks the same
as a good slip. A printer that loses power after receiving a job cannot say whether it printed.
Paper-low needs a near-end sensor that cheap models often lack. A cut has no confirmation of its
own, and a jammed cutter shows up only as an error bit, if the model reports one.
