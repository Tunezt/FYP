# Poernama print bridge

Takes print jobs from the Poernama API and prints them on the café's network receipt printers.
It sends ESC/POS commands over raw TCP, usually port 9100.

```
 Android cashier tablet                cloud (Railway)          restaurant Wi-Fi
 ──────────────────────                ───────────────          ─────────────────────────────
 Chrome: sell / send / pay ──HTTPS──▶  API writes             ┌─▶ FRONT printer  (IW-J300H)
                                       print jobs             │   receipt · nota · Bar slip
 print bridge (this program) ◀─HTTPS──  (it pulls)  ──TCP 9100─┤
 same tablet, beside Chrome                                   └─▶ KITCHEN printer (IW-J300H)
                                                                  Dapur slip
```

The bridge exists because neither end can reach the printers directly. The API is in the cloud
and cannot open a connection into the restaurant's network. The till is a web page in Chrome, and
a web page cannot open a raw TCP connection to a printer. Something on the restaurant network has
to fetch the jobs and hand them to the printers, and it has to stay powered on. That is this
program, and for Poernama it runs on the cashier tablet itself.

**Status: PILOT.** This is tested against simulated printers (`sim_printer.py`) and the real API
code. **No physical printer has printed from it, and it has never run on the cashier tablet.** It
stays a pilot — staff keep an eye on *Antrean cetak* — until the acceptance checklist at the end
of this file has been worked through on both real IW-J300H printers and on the tablet, over a
full service. `docs/printing-test-log.md` records what has been tested where; keep it up to date,
because one good shift is evidence, not proof.

## Where it can run

It needs only Python 3.9 or newer, with no extra packages, on a device that stays on during
service and is **on the same Wi-Fi as both printers**.

For Poernama that device is **the cashier tablet itself** (a Samsung Galaxy Tab A9/A9+). It is
already on, already charging at the counter, and already on the restaurant Wi-Fi, so no extra
hardware is needed. The tablet runs Chrome for the POS and the bridge beside it.

Android does not let a background program run freely, so there are two ways to host it there,
and they are for different stages:

| | Termux (today) | A dedicated Android companion app (for daily service) |
|---|---|---|
| What it is | A terminal app that runs the exact `print_bridge.py` in this folder | A small Android app that does the same work as a **foreground service** with a permanent notification |
| Ready now? | **Yes** — nothing to build, and it is the code these tests cover | **No** — it has to be written, built in Android Studio and installed on the tablet |
| Survives Chrome in the foreground | Usually, with the settings below | Yes: a foreground service is what Android designs for this |
| Survives the screen being off | Usually, with a wake lock | Yes, with a wake lock and a Wi-Fi lock held by the service |
| Survives Android 12+ "phantom process" killing | **Not guaranteed.** Android kills long-running child processes of an app; Termux runs Python as one. It can be switched off, but only with a PC over adb | Yes: the service is the app, not a child process |
| After a tablet reboot | Termux:Boot starts it, if the app has been opened once and battery optimisation is off | Starts itself (boot receiver), same conditions |
| After a force-stop or "swipe away" | **Manual start needed** | **Manual start needed** (Android forbids restarting a force-stopped app) |
| Visible status for staff | A terminal window and the till's printer line | A notification showing both printers, plus the till's printer line |
| Risk | Android may freeze or kill it quietly. The bridge now detects and reports this (below), so it is visible rather than a mystery | The normal, supported way to do this on Android |

**Recommendation.** Use Termux to bring the printers up and prove the whole path end to end on
the real hardware. Keep it only if it survives the tablet tests in the checklist below, run
during a real service. If it does not, the companion app is the fix, and it is a known,
well-trodden piece of Android work rather than a gamble.

**Neither option can survive being force-stopped**, because Android will not let any app restart
itself after that. If someone swipes the bridge away from the recent-apps list, staff must open
it again. The till shows the bridge as silent within 90 seconds, so it is noticed quickly.

### When the bridge is frozen, it says so

