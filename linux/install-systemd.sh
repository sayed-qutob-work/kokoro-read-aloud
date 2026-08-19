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

if [[ ! -x "$ROOT/env/bin/python" ]]; then
  echo "no venv at $ROOT/env -- create it first (see README)" >&2
  exit 1
fi

mkdir -p "$UNITS"
for u in kokoro-server kokoro-overlay; do
  sed "s|@ROOT@|$ROOT|g" "$ROOT/linux/$u.service.in" > "$UNITS/$u.service"
  chmod 644 "$UNITS/$u.service"
  echo "installed $UNITS/$u.service"
done

systemctl --user daemon-reload
systemctl --user enable --now kokoro-server.service

echo
echo "  server   : systemctl --user status kokoro-server"
echo "  logs     : $ROOT/server.log   (truncated at each start)"
echo "  captions : systemctl --user enable --now kokoro-overlay   # opt-in"
