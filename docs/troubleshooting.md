# Troubleshooting

- [The logs](#the-logs)
- [Reads stall or stutter partway through](#reads-stall-or-stutter-partway-through)
- [The hotkey says "Kokoro is off" or "still loading"](#the-hotkey-says-kokoro-is-off-or-still-loading)
- ["No matching distribution found for kokoro"](#no-matching-distribution-found-for-kokoro)
- [`WinError 126` loading `c10.dll` (Windows)](#winerror-126-loading-c10dll)
- ["This app can't run on your PC" for `python.exe` (Windows)](#this-app-cant-run-on-your-pc-for-pythonexe)
- [Changes to the server or the widget don't show up](#changes-to-the-server-or-the-widget-dont-show-up)
- [A script of your own gets "refused"](#a-script-of-your-own-gets-refused)
- [Highlighting is wrong or missing (Windows)](#highlighting-is-wrong-or-missing)
- [Reporting a bug](#reporting-a-bug)

## The logs

Read `server.log` first. It is truncated at each server start, so it always
covers the current run.
- **Linux:** `kokoroctl log` opens it. It lives in
  `~/.local/state/kokoro-read-aloud/`, with the other logs.
- **Windows:** it is next to the code.

| Log | Records |
|---|---|
| `server.log` | The server: model load, device, requests (except the frequent `/now`), errors, refused requests |
| `read_aloud.log` (Linux) | Every hotkey press, and a short excerpt of the selection it read. Cut back past 256 KB |
| `setup.log` (Linux) | The last setup started from the bar panel or with `--background`; `kokoroctl log setup` opens it |
| `overlay.log`, `overlay.err` | The caption strip |
| `highlighter.log`, `highlighter.err` (Windows) | The in-place highlighter. `highlighter.err` is empty when it is healthy |

## Reads stall or stutter partway through

Synthesis is not keeping up with playback. Lower the reading speed until
the warning in the Settings panel (or under the slider in the Omarchy panel)
clears, or use the GPU. See [how-it-works.md](how-it-works.md#cpu-or-gpu).

## The hotkey says "Kokoro is off" or "still loading"

On Linux, Kokoro was turned off from the bar or with `kokoroctl off`, or it
is still loading its model, which takes about 6 s. Turn it back on from the
bar icon, or with `kokoroctl gpu` (or `kokoroctl cpu`).

## "No matching distribution found for kokoro"

```text
ERROR: Ignored the following versions that require a different python version:
  0.9.4 Requires-Python >=3.10,<3.13
ERROR: Could not find a version that satisfies the requirement kokoro==0.9.4
  (from versions: 0.2.1, ... 0.7.16)
ERROR: No matching distribution found for kokoro==0.9.4
```

The venv is on Python 3.13 or newer. The `Ignored the following versions`
line is the real message: pip filtered out every 0.8.x and 0.9.x wheel because
of `Requires-Python`, which left only the 0.7.x line. Rebuild the venv on
3.12.

Don't relax the pin to a 0.7.x `kokoro`. That is a different model and voice
API, and every number in [AUDIT.md](AUDIT.md) was measured on the versions
pinned in `uv.lock`.

## `WinError 126` loading `c10.dll`

```text
Microsoft Visual C++ Redistributable is not installed, this may lead to the DLL load failure.
...
OSError: [WinError 126] The specified module could not be found. Error loading
"...\env\Lib\site-packages\torch\lib\c10.dll" or one of its dependencies.
```

Windows is missing the Visual C++ runtime, which torch's DLLs need. A fresh
Windows does not include it. Install it, then start the server again; the
venv needs no reinstall:

```powershell
winget install Microsoft.VCRedist.2015+.x64
```

Or download it from <https://aka.ms/vs/17/release/vc_redist.x64.exe>.

## "This app can't run on your PC" for `python.exe`

Running `env\Scripts\python.exe` shows that dialog, or prints
`Access is denied.`, and `dir env\Scripts\python.exe` shows 0 bytes.

A line copied from the console together with its prompt was pasted back in:

```text
C:\Users\you\kokoro-read-aloud>env\Scripts\python.exe tts_server.py
```

The `>` after the prompt is a redirection, so the shell writes into
`env\Scripts\python.exe` and empties it. In Command Prompt a right-click
pastes at once, which makes this easy to do by accident; copy with a
selection and Ctrl+C instead. To repair the venv without reinstalling its
packages:

```powershell
py -3.12 -m venv --upgrade env
```

## Changes to the server or the widget don't show up

Editing `tts_server.py` does nothing to a server that is already running. A
second copy dies silently with "address in use" while the old one keeps
serving. Restart it instead:
- **Linux:** `kokoroctl restart`, or
  `systemctl --user restart kokoro-server`.
- **Windows:** see [AUDIT.md](AUDIT.md) §7.

For the Omarchy widget, run `omarchy restart shell`. The shell keeps the old
compiled widget code until it restarts.

## A script of your own gets "refused"

The server only accepts local programs. `server.log` says why it refused a
request, as `refused (<reason>): ...`:

| Reason | Fix |
|---|---|
| `content-type` | Send POSTs as JSON: `-H 'Content-Type: application/json'`, with a JSON body, even an empty `{}` |
| `host` | Call `127.0.0.1:5111` or `localhost:5111`, not another name for this machine |
| `origin`, `sec-fetch-site` | The request came from a web page, which is refused on purpose |

See [how-it-works.md](how-it-works.md#who-may-talk-to-the-server).

## Highlighting is wrong or missing

Windows only. Check the table in [windows.md](windows.md#in-place-highlighting)
first.

The highlighter writes `highlighter.log`: the document it anchored to, and the
rectangle for every word. Diagnose from that log, as described in
[AUDIT.md](AUDIT.md) §8, "Round 4". A crash before logging starts lands in
`highlighter.err`.

## Reporting a bug

[Open a bug report](https://github.com/sayed-qutob-work/kokoro-read-aloud/issues/new?template=bug_report.yml).
The form asks for:
- your platform, GPU and Python version;
- `kokoroctl status`;
- the logs. It says what in them to check before you paste them.
