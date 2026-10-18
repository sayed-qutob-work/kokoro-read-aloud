# Kokoro Read-Aloud

Select text anywhere, press **Ctrl+Alt+R**, and it is read aloud in a natural
voice. Everything runs on your own machine. A small local server keeps the
[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) model loaded and starts
speaking while the rest of the text is still being synthesized. Your text never
leaves the machine, and the clipboard is never polled.

It runs on Windows 11 and on Linux: Fedora with GNOME, and Omarchy with
Hyprland, both on Wayland.

> **Status.** The last tagged release is `v0.1.0-beta`, and it is Windows only.
> Linux support, the caption strip and the Omarchy bar widget are on `main`
> and untagged. Audio is stable and in daily use. The rough part is the Windows
> in-place highlighter, see [Known issues](#known-issues).

## What you get

- **Three hotkeys.** They work wherever text can be selected, including
  terminals and PDFs.

  | Keys | Action |
  |---|---|
  | `Ctrl+Alt+R` | Read the selection |
  | `Ctrl+Alt+T` | Read the clipboard as-is |
  | `Ctrl+Alt+S` | Stop |

- **Fast, steady speech.** On an RTX 4060 Ti, speech starts about a tenth of
  a second after the server gets the text. On a CPU it takes a few tenths. Text
  is split at commas and full stops wherever it can be, so pauses fall where
  the sentence has them. The speed-up keeps the voice's pitch.
- **Your place in the text.** Windows tints the spoken word in the app
  itself. A caption strip works on both platforms, and a Chromium extension
  marks the word inside web pages.
- **A settings panel** for voice, speed, pauses and output device. It knows how
  fast your machine can synthesize, and warns you before you pick a speed it
  cannot keep up with.
- **An Omarchy bar icon** that frees the model's VRAM when a game or a local
  LLM needs it, or moves the model to the CPU.
  [See below](#omarchy-bar-widget).

## Install

| Platform | Desktop | How |
|---|---|---|
| Omarchy 4 (Arch) | Hyprland | Add it as a plugin, then press **Set up** in the bar. See [Omarchy](#linux-omarchy-hyprland) |
| Fedora 44 | GNOME on Wayland | Clone it, then `linux/kokoroctl setup`. See [Fedora](#linux-fedora-gnome) |
| Windows 11 | | Clone it and install with pip. See [Windows](#windows) |

On Linux, one command does the install, [`kokoroctl setup`](#what-setup-does).
It needs a few system packages first, because it runs without root.

### Linux: Omarchy (Hyprland)

Tested on Omarchy 4.0.4 (Arch, Hyprland 0.56.2) with an NVIDIA GPU.

```bash
sudo pacman -S --needed tk portaudio wl-clipboard jq curl libnotify
omarchy plugin add https://github.com/sayed-qutob-work/kokoro-read-aloud --enable
```

`omarchy plugin add` first warns that plugins run unsandboxed inside the
shell, and asks you to confirm. With `--enable` it then asks which bar section
to use. A Kokoro icon appears there, as five dim dots. Click it and press **Set
up**. The panel shows each step, and a notification says when it is ready.
From a terminal, run this instead:

```bash
~/.config/omarchy/plugins/io.github.sayed-qutob-work.kokoro-read-aloud/linux/kokoroctl setup
```

Setup also puts a `kokoroctl` command in `~/.local/bin`, so from then on
`kokoroctl setup`, `kokoroctl status` and the rest work from any terminal.

Setup also writes the Hyprland hotkeys: Ctrl+Alt+R, T and S. They live in
`~/.config/hypr/kokoro.lua`, loaded by one `dofile(...)` line that setup adds
to `~/.config/hypr/hyprland.lua` after backing that file up. If you had
already bound `read_aloud.sh` by hand, setup lists where. Remove those binds,
or every press fires twice.

Notes:

- **Caption strip monitors** come from `hyprctl monitors`. Hyprland has no
  primary monitor, so "Primary monitor" in Settings means the first one it
  lists. Pick a connector such as `DP-3` to be explicit.
- **`sudo` needs a terminal** for its password prompt. In a shell without
  one, such as a script, an AI agent or Claude Code's `!` prefix, use
  `pkexec`. Omarchy's shell shows the password dialog.

### Linux: Fedora (GNOME)

Fedora 44 runs Kokoro Read-Aloud, installed the earlier, manual way.
`kokoroctl setup` does the same steps there as on Omarchy, but has not been
run on Fedora yet.

```bash
sudo dnf install python3-tkinter python3-xlib portaudio wl-clipboard jq curl libnotify
git clone https://github.com/sayed-qutob-work/kokoro-read-aloud.git
cd kokoro-read-aloud
linux/kokoroctl setup
```

`python3-xlib` is for the system Python. GNOME 50.5 stopped giving
`wl-paste` the focus it needs to read the selection, so `read_aloud.sh`
reads it through Xwayland instead (`linux/xselection.py`). Without the
package it falls back to `wl-paste`, and reads stall for seconds.

Setup cannot bind GNOME shortcuts, so it prints the commands. Bind them in
Settings > Keyboard > View and Customize Shortcuts > Custom Shortcuts:

| Shortcut | Command |
|---|---|
| Ctrl+Alt+R | `/path/to/kokoro-read-aloud/read_aloud.sh selection` |
| Ctrl+Alt+T | `/path/to/kokoro-read-aloud/read_aloud.sh clipboard` |
| Ctrl+Alt+S | `/path/to/kokoro-read-aloud/read_aloud.sh stop` |

`read_aloud.sh` reads the PRIMARY selection instead of copying. Wayland
gives a background process no way to send Ctrl+C to another app, and this way
the clipboard is never touched.

There is no tray icon on GNOME. GNOME removed the notification area, and an
indicator would need the third-party AppIndicator extension. The settings
panel opens from the app grid ("Kokoro Settings"), or with
`kokoroctl settings`.

### What setup does

`kokoroctl setup` installs everything outside the code folder, so that folder
is never written to. The steps:

1. **Checks the system packages** listed above. If any are missing, it stops
   and prints the `pacman` or `dnf` command that installs them.
2. **Gets uv.** It uses your own if it is 0.12.10 or newer. Otherwise it
   downloads uv 0.12.24 and checks it against a pinned SHA-256.
3. **Installs Python 3.12 and the server's packages** from `uv.lock`, at the
   exact versions they were tested with. It installs the GPU (CUDA) build
   when `nvidia-smi` finds a GPU, and the CPU build otherwise; `--cpu` and
   `--cuda` override that. On a machine with an NVIDIA GPU, the bar widget
   offers both. The CPU build is smaller but slower, and cannot use the GPU
   later: `kokoroctl setup --cuda` switches to the GPU build.
4. **Downloads the voice model.** That is 326 MiB from Hugging Face, each file
   checked against its pinned SHA-256. From then on the server loads it from
   disk and makes no network connections.
5. **Installs the services:** autostart for the server, the hotkeys on
   Hyprland, a "Kokoro Settings" launcher entry, and the `kokoroctl` command
   in `~/.local/bin`. Then it starts the server.

| | GPU build | CPU build |
|---|---|---|
| Download | about 4 GB | about 0.7 GB |
| On disk | about 7 GB | about 2 GB |
| First sound after the hotkey | about 0.1 s | about 0.4 s |

Most of the time is the download. A fresh NVIDIA install took 1 min 46 s on a
fast connection, and 31 minutes at 2 MB/s.

Everything goes to three folders:

- `~/.local/share/kokoro-read-aloud/` holds uv and its own cache, Python,
  the venv and the model.
- `~/.config/kokoro-read-aloud/` holds your settings.
- `~/.local/state/kokoro-read-aloud/` holds the logs.

Running setup again repairs a broken or half-finished install, and keeps
what is already there. `kokoroctl log setup` shows the last run.

### Windows

Install [AutoHotkey v2](https://www.autohotkey.com/) first, either per-user or
machine-wide. The hotkeys come from it, and nothing else provides them.
You don't need to install eSpeak NG: `espeakng-loader`, pulled in by
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

env\Scripts\python.exe tts_server.py
```

Wait for `[kokoro] ready on http://127.0.0.1:5111`. Then start `read_aloud.ahk`,
select some text and press Ctrl+Alt+R.

To start everything at login, put a shortcut to `start_tts.vbs` in
`shell:startup`. It launches the server, hotkeys, highlighter and tray icon,
all hidden, and logs the server to `server.log`.

- **Python 3.12 exactly.** `kokoro` declares `Requires-Python >=3.10,<3.13`.
  On 3.13 or newer, pip filters out every usable wheel and reports a missing
  package instead (see [Troubleshooting](#troubleshooting)). A newer Python
  can be installed alongside, as long as the venv is not built from it.
- **One requirements file.** Install `requirements-cuda.txt` if you have an
  NVIDIA GPU, otherwise `requirements.txt`. Never install both: Kokoro uses
  whichever torch build is present. Both pin every package to the version
  and SHA-256 in `uv.lock`, so pip installs the tested set.
- **The model** downloads on the first run, about 330 MB, and the server
  then checks Hugging Face for updates at every start. To stop that, run
  `env\Scripts\python.exe model_files.py fetch` once. The server then loads
  the pinned files from `models\` and makes no network connections. This was
  tested on Linux, not yet on Windows.
- **PowerShell traps:** use `py -3.12 -m venv`, not `python -m venv`. Use
  `env\Scripts\python.exe -m pip`, never bare `pip`.

### Updating

- **Omarchy:**

  ```bash
  omarchy plugin update io.github.sayed-qutob-work.kokoro-read-aloud
  omarchy restart shell
  kokoroctl setup
  ```

  The shell restart loads the new widget code; the shell keeps the old
  compiled code until then. Setup installs any new packages and restarts
  the server on the new code.
- **Fedora:** `git pull`, then `kokoroctl setup`.
- **Windows:** `git pull`, run the same `pip install -r` again, then restart
  the server.

### Removing

- **Omarchy:**

  ```bash
  kokoroctl uninstall
  omarchy plugin remove io.github.sayed-qutob-work.kokoro-read-aloud
  ```
- **Fedora:** `kokoroctl uninstall`, delete the hotkeys you bound in
  GNOME Settings, then delete the folder.
- **Windows:** first remove the model from Hugging Face's cache, which is
  shared with other apps and so is not inside the folder:
  `env\Scripts\hf.exe cache rm model/hexgrad/Kokoro-82M -y`. Then delete the
  shortcut in `shell:startup`, and the folder.

`kokoroctl uninstall` asks before doing anything. It removes:

- the services and the hotkeys;
- the launcher entry and the `kokoroctl` command;
- the `~/.local/share/kokoro-read-aloud/` and
  `~/.local/state/kokoro-read-aloud/` folders.

It keeps your settings in `~/.config/kokoro-read-aloud/` unless you pass
`--purge`, and it never touches the code folder.

## Omarchy bar widget

<img src="docs/img/omarchy-widget.png" width="385" alt="The Kokoro panel open under its bar icon, reading on the GPU: the sentence being spoken with the read part highlighted, the GPU / CPU / Off switch, Kokoro's share of GPU memory, the live voice and speed, and six action buttons">

On Omarchy, the plugin puts a Kokoro icon in the bar. It is built from the
shell's own components, so it follows your theme
and keyboard conventions like the built-in panels do. The main reason it exists
is to give the GPU back: the server keeps the model loaded all day, and a game or
a local LLM may need that VRAM.

<img src="docs/img/omarchy-bar-states.png" width="390" alt="The bar icon in its three states: five bars at rest, bars moving while reading, and five dim dots when the model is off">

The bars move while it reads, ripple while the model loads, and turn into dim
dots when it is off.

| Input | Does |
|---|---|
| Left click | Open the panel |
| Right click | Turn the model off, freeing its VRAM, or back on |
| Middle click | Stop the current read |
| In the panel | Arrows or `hjkl` move, `Enter` activates, `Esc` closes. `g` / `c` / `o` pick GPU / CPU / Off, `s` stops reading, `r` restarts |

The panel shows:

- **GPU / CPU / Off.** CPU mode restarts the server with the GPU hidden from
  it. It uses no VRAM, and reads keep working.
- **Kokoro's share of the GPU's memory**, next to every other process, plus
  the RAM it holds.
- **The sentence being read**, with the part already spoken shown brighter.
- **The live voice, speed, output device and uptime.**
- **Actions:** stop reading, read the clipboard, restart, captions on/off, open
  Settings, and follow `server.log`.

What each mode costs, measured on an RTX 4060 Ti with an i5-12400F:

| Mode | VRAM | RAM | First sound | Synthesis speed |
|---|---|---|---|---|
| GPU | 0.8 GB after a start, up to 1.6 GB with use | ~2 GB | 0.06–0.1 s | 20–87x realtime |
| CPU | none | 1.5–1.8 GB | 0.35–0.5 s | 4.4–5.3x realtime |
| Off | none | none | n/a | n/a |

Switching to GPU or CPU takes about 6 s, the time it takes to load the model.
Switching off is immediate. On the CPU, a very short first sentence can be
followed by a brief pause, because the next chunk takes longer to synthesize
than the first one takes to play. CPU mode lasts until you log out, and the
next login starts on the GPU again. A hotkey pressed while Kokoro is off shows a
notification and does not start the model, so a game keeps its VRAM.

Before setup has run, the icon shows five dim dots and the panel offers setup
instead: the GPU build, or on an NVIDIA machine the smaller CPU build. To update or remove the widget, see [Updating](#updating) and
[Removing](#removing).

### From a terminal or a keybind

The widget runs everything through `kokoroctl`, which also works on its own.
Setup puts it in `~/.local/bin`:

```bash
kokoroctl off       # stop the server: VRAM and RAM freed, hotkeys go quiet
kokoroctl cpu       # run on the CPU: no VRAM, reads keep working
kokoroctl gpu       # back on the GPU
kokoroctl restart   # same device as now
kokoroctl status    # JSON: state, device, VRAM/RAM, live config
kokoroctl set voice=bf_emma speed=2.1   # live and saved, like the panel
kokoroctl sample    # read a sample sentence with them
```

It also takes `stop`, `clipboard`, `captions [on|off|toggle]`, `settings`
(`--json` prints the voice, speed and voice list), `log [setup]`, `setup` and
`uninstall`.

## Showing where you are

### In-place highlighting (Windows)

The spoken word is tinted in the original text. How well that works depends on
what each app exposes to accessibility APIs:

| Where | Marker | Notes |
|---|---|---|
| Firefox | yes | Gecko exposes real text geometry |
| Chrome / Edge | yes | Load `extension/` unpacked. In Chromium the extension is the reliable path, not UI Automation |
| Notepad and Win32 editors | yes | |
| VS Code editor | partial | Needs `"editor.accessibilitySupport": "on"` in user settings, or no editor text is exposed at all |
| Obsidian (`.md`) | partial | Works since 2026-08-12 |
| Outlook / Hotmail on the web | partial | Intermittent, undiagnosed (`docs/plan.md` D2) |
| Terminals | no | Not possible, see below |
| PDFs in a browser viewer, Google Docs | no | Drawn to a canvas, with no text geometry to point at |

Terminals draw text to a canvas and expose it to accessibility tools through a
hidden off-screen buffer. The position they report has nothing to do with where
the pixels are. So terminal text can be read aloud, but it can't be highlighted.

In VS Code, the highlighter has to move the cursor, and VS Code then tints every
other occurrence of the word. To turn that off for markdown only:

```json
"[markdown]": {
    "editor.occurrencesHighlight": "off",
    "editor.selectionHighlight": false
}
```

There is no in-place highlighter on Linux. UI Automation is a Windows API and
Wayland has no equivalent, so Linux uses the caption strip. The Chromium
extension only needs the local server, so it should work on Linux too. That is
untested.

### Caption strip

`overlay.py` shows the sentence being read in a strip that appears while speech
plays. It hides about 0.7 s after speech stops. Drag it to move it, right-click
it to close it. It gets the text from the server, so it works for terminals,
PDFs and everything else the in-place highlighter can't reach.

It is off by default. To start it:

- **Linux:** `systemctl --user enable --now kokoro-overlay` to start it at every
  login. On Omarchy you can also use the panel's Captions button.
- **Windows:** `env\Scripts\pythonw.exe overlay.py`.

All of its options are in the Settings panel:

| Setting | Values | |
|---|---|---|
| `caption_layout` | `teleprompter`, `rows` | Teleprompter wraps the whole passage and scrolls it past a fixed reading line. Rows is a static three-line strip |
| `caption_scroll` | `continuous`, `line`, `off` | Teleprompter only. `continuous` creeps at reading pace, `line` glides a line at a time, `off` snaps |
| `caption_style` | `underline`, `terminal`, `rail` | Three visual themes |
| `caption_position` | `bottom`, `center`, `top` | |
| `caption_monitor` | `primary` or a connector name (`DP-1`) | Monitors are listed by the compositor |

The strip reads these when it starts, so changing one restarts it. The Settings
panel does that for you when you save.

## Settings

The Settings panel is the main way to change settings. It writes
`settings.json`, which the server loads at startup, and it shows the fastest
reading speed your machine can sustain. On Linux the file is
`~/.config/kokoro-read-aloud/settings.json`. On Windows it is next to the code,
and not tracked by git.
[settings.example.json](settings.example.json) shows its format and the shipped
defaults.

| Key | What |
|---|---|
| `voice` | e.g. `af_heart`, `am_michael`, `bf_emma` (all 28 in `kokoro_settings.py`) |
| `playback_speed` | Speed-up applied after synthesis, keeping pitch. Set it above what the machine can synthesize and reads stall |
| `model_speed` | The speed Kokoro itself is asked for. Keep it at or below 1.3, above that the voice degrades |
| `pause` | Silence after a sentence |
| `first_chunk_audio` | Seconds of audio in the opening chunk. Lower starts faster but sounds choppier |
| `output_device` | `null` follows the system default |
| `caption_*` | The caption strip, see [above](#caption-strip) |

Everything except the `caption_*` keys can also be changed live over HTTP:

```bash
curl -X POST http://127.0.0.1:5111/config -H 'Content-Type: application/json' \
     -d '{"playback_speed":2.0}'
curl http://127.0.0.1:5111/config          # current values, device and measured speed
```

Changes made over HTTP last until the server restarts. Use the panel to keep
them.

## CPU or GPU

Playback uses up audio at `playback_speed` seconds of audio per second.
Synthesis has to produce it faster than that, or long reads stall.

| Machine | Measured synthesis | Against the default 1.8x playback |
|---|---|---|
| GTX 1650 laptop, Windows (2026-08-11) | 25.0x realtime | 14x headroom |
| Same laptop, Fedora (2026-08-18) | 13.3x realtime | 7.4x headroom |
| Same laptop, CPU build | 1.77x realtime | Below break-even, stalls |
| RTX 4060 Ti desktop, Omarchy (2026-10-07) | 20–87x realtime per chunk, short chunks lowest | 11x or more |
| Same desktop, i5-12400F, GPU hidden | 4.4–5.3x realtime | 2.4x headroom |

Linux is slower than Windows on the same laptop. The cause is not known yet,
but both are well above the break-even point. Your own figure is `measured_rt`
in `GET /config`. It is learned from real reads and saved separately for the
GPU and the CPU in `calibration.json`, which on Linux is in
`~/.local/state/kokoro-read-aloud/`.

On a CPU, lower the reading speed in the Settings panel until its warning
clears. The panel knows your machine's measured throughput. A CUDA install can
still run on the CPU when you want the VRAM back, see
[Omarchy bar widget](#omarchy-bar-widget) and `kokoroctl`.

## Optional extras

- **Reading terminal text.** Turn on copy-on-select:
  `"terminal.integrated.copyOnSelection": true` in VS Code, or
  `"copyOnSelect": true` in Windows Terminal. Sending Ctrl+C isn't an option
  in a terminal, because there it means interrupt.
- **A mouse button that reads what you drag over.** Hold the button to
  drag-select, and the selection is read when you let go. On Windows this is
  a Logitech G Hub macro. On Linux it is an `input-remapper` macro, which
  works at the evdev level and is Wayland-safe. There, a quick tap still does
  what the button did before. On Omarchy, `input-remapper` is in the AUR
  (`yay -S input-remapper`, then `sudo systemctl enable --now input-remapper`).
  The macro and the reasons behind it are in `docs/AUDIT.md` (2026-08-19).
  Solaar cannot remap a G Pro Wireless (`docs/AUDIT.md` §6).

## What gets read

Kokoro read-aloud reads English. Other languages written in the Latin
alphabet, such as French, Spanish or German, are read with English
pronunciation.

These are skipped rather than spelled out:

- **Other scripts:** Japanese, Chinese, Korean, Cyrillic, Greek, Hebrew,
  Arabic, Hindi and the rest. In mixed text the English is still read.
- **Emoji,** with their skin tones and flags. Kokoro would read their names,
  such as "grinning face".

These are kept:

- Symbols with a spoken name, such as ©, ™, °C, €, ½, x² and ≈.
- A lone Greek letter such as π or Δ, which is read by its name.
- The digit in a keycap emoji: 1️⃣ is read as "one".

On Linux, a selection with nothing readable in it shows a notification.

Reading the other languages Kokoro supports, each with its own
pronunciation, is planned for v0.3.0.

## Known issues

These are all in the Windows in-place highlighter. None of them affect audio.

- Multi-line selections highlight less reliably than single-line ones.
- Reading the same passage repeatedly can make the marker clash with itself or
  stop updating.
- Outlook / Hotmail on the web works sometimes and fails often. The cause is
  not known.
- The marker can land on the wrong word rather than simply not appearing.

The terminal, PDF-viewer and Google Docs cases in the table above can't be
fixed. `docs/AUDIT.md` §6 has the evidence.

## Troubleshooting

Read `server.log` first. It is truncated at each server start, so it always
covers the current run. On Windows it is next to the code. On Linux,
`kokoroctl log` opens it. It lives in `~/.local/state/kokoro-read-aloud/`
with the other logs: `read_aloud.log` records every hotkey press, and
`overlay.log` and `overlay.err` record the caption strip.

If none of the cases below fits, [open a bug
report](https://github.com/sayed-qutob-work/kokoro-read-aloud/issues/new?template=bug_report.yml).
The form asks for your platform, GPU, Python version, `kokoroctl status` and
the logs, and says what in the logs to check before you paste them.

### "No matching distribution found for kokoro"

```text
ERROR: Ignored the following versions that require a different python version:
  0.9.4 Requires-Python >=3.10,<3.13
ERROR: Could not find a version that satisfies the requirement kokoro==0.9.4
  (from versions: 0.2.1, ... 0.7.16)
ERROR: No matching distribution found for kokoro==0.9.4
```

The venv is on Python 3.13 or newer. The `Ignored the following versions` line
is the real message: pip filtered out every 0.8.x/0.9.x wheel because of
`Requires-Python`, which left only the 0.7.x line. Rebuild the venv on 3.12.
Don't relax the pin to a 0.7.x `kokoro`: that is a different model and voice
API, and every number in `docs/AUDIT.md` was measured on the versions pinned in
`uv.lock`.

### Reads stall or stutter partway through

Synthesis is not keeping up with playback. Lower the reading speed in the
Settings panel until the warning clears, or use the GPU. See
[CPU or GPU](#cpu-or-gpu).

### The hotkey says "Kokoro is off" or "still loading"

On Linux, Kokoro was turned off from the bar or with `kokoroctl off`, or it is
still loading its model, which takes about 6 s. Turn it back on from the bar
icon or with `kokoroctl gpu`.

### Changes to the server or the widget don't show up

Editing `tts_server.py` does nothing to a server that is already running. A
second copy dies silently with "address in use" while the old one keeps
serving. Restart it instead: on Linux with `kokoroctl restart` (or
`systemctl --user restart kokoro-server`), on Windows as described in
`docs/AUDIT.md` §7. For the Omarchy widget, run `omarchy restart shell`: the
shell keeps the old compiled widget code until it restarts.

### Highlighting is wrong or missing

Check the table above first. The highlighter writes `highlighter.log`, which
records the document it anchored to and the rectangle for every word. Diagnose
from that log (`docs/AUDIT.md` §8, "Round 4"). `highlighter.err` is empty when
the highlighter is healthy.

## How it works

| Piece | Platform | What it does |
|---|---|---|
| `tts_server.py` | both | Flask on 127.0.0.1:5111. Keeps the model loaded, splits text into chunks sized to stay ahead of playback, time-stretches without changing pitch, plays the audio |
| `read_aloud.ahk` | Windows | AutoHotkey v2 hotkeys. Copies the selection, saving and restoring the clipboard, and POSTs it |
| `read_aloud.sh` | Linux | The same hotkeys, bound through GNOME or Hyprland. Reads the PRIMARY selection, so it never touches the clipboard |
| `highlighter.py` | Windows | Tints the spoken word in the source app, using UI Automation and a click-through layered window |
| `overlay.py` | both | The caption strip. Polls `/now` and renders the sentence being read |
| `tray.py` | both | The Settings panel over `/config`. Windows also gets a tray icon (`tray_win32.py`) |
| `kokoro_settings.py` | both | The voices, the speed rules and `settings.json`, shared by the Settings panel and the widget |
| `extension/` | both | Chromium in-page highlighter, loaded unpacked |
| `linux/kokoroctl` | Linux | Off / CPU / GPU / restart / status, voice and speed, for the bar widget, keybinds and terminals |
| `linux/omarchy/` | Omarchy | The bar widget (a Quickshell plugin) |

On Linux, `tray.py` and `overlay.py` run on the system Python and its Tk.
Only the server needs the Python 3.12 venv.

Everything talks to the server over localhost. The server never knows the
highlighter, caption strip or widget exist, so closing any of them affects
nothing else.

The engineering docs in [docs/](docs/README.md) hold the measurements and the
decisions behind all of this. Start with `docs/AUDIT.md` §4 and §6 before
changing anything.

## Credits & third-party licenses

The code in this repository is MIT-licensed (see [LICENSE](LICENSE)). It
builds on the work below, and redistributes none of it: uv or pip installs
the libraries from PyPI and PyTorch's index, the model downloads from Hugging
Face, and the programs come from your own system.

**Not affiliated with hexgrad or the Kokoro project.** "Kokoro" in the name
says which model this uses, not who made it.

| Component | License | Used for | How it gets to you |
|---|---|---|---|
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), weights and voices | Apache-2.0 | The voice | Hugging Face, during setup (on Windows, on the first run) |
| [kokoro](https://github.com/hexgrad/kokoro) and [misaki](https://github.com/hexgrad/misaki), by hexgrad | Apache-2.0 | Running the model, turning text into phonemes | PyPI |
| [eSpeak NG](https://github.com/espeak-ng/espeak-ng) | GPL-3.0 | Phonemes for words misaki's dictionary doesn't know | PyPI, as a library inside `espeakng-loader`. misaki always loads that copy, never a system install |
| [phonemizer-fork](https://pypi.org/project/phonemizer-fork/) | GPL-3.0+ | misaki's interface to eSpeak NG | PyPI |
| [PyTorch](https://pytorch.org) | BSD-3-Clause | Running the model | PyTorch's own index, download.pytorch.org |
| NVIDIA CUDA libraries | NVIDIA proprietary | GPU support, CUDA installs only | PyPI, pulled in by the CUDA build of PyTorch |
| [spaCy](https://spacy.io) and `en_core_web_sm` | MIT | English text analysis inside misaki | spaCy from PyPI, `en_core_web_sm` from spaCy's GitHub releases, pinned with its hash |
| [num2words](https://github.com/savoirfairelinux/num2words) | LGPL-2.1 | Reading numbers as words | PyPI |
| Flask, NumPy, sounddevice (with PortAudio), transformers, huggingface_hub, comtypes | BSD-3-Clause, MIT, Apache-2.0 | Server, audio output, model download, Windows UI Automation | PyPI |
| [uv](https://github.com/astral-sh/uv) | MIT or Apache-2.0 | Installs Python and the packages on Linux | Your distribution, or a pinned release from GitHub that setup downloads |
| CPython 3.12, built by [python-build-standalone](https://github.com/astral-sh/python-build-standalone) | PSF-2.0, plus the licenses of the libraries it bundles | Runs the server on Linux | GitHub, downloaded by uv during setup |
| Tcl/Tk, through Python's tkinter | TCL (BSD-style) | Settings panel and caption strip | Your Python or your distribution |
| [AutoHotkey v2](https://www.autohotkey.com/) | GPL-2.0 | Windows hotkeys | You install it |
| wl-clipboard, jq, curl, notify-send (libnotify), python-xlib | GPL-3.0+, MIT, curl, LGPL-2.1+, LGPL-2.1+ | Linux hotkey script | Your distribution |
| [Omarchy](https://omarchy.org)'s shell, built on [Quickshell](https://quickshell.org) | MIT, LGPL-3.0 | Hosts the bar widget, whose panel uses the shell's own components | Part of Omarchy |
| [input-remapper](https://github.com/sezanzeb/input-remapper), optional | GPL-3.0+ | The mouse-button trigger on Linux | Your distribution or the AUR |

Because nothing above is bundled, using, modifying or forking this repository
carries no obligation beyond the MIT license. That changes for anyone who
**redistributes a bundle** that includes these parts, such as a zip with the
virtualenv, a frozen executable or a container image:

- eSpeak NG and phonemizer-fork are GPL-3.0, so a bundle containing them must
  be distributed under the GPL-3.0's terms, with their source code available.
- The Apache-2.0, BSD and MIT components need their license texts and notices
  included.
- The NVIDIA libraries may only be redistributed under NVIDIA's own terms.
