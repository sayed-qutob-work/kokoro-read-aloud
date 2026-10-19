# Windows test checklist

A retest of the current version on Windows 11. Windows was last tested in
mid-August 2026, around v0.1.0-beta. Since then, these have changed:
- the pinned requirements files;
- the request guard in the server;
- where the model is loaded from;
- the settings panel's shared code;
- the caption strip, rebuilt on Linux and never run on Windows.

First run on 2026-10-19, on a fresh Windows 11 in a VM without a GPU; the
results are in [AUDIT.md](AUDIT.md) §8.

Work top to bottom: later items assume the earlier ones passed. Each item
says what to do, what should happen, and where to look if it doesn't. All
the logs are in the code folder.

Commands are for **Windows PowerShell 5.1**: use `curl.exe`, not `curl`, and
run one command per line (no `&&`).

## Results

Fill this in as you go, and send it back with the log lines of anything that
failed.

| # | Item | Result | Notes |
|---|---|---|---|
| 0 | Before you start | | |
| 1 | Install from the pinned requirements | | |
| 2 | The model files, offline | | |
| 3 | Start the server | | |
| 4 | The server's request checks | | |
| 5 | Hotkeys | | |
| 6 | AutoHotkey passes the request checks | | |
| 7 | The settings panel | | |
| 8 | The tray icon | | |
| 9 | Caption strip | | |
| 10 | In-place highlighting: Firefox | | |
| 11 | In-place highlighting: Notepad | | |
| 12 | Start at login | | |
| 13 | Uninstall | | |

## 0. Before you start

- [ ] **Do:** note the facts for the report:
  - the Windows version (`winver`);
  - the GPU: run `nvidia-smi`, or write "none" (a VM usually has none);
  - the Python versions: run `py -0p`. A 3.12 must be listed.

  Check that AutoHotkey v2 is installed: `AutoHotkey64.exe` in
  `%LOCALAPPDATA%\Programs\AutoHotkey\v2\` or
  `%ProgramFiles%\AutoHotkey\v2\`.

  Install the Visual C++ runtime, which torch needs and a fresh Windows
  lacks: `winget install Microsoft.VCRedist.2015+.x64`. On a PC that
  already has it, winget says so and changes nothing.
- [ ] **Do:** if an older copy is installed, for example in `C:\kokoro`, stop
  it first:
  1. Quit it from its tray icon.
  2. Remove its shortcut from `shell:startup`.
  3. Check that nothing still holds the port:
     `Get-NetTCPConnection -LocalPort 5111 -State Listen -ErrorAction SilentlyContinue`.
- **Expect:** the port check prints nothing.
- **If not:** find and stop the old server:

  ```powershell
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*tts_server.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  ```

## 1. Install from the pinned requirements

- [ ] **Do:**

  ```powershell
  git clone https://github.com/sayed-qutob-work/kokoro-read-aloud.git
  cd kokoro-read-aloud
  git rev-parse --short HEAD
  py -3.12 -m venv env
  env\Scripts\python.exe -V
  env\Scripts\python.exe -m pip install -r requirements-cuda.txt
  ```

  Without an NVIDIA GPU, use `requirements.txt` instead of
  `requirements-cuda.txt`. Never install both.

  Then check which torch build you got:

  ```powershell
  env\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"
  ```
- **Expect:**
  - `python -V` prints `Python 3.12.x`, and the commit is the one you meant
    to test (write it in the report);
  - pip ends with `Successfully installed ...`, with no hash error;
  - torch prints `2.13.0+cu126 True` with an NVIDIA GPU, or `2.13.0+cpu False`
    with the CPU file.
- **If not:**
  - **`THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE`:**
    note which package and the two hashes. That is a pinning bug.
  - **`No matching distribution found for kokoro`:** the venv is not 3.12.
    Delete `env` and recreate it with `py -3.12`.
  - **`2.13.0+cu126 False` on an NVIDIA machine:** run `nvidia-smi` and note
    the driver version.

## 2. The model files, offline

On Windows this has never been tested.

- [ ] **Do:**

  ```powershell
  env\Scripts\python.exe model_files.py fetch
  env\Scripts\python.exe model_files.py verify
  ```
- **Expect:**
  - `fetch` shows a progress line and ends with
    `ok: 30 downloaded, 0 already in place`, about 326 MiB, into
    `models\Kokoro-82M`;
  - `verify` prints `ok`.
- **If not:** copy the error. A line starting `missing or wrong:` names the
  files that failed their size or SHA-256 check.

## 3. Start the server

- [ ] **Do:** `env\Scripts\python.exe tts_server.py` in its own PowerShell
  window, and leave it open. This window is the server log for items 3 to
  11. Then, from another window:

  ```powershell
  curl.exe -s http://127.0.0.1:5111/config
  ```
- **Expect:**
  - **in the server window:**
    - a `settings.json:` line;
    - `[kokoro] model: ...\models\Kokoro-82M`, which means it loads from
      disk, offline;
    - `[kokoro] warmed up in ... on cuda`, or `on cpu`;
    - `[kokoro] ready on http://127.0.0.1:5111`;
    - no line about "unauthenticated requests to the HF Hub";
  - **`curl.exe`** prints JSON with `"device":"cuda"` (or `"cpu"`).
