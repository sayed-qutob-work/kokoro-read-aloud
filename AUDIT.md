# Local Read-Aloud (Kokoro TTS) — Session Audit & Handoff

**Status:** Working, user-accepted 2026-07-16 (perceived start ~200–300ms, flow judged
smooth and seamless in daily use). That day's changes:
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
| `C:\kokoro\start_tts.vbs` | Launches both, hidden, logs to `server.log`. |
| `C:\kokoro\server.log` | Server output when started via the `.vbs`. **Read this first on any failure.** |
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

### OPEN: word-by-word highlighting (Speechify-style), user-requested 2026-07-17

Feasibility established: KPipeline already emits per-word `start_ts`/`end_ts`
(§4, now flowing through `synth()`), and the transforms after synthesis are all
deterministic (stretch ÷ PLAYBACK_SPEED, leading-trim offset, pauses), so the
server can know exactly which word is sounding. Display options assessed:
**(a) always-on-top overlay caption window** — separate small process polling the
server, works for EVERY text source (web, PDF, terminal, editor; other apps'
windows cannot be painted into, so this is the only universal form);
**(b) browser extension** — true in-page highlighting like Speechify, web-only,
substantially more code. Recommended a → optionally b later. Awaiting user's
choice of form factor; not yet built.

---

## 9. Accepted trade-offs (settled — reopen only with new information)

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
