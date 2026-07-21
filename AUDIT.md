# Local Read-Aloud (Kokoro TTS) — Session Audit & Handoff

**Status:** Working, user-accepted 2026-07-16 (perceived start ~200–300ms, flow judged
smooth and seamless in daily use). **2026-07-17, two rounds:** (1) fixed "random slow
words" via final-word compression (§5 item 6) + word-highlight caption overlay
(`overlay.py` + `/now`, §8). (2) User reported reads after the first were choppy
(fast/slow churn, stalls, worst on small texts) and mandated **steady flow — chunk
boundaries only at commas/periods**. Root causes found and fixed (§5 item 7):
stopword-backup shrank chunks past the 2.24x gap constraint; density learned from
raw audio was inflated by Kokoro's fixed ~0.7s padding after every short read;
the compressor fired on unpunctuated text ends. Chunking now packs whole CLAUSE
atoms — verified live, zero gaps/cutfixes across long, short, unpunctuated and
interrupted reads. Also added in-page browser-extension highlighting (§8).
Earlier (2026-07-16) changes:
budget chunking verified (0 gaps); START cut to ~500–600ms server-side via
audio-budgeted first chunk + speech-weighted chars + boot warmup; boundary stalls
killed via silence trimming + CUT_PAUSE; markdown/TUI sanitizing; terminal reading via
clipboard path (window-aware Ctrl+Alt+R, new Ctrl+Alt+T); VS Code `copyOnSelection`
enabled.
**Machine:** Windows 11, Intel i5-12400F (6 P-cores), RTX 4060 Ti 8GB, 16GB DDR4.
**Root:** `C:\kokoro\`

---

## 1. What this is

A hotkey-driven read-aloud system. Select text anywhere → **Ctrl+Alt+R** reads it aloud
→ **Ctrl+Alt+S** stops. Ctrl+Alt+R is **window-aware** (details in §8): terminals get
the clipboard path (never a simulated Ctrl+C — that means "interrupt" there); inside
VS Code, clipboard *freshness* tells terminal selections from editor/markdown-preview
selections, so `.md` files read correctly too. **Ctrl+Alt+T** reads the clipboard
as-is anywhere. Fully local, no network, no clipboard polling (everything fires only
on an explicit hotkey).

**Why it exists:** the original goal was a read-aloud tool for long text. Piper TTS was
tried first but had two problems: mediocre pronunciation, and the wrapper around it
polled the clipboard and read *anything* that got copied. Both are solved.

### Architecture

```
[G Hub mouse macro OR keyboard]
        │  Ctrl+Alt+R
        ▼
  read_aloud.ahk          AutoHotkey v2. Saves clipboard → sends Ctrl+C →
        │                 restores clipboard → POSTs text over localhost.
        │  HTTP POST      Clipboard is never left modified.
        ▼
  tts_server.py           Flask on 127.0.0.1:5111. Model resident in memory.
        │                 Splits text → synthesizes → time-stretches → plays.
        ▼
  [speakers]
```

Two processes, both background, talking over localhost. The server holds the model
so there is no per-invocation load cost.

---

## 2. Files

| Path | Purpose |
|---|---|
| `C:\kokoro\tts_server.py` | The server. All tuning lives in its config block. |
| `C:\kokoro\read_aloud.ahk` | Hotkey front-end (AutoHotkey **v2**). |
| `C:\kokoro\highlighter.py` | **In-place** word highlighter for native apps (UIA + layered window). |
| `C:\kokoro\extension\` | Browser extension: in-page word highlighting (load unpacked). |
| `C:\kokoro\overlay.py` | Caption strip (retired from autostart — user wants no bottom transcript). |
| `C:\kokoro\start_tts.vbs` | Launches all three, hidden, logs to `server.log`. |
| `C:\kokoro\server.log` | Server output when started via the `.vbs`. **Read this first on any failure.** |
| `C:\kokoro\highlighter.log` | Highlighter diagnostics (on since 2026-07-21). Rotated to `.log.1` at each start. |
| `C:\kokoro\highlighter.err` | Highlighter stderr — where an import/COM-init crash lands. Empty = healthy. |
| `C:\kokoro\env\` | The virtualenv (not in git; rebuild via `requirements.txt`). |
| `C:\kokoro\README.md` | Fresh-machine setup guide. |
| `C:\kokoro\requirements.txt` | Pinned deps from the known-good venv. |

2026-07-17 cleanup: deleted the unused ONNX model+voices (~340MB, rejected engine §6),
the `*.bak` server backups, `kokoro.rar` (manual backup of the same scripts), and the
day-one test files (`test_kokoro.py`, `test_0.wav`, `debug_read_aloud.ahk`). **Git is
the safety net now** — repo: https://github.com/sayed-qutob-work/kokoro-read-aloud

**Dependencies outside the venv:**
- **eSpeak NG** — `C:\Program Files\eSpeak NG`, on PATH. Required for phonemization.
- **AutoHotkey v2** — `%LOCALAPPDATA%\Programs\AutoHotkey\v2\AutoHotkey64.exe`
  (**per-user install** — this is why `assoc .ahk` reports nothing; that's normal).

**Autostart:** shortcut to `start_tts.vbs` in `shell:startup` (Win+R → `shell:startup`).

---

## 3. Current config (in `tts_server.py`)

```python
ENGINE = "torch"          # kokoro via PyTorch. "onnx" exists but is slower — see §6.
KOKORO_VOICE = "af_heart"
MODEL_SPEED = 1.15        # asked of Kokoro itself. KEEP <= 1.3.
PLAYBACK_SPEED = 1.8      # WSOLA time-stretch after synthesis, pitch-preserving.
SENTENCE_PAUSE = 0.1      # absolute seconds after real sentence ends; NOT stretched.
CUT_PAUSE = 0.03          # after mid-sentence chunk cuts (see §5 item 4).
FIRST_CHUNK_AUDIO = 2.0   # SECONDS of audio in the opening chunk — sets START.
                          # START ≈ 104ms + 248ms × this. Live-tunable via /config.
CHUNK_CHARS = 240         # ceiling on any chunk (speech-weighted chars).
MIN_CHUNK_CHARS = 25
SAFETY = 0.7              # fraction of playback budget spent per chunk.
VERBOSE = True
PREFETCH = 2
```

Effective reading speed = `MODEL_SPEED × PLAYBACK_SPEED` = **2.07x**.

**Why speed is split into two knobs:** Kokoro's `speed` parameter rescales its duration
predictor, so at 3x the model physically cannot articulate and garbles onsets and
sentence endings. Keeping the model at ~1.15 and doing the rest with WSOLA
time-stretching after synthesis gives the same speed with none of the mush.
**Do not raise `MODEL_SPEED` above ~1.3.**

Live tuning without restarting (restart = model reload):

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:5111/config -Method Post -ContentType "application/json" -Body '{"playback_speed":2.0}'
Invoke-RestMethod -Uri http://127.0.0.1:5111/config     # also reports measured_density / measured_rt
```

Changes via `/config` are in-memory only — write them into the file to persist.

---

## 4. Measured facts (do not re-guess these)

All fitted from this machine's own logs. **Every estimate made without measuring during
this session was wrong, and always optimistic.** Trust only these.

