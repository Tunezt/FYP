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

## Still to be written (M15-T9)

- The internet is down
- The tablet died mid-service
- The printer will not print
- The API is down
- A sale was rung up wrong
- Stock looks wrong
- Closing the day
- Who pays for what, each month
