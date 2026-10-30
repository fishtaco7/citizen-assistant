# Citizen Assistant

A small Star Citizen overlay. Press **F3** for a nav bar at the top of the screen;
the first tool is a **mining scanner** that reads the signature number from the
scan HUD and tells you what the rock is.

The scanner and ore finder use screen capture + OCR; the hauling tools use
read-only public HTTP APIs. The combat tracker tails `Game.log` for damage,
kills and soft deaths and feeds the **COMBAT** tab. The headless
blueprint/inventory tracker tails the same log for blueprint unlocks and item
acquisitions, then publishes structured events to the host overlay. Nothing
reads game memory, injects code, or synthesises input.

---

## Requirements

- Windows 10/11, Star Citizen running **borderless / windowed fullscreen**
  (exclusive fullscreen cannot be captured or overlaid reliably)
- Python 3.11+ (built and tested against 3.13)

### macOS (UI preview only)

The panels, theme, network tools and scanner can run on a Mac for development.
A small platform layer (`app\win32.py`, `app\hotkeys.py`, `app\config.py`)
swaps the Win32 calls for Qt equivalents. What is different:

| Feature | macOS preview |
| --- | --- |
| Nav bar, scanner, ore finder, HAULING, INVENTORY, COMBAT, settings | Work |
| `--demo` synthetic signature | Works (OCR included) |
| Spoken ore announcements | Work (AVSpeechSynthesizer) |
| Screen capture of a game | Not applicable — Star Citizen is Windows-only |
| Capture exclusion (`WDA_EXCLUDEFROMCAPTURE`) | No-op, windows appear in screenshots |
| F3 / F4 | Application shortcuts: only while an overlay window has focus. Use the tray menu otherwise |
| Config location | `~/Library/Application Support/Citizen Assistant/` |

