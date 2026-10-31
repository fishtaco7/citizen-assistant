#!/usr/bin/env bash
# Start the overlay from a source checkout on Linux.
#
#   bash run.sh                  normal start
#   bash run.sh --demo           fake signature, no game needed
#   bash run.sh --open hauling   start with a tool open
#
# X11 is recommended: global hotkeys use X11 key grabs, which Wayland does not
# allow background applications to use.
set -euo pipefail

cd "$(dirname "$0")"

PY="python3"
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
fi

exec "$PY" -m app "$@"