- **If not:**
  - **`[kokoro] model: Hugging Face cache (online)`:** item 2 did not leave
    complete files. Run `model_files.py verify`.
  - **The window closes, or shows a traceback:** copy it.
  - **"address already in use":** an old server is running. See item 0.
  - **`OSError: [WinError 126]` loading `torch\lib\c10.dll`:** the Visual
    C++ runtime is missing. See item 0.

## 4. The server's request checks

These are the checks added on 2026-10-18, which refuse requests from web
pages. This runs the same tests as on Linux.

- [ ] **Do:** with the server still running:

  ```powershell
  env\Scripts\python.exe -B tests\test_server.py
  ```
- **Expect:** the last lines say `Ran 20 tests` and `OK`. The `refused (...)`
  lines printed along the way are the tests' attacks being refused, which is
  correct. The two `LiveServerTests` run, rather than being skipped, because
  the server is up.
- **If not:** copy the whole output. A `FAIL` names the test and the status
  code it got.

## 5. Hotkeys

- [ ] **Do:** double-click `read_aloud.ahk`. A green "H" AutoHotkey icon
  appears in the tray.
- [ ] **Ctrl+Alt+R, reading the selection:**
  1. Copy the word `canary` to the clipboard.
  2. In Notepad, type or paste a few sentences, select them, and press
     **Ctrl+Alt+R**.
  3. When it has finished, press Ctrl+V somewhere.
- **Expect:** speech starts within about half a second, and Ctrl+V pastes
  `canary`: the hotkey put your clipboard back.
- [ ] **Ctrl+Alt+T, reading the clipboard:** copy a sentence and press
  **Ctrl+Alt+T**.
- **Expect:** that sentence is read as-is.
- [ ] **Ctrl+Alt+S, stop:** press it while something is being read.
- **Expect:** it stops within a moment.
- **If not:**
  - **A "Read-aloud: cannot reach tts_server.py" box:** the server is not
    running.
  - **Nothing happens and no box appears:** see item 6.
  - **It reads the wrong text:** note the app and what was read instead.

## 6. AutoHotkey passes the request checks

AutoHotkey sends its requests through WinHTTP. Reading its code says that
passes the new checks. This item confirms it.

- [ ] **Do:** after item 5, look through the server window.
- **Expect:**
  - `POST /speak HTTP/1.1" 200` and `POST /stop HTTP/1.1" 200` lines for
    your presses;
  - no line starting with `refused (`.
- **If not:** a `refused (<reason>): POST /speak ...` line means AutoHotkey
  fails a check. Copy the whole line: it shows the `Host`, `Origin`,
  `Sec-Fetch-Site` and content type the server saw. AutoHotkey does not look
  at the answer, so a refused request is silent on its side.

## 7. The settings panel

- [ ] **Do:** `env\Scripts\python.exe tray.py` in a third window. Then
  left-click the new tray icon, which may be in the hidden-icons overflow
  (`^`). The Settings panel opens.
- [ ] **Voice:** open the voice list.
- **Expect:** 28 voices, including `am_santa`, which was missing before.
- [ ] **Test:** pick `bf_emma`, then press **Test**.
- **Expect:** "Speaking a sample..." appears, and the sample is read in a
  British voice.
- [ ] **The speed warning:** drag Reading speed to the far right.
- **Expect:** past this machine's limit a red line appears: "Above this
  machine's limit...", or "Close to this machine's limit...". Drag back down
  until it clears. On a VM's CPU the limit may be low.
- [ ] **Rescan:** press it next to "Play through". This is now a POST.
- **Expect:** "Rescanning audio devices...", then "Audio devices rescanned.",
  and the list is filled. The server window shows `POST /devices ... 200`.
- [ ] **Save:** press it.
- **Expect:** "Saved - these settings now survive a restart.", and
  `settings.json` appears next to the code with `"voice": "bf_emma"`.
- **If not:** look in `tray.log` next to the code, and for any `refused (`
  line in the server window.

## 8. The tray icon

- [ ] **Do:** right-click the tray icon.
- **Expect:**
  - a grey status line such as `Server: running - 2.07x, bf_emma`, and an
    `Output:` line;
  - then Settings..., Stop speaking, Reconnect audio device, Restart TTS
    server, Restart highlighter, Open Kokoro folder and Quit Kokoro.
- [ ] **Each item:**
  - **Stop speaking,** during a read: the read stops.
  - **Reconnect audio device:** the server window shows `POST /devices ...
    200`.
  - **Restart TTS server:** the server in your window stops, because the
    tray ends it. A new, hidden server starts within about 10 s, writing to
    `server.log`. Check `curl.exe -s http://127.0.0.1:5111/config` shows
    `"voice":"bf_emma"`: the setting survived the restart.
  - **Open Kokoro folder:** Explorer opens the code folder.
  - **Quit Kokoro:** do this last. The tray icon goes, and the server and
    highlighter stop.
