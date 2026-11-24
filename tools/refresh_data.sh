#!/usr/bin/env bash
# Refresh the bundled blueprint data from SCMDB.
#
#     bash tools/refresh_data.sh            # current live build
#     CHANNEL=ptu bash tools/refresh_data.sh
#
# Downloads the merged dump and the crafting dump for the chosen channel, runs
# the importer into app/data, then audits the result. Nothing is committed; the
# script prints what changed so you can review it.
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"
DATA="${SCMDB_DIR:-$HOME/Documents/projects/scmdb}"
CHANNEL="${CHANNEL:-live}"

mkdir -p "$DATA"

echo "==> Consulting SCMDB for the latest $CHANNEL build"
curl -fsSL --retry 3 \
  https://scmdb.net/data/game-versions.json \
  -o "$DATA/game-versions.json"

VER="$("$PY" - "$DATA/game-versions.json" "$CHANNEL" <<'PY'
import json
import sys

rows = json.load(open(sys.argv[1], encoding="utf-8"))
channel = sys.argv[2].strip().casefold()
wanted = [row["version"] for row in rows
          if f"-{channel}" in str(row.get("version", "")).casefold()]
if not wanted:
    raise SystemExit(f"no {channel} build in game-versions.json")
# Newest first in the published list; keep that order.
print(wanted[0])
PY
)"
echo "    $VER"

MERGED="$DATA/merged-$VER.json"
RECIPES="$DATA/crafting_blueprints-$VER.json"

echo "==> Downloading dumps"
curl -fsSL --retry 3 -o "$MERGED" \
  "https://scmdb.net/data/merged-$VER.json"
curl -fsSL --retry 3 -o "$RECIPES" \
  "https://scmdb.net/data/crafting_blueprints-$VER.json"

# SCMDB answers unknown paths with an HTML page and a 200, so check the bytes.
for file in "$MERGED" "$RECIPES"; do
  if [ "$(head -c 1 "$file")" != "{" ]; then
    echo "!! $file is not JSON (wrong filename?)" >&2
    exit 1
  fi
done
ls -lh "$MERGED" "$RECIPES"

echo "==> Importing into app/data"
"$PY" -m tools.scmdb_import "$MERGED" \
  --recipes "$RECIPES" \
  --out app/data/blueprint_sets.json

echo "==> Auditing"
"$PY" -m tools.audit_blueprints > audit.txt 2>&1 || true
grep -E "^(Recipes|Structural|RESULT)|no recipe|no materials" audit.txt || true

echo "==> Checking for the Skarnov"
if grep -qi skarnov app/data/blueprint_crafting.json; then
  echo "    found in this build"
  grep -in '"skarnov[^"]*"' app/data/blueprint_crafting.json | head -5
else
  echo "    not in this build"
fi

echo
echo "Review the diff, then commit app/data and audit.txt."
