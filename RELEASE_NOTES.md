# Citizen Assistant

A Star Citizen overlay: mining scanner, hauling and inventory helpers, a combat
log tracker, and a blueprint browser with a crafting calculator.

## Blueprints (new in this build)

- Browse every blueprint a mission can award, searchable by artifact, mission,
  material or reputation.
- Click one for its recipe: each part's material, quantity and a quality slider
  from stock **500** to perfect **1000**, with a box for exact values.
- Every part shows what that material does on its own; the **STAT / BASE /
  CRAFTED** table below is the combined result. Quality modifiers multiply
  across parts, and power pips stay whole numbers.
- **Tier** buttons and material pickers appear when a blueprint has them.
- The header tells you how to earn it: mission, faction, rank and reputation,
  and the outposts or stations where the mission is offered.

## Data

- Generated from SCMDB game data, patch `4.10.2-live.12913470`.
- Recipes for variants no pool awards are excluded, so everything listed can
  actually be earned. Variants that are awarded, such as `Artimex Core Canuto`,
  stay.
- `BASE` is blank because no public dataset carries base damage or armour; the
  calculator reports relative changes, not a predicted item card.

## Install

**EXE** — download `CitizenAssistant-windows-x64.zip`, unzip it, and run
`CitizenAssistant.exe`. Keep the folder together. The build is unsigned, so
SmartScreen warns on first run: *More info → Run anyway*. The EXE cannot replace
its own files, so a new build means downloading again.

**Updating in place** — the `install.bat` route updates from
`⚙` → **CHECK** → **UPDATE**, and keeps your settings and data.

Requires Windows 10/11 and Star Citizen running borderless or windowed
fullscreen. **F3** shows the nav bar.
