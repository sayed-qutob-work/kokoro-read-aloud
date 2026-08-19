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
# THE SETTLE LOOP (2026-08-19) -- do not remove, see AUDIT.
# mutter exposes no data-control protocol, so wl-paste has to briefly STEAL
# KEYBOARD FOCUS to be handed the selection offer (verified with WAYLAND_DEBUG:
# it binds xdg_activation_v1 + gtk_shell1, and `wl-paste --watch` refuses to
# run for exactly this reason). Apps that publish PRIMARY continuously while
# you drag (browsers, Electron) are already committed by the time the hotkey
# fires. VTE terminals claim it only on button-RELEASE -- which is the same
# instant wl-paste takes focus away, so the claim is deferred and the first
# read returns the PREVIOUS selection, or nothing. Hence: re-read a few times
# until the value is non-blank and is not the text we spoke last. Costs
# nothing on a normal read (first try is accepted); only a stale/blank first
# read pays the ~460ms budget.
#
# Usage:  read_aloud.sh [selection|clipboard|stop]
# Wire to GNOME Settings > Keyboard > View and Customize > Custom Shortcuts.

set -uo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SERVER="${KOKORO_SERVER:-http://127.0.0.1:5111}"
LOG="${KOKORO_READ_LOG-$HERE/read_aloud.log}"   # KOKORO_READ_LOG= (empty) disables
STATE="${XDG_RUNTIME_DIR:-/tmp}/kokoro-read-aloud"
SETTLE_TRIES="${KOKORO_SETTLE_TRIES:-5}"        # incl. the first read
SETTLE_SLEEP="${KOKORO_SETTLE_SLEEP:-0.06}"

T0=$(date +%s%3N)
if [[ -n $LOG && -f $LOG && $(stat -c%s "$LOG" 2>/dev/null || echo 0) -gt 262144 ]]; then
  : >"$LOG"
fi
log() {
  [[ -n $LOG ]] || return 0
  printf '%s +%dms %s\n' "$(date +%H:%M:%S)" "$(( $(date +%s%3N) - T0 ))" "$*" >>"$LOG" 2>/dev/null
}

note() {
  log "NOTE $1 ${2:-}"
  if command -v notify-send >/dev/null; then
    notify-send -a 'Kokoro read aloud' "$1" "${2:-}"
  else
    printf '%s %s\n' "$1" "${2:-}" >&2
  fi
}

ERRF=$(mktemp) || ERRF=/dev/null
trap 'rm -f "$ERRF"' EXIT

GRAB_RC=0 GRAB_ERR=
grab() {  # $1 = primary|clipboard -> text on stdout, diagnostics in GRAB_RC/GRAB_ERR
  local out
  if [[ $1 == primary ]]; then out=$(wl-paste --primary --no-newline 2>"$ERRF")
  else                         out=$(wl-paste           --no-newline 2>"$ERRF"); fi
  GRAB_RC=$?
  GRAB_ERR=$(tr '\n' ';' <"$ERRF")
  printf '%s' "$out"
}

digest() { printf '%s' "$1" | sha256sum | cut -c1-12; }
blank()  { [[ -z ${1//[[:space:]]/} ]]; }
peek()   { printf '%.48s' "${1//[$'\n\t']/ }"; }   # log excerpt, one line

mode=${1:-selection}
log "RUN mode=$mode"

case "$mode" in
  stop)
    curl -sf -m 2 -X POST "$SERVER/stop" >/dev/null || note 'Kokoro' 'server not responding'
    exit 0
    ;;
  clipboard)
    # The clipboard is explicit and sticky -- no settle needed, and re-reading
    # the same clipboard on purpose is a normal thing to do.
    text=$(grab clipboard)
    log "clipboard rc=$GRAB_RC len=${#text}${GRAB_ERR:+ err=$GRAB_ERR} [$(peek "$text")]"
    ;;
  selection)
    last=''
    [[ -r $STATE/last-spoken ]] && last=$(<"$STATE/last-spoken")
    text=''
    for (( try = 0; try < SETTLE_TRIES; try++ )); do
      (( try )) && sleep "$SETTLE_SLEEP"
      text=$(grab primary)
      sha=$(digest "$text")
      stale=0; [[ $sha == "$last" ]] && stale=1
      blank "$text" && empty=1 || empty=0
      log "try=$try rc=$GRAB_RC len=${#text} sha=$sha blank=$empty stale=$stale${GRAB_ERR:+ err=$GRAB_ERR} [$(peek "$text")]"
      (( empty || stale )) || break
    done
    ;;
  *)
    note 'Kokoro' "unknown mode: $mode"; exit 2
    ;;
esac

# Whitespace-only counts as empty: a stray click clears PRIMARY to a blank.
if blank "$text"; then
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

# Remember what we spoke, so the next selection read can tell a stale PRIMARY
# (same text again) from a fresh one.
if mkdir -p "$STATE" 2>/dev/null; then
  printf '%s' "$(digest "$text")" >"$STATE/last-spoken" 2>/dev/null
fi
log "SPOKE len=${#text}"
