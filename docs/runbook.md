# Runbook

**For the person on shift, not for a developer.** Find your situation, do what it says.

This file grows as the go-live tasks land. Today it covers first-time setup, backups and
restoring one (roadmap M15-T1 to M15-T3). The rest — internet down, tablet dead, printer
stuck, API down, a sale rung up wrong, stock looks wrong, closing the day, who pays for
what — arrives with **M15-T9**, and the empty headings at the bottom say so rather than
pretending.

---

## Setting up the real cafe, once

```bash
cd backend && ./.venv/Scripts/python.exe -m app.bootstrap --name "Kopi Senja" --owner "Ibu Ratna" --phone 081200011112
```

It asks for the owner's PIN twice rather than taking it on the command line, so
it does not sit in shell history. Out comes one business with the owner's login,
the standard units, the chart of accounts, the posting rules, pricing and loyalty
settings — and no products, no sales, no customers. Add the menu from the
dashboard, or photograph it.

Run it twice with the same number and it refuses: bootstrap makes a new business,
it does not reset an existing one.

**Then set when the day starts.** Dashboard → Pengaturan → Profil usaha → "Hari
usaha dimulai jam". If the cafe closes after midnight, set it to an hour after
the last bill is normally settled — 04.00 is the usual answer. Leave it at 00.00
only for a shop that shuts before midnight.

This is the single setting that decides what "today" means: the owner's daily
number, the shift reconciliation, the nightly alert baselines and the profit and
loss statement all use it. Get it wrong and a 00.15 bill is counted as tomorrow's
takings, which makes both days wrong and teaches the anomaly detector nonsense.
Changing it later is safe — nothing is rewritten, every report simply re-cuts the
same sales on the new boundary — but the daily numbers either side of the change
will not line up with the ones printed before it, so do it once, at setup.

**`python -m app.seed` is the demo cafe, not this.** It invents a month of sales
and deletes and recreates what it made last time. It refuses to run with
`ENVIRONMENT=production`, and asks before touching any database that is not on
the machine you are typing on. If you ever see it offer to run against the real
cafe, stop.

---

## Backups

### What runs

`python -m app.jobs.backup` takes one full dump of the database, checks it can be read
back, keeps a copy for the month, deletes anything older than the retention window, and
writes one line to a log whether it worked or not.

| | |
|---|---|
| **Where dumps go** | `BACKUP_DIR/daily/warung-pintar-<timestamp>.dump` |
| **Month copies** | `BACKUP_DIR/monthly/warung-pintar-<YYYYMM>.dump` — the first good dump of each month |
| **How many kept** | 30 daily, 6 monthly |
| **Log of every run** | `BACKUP_DIR/backup-log.jsonl`, one line per run, newest at the bottom |
| **When a run fails** | a high-severity WhatsApp alert to the owner, immediately, and the process exits 1 |

> **Not live yet.** The schedule itself is created in M12-T4 as a Railway Cron job:
> `0 19 * * *` (02:00 WIB) running `python -m app.jobs.backup`. On a Windows machine
> instead, `scripts\schedule-backup.ps1` registers the same thing as a daily task.
> `BACKUP_DIR` must point at storage on a **different machine and account** from the
> database — with `ENVIRONMENT=production` the job refuses to run otherwise.

### "Did the backup work last night?"

```bash
# the last run, whatever it was
tail -1 "$BACKUP_DIR/backup-log.jsonl"
```

`"ok": true` and a `"bytes"` figure in the hundreds of thousands or more means yes.
`"ok": false` means read `"error"` — it says what went wrong in plain terms.

You should never need to check this: a failed run sends a WhatsApp message that starts
**"Backup otomatis database GAGAL"** and names when the last good one was. If that message
arrives, the data is one server failure away from being gone. Deal with it today.

### The backup keeps failing

| What the error says | What it means | What to do |
|---|---|---|
| `pg_dump not found` | the Postgres client tools are missing on the machine running the job | install the Postgres 16 client tools, or set `PG_BIN_DIR` |
| `connection to server ... failed` | the database is unreachable | check the database is up before anything else |
| `row-level security policy` | the job is connecting as the restricted app role | `BACKUP_DATABASE_URL` must be the elevated role, never `DATABASE_URL` |
| `BACKUP_DIR is unset` / `not off-host` / `inside the deployment tree` | the destination is wrong | point `BACKUP_DIR` at storage on another machine and account |
| `under the ... floor` / `no table data for:` | the dump came out incomplete | do not trust it; fix the cause and run again by hand |

---

## Restoring from a backup

### First: the drill (do this monthly, not in an emergency)

```bash
backend/.venv/Scripts/python.exe scripts/restore-drill.py
```

It restores the newest dump into a throwaway database, checks the schema is the version
the code expects, runs the invariant tests against it as the app's own restricted role, and
compares row counts against the live database, table by table. Then it deletes the throwaway
database and prints `PASS` or `FAIL`.

**A backup you have never restored is not a backup.** If the drill has not been run this
month, you do not know that you have backups.

A row-count `delta` is normal — the shop kept trading after the dump was taken. A table
that has rows in the live database and **zero** in the restore is not normal, and the drill
fails on it.

### How long a restore takes — measured, not guessed

Measured 6 September 2026 on the development machine (Windows 11, local Postgres 16,
21 MB database, 355 KB dump, 3,555 rows across 44 tables):

| Phase | Time |
|---|---|
| create the database and its role | 0.3 s |
| `pg_restore` | 1.0 s |
| check the schema version | 2.6 s |
| run the invariant tests against it | 2.9 s |
| count every table on both sides | 0.2 s |
| drop the throwaway database | 0.3 s |
| **total** | **7.4 s** |

