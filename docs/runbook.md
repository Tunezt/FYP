# Runbook

**For the person on shift, not for a developer.** Find your heading, do what it says. You do not
have to read the rest.

| If this is happening | Go to |
|---|---|
| The internet is down | [When something is wrong during service](#the-internet-is-down) |
| The tablet died mid-service | [The tablet died mid-service](#the-tablet-died-mid-service) |
| The printer will not print | [The printer will not print](#the-printer-will-not-print) |
| Nothing in the app works, but the internet is fine | [The API is down](#the-api-is-down-the-app-loads-but-nothing-works) |
| A sale was rung up wrong | [A sale was rung up wrong](#a-sale-was-rung-up-wrong) |
| The stock number is wrong | [Stock looks wrong](#stock-looks-wrong) |
| Someone forgot their PIN | [Locked out](#locked-out-a-lost-tablet-a-lost-link-a-forgotten-pin) |
| The tablet is lost or stolen | [Locked out](#locked-out-a-lost-tablet-a-lost-link-a-forgotten-pin) |
| Something has to be cancelled and the owner is away | [When the owner is not there](#when-the-owner-is-not-there-and-something-has-to-be-cancelled) |
| It is closing time | [Closing the day](#closing-the-day) |
| The database is gone | [Restoring from a backup](#restoring-from-a-backup) |

**Two rules that outrank everything below: keep selling, and do not make the numbers up.** A sale
written on paper can be entered later. A sale nobody wrote down is gone for good, and so is the
stock it took.

---

## When something is wrong during service

Read the one heading that matches. Each is written to be finished in a minute, standing up, with
customers waiting.

### The internet is down

The till needs the internet. There is no offline mode yet — that is M14, and it is not built — so
when the connection drops the kiosk stops accepting sales.

1. **Check it is really the internet.** Open any website on the tablet. If that fails too, it is
   the connection, not the app.
2. **Try the phone's hotspot.** Turn on tethering, connect the tablet to it, reload the kiosk. This
   is the fastest fix and it works for most outages. Keep the hotspot password written somewhere
   the staff can reach without you.
3. **If neither works, go to paper.** One line per customer: what they bought, how many, what they
   paid, cash or QRIS, and the time. Keep the sheet — it is the only record that exists.
4. **When the connection comes back**, ring the paper sales up on the till, oldest first. They will
   be timestamped as of now, not when they happened, so if it crossed a day boundary write a note
   on the sheet and keep it. (Entering them at their real time is M15-T10, not built yet.)
5. **Stock will be wrong until you do step 4**, because nothing came off the shelf in the system.
   Do not "fix" the stock number by hand as well or you will subtract it twice.

### The tablet died mid-service

Dead battery, cracked screen, will not wake. One tablet is one point of failure and this is what
that costs.

1. **Go to paper immediately** — same list as above. Do not stop selling.
2. **Get any other device on the kiosk**: a phone, a laptop, the owner's tablet. Open the kiosk
   link (Pengaturan → Layar kasir), sign in with a PIN, and carry on. A phone screen is a bad till
   but it is a working one.
3. If the link is not to hand and the old tablet is unrecoverable, see
   [Locked out: a lost tablet](#the-tablet-is-lost-stolen-or-sold) below.
4. **Enter the paper sales** on whatever device is now the till, oldest first, as in step 4 above.

### The printer will not print

1. Ask first whether the customer needs paper at all. Most do not.
2. Check the obvious: paper roll in the right way round, lid clicked shut, power, cable or
   Bluetooth pairing.
3. **The receipt still exists without the printer.** It is on the sale in the app, and it can be
   shown on the screen or read out.
4. Never hold up the queue for a receipt. Sell, then sort the printer out between customers.

### The API is down (the app loads but nothing works)

The symptom is the dashboard or the kiosk showing errors on everything while other websites are
fine.

1. **Reload once.** Then wait sixty seconds and reload again — most of these are momentary.
2. **Check whether it is only you.** Try the dashboard on the phone's mobile data. If it works
   there, it is the café's WiFi and the "internet is down" steps apply instead.
3. **If it is really down, go to paper** and keep selling. It is the same drill as a lost
   connection, because from behind the counter it is the same thing.
4. **Restart the API.** Once deployed this is Railway → the API service → Restart (M12-T4). Nothing
   is lost by restarting; the database is a separate service and keeps running.
5. If it comes back and immediately breaks again, do not keep restarting it. Write down the time it
   started and what was on screen, and send that with the day's paper sheet.

### A sale was rung up wrong

**Not yet paid** — an order that is still sitting in the queue from a QR menu: cancel it at the
till (Batal on the ticket) and ring it up again correctly. Nothing has moved yet.

**Already paid** — at the till, tap **Transaksi**. Today's sales are listed newest first; find it
by the time, or type the receipt number into the search box. Open it, check the lines are the ones
you meant, then choose:

* **Batalkan** — the whole thing was a mistake and the customer never took the goods;
* **Kembalikan** — the customer is handing goods back, or you are handing money back. Leave
  "barang kembali ke stok" on if the goods are re-sellable, and turn it **off** if they were
  drunk, spilled or thrown away.

Type the reason, then a **PIN pemilik atau manajer**. A cashier's own PIN will not work — that is
the point of it. The screen confirms what happened and can reprint the slip.

**After the shift has closed**, or for anything older than today: dashboard → Penjualan →
**Batalkan atau kembalikan transaksi**. Same search, same two choices, same PIN.

Either way the system writes a reversing entry — the original sale stays visible, nothing is
deleted, stock goes back on the shelf unless you said otherwise, and the approval is recorded in
Keuangan → Otorisasi manajer with who approved it and why.

**Two things never to do instead.** Do not ring up a second "correcting" sale, and do not edit the
stock by hand to compensate. Both leave the books further from reality than the original mistake,
and neither leaves a trail anyone can follow later.

### Stock looks wrong

Count the shelf first. Then Stok → the item → set the stock figure to what you counted → save.

That is not an override: it writes a **correction** movement into the ledger, so the difference is
visible afterwards as a correction rather than quietly disappearing. Correct one item at a time and
only when you have actually counted it.

If a lot of items are wrong at once, the usual cause is sales that never reached the system —
paper sales from an outage that were never entered. Enter those first, then count.

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

## Closing the day

1. **The cashier closes the shift at the till.** Tutup shift → count the drawer → type what is
   actually there. Do not type what the screen expects. The difference is the point of the
   exercise, and it is posted to the books as a variance either way.
2. **A difference is information, not an accusation.** Small and both directions over a week is
   normal. Consistently short is worth a quiet conversation. Consistently short on one person's
   shifts is worth a louder one.
3. **The owner checks Keuangan**: the shift you just closed, its expected-versus-counted, and the
   day's expenses. Then **Otorisasi manajer** — anything approved today that you did not know
   about.
4. **The nightly job runs by itself** and sends what needs attention to WhatsApp. You do not have
   to wait up for it. If it found nothing, it sends nothing — silence means normal.
5. **If the day's number looks wrong**, check the business day boundary before anything else:
   Pengaturan → Profil usaha → "Hari usaha dimulai jam". A café that closes after midnight with
   this set to 00.00 splits every night across two days.

---

## Known gaps at go-live

Honest list, so nobody discovers these at 8am. Each one is a task that exists and is not built.

| Gap | What it means in the shop | Task |
|---|---|---|
| No offline till | the internet going down stops sales; paper and re-entry | M14 |
| No backdated entry | paper sales are entered at today's time, not theirs | M15-T10 |
| No uptime alert | you find out the API is down by trying to use it | M15-T5 |
| No tested printer | receipt printing has not been proven on real hardware | M15-T6 |

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

## Who pays for what, every month

Fill in the amounts when each account is created (M12) and keep this table current. **Nothing here
should be on a free tier.** This project has already lost a database once, on 16 July 2026, to a
free-tier project being reaped with no warning, which is exactly what free tiers are allowed to do.

| Service | What it is | If it lapses | Cost |
|---|---|---|---|
| Supabase | the database, and the stored receipt photos | everything stops, and the data is at risk | |
| Railway | the API and the scheduled jobs (nightly alerts, backups) | till and dashboard stop; no backups | |
| Vercel | the dashboard and the kiosk pages | nothing loads, even though the data is fine | |
| Meta WhatsApp | the assistant and the alerts | messages stop; the rest keeps working | |
| Google Gemini | reading receipt photos, answering questions | photo capture and the assistant stop; the till is fine | |
| Domain (if any) | the address people type | links break | |

Two habits that cost nothing:

* **Put every renewal on one card and check that card monthly.** A card that expires takes the
  café down as effectively as a bug.
* **Check that last night's backup ran** — ["Did the backup work last night?"](#did-the-backup-work-last-night)
  above, once a week, takes ten seconds.
