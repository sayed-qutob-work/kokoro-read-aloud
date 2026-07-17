# Session Audit — Highlighter work (2026-07-17)

This is a handoff written at the end of a session the user judged
unproductive ("after changing to opus a lot of things went wrong… I
can't even hear the voice"). It records, honestly and in depth, what
this session did, what state things are in now, what went wrong, and
what a fresh session should do next. Read this first, then AUDIT.md.

---

## 0. BOTTOM LINE UP FRONT

1. **The voice/pacing code was NOT changed this session.** The only file
   modified on disk is `highlighter.py`. `tts_server.py` — which owns
   synthesis, chunking, pacing, and audio playback — is byte-for-byte
   identical to commit `67f3bd9` (the last-known-good version the user
   approved). Verified with `git diff --name-only HEAD` → only
   `highlighter.py`.

2. **Therefore "I can't hear the voice" is a runtime/state problem, not
   a code regression.** Most likely cause: this session fired ~10 rapid
   test `/speak` calls at the running server, which can wedge the
   sounddevice output stream. **The fix to try FIRST in a fresh session
   is a clean server restart** (see §5). The known-good voice should
   return because the code that produces it never changed.

3. **What genuinely improved this session:** in-place word highlighting
   now works in **Firefox** (verified end-to-end — marker tracks each
   spoken word on the real page). Previously it only worked in Notepad.

4. **What is unfinished:** VS Code (.md files) highlighting — the
   *mechanism* is proven but a clean end-to-end test was never captured
   because the test harness kept losing a focus fight between two
   overlapping windows. This is a test-harness problem, not evidence the
   code is wrong, but it is also not confirmed working.

5. **Direction:** the highlighter direction is sound (Firefox is a real
   win). The session's unproductiveness was almost entirely wasted
   motion on VS Code test automation (§7), plus the alarming — but
   code-wise harmless — "no voice" runtime issue.

---

## 1. What was asked (this session's scope)

Continuation of prior work. The standing request across the last few
prompts:

- **Prior sessions (context):** fix arrhythmic pacing (done, in
  `tts_server.py`, already committed) and add Speechify-style
  highlighting of each spoken word **on the original text in its
  original position** — browser, textpad, `.md`, whatever — **no
  bottom transcript, no clone**.
- **This session's specific bug:** "it works in Notepad, but it doesn't
  work in the browser nor any other file (e.g. .md files)." → make
  in-place highlighting work in the browser (default browser = Firefox)
  and in `.md` files (opened in VS Code).

The user did NOT ask for any change to the voice or pacing this session.

---

## 2. What actually changed on disk

| File | Changed? | Notes |
|------|----------|-------|
| `highlighter.py` | **YES** (+134 / −52) | The only code change. See §4. |
| `tts_server.py`  | **NO** | Voice/pacing/audio untouched. Still `67f3bd9`. |
| everything else in repo | NO | |

**Outside the repo (environment changes this session + prior):**
- `C:\Users\MrSp\AppData\Roaming\Code\User\settings.json` — added
  `"editor.accessibilitySupport": "on"`. This was needed so VS Code
  exposes editor text to UI Automation at all. **Reversible** — delete
  that one line to restore default. It also makes VS Code show a "screen
  reader optimized" mode; if the user finds that intrusive, revert it.
- A VS Code tab for `…\scratchpad\hl_test.md` was opened during testing.
- Firefox was **minimized** by a test script (harmless; just un-minimize).
- Many scratchpad files were created (probes, e2e scripts, screenshots).
  All under the session scratchpad dir; none in the repo.

---

## 3. Current runtime state (as observed at audit time)

- Server: running, listening on `127.0.0.1:5111`, `/now` returns
  `{"active":false}` (idle, healthy). Two `python.exe … tts_server.py`
  processes = one server (venv launcher pattern — normal, per memory).
- Highlighter: running (two `pythonw.exe … highlighter.py`). **It was
  last launched with an env var `KOKORO_HL_DEBUG` pointing at a debug
  log** (a temporary logging hook I added — see §4.6). This is benign
  but means the running highlighter is writing a debug file. A clean
  relaunch via `start_tts.vbs` (without that env var) removes it.
- Audio: server is idle and responsive, but the user reports no audible
  voice when triggering a read. Not diagnosed to root cause before the
  audit was requested. **Hypothesis: wedged output stream from repeated
  test reads; restart clears it.** (Not yet proven — first task for the
  fresh session.)

---

## 4. The `highlighter.py` changes, in depth

Full diff saved at:
`…\scratchpad\highlighter_session.diff` (also recoverable via
`git diff HEAD -- highlighter.py`). To discard ALL of this session's
code and return to the committed state: `git checkout -- highlighter.py`.

The highlighter is a **separate process** from the server. It polls
`GET /now` (current word) and `GET /utterance` (original text), finds
that text in whatever app is focused via **UI Automation (UIA)
TextPattern**, and paints a translucent click-through marker over the
current word's on-screen rectangle. It never touches audio. Killing it
changes nothing about the voice.

### 4.1 Why Notepad worked but Firefox/VS Code didn't (root causes found)
- **Notepad** exposes its TextPattern on the *focused element itself*,
  and returns real geometry immediately. The old code only looked at the
  focused element — fine for Notepad, broken elsewhere.
- **Firefox** hangs the TextPattern on a *document ancestor* of the
  focused element, and its accessibility engine **warms up lazily** —
  the first UIA queries after startup return empty selections / zero
  rectangles; later queries succeed. The old code (a) only checked the
  focused element, and (b) anchored exactly once per utterance and
  **cached failed word lookups**, so a single early miss during warm-up
  stuck permanently.
- **VS Code** exposes editor text only when
  `editor.accessibilitySupport: "on"` (now set), AND — critically —
  `FindText` ranges report **zero bounding rectangles** until the range
  is **`Select()`-ed**. VS Code only materializes geometry for the text
  near its accessibility "page"; selecting a word moves the page onto
  it. Proven by probe: pre-select `rects=[]`, after `.Select()`
  `rects=[(809,131,38,17)]`.

### 4.2 New TextPattern discovery (`candidate_patterns`)
Replaced `text_pattern_of_focus()` (focused element only) with a
generator that yields TextPatterns **most-specific-first**: the focused
element, then each ancestor (Firefox's document), then the first
TextPattern descendant. The anchor tries each in turn.

### 4.3 Anchor ret/retry + no miss-caching
- `Anchor.__init__` now loops over `candidate_patterns()` and, for each,
  prefers the live selection, else `FindText` of the utterance's
  **first line only** (a multi-line head can never match).
- `Anchor.locate()` **no longer caches `None` misses** — a word that
  fails to resolve during warm-up is retried on later polls.
- Main loop now **retries anchoring for ~6 s** (`ANCHOR_WINDOW`) at
  ~0.5 s intervals instead of once per utterance — this is what fixes
  Firefox's lazy warm-up.
- Ordered resolution: already-passed words get one attempt; the current
  word keeps retrying until it resolves.

### 4.4 VS Code rect fallback (the debatable part)
When a resolved range reports no rectangles, the marker code calls
`rng.Select()` once and re-queries rects. This is the ONLY way to get
word geometry in VS Code. **Side effects the user must accept or reject:**
- It moves VS Code's real cursor/selection to each spoken word (and
  auto-scrolls to follow — which actually keeps the word on screen).
- It can trigger VS Code's `editor.selectionHighlight`, faintly
  highlighting other occurrences of the current word (e.g. every "the").
  Suppressible only via a **global** editor setting — NOT applied,
  because it degrades normal editing and the user should decide.

**Open question for the user:** is Select()-based highlighting in VS
Code worth these side effects, or should `.md` reading just recommend
opening the file in Firefox (which highlights cleanly with no side
effects)? This was never resolved.

### 4.5 Guards added
`words` bounds-checked before indexing; `idx >= len(words)` skipped.

### 4.6 TEMPORARY debug hook (should be removed before commit)
Added an env-gated block: if `KOKORO_HL_DEBUG` is set to a file path,
the highlighter appends `chunk/idx/token/rects` per draw. Used for
ground-truth verification instead of screenshots. **A fresh session
should delete this block** (the `import os`, the `DEBUG =` line, and the
`if DEBUG:` block near the end of `main()`) before any commit — it is
scaffolding, not a feature.

---

## 5. FIRST STEPS for the fresh session (do these in order)

1. **Restore the voice.** Clean-restart the server per the memory's
   restart discipline:
   - Kill both `python*.exe … tts_server.py` processes.
   - Confirm port 5111 is empty (`Get-NetTCPConnection -LocalPort 5111`).
   - Relaunch via `C:\kokoro\start_tts.vbs`.
   - Test one read. The voice/pacing should be exactly as before — the
     code is unchanged. If it is NOT, that is new information and worth
     real investigation (but do not assume it; verify first).
2. **Relaunch the highlighter without the debug env var** (start_tts.vbs
   does this) so it stops writing the debug log.
3. Only then continue on highlighting.

---

## 6. Verification status of the highlighting

| App | Status | Evidence |
|-----|--------|----------|
| Notepad | Worked before this session; **regression NOT re-checked** | Prior session screenshot. The `candidate_patterns` rewrite changed discovery; a 10-second Notepad re-check is owed. |
| Firefox | **VERIFIED working this session** | Debug log showed every word resolving to distinct, correct rects (x marching 87→137→192…884); screenshots showed the marker on "bridge" then "the" tracking the voice. Native Ctrl+A selection also present (test artifact, harmless). |
| VS Code (.md) | Mechanism **proven**, clean E2E **NOT captured** | Probe proved `Select()`→rects. One debug-log run resolved all words to real editor rects (x 58→884 at the editor's coords) once the editor was truly focused — but that run's cleanliness is uncertain because of the focus fight in §7. Needs one honest manual confirmation. |
| Chromium extension | Untouched; optional | User's default is Firefox, so it never applied. Left as-is for Chromium users. |

---

## 7. What went wrong / where the session burned time (honest)

- **Priority inversion.** Firefox (the primary target, the user's default
  browser) was the clean, easy win, but I spent most of the session deep
  in VS Code's much harder accessibility model before verifying Firefox.
  Firefox should have been locked down first.
- **Test-harness focus fight.** To E2E-test VS Code I needed VS Code
  genuinely foreground with its editor focused. `SetForegroundWindow`
  is blocked by Windows' foreground lock; two windows (Firefox + VS
  Code) held the **same** paragraph text, so my scripts repeatedly
  anchored to the wrong window and produced misleading "passing" logs
  (identical rects that were actually Firefox's). I only caught this by
  noticing coordinates that matched Firefox exactly. This cost several
  iterations: `AttachThreadInput` foregrounding, minimizing Firefox,
  then hunting for the editor's screen coordinates. This was **test
  automation churn, not product debugging** — and it read as "going in
  circles" because it was.
- **The "no voice" scare.** Firing many rapid `/speak` calls at the live
  server during testing is the likely cause of the wedged audio. I
  should have restarted the server between test bursts and never let the
  audio path get into an unknown state while the user might try to use
  it.
- **Model switch timing.** The `/model` switch to Opus 4.8 happened
  partway through (user-initiated). For the record: no voice/pacing code
  was edited before or after the switch, so the "no voice" symptom is
  not attributable to a code change from either model. The wasted motion
  on VS Code focus automation is the real productivity loss.

---

## 8. Open decisions the user still needs to weigh in on

1. **VS Code `.md` highlighting:** accept `Select()` side effects
   (cursor jumps, possible occurrence-highlight flicker), OR drop VS
   Code editor support and recommend reading `.md` in Firefox?
2. If keeping VS Code: may I set `editor.selectionHighlight: false`
   (global) to remove the occurrence flicker? (Not done — needs consent.)
3. Keep `editor.accessibilitySupport: "on"`? It's required for VS Code
   highlighting but changes the editing experience slightly.

---

## 9. How to fully revert this session (if desired)

- Code: `git checkout -- highlighter.py` (returns to committed 67f3bd9).
- VS Code settings: remove `"editor.accessibilitySupport": "on"` from
  `…\Code\User\settings.json`.
- Un-minimize Firefox; close the `hl_test.md` scratchpad tab in VS Code.
- Restart server + highlighter via `start_tts.vbs`.

After that, the system is exactly as it was at the start of this
session, with the known-good voice and Notepad-only highlighting.

---

## 10. Key facts / gotchas for whoever continues

- Editing `tts_server.py` or `highlighter.py` does nothing until the
  process is restarted (per memory's restart discipline).
- Two `python(w).exe` per tool = venv launcher pattern; not a bug.
- Firefox UIA warms up lazily → always allow retry/anchor windows.
- VS Code exposes editor text only with `accessibilitySupport: on`, and
  geometry only for `Select()`-ed / visible-page ranges.
- Windows foreground lock defeats `SetForegroundWindow`; real mouse
  input or `AttachThreadInput` is needed to move foreground in tests —
  and beware two windows holding identical text (false positives).
- Don't stress-test `/speak` against the live server the user is using;
  restart between bursts.
