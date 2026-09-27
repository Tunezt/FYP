# Printing: what has actually been tested, and where

**Status: PILOT.** The printing path is written and tested in software. **No ticket has ever come
out of a physical Iware IW-J300H from this system, and the bridge has never run on the cashier
tablet.** Until both of those have happened, over a full service, printing is a pilot and staff
should keep an eye on the print queue.

Three columns, kept honest and separate. "Simulated" means a program that pretends to be a
printer (`bridge/sim_printer.py`) on localhost TCP. It proves what our software does. It proves
nothing about an Iware printer, about Wi-Fi through walls, or about Android battery management.

Last updated 21 September 2026 (prt-9).

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

Run by hand against the live API and the real bridge **process** (`scratchpad/e2e_bridge.py`,
17/17 checks): routing, cuts, a dead kitchen printer with the front carrying on, a hard kill of
the bridge with a restart, a connection cut mid-job, and a marked reprint.

In a browser, against the running API, the real bridge process and two simulators: both *Cetak
tes* buttons printed their own ticket and reported "Tercetak — printer mengonfirmasi"; the
station-mapping card emptied as products were assigned; the till showed *Tertahan*, *Belum pasti
tercetak* and the bridge-offline warning.

## 3. Verified on real hardware

**Nothing yet.** The printers have been ordered and have not arrived. Nothing below has been
attempted on a physical printer or on the cashier tablet.

To be filled in, with dates, as `bridge/README.md`'s acceptance checklist is worked through:

| # | Test | Date | Result |
|---|---|---|---|
| 1 | `--check` and the probe: port, status support, cut command, buzzer command | | |
| 2 | Test ticket on each printer: readable, fits 80 mm, cuts below the last line | | |
| 3 | Mixed food + drink order: receipt and Bar slip at the front, Dapur slip in the kitchen | | |
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

**One good shift is evidence, not proof.** Keep this log going for the first weeks: a date, what
was run, and what came out. The failures worth recording are the quiet ones — a slip that never
arrived, a duplicate, a bridge that stopped overnight.