Read that as: **the restore itself is about one second per 350 KB of dump**, and the
checking around it is a fixed ~6 seconds. A café's first year is a dump in the low tens of
megabytes, so a real restore is minutes, not hours. Re-measure and update this table once
the production database has real history — an estimate in a runbook is worth nothing.

### A real restore, when the live database is gone

1. **Stop writing to the broken database.** Take the till offline. Staff go to pen and
   paper (M15-T10 enters those sales afterwards).
2. **Find the newest good dump.** Newest file in `BACKUP_DIR/daily/`. Check it appears in
   `backup-log.jsonl` with `"ok": true`.
3. **Prove it restores before you rely on it:**
   ```bash
   backend/.venv/Scripts/python.exe scripts/restore-drill.py --dump <path-to-that-dump>
   ```
   If that says `FAIL`, try the dump before it, and the month copy after that.
4. **Create the new database**, then apply `scripts/db-bootstrap.sql` to it — this creates
   the restricted `app_role` the application connects as. Skipping it means the app cannot
   log in, and giving the app an elevated role instead **silently turns off every tenant
   isolation policy in the system**.
5. **Restore:**
   ```bash
   pg_restore --dbname <new-database> --no-owner --single-transaction <dump>
   ```
6. **Point the application at it** (`DATABASE_URL`, `MIGRATION_DATABASE_URL`) and confirm
   the schema: `alembic current` must print the same revision as `alembic heads`.
7. **Check the books before reopening the till:**
   ```bash
   cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_invariants.py -q
   ```
8. **Say what was lost.** Everything between the dump's timestamp and the failure is gone
   from the system. Those sales are on paper or in people's heads; enter them (M15-T10)
   before the day's numbers are used for anything.

---

## When the owner is not there and something has to be cancelled

Batal transaksi, refund and a discount all need a **PIN pemilik atau manajer**. If you are the
only person who can give one and you are not at the shop, the cashier's only remaining option
is to do the sums in their head — which is exactly how the books stop matching the till.

So appoint a manager before you need one. Dashboard → Pengaturan → Staf kasir → **jadikan
manajer** on someone you trust, or pick "Manajer" when you add them. The same button takes it
back. A manager can approve those three things at the kiosk and nothing else: reports and
settings need the owner login, which is your phone number and your OTP, and no staff PIN can
reach them.

Every approval is recorded and you can read it: Keuangan → **Otorisasi manajer**. Each row says
what was approved, who approved it, the role they held at the time, who asked, and how much it
was worth. Look at it once a week. A manager approving large discounts late at night, or the
same cashier asking for voids every day, is the pattern this list exists to make visible.

If someone leaves: Pengaturan → Staf kasir → **nonaktifkan**. Their PIN stops working
immediately, including for approvals — the check is on the account, not on a token they were
given at the start of the shift. What they already approved stays in the trail, with the role
they held then.

---

## Locked out: a lost tablet, a lost link, a forgotten PIN

### Someone forgot their PIN, and there is a queue

Dashboard → Pengaturan → Staf kasir → **ganti PIN** next to their name → type four digits →
Simpan. The cashier goes back to the "siapa kamu" screen, picks their name and types the new PIN.
The old PIN stops working immediately. Nothing they already rang up changes — a PIN is a key, not
a name, and the sales stay under theirs.

Your own PIN has the same button. Reset it the moment you notice, because it is also the PIN that
approves a void or a refund, so forgetting it locks the till out of fixing mistakes as well as out
of your own shifts. If you would rather not be the only one who can approve, appoint a manager
(see "When the owner is not there").

### The tablet is lost, stolen, or sold

Do this from any phone or laptop you can sign in on. It takes one button.

1. **Pengaturan → Layar kasir (POS) → "Tablet hilang? Putuskan perangkat lama & buat tautan
   baru"** → confirm. Every old kiosk link stops working, and every till that is currently open —
   including a good tablet still on the counter — is signed out on its next tap. That bluntness is
   the point: you press this because a device is out of your hands.
2. **Copy the new link** it gives you and open it once on the replacement device.
3. The cashier picks their name and types their PIN. If they have forgotten it too, reset it
   first (above).
4. Sell something small to check, then carry on.

Nothing that was already rung up is affected. The old tablet, wherever it is, now shows
"Perangkat ini sudah tidak dipasangkan" and cannot see your menu, your staff names, or your
takings — even though whoever has it still holds the old link.

**Just showing the link again is safe.** The plain "Buat tautan kasir" button does not cut anyone
off; use it when you are simply pairing a second device or reading the link out over the phone.
Only the red one retires what exists.

### How long the recovery takes — measured

6 September 2026, development machine. The software's share of the whole drill — cut off, reset a
PIN, open the link, sign in, load the menu, take the first sale — is **0.2 seconds**:

| Step | |
|---|---|
| Cut the lost tablet off | 7 ms |
| The lost tablet's session is dead on its next request | 2 ms |
| Reset the forgotten PIN | 47 ms |
| Replacement tablet opens the link | 4 ms |
| Cashier signs in | 43 ms |
| Menu loads | 5 ms |
| First sale on the new device | 97 ms |
| **Total** | **204 ms** |

So the five minutes the plan allows is spent entirely on people and hardware: finding the
replacement tablet, getting the link onto it, and typing a PIN. Copy the link into WhatsApp and
open it there rather than retyping a long URL on a touchscreen — that one habit is most of the
difference between one minute and five.

**Re-time this on the café's own tablet before go-live**, once and with a stopwatch, and write the
number here. The figures above are the server's work only; they say nothing about how long the
café's WiFi and the café's tablet take.

---

## Still to be written (M15-T9)

- The internet is down
- The tablet died mid-service
- The printer will not print
- The API is down
- A sale was rung up wrong
- Stock looks wrong
- Closing the day
- Who pays for what, each month
