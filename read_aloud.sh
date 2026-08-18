#!/usr/bin/env bash
# Linux counterpart to read_aloud.ahk (AutoHotkey is Windows-only).
#
# Wayland does not let a background process inject Ctrl+C into another app:
# there is no XTEST, and mutter does not implement the virtual-keyboard
# protocol that wtype needs. So unlike the AHK version this never copies --
# it reads the PRIMARY selection, which the compositor fills whenever text is
# selected. The clipboard is never touched, so there is nothing to save and
# restore, and none of the AHK KeyWait/Sleep timing dance is needed.
#
# Usage:  read_aloud.sh [selection|clipboard|stop]
# Wire to GNOME Settings > Keyboard > View and Customize > Custom Shortcuts.

set -uo pipefail

SERVER="${KOKORO_SERVER:-http://127.0.0.1:5111}"

note() {
  if command -v notify-send >/dev/null; then
    notify-send -a 'Kokoro read aloud' "$1" "${2:-}"
  else
    printf '%s %s\n' "$1" "${2:-}" >&2
  fi
}

case "${1:-selection}" in
  stop)
    curl -sf -m 2 -X POST "$SERVER/stop" >/dev/null || note 'Kokoro' 'server not responding'
    exit 0
    ;;
  selection) text=$(wl-paste --primary --no-newline 2>/dev/null || true) ;;
  clipboard) text=$(wl-paste           --no-newline 2>/dev/null || true) ;;
  *) note 'Kokoro' "unknown mode: $1"; exit 2 ;;
esac

# Whitespace-only counts as empty: a stray click clears PRIMARY to a blank.
if [[ -z ${text//[[:space:]]/} ]]; then
  note 'Kokoro' 'nothing selected'
  exit 0
fi

# jq -Rs handles the JSON escaping that hand-rolled quoting always gets wrong
# on text containing quotes, newlines or backslashes.
if ! printf '%s' "$text" | jq -Rs '{text: .}' |
     curl -sf -m 10 -X POST "$SERVER/speak" \
          -H 'Content-Type: application/json' --data-binary @- >/dev/null; then
  note 'Kokoro' 'server not running (start tts_server.py)'
  exit 1
fi
