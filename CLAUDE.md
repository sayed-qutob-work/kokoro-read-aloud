# Kokoro read-aloud — project instructions

Local hotkey-driven TTS read-aloud system on Windows 11. Select text →
Ctrl+Alt+R speaks it (Ctrl+Alt+T = clipboard as-is, Ctrl+Alt+S = stop), with
Speechify-style in-place word highlighting.

## Read first, always

**`docs/AUDIT.md` is the source of truth.** Read it before proposing or changing
anything. In particular:

- **§4 Measured facts** — measured numbers, but **measured on the ORIGINAL
  desktop's CPU under Windows (i5-12400F, CPU build of torch)**, a setup no
  longer in use. Its throughput, synthesis-cost, START and thread-count rows
  do NOT transfer. Each machine since has its own values in §8: the
  i7-11370H + GTX 1650 laptop in 2026-08-11 (Windows) and 2026-08-18
  (Fedora), and the i5-12400F + RTX 4060 Ti desktop running Omarchy in
  2026-10-06/07 (GPU, and CPU mode); the AUDIT header lists all three. Rows
  about *Kokoro's behaviour* (silence padding, markdown inertness, cut
  lengthening, per-word timestamps) are model-level and still hold. Every
  unmeasured estimate made in past sessions was wrong, always optimistically.
- **§6 Rejected options** — do not re-propose Piper, kokoro-onnx,
  MODEL_SPEED > 1.3, torch thread tuning, or in-place terminal highlighting
  (proven impossible). **GPU is no longer on that list** — it was adopted
  2026-08-11 and measured at 25.03x RT vs 1.77x on the GTX 1650 laptop's CPU.
- **Speed is bounded by hardware, not taste.** Playback drains
  `PLAYBACK_SPEED` audio-seconds per second; synthesis produces `rt`
  (`GET /config → measured_rt`). Reads stall unless `rt > PLAYBACK_SPEED`.
  If the user reports stalling, check that ratio before anything else.
- **Measure, don't estimate.** If a claim isn't in §4, instrument and measure
  it before acting on it.

`docs/plan.md` (2026-07-21) is the highlighter work item: diagnosis + phased
fix plan. `docs/RELEASE_PLAN.md` (2026-08-14) is the current work item: the
phased plan for the Windows beta, the caption strip, and the Linux (Fedora)
port. Phase 1 (release hygiene) shipped as `v0.1.0-beta`.

## Files

