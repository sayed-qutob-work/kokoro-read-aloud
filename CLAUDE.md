# Kokoro read-aloud — project instructions

Local hotkey-driven TTS read-aloud system on Windows 11. Select text →
Ctrl+Alt+R speaks it (Ctrl+Alt+T = clipboard as-is, Ctrl+Alt+S = stop), with
Speechify-style in-place word highlighting.

## Read first, always

**`AUDIT.md` is the source of truth.** Read it before proposing or changing
anything. In particular:

- **§4 Measured facts** — every number there was measured on this machine.
  Do not re-guess them. Every unmeasured estimate made in past sessions was
  wrong, always optimistically.
- **§6 Rejected options** — do not re-propose Piper, kokoro-onnx,
  MODEL_SPEED > 1.3, torch thread tuning, or in-place terminal highlighting
  (proven impossible).
- **Measure, don't estimate.** If a claim isn't in §4, instrument and measure
  it before acting on it.

`plan.md` (2026-07-21) is the current work item: diagnosis + phased fix plan
for the highlighting system.

## Files

| Path | What |
|---|---|
| `tts_server.py` | Flask server on 127.0.0.1:5111; model resident; all tuning in its config block |
| `read_aloud.ahk` | Hotkeys (AutoHotkey **v2**); window-aware clipboard/copy logic |
| `highlighter.py` | In-place word highlighter (UIA TextPattern + layered window) |
| `extension/` | Chromium in-page highlighter (load unpacked); Firefox works via UIA instead |
| `overlay.py` | Retired caption strip (kept, not autostarted) |
| `start_tts.vbs` | Autostart: server + AHK + highlighter, hidden; server output → `server.log` |
| `server.log` | **Read this first on any failure.** Truncated at each server start |
| `highlighter.log` | Highlighter diagnostics (on by default). Rotated to `.log.1` at each start |
| `highlighter.err` | Highlighter stderr; an import/COM-init crash lands here. Empty = healthy |
| `env\` | The venv. Always `env\Scripts\python.exe -m pip`, never bare `pip` |

## The one discipline that matters (restarts)

Editing `tts_server.py` does **nothing** to a running server, and a second
launch dies silently ("address in use") while the old one keeps serving your
old code. Always:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*tts_server.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-NetTCPConnection -LocalPort 5111 -State Listen -ErrorAction SilentlyContinue  # must print nothing
cd C:\kokoro; env\Scripts\python.exe tts_server.py
```

Same idea for `highlighter.py`/`overlay.py`. Filter on the CommandLine, not
the image name: since 2026-07-21 the highlighter runs as a **`python.exe`**
pair (was `pythonw.exe` — swapped so stderr can be captured), `overlay.py` is
still `pythonw.exe`. Two processes per script is NORMAL — the venv launcher
spawns the real interpreter as a child.

## Debugging the highlighter

`start_tts.vbs` sets `KOKORO_HL_DEBUG=C:\kokoro\highlighter.log`, so anchor
decisions and per-token rects are logged in normal use; the previous log is
rotated to `highlighter.log.1` at each start. **Diagnose with this log; never
guess** — every guessed highlighter diagnosis in past sessions was wrong
(AUDIT §8 round 4). Key lines: `GIVEUP` (unanchored read = symptom A),
`RESUME … RC6` (mid-read state wipe = symptom B/C), `ANCHOR try#k` depth
(slow anchor = symptom C), `FETCH fail` (RC9), `POLL ERROR`/`FATAL`. A
crash before logging exists shows up in `highlighter.err`.

On per-token lines, `found=0` (FindText couldn't locate the token —
anchoring/alignment) and `found=1 rects=[]` (located, but the app exposes no
geometry — viewport effect) are **different failures**; don't lump them.
`cand[] … who=` names the app that supplied each candidate TextPattern.

## Key endpoints

- `POST /speak {"text":...}`, `POST /stop`
- `GET/POST /config` — live tuning (in-memory only; persist by editing the file)
- `GET /now` — current chunk, word timings, sounding word index, `utt` counter
- `GET /utterance` — original pre-sanitize text of the current utterance

## Environment gotchas (short list; more in AUDIT §7)

- Windows PowerShell 5.1: quote pip specs (`pip install "kokoro>=0.9.4"`),
  use `curl.exe` not `curl`, no `&&`.
- eSpeak NG must be on PATH (phonemization).
- AutoHotkey v2 is a per-user install; `assoc .ahk` reporting nothing is normal.
- Windows 11 suppresses TrayTip — AHK errors use MsgBox; don't revert.
- PortAudio is not thread-safe — all stream ops go through `audio_lock` in the
  server; don't bypass it.

## Conventions

- User-facing behavior decisions (start latency vs flow, overlay vs in-place)
  are settled in AUDIT §9 — reopen only with new information.
- After any deployed change, update `AUDIT.md` so the next session inherits
  the truth.
