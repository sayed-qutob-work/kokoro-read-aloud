#!/usr/bin/env bash
# Install the "Kokoro Settings" launcher into the user's app grid.
#
# GNOME has no notification-area tray (an icon there needs the third-party
# AppIndicator extension AND PyGObject, which Fedora builds only for the
# system Python while this venv must be 3.12), so the settings panel is
# reached from the app grid and the command line instead of a tray icon.
#
# Per-user, no root needed. Re-run after moving the folder: the paths are
# baked in at install time from wherever this script lives.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DEST="$APPS/kokoro-settings.desktop"

if [[ ! -x "$ROOT/env/bin/python" ]]; then
  echo "no venv at $ROOT/env -- create it first (see README)" >&2
  exit 1
fi

mkdir -p "$APPS"
sed "s|@ROOT@|$ROOT|g" "$ROOT/linux/kokoro-settings.desktop.in" > "$DEST"
chmod 644 "$DEST"

# harmless if absent; GNOME picks the file up either way
command -v update-desktop-database >/dev/null &&
  update-desktop-database "$APPS" 2>/dev/null || true

echo "installed $DEST"
echo
echo "  app grid : search for \"Kokoro Settings\""
echo "  terminal : $ROOT/env/bin/python $ROOT/tray.py --settings"
