# Printing: what has actually been tested, and where

**Status: PILOT.** The printing path is written and tested in software, and on 3 October 2026 both
Iware IW-J300H printers printed real orders from it (section 3). **The bridge has never run on the
cashier tablet, and nothing has been run at the café.** Until both of those have happened, over a
full service, printing is a pilot and staff should keep an eye on the print queue.

Three columns, kept honest and separate. "Simulated" means a program that pretends to be a
printer (`bridge/sim_printer.py`) on localhost TCP. It proves what our software does. It proves
nothing about an Iware printer, about Wi-Fi through walls, or about Android battery management.

Last updated 3 October 2026 (prt-10, and the first session on the real printers).

## 1. Verified on this computer, automatically (649 tests, run on every change)

| What | Where |
|---|---|
| Ticket routing per product: Bar/Dapur/front, per send, per batch | `tests/test_prep_routing.py`, `tests/test_open_bills.py` |
| Open bills: send, add a round, TAMBAHAN, cancel with reason (BATAL), pay once, receipt only | `tests/test_open_bills.py` |
| Deduplication: double taps, replays, retried sends and payments create no second job | `tests/test_prep_routing.py`, `tests/test_open_bills.py` |
| Atomic claims (`FOR UPDATE SKIP LOCKED`), one device per job | `tests/test_prep_routing.py` |
| Results bound to the device and the claim attempt; stale acknowledgements refused | `tests/test_print_bridge.py` |
| Stale jobs held after 15 minutes; released (TERLAMBAT) or withdrawn by a person | `tests/test_print_bridge.py` |
| Uncertain delivery never auto-resent; marked reprint is the only copy | `tests/test_print_bridge.py`, `tests/test_prep_routing.py` |
| Every document block → ESC/POS at 32 and 48 columns, wrapping, one cut per document | `tests/test_print_bridge.py` |
| Tenant isolation (RLS) for print jobs and print devices | `tests/test_prep_routing.py`, `tests/test_print_bridge.py` |
| Money is never affected by a printer failure | `tests/test_open_bills.py`, `tests/test_prep_routing.py` |

## 2. Verified against a simulated printer (localhost TCP)

Automated, in `tests/test_print_bridge.py`:

| What | Result |
|---|---|
| Both printers print their own tickets, receipt before Bar slip, a cut between | pass |
| A dead kitchen printer does not hold up the front printer | pass |
| Paper out before a job: nothing sent, job returns to the queue unmarked, prints once when refilled | pass |
| Connection cut mid-job: reported uncertain, never resent by the bridge | pass |
| Printer that never confirms: uncertain, not "printed" | pass |
| Printer with no status support: "Terkirim ke printer", never "Tercetak" | pass |
| Bridge restart: never-sent released, maybe-sent reported uncertain, nothing printed twice | pass |
| Lost claim answer and lost result answer: recovered, printed exactly once | pass |
| The owner's test ticket, including when the printer is off (answered as failed) | pass |
| The probe's reading of a printer that answers, and one that answers nothing | pass |
| prt-10, `tests/test_printer_by_name.py`: a printer found by name; found again at a new address after a power cut, the waiting slip printed once; a silent name falls back to the last address; a slip never sent to the other printer when the two swap addresses | pass |

Run by hand against the live API and the real bridge **process** (`scratchpad/e2e_bridge.py`,
17/17 checks): routing, cuts, a dead kitchen printer with the front carrying on, a hard kill of
the bridge with a restart, a connection cut mid-job, and a marked reprint.

In a browser, against the running API, the real bridge process and two simulators: both *Cetak
tes* buttons printed their own ticket and reported "Tercetak — printer mengonfirmasi"; the
station-mapping card emptied as products were assigned; the till showed *Tertahan*, *Belum pasti
tercetak* and the bridge-offline warning.

## 3. Verified on real hardware

