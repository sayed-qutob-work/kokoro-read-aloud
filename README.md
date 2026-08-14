# Local Read-Aloud (Kokoro TTS)

Select text anywhere on Windows → **Ctrl+Alt+R** reads it aloud in a natural
voice → **Ctrl+Alt+S** stops. Fully local, no network calls at runtime, no
clipboard polling. While it reads, the word being spoken lights up **in the
original text, where it already is** — no caption strip, no copy of the text.

> **Beta**
>
> This is `v0.1.0-beta`. The audio path is solid and in daily use. The
> **highlighter is the rough part** and has known faults — see
> [Known issues](#known-issues) before filing anything. Windows only for now;
> a Linux port is planned ([docs/RELEASE_PLAN.md](docs/RELEASE_PLAN.md)).

```text
[keyboard or a spare mouse button]
        │  Ctrl+Alt+R
        ▼
  read_aloud.ahk        AutoHotkey v2: grabs the selected text (window-aware,
        │               clipboard is always restored), POSTs it over localhost
        ▼
  tts_server.py         Flask on 127.0.0.1:5111, Kokoro-82M resident in memory,
        │               budget-driven chunking + WSOLA time-stretch
        ├──────────────► [speakers]
        │
        │  GET /now, /utterance
        ├────────────► highlighter.py   Tints the spoken word, in place, in the
        │                               source app — UI Automation + a
        │                               click-through layered window. Read-only;
        │                               never touches audio.
        │  GET/POST /config
        └────────────► tray.py          Tray icon + Settings panel. Writes
                                        settings.json, which the server loads
                                        at startup.
```

For the full engineering history — measured performance facts, rejected
alternatives, and every non-obvious design decision — see
[docs/](docs/README.md).

## Hotkeys

| Keys | Action |
|---|---|
| `Ctrl+Alt+R` | Read the current selection (works in browsers, PDFs, editors, terminals — see [Word highlighting](#word-highlighting) for where the visual marker is supported) |
| `Ctrl+Alt+T` | Read the clipboard as-is |
| `Ctrl+Alt+S` | Stop |

## Word highlighting

Reading works **everywhere text can be selected.** The visual marker is a
separate concern and depends on what the app exposes to accessibility APIs. It
runs as its own process and cannot affect playback: kill it and audio continues
unchanged.

| Where | Marker | Notes |
|---|---|---|
| Firefox | ✅ | Best-supported surface; Gecko exposes real text geometry |
| Chrome / Edge | ✅ | Load `extension/` unpacked — in Chromium the extension is the reliable path, not UI Automation |
| Notepad and Win32 editors | ✅ | |
| VS Code editor | ⚠️ | Needs a setting, below |
| Obsidian (`.md`) | ⚠️ | Works since 2026-08-12; Chromium needs a different locating primitive than Gecko |
| Outlook / Hotmail on the web | ⚠️ | Intermittent, open issue (`docs/plan.md` → D2) |
| **Terminals** | ❌ | **Not possible.** Reading works; the marker cannot. See below |
| PDFs in a browser viewer, Google Docs | ❌ | Canvas-rendered; no text geometry exists to point at |

**VS Code** needs `"editor.accessibilitySupport": "on"` in your user settings,
otherwise it exposes no editor text at all and there is nothing to locate.

Optionally, to stop VS Code faintly tinting other copies of the current word
while reading markdown (its `occurrencesHighlight` feature reacting to the
cursor the highlighter has to move), scope it off for markdown only:

```json
"[markdown]": {
    "editor.occurrencesHighlight": "off",
    "editor.selectionHighlight": false
}
```

**Terminals cannot be highlighted, by design of the terminal — not a bug.**
VS Code's integrated terminal (xterm.js) draws text to a canvas and exposes it
to accessibility through a hidden buffer parked far off-screen, so the position
it reports has no relation to where the pixels are. *Reading* terminal text
works fine (see step 5 of the setup); only the visual marker is unavailable.
This is not fixable in place and will not be fixed — the planned answer is an
optional caption strip ([docs/RELEASE_PLAN.md](docs/RELEASE_PLAN.md)).

## Known issues

`v0.1.0-beta`, all in the highlighter — audio is unaffected by every one of
these:

- Multi-line selections highlight less reliably than single-line ones.
- Reading the *same* passage repeatedly can make the marker clash with itself
  or stop updating.
- Outlook / Hotmail on the web works sometimes and fails often; undiagnosed.
- The marker can occasionally land on the wrong word rather than simply not
  appearing.

Before reporting a highlighting problem, please check the table above — the
terminal, PDF-viewer and Google-Docs cases are structurally impossible, not
bugs, and `docs/AUDIT.md` §6 has the evidence.

## CPU or GPU

This matters more than it sounds. Playback drains audio at `playback_speed`
audio-seconds per second, while synthesis produces audio at some multiple of
realtime. **If synthesis is slower than playback, long reads stall** — and CPU
synthesis is right at that line.

| | Measured throughput | Against a default drain of 1.8 |
|---|---|---|
| GTX 1650 (CUDA build) | **25.0x realtime** | Vast headroom |
| Same laptop, CPU build | **1.77x realtime** | **Below break-even — stalls** |

Both measured 2026-08-11 on the same machine (`docs/AUDIT.md` §8). Your own
figure is always available live as `measured_rt` from `GET /config`; it is
learned from real reads, so it starts low after a restart and climbs.

So: install `requirements-cuda.txt` if you have an NVIDIA GPU. If you're on
CPU, expect to lower the reading speed in the tray's Settings panel until its
sustainable-speed warning clears. The tray reads the machine's actual measured
throughput and tells you the ceiling — trust it over any number in this file.

There is no device-selection code anywhere in the server; Kokoro takes the
device from whichever torch build is installed.

## Setting up on a fresh Windows machine

1. **Install prerequisites** (all free):
   - **Python 3.12 specifically** — [python.org](https://www.python.org/downloads/release/python-31210/)
     (check "Add to PATH"), or `py install 3.12` if you have the Python Install
     Manager. **3.13 and newer will not work**: `kokoro` declares
     `Requires-Python >=3.10,<3.13`, so pip silently filters out every usable
     release. See [Troubleshooting](#troubleshooting) for what that failure
     looks like. A newer Python being already installed is fine — you just must
     not build the venv from it.
   - [AutoHotkey v2](https://www.autohotkey.com/) — per-user or machine-wide,
     `start_tts.vbs` finds either. Without it the hotkeys are silently dead;
     nothing else in the system provides them.
   - **eSpeak NG is *not* a separate install** (verified 2026-08-05). The
     `espeakng-loader` package pulled in by `misaki[en]` ships `espeak-ng.dll`
     and its data inside the venv, and phonemization — including
     out-of-dictionary words, the only thing that reaches eSpeak — works with
     nothing on PATH.

2. **Clone and install** (PowerShell). Any folder works; the scripts locate
   themselves:

   ```powershell
   git clone https://github.com/sayed-qutob-work/kokoro-read-aloud.git
   cd kokoro-read-aloud
   py -3.12 -m venv env
   env\Scripts\python.exe -V          # must print Python 3.12.x — stop here if it doesn't

   # NVIDIA GPU (strongly preferred — see "CPU or GPU"):
   env\Scripts\python.exe -m pip install -r requirements-cuda.txt
   # No NVIDIA GPU:
   env\Scripts\python.exe -m pip install -r requirements.txt
   ```

   Use `py -3.12 -m venv`, not `python -m venv` — bare `python` picks whatever
   version is first on PATH, which is how you end up with a venv the pinned
   dependencies cannot install into.

   Always use `env\Scripts\python.exe -m pip`, never bare `pip` — and quote any
   version specs (`pip install "kokoro>=0.9.4"`), because `>` is a redirect in
   PowerShell.

3. **First run** (one-time downloads into the user cache: the ~330MB Kokoro-82M
   model from Hugging Face, plus a ~13MB `en_core_web_sm` spaCy model that
   `misaki` fetches by itself the first time it phonemizes — that second one
   prints its own pip-style progress and is expected):

   ```powershell
   env\Scripts\python.exe tts_server.py
   ```

   Wait for `[kokoro] ready on http://127.0.0.1:5111`, then select some text and
   press Ctrl+Alt+R (start `read_aloud.ahk` by double-clicking it first).

4. **Autostart**: Win+R → `shell:startup` → put a shortcut to `start_tts.vbs`
   there. It launches all four processes hidden — server, hotkeys, highlighter,
   tray — and logs the server to `server.log` (read that first whenever
   something misbehaves).

5. **Terminal reading** (optional): in VS Code settings, set
   `"terminal.integrated.copyOnSelection": true`. In Windows Terminal, set
   `"copyOnSelect": true`. This lets Ctrl+Alt+R work on terminal text, where
   simulating Ctrl+C is not an option (it means "interrupt" there). Terminal
   text is read aloud but not visually highlighted — see above for why.

6. **Mouse button** (optional): a macro on a spare button — on press: left-click
   down; on release: left-click up, then Ctrl+Alt+R. Drag-selecting with that
   button then reads the selection when you let go. (Tested with Logitech G Hub.)

## Settings

Right-click the tray icon → **Settings**. That panel is the intended interface:
it writes `settings.json`, which the server loads at startup, and it shows the
sustainable-speed ceiling measured on your machine.

`settings.json` is not tracked by git — it's yours.
[settings.example.json](settings.example.json) documents the shape and the
shipped defaults:

| Key | What |
|---|---|
| `voice` | e.g. `af_heart`, `am_michael`, `bf_emma` (full list in `tts_server.py`) |
| `playback_speed` | Pitch-preserving speed-up after synthesis. **This is the one that stalls reads if set above what your machine can synthesize** |
| `model_speed` | What Kokoro itself is asked for. Keep ≤ 1.3 — above that it degrades |
| `pause` | Silence after a sentence |
| `first_chunk_audio` | Seconds of audio in the opening chunk; lower = faster start, choppier opening |
| `output_device` | `null` follows the Windows default |

The same values can be changed live, without a restart, over HTTP:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:5111/config -Method Post -ContentType "application/json" -Body '{"playback_speed":2.0}'
Invoke-RestMethod -Uri http://127.0.0.1:5111/config    # current values + measured stats
```

Live `/config` changes are in-memory only; the tray is what makes them survive a
restart.

**Important:** editing `tts_server.py` does nothing to a running server. Kill it,
verify port 5111 is free, then start it again — the procedure is in
`docs/AUDIT.md` §7.

## Troubleshooting

### The install fails with "No matching distribution found for kokoro"

```text
ERROR: Ignored the following versions that require a different python version:
  0.9.4 Requires-Python >=3.10,<3.13
ERROR: Could not find a version that satisfies the requirement kokoro==0.9.4
  (from versions: 0.2.1, ... 0.7.16)
ERROR: No matching distribution found for kokoro==0.9.4
```

**Your venv is on Python 3.13 or newer.** The first `Ignored the following
versions` line is the real message — pip filtered out every 0.8.x/0.9.x wheel on
`Requires-Python`, leaving only the ancient 0.7.x line, which has no `0.9.4`.
It is not a network, proxy, or index problem, and no amount of retrying,
`--upgrade`, or a different mirror will change it.

Confirm, then rebuild the venv on 3.12 (from the repo folder):

```powershell
env\Scripts\python.exe -V               # the culprit, if this isn't 3.12.x
py -0p                                  # what's actually installed

py install 3.12                         # or the python.org 3.12 installer
Remove-Item -Recurse -Force env
py -3.12 -m venv env
env\Scripts\python.exe -V               # must print Python 3.12.x
env\Scripts\python.exe -m pip install --upgrade pip
env\Scripts\python.exe -m pip install -r requirements.txt
```

Do **not** "fix" this by relaxing the pin to a 0.7.x `kokoro` that installs on
3.13+ — that is a different model/voice API generation, and every measured
number in `docs/AUDIT.md` was taken on the pinned versions in
`requirements-base.txt`.

### Reads stall or stutter partway through

Synthesis can't keep up with playback. Open the tray Settings panel and lower
the reading speed until the warning clears, or install the CUDA build — see
[CPU or GPU](#cpu-or-gpu).

### Highlighting is wrong or missing

Check the table in [Word highlighting](#word-highlighting) first; several cases
are impossible rather than broken. The highlighter writes `highlighter.log` with
the document it anchored to and the rectangle for every word — that log, not
guesswork, is how these get diagnosed (`docs/AUDIT.md` §8, "Round 4").
`highlighter.err` is empty when healthy.

### Anything else

`server.log` first — it is truncated at each server start, so what's in it is
current. `docs/AUDIT.md` §7 and §8 cover the rest.

## Credits & licensing

The code in this repository is MIT-licensed (see `LICENSE`). It builds on:

- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) and the
  [kokoro](https://github.com/hexgrad/kokoro) library by hexgrad — Apache 2.0.
  Neither is redistributed here; the library installs from PyPI and the model
  downloads from Hugging Face on first run.
- [eSpeak NG](https://github.com/espeak-ng/espeak-ng) (GPL-3.0), used by Kokoro
  for phonemization. Not redistributed here either: pip pulls it in as a bundled
  binary inside `espeakng-loader`, a dependency of `misaki[en]`.
- Flask, NumPy, PyTorch, sounddevice — installed from PyPI under their own
  permissive licenses.
