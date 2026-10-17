#!/usr/bin/env bash
# DEVELOPER deploy of the bar widget. Users install with
#
#   omarchy plugin add https://github.com/sayed-qutob-work/kokoro-read-aloud --enable
#
# which clones the repo into ~/.config/omarchy/plugins/<id>/ -- the repo root
# is the plugin (manifest.json), and the widget is linux/omarchy/Panel.qml.
#
# This copies the working tree's files there instead, the same layout a
# clone has, so uncommitted QML can be tried. The copy is not a git
# checkout: `omarchy plugin update` cannot update it, and `omarchy plugin
# remove` backs it up rather than deleting it. Re-run after every edit: the
# shell's hot-reload keeps old compiled QML, so this restarts the shell
# (AUDIT 2026-10-07). Per-user, no root needed.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
for cmd in omarchy omarchy-shell jq git; do
  command -v "$cmd" >/dev/null || { echo "needs $cmd -- is this Omarchy?" >&2; exit 1; }
done

ID="$(jq -r '.id // empty' "$ROOT/manifest.json")"
# DEST is rm -rf'd below, so an empty id must never reach it
[[ $ID =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "bad plugin id in $ROOT/manifest.json" >&2; exit 1; }
DEST="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/$ID"
if [[ -d $DEST/.git ]]; then
  echo "note: replacing a git checkout of the plugin with a working-tree copy" >&2
fi

# Build in a staging dir and swap it in, so the shell's file watcher never
# reloads a half-copied plugin. Tracked files plus new untracked ones, never
# ignored ones (a venv would bring symlinks, which the shell refuses).
STAGE="$(dirname "$DEST")/.$ID.new"
rm -rf -- "$STAGE"
mkdir -p "$STAGE"
(cd "$ROOT" && git ls-files -z --cached --others --exclude-standard |
   while IFS= read -r -d '' f; do [[ -e $f ]] && printf '%s\0' "$f"; done |
   xargs -0 cp -p --parents -t "$STAGE")
omarchy plugin validate "$STAGE" >/dev/null
rm -rf -- "$DEST"
mv -- "$STAGE" "$DEST"
echo "installed $DEST (working tree of $ROOT)"

if omarchy plugin list --json | jq -e --arg id "$ID" 'any(.[]; .id == $id and .enabled)' >/dev/null; then
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
