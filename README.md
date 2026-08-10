# Local Read-Aloud (Kokoro TTS)

Select text anywhere on Windows → **Ctrl+Alt+R** reads it aloud in a natural voice →
**Ctrl+Alt+S** stops. Fully local (model runs on CPU), no network calls at runtime,
no clipboard polling. Starts speaking ~0.5s after the hotkey.

```
[keyboard or Logitech G Hub mouse macro]
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
        ▼
  highlighter.py        Tints the word being spoken, in place, in the source
                        app itself — via UI Automation + a click-through
                        layered window. Read-only; never touches audio.
```

For the full engineering history — measured performance facts, rejected
alternatives, and every non-obvious design decision — read **AUDIT.md** before
changing anything.

## Hotkeys

| Keys | Action |
|---|---|
| `Ctrl+Alt+R` | Read the current selection (works in browsers, PDFs, editors, terminals — see [Word highlighting](#word-highlighting) for where the visual marker is supported) |
| `Ctrl+Alt+T` | Read the clipboard as-is |
| `Ctrl+Alt+S` | Stop |

## Word highlighting

While a passage is read, the word being spoken lights up **in the original
text, where it already is** — no caption strip, no copy of the text. This runs
as a separate process (`highlighter.py`), reads the source app through UI
Automation, and paints a click-through translucent marker on top. It cannot
affect playback: kill it and audio continues unchanged.

| Where | Highlighting | Notes |
|---|---|---|
| Firefox | ✅ | Also Chrome/Edge and other UIA-exposing browsers |
| Notepad, text editors | ✅ | |
| VS Code editor (`.md`, code) | ✅ | Requires the setting below |
| **Terminals** | ❌ **Not possible** | See below — reading still works |
| PDFs in a browser viewer, Google Docs | ❌ | Canvas-rendered; no text geometry exists |

**VS Code** needs `"editor.accessibilitySupport": "on"` in your user settings,
otherwise VS Code exposes no editor text at all and there is nothing to locate.

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
works fine (that's step 5 below); only the visual marker is unavailable.

Troubleshooting: launch it with `KOKORO_HL_DEBUG=C:\path\to\log.txt` set and it
records which document it anchored to and the rectangle for every word. Check
that before theorising — see AUDIT.md §8 "Round 4".

## Setting up on a fresh Windows machine

1. **Install prerequisites** (all free):
   - **Python 3.12 specifically** — [python.org](https://www.python.org/downloads/release/python-31210/)
     (check "Add to PATH"), or `py install 3.12` if you have the Python Install
     Manager. **3.13 and newer will not work**: `kokoro` declares
     `Requires-Python >=3.10,<3.13`, so pip silently filters out every usable
     release. See [Troubleshooting](#troubleshooting) below for what that
     failure looks like. A newer Python being already
     installed is fine — you just must not build the venv from it.
   - [AutoHotkey v2](https://www.autohotkey.com/) — per-user or machine-wide,
     `start_tts.vbs` finds either. Without it the hotkeys are silently dead;
     nothing else in the system provides them.
   - **eSpeak NG is _not_ a separate install** (verified 2026-08-05). The
     `espeakng-loader` package pulled in by `misaki[en]` ships `espeak-ng.dll`
     and its data inside the venv, and phonemization — including out-of-dictionary
     words, the only thing that reaches eSpeak — works with nothing on PATH.
     Earlier versions of this README required the `.msi`; it is no longer needed
     with the pinned versions in `requirements.txt`.

2. **Clone and install** (PowerShell):

   ```powershell
   git clone https://github.com/sayed-qutob-work/kokoro-read-aloud.git C:\kokoro
   cd C:\kokoro
   py -3.12 -m venv env
   env\Scripts\python.exe -V          # must print Python 3.12.x — stop here if it doesn't
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

4. **Autostart**: Win+R → `shell:startup` → put a shortcut to
   `C:\kokoro\start_tts.vbs` there. It launches both processes hidden and logs the
   server to `server.log` (read that first whenever something misbehaves).

5. **Terminal reading** (optional): in VS Code settings, set
   `"terminal.integrated.copyOnSelection": true`. In Windows Terminal, set
   `"copyOnSelect": true`. This lets Ctrl+Alt+R work on terminal text, where
   simulating Ctrl+C is not an option (it means "interrupt" there). Terminal
   text is read aloud but not visually highlighted — see above for why.

6. **Mouse button** (optional): a Logitech G Hub macro bound to a spare button —
   on press: left-click down; on release: left-click up, then Ctrl+Alt+R. Then
   drag-selecting with that button reads the selection when released.

## Tuning

All knobs live in the config block at the top of `tts_server.py` (restart to apply),
and the important ones can be changed live without a restart:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:5111/config -Method Post -ContentType "application/json" -Body '{"playback_speed":2.0}'
Invoke-RestMethod -Uri http://127.0.0.1:5111/config    # current values + measured stats
```

- `playback_speed` — pitch-preserving speed-up applied after synthesis (default 1.8;
  effective rate = this × `model_speed`)
- `first_chunk_audio` — seconds of audio in the opening chunk; lower = faster start,
  choppier opening (default 2.0 ≈ 0.5s to first sound)
- `voice` — e.g. `af_heart`, `am_michael`, `bf_emma` (full list in `tts_server.py`)

**Important:** editing `tts_server.py` does nothing to a running server. Kill it,
verify port 5111 is free, then start it again — the procedure is in AUDIT.md §7.

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

Confirm, then rebuild the venv on 3.12:

```powershell
C:\kokoro\env\Scripts\python.exe -V     # the culprit, if this isn't 3.12.x
py -0p                                  # what's actually installed

py install 3.12                         # or the python.org 3.12 installer
Remove-Item -Recurse -Force C:\kokoro\env
py -3.12 -m venv C:\kokoro\env
C:\kokoro\env\Scripts\python.exe -V     # must print Python 3.12.x
C:\kokoro\env\Scripts\python.exe -m pip install --upgrade pip
C:\kokoro\env\Scripts\python.exe -m pip install -r C:\kokoro\requirements.txt
```

Rebuilding in place at `C:\kokoro\env` keeps the hardcoded
`env\Scripts\python.exe` paths in `start_tts.vbs` and the docs valid.

Do **not** "fix" this by relaxing the pin to a 0.7.x `kokoro` that installs on
3.13+ — that is a different model/voice API generation, and every measured
number in AUDIT.md was taken on the pinned versions in `requirements.txt`.

### Anything else

`server.log` first — it is truncated at each server start, so what's in it is
current. Highlighter problems: `highlighter.log` (and `highlighter.err`, which
is empty when healthy). AUDIT.md §7 and §8 cover the rest.

## Credits & licensing

The code in this repository is MIT-licensed (see `LICENSE`). It builds on:

- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) and the
  [kokoro](https://github.com/hexgrad/kokoro) library by hexgrad — Apache 2.0.
  Neither is redistributed here; the library installs from PyPI and the model
  downloads from Hugging Face on first run.
- [eSpeak NG](https://github.com/espeak-ng/espeak-ng) (GPL-3.0) — installed
  separately by the user, used by Kokoro for phonemization.
- Flask, NumPy, PyTorch, sounddevice — installed from PyPI under their own
  permissive licenses.
