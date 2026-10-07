#!/usr/bin/env bash
# Install the Omarchy bar widget: an icon in the top bar whose panel frees
# the model's VRAM (off / CPU mode), restarts the server, toggles captions
# and shows what is being read. Everything it does goes through
# linux/kokoroctl, which works without the widget too.
#
# Copies linux/omarchy/ to ~/.config/omarchy/plugins/<id>/ (the shell
# rejects symlinked plugins) with this repo's path baked into Paths.js,
# then enables it next to the system tray. Re-run after editing the QML or
# moving the folder. Per-user, no root needed. Tested on Omarchy 4.0.4.
#
# Remove:  omarchy plugin disable io.github.sayed-qutob-work.kokoro-read-aloud
#          rm -r ~/.config/omarchy/plugins/io.github.sayed-qutob-work.kokoro-read-aloud

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/linux/omarchy"
for cmd in omarchy omarchy-shell jq; do
  command -v "$cmd" >/dev/null || { echo "needs $cmd -- is this Omarchy?" >&2; exit 1; }
done

ID="$(jq -r '.id // empty' "$SRC/manifest.json")"
# DEST is rm -rf'd below, so an empty id must never reach it
[[ $ID =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "bad plugin id in $SRC/manifest.json" >&2; exit 1; }
DEST="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/$ID"

# The widget reads status and acts through these; without the unit it still
# works, by spawning the server directly.
chmod +x "$ROOT/linux/kokoroctl"
if ! systemctl --user cat kokoro-server.service >/dev/null 2>&1; then
  echo "note: no kokoro-server systemd unit (linux/install-systemd.sh)." >&2
  echo "      The widget will start and stop tts_server.py directly." >&2
fi

# Build in a staging dir and swap it in, so the shell's file watcher never
# reloads a half-copied plugin.
STAGE="$(dirname "$DEST")/.$ID.new"
rm -rf -- "$STAGE"
mkdir -p "$STAGE"
cp "$SRC"/manifest.json "$SRC"/*.qml "$STAGE"/
sed "s|@ROOT@|$ROOT|g" "$SRC/Paths.js.in" > "$STAGE/Paths.js"
omarchy plugin validate "$STAGE" >/dev/null
rm -rf -- "$DEST"
mv -- "$STAGE" "$DEST"
echo "installed $DEST"

if omarchy plugin list --json | jq -e --arg id "$ID" 'any(.[]; .id == $id and .enabled)' >/dev/null; then
  # Already loaded: the shell's hot-reload re-creates the widget from its
  # cached compiled QML, so edits would not show until a shell restart
  # (measured 2026-10-07 on Quickshell 0.3.1; rescanPlugins is not enough).
  echo "already on the bar -- restarting the Omarchy shell to load the new code"
  omarchy restart shell >/dev/null
  exit 0
fi

omarchy-shell shell rescanPlugins >/dev/null

if omarchy plugin list --json | jq -e 'any(.[]; .id == "omarchy.tray" and .enabled)' >/dev/null; then
  omarchy plugin enable "$ID" --after omarchy.tray
else
  omarchy plugin enable "$ID" --section right --index 0
fi

echo
echo "  left click    open the panel"
echo "  right click   turn the model off (frees its VRAM) / back on"
echo "  middle click  stop reading"
echo
echo "  From a terminal or a keybind: $ROOT/linux/kokoroctl gpu|cpu|off|restart"
