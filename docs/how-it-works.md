# How it works

- [The pieces](#the-pieces)
- [What happens on a read](#what-happens-on-a-read)
- [Speed](#speed)
- [CPU or GPU](#cpu-or-gpu)
- [What gets read](#what-gets-read)
- [Who may talk to the server](#who-may-talk-to-the-server)
- [Pinned and offline](#pinned-and-offline)

Every number here was measured, on the machine named next to it. The full
record, with the reasoning and the dead ends, is in [AUDIT.md](AUDIT.md).

## The pieces

| Piece | Platform | What it does |
|---|---|---|
| `tts_server.py` | both | Flask on 127.0.0.1:5111. Keeps the model loaded, splits text into chunks sized to stay ahead of playback, time-stretches without changing pitch, plays the audio |
| `read_aloud.ahk` | Windows | AutoHotkey v2 hotkeys. Copies the selection, saving and restoring the clipboard, and sends it to the server |
| `read_aloud.sh` | Linux | The same hotkeys, bound through GNOME or Hyprland. Reads the PRIMARY selection, so it never touches the clipboard |
| `highlighter.py` | Windows | Tints the spoken word in the source app, using UI Automation and a click-through layered window |
| `overlay.py` | both | The caption strip. Polls `/now` and shows the sentence being read |
| `tray.py` | both | The Settings panel. Windows also gets a tray icon (`tray_win32.py`) |
| `kokoro_settings.py` | both | The voices, the speed rules and `settings.json`, shared by the Settings panel and the widget |
| `extension/` | both | Chromium in-page highlighter, loaded unpacked |
| `linux/kokoroctl` | Linux | Off / CPU / GPU / restart / status, voice and speed, setup and uninstall |
| `linux/omarchy/` | Omarchy | The bar widget, a Quickshell plugin |

On Linux, `tray.py` and `overlay.py` run on the system Python and its Tk.
Only the server needs the Python 3.12 venv.

Everything talks to the server over localhost. The server never knows that
the highlighter, the caption strip or the widget exist, so closing any of them
affects nothing else.

## What happens on a read

1. **The hotkey gets the text.**
   - On Linux, `read_aloud.sh` reads the PRIMARY selection, the text you just
     selected, without copying it. Wayland gives a background process no way
     to send Ctrl+C to another app, and this way the clipboard is never
     touched.
   - On Windows, AutoHotkey copies the selection and then puts your previous
     clipboard back.

   Nothing polls the clipboard.
2. **The server splits the text into chunks,** at commas and full stops
   wherever it can, so pauses fall where the sentence has them. The first
   chunk is short, about 2 s of audio (`first_chunk_audio`), so speech starts
   quickly.
3. **It starts playing the first chunk** while it synthesizes the rest. On
   an RTX 4060 Ti, speech starts 0.06–0.1 s after the server gets the text; on
   that machine's i5-12400F, 0.35–0.5 s.
4. **Kokoro reports when each word starts.** The server publishes the word
   being spoken, and the sentence around it, at `GET /now`. The caption strip,
   the Windows highlighter, the extension and the Omarchy widget all read it
   from there.

## Speed

Your reading speed is split in two:
- **What Kokoro itself is asked for** (`model_speed`), kept at 1.3 or below.
  Past about 1.3 Kokoro cannot articulate: onsets and endings garble (AUDIT
  §6).
- **A time-stretch after synthesis** (`playback_speed`), which keeps the
  voice's pitch. It does the rest, up to a reading speed of 3×.

Playback uses up `playback_speed` seconds of audio per second, and synthesis
has to produce them faster than that, or long reads stall. So the top speed
you can listen at is set by your hardware.

The server measures how fast your machine synthesizes during real reads.
It keeps that figure per device, GPU and CPU separately, in
`calibration.json`. The Settings panel and the Omarchy panel use it to show
the fastest speed your machine keeps up with, and warn when you set more.

## CPU or GPU

Measured synthesis speeds, against the default reading speed (1.8x
playback):

| Machine | Measured synthesis | Against 1.8x playback |
|---|---|---|
| GTX 1650 laptop, Windows (2026-08-11) | 25.0x realtime | 14x headroom |
| Same laptop, Fedora (2026-08-18) | 13.3x realtime | 7.4x headroom |
| Same laptop, i7-11370H, CPU build | 1.77x realtime | Below break-even, stalls |
| RTX 4060 Ti desktop, Omarchy (2026-10-07) | 20–87x realtime per chunk, short chunks lowest | 11x or more |
| Same desktop, i5-12400F, GPU hidden | 4.4–5.3x realtime | 2.4x headroom |

- **Linux is slower than Windows on the same laptop.** The cause is not known
  yet, but both are well above break-even.
- **Your own figure** is `measured_rt` in `GET /config`.
- **On a slow CPU,** lower the reading speed until the panel's warning
  clears.
- **A GPU install can still run on the CPU** when you want the VRAM back. See
  [omarchy.md](omarchy.md).

## What gets read

Kokoro Read-Aloud reads English. Other languages written in the Latin
alphabet, such as French, Spanish or German, are read with English
pronunciation.

**Skipped,** rather than spelled out letter by letter:
- **Other scripts:** Japanese, Chinese, Korean, Cyrillic, Greek, Hebrew,
  Arabic, Hindi and the rest. In mixed text, the English is still read.
- **Emoji,** with their skin tones and flags. Kokoro would read their names,
  such as "grinning face".

**Kept:**
- Symbols with a spoken name, such as ©, ™, °C, €, ½, x² and ≈.
- A lone Greek letter such as π or Δ, which is read by its name.
- The digit in a keycap emoji: 1️⃣ is read as "one".

On Linux, a selection with nothing readable in it shows a notification.

Reading the other languages Kokoro supports, each with its own
pronunciation, is planned for v0.3.0.

## Who may talk to the server

The server listens only on 127.0.0.1, but that alone does not keep web pages
out. Every browser tab can send requests to 127.0.0.1 too. In a real Chromium
(AUDIT 2026-10-18), a page from another site could:
- make the server speak, and change its settings, with a "simple" request
  that browsers send without asking;
- read the text being read aloud, through DNS rebinding: a hostname that
  resolves to 127.0.0.1 makes the server look same-origin to the browser.

The full account, with the headers Chromium sent and the before and after
results, is in [security.md](security.md). So the server refuses:
- any `Host` other than `127.0.0.1:5111` or `localhost:5111`;
- any request from a web page: an `Origin` header, or a `Sec-Fetch-Site` of
  `cross-site` or `same-site`;
- any POST that is not JSON, the one kind of POST a page cannot send without
  asking the server first.

It never grants that request (no CORS headers, anywhere), and no GET changes
anything.

Every client of ours is a local program, or the extension's background
worker, so they all pass. `tests/test_server.py` replays each attack with
the headers Chromium sent:

```bash
~/.local/share/kokoro-read-aloud/venv/bin/python -B tests/test_server.py   # Linux
env\Scripts\python.exe -B tests\test_server.py                             # Windows
```

Any program running as you can still use the server, as with any local
service: there is no password.

## Pinned and offline

- **uv:** setup downloads it only if you have none, or one older than
  0.12.10, and checks the download against a pinned SHA-256.
- **The packages** are installed at the versions and hashes in `uv.lock`;
  uv refuses any package whose hash does not match (checked by tampering
  with a lock file).
  On Windows, the requirements files carry the same hashes, and pip installs
  in hash-checking mode.
- **The model files** (`model_files.py`) are pinned to one revision of
  Kokoro-82M, and each one is checked against its SHA-256 before it gets its
  real name.
- **Once the model files are present,** the server loads them by path with
  Hugging Face's offline mode on. It then makes no network connection: on
  Linux it was run in a network namespace with no network at all, and still
  loaded and spoke.
- **On Windows,** until you run `model_files.py fetch`, the model comes
  from Hugging Face's cache, which checks online at every start.