Setup without Homebrew, using [uv](https://astral.sh/uv):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.13
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
./run.command                     # demo mode
./run.command --open hauling      # start with a tool open
```

`run.command` passes any arguments straight to `app`, and defaults to `--demo`
so no capture region is needed. The Windows launchers are unaffected.

## Install

### One-click install (Windows)

Run **`install.bat`** from the folder you want to use. It sets up `C:\scov` (if
needed), installs the requirements, creates a **Citizen Assistant** shortcut on the
desktop and in the Start menu, removes shortcuts left behind by a previous install,
and asks whether it should start with Windows.

The shortcuts launch `pythonw.exe launcher.py` by absolute path, so **no console
window is ever shown** and the shortcut always runs the copy it was created from.
`run-hidden.vbs` does the same thing straight from the folder.

Only one copy runs at a time. Launching a second copy shows a short notice
instead of opening a duplicate overlay, because the extra copy could not
register the global F3/F4 hotkeys and would look like they had stopped working.

`uninstall.bat` removes the desktop / Start menu / startup shortcuts; your
settings and the `C:\scov` environment are left alone.

Because `pythonw.exe` has no console, unhandled errors are appended to
`%APPDATA%\Citizen Assistant\error.log` — check that file if the overlay stops starting.

### Checking which build is running

Every copy stamps its build in `⚙` next to the title, and on the command line:

```bat
C:\scov\Scripts\python.exe -m app --version
```

Windows keeps shortcuts forever. After downloading a new copy, run `install.bat`
**from the new folder**: it rewrites the desktop and Start menu shortcuts to point
at that folder, and deletes any left behind by a previous install, so Start menu
search can no longer launch the old one. Shortcuts now call `launcher.py` by
absolute path, so the copy that runs is the one next to the shortcut's folder
rather than whatever happens to be the working directory.

Settings and notes from an older `SC-Overlay` folder (`config.json`,
`inventory.json`, `unlocked_blueprints.json`) are copied into the new
`Citizen Assistant` folder automatically on first run, so the region
calibration and stash notes carry over.

### Prebuilt EXE (GitHub Actions)

The repository can build a Windows executable without a Windows machine. On
GitHub, open **Actions → Build Windows EXE → Run workflow** on `main`. When it
finishes, download the **CitizenAssistant-windows-x64** artifact, unzip it, and
run `CitizenAssistant.exe`. Keep the folder intact: the executable needs the
files next to it.

The build runs PyInstaller on `windows-latest` with the pinned versions from
`requirements-lock.txt`. `citizen-assistant.spec` bundles `app/data` and
`app.ico`, and collects the OCR runtime that `app/ocr.py` imports lazily. Pushing
to `main` or a `v*` tag runs the build, so the newest artifact always matches the
newest code; you can also start it by hand.

To build locally on Windows:

```bat
py -3.13 -m pip install -r requirements-lock.txt pyinstaller
py -3.13 -m PyInstaller --noconfirm --clean citizen-assistant.spec
```

The result is `dist\CitizenAssistant\CitizenAssistant.exe`.

### Manual install

PySide6 pulls in very deep paths, so use a short install location — `C:\scov` is
what the launchers look for:

```bat
py -3.13 -m venv C:\scov
C:\scov\Scripts\python.exe -m pip install --upgrade pip
C:\scov\Scripts\python.exe -m pip install -r requirements.txt
```

`setup.bat` runs exactly those three steps, which is the quickest way to set up a
second machine.

`requirements.txt` installs `PySide6-Essentials` + `PySide6-Addons` instead of
the full `PySide6` meta-package, so Qt WebEngine and Qt PDF are not downloaded
(several hundred MB saved). The overlay only needs Qt Core/GUI/Widgets and
QtTextToSpeech.

If you install the venv somewhere else (for example `.venv` next to this file),
`run.bat` will find it — the search order is `C:\scov`, then `.venv`, then
whatever `pythonw` is on `PATH`.

## Continue on another PC

Everything needed to build and run is in this folder — no database, no compiled
artifacts. Copy it over (or clone a private repo) and rebuild the venv:

1. Install **Python 3.13 (64-bit)**. The python.org build is the least
   surprising; the Microsoft Store build also works (this project was built on
   it), but it redirects `%APPDATA%` and trips the 260-character path limit when
   PySide6 is installed in a deep folder.
2. Get the folder onto the new PC — it already lives in OneDrive, so it may sync
   by itself; otherwise copy it or clone it. Skip `.venv`, `__pycache__`,
   `.DS_Store` and `tools\preview_out`; they are machine-specific or
   regenerated. A source-only `citizen-assistant-windows.zip` is the easiest way to
   move it without the macOS virtualenv.
3. Run `install.bat` (or `setup.bat` if you only want the environment). It
   creates `C:\scov` and installs `requirements.txt`, then adds the shortcuts.
   For the exact versions this build was tested with, install
   `requirements-lock.txt` instead.
4. Launch the overlay from the **Citizen Assistant** shortcut (or `run-hidden.vbs`);
   both pick up `C:\scov` or a local `.venv` on their own and show no console.

Not everything lives in the folder:

| What | Where it lives | On the new PC |
| --- | --- | --- |
| Capture region, panel positions, hotkey prefs | `%APPDATA%\Citizen Assistant\config.json` | Don't copy it — the region picker runs on first start |
| Ore location cache | `%APPDATA%\Citizen Assistant\where_to_mine.json` | Re-downloaded from scminer.rocks |
| Inventory stash notes | `%APPDATA%\Citizen Assistant\inventory.json` | Manual notes — copy this file to keep them |
| Blueprint + inventory registry | `%APPDATA%\Citizen Assistant\unlocked_blueprints.json` | Permanent unlock history — copy this file to keep it |
| Planner route cache | `%APPDATA%\Citizen Assistant\routes_cache.json` | Rebuilt on demand; delete it to force a full UEX sync |
| OCR models | inside the `rapidocr-onnxruntime` wheel | Reinstalled with the deps |
| Signature database | `app\data\signatures.json` | Travels with the code |

Panel positions are per-machine, so expect to place the scanner, ore finder and
inventory panel once after calibrating.

Git is the easiest way to keep two machines in sync:

```bat
git init
git add .
git commit -m "Citizen Assistant"
```

`.gitignore` already excludes the venv, `__pycache__` and `tools\preview_out`,
so nothing machine-specific ends up in the repo. Push to a private remote and
pull on the other PC, then run `setup.bat` once there.

## Run

```bat
run.bat
```

`run-hidden.vbs` starts the same command with no console flash at all. Double-
click it, or make a shortcut to `C:\scov\Scripts\pythonw.exe` with arguments
`-m app` and “Start in” set to this folder. Some managed PCs block VBScript; in
that case use `run.bat` or the shortcut.

`run-debug.bat` keeps a console open and prints scan results, which is what you
want if something looks wrong.

Starting the app with no saved region (first run, or after deleting
`%APPDATA%\Citizen Assistant\config.json`) immediately opens the region picker. The
scanner loads OpenCV and the OCR model only after a region exists, so the window
opens faster; the first scan after calibration may take a moment while they
load on the scanner thread.

### Windows notes

- `run.bat`, `run-hidden.vbs` and the in-app **RESTART** button all start the app
  with `pythonw`, so no console window appears — and the helpers that talk to
  PowerShell (refinery speech, WAV playback) pass `CREATE_NO_WINDOW` for the
  same reason.
- `Game.log` is looked for in the RSI launcher's default
  `…\Roberts Space Industries\StarCitizen\LIVE\Game.log`, the same layout under
  `Program Files`, `Program Files (x86)` and `C:\Games`, and under
  `StarCitizen`, `Roberts Space Industries\StarCitizen` and `Games\StarCitizen`
  on every fixed drive. `LIVE`, `PTU`, `EPTU` and `HOTFIX` are all checked. Set
  `SC_GAME_LOG` to a full path to override the search.
- The overlay windows are marked `WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`, so they
  never steal focus from the game. They are **visible in screenshots and
  streams by default**; `⚙` → the **HIDDEN** button hides them from capture
  (OBS, the game's own screenshots) when you want that instead.
- The OCR path needs `opencv-python` and `rapidocr-onnxruntime` from
  `requirements.txt`. `setup.bat` installs both into `C:\scov`.

## Controls

| Key / control | Action |
| --- | --- |
| **F3** | Show / hide the nav bar |
| **F4** | Hide everything (panic key) |

F3 and F4 are delivered two ways: `RegisterHotKey` for the normal case, and a
25 ms `GetAsyncKeyState` poll for games that stop delivering hotkeys while
exclusive fullscreen owns the display. If they still do nothing in a particular
game, run it borderless / windowed fullscreen rather than exclusive.
| drag a panel header | Move that window |
| `⚙` on the nav bar | Open settings |
| `MINING` on the nav bar | Show / hide the scanner |
| `ORE FINDER` on the nav bar | Show / hide ore locations |
| `HAULING` on the nav bar | Show / hide trade routes and hauling contracts |
| `INVENTORY` on the nav bar | Show / hide manual stash notes |
| `COMBAT` on the nav bar | Show / hide the live Game.log combat feed |
| `NOTES` on the nav bar | Show / hide the notepad |
| tray icon | Show the bar, quit |

> On macOS, F3/F4 are application shortcuts — they work while one of the
> overlay windows has focus. The tray menu always works.

The nav bar remembers whether it was open and the scanner remembers where you
dragged it.

The **combat tracker** runs automatically in the background and feeds the
`COMBAT` tab. The tracker itself stays headless; the panel renders events from
the `OverlayApp.combat_event` Qt signal. Other hosts can consume the same
payloads through the tracker's thread-safe queue.

## Calibrating the scanner

The scanner needs to know where the signature number appears on your screen.

1. Open the SC mining scan HUD so a signature number is visible.
2. Click `⚙` → **SELECT** in *Capture region*.
3. Drag a box **tightly around the digits only** (a little padding is fine, but
   do not include the word "SIGNATURE" or the units).

A good box is roughly 200 × 50 physical pixels. The region is stored in
physical pixels, so it stays correct if you change display scaling later.

The scanner samples that box ~8× per second and ignores frames that have not
changed. A high-confidence exact signature locks on the first read; fuzzy or
less certain reads still require two identical results before they are shown.

## Reading the scanner

```
● MINING SCANNER              LOCKED
SIGNATURE  10,710             73% MATCH
BORASE                            ×3
EPIC · Gold / Bexalite 5-10%
```

- **BORASE ×3** — the mineral and how many deposits are in the rock
  (10,710 = 3 × 3,570).
- **DEBRIS ×2** — salvage: exterior hull panels and scrap metal from wrecks
  read as clean multiples of 2,000 (2,000, 4,000, 6,000, 8,000 …). A large
  wreck goes well past a 10-rock cluster, so `22,000` is `DEBRIS ×11` rather
  than an ore. When the same number could also be a ground deposit (4,000,
  6,000, 8,000 …), the detail line says so, e.g.
  `HULL PANELS / SCRAP METAL · OR ROC DEPOSIT ×1`.
- A leading `~` (`~QUANTANIUM`) means the number is *near* that ore's signature
  but not an exact multiple of it — a hint from OCR jitter, never a lock, and
  never spoken.
- **73% MATCH** — OCR confidence, not mineral certainty. The signature itself
  is matched exactly against the table whenever possible.
- A number already on screen is only replaced after two identical reads, so OCR
  alternating between two readings (for example `8,000` and a stray leading
  digit giving `28,000`) cannot make the panel flip back and forth. The first
  lock is still immediate.
- If the reading keeps flickering, the crop is probably catching a digit from a
  neighbouring HUD element; re-calibrate with a tighter box around the digits.
- The second line is the rarity and the secondary ores for that signature.
- No known signature at that exact number shows `UNKNOWN` plus the closest
  table entry, so a single misread digit is obvious instead of misleading.

When the number leaves the screen the last reading stays visible but dims.

Leaving the pilot seat closes the scanner. The overlay reads the same
`Game.log` notification the hauling workflow uses (`Exited pilot seat`), so the
panel disappears when you stand up without anything touching the game. Set
`close_scanner_on_seat_exit` to `false` in `config.json` to keep it open.

### Voice

The scanner can say the result out loud. When a signature locks it announces the
ore and the number of rocks:

```
7,200 -> "Bexalite, times 2"
10,710 -> "Borase, times 3"
3,585 -> "Gold"
6,000 -> "Debris, times 3"
```

- Only **exact** signature matches are spoken. A fuzzy match is usually an OCR
  misread, and saying the wrong ore is worse than saying nothing.
- Each rock is announced once. Re-reading the same signature, a one-digit OCR
  jitter, or the scan HUD dropping and re-acquiring the same rock does not repeat
  the announcement. A genuinely different signature (a new rock) still speaks,
  even when it is the same ore. The remembered rock is only forgotten after
  `voice_idle_reset_seconds` (default `12`) without a scan, or when pausing and
  resuming.
- `⚙` → **Voice** has an **ON/OFF** button and a volume slider; the choice is
  saved as `voice_enabled` and `voice_volume` (0.0–1.0).

Speech uses QtTextToSpeech — SAPI on Windows, AVSpeechSynthesizer on macOS. If no
speech engine is installed the VOICE button says so in its tooltip and the
scanner keeps working silently.

## Refinery work orders (headless)

`app/refinery.py` is a separate, windowless tracker for refinery work orders. It
does not create widgets, resize the scanner, or open its own window. A host such
as `MiningPanel` subscribes to it and decides how to expand the layout.

```python
from app.refinery import RefineryTracker

tracker = RefineryTracker()
tracker.subscribe(lambda event: print(event.event_type,
                                      event.is_refinery_order_active))
tracker.start()                 # emits the restored state immediately
...
tracker.close()
```

- It tails `~/starcitizen/LIVE/Game.log` read-only, then falls back to
  `SC_GAME_LOG`, the normal LIVE/PTU/EPTU locations, and CrossOver/Whisky
  bottles. A missing log is normal: the watcher waits and retries.
- Jobs persist to `refinery_jobs.json` in the same per-user config directory as
  the other caches. Each job stores system, station, processing method, start
  timestamp, duration/countdown and end timestamp, so multi-day orders survive
  restarts.
- `is_refinery_order_active` is true while at least one timer is running.
  `state_changed` events carry that boolean; `state_queue` also receives the
  booleans for a host that prefers polling a `queue.Queue`. Snapshot helpers are
  `tracker.jobs`, `tracker.active_jobs`, `tracker.countdowns` and
  `tracker.state`.
- Other events are `job_started`, `job_complete`, `countdown` and
  `jobs_changed`. `countdown` is emitted once per second while work is active.
  Callbacks run on the tracker's dispatcher thread, so a Qt host should marshal
  them to the UI thread. `RefineryEvent.to_dict()` and
  `tracker.state.to_dict()` are ready for a Qt signal or a JSON bridge.
- When a timer hits zero the tracker queues an OS speech alert saying
  “Refinery order complete” from a dedicated worker thread. macOS uses `say`,
  Windows uses SAPI through PowerShell, and Linux uses `spd-say`/`espeak`; an
  optional WAV can be supplied as a fallback. Pass
  `audio=RefineryAudio(enabled=False)` to silence it.
- Log formats change. The default parser accepts broad refinery/work-order
  triggers and fields such as `station`, `location`, `system`, `method`,
  `duration`/`time_remaining`, `commodity` and `quantity`. If a patch omits the
  duration, call `tracker.add_job(...)` manually; pass custom
  `RefineryLogParser` patterns through `log_watcher_factory` if needed.

The tracker is read-only and anti-cheat-safe: no memory reads, injection, input
synthesis, or game-file writes. It is not wired to the scanner layout in this
repository yet; that is deliberately left to the host UI.

## Ore finder

`ORE FINDER` on the nav bar opens a live copy of
[scminer.rocks/data/where-to-mine](https://scminer.rocks/data/where-to-mine):

- The list is every minable ore with its price per SCU, scan signature and best
  hotspot. Filter with `ALL / SHIP / ROC / FPS`.
- Click an ore to see the ranked hotspot list: system, body type, abundance %,
  gravity, hazard and the suit or gear it needs.
- Below that: every refinery deck with its yield bonus, the recommended laser
  and gadget, and the page's thermal/cryogenic warnings.
- If the scanner has locked a rock, opening the ore finder jumps straight to
  that ore's page.

The list is one request, and each ore page is fetched the first time you open
it (about 0.5 s). The `↻` button re-syncs. If the site cannot be reached the
panel keeps working from the last snapshot and shows `OFFLINE` plus the cache
time, so it never goes blank mid-flight.

Data belongs to [SCMINER](https://scminer.rocks) and is fetched on demand for
personal use; the footer shows the patch number the data came from.

## Combat log

The **COMBAT** tab in the nav bar shows the live `Game.log` feed: victim,
attacker, weapon, soft-dead/destroyed status, and session counters. **DAMAGE**
also shows weapon-impact and hull-damage lines; **CLEAR** clears the feed
without resetting the session stats. The feed keeps the newest events first and
does not write anything to disk. It shows the victim/attacker pair exactly as
logged; it does not yet know which character name is yours, so it does not label
one side as “you”. `DOWNED` means a ship at `Soft-Dead`; FPS knockdown is not a
separate state in the current parser.

Under the hood, `app/combat.py` and `app/combat_log.py` stay Qt-free. The
tracker reads `Game.log` asynchronously, updates one target state per ship or
actor, and publishes structured payloads. `app/ui/combat_panel.py` is only a
renderer subscribed to `OverlayApp.combat_event`. The UEX HTTP client stays in
`app/hauling.py`, `app/planner.py` and the station snapshot; only the
marketplace search panel was removed.

```python
from app.combat import CombatTracker

tracker = CombatTracker()
tracker.subscribe(lambda event: print(event.to_dict()))
tracker.start()                 # starts the read-only log watcher
...
tracker.reset_session()         # start a fresh gameplay loop
tracker.close()
```

The host overlay wires the tracker to `OverlayApp.combat_event`, a Qt
`Signal(object)`, and the COMBAT panel renders those payloads. A host can also
poll `tracker.event_queue`, which receives `CombatEvent` objects, or read
`tracker.stats` / `tracker.state` directly. A typical HUD alert filters terminal
statuses:

```python
if event.status == "Soft-Dead":
    show_hud("Target Disabled (Soft Death) - Clear to Board")
elif event.status == "Destroyed":
    show_hud("Target Destroyed")
```

Every payload includes:

- **Timestamp** — the UTC epoch from the `Game.log` line, plus an ISO string in
  `to_dict()`;
- **Target name and type** — for example `Gladius` / `Vehicle`, or the victim
  actor name / `Actor`;
- **Operational status** — `Active`, `Soft-Dead`, or `Destroyed`;
- optional attacker, weapon, damage type, zone, destroy level and raw line.

The parser recognises the current `CVehicle::OnAdvanceDestroyLevel` line and
distinguishes `Soft-Dead` (destroy level `0 → 1`, hull disabled but intact)
from `Destroyed` (`1 → 2` or `0 → 2`, catastrophic explosion). It also parses
`CActor::Kill` player-kill lines and broad, configurable vehicle-damage and
weapon-impact patterns. A late soft-death line cannot downgrade a destroyed
target, and repeated terminal lines do not flash the HUD twice. If a patch
changes the prose, pass a `CombatLogParser` with custom patterns through
`CombatTracker(log_watcher_factory=...)`; the stable destroy-level and actor-kill
formats remain available.

Session analytics are scoped to the active gameplay loop:

- unique targets neutralised, soft-dead and fully destroyed;
- total damage events, weapon impacts, kills, player kills and deaths;
- engagement count, the last time delta between neutralisations, and the mean
  interval;
- survival uptime, current survival stretch and longest stretch;
- time since the last event and last damage.

Call `reset_session()` (or `reset_analytics()`) to clear targets, counters and
timing. `flush_stats(reset=True)` returns the completed loop's snapshot and
starts a clean session in one call. The tracker keeps no persistent combat
history by default.

Parsing and stats stay off the UI thread: the watcher runs in its own daemon
thread, and listeners run on the event-bus dispatcher. A Qt host should connect
`combat_event` with a queued connection when the receiver must run on the GUI
thread.

## Blueprint / inventory tracker (headless)

`app/blueprints.py` and `app/blueprint_log.py` add a windowless crafting and
inventory feed. The watcher tails `Game.log` read-only, the parser extracts
blueprint names, layout/class codes, item names, quantities and loot containers,
and the registry worker persists permanent history to
`unlocked_blueprints.json`. No widget or window is created.

The canonical blueprint notification is the authoritative line Star Citizen
writes for an unlock:

```text
<2026-03-26T17:15:41.684Z> [Notice] <SHUDEvent_OnNotification> Added notification "Received Blueprint: Defiance Helmet Tactical: " [23] to queue.
```

The parser ignores the matching `<UpdateNotificationItem>` echo and bare
continuation lines, so one unlock produces one event. It also recognises broad
`CraftingBlueprint` / recipe unlock forms and inventory-database or loot lines
such as `CInventory::AddItem item_name='FS-9 LMG' quantity=1` and
`Opened loot box 'Crate_123' and found item 'Coda Pistol'`. The broad patterns
are configurable through `BlueprintLogParser`.

```python
from app.blueprints import BlueprintTracker

tracker = BlueprintTracker()
tracker.subscribe(lambda event: print(event.to_dict()))
tracker.start()
...
tracker.close()
```

The host overlay wires the tracker to two Qt signals:

- `OverlayApp.blueprint_unlocked_signal` — emits exactly
  `{"item_name": str, "timestamp": str, "is_new_unlock": bool}` for a blueprint
  acquisition. `is_new_unlock` is `False` when the registry already contains the
  blueprint, so a toast can say “already known” or skip the alert.
- `OverlayApp.inventory_event_signal` — emits the full event dictionary,
  including `event_type`, `item_code`, `quantity`, `container`, `is_new` and
  `checked_off`.

For a backend-only consumer, `tracker.subscribe_blueprints(listener)` calls
`listener` with the same three-key dictionary. A host can also poll
`tracker.event_queue`, read `tracker.last_event`, inspect
`tracker.blueprints` / `tracker.items`, call `tracker.is_unlocked(name)`, or read
`tracker.stats`. Acquired blueprints and components are stored with
`checked_off: true`, so a shopping list can mark them off without another file
format. The worker loads the registry before processing events, which is how a
fresh launch distinguishes a new drop from a duplicate.

Parsing, registry lookups and JSON writes stay off the UI thread. The log watcher
and registry worker are daemon threads, and callbacks run on the
`sc-blueprint-events` dispatcher. A Qt receiver should use a queued connection
when it must run on the GUI thread. Set `SC_GAME_LOG` to override the log path,
or pass `registry_path=` / `log_watcher_factory=` for tests or alternate
installs.

## NOTES

`NOTES` is a plain notepad next to `COMBAT`, for anything worth remembering —
routes, buyers, spawn rules, prices — without leaving the game.

- Type and it saves itself: a write is queued 700 ms after the last keystroke,
  and again when the panel is hidden or the app quits.
- The status chip reads `SAVED`, `SAVING…`, or `NOT SAVED` with the reason.
- Stored as plain UTF-8 at `%APPDATA%\Citizen Assistant\notes.txt`, so it can be
  opened, edited or backed up with any text editor. Writes go through a
  temporary file and a rename, so a crash cannot leave a half-written note.
- The footer shows the character count and the file name.

## HAULING

`HAULING` sits directly beside `ORE FINDER` and has three tabs:

- **TRADE ROUTES** — live commodity buy → sell routes from the public UEX API
  2.0: prices, stock, distance, profit per SCU, ROI and trip profit. By default
  no specific commodity is selected: the tab opens on the most profitable routes
  across every tradable commodity.
- **CONTRACTS** — a reference board of community-collected hauling missions
  (payout, rank, contractor, pickup/drop-off locations and SCU per leg).
  There is no public live hauling-contract API, so this tab is a **snapshot**,
  not a live mission board. It is clearly labelled `SNAPSHOT` / `NOT LIVE`.
- **HAULING PLANNER** — a profit-per-minute optimizer. It uses the same cargo
  capacity as TRADE ROUTES, then ranks complete buy → sell runs by estimated
  aUEC per minute after stock, budget, quantum distance, atmosphere and loading
  time. Each row exposes the path, investment, gross profit, duration and PPM.

Set **CARGO** to the SCU capacity of the ship you are flying; trip profit,
`FITS` and stock reliability are calculated from it.

The commodity box starts on **All commodities — top routes**. It lists the 500
best routes for the current cargo setting and shows each route's commodity in
the second line. Selecting a commodity switches to that commodity's complete
route list. The first all-commodity sync checks every tradable commodity (about
205 in the current UEX snapshot) and takes roughly 20 seconds; progress is shown
in the panel and the result is cached for 15 minutes.

### Planning filters

The three filter buttons combine, and the footer shows the reduced route count
(for example `98/264 ROUTES` or `TOP 500/7,254`):

- **AUTO-LOAD** — keeps runs where both ends are a major city or orbital station
  with a freight elevator, and removes planetary outposts. UEX does not publish a
  single "instant loading" flag, so this is an approximation: it excludes the
  small outposts that require manual cargo-grid work, but you should still
  confirm the loading service at the terminal in game.
- **CITIES + STATIONS** — uses UEX terminal metadata (`id_city` or
  `id_space_station`) to hide tiny or high-gravity outposts and keep major
  landing zones and orbital stations. Useful for large ships that need room to
  manoeuvre.
- **RELIABLE STOCK** — hides runs whose reported reachable stock cannot fill the
  configured cargo capacity. Each row also shows `STOCK OK`, `ONLY n SCU` or
  `STOCK ?`, so a short supply is visible even with the filter off.

Trade routes auto-refresh every five minutes while the panel is open; the `↻`
button refreshes immediately. UEX reports community price/stock data roughly
hourly, not in real time, so stock can still change before you arrive. The
footer shows the last update time and the route count.

### HAULING PLANNER

The planner is deliberately separate from the live TRADE ROUTES list. It is a
headless engine (`app/planner.py`) plus a thin UI worker
(`app/ui/hauling_planner.py`), so network calls, cache reads and the route maths
never run on the UI thread.

For each buy → sell pair it calculates:

- **Trip size** — `min(cargo capacity, reported stock, affordable SCU)`. A
  capital cap reduces the load rather than throwing away an otherwise usable
  route.
- **Investment** — buy price × trip SCU.
- **Gross profit** — (sell price − buy price) × trip SCU.
- **Duration** — loading/unloading, quantum travel, cross-system jump, docking
  and atmospheric penalties. Manual grid loading is deliberately slower than a
  major station's auto-load/freight-elevator service.
- **PPM** — gross profit ÷ duration, in aUEC per minute.

The path column shows the ordered route, including an orbit marker when a
planetary atmosphere is involved and a jump marker when the route crosses star
systems. UEX does not publish exact 3D coordinates, so quantum time uses the
published route distance in Gm plus the system/planet/orbit anchors and fixed
jump, atmospheric and loading constants. The defaults are a 0.35 Gm/s quantum
speed, auto-load base time, manual-load time per SCU, and city atmosphere
penalties (Area 18, Lorville, New Babbage, Orison and Levski).

Planner filters:

- **AUTO-LOAD** — only routes where both ends report auto-load/freight-elevator
  service.
- **LOW GRAVITY** — removes high-gravity cities and planet-side outposts,
  leaving orbital stations and low-gravity moon locations.
- **CAPITAL** — maximum aUEC to invest in one run. Leave it blank for no limit.
- **RANK BY PPM / PROFIT / TIME / INVEST** — re-ranks the routes by the selected metric; the planner recomputes on the worker thread.

The result list shows the 500 highest-ranked matches, with `PPM`, `PROFIT`,
`INVEST`, `SCU`, `MIN`, the path, and `AUTO`/`MANUAL` plus `LOW-G`/`ATMO`
badges. Hover a row for the loading, QT, jump and atmosphere breakdown.

Data is cached in `routes_cache.json` with a 15-minute refresh interval. The
cache stores commodity prices, raw route stock/demand, terminal navigation
anchors (system, planet, moon, orbit) and the timing model. When the file is
missing, the planner can seed itself from the existing `hauling_top_routes.json`
cache so the first open is instant; press **REFRESH** to force a full UEX sync
and rebuild the cache. If UEX is unreachable, the last good cache is used and
the header/footer say `CACHED` or `OFFLINE`.

### Workflow engine

`app/workflow.py` is a headless, thread-safe state machine that turns a chosen
Commodity-tab `Route` or a planner `PlannerRoute` into one active hauling run.
It creates no windows and imports no Qt classes; the hosting panel subscribes to
events and switches its own view.

The bridge stores an immutable `ActiveRouteManifest` with the session ID and
timestamp, commodity, total SCU, purchase price, origin (station/city and
auto-load), destination (station/city, gravity and demand price), base
investment, gross projected profit, target PPM, planned duration and the live
cargo-loss state. Commodity-tab routes without a planner PPM get an estimate
from the same timing constants used by the HAULING PLANNER.

```text
BROWSING ──initialize_hauling_job(route)──▶ LOCKED_LOADED
                                              │
                              pilot seat / begin_transit()
                                              ▼
                                          EN_ROUTE
                                           │    │
                              complete_hauling_job  abort_hauling_job
                                           ▼    ▼
                                       COMPLETED / ABORTED
```

`initialize_hauling_job()` maps the route, deducts the base investment from the
unlocked wallet, writes `active_run.json`, starts the read-only `Game.log`
tailer and emits `target_state="HAULING"` with the manifest. A pilot-seat enter
event advances `LOCKED_LOADED → EN_ROUTE`; entering the destination emits
`destination_reached` (automatic completion is opt-in).
`adjust_active_manifest(cargo_lost_scu)` subtracts lost cargo, recalculates
gross profit and live PPM, and rewrites the active cache. Completion sells the
remaining manifest SCU at the destination price (or an explicit `sale_price`);
abort releases the reservation and accepts an optional `salvage_proceeds`. Both
clear the active cache and settle `workflow_wallet.json`.

Events are delivered asynchronously through a queue-backed listener thread, so
a listener must marshal to the GUI thread before touching widgets. Keep the
engine for the process lifetime; call `close()` on application shutdown.

```python
from app.workflow import HaulingWorkflowEngine

engine = HaulingWorkflowEngine(starting_capital=2_000_000)
engine.subscribe(on_workflow_event)          # event.manifest_data, target_state

manifest = engine.initialize_hauling_job(route, cargo_capacity=32)
# ... pilot seat detected from Game.log ...
engine.adjust_active_manifest(cargo_lost_scu=4)
engine.complete_hauling_job(sale_price=manifest.destination.demand_price)
engine.close()
```

If `starting_capital` is omitted, the ledger starts with unknown capital and
will not reject a run; call `set_trading_capital()` before locking a load to
enforce the budget. State is stored under the normal per-user config directory
(`~/Library/Application Support/Citizen Assistant/` on macOS,
`%APPDATA%\Citizen Assistant\` on Windows): `active_run.json` is the resume cache and
`workflow_wallet.json` holds cash, reservations, realized profit and run counts.

The log tailer looks at `SC_GAME_LOG` first, then common LIVE/PTU/EPTU install
paths (including CrossOver and Whisky bottles on macOS). A missing log never
stops the engine; the monitor waits and retries. It reads the file only, starts
at the current end of file, and tolerates rotation/truncation. Default parsing
covers pilot-seat enter/exit notifications and the common `location`/`arriving
at` forms; pass a custom `log_monitor_factory` if a patch changes the log text.

### Data and safety

Trade routes and terminal metadata come from `api.uexcorp.uk/2.0`. The contract
snapshot is generated from
[FSCSOFT-Official/SCHaulingPlanner](https://github.com/FSCSOFT-Official/SCHaulingPlanner)
(MIT) and bundled in `app/data/hauling_missions.json`, with a background refresh
from GitHub when available. Routes — including the combined top-routes cache —
cache for 15 minutes, commodities for 12 hours, terminals for 7 days and
contracts for 24 hours; the last good data is kept when a source is unreachable.

HAULING only makes read-only HTTP requests and reads local JSON. The workflow,
combat and blueprint/inventory trackers additionally tail Star Citizen's own
`Game.log` read-only to detect pilot-seat, location, combat, blueprint and item
events. They do **not** read game memory, inject code, synthesise input, or
modify the game in any way.

## UEX

`UEX` searches the player marketplace at <https://uexcorp.space/marketplace/>
for items other players are selling.

- Type in the search box to filter by item name (or seller). Matching is
  case-insensitive and instant: the board is fetched once and filtered locally,
  so typing does not hit the network.
- **SELL / BUY** chips switch between ads offering an item and ads looking for
  one; the header counts both.
- Rows show item, price in aUEC, quantity, seller and age, newest first.
  Expired, sold and inactive ads are dropped when the board is read — the site
  keeps a lot of them visible.
- Double-click a row to open the marketplace in your browser.
- `↻` re-syncs. The board is cached for five minutes in
  `%APPDATA%\Citizen Assistant\marketplace_cache.json`, so reopening the tab is
  instant and an offline start still shows the previous board.

The public API has used more than one collection name for this data, so
`app/marketplace.py` asks `marketplace`, `marketplace_ads` and
`marketplace_listings` and keeps **whichever returns the most rows**, so a small
response cannot shadow the full board. The footer reports
`N SHOWN · N LIVE · N HIDDEN`, which separates "the API sent a short page" from
"the inactive filter removed rows"; hover the status chip to see which endpoint
was used.

If UEX renames the collection, pin it without touching the code:

```json
"uex_marketplace_endpoint": "marketplace_ads"
```

in `%APPDATA%\Citizen Assistant\config.json`. Field renames are still handled by
the alias tables at the top of that file.

## INVENTORY

`INVENTORY` is a manual stash notebook, not a live game-inventory reader. Use it
to remember loot or supplies you left at another station so you can find them
again later.

Add a note with:

- **Station** — required; start typing and the dropdown searches the bundled
  UEX station snapshot. A few letters are enough (for example `sera` finds
  `Seraphim Station`). You can still type a location that is not in the list.
- **Item or loot** — required; what is there.
- **Qty** — optional free text, for example `2`, `12 SCU` or `a crate`.
- **Note** — optional; container, locker, hangar bay, etc.

Click any note to edit it. Click the `✕` on a row once to arm deletion and again
to confirm. Search filters across item, location and note. **RECENT** sorts newest
first; **LOCATION** groups notes by where they are stored.

Everything is stored locally in `%APPDATA%\Citizen Assistant\inventory.json` (or
`~/Library/Application Support/Citizen Assistant/inventory.json` on macOS). Nothing is
sent anywhere. The panel can take keyboard focus, so F3/F4 still work as
application shortcuts while you type.

## Settings window

`⚙` on the nav bar opens:

- **SUPPORT** — "Like my overlay? Buy me a coffee (or a ship)!" with a BTC
  address and copy button.
- **APPEARANCE** — UI scale (75–150% in 5% steps) and opacity slider
  (30–100%) for the content panels (mining, ore finder, HAULING, UEX, INVENTORY,
  COMBAT and NOTES); the nav bar and settings window stay fully visible while you
  adjust opacity. The **VISIBLE/HIDDEN** button beside *Shown in screenshots and
  streams* controls whether the windows appear in screenshots, OBS and screen
  shares (stored as `capture_exclude`).
  The scale is applied by the overlay itself (`app/ui/theme.py`) rather than by
  Qt's `QT_SCALE_FACTOR`, because that variable only changes widget geometry on
  Windows/Linux — on macOS it was ignored, which is why the slider used to do
  nothing there. Widgets are built once, so a new scale needs a fresh process:
  the hint under the slider turns amber and the **RESTART** button relaunches the
  overlay in place (positions and settings are saved first). At the default 100%
  nothing is scaled and the layout is pixel-identical to earlier builds.
- **Signature capture region** — re-calibrate.
- **PAUSE / RESUME** — stop OCR without closing anything.
- **SHOW PREVIEW** — show the raw pixels being read; useful for checking the box.
- **VOICE** — enable/disable spoken announcements and set their volume (see *Voice* above).
- **RELOAD** — re-read `app/data/signatures.json` without restarting.
- **UPDATE** — last section in the window: the installed build and a **CHECK**
  button. Checking reads `app/version.py` from GitHub and compares it with this
  copy; when something newer exists the button becomes **UPDATE**, which
  downloads the `main` branch archive and copies `app/`, `tools/`, the launchers
  and the requirements over this folder, then restarts the overlay. Settings,
  notes and logs live in `%APPDATA%\Citizen Assistant` and are never touched, and
  `opencode.jsonc` is deliberately left alone. It does not work for the prebuilt
  EXE — a running `.exe` cannot replace itself, so that build is updated by
  downloading a new copy.

Advanced scan tuning lives in `config.json`: `poll_ms` controls the capture
cadence (default `125`), `fast_single_read` enables the high-confidence
single-read lock, and `fast_confidence` sets its confidence threshold (default
`0.97`). Set `fast_single_read` to `false` to always require two reads.

Settings are saved to `%APPDATA%\Citizen Assistant\config.json`, next to the ore-location
cache (`where_to_mine.json`), inventory notes (`inventory.json`), the planner
route cache (`routes_cache.json`) and the workflow caches (`active_run.json`,
`workflow_wallet.json`). Panel positions (mining, ore finder, HAULING, UEX,
INVENTORY, COMBAT, NOTES) and the nav bar's visibility are also stored in
`config.json` when the app quits normally, so they reopen where you left them.

> If the venv was built from the Microsoft Store build of Python, Windows
> redirects those writes into the app package container, so the files live under
> `%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.13_*\LocalCache\Roaming\Citizen Assistant\`.
> A python.org install uses the plain `%APPDATA%` path.

## Signature data

`app/data/signatures.json` holds the mineral table (game 4.10.2, source:
<https://scminer.rocks/data/signature-scanner>). Each entry is a base signature:

```json
{ "name": "Borase", "rarity": 4, "base": 3570,
  "secondary": "Gold / Bexalite 5-10%" }
```

The loader expands each base into `base × count` for the 1-10 rock cluster
sizes shown by the SCMINER scanner. `max_cluster` in the JSON controls that
range, so adding an ore is a one-line edit. ROC and FPS deposits use their own
base values and a separate 1-8 range (`ground.max_cluster`).

## Anti-cheat

Star Citizen runs Easy Anti-Cheat. This overlay only:

- captures pixels from the desktop with `mss`,
- runs a local OCR model on the cropped image,
- draws its own always-on-top, click-through-ish windows,
- reads and writes its own local JSON notes for the inventory panel,
- reads and writes its own planner and workflow caches,
- tails Star Citizen's `Game.log` read-only for workflow, refinery, combat and
  blueprint/inventory events,
- reads and writes its own local refinery timer registry,
- reads and writes its own local `unlocked_blueprints.json` unlock registry,
- makes read-only UEX HTTP requests for hauling, planner and station data.

It never opens a handle to the game, reads its memory, injects a DLL, or
synthesizes input into the game. That keeps it on the safe side of EAC, but any
third-party overlay is still your own risk to take.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Nothing ever reads | Re-calibrate; the box must contain only the digits. Check the preview. |
| Reads the wrong number | Box too large or too small, or the HUD is scaled differently — re-calibrate. |
| Ore finder list is empty | No internet, or scminer.rocks is down. Press `↻`; the cache is used when available. |
| Ore finder shows `OFFLINE` | Same as above — the data on screen is the last successful sync. |
| Combat tracker shows no events | Confirm `Game.log` is being written, or set `SC_GAME_LOG` to its path. Combat log formats are patch-dependent; update the parser patterns if a patch changes them. |
| NOTES says `NOT SAVED` | The footer shows the reason (normally a permissions problem on `%APPDATA%`). The text is still in the editor — copy it out before closing. A hard crash can lose the last 700 ms of typing, which is the autosave delay. |
| Blueprint tracker misses an unlock | Confirm the log contains `<SHUDEvent_OnNotification> Added notification "Received Blueprint: ...`; set `SC_GAME_LOG` to the right `Game.log`. If a patch changes the wording, pass custom `BlueprintLogParser` patterns. Delete `unlocked_blueprints.json` only if you want to reset new-vs-duplicate detection. |
| Bar/scanner hidden behind a browser | It re-asserts topmost every 2 s; if it still loses, run the game borderless, not exclusive fullscreen. |
| F3/F4 do nothing while the game is focused | Fixed in v1.2.1 by polling `GetAsyncKeyState` alongside `RegisterHotKey`, because exclusive fullscreen stops `WM_HOTKEY` reaching a background thread. If it still fails, run the game borderless. |
| Bar appears twice | Two copies are running. Kill the extra `python`/`pythonw` process. |
| Start menu opens an old version | Run `install.bat` from the new folder — it repoints the shortcuts and deletes stale ones. Then compare the build stamp in `⚙`. |
| `⚙` shows no build stamp or no screenshots button | You are running a copy from before those features. Quit it from the tray, then start `run.bat` inside the folder you downloaded. |
| COMBAT does nothing and no panel appears | Usually an older build without the on-screen clamp. Check the build stamp before changing anything else. |
| UEX shows no ads | Press `↻`. If the status stays `OFFLINE`, the public API is not serving the marketplace at that path — check the endpoint names at the top of `app/marketplace.py` against the current UEX API docs. |
| Scanner shows `UNKNOWN` | The number is not in the table, or OCR dropped a digit. Check the raw value shown. |
| Nothing appears in screenshots | Open `⚙` and set the screenshots button to **VISIBLE**. If it already says VISIBLE, your build predates the toggle: run with `--capture-visible`. |
| Scanner stops reading after making screenshots visible | A panel now overlaps the calibrated scan box, so the scanner captures the overlay instead of the HUD. Move that panel away from the box, or set the screenshots button back to **HIDDEN** while mining. |
| UI scale slider seems to do nothing | It needs a restart: press **RESTART** next to the slider (or quit and start again). The hint turns amber while the chosen scale is not active yet. |
| `run.bat` says Python was not found | Install Python 3.11+ (64-bit) and tick “Add python.exe to PATH”, or run `setup.bat` once to create `C:\scov`. |
| A black console flashes when a refinery order completes | Fixed: the speech/WAV helpers start PowerShell with `CREATE_NO_WINDOW`. If you see it, you are running an older copy. |
| Scanner stays on `READING` and never locks | OCR returned no text: confirm the crop in **SHOW PREVIEW** contains only the digits, then re-calibrate. `run-debug.bat` prints the scan error; if it reports a missing module, reinstall the OCR deps with `pip install -r requirements.txt`. |

## Development

```bat
C:\scov\Scripts\python.exe -m tools.selftest     REM OCR + lookup regression checks
C:\scov\Scripts\python.exe -m tools.workflow_selftest REM workflow + Game.log checks
C:\scov\Scripts\python.exe -m tools.refinery_selftest REM refinery parser + timers
C:\scov\Scripts\python.exe -m tools.combat_selftest REM combat parser + tracker + queue
C:\scov\Scripts\python.exe -m tools.blueprint_selftest REM blueprint parser + registry + queue
C:\scov\Scripts\python.exe -m tools.preview      REM render the windows to tools\preview_out
C:\scov\Scripts\python.exe -m tools.preview --scale=1.25   REM same, at a UI scale
C:\scov\Scripts\python.exe -m app --demo         REM fake 7,200 signature, no game needed
C:\scov\Scripts\python.exe -m app --demo --open hauling      REM start with a tool open
C:\scov\Scripts\python.exe -m app --demo --open inventory   REM stash notes panel
C:\scov\Scripts\python.exe -m app --demo --open orefinder  REM (mining / orefinder / hauling / inventory / settings)
C:\scov\Scripts\python.exe -m tools.signage 10710 REM draw a signature on screen for live tests
```

`--capture-visible` disables capture exclusion so the overlay shows up in
screenshots and screen shares. If your venv is `.venv` instead of `C:\scov`,
substitute `.venv\Scripts\python.exe` in the commands above.

On macOS use `./run.command --demo`, `./run.command --demo --open hauling`,
`.venv/bin/python -m tools.selftest`,
`.venv/bin/python -m tools.workflow_selftest`,
`.venv/bin/python -m tools.refinery_selftest`,
`.venv/bin/python -m tools.combat_selftest` and
`.venv/bin/python -m tools.blueprint_selftest`; there is no `pythonw`, so the
console stays attached.