- **If not:** see `tray.log`, and `server.log` for the restarted server.

From here on, the server's output is in `server.log`. Start it again if you
quit it:

```powershell
cmd /c "env\Scripts\python.exe tts_server.py > server.log 2>&1"
```

Or simply double-click `start_tts.vbs`, which item 12 tests.

## 9. Caption strip

The current version of the strip was rebuilt on Linux and has never run on
Windows.

- [ ] **Do:** `env\Scripts\python.exe overlay.py` in a window, so you see
  any error. Then read a paragraph with Ctrl+Alt+R.
- **Expect:**
  - a strip at the bottom of the main monitor shows the passage, and keeps
    up with the voice;
  - it hides about 0.7 s after speech stops;
  - dragging it moves it, and right-clicking it closes it.
- [ ] **Do:** in Settings, change Caption strip > Look, and Save.
- **Expect:** "Saved - restarting the caption strip.", and the next read
  uses the new look.
- **If not:** the traceback in that window, and `overlay.log` and
  `overlay.err` next to the code. With two monitors, also note which one it
  used.

## 10. In-place highlighting: Firefox

- [ ] **Do:** start the highlighter with its diagnostic log:

  ```powershell
  $env:KOKORO_HL_DEBUG = "$PWD\highlighter.log"
  env\Scripts\python.exe highlighter.py 2> highlighter.err
  ```

  Then, in Firefox, open a Wikipedia article. Select one paragraph, and press
  Ctrl+Alt+R.
- **Expect:** each word is tinted in the page as it is spoken, and the tint
  follows the voice to the end of the paragraph.
- **If not:** diagnose from `highlighter.log`; don't guess.
  - **`MODE ...`:** should say `findtext` for Firefox.
  - **`GIVEUP`:** the read never anchored to the page.
  - **Per-word `found=0` lines:** a word could not be located.
  - **`found=1 rects=[]`:** a word was found, but Firefox gave no position.
  - **`WRONG`:** the tint was drawn on the wrong word.
  - **`highlighter.err`:** has a traceback if it crashed before logging.

  Note `SUMMARY ... painted=` too, but it is not a correctness measure: it
  counts drawn rectangles, not right ones.

## 11. In-place highlighting: Notepad

- [ ] **Do:** with the highlighter still running, select a few lines in
  Notepad and press Ctrl+Alt+R.
- **Expect:** each word is tinted as it is spoken.
- **If not:** `highlighter.log`, as in item 10. A multi-line selection is a
  known weak spot, so also try a single line and note both.

## 12. Start at login

- [ ] **Do:**
  1. Quit everything: Quit Kokoro from the tray, close the windows, and exit
     AutoHotkey from its "H" icon.
  2. Double-click `start_tts.vbs`.
  3. If it works, put a shortcut to it in `shell:startup`, sign out, and
     sign in.
- **Expect:**
  - after the double-click, and again after signing in: the AutoHotkey "H"
    and the Kokoro tray icon appear, and Ctrl+Alt+R reads;
  - `server.log` is fresh;
  - this lists the server, the highlighter and the tray, two `python.exe`
    for each, which is normal:

    ```powershell
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
      Select-Object ProcessId, CommandLine
    ```
- **If not:**
  - **An "AutoHotkey v2 not found" box:** it looked in the two paths from
    item 0.
  - **Otherwise:** look in `server.log`, `highlighter.err` and `tray.err`.

## 13. Uninstall

Follows [install.md](install.md#removing).

- [ ] **Do:**
  1. Quit Kokoro from the tray, and exit AutoHotkey.
  2. Check what is in Hugging Face's cache:
     `env\Scripts\hf.exe cache ls`.
  3. If it lists `hexgrad/Kokoro-82M`, remove it:
     `env\Scripts\hf.exe cache rm model/hexgrad/Kokoro-82M -y`. It is
     listed only if the model was loaded online before item 2.
  4. Delete the shortcut in `shell:startup`.
  5. Delete the folder.
- **Expect:**
  - `Get-NetTCPConnection -LocalPort 5111 -State Listen -ErrorAction SilentlyContinue`
    prints nothing;
  - no `python.exe` runs from the old folder;
  - `%USERPROFILE%\.cache\huggingface\hub` has no `models--hexgrad--Kokoro-82M`.
- **If not:** note what was left behind, and where.

## Optional, if there is time

- **The Chromium extension:**
  1. In Chrome or Edge, open `chrome://extensions` (or `edge://extensions`),
     turn on Developer mode, and choose "Load unpacked" with the `extension`
     folder.
  2. Read a paragraph of a web page.
  - **Expect:** the word is marked inside the page.
  - **If not:** check the extension's worker console, under "Inspect views"
    on the extensions page.
- **Terminal text:** turn on copy-on-select in Windows Terminal
  (`"copyOnSelect": true`). Then select text in a terminal and press
  Ctrl+Alt+R.