| Path | What |
|---|---|
| `tts_server.py` | Flask server on 127.0.0.1:5111; model resident; config block holds the DEFAULTS, `settings.json` overrides them at boot |
| `read_aloud.ahk` | Hotkeys (AutoHotkey **v2**); window-aware clipboard/copy logic |
| `read_aloud.sh` | The Linux hotkey handler (GNOME custom shortcuts, or Hyprland binds written by `linux/install-hyprland.sh` → `selection`/`clipboard`/`stop`). **On Hyprland reads go `via=wl`**: no python-xlib there, and none needed, because Hyprland implements data-control, so wl-paste takes no focus (AUDIT 2026-10-06). Reads the PRIMARY selection — it never copies, Wayland forbids injecting Ctrl+C. **Reads go through Xwayland (`linux/xselection.py`, system `/usr/bin/python3` + python-xlib), not wl-paste**: since mutter 50.5 (2026-09-30), wl-paste is refused the focus it needs, which gives "wl-clipboard is ready" notifications, multi-second stalls and stale text. wl-paste is only the fallback when X is unavailable (exit 2); log lines carry `via=x11|wl` (AUDIT 2026-09-30). A **mouse button is a second trigger route**: input-remapper maps the G Pro Wireless side button to a tap/hold macro (tap = back, hold = drag-select then Ctrl+Alt+R) — config lives outside the repo in `~/.config/input-remapper-2/`, and the right-side button is physically the *same* code as left-back, so it cannot be split (AUDIT 2026-08-19). The mouse enumerates under **two** evdev names — `Logitech G Pro ` (trailing space; Lightspeed receiver) and `Logitech G Pro Wireless Gaming Mouse` (charging cable plugged in) — and **both** need an `autoload` entry in `config.json`, or the button silently dies whenever the mouse is plugged in to charge (AUDIT 2026-09-16). Its **settle loop is load-bearing**: `wl-paste` steals focus to read PRIMARY, which races VTE's on-release claim and made the first terminal read speak the previous selection (AUDIT 2026-08-19). Diagnostics in `read_aloud.log` |
| `highlighter.py` | In-place word highlighter (UIA TextPattern + layered window) |
| `tray.py` | Settings panel over `/config` + process control. Platform-neutral since 2026-08-18; `--settings` opens the panel directly. On Linux it runs on the **system Python** (`/usr/bin/python3`, `paths.GUI_PYTHON`), not the venv: a prebuilt 3.12 bundles a Tk with one bitmap font (AUDIT 2026-10-17). Its voices, speed rules and `settings.json` load/save are in `kokoro_settings.py` |
| `kokoro_settings.py` | The reading settings and their rules, **shared by the Tk panel and the Omarchy widget** so the two cannot disagree: all 28 voices with their VOICES.md grades (★ = B- or better: af_heart, af_bella, af_nicole, bf_emma; "best male" = the top male grade, C+: am_michael, am_fenrir, am_puck), `DEFAULTS`, `load_settings`/`save_settings`, `split` (effective speed → model + playback), `sustain_ceiling`/`sustain_level`. Stdlib only, no Tk: kokoroctl imports it on the system Python (AUDIT 2026-10-18) |
| `tray_win32.py` | The Windows notification-area icon (Shell_NotifyIcon), split out of `tray.py` so it imports on Linux. Talks only to the `app` object, never to the server |
| `linux/` | `install-desktop.sh` (app-grid launcher — **there is no tray icon on GNOME**, see AUDIT 2026-08-18), the `.desktop` template and the icon; `install-systemd.sh` + the two `*.service.in` templates (autostart — `kokoro-server` enabled, `kokoro-overlay` opt-in, AUDIT 2026-08-19); `install-hyprland.sh` + `kokoro-hyprland.lua.in` (Hyprland binds + settings-panel window rule → `~/.config/hypr/kokoro.lua`, loaded by one `dofile` line in `hyprland.lua`, AUDIT 2026-10-06). Restarting the server on Linux means `systemctl --user restart kokoro-server`, not a bare `python tts_server.py` |
| `linux/kokoroctl` | Stdlib CLI (system python3): `status` JSON (with `installed` and `setup` progress), `gpu`/`cpu`/`off`/`restart`, `stop`, `clipboard`, `captions`, `settings`, `log`, `setup`, `uninstall`, and for the widget's settings rows `settings --json`, `set voice=NAME speed=X [--no-save]` (live via `/config`, saved via `kokoro_settings`; `--no-save` = slider still dragging) and `sample`. **Freeing VRAM = ending the process** (a CUDA context lives as long as it does). CPU mode = a systemd drop-in in `$XDG_RUNTIME_DIR` setting `CUDA_VISIBLE_DEVICES=` (empty), so it ends at logout. A hotkey while off notifies and does **not** auto-start the model, so a game keeps its VRAM (AUDIT 2026-10-07) |
| `linux/omarchy/` | Omarchy bar widget (Quickshell plugin `io.github.sayed-qutob-work.kokoro-read-aloud`): icon + panel, all actions via `kokoroctl`, `/now` polled directly by XHR. **The repo root is the plugin**: `manifest.json` at the root, entry point `linux/omarchy/Panel.qml`, so users install with `omarchy plugin add <repo-url>`. The shell loads it by `file://` URL from the plugin folder, and `Service.qml` finds `kokoroctl` with `Qt.resolvedUrl("../kokoroctl")`. **While `kokoroctl status` says `installed: false` the panel shows a first-run view**: a button per build (GPU build ~4 GB as the default, CPU build ~0.7 GB; only the CPU one without NVIDIA), the progress of `setup --background --cuda|--cpu`, and on failure the error plus Copy install command. IPC `setup` / `setupCpu` run the same as the buttons. **Voice and speed are edited in the panel** through `kokoroctl set` (IPC `voice`/`speed`/`sample` do the same): the QML only draws and calls, the rules stay in Python. The Output line shows the system default, which **follows Omarchy's audio panel even mid-read** (measured, AUDIT 2026-10-18); a specific device stays in the Tk panel ("More settings"). **`status` reports `build` (`cuda`/`cpu`, read from torch's `version.py`)**; on the CPU build the GPU mode is greyed out and `kokoroctl gpu` refuses, because that torch cannot use the GPU. `linux/install-omarchy.sh` is now a **developer** deploy: it copies the working tree's files into `~/.config/omarchy/plugins/<id>/` (the shell rejects symlinks, so no venv there), not a git checkout; re-run after editing the QML. Setup started from the widget runs from the plugin folder, so it points the units and hotkeys there; run `linux/kokoroctl setup` from the repo to point them back. **The shell's hot-reload keeps the old compiled QML** (Quickshell 0.3.1 has no `Qt.clearComponentCache`), so a re-install restarts the shell. Never judge a QML edit before that restart (AUDIT 2026-10-07). QML errors: `qs log -p $OMARCHY_PATH/shell` |
| `paths.py` | **Where every runtime file goes.** Windows: next to the code, as before. Linux: `~/.config/kokoro-read-aloud/` (`settings.json`), `~/.local/state/kokoro-read-aloud/` (logs, `calibration.json`) and `~/.local/share/kokoro-read-aloud/` (`paths.DATA`: model files), **never the code folder**: on Omarchy it becomes the plugin folder, which the shell watches, and any write inside it reloads every plugin. Each entry point calls `paths.prepare()` (creates the folders, moves old files over once) and sets `sys.dont_write_bytecode` before importing local modules, since `__pycache__` counts as a write. `read_aloud.sh` repeats the state path in bash (AUDIT 2026-10-17) |
| `model_files.py` | The model files, **pinned**: revision `f3ff357`, `config.json`, `kokoro-v1_0.pth` and the 28 English voices, each with size and SHA-256. `python3 model_files.py fetch|verify|where`; stdlib only, so setup can run it before the venv exists. A file only gets its real name after its hash matched. When all are present (`complete()`, sizes only) the server loads the model and voices **by path with `HF_HUB_OFFLINE=1`** and makes no network connection. Without them it falls back to the Hugging Face cache, which checks online at each start (AUDIT 2026-10-17) |
| `settings.json` | User's tuned voice/speed/pause. **Written by the tray, loaded by the server at startup.** On Linux it is `~/.config/kokoro-read-aloud/settings.json` (`paths.py`). Untracked since v0.1.0-beta — it is per-user, not a default. `settings.example.json` documents the shape. The five `caption_*` keys (`caption_style`, `caption_layout`, `caption_scroll`, `caption_position`, `caption_monitor`) are the exception: read by `overlay.py` at startup, never by the server, so changing them needs the strip restarted |
| `requirements.txt` | CPU install for pip (Windows). `requirements-cuda.txt` is the NVIDIA one. **Install one or the other, not both**. **Generated from `uv.lock` by `tools/export-requirements.sh`, never edited by hand**: every package pinned with its SHA-256 (pip hash-checking mode), torch from PyTorch's own index (`+cpu`, `+cu126`), markers make both files valid on Linux and Windows. Re-run the script after any `uv.lock` change. `--index-url` must come before `--extra-index-url`: in a requirements file it replaces the list (AUDIT 2026-10-17) |
| `pyproject.toml`, `uv.lock` | The server's environment for uv: `uv sync --extra cuda` or `--extra cpu`, **always one** (a bare `uv sync` installs an untested PyPI torch). The lock reproduces the measured `env/` exactly, 115/115 packages, via `constraint-dependencies`; torch comes from PyTorch's own indexes. The venv lives outside the code folder (`UV_PROJECT_ENVIRONMENT`), never `.venv` (AUDIT 2026-10-17) |
| `docs/` | `AUDIT.md`, `plan.md`, `RELEASE_PLAN.md` — moved out of the root in v0.1.0-beta |
| `calibration.json` | On Linux in `~/.local/state/kokoro-read-aloud/`. Measured `density`/`rt` for THIS machine, so a boot starts calibrated, kept **per device** (`by_device.cuda`/`.cpu`) since 2026-10-07. Delete it to re-learn |
| `extension/` | Chromium in-page highlighter (load unpacked); Firefox works via UIA instead |
| `overlay.py` | Caption strip: polls `/now`, renders the sentence being read. On Linux it runs on the system Python, like `tray.py`. `teleprompter`/`rows` layouts, three themes, continuous scrolling. The only position indicator on Linux (no UIA there). **Not autostarted on either platform** — start it by hand; the tray restarts it when a `caption_*` setting changes |
| `start_tts.vbs` | Autostart: server + AHK + highlighter + tray, hidden; server output → `server.log` |
| `server.log` | **Read this first on any failure.** Truncated at each server start. On Linux it is `~/.local/state/kokoro-read-aloud/server.log` (`kokoroctl log` opens it), with `overlay.log`/`overlay.err` and `tray.log` beside it |
| `read_aloud.log` | `~/.local/state/kokoro-read-aloud/read_aloud.log`. Per-try PRIMARY reads from `read_aloud.sh` (`sha`/`blank`/`stale` + excerpt). Read it before re-diagnosing a wrong/empty selection; truncated past 256KB |
| `highlighter.log` | Highlighter diagnostics (on by default). Rotated to `.log.1` at each start |
| `highlighter.err` | Highlighter stderr; an import/COM-init crash lands here. Empty = healthy |
| `env\` | The venv. Always `env\Scripts\python.exe -m pip`, never bare `pip`. **On Linux the server runs from `~/.local/share/kokoro-read-aloud/venv`**, made by `kokoroctl setup`; a hand-made `env/` is only the fallback (`paths.SERVER_PYTHON`) |
| `linux/kokoro_setup.py` | `kokoroctl setup [--cpu\|--cuda] [--background]`, stdlib. Five steps:<br>1. checks the system packages and prints the pacman/dnf command for what is missing;<br>2. uv: the system's if ≥ 0.12.10, else pinned 0.12.24 checked by SHA-256;<br>3. `uv python install 3.12` + `uv sync --frozen --extra <variant> --compile-bytecode`, then checks the torch build;<br>4. `model_files.fetch`;<br>5. the installers and a `~/.local/bin/kokoroctl` symlink (only replaced if it already points at a `linux/kokoroctl`; uninstall removes it), then a restart if the server was running.<br>Everything is under `~/.local/share/kokoro-read-aloud` (uv's own cache too: `UV_CACHE_DIR`, `UV_NO_CONFIG`). **Zero writes to the code folder** (inotify-checked). Progress in `setup.json` (state dir). `--background` = transient unit `kokoro-setup`, log `setup.log`. Re-running repairs. **`kokoroctl uninstall [--yes] [--purge]`** undoes it all:
units (only files that are ours), the CPU drop-in, stray processes, the launcher entry, the two Hyprland lines plus `kokoro.lua` (`hyprland.lua` backed up first), the data folder and the state folder. It keeps your settings unless `--purge`, and never touches the code folder or the shared HF cache. It asks first; without a TTY it needs `--yes` (AUDIT 2026-10-17) |

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

**Only local programs may call the server** (`_only_local_programs`, AUDIT
2026-10-18). It refuses a `Host` other than `127.0.0.1:5111`/`localhost:5111`
(DNS rebinding), any web-page `Origin` (extension origins may only GET), a
cross-site/same-site `Sec-Fetch-Site`, and any POST that is not
`application/json`. It never sends an `Access-Control-*` header. So **every
new client must POST JSON**, and **a GET must never change state**. Refusals
are logged in `server.log` as `refused (<reason>): ...`. Tests:
`tests/test_server.py` (server venv, `-B`).

- `POST /speak {"text":...}`, `POST /stop`. `/speak` returns `atoms`, the
  chunks queued: **0 means nothing was readable**. `skip_unreadable()` drops
  every script but Latin (a lone Greek letter stays), `skip_emoji()` drops
  emoji (© ® ™ stay), and `read_aloud.sh` notifies on 0 (AUDIT 2026-10-17)
- `GET/POST /config` — live tuning; the tray persists it to `settings.json`,
  which the server loads at startup (`voice`, `model_speed`, `playback_speed`,
  `pause`, `first_chunk_audio`, `output_device`). GET also reports `device`
  (`cuda`/`cpu`), `measured_rt`, and `rt_known` — false while `rt` is still
  the 2.0 boot seed, so don't present it as a measurement then
- `GET/POST /devices` — output devices. **POST re-initializes PortAudio**
  (GET only lists; `?refresh=1` is gone),
  which is the only way a device plugged in after startup becomes visible
  (PortAudio enumerates once). This is the headphone-replug fix
- `GET /now` — current chunk, word timings, sounding word index, `utt` counter
- `GET /utterance` — original pre-sanitize text of the current utterance

## Environment gotchas (short list; more in AUDIT §7)

- Windows PowerShell 5.1: quote pip specs (`pip install "kokoro>=0.9.4"`),
  use `curl.exe` not `curl`, no `&&`.
- eSpeak NG does **not** need a separate install — `espeakng-loader` (via
  `misaki[en]`) ships the DLL inside the venv. Measured 2026-08-05, AUDIT §7.
- The venv must be **Python 3.12** (`py -3.12 -m venv env`); on Linux only the
  server runs on it. `kokoro` is
  `>=3.10,<3.13`; a 3.13+ venv fails every install with a misleading
  "No matching distribution found". AUDIT §7.
- AutoHotkey v2 is a per-user install; `assoc .ahk` reporting nothing is normal.
- Windows 11 suppresses TrayTip — AHK errors use MsgBox; don't revert.
- PortAudio is not thread-safe — all stream ops go through `audio_lock` in the
  server; don't bypass it.

## Conventions

- User-facing behavior decisions (start latency vs flow, overlay vs in-place)
  are settled in AUDIT §9 — reopen only with new information.
- After any deployed change, update `docs/AUDIT.md` so the next session
  inherits the truth.
- Runtime files (logs, settings, calibration) go through `paths.py`; on Linux
  nothing may be written next to the code (AUDIT 2026-10-17).
- Paths are derived from each script's own location (`start_tts.vbs` uses
  `WScript.ScriptFullName`, the Python files use `__file__`). Nothing hardcodes
  `C:\kokoro` any more — don't reintroduce it, the repo has to work from any
  folder now that it is published.