Android may suspend the process when the screen goes off or another app is busy. The bridge
measures this: if a two-second wait really took minutes, it logs it, rechecks with the server
what it was holding, and puts it in the printer's status line as *bridge sempat berhenti N
menit*. That turns "Android battery management" from a rumour into something recorded, so the
tablet tests below have an answer rather than a feeling.

### Running it under Termux on the tablet

1. Install **Termux** and **Termux:Boot** from **F-Droid** (not the Play Store build, which is
   old and cannot install add-ons). Both must come from F-Droid so their signatures match.
2. In Termux: `pkg update && pkg install python`.
3. Copy `print_bridge.py`, `probe_printer.py`, `bridge-config.json` and the two token files into
   `~/poernama-bridge` (via `Downloads`, `termux-setup-storage`, or a cable).
4. Samsung battery settings, all of them:
   - Settings → Apps → Termux → Battery → **Unrestricted**. Same for Termux:Boot.
   - Settings → Battery → Background usage limits → make sure Termux is **not** in *Sleeping
     apps* or *Deep sleeping apps*, and turn off *Put unused apps to sleep*.
   - Keep the tablet on the charger. Do not let it power off overnight if you want the morning's
     first ticket to print without anyone touching it.
5. Autostart: copy `termux-boot.sh` to `~/.termux/boot/`, then `chmod +x` it, and open
   Termux:Boot once so Android registers it.
6. Android 12 and newer kill long-running child processes ("phantom processes"). If the bridge
   dies after ~20 minutes with nothing in its log, that is this. It is switched off once, from a
   PC with adb:
   ```
   adb shell device_config set_sync_disabled_for_tests persistent
   adb shell device_config put activity_manager max_phantom_processes 2147483647
   ```
   If you would rather not connect a PC, that is the point at which the companion app is worth
   building instead.
7. Start it by hand the first time and watch: `cd ~/poernama-bridge && python print_bridge.py --config bridge-config.json`

### What the companion app would be, if we build it

A small Kotlin app, no Play Store account needed (installed as an APK on this one tablet):

- a **foreground service** with a persistent notification showing each printer's state, holding a
  partial wake lock and a Wi-Fi lock, restarted by `START_STICKY` and a periodic WorkManager check;
- the same protocol and the same safety rules as `print_bridge.py`, ported: one worker per
  printer, atomic claims, results bound to the claim's attempt, the fsynced journal that
  separates "never sent" from "may be on paper", held stale jobs;
- **device registration** by pasting or scanning the two printer tokens once, stored in
  EncryptedSharedPreferences, never in a file next to the app;
- a boot receiver so it comes back after a restart, and a button that deep-links into Samsung's
  battery settings so the exemptions can be set without hunting;
- Wi-Fi recovery: it watches connectivity, and re-checks what it was holding whenever the network
  returns or the process was frozen.

It needs the tablet's exact Android version to set the foreground-service type correctly (Android
14 requires one to be declared), and it must be tested on the tablet itself.

## Setup

1. **Printers on the network.** Connect both printers to the restaurant's **main** Wi-Fi (not a
   guest network, which usually blocks devices from reaching each other); the IW-J300H also has
   Ethernet if a cable can reach. Give each printer a fixed IP: set it on the printer, or add a
   DHCP reservation on the router, so the address in the config keeps working after a power cut.
   Most printers print their IP on a self-test page (hold FEED while powering on).
2. **Ask the printer what it speaks** — do not assume. From the tablet (Termux) or any machine on
   the same Wi-Fi:
   ```
   python probe_printer.py 192.168.1.50 --label front
   ```
   It prints nothing by default: it reports which ports are open, whether the printer answers the
   ESC/POS status requests, and whether it has a web configuration page. Add `--paper --buzzer` to
   print one labelled slip per cut command and per buzzer command — then write down which slip was
   actually cut and which one made a sound. That is how `cut` and `beep` get set. The results are
   saved to `probe-report.json`.
3. **Tokens.** In the dashboard: Pengaturan → Printer → *Buat token perangkat*, once for
   **Printer depan** and once for **Printer dapur**. Save each token in its own file next to the
   config (`front.token`, `kitchen.token`). Do not put a token inside the config file (the bridge
   refuses it), and never commit it. A token for the wrong printer is also refused.
