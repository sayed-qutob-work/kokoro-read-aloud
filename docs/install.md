# Installing, updating and removing

The [README](../README.md#install) has the short version for each platform.
This page has the details: what each step does, what it costs, where the
files go, and how to undo it.

- [Omarchy](#omarchy)
- [Fedora](#fedora)
- [What setup does](#what-setup-does) (both Linux platforms)
- [Windows](#windows)
- [Updating](#updating)
- [Removing](#removing)
- [Optional extras](#optional-extras)

## Omarchy

Tested on Omarchy 4.0.4 (Arch, Hyprland 0.56.2) with an NVIDIA GPU, as a
clean install by a new user from the GitHub URL.

```bash
sudo pacman -S --needed tk portaudio wl-clipboard jq curl libnotify
omarchy plugin add https://github.com/sayed-qutob-work/kokoro-read-aloud --enable
```

On a fresh Omarchy, `tk` is the only one of those packages that is missing.

`omarchy plugin add` first warns that plugins run unsandboxed inside the
shell, and asks you to confirm. With `--enable` it then asks which bar section
to use. A Kokoro icon appears there, as five dim dots.

Click it, and the panel offers setup:
- **GPU build,** about 4 GB, the default on a machine with an NVIDIA GPU;
- **CPU build,** about 0.7 GB.

The panel shows each step, and a notification says when Kokoro is ready. From
a terminal, run this instead:

```bash
~/.config/omarchy/plugins/io.github.sayed-qutob-work.kokoro-read-aloud/linux/kokoroctl setup
```

**The hotkeys.** Setup binds Ctrl+Alt+R, T and S in
`~/.config/hypr/kokoro.lua`. That file is loaded by one `dofile(...)` line,
which setup adds to `~/.config/hypr/hyprland.lua` after backing that file up.
If you had already bound `read_aloud.sh` by hand, setup lists where. Remove
those binds, or every press fires twice.

**`sudo` needs a terminal** for its password prompt. In a shell without one,
such as a script, an AI agent or Claude Code's `!` prefix, use `pkexec`
instead: Omarchy's shell shows the password dialog.

## Fedora

Kokoro Read-Aloud runs on Fedora 44 with GNOME on Wayland, installed the
earlier, manual way; it was last run there on 2026-09-30. `kokoroctl setup` does the same steps there
as on Omarchy, but has not been run on Fedora yet.

```bash
sudo dnf install python3-tkinter python3-xlib portaudio wl-clipboard jq curl libnotify
git clone https://github.com/sayed-qutob-work/kokoro-read-aloud.git
cd kokoro-read-aloud
linux/kokoroctl setup
```

**Why `python3-xlib`.** GNOME 50.5 stopped giving `wl-paste` the focus it
needs to read the selection. So `read_aloud.sh` reads it through Xwayland
instead (`linux/xselection.py`, on the system Python). Without the package it
falls back to `wl-paste`, and reads stall for seconds.

**The hotkeys.** Setup cannot bind GNOME shortcuts, so it prints the
commands. Bind them in Settings > Keyboard > View and Customize Shortcuts >
Custom Shortcuts:

| Shortcut | Command |
|---|---|
| Ctrl+Alt+R | `/path/to/kokoro-read-aloud/read_aloud.sh selection` |
| Ctrl+Alt+T | `/path/to/kokoro-read-aloud/read_aloud.sh clipboard` |
| Ctrl+Alt+S | `/path/to/kokoro-read-aloud/read_aloud.sh stop` |

**There is no tray icon on GNOME.** GNOME removed the notification area, and
an indicator would need the third-party AppIndicator extension. The settings
panel opens from the app grid ("Kokoro Settings"), or with
`kokoroctl settings`.

## What setup does

`kokoroctl setup` installs everything outside the code folder, and never
writes to that folder. It needs no root; only the system packages above do.

1. **Checks the system packages.** If any are missing, it stops and prints
   the `pacman` or `dnf` command that installs them.
2. **Gets uv.** It uses yours if it is 0.12.10 or newer. Otherwise it
   downloads uv 0.12.24 and checks it against a pinned SHA-256.
3. **Installs Python 3.12 and the server's packages** at the versions and
   hashes recorded in `uv.lock`. It picks the GPU (CUDA) build when
   `nvidia-smi` finds a GPU, and the CPU build otherwise. `--cuda` and `--cpu`
   override that.
4. **Downloads the voice model:** 326 MiB from Hugging Face, every file
   checked against its pinned SHA-256. From then on the server loads it from
   disk and makes no network connection.
5. **Installs the rest:**
   - autostart for the server;
   - the hotkeys, on Hyprland;
   - a "Kokoro Settings" launcher entry;
   - the `kokoroctl` command in `~/.local/bin`.

   Then it starts the server.

| | GPU build | CPU build |
|---|---|---|
| Download | about 4 GB | about 0.7 GB |
| On disk | about 7 GB | about 2 GB |
| First sound after the hotkey | about 0.1 s | about 0.4 s |

Most of the time is the download. A fresh NVIDIA install took 1 min 46 s on a
fast connection, and 31 minutes at 2 MB/s.

The CPU build cannot use the GPU later. `kokoroctl setup --cuda` switches to
the GPU build.

Everything goes into three folders:

| Folder | Holds |
|---|---|
| `~/.local/share/kokoro-read-aloud/` | uv and its own cache, Python, the venv and the model |
| `~/.config/kokoro-read-aloud/` | Your settings |
| `~/.local/state/kokoro-read-aloud/` | The logs and the speed calibration |

Running setup again repairs a broken or half-finished install, and keeps
what is already there. A setup started from the bar panel runs in the
background; `kokoroctl log setup` shows its output.

## Windows

> Retested on 2026-10-19 on a fresh Windows 11, in a VM without a GPU.
> The GPU path was last tested in mid-August 2026.

You need:
- **[Python 3.12](https://www.python.org/downloads/).** Exactly 3.12, see
  below.
- **[AutoHotkey v2](https://www.autohotkey.com/),** per-user or
  machine-wide. The hotkeys come from it.
- **The [Visual C++ runtime](https://aka.ms/vs/17/release/vc_redist.x64.exe)
  (x64),** or `winget install Microsoft.VCRedist.2015+.x64`. torch's DLLs
  need it. Many apps and games install it, so most PCs have it, but a fresh
  Windows does not: without it the server stops at `import torch` with
  `WinError 126` (tested 2026-10-19, on a fresh Windows 11).

You don't need to install eSpeak NG: `espeakng-loader`, pulled in by
`misaki[en]`, ships the DLL and its data inside the venv.

These commands work in PowerShell and in Command Prompt:

```powershell
git clone https://github.com/sayed-qutob-work/kokoro-read-aloud.git
cd kokoro-read-aloud
py -3.12 -m venv env
env\Scripts\python.exe -V
env\Scripts\python.exe -m pip install -r requirements-cuda.txt
env\Scripts\python.exe tts_server.py
```

`python.exe -V` must print `3.12.x`. Without an NVIDIA GPU, install
`requirements.txt` instead of `requirements-cuda.txt`.

Wait for `[kokoro] ready on http://127.0.0.1:5111`. Then start
`read_aloud.ahk`, select some text and press Ctrl+Alt+R.

**To start everything at login,** put a shortcut to `start_tts.vbs` in
`shell:startup`. It launches the server, the hotkeys, the highlighter and the
tray icon, all hidden, and logs the server to `server.log`.

Things to know:
- **Python 3.12 exactly.** `kokoro` declares `Requires-Python >=3.10,<3.13`.
  On 3.13 or newer, pip filters out every usable wheel and reports a missing
  package instead (see [troubleshooting](troubleshooting.md#no-matching-distribution-found-for-kokoro)).
  A newer Python can be installed alongside, as long as the venv is not built
  from it.
- **One requirements file.** Install `requirements-cuda.txt` if you have an
  NVIDIA GPU, otherwise `requirements.txt`. Never both: Kokoro uses whichever
  torch build is present. Both pin every package to the version and SHA-256
  in `uv.lock`, so pip installs the tested set in hash-checking mode.
- **The model** downloads on the first run, about 330 MB, and the server
  then checks Hugging Face for updates at every start. To stop that, run
  `env\Scripts\python.exe model_files.py fetch` once. The server then loads
  the pinned files from `models\` and makes no network connection. Tested
  on Linux, and on Windows on 2026-10-19.
- **PowerShell traps:** use `py -3.12 -m venv`, not `python -m venv`, and
  `env\Scripts\python.exe -m pip`, never a bare `pip`.

## Updating

**Omarchy:**

```bash
omarchy plugin update io.github.sayed-qutob-work.kokoro-read-aloud
omarchy restart shell
kokoroctl setup
```

The shell keeps the old compiled widget code until it restarts. Setup
installs any new packages and restarts the server on the new code.

**Fedora:** `git pull`, then `kokoroctl setup`.

**Windows:** `git pull`, run the same `pip install -r` again, then restart
the server.

## Removing

**Omarchy:**

```bash
kokoroctl uninstall
omarchy plugin remove io.github.sayed-qutob-work.kokoro-read-aloud
```

**Fedora:** `kokoroctl uninstall`, delete the hotkeys you bound in GNOME
Settings, then delete the folder.

**Windows:**
1. Remove the model from Hugging Face's cache. It is shared with other apps,
   so it is not inside the folder:
   `env\Scripts\hf.exe cache rm model/hexgrad/Kokoro-82M -y`.
2. Delete the shortcut in `shell:startup`.
3. Delete the folder.

`kokoroctl uninstall` asks before doing anything. It removes:
- the services and the hotkeys;
- the launcher entry and the `kokoroctl` command;
- `~/.local/share/kokoro-read-aloud/` and `~/.local/state/kokoro-read-aloud/`.

It keeps your settings in `~/.config/kokoro-read-aloud/` unless you pass
`--purge`, and it never touches the code folder.

## Optional extras

**Reading terminal text on Windows.** Turn on copy-on-select:
`"terminal.integrated.copyOnSelection": true` in VS Code, or
`"copyOnSelect": true` in Windows Terminal. The hotkey cannot send Ctrl+C in a
terminal, because there it means interrupt. On Linux this is not needed: the
hotkey reads the selection itself.

**A mouse button that reads what you drag over.** Hold the button to
drag-select, and the selection is read when you let go.
- **Windows:** a Logitech G Hub macro.
- **Linux:** an `input-remapper` macro. It works at the evdev level, so it is
  safe on Wayland, and a quick tap still does what the button did before. On
  Omarchy, `input-remapper` is in the AUR: run
  `yay -S input-remapper`, then `sudo systemctl enable --now input-remapper`.

The macro and the reasons behind it are in [AUDIT.md](AUDIT.md) (2026-08-19).
Solaar cannot remap a G Pro Wireless (AUDIT §6).