| Fact | Value | Evidence |
|---|---|---|
| Kokoro synthesis cost (torch) | `synth_ms = 104 + 247.8 × audio_seconds` | R²=0.995, n=15 |
| Throughput (torch) | **4.03x realtime** | same fit |
| Throughput (kokoro-onnx) | **3.89x realtime** — *slower* | R²=0.997, n=8 |
| `torch.get_num_threads()` | **6** — all P-cores, already maxed | direct check |
| START latency | ≈ chunk-0 synth time + ~5ms | START−synth = 3–10ms across ~15 blocks |
| Audio per character | **0.074 – 0.295 s/char** — varies 4x | observed; this is why fixed char ramps fail |
| WSOLA time-stretch cost | ~15ms on 14s of audio | negligible, ruled out |
| Gap constraint | chunk-1 audio must be < **2.24x** chunk-0 audio | predicted 15/16 real gaps |
| First synth after model load | **2.4–3.1x RT** vs 4.0x warm (~350ms penalty) | 3 cold starts observed |
| Boot warmup synth ("Warm up.") | **534ms**, paid before "ready" | 2026-07-16 log |
| Digit expansion when spoken | "In 2024, revenue grew 34.7%" = 30ch → **4.3s** (0.143 s/char) | 2026-07-16 log |
| Density per *weighted* char | **0.064–0.070, stable across prose AND numeric text** | 2026-07-16, both test blocks |
| START after latency fix | **507–599ms**, prose and dense alike | 2026-07-16, matches 104+248×audio fit |
| Kokoro silence padding | **~280ms leading, ~400–500ms trailing, EVERY synthesis** | measured via silence-run analysis |
| Inline markdown (`**` `` ` ``) | **inert** — byte-identical audio | same analysis; do NOT re-suspect it |
| Markdown list markers (`- `) | real pauses (752ms vs 432ms) and +2s audio on a 2-bullet text | same analysis |
| Bare word-boundary cut | final word lengthened **2–4x** ("the" 0.087→0.350s, "jumps" 0.300→0.550s, "and" 0.113→0.425s) | 2026-07-17 token-timestamp test |
| Clause-boundary cut (`, ; :`) | **no** artificial lengthening ("dog" 0.600→0.525s) | same test; comma cuts are benign |
| Per-word timestamps | KPipeline `Result.tokens` carry `start_ts`/`end_ts` per word, aligned with the audio buffer | verified in installed pkg, used by cutfix |

Current measured behaviour: **START ≈ 500–600ms**, steady-state playback clean, no gaps.

---

## 5. The chunking design (the non-obvious part)

Naive approach — split text into equal chunks — fails two ways at once:

1. **Big first chunk** → long wait before any sound (110 chars ≈ 2.4s on this machine).
2. **Small first chunk** → it plays for so little time that the *next* chunk can't be
   synthesized before playback runs dry → gap right after the first sentence.

A fixed `CHUNK_RAMP` multiplier was tried and **failed**, because it ramps *characters*
while the real constraint is *audio seconds*, and the conversion between them swings 4x.
A "1.6x" char ramp produced a **3.85x** audio ramp on real text. It also ramped from the
previous *target* rather than the previous *actual*, so a short first sentence
(15 chars vs a 40 target) meant chunk 1 jumped **4.3x**.

**Current design — budget-driven, self-calibrating:**

- `play_until` — wallclock when banked audio runs out. This is the real budget.
- `density` — seconds of audio per character, EMA, **learned live**.
- `rt` — throughput, EMA, **learned live**.

Before each chunk: `affordable_audio = budget × SAFETY × rt`, converted to chars via
`density`. No fixed multiplier anywhere.

Sentences are the packing unit, and atoms are **rejoined before synthesis** — Kokoro sees
the same text it would have anyway, so joins are free. Only chunk *boundaries* cost
prosody. A sentence is sliced only if it alone blows the budget with nothing buffered.

**Added 2026-07-16 — three refinements, all measured:**

1. **Speech-weighted characters.** Digits weigh 5, `$%€£` weigh 7, everything else 1
   (`wlen()` in the code). "2024" → "twenty twenty four" is why raw char counts swung
   4x; weighting collapsed measured density to a stable 0.064–0.070 s/weighted-char
   across prose and dense numeric text alike. All sizing (`CHUNK_CHARS`, targets,
   density) is in weighted units now.
2. **First chunk budgeted in audio seconds** (`FIRST_CHUNK_AUDIO = 2.0`), converted to
   weighted chars via the learned density. START is now ~constant regardless of text:
   507ms (dense) / 586ms (prose), vs 1103–1461ms before.
3. **Clause-boundary cuts** (`cut_point()`): when a sentence must be sliced, prefer the
   last `, ; :` boundary in the back half of the window over a bare word boundary —
   Kokoro drops pitch at a cut as if the sentence ended, least damaging at a comma.
4. **Silence trimming at boundaries** (`trim_silence()`): Kokoro pads every synthesis
   with ~280ms leading + ~400–500ms trailing silence. Untrimmed, every chunk boundary
   was **~0.5s of dead air** (trailing + SENTENCE_PAUSE + leading) — heard as
   "stops for half a second after a word", worst with the small early chunks of the
   START fix. Now trimmed to 50ms breath each side, then a controlled pause is added:
   `SENTENCE_PAUSE` (0.1s) after real sentence ends, `CUT_PAUSE` (0.03s) after
   mid-sentence slices. Boundary dead air: ~0.8s → ~0.13s mid-sentence.
5. **Input sanitizing** (`sanitize()`): markdown list markers, headers, table pipes,
   box-drawing chars and arrows are stripped before chunking (terminal text is full
   of them). Inline `**`/backticks are inert to Kokoro (measured) but stripped anyway.

**Added 2026-07-17 — the "random slow words" fix (user-reported):**

6. **Bare-cut final-word compression** (`compress_final_word()` + `_STOP_TAIL` in
   `cut_point()`). User heard the voice "slowing down on some words" arrhythmically.
   Measured cause: Kokoro treats ANY cut as an utterance end and lengthens the final
   word 2–4x (§4). Clause cuts at `, ; :` are clean; **bare word-boundary cuts** (the
   `cut_point` fallback, common in the small early chunks of the START ramp) each
   produced one drawn-out word. Two-part fix, both verified live: (a) bare cuts back
   up past trailing function words (a 4x-stretched "the" was the worst case), so cuts
   end on content words; (b) the chunk's per-word timestamps (`Result.tokens`) locate
   the final word, and if its duration exceeds ~1.3x the chunk's own median
   per-phoneme rate it is WSOLA-compressed back to it (factor clamped ≤2.5, crossfaded
   splices), *before* the global time-stretch. Logged as `cutfix 'word' 0.60s / 1.92`.
   Live test 2026-07-17: 5/5 bare cuts caught, 0 gaps. The remaining word-to-word
   pacing variation is Kokoro's natural prosody (stressed/content words are longer),
   which the 2.07x speed makes more noticeable — that part is the model, not a bug.

**Added 2026-07-17 later — clause-atom rewrite (supersedes how often item 6 fires):**

7. **Chunk boundaries only at `, ; : . ! ?` — user-mandated steady flow.** After
   item 6 shipped, real use showed reads after the first were choppy: (a) the
   `_STOP_TAIL` backup shrank sliced chunks below the audio the budget planned,
   violating the 2.24x gap constraint (§4) → stalls; (b) `density` was learned
   from RAW audio including Kokoro's fixed ~0.7s silence padding — negligible on
   long chunks, dominant on short ones, so every small read inflated density and
   shrank all later chunks (matches "the smaller the text, the worse");
   (c) `compress_final_word` fired on selections without ending punctuation,
   speeding up natural endings. Fixes, all deployed + verified live:
   - `split_atoms` splits sentences into **clause atoms** at `[,;:]\s` (clauses
     keep their punctuation, so rejoining reconstructs the exact sentence —
     joins stay free). Bare cuts only remain for a single clause > CHUNK_CHARS.
   - `_take` never slices: first atom is always taken whole; an underfilled buf
     (< target/2) also takes the next atom whole (a tiny chunk banks too little
     audio for the 2.24x constraint). Returns a `final` flag.
   - density = SPEECH seconds per wchar (trim now happens BEFORE stretch and
     before learning); `rt` stays on raw audio, matching the §4 synth fit.
   - compression skipped when `final` or chunk ends in any of `.!?…,;:`;
     the utterance's last chunk gets SENTENCE_PAUSE even unpunctuated.
   - `play_until` update is gen-guarded under `self.lock`: a `/speak` racing a
     finishing synth could inherit ~2–14s of stale budget → monster first chunk.
   **Cost, measured:** START on a clause-poor opening is now the whole first
   clause: observed 1332ms on a 76-char opening clause (was ~500–600ms). This is
   the user's explicit trade: flow > start latency. `FIRST_CHUNK_AUDIO` is now a
   packing target, not a cap. Supersedes §9's START figure.

**Known limit:** a chunk whose density is far off the running average still gaps *once*,
then adapts (weighting has removed the *predictable* part of that variance). The fixed
ramp gapped *every* time.

---

## 6. Rejected — do not redo

| Option | Verdict | Why |
|---|---|---|
| **Piper TTS** | Rejected | Quality ceiling. VITS optimized for Raspberry Pi realtime; espeak-ng phonemizes *everything*. Kokoro uses espeak only as OOD fallback. |
| **kokoro-onnx** | **Tested, rejected** | 3.89x vs torch's 4.03x. Worse on both the marginal rate (+3.8%) and fixed cost (130ms vs 104ms). Model files deleted 2026-07-17; the `ENGINE="onnx"` code path remains but needs `pip install kokoro-onnx` + re-downloading the files. |
| **torch thread tuning** | No headroom | Already 6/6 P-cores. |
| **`MODEL_SPEED` > 1.3** | Rejected | Garbles onsets/endings. Use `PLAYBACK_SPEED` instead. |
| **Trimming AHK `Sleep`/`KeyWait`** | Pointless | AHK contributes ~0 to START. Measured. |
| **In-place highlighting in a terminal, via the terminal's OWN TextPattern** | **Impossible — proven 2026-07-18, still true** | xterm.js (VS Code's integrated terminal) paints text to a canvas and exposes it to accessibility through a hidden off-screen DOM mirror. Probed live: terminal text reports rects at `x=-11571` and `x=-14907`, height `58557`, for a window occupying `x=-1928..8`. UIA gives the text but no usable geometry and nothing bridges the two. Reading terminal text still works (that is `copyOnSelection`, unrelated). **Narrowed 2026-07-21** — the *ancestor* path is a different story, see §8. |

### GPU — the one open option, **untested**

Kokoro auto-detects CUDA. Installing a CUDA torch (`--index-url .../cu124`, ~2.5GB)
into the venv would **silently** enable it.

- **Estimated** gain: chunk-0 synth ~2400ms → ~200ms. **This is an estimate and estimates
  in this session have been consistently wrong. Measure before believing.**
- **Cost:** ~1–2GB VRAM held permanently, on an 8GB card shared with other everyday
  GPU workloads (local LLMs, games). The server autostarts and runs all day.
- **Floor:** the 104ms fixed cost is phonemization + Python — CPU-bound. GPU can't touch it.
- **Verdict:** declined on this machine. Reconsider if the RTX 3090 (24GB) upgrade happens —
  2GB of 24 is noise.

---

## 7. Operating procedures

### The one discipline that matters

**Editing `tts_server.py` does nothing to a running process.** Python reads source once at
startup. If a server is already on 5111, launching another fails with "address already in
use" and dies — silently, because the `.vbs` hides the console. The old one keeps serving.
Everything looks fine and your edits appear to be ignored.

**Always: kill → verify port empty → start.**

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*tts_server.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Get-NetTCPConnection -LocalPort 5111 -State Listen -ErrorAction SilentlyContinue
# ^ MUST print nothing before starting

cd C:\kokoro
env\Scripts\python.exe tts_server.py
```

cmd equivalent of the kill: `taskkill /F /IM python.exe` (blunter — kills all Python).

### Error decoder

| Symptom | Meaning |
|---|---|
| `Unable to connect` / `0x80072EFD` | Nothing listening. Server isn't running. |
| `404 Not Found` | Server running, but **old code**. Kill and restart. |
| `400` on `/speak` | Empty text — the G Hub macro fired with nothing selected. By design. |
| MsgBox "cannot reach tts_server.py" | AHK is fine, Python side is down. |
| `BadZipFile` | Corrupt download (`curl` without `-L` on a GitHub release). |
| `ModuleNotFoundError` | Wrong interpreter. Use `env\Scripts\python.exe -m pip`, never bare `pip`. |

### Shell traps hit this session

- **PowerShell vs cmd.** `Invoke-RestMethod`/`$vars` are PS-only. `curl`/`taskkill` are cmd-friendly.
- **`pip install kokoro>=0.9.4`** — in PowerShell `>` is a *redirect*. It silently created
  a file named `=0.9.4` and installed nothing. **Always quote:** `pip install "kokoro>=0.9.4"`.
- **`curl` in PowerShell** is an alias for `Invoke-WebRequest`. Use `curl.exe`, and `--%`
  to stop PS parsing, or just use `Invoke-RestMethod`.
- **`curl -L`** is mandatory for GitHub release URLs (they redirect to a CDN).
- **`env\Scripts\python.exe -m pip`** — never bare `pip`. Guarantees the right interpreter.
- **Windows 11 suppresses `TrayTip`.** All AHK errors now use `MsgBox`. Do not revert.
- **AHK tray icon hides** behind the `^` arrow. Settings → Personalization → Taskbar →
  Other system tray icons → toggle AutoHotkey on.

### Server-side gotchas encoded in the code — don't undo them

- **PortAudio is not thread-safe.** `stream.abort()` from Flask while `_play_loop` is inside
  `stream.write()` can hang the audio system. All stream ops go through `audio_lock`.
  This caused a real freeze when interrupting playback mid-read.
- **`sd.play()` re-opens the audio device every call** (~50–150ms). One persistent
  `OutputStream` is held instead.
- **Kokoro's `synth()` must collect all yielded chunks.** An earlier version `return`ed the
  first and silently dropped text.
- **lang_code is derived from the voice prefix** (`af_`→'a', `bf_`→'b') automatically.
- **`SENTENCE_PAUSE` is appended after time-stretch**, so it stays absolute. At 1.8x this
  makes it feel ~1.8x longer than at 1x — that's why it's 0.1, not 0.25.

---

## 8. OPEN items (updated 2026-07-16)

### RESOLVED: budget chunking verified

Three real blocks (~15 chunks) on 2026-07-16: **zero GAP lines** (baseline: 5 gaps in
16 blocks). `dens` adapts as designed. Closed.

### RESOLVED: START latency (was ~1–1.5s, now ~0.5–0.6s)

Deployed and measured same day — see §5 additions and §4 new rows. (The `.bak`
restore points were deleted in the 2026-07-17 cleanup; git history is the safety
net now.) Live knob:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:5111/config -Method Post -ContentType "application/json" -Body '{"first_chunk_audio":1.5}'
```

`1.5` → START ≈ 475ms, `1.0` → ≈ 350ms, at increasing risk of a choppier opening
(shorter first phrase). 2.0 is the deployed default. **Not yet judged by ear** — the
first cut now lands at a clause boundary; user should confirm it sounds fine.

### RESOLVED: "hotkey doesn't work" after the 2026-07-16 reboot — it was terminals

Diagnosis chain (each step verified): autostart was fine, server was fine, AHK was
firing — the AHK line-trace showed `ClipWait` timing out: **Ctrl+C was copying
nothing**. Browser text worked; **terminal text (VS Code integrated terminal running
Claude Code) never did**, because:

1. In a terminal, **Ctrl+C means "interrupt"**, not copy — it only copies if a
   selection still exists when it arrives.
2. **TUI apps like Claude Code redraw constantly, and every redraw clears the
   terminal selection** — it is usually gone before the simulated ^c lands (~150ms
   after the hotkey). The ^c then hits the shell as a real interrupt.

**Fix (deployed):**
- `terminal.integrated.copyOnSelection: true` in VS Code user settings — terminal
  selections hit the clipboard at mouse-up, no keystroke involved.
- New **Ctrl+Alt+T** hotkey in the `.ahk`: speaks the clipboard **as-is**, no ^c
  dance. Terminal flow: select → Ctrl+Alt+T. Fires only on explicit keypress — this
  is NOT the hated clipboard polling.
- Ctrl+Alt+R kept as-is for everything else, plus: retries ^c once in event mode,
  and on total failure shows a 2.5s ToolTip pointing at Ctrl+Alt+T (was: silent).

**Superseded same day, twice:** Ctrl+Alt+R is now window-aware, so the one G Hub
mouse button works everywhere:

- **Pure terminals** (WindowsTerminal, conhost, …): clipboard path, no ^c ever sent
  (^c = interrupt there). Needs copy-on-select in that terminal (Windows Terminal:
  `"copyOnSelect": true`).
- **VS Code (Code.exe)** is both terminal and editor, disambiguated by **clipboard
  freshness** (`OnClipboardChange` timestamp in the `.ahk`): clipboard changed
  externally < 3s ago → that IS the terminal selection (copyOnSelection fires at
  mouse-up) → speak it, send nothing. Stale → focus is the editor / markdown
  preview → normal ^c dance (plain copy there); if the dance yields nothing, fall
  back to the saved clipboard. This fixed "reading `.md` files speaks the last
  copied thing". `ClipBusy` masks the dance's own clipboard churn from the
  freshness timestamp — don't remove it.
- **Everything else**: the classic copy dance.

Residual quirks, accepted: pressing the hotkey in a terminal with nothing selected
re-reads the last clipboard; selecting in the VS Code *editor* within 3s of an
external clipboard change speaks the clipboard instead of the selection. Both are
misfire-shaped and stoppable with Ctrl+Alt+S. Ctrl+Alt+T = explicit clipboard read.

### Note on process listing (don't re-diagnose this)

`env\Scripts\python.exe` on Windows is a **venv launcher** that spawns the base
`Python312\python.exe` as a child. Two `python.exe` processes for one server, and the
**child** owns port 5111, is NORMAL — not a duplicate server.

### Remaining from before

Done 2026-07-17 — ONNX files deleted with the rest of the cleanup (§2).

### DEPLOYED 2026-07-17: word-by-word highlighting (Speechify-style), user-requested

User chose the **overlay caption window** form (the only universal one — other
apps' windows cannot be painted into; a browser extension would be web-only and
remains a possible phase 2). How it works:

- `synth()` returns per-word `(text, n_phonemes, start_ts, end_ts)` from
  KPipeline tokens (§4). `_synth_loop` maps them through every audio transform:
  `compress_final_word` updates the final word's end, `time_stretch` divides by
  `PLAYBACK_SPEED`, `trim_silence` now returns the leading-trim offset which is
  subtracted. `_play_loop` stamps wallclock at chunk start.
- **`GET /now`** returns `{active, text, words:[[w,start,end]...], word: idx, t}`
  — which word is sounding at this instant. Werkzeug logging for `/now` is
  filtered out of `server.log` (12 polls/s all day would bloat it).
- `overlay.py` — tkinter strip, frameless, topmost, bottom-center, launched by
  `start_tts.vbs` via `pythonw.exe` (so: TWO `pythonw.exe` processes = one
  overlay, same venv-launcher pattern as the server, don't re-diagnose). Polls
  80ms while visible / 500ms hidden; shows the current chunk, dims spoken words,
  blue-highlights the sounding word; hides ~0.7s after playback ends.
  **Drag to move, right-click to close.** Server never knows it exists; killing
  the overlay changes nothing else.

Verified 2026-07-17: `/now` word index tracks reading order across chunks
(sampled live), goes inactive after `/stop`, screenshot confirmed the rendered
strip mid-playback ("into the" dimmed, "quiet" highlighted). Not yet judged by
eye at real reading speed — highlight lag vs audio, if any, is bounded by the
output-stream latency (~tens of ms) + ≤80ms poll; if it feels late, lower
POLL_MS or subtract a fixed offset in overlay.py.

**Round 2 (same day): in-page highlighting** — user wants the highlight ON the
word in the page (Speechify proper), not a strip underneath. Built as a browser
extension in `C:\kokoro\extension\` (the only way to paint inside a page):

- `content.js` snapshots the selection as one DOM Range per word on
  `selectionchange`, polls `/now` through `background.js` (MV3 content scripts
  are CORS-bound; the service worker fetches with `host_permissions`), aligns
  spoken tokens to the snapshot in reading order (normalized match, bounded
  forward scan), and paints via the **CSS Custom Highlight API** — zero DOM
  mutation, cannot break page layout.
- Install: browser → extensions page → Developer mode → **Load unpacked** →
  `C:\kokoro\extension` (works in Chrome/Edge/Brave/Vivaldi).
- Limits: DOM text only — no PDFs in the built-in viewer, no Google Docs
  (canvas). Non-web sources never match; the overlay covers those. The overlay
  and extension coexist; right-click the overlay to close it, or remove its
  line from `start_tts.vbs` to stop autostarting it.
- **NOT yet verified in a real browser** (built blind); the server side it
  depends on (`/now`) is verified. First-run check: select a paragraph, hit
  Ctrl+Alt+R, watch for the blue word marker following the voice.

**Round 3 (same day): highlight the ORIGINAL text, everywhere — deployed.**
User clarified: no transcript anywhere, the source text itself must light up —
browser, Notepad, `.md` files, wherever. Windows cannot restyle another
process's rendered text, but `highlighter.py` achieves the same look:

- Per utterance it anchors the spoken text in the focused app via **UI
  Automation TextPattern**: the live selection if the app kept it (editors do
  after ^C), else `FindText` of the utterance head in the document (terminals
  drop selections). New `GET /utterance` serves the original pre-sanitize text;
  `/now` gained an `utt` counter to detect utterance changes.
- Each spoken token is located with `FindText` inside the not-yet-spoken
  remainder (self-aligning; tolerant of markdown that `sanitize()` stripped,
  so `**bold**` in an `.md` still matches its inner word). Bounding rects are
  re-queried every 80ms, so scrolling moves the marker.
- The marker is a tiny **per-pixel-alpha layered window** (`UpdateLayeredWindow`,
  premultiplied BGRA, `WS_EX_TRANSPARENT` = click-through, `NOACTIVATE`,
  PerMonitorV2 DPI-aware — ctypes prototypes matter, handles truncate to 32-bit
  without them) that jumps word to word, tinting the word `#3d5afe` at ~43%.
- **Verified end-to-end 2026-07-17 in Notepad**: scripted select-all + `/speak`,
  screenshot mid-read shows the word "pine" tinted in place at the moment it
  was spoken. Known cosmetic: the marker is line-height tall (UIA reports full
  line rects), so it can poke above short glyphs.
- Apps with no TextPattern (or Chromium with accessibility off) simply get no
  marker — the browser extension covers web pages properly. `overlay.py` was
  removed from `start_tts.vbs` per the user's explicit "no bottom transcript";
  the file stays for anyone who wants it back.
- Needs `comtypes` (added to requirements.txt). Highlighter is a third hidden
  `pythonw.exe` pair at startup; killing it affects nothing else.

**Round 4 (2026-07-18): multi-app support finished; verified by the user.**
Round 3 worked only in Notepad. Each remaining app failed for its own reason;
all diagnosed from live UIA probes and a debug log, none by guessing.

- **Firefox** — the TextPattern is not on the focused element but on a
  *document ancestor* of it, and the accessibility engine warms up **lazily**
  (the first queries after startup return empty selections and zero rects).
  Fix: `candidate_patterns()` yields the focused element, then up to 8
  ancestors, then the first TextPattern descendant; anchoring retries for
  `ANCHOR_WINDOW` (6s) instead of giving up on the first miss, and `locate()`
  no longer caches misses.
- **VS Code editor (`.md`)** — needs `"editor.accessibilitySupport": "on"` in
  user settings, *and* `FindText` ranges report **zero rects** until the range
  is `Select()`-ed: VS Code only materializes geometry near its accessibility
  "page", and selecting moves the page. Fix: on empty rects, `Select()` once
  per word, harvest rects, then collapse to a bare caret so VS Code stops
  painting its own selection block over the word.
- **The paragraph-boundary anchoring bug** (this is the one that made the
  browser feel random). The server flattens the selection into one line,
  joining a heading to the paragraph beneath it with whitespace; in the
  document those are separate text runs, so `FindText` of a 60-char head
  matched *nothing*. With a live selection the anchor came from the selection
  and worked; the moment the user clicked away and cleared it, the FindText
  fallback failed on every passage spanning a heading — i.e. most real ones.
  Fix: `head_candidates()` returns progressively shorter search strings,
  longest first, splitting on runs of 2+ spaces (the tell that flattening
  happened) so a bare heading is tried. Longest-first matters: a short
  fragment can match a nav item and anchor the whole read in the wrong place.
- **First word of a read was never highlighted** — not a highlighting bug.
  `POLL_IDLE` was 0.5s, so up to half a second passed before the highlighter
  even noticed a read had started, by which time the voice was a word or two
  in. Continuation chunks always started at word 0; only an utterance's first
  chunk lost words — that asymmetry is what identified it. Now 0.12s.
- **Debug logging**: set `KOKORO_HL_DEBUG=<path>` before launching
  `highlighter.py` and it appends timestamped anchor decisions (which document
  it locked onto, via selection or which search string) and per-token rects.
  Off unless the variable is set. This is how Round 4 was diagnosed — every
  earlier attempt guessed and was wrong. Use it before theorising.
- **Verified by the user 2026-07-18**: `.md` in VS Code — every word, correct
  positions. Firefox — repeated reads with the selection cleared between them,
  including heading-spanning passages, all correct; first word included.
- **Known cosmetic (accepted by the user):** in VS Code, the `Select()` needed
  to materialize geometry moves the caret into the word, and VS Code's own
  `editor.occurrencesHighlight` then faintly tints every other instance of
  that word. It cannot be suppressed from outside the editor. Optional user
  fix, scoped so code files keep the feature:
  `"[markdown]": {"editor.occurrencesHighlight": "off", "editor.selectionHighlight": false}`.
- **Deliberately NOT changed:** `Anchor.__init__` accepts the first non-empty
  selection without checking it matches the text being spoken, so a stale
  selection elsewhere could in principle hijack the anchor. Logged as a latent
  risk, not a fix — the debug log showed the selection path anchoring to the
  correct document every single time, and this is the path everything relies
  on. Do not "fix" it without evidence it actually bites.

### DEPLOYED 2026-07-21: highlighter instrumentation + crash-proofing (`plan.md` Phases 0 & 1)

User reports three symptoms in daily use: (A) some reads never highlight,
(B) some glitch, (C) some start dark and begin working two–three lines in.
`plan.md` maps them to ten code-verified root causes (RC1–RC10). Nothing about
*frequency* is known yet, so this deployment **changes no timing and no
control flow** — it only makes the log answer the ranking question. Anything
that alters behavior (retry cadence, the 6s give-up, the wipe grace period,
the 0.15s HTTP timeout) is Phase 2+ and deliberately untouched.

- **Logging is on by default now.** `start_tts.vbs` sets
  `KOKORO_HL_DEBUG=C:\kokoro\highlighter.log`, and the highlighter rotates the
  previous log to `highlighter.log.1` at startup (rotate, not truncate: if it
  died and was relaunched, the traceback that killed it survives).
- **The launcher no longer uses `pythonw.exe`.** It runs `python.exe` inside a
  hidden `cmd` with `> highlighter.err 2>&1`, the same pattern as the server.
  Reason: `pythonw` has *no stderr at all*, so an import or COM-init failure —
  which happens before `main()`, before any guard, before dlog exists — left
  literally no trace. That is RC5's worst form: dead all session, invisible.
  Verified by launching a module that fails to import; the traceback landed in
  the `.err` file. **So the highlighter is now a `python.exe` pair, not a
  `pythonw.exe` pair** (two processes is still normal — venv launcher + child).
- **New log lines**, each tied to the RC it proves or kills:
  `START` (proof of life) · `UTT n begins` · `ANCHOR try#k +Δs` (RC1: count the
  rounds before success) · `ANCHOR ok/FAILED` with the head candidates (RC3) ·
  `GIVEUP … RC2` when the 6s window expires unanchored · `WIPE utt=…` on every
  `active:false` · **`RESUME utt=… after Δs dark — RC6`** when the *same* gen
  reappears within 5s of a wipe, which is the signature of a mid-read state
  wipe rather than a finished read · `FETCH fail/ok` (RC9; first failure of a
  run always logged, a continuing outage collapses to one line per 10s) ·
  `POLL ERROR` / `FATAL` with tracebacks.
- **Crash-proofing (Phase 1):** the whole poll body is now
  catch-log-continue with an escalating sleep (0.25s → 2s cap) so a repeating
  fault can't spin the CPU or the log; `main()` itself is wrapped in a
  restart-after-2s loop; `Marker.draw` checks `CreateDIBSection`/`bits` for
  NULL (`from_address(None)` was a real process-killer) and releases its DC +
  bitmap in a `finally` — the old straight-line path leaked a DC per raised
  exception, and GDI exhaustion is what makes `CreateDIBSection` start
  returning NULL in the first place. `d["text"]` → `d.get("text") or ""`.
- **Verified live 2026-07-21** (Notepad, warm): `ANCHOR acquired on try#1,
  0.01s after utt start`, every token from idx=0 logged with rects, `WIPE` at
  end of read. Fault injection (raise every 15th poll) confirmed the loop
  guard: 6 `POLL ERROR` tracebacks logged, process alive, highlighting
  continued throughout.
- **Not done, and it's the user's step:** ranking RC1–RC10 by observed
  frequency needs a day or two of real reads. Read `highlighter.log` then —
  count `GIVEUP` (A), `RESUME` (B/C), `ANCHOR try#` depth (C), and `FETCH
  fail` clustering at chunk boundaries (B) before starting Phase 2. Two
  filters that keep the count honest: **count `RESUME`, never `WIPE`** —
  `WIPE` fires at the end of every normal read and is pure noise, only a
  `WIPE`→`RESUME` pair is an RC6 event; and for RC9 read the exception type —
  `timeout`/`TimeoutError` clustering mid-read is RC9, `URLError` (connection
  refused) just means the server wasn't running.

### OBSERVED 2026-07-21, hours after the logging went in: the VS Code terminal partly highlights — §6 was too broad

User read Claude Code's output in the VS Code integrated terminal and saw the
highlight track it — "the last maybe four lines, not 100% accurate but a good
amount". Nothing was changed to make that happen; it has presumably worked
since Round 4 and nobody noticed. The log says exactly why:

```
cand[0] how=None doc='Terminal 5, ✳ Implement Phase 0 and Phase 1 loggin'
cand[1] how=findtext['Two smaller things in th'] doc='… File Edit Selection View Go Run Terminal'
ANCHOR ok via=findtext['Two smaller things in th'] cand=1
```

`cand[0]` **is** the terminal element and it is as useless as §6 says — no
selection, FindText miss. The anchor came from `cand[1]`: an **ancestor, the
whole VS Code window's document**, reached by the 8-level parent walk that
Round 4 added for *Firefox*. Ranges found there report sane geometry —
17px-tall lines at real screen coordinates — not the `h=58557` garbage the
terminal's own pattern hands out. So §6's verdict is correct about the
mechanism it tested and wrong as a blanket claim.

**Measured, same reads** (154 unique tokens across 4 utterances):

- **83 of 154 tokens (54%) ever got a rect.** The rest never did.
- Every hit landed on one of **6 distinct line tops** (y = 425, 1141, 1158,
  1175, 1209, 1345) — i.e. the highlight worked on a handful of lines and was
  dark everywhere else, which is precisely "the last four lines". Why those
  lines materialize geometry and the others don't is **not yet diagnosed** —
  the obvious suspect is the same viewport/accessibility-page effect that
  forces the `Select()` fallback in the VS Code editor, but that is a guess
  and this project's guesses have a 0% hit rate. Instrument first.
- **Added the same evening** (still behavior-preserving), because both gaps
  showed up while reading the numbers above:
  - `cand[]` lines now carry
    `who=<image.exe> '<Name>' [<ClassName>]` — e.g.
    `who=Notepad.exe 'Text editor' [RichEditD2DPT]`. Process name is resolved
    via `QueryFullProcessImageNameW` and cached per pid; the whole `_ident()`
    path is skipped unless `KOKORO_HL_DEBUG` is set, since each property read
    is a cross-process COM call.
  - Per-token lines now carry **`found=` and `sel=`**. This split matters:
    `found=0` means FindText could not locate the token in the remainder (an
    alignment / anchoring failure) while `found=1 rects=[]` means it was
    located but the app materializes no geometry for it (the viewport /
    accessibility-page effect). **Both used to log as `rects=[]`**, so the
    "54% of tokens" figure above cannot distinguish them — treat it as a
    ceiling on one cause, not a measurement of either. `sel=1` marks the poll
    where the `Select()` fallback ran.

**Also confirmed in the same 60 seconds: RC6 is real, in the wild.**
`RESUME utt=15 after 0.12s dark` and `RESUME utt=17 after 0.37s dark` — two
mid-read state wipes in one minute of normal use, each one a re-anchor plus a
cursor reset to the utterance head. That is `plan.md` Phase 4's evidence gate,
met on day one.

### MEASURED 2026-07-21 evening: the Phase 0 ranking. RC6 is the whole problem; RC1/RC2/RC9 never fired

19 real user reads (utt 9–28, ~22:40–23:04, `highlighter.log` + `.log.1`).
Counts are structural — parse on the `HH:MM:SS ` prefix + keyword, **never
`"RESUME" in line`**: the user reads this project's own prose aloud, so token
lines contain the words `RESUME`, `GIVEUP`, `ANCHOR try#`. The first pass at
this analysis reported 89 RC6 events; the real number was 1. Don't repeat it.

| RC | What the log says | Verdict |
|---|---|---|
| **RC6** wipe on transient `active:false` | **5 events / 19 reads ≈ 26%.** Dark gaps 0.12, 0.37, 0.49, 0.61, 0.74s | **Confirmed, dominant, worse than predicted — fix this** |
| RC1/RC2 slow or abandoned anchor | **0.** Every one of the 19 anchored on `try#1`, 0.01–0.03s. Zero `GIVEUP` | Not observed — Phase 2 is unjustified |
| RC9 HTTP timeout | **0** `FETCH fail` in ~25 min of polling at 12/s | Not observed — leave the 0.15s timeout alone |
| RC5 crash | 0 `POLL ERROR`, 0 `FATAL`, 1 `START`, empty `.err` | No crash since the guards; window too short to claim they were needed |

**RC6 costs more than `plan.md` predicted.** The plan expected "0.5s dark gap
+ backwards jumps". Observed on utt 26 (markdown preview of `plan.md`, live
selection, anchored on try#1, painting correctly):

```
23:02:38 WIPE utt=26 anchored=True chunk='3. Optional hardening:'
23:02:39 RESUME utt=26 after 0.61s dark -- RC6
23:02:39   cand[0] how=None who=Code.exe 'plan.md - kokoro - Visual Studio Code - ' []
23:02:40   cand[0] how=None who=Code.exe 'Terminal 5, …' [xterm-helper-textarea]
         … 6 attempts, all ANCHOR FAILED, rest of the read dark
```

The preview element that had the selection **was no longer among the
candidates** — in those 0.61s the focus moved (by try#3 the candidates are
the terminal). The discarded anchor was still a perfectly valid UIA range;
nothing about it had gone stale. So the real cost of RC6 is not a gap, it's
**losing the whole remainder of a read**, because re-anchoring depends on
focus and focus is a moving target. That is the argument for Phase 4's grace
period: never re-derive an anchor that still works. All five observed gaps
are ≤0.74s, so the plan's ~2s grace covers them with room to spare.

**Not concluded, deliberately:** VS Code tokens located at 2% (7/321) looks
alarming but 2 of those 3 reads were *terminal* reads (§6: unsupported), and
the third is utt 26, which worked until RC6 killed it. Notepad was 16/16 =
100%. There is no Firefox or `.md`-editor read in this sample — i.e. **no
data yet on the surfaces the highlighter is actually for.** Get some before
theorising about VS Code.

### DEPLOYED 2026-07-21: Phase 4 — the anchor survives a transient `active:false`

The fix the measurements above justified. `/now` going inactive no longer
destroys anything: the marker still hides immediately (nothing is sounding),
but `anchor`, `utt_seen`, `chunk_seen` and the resolved-token cursor are held
for `GRACE = 2.0s`. If the same `utt` comes back inside that window the read
continues untouched — no `candidate_patterns()` re-run, no cursor reset to the
utterance head, no backwards jumps. Only a genuine end (>2s) or a different
`utt` releases the anchor.

Why holding beats re-deriving: the anchor is a UIA range that never went
stale — but rebuilding it depends on *focus*, and focus moves. Utt 26 is the
proof (§8 above): 0.61s of silence, and by the third retry the candidate list
was the terminal instead of the markdown preview that had the selection.

New log vocabulary: `IDLE` (inactive, anchor held) → either `HELD after Xs
idle — RC6 avoided` or `DROP … read is over`. `RESUME` now means something
sharper than before: a same-`utt` read reappearing *after* the grace already
expired, i.e. **GRACE was too short** — if those start showing up, raise it.

**Verified 2026-07-21, both paths:**

- Real read, Notepad, 131 tokens: 131/131 located and painted (100%),
  `IDLE` → `DROP utt=39 idle 2.09s` at the end. No behavior change to a
  normal read.
- Forced RC6, deterministic: a mock `/now` on port 5112 (scratch, deleted)
  served active → `active:false` for 0.7s → active again with the same `utt`
  and an advanced word index. Log: `IDLE` → `HELD after 0.77s idle (utt=1
  prev=1) -- RC6 avoided, no re-anchor`, and the tokens continue *forward*
  across the gap — idx=17 at x=-1118 before, idx=20 at x=-1006 after, same
  line, same anchor. Pre-fix this is precisely where the read went dark or
  jumped backwards.

**Also added (instrumentation, not a fix):** on `found=0` the token line now
carries `rem=` — the first 30 chars of the search cursor's remaining range.
Every observed all-miss read (terminal 0%, Firefox 4%) located a few tokens
and then missed *every* subsequent one, which smells like `remaining` having
collapsed to nothing or been dragged past the text by one bad `FindText` hit.
`rem=''` would confirm collapse; `rem=` showing text far past the spoken word
would confirm the jump. **Not yet diagnosed — read this field before
theorising.** That is the next open question, and it is worth more than the
remaining phases: it is what stands between a 4% Firefox read and a 98% one.

### DIAGNOSED 2026-07-21 (late): the two remaining bugs, both named by the log

The `rem=` field paid for itself on the first read. Both all-miss failures
have concrete mechanisms now; neither is fixed yet.

**Bug A — cursor runaway.** `locate()` only ever moves `remaining` forward,
and `FindText` has no word-boundary concept, so **one bad hit is permanent**.
Utt 40 (terminal read, anchored on the VS Code *window* document):

```
idx=0 tok='So'     found=1 rects=[(-1279,886,16,17)]      <- correct
idx=1 tok='.md'    found=1 sel=1 rects=[]                 <- matched the "AUDIT.md" TAB LABEL
idx=2 tok='worked' found=1 sel=1 rects=[]
idx=4 tok='first'  found=0 rem='￼ ￼ Terminal 5, ✳ Implement Ph'
... every later token: found=0, same rem
```

`rem=` shows the cursor parked in VS Code's *chrome* — tab labels, the
terminal's a11y label — miles past the prose being read. Another read shows
`rem='d Succeeded '` (the status bar). The text the next token needs is now
*behind* the cursor, so nothing matches again, ever. This is why terminal /
window-anchored reads score 0–2% while the same code scores 98–100% elsewhere.
Two composing fixes, both in `plan.md` Phase 5's spirit: (1) verify word
boundaries after a `FindText` hit — that alone rejects `.md` inside
`AUDIT.md`; (2) a recovery rule — after K consecutive misses, reset
`remaining` to the anchor's original range instead of staying lost forever.
A hit yielding no rects while earlier hits had them is also a strong
wrong-match tell, but the VS Code editor legitimately needs `Select()` first,
so don't use rect-emptiness alone as the reject signal.

**Bug B — the anchor can't reach the page document in Firefox.** Utt 38 and
43 never anchored (12 tries, `GIVEUP`, read dark). The only candidate offered
was `who=firefox.exe 'Not impossible, but "hardware-free" and…'` whose whole
document *is* that one paragraph — a fragment of the page, not the page.
The ancestor walk surfaced the real page document on utt 37 and 42
(`'WiFi-based through-wall person detection'`) but not on 38 and 43, in the
same browser minutes apart. When it does surface, Firefox is excellent:
**utt 42 = 94/94 tokens, 100%.** So Firefox isn't broken — reaching the right
element is. Needs a wider net than "focused element + 8 ancestors" (e.g. the
foreground window's element via `ElementFromHandle`), which is Phase 3.

**Correction to the ranking above:** the earlier entry says Phase 2 (anchor
retry cadence) is "unjustified by the data". That sample contained **no
Firefox reads**. With them, anchoring took **3, 7 and 12+ attempts** — at
`ANCHOR_RETRY = 0.5s` that is 1.1s, 3.2s and never. Firefox's lazy
accessibility engine is exactly the cold-start case Phase 2 was written for,
so **Phase 2 is justified for Firefox specifically**; it remains pointless for
Notepad/VS Code, which anchor on try #1 every time. Phase 6 (HTTP timeout)
is still unjustified: zero `FETCH fail` across every read so far.

### DEPLOYED 2026-07-21: Bug A fixed — word-bounded matching + a cursor that can rewind

Two changes in `Anchor`, addressing the runaway diagnosed above:

1. **`_word_bounded()`** — after a `FindText` hit, expand one character each
   way (`MoveEndpointByUnit(…Character, ±1)`) and reject the hit if either
   neighbour is alphanumeric. `Anchor._find()` then skips up to
   `FIND_RETRIES = 4` bogus hits before giving up on a token for that poll.
   Punctuation neighbours stay legal, so markdown tolerance survives
   (`bold` inside `**bold**` still matches).
2. **`MISS_RESET = 8`** consecutive `locate()` failures rewind `remaining`
   to `last_good` — the cursor position after the last *verified* hit.
   Previously the cursor only moved forward, so being wrong once meant being
   wrong for the rest of the read.

**Found while testing, and it is the subtler bug of the two:** a fruitless
`FindText` sometimes returns a **NULL COM pointer rather than `None`**.
`if found is not None` is therefore true for a hit that raises on every use.
The old code cached that NULL as a located range (`found=1 rects=[]`, cursor
stuck); with `_word_bounded` in front of it the NULL would have been *treated
as a valid hit*, since the boundary check can't inspect it and fails open.
Both call sites now test `if found:` / `if not r:`. Don't revert these to
`is not None`.

**Verified 2026-07-21, three ways:**

- Scripted against live Notepad holding `The raining season ended. It rains
  in the evening.` — raw `FindText('in')` returns 5 hits in order; the
  boundary check rejects hits 1, 2, 3 and 5 (inside `raining`, `rains`,
  `evening`) and accepts only hit 4, the standalone word. `_find('in')`
  picks hit 4.
- End-to-end at 1.2 words/s (mock `/now`, so the poll dwells on the short
  word): `tok='in'` painted at **x=-1606 w=16**, sitting exactly between
  `rains` (ends -1614) and `the` (starts -1582). Pre-fix this bound inside
  `rains`.
- No regression: the same rig still logs `HELD after 0.63s idle -- RC6
  avoided`, and a full Notepad read stays 100%. 0 `POLL ERROR`, 0 `FATAL`,
  empty `.err`.

**Still open: Bug B** (Firefox anchor reach) and the Phase 2 retry cadence
that Bug B's data justified. Nothing about Bug A's fix touches those.

### DEPLOYED 2026-07-21: Bug B — and the real reason Firefox reads died

Bug B turned out to be two mechanisms, and the second one was invisible until
the `rem=` field printed the exception instead of swallowing it.

**B1 — the anchor could not reach the page document.** Firefox sometimes
gives focus to a single message block whose document *is* that block, with no
TextPattern ancestor above it; those reads never anchored (12 tries, `GIVEUP`,
dark — utt 38, 43, 57). `candidate_patterns()` now ends with the **foreground
window** (`GetForegroundWindow` → `ElementFromHandle`) and a subtree search
for the first TextPattern under it. Measured cost of that search: **9.9ms on
VS Code, 9.8ms on Firefox, 3.3ms on Notepad** — cheap, but it is deliberately
*last* so the common path never pays for it.

**B2 — the anchored range dies mid-read. This is the one that mattered.**
Firefox rebuilds its accessibility tree as a page re-renders, and every range
into the old tree starts raising. Signature in the log:

```
idx=4 tok='and' found=0 rects=[] rem='<err>'      ... and every token after it
CURSOR rewind x194
```

`locate()` could not tell "no match here" from "this range is dead", so it
rewound 194 times to a `last_good` that was equally dead, and the read stayed
dark to the end. Now `_find()` counts *consecutive COM failures* separately
from misses; `DEAD_ERRORS = 3` flips `Anchor.broken`, and the main loop
rebuilds the anchor mid-read (re-arming the retry window, since the
utterance's original one has usually expired). **This is not a violation of
Phase 4** — Phase 4 says don't discard a *working* anchor during a transient
silence; a range that raises on every call is provably not working.

**Selection validation, finally added** (the latent risk from round 4).
Widening the net to the whole foreground window made "take the first
non-empty selection" genuinely dangerous, so `_selection_matches()` now
compares normalized text (lowercase, alphanumerics only, first 20 chars,
either may be the longer). Mismatch logs `stale selection ignored` and falls
through to the FindText heads.

**Verified 2026-07-21:**

- Dead-range handling, forced for real: anchored and painting in Notepad
  (idx=11 `moves`, real rect), then Notepad was killed mid-read. Log:
  `rem="<err COMError: (-2147220991, 'An event was unable to invoke any of
  the subscribers')"` → `ANCHOR DEAD utt=1 (COMError…) -- re-anchoring
  mid-read`, and **CURSOR rewinds: 0** (was 194). `-2147220991` is
  `UIA_E_ELEMENTNOTAVAILABLE` — exactly what a rebuilt Firefox tree yields.
- Selection validation unit-checked: exact match / longer selection / short
  selection all accepted, unrelated clipboard text rejected, empty rejected.
- No regression: full Notepad read **48/48 painted (100%)**, anchor on try#1
  in 0.02s, zero stale-selection rejections, zero rewinds, zero `POLL ERROR`.

**Still open:** the Phase 2 retry cadence (Firefox needed 2–12 attempts at
0.5s apart; the first attempt almost always fails there because the a11y
engine is cold). And **VS Code terminal reads remain ~0-2%** — but the log
now shows why, and it is not a bug to fix: the window-level document
interleaves the terminal's text with chrome (`rem=' Analyze and plan
highlight sy'` = the tab title), so token order does not follow reading
order. That is §6's "terminals are impossible" reasserting itself one level
up. The 2026-07-21 22:45 batch that painted 6 lines was luck, not support.

### DEPLOYED 2026-07-21 (late): why some pages worked and Gmail never did

User report: "some pages work, claude.ai works sometimes, Gmail not at all."
Both causes found by probing the live a11y tree, not by reasoning.

**1. The foreground-window fallback was finding the URL bar.**
`FindFirst(Descendants, IsTextPatternAvailable)` returns the *first* pattern
in tree order, and in a browser that is browser chrome:

```
cand[1] who=firefox.exe 'Search with Google or enter address' [urlbar-input]
        doc='mail.google.com/mail/u/1/#inbox/FMfcgzQhVWxSxlLJPm'
```

Fixed by searching for `ControlType == Document` **and** TextPattern, and
offering up to `WINDOW_DOCS = 6` of them (`window_candidates()`, factored out
so it can be probed directly with a window handle). On the user's Firefox
that yields all five open pages — including Gmail — with the URL bar demoted
to last resort. Cost: 53ms Firefox, 20ms VS Code.

**2. Gmail's head never matched, because of a mention chip.** The Gmail
document *does* contain the text; the heads the anchor tried did not match it:

```
'My Cousin @Ryan H. was tasked with'  miss
'My Cousin @Ryan H.'                  miss
'My Cousin @Ryan'                     HIT  (rects=1)
```

`@Ryan H.` is its own text run, so any head spanning it fails contiguous
`FindText`. The old ladder bottomed out at *four words* and never got below
the chip. `head_candidates()` now descends 60ch → 30ch → 6 → 4 → 3 → **2
words**, and falls back to the raw first line when the ≥8-char rule would
otherwise return an empty list (short first lines like `Hello.` used to yield
*nothing*). Any inline element — mention, link, `<b>`, emoji — splits a line
the same way, so this is general, not a Gmail quirk.

**Cost of the longer ladder, measured on the pathological case** (text that
matches nowhere, so every candidate × every head is tried): **238ms on VS
Code** (12 FindText over its huge flattened UI document), **69ms on Firefox**
(42 FindText). Inside the 0.5s retry budget, and only paid by reads that were
previously dark anyway. **Note for Phase 2:** if the retry cadence is ever
tightened to ~0.15s, schedule the next attempt *after the previous finishes*
rather than on a fixed timer, or attempts will overlap.

**Verified:** Gmail's exact failed read re-probed against the live document —
the new ladder reaches a hit with a real rect. Regression: Notepad read
**47/47 painted (100%)**, anchor try#1 in 0.02s, `IDLE`→`DROP` clean, empty
`.err`.

### DEPLOYED 2026-07-22: Outlook/Hotmail — it was a tab-count bug, not a site quirk

User: "Gmail and claude.ai now work 100%, Hotmail doesn't — is this
site-specific? We can't tune per site." Correct instinct, wrong culprit. The
log shows the Outlook read offering 8 candidates, and **the Outlook document
is not among them**:

```
cand[1..6]  = the OTHER six tabs (Anikai, VibePlayer, claude.ai, YouTube, Gmail, Anikai)
cand[7]     = urlbar-input, doc='outlook.live.com/mail/0/inbox/id/AQQkADAwATY0MDABL'
ANCHOR FAILED
```

The URL bar proves Outlook was the active tab. It was missing because
`WINDOW_DOCS` was **6** and a browser window exposes **one Document per open
tab** — the 7th tab silently fell off the list. Nothing to do with Outlook;
any site would fail as tab 7. Cap raised to 12.

**Tab ordering, and why it is a correctness fix.** Documents now sort
`IsOffscreen == False` first (stable, so tree order survives within groups).
Measured with Firefox in the foreground: exactly one document reports
`offscreen=0` — the active tab — while background tabs report `offscreen=1`.
The trap: with the window focused, background tabs report **plausible
on-screen rects** (`50,40,2510,1352`), *not* the parked `-31942` they show
when the window is occluded. So anchoring to a background tab whose text
happens to match would paint a marker at a real but wrong position — a
"glitchy highlight", not an absent one. `IsOffscreen` discriminates; rect
coordinates do not. Do not swap this test for a rect check.

**Answering the architectural question, with the evidence to date:** none of
the four fixes contains a site name or a per-site branch, and each one was a
*class* of failure:

| Symptom | Actual mechanism | Scope |
|---|---|---|
| Gmail never highlighted | inline mention chip = its own text run, so contiguous `FindText` of the head missed | any link/bold/emoji/mention, any site |
| claude.ai worked sometimes | Firefox rebuilds the a11y tree on re-render, killing the anchored range | any dynamic/SPA page |
| Hotmail never highlighted | 7th tab dropped by a hardcoded cap | any site, any tab beyond the cap |
| terminal reads ~0% | window document interleaves chrome with content, so token order ≠ reading order | structural, see §6 — not fixable |

The remaining hard limit is text that never reaches the accessibility tree at
all (canvas-rendered: Google Docs, the terminal). That is a wall, not a
tuning problem, and no amount of per-site work would move it.

**CORRECTION, 2026-07-22, same night.** The claim above that Hotmail "was a
tab-count bug, not a site quirk" was stated with more confidence than the
evidence carried, and the user's next session disproved it: with the cap
raised to 12, Outlook **still fails most of the time — "sometimes works but
rarely."** What was actually proven is only that the cap dropped candidates
on a 7-tab window. Whether Outlook's document was among the dropped ones was
never established; it simply was not in the list.

**The intermittency is the clue, and it points away from a site quirk.**
"Rarely works" is the signature of a *lazily built* accessibility tree — the
same shape as Firefox's engine warm-up in §8 round 4, where the first queries
after a cold start return nothing and later ones succeed. If OWA's document
only materializes sometimes, the fix is retry persistence (Phase 2/D3), not
anything Outlook-specific.

**Diagnostic to run next time it fails — one question, one answer:** read the
`cand[]` lines of that utterance and check whether **any** candidate's
`who=`/`doc=` mentions `outlook.live.com`.
- Present but head missed → run-boundary problem, extend the head ladder.
- Present only on later `try#` numbers → lazy tree, Phase 2 fixes it.
- Absent from every attempt → OWA does not expose its content as a Document
  control type at all, and needs a different condition (probe what control
  type it *does* use before writing code).
Do not guess between these three.

---

## 9. Accepted trade-offs (settled — reopen only with new information)

- **Bottom caption strip — NARROWLY REOPENED 2026-07-22.** "No bottom
  transcript" (2026-07-17) still stands for everything the in-place
  highlighter can reach. The user has since asked for a caption box **as a
  fallback for the surfaces it cannot reach** — terminals (§6, proven
  impossible) and pages where anchoring fails: a few lines of text at the
  bottom of the screen with the **sentence** being read highlighted. Deferred,
  not started; the requirements and the open design questions are in
  `plan.md` → "Deferred work" → D1. The load-bearing constraint: it must
  appear *only* when in-place highlighting is not working, or it silently
  becomes the transcript that was rejected.

- **START — superseded 2026-07-17 (§5 item 7):** now = synthesis of the whole first
  clause (observed 1332ms on a 76-char clause; short clauses still ~500–600ms). The
  user explicitly traded start latency for steady flow. Historical figure below:
- **START ≈ 500–600ms** (re-opened and improved 2026-07-16; was 640–800ms nominal,
  1103–1461ms observed on real text). It is ~100% chunk-0 synthesis: 104ms fixed +
  248ms per second of first-chunk audio. `FIRST_CHUNK_AUDIO` is the knob; the CPU
  floor for a usable opening (~1s of audio) is ~350ms. Going materially below that
  needs a GPU (§6 — reconsider on an RTX 3090).
- **G Hub macro** (mouse button: click-down → on release, click-up + Ctrl+Alt+R) works.
  Its ~50ms keystroke delay was tuned out but is **invisible to `START`**, which is
  measured server-side from when Flask receives the POST. Measuring it would require
  timestamping in AHK and sending it with the text.
- **AHK sends Ctrl+C internally.** Windows has no other way to read a selection. The
  clipboard is saved and restored, and it only fires on the hotkey.
- **`KeyWait` + `Sleep(40)` in the AHK** are load-bearing: G Hub releases Ctrl+Alt a few ms
  *after* the handler starts, so without the wait the app receives Ctrl+Alt+C and never
  copies. Do not remove.

---

## 10. How to open the next session

Attach this file and `C:\kokoro\tts_server.py`, then say:

> Continuing a local Kokoro TTS read-aloud project on Windows 11. Full audit attached.
> The budget-driven chunking rewrite in §8 is deployed but unverified — I'm starting there.
> Please read §4 (measured facts) and §6 (rejected options) before suggesting anything.

**Ask the new session to honour §4.** The single largest source of wasted time here was
confident performance estimates that turned out wrong — 20x RT (actual: 4.03x), GPU saving
200ms (actual implication: ~2000ms), kokoro-onnx 2–3x faster (actual: 4% slower). The
instrumentation exists so nothing has to be guessed. Every number in §4 came from this
machine and should be treated as fact; anything not in §4 should be measured, not assumed.