4. **Config.** Copy `bridge-config.example.json` to `bridge-config.json` and set:
   - `api_base`: the API's `https://` address. Plain `http://` is refused except to `localhost`.
   - `device_name`: unique for **this installation** (e.g. `kasir-laptop`). Each printer's worker
     reports as `<device_name>-front` / `-kitchen`. The name is how the bridge recognises its own
     unfinished jobs after a restart: don't give two installations the same name, and don't reuse
     a name on a new host without copying its `state/` folder across.
   - per printer: `model` (`iware-iw-j300h` fills in the defaults below), `host`, `port`,
     `paper_mm` (80 → 48 columns, 58 → 32; or set `columns`), `cut` (`partial`, `full`, `none`),
     `feed_lines`, `status` (below), and optionally `beep`. Anything you set explicitly wins over
     the model's defaults.
   - `beep` only works once its command is confirmed: `{"command": "esc_b", "times": 2,
     "duration": 3, "verified": true}`. The bridge refuses a buzzer that has not been verified,
     because the wrong byte makes some printers print rubbish instead of beeping.
   - Remove a printer's section, or set `"enabled": false`, to run the bridge for one printer only.
5. **Check before going live:** `python print_bridge.py --config bridge-config.json --check`
   reports each printer's status and whether the API accepts the token, without taking any job.
   Add `--test-print` to print a layout test page on each printer (not a real order).
6. **Run:** `python print_bridge.py --config bridge-config.json`. Stop with Ctrl+C. A job being
   printed finishes first.

### Keeping it running