**First session: 3 October 2026.** Both IW-J300H units (firmware V1.050.r1), at the vendor's home
in Balikpapan on a home router (2.4 GHz), the bridge on a Windows laptop, the API local with demo
data. **Not at the café, not on the café's Wi-Fi, and not with the bridge on the tablet.** What
"pass" means here is what a person at the printers reported or photographed, and what the bridge
logged; where nobody looked, it says so.

| # | Test | Date | Result |
|---|---|---|---|
| 1 | `--check` and the probe: port, status support, cut command, buzzer command | 3 Oct 2026 | **pass, both units.** 9100 open; `DLE EOT` and `GS r` answer; `--check` `ready`. Cut: every command cuts and leaves a centre tab (partial only). Buzzer: `ESC B` sounds; `ESC ( A` prints `(A0`. Details in `docs/printing.md` |
| 2 | Test ticket on each printer: readable, fits 80 mm, cuts below the last line | 3 Oct 2026 | **partly.** Both printed and confirmed processing (`GS r`); the owner reported both "work as intended". No photo of the test page was checked for the right edge, the large `MEJA 12` or the inverse labels |
| 3 | Mixed food + drink order: receipt and Bar slip at the front, Dapur slip in the kitchen | 3 Oct 2026 | **pass for a table's send** (Pesanan 002, Es Kopi Susu + Matcha Latte + Nasi Goreng): nota and Bar slip at the front, Dapur slip in the kitchen, all three *Tercetak*; its receipt printed at payment. A pay-first takeaway with both printers on has not been run. **The receipt's logo (`GS v 0`) has not been looked at** |
| 4 | Full table flow: send, add a round, cancel a sent item, pay | | |
| 5 | Marked reprint (CETAK ULANG) | | |
| 6 | Cover open, then closed: prints once | | |
| 7 | Paper exhausted, then refilled: prints once | | |
| 8 | Kitchen printer off 20 minutes: front keeps printing, old slip is held, TERLAMBAT on release | | |
| 9 | Power pulled mid-slip: shown uncertain, never reprinted on its own | | |
| 10 | Printer restart | | |
| 11 | Router restart | | |
| 12 | Wi-Fi interruption and recovery | | |
| 13 | Tablet restart | | |
| 14 | Chrome POS in front while both printers receive their tickets | | |
| 15 | Switching apps, and the screen off | | |
| 16 | A full service-length run, both printers, no duplicate and no lost ticket | | |
| 17 | Kitchen distance: 10–15 m through walls over a whole service | | |
| 18 | Buzzer and cutter on real kitchen tickets | | |

Also seen on 3 October 2026, outside the numbered list:

- **A slip older than 15 minutes was held, not printed** (towards item 8). Pesanan 001's Dapur slip
  was made at 12.31 while only the front printer was connected; when the kitchen printer joined at
  12.46 the till showed it *Tertahan* and nothing came out on its own. The release (*Cetak
  sekarang (terlambat)*) was not pressed, so the TERLAMBAT label is still unseen on paper.
- **The bridge noticed its host sleeping.** The laptop slept from 13.01 to 16.16; on waking the
  bridge logged "frozen for 10886 s" and reconciled. Nothing was queued, so this shows the
  detection, not a recovery with work waiting.
- **Kitchen buzzer on a real slip:** `beep` was switched on before Pesanan 003's Dapur slip
  (13.01). Nobody has yet said whether it beeped or whether the alarm light came on.
- **prt-10 (find by name), on both printers, 22.31:** with no address in the config, `--check`
  found `IW-J300H-41CC` and `IW-J300H-2894` by name and both were `ready`. Each of the three ways
  of asking found each printer (everyone: 19–168 ms; its own address: 2–4 ms; every neighbour:
  9–18 ms); 20 of 20 questions answered each way; a printer asked for the other's name stayed
  silent; a wrong last address was corrected. Both printers had been switched off and on since
  the afternoon and came back with the **same** addresses, so a real change of address has still
  not been seen — that part rests on the simulated tests.

**One good shift is evidence, not proof.** Keep this log going for the first weeks: a date, what
was run, and what came out. The failures worth recording are the quiet ones — a slip that never
arrived, a duplicate, a bridge that stopped overnight.
