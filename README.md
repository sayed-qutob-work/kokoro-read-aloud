# Local Read-Aloud (Kokoro TTS)

Select text anywhere, press Ctrl+Alt+R, and it is read aloud in a natural voice.
Everything runs locally: a Flask server holds the Kokoro-82M model in memory and
synthesises on demand. Nothing leaves the machine at runtime and the clipboard is
never polled.

While it reads, the current position is shown either in place (the spoken word is
tinted in the source app, Windows only) or on a caption strip at the bottom of the
screen (both platforms).

> **Status.** The last tagged release is `v0.1.0-beta`, Windows only. Linux
> (Fedora 44, GNOME on Wayland) support and the caption strip are on `main` and
> untagged. The audio path is stable and in daily use; the in-place highlighter is
> the rough part, see [Known issues](#known-issues).

## Processes

| Process | Platform | What it does |
|---|---|---|
| `tts_server.py` | both | Flask on 127.0.0.1:5111. Model resident, budget-driven chunking, WSOLA time-stretch, audio out |
| `read_aloud.ahk` | Windows | AutoHotkey v2 hotkeys. Copies the selection (clipboard saved and restored) and POSTs it |
| `read_aloud.sh` | Linux | Same hotkeys, bound through GNOME custom shortcuts. Reads the PRIMARY selection, so it never touches the clipboard |
| `highlighter.py` | Windows | Tints the spoken word in the source app via UI Automation and a click-through layered window. Read-only, cannot affect audio |
| `overlay.py` | both | Caption strip. Polls `/now` and renders the sentence being read |
| `tray.py` | both | Settings panel over `/config`, plus process control. Windows also gets a tray icon (`tray_win32.py`) |
| `extension/` | both | Chromium in-page highlighter, loaded unpacked. Talks to the server directly |

The engineering docs in [docs/](docs/README.md) carry the measurements and the
decisions behind all of this.

## Hotkeys

| Keys | Action |
|---|---|
| `Ctrl+Alt+R` | Read the current selection |
| `Ctrl+Alt+T` | Read the clipboard as-is |
| `Ctrl+Alt+S` | Stop |

Reading works wherever text can be selected, including terminals and PDFs. Only
the visual marker is limited.

## Showing where you are

Two mechanisms, with different reach.

### In-place highlighting (Windows)

The spoken word is tinted in the original text. This depends on what each app
exposes to accessibility APIs.

| Where | Marker | Notes |
|---|---|---|
| Firefox | yes | Gecko exposes real text geometry |
| Chrome / Edge | yes | Load `extension/` unpacked. In Chromium the extension is the reliable path, not UI Automation |
| Notepad and Win32 editors | yes | |
| VS Code editor | partial | Needs `"editor.accessibilitySupport": "on"` in user settings, otherwise no editor text is exposed at all |
| Obsidian (`.md`) | partial | Works since 2026-08-12 |
| Outlook / Hotmail on the web | partial | Intermittent, undiagnosed (`docs/plan.md` D2) |
| Terminals | no | Structurally impossible, see below |
| PDFs in a browser viewer, Google Docs | no | Canvas-rendered, no text geometry exists to point at |

Terminals draw text to a canvas and expose it through a hidden off-screen buffer,
so the position they report has no relation to where the pixels are. Reading
terminal text works; the marker does not, and cannot.

In VS Code you may also want to scope off its own word-occurrence tinting for
markdown, which reacts to the cursor the highlighter has to move:

```json
"[markdown]": {
    "editor.occurrencesHighlight": "off",
    "editor.selectionHighlight": false
}
```

There is no in-place highlighter on Linux. UI Automation is a Windows API and
Wayland exposes no equivalent, so Linux uses the caption strip. The Chromium
extension is the exception: it only needs the local server, so it should work on
Linux too (untested there).

### Caption strip (`overlay.py`, both platforms)

A strip that appears while speech is playing and hides about 0.7s after it stops.
Drag to move, right-click to close. It reads the text from the server, so it works
for terminals, PDFs and anything else the in-place highlighter cannot reach.

Start it yourself; nothing autostarts it yet:

```
env/bin/python overlay.py          # Linux
env\Scripts\pythonw.exe overlay.py # Windows
```

Options, all in the tray's Settings panel:

| Setting | Values | |
|---|---|---|
| `caption_layout` | `teleprompter`, `rows` | Teleprompter wraps the whole passage and scrolls it past a fixed reading line. Rows is a static three-line strip |
| `caption_scroll` | `continuous`, `line`, `off` | Teleprompter only. `continuous` creeps at reading pace, `line` glides a line at a time, `off` snaps |
| `caption_style` | `underline`, `terminal`, `rail` | Three visual themes |
| `caption_position` | `bottom`, `center`, `top` | |
| `caption_monitor` | `primary` or a connector name (`DP-1`) | Monitors are enumerated from the compositor |

These five are the only settings the server never sees. The strip reads them off
disk at startup, so changing them restarts the strip. The tray does that for you
on save.

## CPU or GPU

Playback drains audio at `playback_speed` audio-seconds per second while synthesis
produces audio at some multiple of realtime. If synthesis is slower than playback,
long reads stall.

| | Measured throughput | Against a default drain of 1.8 |
|---|---|---|
| GTX 1650, Windows (2026-08-11) | 25.0x realtime | 14x headroom |
| GTX 1650, Fedora (2026-08-18) | 13.3x realtime | 7.4x headroom |
| Same laptop, CPU build | 1.77x realtime | Below break-even, stalls |

All three on the same machine (`docs/AUDIT.md` §8). Linux is slower than Windows
on identical hardware and that is not yet root-caused; it is far enough above the
floor not to matter. Your own figure is live as `measured_rt` from `GET /config`.
It is learned from real reads, so it starts low after a restart and climbs.

Install `requirements-cuda.txt` if you have an NVIDIA GPU. On CPU, lower the
reading speed in the Settings panel until its sustainable-speed warning clears;
the panel reads your machine's measured throughput and knows the ceiling.

There is no device-selection code in the server. Kokoro takes the device from
whichever torch build is installed, so install one of `requirements.txt` or
`requirements-cuda.txt`, never both.

## Install

Both platforms need Python 3.12. `kokoro` declares `Requires-Python >=3.10,<3.13`,
and on 3.13+ pip filters out every usable wheel and reports it as a missing
package (see [Troubleshooting](#troubleshooting)). Having a newer Python installed
is fine as long as the venv is not built from it.

Any folder works. The scripts locate themselves from their own path.

### Windows

Install [AutoHotkey v2](https://www.autohotkey.com/) first, per-user or
machine-wide. Without it the hotkeys are dead and nothing else provides them.
eSpeak NG does not need a separate install: `espeakng-loader`, pulled in by
`misaki[en]`, ships the DLL and its data inside the venv.

```powershell
git clone https://github.com/sayed-qutob-work/kokoro-read-aloud.git
cd kokoro-read-aloud
py -3.12 -m venv env
env\Scripts\python.exe -V          # must print 3.12.x

# NVIDIA GPU:
env\Scripts\python.exe -m pip install -r requirements-cuda.txt
# otherwise:
env\Scripts\python.exe -m pip install -r requirements.txt
```

Use `py -3.12 -m venv`, not `python -m venv`, and always
`env\Scripts\python.exe -m pip`, never bare `pip`. Quote version specs
(`pip install "kokoro>=0.9.4"`), because `>` is a redirect in PowerShell.

First run downloads the ~330MB Kokoro-82M model and a ~13MB `en_core_web_sm`
spaCy model that `misaki` fetches on its first phonemization:

```powershell
env\Scripts\python.exe tts_server.py
```

Wait for `[kokoro] ready on http://127.0.0.1:5111`, start `read_aloud.ahk`, then
select text and press Ctrl+Alt+R.

For autostart, put a shortcut to `start_tts.vbs` in `shell:startup`. It launches
the server, hotkeys, highlighter and tray hidden, and logs the server to
`server.log`.

### Linux (Fedora 44, GNOME on Wayland)

Tested on Fedora 44. Other distributions should work but have not been tried.

```bash
sudo dnf install python3.12 python3.12-devel python3.12-tkinter python3-tkinter \
                 portaudio espeak-ng wl-clipboard jq curl

git clone https://github.com/sayed-qutob-work/kokoro-read-aloud.git
cd kokoro-read-aloud
python3.12 -m venv env
env/bin/python -V                  # must print 3.12.x

# NVIDIA GPU (needs the RPM Fusion akmod-nvidia driver):
env/bin/python -m pip install -r requirements-cuda.txt
# otherwise:
env/bin/python -m pip install -r requirements.txt

env/bin/python tts_server.py
```

`espeak-ng` and `portaudio` come from the system here, so there is no
DLL-bundling step.

Bind the hotkeys in Settings > Keyboard > View and Customize Shortcuts > Custom
Shortcuts, using absolute paths:

| Shortcut | Command |
|---|---|
| Ctrl+Alt+R | `/path/to/kokoro-read-aloud/read_aloud.sh selection` |
| Ctrl+Alt+T | `/path/to/kokoro-read-aloud/read_aloud.sh clipboard` |
| Ctrl+Alt+S | `/path/to/kokoro-read-aloud/read_aloud.sh stop` |

`read_aloud.sh` reads the PRIMARY selection rather than copying, because Wayland
gives a background process no way to inject Ctrl+C into another app.

Then install the settings launcher:

```bash
linux/install-desktop.sh
```

That writes a per-user `.desktop` entry, so "Kokoro Settings" appears in the app
grid. Re-run it if you move the folder; the paths are baked in at install time.
`env/bin/python tray.py --settings` opens the same panel from a terminal.

There is no tray icon on GNOME. GNOME removed the notification area, and an
indicator would need the third-party AppIndicator extension plus PyGObject, which
Fedora builds only for the system Python (3.14) while this venv must be 3.12.
`docs/AUDIT.md` (2026-08-18) has the detail.

Nothing autostarts on Linux yet. Start the server, the strip and the panel by
hand; `systemd --user` units are planned.

### Optional extras

- **Terminal reading.** Set `"terminal.integrated.copyOnSelection": true` in VS
  Code, or `"copyOnSelect": true` in Windows Terminal, so selecting terminal text
  makes it readable. Simulating Ctrl+C is not an option there, since it means
  interrupt.
- **Mouse button.** Bind a spare button to left-click-on-press, then
  left-click-release plus Ctrl+Alt+R on release, so drag-selecting reads the
  selection when you let go. Logitech G Hub on Windows; `input-remapper` on Linux,
  which works at the evdev level and is Wayland-safe. Solaar cannot remap a G Pro
  Wireless, see `docs/AUDIT.md` §6. On Linux the macro needs a ~60ms gap before
  the hotkey, because PRIMARY-selection ownership does not transfer synchronously
  with mouse-up.

## Settings

The Settings panel is the intended interface. It writes `settings.json`, which the
server loads at startup, and shows the sustainable-speed ceiling for your machine.
`settings.json` is not tracked by git; [settings.example.json](settings.example.json)
documents the shape and the shipped defaults.

| Key | What |
|---|---|
| `voice` | e.g. `af_heart`, `am_michael`, `bf_emma` (full list in `tts_server.py`) |
| `playback_speed` | Pitch-preserving speed-up after synthesis. This is the one that stalls reads if set above what the machine can synthesise |
| `model_speed` | What Kokoro itself is asked for. Keep at or below 1.3; above that it degrades |
| `pause` | Silence after a sentence |
| `first_chunk_audio` | Seconds of audio in the opening chunk. Lower starts faster and sounds choppier |
| `output_device` | `null` follows the system default |
| `caption_*` | The caption strip, see [above](#caption-strip-overlaypy-both-platforms) |

Everything except the `caption_*` keys can also be changed live over HTTP:

```bash
curl -X POST http://127.0.0.1:5111/config -H 'Content-Type: application/json' \
     -d '{"playback_speed":2.0}'
curl http://127.0.0.1:5111/config          # current values + measured stats
```

Live changes are in-memory only. The panel is what makes them survive a restart.

Editing `tts_server.py` does nothing to a running server, and a second launch dies
silently on "address in use" while the old one keeps serving the old code. Kill it,
check that port 5111 is free, then start it again. The procedure is in
`docs/AUDIT.md` §7.

## Known issues

All in the Windows in-place highlighter. Audio is unaffected by every one:

- Multi-line selections highlight less reliably than single-line ones.
- Reading the same passage repeatedly can make the marker clash with itself or
  stop updating.
- Outlook / Hotmail on the web works sometimes and fails often; undiagnosed.
- The marker can land on the wrong word rather than simply not appearing.

The terminal, PDF-viewer and Google Docs cases in the table above are structural
limits, not bugs. `docs/AUDIT.md` §6 has the evidence.

## Troubleshooting

### "No matching distribution found for kokoro"

```text
ERROR: Ignored the following versions that require a different python version:
  0.9.4 Requires-Python >=3.10,<3.13
ERROR: Could not find a version that satisfies the requirement kokoro==0.9.4
  (from versions: 0.2.1, ... 0.7.16)
ERROR: No matching distribution found for kokoro==0.9.4
```

The venv is on Python 3.13 or newer. The `Ignored the following versions` line is
the real message: pip filtered out every 0.8.x/0.9.x wheel on `Requires-Python`
and left only the 0.7.x line, which has no 0.9.4. Rebuild the venv on 3.12.

Do not work around it by relaxing the pin to a 0.7.x `kokoro`. That is a different
model and voice API generation, and every measured number in `docs/AUDIT.md` was
taken on the pins in `requirements-base.txt`.

### Reads stall or stutter partway through

Synthesis is not keeping up with playback. Lower the reading speed in the Settings
panel until the warning clears, or install the CUDA build. See
[CPU or GPU](#cpu-or-gpu).

### Highlighting is wrong or missing

Check the table above first. The highlighter writes `highlighter.log` with the
document it anchored to and the rectangle for every word; diagnose from that log
(`docs/AUDIT.md` §8, "Round 4"). `highlighter.err` is empty when healthy.

### Anything else

Read `server.log` first. It is truncated at each server start, so its contents are
always current. `docs/AUDIT.md` §7 and §8 cover the rest.

## Credits & licensing

The code here is MIT-licensed (see `LICENSE`). It builds on:

- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) and the
  [kokoro](https://github.com/hexgrad/kokoro) library by hexgrad, Apache 2.0.
  Neither is redistributed here: the library installs from PyPI, the model
  downloads from Hugging Face on first run.
- [eSpeak NG](https://github.com/espeak-ng/espeak-ng) (GPL-3.0), used by Kokoro
  for phonemization. Also not redistributed: on Windows pip pulls it in as a
  bundled binary inside `espeakng-loader`, on Linux it comes from the system.
- Flask, NumPy, PyTorch, sounddevice, installed from PyPI under their own
  permissive licenses.