- **Windows:** Task Scheduler → Create Task → Trigger *At log on* (or *At startup* with "Run
  whether user is logged on or not") → Action: start `run-bridge.cmd` → Settings: "If the task
  fails, restart every 1 minute". Set Power Options so the machine never sleeps while plugged in.
  Logs go to `bridge.log`.
- **Linux / Raspberry Pi:** `poernama-print-bridge.service` (instructions at its top).
  `Restart=always` brings it back after a crash; `enable` starts it at boot.
- **Android:** `termux-boot.sh` → `~/.termux/boot/`. Open Termux:Boot once after installing, turn
  off battery optimisation for Termux and Termux:Boot, and keep the phone charging.

The bridge also catches its own errors and keeps going. The restart loops are there for crashes of
the Python process itself and for reboots.

## What the till will say, and why

| The bridge… | The job becomes | The till shows |
|---|---|---|
| saw the printer was down (unreachable, paper out, cover open) **before** taking anything | stays `pending` | *Menunggu printer*, plus the printer's status line in *Antrean cetak* |
| took a job, then found the printer down before sending a single byte | released back to `pending`, with the reason | *Menunggu printer* + reason. It prints unmarked when the printer returns: no paper can exist |
| sent it and the printer answered `GS r` after the cut | `printed`, evidence `printer_status` | *Tercetak* |
| sent it, but the printer cannot be asked (`status: none`) | `printed`, evidence `bytes_delivered` | *Terkirim ke printer*: the bytes arrived, nothing more is known |
| lost the connection part-way, or got no confirmation in time | `uncertain` | *Belum pasti tercetak*. **Never resent automatically.** The cashier checks the paper: *Kertas sudah ada* or *Cetak ulang* (marked CETAK ULANG) |
| could not turn the document into printer commands | `failed` | *Gagal cetak*: *Coba lagi* / *Tidak perlu dicetak* |
| was restarted mid-job | journal decides: never started → released; bytes may have left → `uncertain` | as above |

A job waiting more than **15 minutes** (e.g. the kitchen printer was off all morning) is **held**.
No printer takes it on reconnect. The cashier sees *Tertahan* and decides: *Cetak sekarang
(terlambat)* prints it with a **TERLAMBAT** label and the time it was made, and *Tidak perlu
dicetak* withdraws it. Fresh orders keep printing meanwhile.

Money never depends on printing: a failed or uncertain slip cannot undo or repeat a sale.

## The Iware IW-J300H: advertised, and what that is worth

The seller advertises 80 mm thermal printing, an automatic cutter, ESC/POS, Wi-Fi + LAN + USB +
Bluetooth + serial, and a kitchen alarm. None of that is a protocol specification, and Iware
publishes no command manual for this model. So the bridge ships a profile
(`"model": "iware-iw-j300h"`) whose settings are a **starting point taken from the specification,
not a tested fact**:

| Setting | Default in the profile | How it gets verified |
|---|---|---|
| Port | 9100 (the usual raw ESC/POS port) | `probe_printer.py` lists the ports that actually answer |
| Paper | 80 mm → 48 columns | the test ticket: nothing should run off the right edge |
| Cut | `GS V 66` (partial, feed first) | `probe_printer.py --paper` prints one labelled slip per cut command |
| Status | `gs_r` (printer confirms it finished) | the probe says whether `DLE EOT` and `GS r` answer over the network |
| Buzzer | **off** | `probe_printer.py --paper --buzzer` — whichever slip made a sound names the command |
| Wi-Fi mode | station (joins the restaurant router) | the printer's own self-test page and its configuration tool |

`--check` prints an **UNVERIFIED** line for this model until you have run the probe. Until the
tickets have come out of the real printers, nothing here claims this model works.

## `status` modes and what can really be detected

| `status` | Before each job | After each job | Use when |
|---|---|---|---|
| `gs_r` (default) | `DLE EOT 1/2/4`: offline, cover open, paper end, paper near end, error | `GS r 1`, which the printer answers only after it has processed everything before it, including the cut | the printer answers both (check with `--check` and `--test-print`) |
| `dle_eot` | as above | `DLE EOT` again. Real-time, so it says nothing about whether the job finished. Reports *delivered*, or *uncertain* if an error shows | the printer answers `DLE EOT` but not `GS r` |
| `none` | only that the TCP port accepts a connection | nothing. Reports *delivered* | the printer ignores status requests |

Limits, whatever the mode:

- **Printed ≠ readable.** The best evidence is "the printer processed the job through the cut".
  Faded paper, paper loaded the wrong way round, or a slip nobody picked up all look the same.
- **Paper out** needs the printer's paper-end sensor (almost all have one). **Paper low** needs a
  near-end sensor, which cheaper models often lack.
- **Jams / cutter faults** show only as the general error bit, and only if the model reports it.
  The bridge then stops sending to that printer until it recovers.
- **Cutting** has no confirmation of its own. A failed cut usually shows as an error on the next
  job, not this one.
- **Power loss after the bytes arrived** cannot be told apart from a slow print. It becomes
  *uncertain*.
- Some printers accept only one connection at a time. The bridge uses one connection per job, one
  job at a time per printer.
- Text is printed as plain ASCII: accents are stripped and `×`, `·`, `—` become `x`, `-`, `-`, so a
  wrong code page cannot garble an order. Indonesian needs nothing more.

## Testing without a printer

```bash
python sim_printer.py --port 9100 --name depan      # terminal 1: shows each slip as it "prints"
python sim_printer.py --port 9101 --name dapur      # terminal 2
```

Point the config at `127.0.0.1:9100` / `9101`, and `api_base` at a local API
(`http://localhost:8000`). The simulator can also refuse connections, run out of paper, drop the
connection mid-job, stall, or ignore status requests. The backend's `tests/test_print_bridge.py`
uses all of these. **The simulator only proves what the bridge does. It says nothing about any
real printer.**

## Real-printer acceptance checklist

Run each item on the actual printers, at the café, before relying on unattended printing. Write
down the model and firmware.

1. `--check` shows both printers `ready` (not `error`: if `error`, try `"status": "dle_eot"`,
   then `"none"`, and write down which works).
2. `--test-print` on each printer: all text readable, nothing cut off at the right edge, MEJA 12
   large, the labels as white-on-black bars, the paper cut below the last line (not through it).
   On 58 mm paper, set `paper_mm: 58` and repeat.
3. A takeaway with Bar and Dapur items: the front printer gives **receipt, then a separate Bar
   slip** (cut between); the kitchen gives the Dapur slip; the till shows *Tercetak* for all three.
4. A dine-in table: send, send more, cancel one sent item, pay. The paper matches docs/printing.md
   (nota and slips per send, TAMBAHAN, BATAL with reason, only the receipt at payment).
5. Reprint a slip from the till: it comes out marked CETAK ULANG.
6. Open the kitchen printer's cover, then make a sale. The till's printer line says the cover is
   open, and the slip waits. Close the cover: it prints **once**.
7. Take the paper out, make a sale: same as 6 with *Kertas habis*.
8. Switch the kitchen printer off for 20 minutes during a sale. The front keeps printing. When it
   comes back, the old slip is *Tertahan*. *Cetak sekarang (terlambat)* prints it with TERLAMBAT.
9. Pull the kitchen printer's power **while** a long slip is printing. The till must show *Belum
   pasti tercetak*, and the slip must **not** come out again on its own when power returns.
10. Disconnect the tablet from the internet for 5 minutes, make a sale from another device (or
    keep the till on a hotspot), reconnect: the slip prints once, not twice.
11. Restart each printer while a job is waiting: it prints once when the printer is back.
12. Restart the router. Both printers and the tablet rejoin, and queued slips print once. This is
    the test that catches a printer whose IP has moved, so check the DHCP reservations after it.
13. Restart the tablet mid-service: nothing prints twice, nothing is lost.
14. Kitchen distance: 50 slips over a real service without a missed or duplicated slip. If the
    Wi-Fi signal at the kitchen printer is weak, use Ethernet or a closer access point.
15. Optional buzzer: if `beep` is set (after the probe), confirm it sounds on a real kitchen
    ticket. Otherwise leave it off.

### The tablet, as it will actually be used

The bridge shares the tablet with Chrome, so these are as important as the printer tests. Run each
one, then look at the till's printer line and the bridge log. **Reliable background printing is
not claimed until these pass on the real tablet.**

16. **Chrome in front, a real order:** ring up a mixed order in the POS. The receipt, Bar slip and
    Dapur slip print while Chrome stays in the foreground.
17. **Switching apps:** open another app for five minutes, then send an order from a QR guest (or
    another device). The tickets print without anyone touching Termux.
18. **Screen off:** lock the tablet for 15 minutes with an order sent halfway through. The tickets
    print. If the till shows *bridge sempat berhenti N menit*, Android froze it: the battery
    settings are not yet right.
19. **Overnight:** leave it charging overnight and send an order in the morning before touching
    the tablet. This is the test that phantom-process killing fails.
20. **Wi-Fi off and back:** turn Wi-Fi off for two minutes during service. Orders keep being taken
    (they need the internet, so use a hotspot to keep the POS online if you want to test both
    halves), and the tickets print once when Wi-Fi returns — once, not twice.
21. **Tablet restart:** reboot the tablet and, without opening Termux, send an order. If nothing
    prints, autostart is not working and staff must open it manually — decide then whether that is
    acceptable or whether to build the companion app.
22. **Force-stop:** swipe Termux away from recent apps. The till shows *bridge di tablet tidak
    melapor sejak ...* across the top within 90 seconds — that warning comes from the server,
    which notices the silence, because a stopped bridge cannot report its own death. Someone
    reopens it: nothing is lost, everything queued prints, once.
23. **A full service-length run**, both printers, ordinary trade: no duplicate ticket, no missing
    ticket, nothing printed twice after a reconnect. Write it up in `docs/printing-test-log.md`.

### If Termux turns out to be unreliable, or too fiddly for staff

Then the companion app is the answer, and its build tooling has to be set up first. It is not on
the development machine today: there is a JDK (Java 21), but no Android SDK, no Gradle and no adb.
What is needed, once:

- Android SDK command-line tools, platform 34/35 and build-tools (3–5 GB);
- Gradle (the project's own wrapper pins the version);
- `adb`, to install the APK over USB — the same tool that switches off the phantom-process killer;
- the tablet's exact Android version, to declare the right foreground-service type.

None of that is unusual: it is an afternoon of setup, not a blocker. Ask for it when the decision
is made.
