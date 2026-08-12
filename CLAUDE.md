# Kokoro read-aloud — project instructions

Local hotkey-driven TTS read-aloud system on Windows 11. Select text →
Ctrl+Alt+R speaks it (Ctrl+Alt+T = clipboard as-is, Ctrl+Alt+S = stop), with
Speechify-style in-place word highlighting.

## Read first, always

**`AUDIT.md` is the source of truth.** Read it before proposing or changing
anything. In particular:

- **§4 Measured facts** — measured numbers, but **measured on the ORIGINAL
  desktop (i5-12400F), which is no longer the machine.** The hardware changed
  ~2026-08-09 to an i7-11370H laptop + GTX 1650, and the throughput,
  synthesis-cost, START and thread-count rows do NOT transfer — see the
  2026-08-11 entry in §8 for the current values. Rows about *Kokoro's
  behaviour* (silence padding, markdown inertness, cut lengthening, per-word
  timestamps) are model-level and still hold. Every unmeasured estimate made
  in past sessions was wrong, always optimistically.
- **§6 Rejected options** — do not re-propose Piper, kokoro-onnx,
  MODEL_SPEED > 1.3, torch thread tuning, or in-place terminal highlighting
  (proven impossible). **GPU is no longer on that list** — it was adopted
  2026-08-11 and measured at 25.03x RT vs 1.77x on this laptop's CPU.
- **Speed is bounded by hardware, not taste.** Playback drains
  `PLAYBACK_SPEED` audio-seconds per second; synthesis produces `rt`
  (`GET /config → measured_rt`). Reads stall unless `rt > PLAYBACK_SPEED`.
  If the user reports stalling, check that ratio before anything else.
- **Measure, don't estimate.** If a claim isn't in §4, instrument and measure
  it before acting on it.

`plan.md` (2026-07-21) is the current work item: diagnosis + phased fix plan
for the highlighting system.

## Files

| Path | What |
|---|---|
| `tts_server.py` | Flask server on 127.0.0.1:5111; model resident; config block holds the DEFAULTS, `settings.json` overrides them at boot |
| `read_aloud.ahk` | Hotkeys (AutoHotkey **v2**); window-aware clipboard/copy logic |
| `highlighter.py` | In-place word highlighter (UIA TextPattern + layered window) |
| `tray.py` | Tray icon + settings panel over `/config` (right-click: Settings, Stop, restarts, Quit) |
| `settings.json` | User's tuned voice/speed/pause. **Written by the tray, loaded by the server at startup** |
| `calibration.json` | Measured `density`/`rt` for THIS machine, so a boot starts calibrated. Delete it to re-learn |
| `extension/` | Chromium in-page highlighter (load unpacked); Firefox works via UIA instead |
| `overlay.py` | Retired caption strip (kept, not autostarted) |
| `start_tts.vbs` | Autostart: server + AHK + highlighter + tray, hidden; server output → `server.log` |
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

On per-token lines, `found=0` (couldn't locate the token —
anchoring/alignment) and `found=1 rects=[]` (located, but the app exposes no
geometry — viewport effect) are **different failures**; don't lump them.
`cand[] … who=` names the app that supplied each candidate TextPattern.

**`painted=` in `SUMMARY` is not a correctness measure** — it records that a
rect was drawn, never that it was drawn on the right word. A read can log
`painted=62%` while highlighting arbitrary words (AUDIT §8, 2026-08-12).
Judge by `mode=`, `WRONG`-free tracking and `rewinds=`. `MODE …` is logged
once per anchor and says which locating primitive this surface honours:
`findtext` (Gecko/Win32) or `offset` (Chromium — Obsidian, VS Code, where
UIA `FindText` returns ranges at the wrong position). `.md` reads happen in
**Obsidian and VS Code**, both Chromium, so they should log `mode=offset`.

## Key endpoints

- `POST /speak {"text":...}`, `POST /stop`
- `GET/POST /config` — live tuning; the tray persists it to `settings.json`,
  which the server loads at startup (`voice`, `model_speed`, `playback_speed`,
  `pause`, `first_chunk_audio`, `output_device`)
- `GET/POST /devices` — output devices. **POST re-initializes PortAudio**,
  which is the only way a device plugged in after startup becomes visible
  (PortAudio enumerates once). This is the headphone-replug fix
- `GET /now` — current chunk, word timings, sounding word index, `utt` counter
- `GET /utterance` — original pre-sanitize text of the current utterance

## Environment gotchas (short list; more in AUDIT §7)

- Windows PowerShell 5.1: quote pip specs (`pip install "kokoro>=0.9.4"`),
  use `curl.exe` not `curl`, no `&&`.
- eSpeak NG does **not** need a separate install — `espeakng-loader` (via
  `misaki[en]`) ships the DLL inside the venv. Measured 2026-08-05, AUDIT §7.
- The venv must be **Python 3.12** (`py -3.12 -m venv env`). `kokoro` is
  `>=3.10,<3.13`; a 3.13+ venv fails every install with a misleading
  "No matching distribution found". AUDIT §7.
- AutoHotkey v2 is a per-user install; `assoc .ahk` reporting nothing is normal.
- Windows 11 suppresses TrayTip — AHK errors use MsgBox; don't revert.
- PortAudio is not thread-safe — all stream ops go through `audio_lock` in the
  server; don't bypass it.

## Conventions

- User-facing behavior decisions (start latency vs flow, overlay vs in-place)
  are settled in AUDIT §9 — reopen only with new information.
- After any deployed change, update `AUDIT.md` so the next session inherits
  the truth.
