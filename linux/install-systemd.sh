#!/usr/bin/env bash
# Install the systemd --user units that autostart Kokoro read-aloud.
#
# Two units, deliberately not treated the same:
#   kokoro-server   -- enabled. Nothing works without it, and after a reboot
#                      the hotkeys silently do nothing until it is up.
#   kokoro-overlay  -- installed but NOT enabled. The caption strip is a
#                      deliberate opt-in (see CLAUDE.md); enable it yourself
#                      with `systemctl --user enable --now kokoro-overlay`.
#
# Per-user, no root needed. Re-run after moving the folder: the paths are
# baked in at install time from wherever this script lives.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
# where paths.py puts the logs; systemd will not create it for us
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/kokoro-read-aloud"
# the server's Python, the same choice as paths.SERVER_PYTHON
PY="${XDG_DATA_HOME:-$HOME/.local/share}/kokoro-read-aloud/venv/bin/python"
[[ -x $PY ]] || PY="$ROOT/env/bin/python"

if [[ ! -x $PY ]]; then
  echo "no Python environment for the server -- run linux/kokoroctl setup" >&2
  exit 1
fi

mkdir -p "$UNITS" "$STATE"
for u in kokoro-server kokoro-overlay; do
  sed -e "s|@ROOT@|$ROOT|g" -e "s|@STATE@|$STATE|g" -e "s|@PYTHON@|$PY|g" \
      "$ROOT/linux/$u.service.in" > "$UNITS/$u.service"
  chmod 644 "$UNITS/$u.service"
  echo "installed $UNITS/$u.service"
done

systemctl --user daemon-reload
systemctl --user enable --now kokoro-server.service

echo
echo "  server   : systemctl --user status kokoro-server"
echo "  logs     : $STATE/server.log   (truncated at each start)"
echo "  captions : systemctl --user enable --now kokoro-overlay   # opt-in"
