# Citizen Assistant

A friendly little overlay for Star Citizen. Press **F3** and a small bar appears
with the tools you actually use — no alt-tabbing, no second monitor.

## What you get

- **Mining scanner** — reads the number on the scan HUD and tells you what the
  rock is before you crack it.
- **Ore finder** — what each ore is worth, and where to sell it.
- **Hauling** — contracts and routes, with the boring maths done for you.
- **Inventory** — what you own, kept up to date from the game log.
- **Combat** — damage, kills and soft deaths as they happen.
- **Blueprints** — every blueprint you can earn, how to unlock it, and what your
  materials will make.
- **Notes** — a scratchpad that survives a restart.

It only reads two things: Star Citizen's game log, and the little patch of screen
you point it at. No memory reading, no injection, no fake inputs.

## Blueprints

This is the big one in this build. Open the tab and you get every blueprint a
mission can actually award right now.

**Find one.** Search by item, mission, faction or material. Search a mission and
it shows up as a pool first — how many of its blueprints you already own, then
the full list. Search `cargo shipment` and you'll get the whole Citizens for
Prosperity pool, including the S71 even though an easier mission also hands it
out.

**See how to get it.** Each blueprint tells you the mission, the faction whose
reputation gates it, the rank and rep you need, and the outposts or stations
where the mission is offered. If several missions award it, the easiest one is
shown first and the rest are listed under **ALSO**.

**Plan the craft.** Every part lists its material, how much you need, and a
quality slider from stock **500** to perfect **1000**. Drag it, or type an exact
number. Each part shows what that material adds on its own, and the table
underneath gives you the finished item's stats.

A few honest notes:

- The game files don't include base damage or armour numbers, so the calculator
  shows **changes** (`Impact Force +7.5%`), not a full item card.
- Only earnable blueprints are listed. Some definitions exist in the files but
  no mission awards them yet; they stay out of the way until they do.
- New: the scanner preview in settings now sits right under the SCANNING buttons
  instead of below the volume slider.

## Install

**Windows EXE** — download `CitizenAssistant-windows-x64.zip`, unzip it, run
`CitizenAssistant.exe`, and keep the folder together. It isn't signed, so
Windows will warn the first time: choose *More info → Run anyway*. The EXE can't
update itself, so a new build means downloading again.

**Updating in place** — if you installed with `install.bat`, open `⚙` →
**CHECK** → **UPDATE**. Your settings and data stay put.

**Running from source** — Windows, macOS and Linux instructions are in the
[README](https://github.com/fishtaco7/citizen-assistant#readme).

Needs Windows 10/11 and Star Citizen in borderless or windowed fullscreen.
**F3** shows the bar.
