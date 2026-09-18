# Printing and open bills: two printers, one queue, and what is still a hardware decision

Written 18 September 2026 with the `prt-*` and `bill-*` tasks. It separates what the software
does and has been tested to do from what depends on hardware that has not been bought or
tested.

## The room

| Where | Who | Paper |
|---|---|---|
| Front counter | cashier and barista, side by side | **Front printer:** the customer's receipt, the table's nota, and a **separate** Bar slip |
| Kitchen, 15–20 m behind | chef, no tablet | **Kitchen printer:** the Dapur slip |

There is one tablet, the cashier's. Nobody needs a barista screen or a chef screen. Nobody
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

## What gets printed, and when

An item's station is set by the owner per product (Stok → edit → *Disiapkan di*): Bar, Dapur,
or *Tanpa persiapan* (no slip). It is never guessed from the name. An item with no station yet
prints on the Bar slip, flagged `[TUJUAN BELUM DIATUR]`, because the person reading that slip
is at the front and can walk it back.

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
| `pending` | Menunggu printer | nobody has taken it |
| `claimed`, < 90 s | Sedang dikirim | a device took it |
| `claimed`, ≥ 90 s | **Belum pasti tercetak** | a device took it and never reported back: paper may or may not exist |
| `printed` | Tercetak | a device reported success, **or** a person confirmed they are holding it |
| `failed` | Gagal cetak | a device reported it could not print |
| `cancelled` | Ditarik | withdrawn before anything took it |

Recovery at the till (header printer icon → *Antrean cetak*, or in the order's detail, where
each send's slips and nota are listed separately):

- **Coba lagi**: failed jobs only; back to the queue.
- **Cetak ulang**: marked reprint. This is the answer to *uncertain*; the job is never silently
  re-queued, because that could print the same work twice without the label.
- **Kertas sudah ada**: a person confirms an uncertain job; recorded with their staff id.
- **Cetak manual** (front printer only): the browser fallback below.

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
→ 200 {"job": {"id": "...", "kind": "kitchen_ticket", "copy_kind": "original",
              "order_label": "Pesanan 042", "document": {"v": 1, "blocks": [...]}, ...}}

POST /print/agent/jobs/{id}/result
Authorization: Bearer <printer token>
{"device": "dapur-1", "ok": true}                    or {"ok": false, "error": "kertas habis"}
```

`kind` is one of `receipt`, `nota`, `bar_ticket`, `kitchen_ticket`, `bar_cancel`,
`kitchen_cancel`. Claiming takes the oldest pending job for that printer with `FOR UPDATE SKIP
LOCKED`, so two devices polling at once never take the same job. A device token cannot open
the till, and a till token cannot claim jobs.

`document.blocks` is printer-neutral: `title`, `label` (inverse, e.g. `TAMBAHAN`/`BATAL`/`CETAK
ULANG`/`BELUM DIBAYAR`), `banner` (large heading), `line`, `kv` (left/right), `rule`, `item`
(qty, name, size, modifiers, notes, flag), `item_priced`, `total`, `text`, `note`. Whatever sits
on the device side maps these to its printer's commands.

## What the café's setup constrains

- The API runs in the cloud (Railway), the pages on Vercel. **The cloud cannot open a connection
  to a printer on the café's wifi.** Anything unattended must *pull* from the API over HTTPS.
- The till is one Android tablet running Chrome. A web page **cannot** open a raw TCP connection
  to a network printer (port 9100). **Web Bluetooth** can reach a nearby Bluetooth printer from
  Chrome on Android, but only while the page is open and only after the person pairs it, and
  15–20 m through a wall to the kitchen is beyond reliable Bluetooth range.

## Required printer capabilities

- 80 mm thermal paper (58 mm works; the slip is laid out in blocks, so long names wrap)
- Auto-cutter (so the front printer's receipt or nota and the Bar slip come out **separately**)
- ESC/POS command set, or a cloud-print protocol the café chooses (below)
- **Kitchen printer:** Ethernet or Wi-Fi (not Bluetooth), heat- and grease-tolerant
- **Front printer:** network, USB or Bluetooth, depending on the option chosen
- Optional: buzzer or light for the kitchen printer, so a new slip is noticed

## Connection options (a decision is needed)

| | How unattended printing would work | What must be added | Tested? |
|---|---|---|---|
| **A. Cloud-print printers** (e.g. Epson ePOS "Server Direct Print", Star "CloudPRNT") | the printer itself polls a URL over HTTPS and prints what it receives | a small adapter translating that vendor's polling format to `/print/agent/*` and `blocks` to its markup | **No.** Not written, because a protocol adapter cannot honestly be tested without the printer |
| **B. Local print bridge** (an always-on Raspberry Pi or a spare Android phone on the café wifi) | the bridge polls `/print/agent/claim` with two tokens and sends ESC/POS to both LAN printers on port 9100, then reports | the bridge program (≈ one small script) and a way to keep it running | **No.** Needs the real printers to verify layout, cutting, and failure reporting |
| **C. Android POS terminal with a built-in printer at the front** (Sunmi / iMin), plus a network printer in the kitchen driven by a bridge (B) | front printing through the terminal's print SDK in a wrapper app; kitchen via B | an Android wrapper app (outside the "no native apps" scope) or the vendor's web-print bridge | **No** |
| **D. Browser print (built now)** | *Cetak manual*: the till shows the slip, opens Chrome's print dialog, then asks "Apakah kertasnya keluar?" | nothing | **Rendering and state flow tested in software; not tested on any physical printer.** A print dialog per slip is too slow for a queue, and it cannot reach the kitchen printer unless that printer is installed on the tablet. |

**Recommendation:** B, or A if the café buys printers that support a cloud-print protocol.
Both keep the pull model this API already implements, need no change to the till, and handle
the kitchen distance by using the wired or wifi network. **Nothing here has been tested on
real hardware, and no printer model is claimed to be compatible.**

**What I need from the owner:** which printers are bought (model and connection), and
whether a small always-on bridge device at the café is acceptable. With that, the next task is
the adapter or bridge for that exact hardware, tested end to end: paper width, wrapping of
long names and modifiers, the cut between nota/receipt and Bar slip, the kitchen buzzer, and a
pulled network cable mid-print to see which state the till shows.
