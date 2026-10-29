#!/bin/zsh
# Launch the Citizen Assistant UI on macOS.
#
# This is the demo build: the Windows-only capture/overlay/hotkey layer is
# stubbed out, so the panels render but there is no game to scan. Any arguments
# are passed through, e.g.  ./run.command --open hauling
cd "$(dirname "$0")" || exit 1

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

# With no arguments, start the synthetic 7,200 signature so the scanner has
# something to show without a capture region.
if [ "$#" -eq 0 ]; then
  set -- --demo
fi

exec "$PY" -m app "$@"

