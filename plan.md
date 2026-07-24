# Highlighting fix plan

**Date:** 2026-07-21. **Scope:** the in-place word highlighter (`highlighter.py` +
the `/now` / `/utterance` server side). The browser extension (`extension/`) is
Chromium-only and mostly out of scope; noted where relevant.

**User-reported symptoms (real daily use):**

- **A.** Sometimes the highlight doesn't work at all for a read.
- **B.** Sometimes it glitches.
- **C.** Sometimes it starts dead and suddenly begins working mid-sentence,
  or two–three lines in.

**Analysis method:** full read of `highlighter.py`, the `/now`, `/utterance`,
`_synth_loop`, `_play_loop` code in `tts_server.py`, `start_tts.vbs`, and the
extension. No live measurements yet — `server.log` was truncated by today's
restart and the highlighter runs with no logging (see Phase 0). Per AUDIT.md §4
discipline, every mechanism below is *code-verified* (the logic path exists and
produces the symptom) but frequency ranking must come from the debug log, not
from guessing.

---

## Root causes, mapped to symptoms

### RC1 — Anchor acquisition is slow by design → symptom C

Timeline of a read today:

1. Voice starts. Highlighter notices within `POLL_IDLE` = 0.12s. Fine.
2. First anchor attempt fires. In Firefox the accessibility engine warms up
   lazily — the first queries return empty selections / FindText misses
   (documented in AUDIT §8 round 4).
3. Retries happen only every **0.5s** (`ANCHOR_RETRY`), each preceded by a
   `/utterance` fetch that itself can time out (RC9) and burn the slot.

Two to four failed rounds = 1–2+ seconds of speech at 2.07x = "starts working
mid-sentence / after two–three lines". This is symptom C, mechanically.

### RC2 — 6-second give-up is permanent → symptom A

`ANCHOR_WINDOW = 6.0`: if anchoring hasn't succeeded 6s after the utterance is
first seen, the highlighter stops trying **for the whole utterance** — a
5-minute read stays dark because the app was slow in its first 6 seconds.
Silent: no log, no fallback.

### RC3 — `head_candidates()` can produce nothing, or only misses → symptom A

- Every candidate must be ≥ 8 chars. A read whose first line is short
  ("Hello.", a one-word heading) yields **zero** candidates; if there is no
  live selection (Ctrl+Alt+T clipboard reads, terminals, user clicked away),
  the anchor can never be found.
- The flattening tell is *2+ spaces*. But the server collapses ALL whitespace
  (`re.sub(r"\s+"...)` happens later; the `/utterance` text is the raw
  clipboard) — the real hazard is a first line whose text spans multiple
  document text runs joined by **single** spaces (inline links, `<span>`s,
  formatting boundaries in Firefox). `FindText` only matches contiguous runs,
  so *all four* candidates (60ch / seg 60ch / seg 30ch / first 4 words) can
  cross the same run boundary and miss together.

### RC4 — Stale-selection hijack (the audit's known latent risk) → symptom A

`Anchor.__init__` takes the **first non-empty selection** on any candidate
TextPattern near focus, without comparing it to the utterance text. AUDIT §8
round 4 explicitly deferred this: "Do not 'fix' it without evidence it
actually bites." The user's symptom A is plausibly that evidence: an old
selection in the focused app (or a nearby pattern) anchors the read to the
wrong text; every subsequent token FindText misses; the read shows nothing.
Must be confirmed via debug log (Phase 0) before the fix lands, per the
audit's own rule — but the fix is cheap and safe (validate, else fall through
to FindText heads).

### RC5 — The highlighter can die silently and stay dead → symptom A (whole sessions)

- Launched by `start_tts.vbs` via `pythonw.exe` with **no output capture** and
  `KOKORO_HL_DEBUG` **unset** — a crash is invisible.
- `main()` has no blanket exception guard. Almost every helper swallows its
  own exceptions, but `Marker.draw` does not: if `CreateDIBSection` fails,
  `bits.value` is `None` and `from_address(None)` raises → process exits.
  `d["text"]` (raw indexing) is a second, smaller exposure.
- Nothing restarts or even detects a dead highlighter. Every read for the
  rest of the session shows no highlight — indistinguishable, to the user,
  from any other failure.

### RC6 — Mid-read state wipe on transient `active:false` → symptom B (and C)

`/now` reports `active: false` whenever `t > dur + 0.3` — i.e. whenever
playback timing slips more than 0.3s past the current chunk's end: a
starvation GAP, a slow dequeue, the boundary between chunks landing badly.
The highlighter's reaction (highlighter.py:393-395):

```
anchor, utt_seen = None, None
```

Then the next active poll sees the *same* utterance id and treats it as new:

- Re-anchors from scratch → 0.5s+ dark gap mid-read (symptom B, and the
  "suddenly works again" flavor of C).
- Worse: the new anchor's `remaining` cursor restarts at the **utterance
  head**, but `resolved` only replays the *current chunk's* tokens. Common
  words then FindText-bind to already-spoken occurrences earlier in the text
  — the highlight visibly jumps backwards to the wrong word (symptom B).

The `+0.3` slack also races the audio pipeline: `t0` is stamped when the
chunk is *dequeued*, but `stream.write` returns before the audio is audible
(output latency), so `t` systematically leads the sound.

### RC7 — `FindText` matches substrings, not words → symptom B

UIA `FindText` has no word-boundary concept. Token `"in"` matches the "in"
inside "singing"; `"a"` matches the first "a" anywhere. Short function words
resolved in the not-yet-spoken remainder can bind **inside a longer word**:
the marker paints a fragment of the wrong word, and the `remaining` cursor
advances to the wrong place, desyncing the next few tokens until a longer,
unambiguous token self-heals it. Reads exactly like "glitches, then it works
again".

### RC8 — VS Code `Select()` fallback is one-shot → symptom B (VS Code only)

`select_tried` is per `(chunk, idx)`: if the one `Select()` attempt fails to
materialize rects (page not moved yet, editor busy), that word is permanently
unhighlighted — flicker in VS Code `.md` reads. (The caret-move /
`occurrencesHighlight` tint is the *accepted* cosmetic; not in scope.)

### RC9 — 150ms HTTP timeout vs a saturated CPU → symptom B

`get()` uses `timeout=0.15`. During next-chunk synthesis, torch pegs all 6
P-cores; the Flask thread (dev server, GIL-bound) can easily take >150ms to
answer `/now`. Each timeout reads as "server unreachable" → `marker.hide()`
for a poll → flicker **correlated with chunk boundaries**. (It does not wipe
the anchor — only `active:false` does — so this is flicker, not the RC6 wipe.)

### RC10 — Minor, listed for completeness

- `chunk_seen` is keyed by chunk *text*: two chunks with identical text
  (repeated lines) reuse stale `token_ranges` → backwards highlight. Rare.
- Chromium browsers get no UIA TextPattern by default — by design; the
  extension covers them, but only if loaded and only when the selection was
  made in-page. Firefox/VS Code users never see this.
- Extension: the 12-word bounded scan in `mapChunk` loses alignment if
  sanitize dropped many tokens; background-tab timer throttling stalls polls.
  Only relevant if the user actually reads in Chromium.

---

## Fix plan — phased, measure-first (AUDIT §4 discipline)

### Phase 0 — Instrument before touching logic — **DEPLOYED 2026-07-21**

Steps 1 and 2 are done and verified live (AUDIT §8, 2026-07-21 entry).
Step 3 — a day or two of real reads, then rank RC1–RC10 from
`highlighter.log` — is the remaining gate before Phase 2.


The debug infrastructure exists and diagnosed Round 4; it is simply off.

1. In `start_tts.vbs`, set `KOKORO_HL_DEBUG=C:\kokoro\highlighter.log` for the
   highlighter process (and truncate/rotate the log at launch so it can run
   permanently without bloating).
2. Add to `dlog` coverage: a startup line (proves the process is alive), a
   line on every `active:false`-triggered state wipe (proves/denies RC6
   frequency), a line on `/now`//`/utterance` timeouts (RC9), a line when the
   anchor window expires (RC2), and an unmissable line on any uncaught
   exception (RC5).
3. Reproduce each symptom in daily use for a day or two; read the log. Rank
   RC1–RC9 by observed frequency. **Expected confirmations to look for:**
   - Symptom C reads: N failed anchor rounds before success (RC1).
   - Symptom A reads: window expiry (RC2), zero/missed heads (RC3), or a
     selection anchor whose doc head doesn't contain the utterance (RC4).
   - Symptom B reads: wipe lines mid-utterance (RC6) and/or token rects
     landing inside longer words (RC7), timeouts clustering at chunk
     boundaries (RC9).

### Phase 1 — Crash-proofing (RC5) — **DEPLOYED 2026-07-21** (items 1–2; 3 deferred)

1. Wrap the body of `main()`'s loop in a catch-log-continue guard (never let
   one bad poll kill the process).
2. Guard `Marker.draw` (check `CreateDIBSection` result; hide-and-return on
   any GDI failure) and replace raw `d["text"]` indexing with `.get`.
3. Optional hardening: a tiny watchdog (the `.vbs` or a scheduled task
   relaunching the highlighter if its process is gone).

### Phase 2 — Fast, persistent anchoring (RC1, RC2)

1. Retry cadence: aggressive at first (~every 0.15s for the first ~2s), then
   back off to 0.5s. Rationale: the common case (warm app, live selection)
   anchors on attempt 1; the Firefox-cold case shouldn't wait half a second
   between tries while the voice runs ahead.
2. Remove the hard 6s give-up: keep retrying at a low rate (e.g. every 1–2s)
   for as long as the utterance is active. A late highlight beats none.
3. Do not raise `POLL_IDLE`; 0.12s is already the floor of "noticing" a read.

### Phase 3 — Anchor correctness (RC3, RC4) — **items 1 & 3 DEPLOYED 2026-07-21**

Selection validation (item 1) and a wider candidate net (item 3, via the
foreground window rather than the second line's head) are in; see AUDIT §8
"Bug B". `head_candidates` (item 2) untouched — no read has yet failed for
the short-first-line reason it describes.

1. Validate the selection before trusting it: normalized comparison of the
   selection text against the utterance head (same normalization the
   extension uses: lowercase, strip non-alphanumerics). Mismatch → fall
   through to FindText heads instead of anchoring wrong. This closes the
   audit's latent risk *with* the evidence Phase 0 gathers, honoring the
   "don't fix without evidence" note.
2. `head_candidates` fixes:
   - Drop the ≥8-char minimum when it would leave the list empty (a short
     first word is still better than nothing).
   - Add progressively shorter prefixes ending at *word* boundaries (first 6,
     4, 2 words of the first line), longest-first as today, so a run-boundary
     inside the line eventually stops mattering.
   - Keep the longest-first ordering (short fragments matching nav items is a
     real, previously-observed failure).
3. On repeated all-heads-miss, try FindText of the *second* line's head —
   the first line may be a heading rendered in a separate run.

### Phase 4 — Survive transient inactivity (RC6) — **DEPLOYED 2026-07-21** (item 1; 2–3 not needed)

Measured first: RC6 hit 5 of 19 reads (~26%) and cost the *whole rest* of a
read, not a gap. Item 1 (a 2s grace in the highlighter) is deployed and
verified on both paths. Items 2 (server-side `/now` change) and 3 (latency
offset) were not needed once the grace was in place — leave them unless
`RESUME` lines reappear. Phase 6 is **unjustified by the data** (0 fetch
timeouts, ever). Phase 2 was called unjustified too — **that was corrected
the same night**: it held only because the sample had no Firefox reads. See
AUDIT §8 "DIAGNOSED 2026-07-21 (late)" for the two remaining bugs and the
priority order.

1. In the highlighter: on `active:false`, **do not wipe state immediately**.
   Keep `anchor`, `utt_seen`, `remaining`, and the resolved-token cache for a
   grace period (~2s) / until a *different* `utt` id appears. If the same
   utterance resumes, continue exactly where it left off — no re-anchor, no
   cursor reset, no backwards jumps.
2. Server side (optional, cleaner): let `/now` distinguish "utterance still
   in flight, between chunks" from "read finished" — e.g. keep `active:true`
   with `word:-1` while `pending`/synthesis for the current gen is non-empty.
   Then the highlighter needs no heuristics at all.
3. Re-check the `+0.3s` slack and the `t0`-at-dequeue lead against the output
   stream latency; if the lead is measurable, subtract a fixed offset.

### Phase 5 — Token matching precision (RC7) — **DEPLOYED 2026-07-21**

Both items done, plus a cursor-rewind rule the plan didn't anticipate (a bad
hit was permanent, not merely local). See AUDIT §8 "Bug A fixed".

1. After a `FindText` hit, verify word boundaries: expand the range by one
   character on each side (Move/GetText) and require non-letter neighbors.
   On failure, resume the search past the bogus hit (bounded retries) before
   giving up for the poll.
2. Keep misses uncached (already the case) so later polls can succeed.

### Phase 6 — Small robustness items

1. `/now`/`/utterance` fetch timeout 0.15 → ~0.3s, and/or a persistent
   HTTP connection (`http.client` keep-alive) to cut per-poll overhead (RC9).
2. Allow the VS Code `Select()` fallback a second attempt after a short delay
   (bounded — never per-poll) (RC8).
3. Key `chunk_seen`/`token_ranges` on a chunk *counter* from `/now` rather
   than chunk text (server adds an index field) (RC10).

### Verification (per phase, live, before moving on)

- **C gone:** first word of a Firefox read highlighted after a browser cold
  start (worst case: within ~1 word, not 2–3 lines). Debug log shows anchor
  on attempt ≤2 warm, ≤~1s cold.
- **A gone:** short-first-line reads, clipboard (Ctrl+Alt+T) reads with no
  selection, and reads with a stale selection elsewhere all anchor correctly
  or log exactly why not. Zero silent give-ups; zero process deaths across
  days (startup lines in the log match reboots).
- **B gone:** an interrupted/starved read (force one with a huge text)
  resumes highlighting in place with no backwards jump; short function words
  never paint inside longer words (spot-check the rect log); no flicker at
  chunk boundaries.

### Explicitly out of scope

- Terminals (impossible — AUDIT §6, proven).
- The VS Code caret/occurrences tint (accepted cosmetic, has a user-side
  setting).
- ~~The bottom-caption overlay (retired by user preference).~~ **Reopened
  2026-07-22 as a fallback only — see "Deferred work" below.**
- Chromium-extension parity work, unless the user actually reads in Chromium.

---

## Deferred work (agreed, not started)

### D1 — Caption-box fallback for surfaces in-place can never reach (user-requested 2026-07-22)

**Do not build this without re-reading the trade-off note in AUDIT §9.** The
bottom strip was *retired* on 2026-07-17 because the user wanted no bottom
transcript. This is a deliberate, narrow reopening by the same user: **not a
replacement for in-place highlighting — a fallback for the cases where
in-place is provably impossible.**

What was asked for:

- A box at the bottom of the screen holding **a few lines** (~4) of the text
  being read — not one line, and not the whole utterance.
- **Sentence-level** highlighting: the sentence currently being spoken is
  highlighted, rather than the single word. (The retired `overlay.py` did
  word-level on a single chunk; this is a different granularity and a
  different amount of context.)
- It exists for terminals (§6, proven impossible) and for sites/apps where
  anchoring fails or no usable TextPattern exists.

Design notes for whoever picks this up:

- **`overlay.py` is the starting point, not the answer.** It already has the
  hard parts: frameless topmost tkinter window, bottom-centre, drag to move,
  right-click to close, 80ms/500ms polling of `/now`, no focus stealing. It
  needs: multi-line layout, sentence segmentation, sentence highlight, and a
  trigger.
- **The trigger is the real design question.** The box must appear *only*
  when in-place highlighting isn't working, or it becomes the bottom
  transcript the user rejected. The highlighter already knows: it logs
  `GIVEUP` (never anchored), `ANCHOR DEAD` without recovery, and sustained
  `found=0`. Two shapes, undecided:
  (a) the highlighter owns the box — it has the state already, but it is a
      ctypes/message-pump process and tkinter would have to coexist with
      `pump()`;
  (b) the box stays a separate process and the highlighter publishes its
      state (a status file, or a new field on `/now` if the server grows one).
  Pick with evidence, not taste.
- **Sentence segmentation is nearly free server-side.** §5 chunking already
  cuts on clause/sentence punctuation, and `/now` returns the current chunk
  plus word timings, so "the sentence being read" ≈ the current chunk. Prefer
  that to re-splitting text in the box.
- Don't let it steal focus, don't let it cover the taskbar, and keep
  right-click-to-close.

### D2 — Hotmail/Outlook still failing (open, undiagnosed)

Fixed candidates-cap bug did **not** resolve it: the user reports Outlook
"sometimes works but rarely". The *intermittent* shape is the clue —
see AUDIT §8 for the one-line diagnostic to run next time it fails.

### D3 — Phase 2 retry cadence

Still justified for Firefox only (cold a11y engine). Note the constraint
recorded in AUDIT §8: a failing attempt costs up to 238ms, so schedule the
next attempt *after* the previous finishes rather than on a fixed timer.

---

# 2026-07-25 — Re-diagnosis from real daily use

**Trigger:** user reports "issues with the word highlighting" after two days
of daily use. **Nothing was changed.** This section is diagnosis + plan only.
Method: structural parse of `highlighter.log` (`HH:MM:SS ` prefix + keyword,
never substring — AUDIT §8's warning: the user reads this project's own prose
aloud, so token lines contain the words `RESUME`, `GIVEUP`, `ANCHOR`), plus a
full re-read of `highlighter.py`.

## 0. Read this before the findings: the sample is 16 minutes, not two days

**The two days of evidence the user is describing no longer exists.**
`log_init()` rotates exactly *one* generation (`highlighter.log` →
`.log.1`) at every start, and the highlighter has restarted several times
since. What survives:

| File | Span | Content |
|---|---|---|
| `highlighter.log` | 07-24 23:58 → 07-25 00:17 | **7 reads.** The entire usable sample. |
| `highlighter.log.1` | 07-24 22:58 → 23:57 | 12,678 `FETCH fail` lines. Zero reads — this is the hour the server was down with the SAC/spaCy `ImportError` (AUDIT §7). |

**And the 7 reads are a biased sample.** They were taken during a debugging
session, so **4 of the 7 are VS Code integrated-terminal reads** — the one
surface AUDIT §6 proves cannot work. The surfaces the highlighter actually
exists for (Firefox on ordinary pages, `.md` in the VS Code editor, Gmail,
Outlook) appear **once each at most**, and there is no `.md`-editor read at
all. AUDIT §8 already burned a whole ranking by generalising from a sample
that was missing the relevant surfaces (the "Phase 2 is unjustified" claim,
corrected the same night). **Do not rank root causes by frequency from this
sample.** Fixing the evidence pipeline is therefore P0 below, not an
afterthought.

## 1. The seven reads

| utt | Surface (from `who=`/`doc=`/`rem=`) | Anchor | Painted / located tokens |
|---|---|---|---|
| 1 | **Unknown — zero candidates offered.** No `cand[]` line at all: `candidate_patterns()` yielded nothing | never anchored, 4 tries | — (read fully dark) |
| 2 | VS Code **window** document; read was the integrated terminal (`cand[0]` = `xterm-helper-textarea`) | try#2, +0.60s | **0 / 87** |
| 3 | same | try#1, +0.02s | **1 / 209** |
| 4 | Firefox, a Cloudflare error page | try#2, +0.62s | **3 / 27 (11%)** |
| 5 | Firefox, Cloudflare interstitial ("Just a moment…") | try#1, +0.03s, `cand=0` | **55 / 56 (98%)** then `ANCHOR DEAD`, 9 failed re-anchors, tail dark |
| 6 | VS Code window doc; terminal read | try#1, +0.04s | **3 / 259 (1%)** |
| 7 | same | try#1, +0.02s | **0 / 16** |

Aggregate: **62 of 654 unique tokens painted (9%)**. One read (utt 5) was
excellent until it died. Every other read was ≥89% dark.

**"Painted" is a ceiling on success, not a measure of it.** The log records
that a rect was drawn, never that it was drawn on the *right* word. AUDIT §8
documents the counter-case explicitly — anchoring to the wrong document
yields "a real but wrong position, a glitchy highlight rather than an absent
one." utt 6's 3 painted tokens sat at y=1064 in a window-level VS Code
document and may well have landed on chrome. Treat every "painted" figure
here as an upper bound.

### Mapping back to the user's original symptoms

| Symptom (plan.md, user-reported) | Findings that produce it |
|---|---|
| **A** — the read never highlights at all | **F3** (wrong anchor held all read), **F8** (no TextPattern anywhere), **F2a** (cursor collapses two words in), and the clipboard/terminal classes in §4 |
| **B** — it glitches | **F7** (marker hides on every missed poll), **F2b** (cursor wanders into chrome, marker jumps), **F1** (anchor dies mid-read) |
| **C** — starts dead, works a line or two in | **F6** (anchored only on try#2, +0.60s) |

Symptom **B** is *not* an audio-sync problem — the user confirmed 2026-07-25
that the marker sits on the word being heard (§3a). B is F7/F2b/F1.

**Where the user actually reads (answered 2026-07-25):** ordinary **Firefox**
web pages **and Claude Code / terminal output**, roughly co-equally. That
settles two things this sample could not. First, the Firefox half is exactly
what P1–P5 fix, and utt 5 — 98% correct until `Select()` killed it — is a
representative read, not an outlier. Second, **the terminal half is not a
sampling artifact of a debugging session; it is half the daily use, and it
is the half that is structurally unfixable in place.** D1 is therefore a
first-class deliverable, not a someday item — see §6.

## 2. Findings — OBSERVED in this sample

### F1 — `Select()`, a VS Code-editor hack, fires on every surface and is followed by collapse 8 times out of 10 · **highest-value fix**

`highlighter.py:877-895` calls `rng.Select()` + a caret collapse whenever a
located range reports no rectangles. That was written for **one** case: the
VS Code *editor*, which materialises geometry only near its accessibility
page (AUDIT §8 round 4). It is currently invoked on **any** anchor — a
window-level document, a live Firefox page — where it does not move an
accessibility page, it moves *the target app's real selection*.

Measured across the sample:

- **10 `sel=1` polls. Geometry produced: 0.** The fallback has not worked
  once in this sample — every `sel=1` line still logs `rects=[]`.
- **8 of those 10 are followed within 1–3 polls by `found=0` or
  `ANCHOR DEAD`.**
- utt 5 is the clean case: 117 consecutive painting polls in Firefox → one
  `sel=1` on `troubleshooting` → the *very next poll* is
  `rem="<err COMError: (-2147220995, 'Object is not connected to server')"`
  → `ANCHOR DEAD` → 9 failed re-anchors → rest of the read lost.
  A programmatic `Select()` into a live page forces a selection change and a
  scroll, and Firefox rebuilds its a11y tree on re-render (AUDIT §8) — which
  is exactly the error that came back.

**Honest caveat, state it in any writeup:** `sel=1` fires *only when a hit
already had no rects*, so it also marks an already-suspect match. Correlation
is not proof of causation here. But the fix is cheap and the downside is nil
(the fallback produced geometry 0/10 times), and gating it is itself the
experiment: if utt-5-style deaths stop, F1 was causal.

### F2 — the cursor-rewind recovery added on 2026-07-21 cannot recover, by construction

`locate()` assigns the rewind target **after** advancing the cursor
(`highlighter.py:685-689`):

```python
nxt = self.remaining.Clone()
nxt.MoveEndpointByRange(Start, r, End)
self.remaining = nxt
self.last_good = nxt.Clone()     # <-- the ALREADY-ADVANCED cursor
```

So when the advance itself is what destroyed the cursor, `last_good` is
equally destroyed and every rewind restores the same dead position.
Observed cost: **245 `CURSOR rewind` lines across the sample, all futile** —
utt 6 rewound **112 times** while `rem=''` on all 639 subsequent polls; utt 3
rewound 86 times, utt 2 39 times.

This is a **regression against `plan.md`'s own Phase 5 text**, which said
"reset `remaining` to *the anchor's original range*". The implementation
substituted `last_good` and the original range is never retained — there is
no recovery path left.

Two distinct sub-cases, and AUDIT's own rule about not lumping failures
applies:

- **F2a — degenerate cursor (`rem=''`).** utt 6: after two `sel=1` polls the
  range collapses to zero length. `FindText` on an empty range can never
  match, so the read is dark forever. 639 polls, 259 tokens, 3 painted.
  Nothing in the code ever checks whether `remaining` is empty.
- **F2b — cursor runaway into chrome (`rem=` wandering).** utt 3's cursor
  walks `' get started!'` → `' Command Succee'` → `' CodeRabbit to get'` →
  the git status bar. utt 4 (Firefox) walks into the page footer
  (`' by Cloudflare￼Privacy'`, `'Ray ID: a205…'`). `_word_bounded()` (added
  2026-07-21) reduced this but did not close it — and note it **fails open**
  (`except: return True`, line 569), so on a document where endpoint moves
  misbehave every bogus hit is accepted.

### F3 — a wrong anchor is held for the whole read; nothing ever notices it is painting nothing

The first candidate whose head matches wins permanently. There is no check
that it is *working*. utt 2, 3, 6, 7 each held a window-level VS Code
document — where the text exists but is interleaved with tab labels, the
status bar and terminal chrome, so token order ≠ reading order — for the
entire read, at 0–1%. The anchor "succeeded" in the log every time.

The highlighter has all the information needed to know better (it counts
found/missed and has/hasn't rects per token) and uses none of it.

### F4 — mid-read re-anchoring searches for the utterance HEAD, which by then may not exist

After `ANCHOR DEAD`, `main()` rebuilds from `head_candidates(utterance)`.
utt 5: the page had re-rendered from "Just a moment…" to "Checking your
Browser…", so the head `'What to do next:'` was no longer in the document —
9 × `ANCHOR FAILED`, the read stayed dark to the end. Mid-read, the right
search string is the **current chunk** (`/now`'s `text`), which is where the
voice actually is. Note `/now`'s text is *sanitized*, so it needs its own
head logic rather than reusing `head_candidates()` unchanged. Re-anchoring
on the head also resets the cursor to the utterance start — the backwards-
jump mechanism from RC6.

### F5 — duplicate candidates burn the anchor budget

utt 5, every attempt: `cand[0]`, `cand[1]`, `cand[2]` are all
`firefox.exe 'Just a moment...'` with byte-identical `doc=` heads — the
focused element, an ancestor and the window document resolving to the same
thing. Each gets a **full head ladder** (up to 7 `FindText` per candidate,
measured at 238ms for the pathological case, AUDIT §8). With
`ANCHOR_RETRY = 0.5s` a large share of the retry budget is spent re-searching
the same document.

### F6 — ~0.6s of every second read is dark at the start (D3/Phase 2, now with evidence)

utt 2 and utt 4 both failed on try#1 and anchored on try#2 at **+0.60s /
+0.62s** — 2 of the 7 reads, and at 2.07x reading speed that is 2–3 words
gone. This is symptom **C** ("starts dead, begins working a line in"),
mechanically. Previously this was justified for Firefox only; utt 2 is VS
Code, so the cold-first-attempt case is broader than assumed. AUDIT's
constraint stands: schedule the next attempt *after* the previous finishes
(a failed attempt costs up to 238ms), never on a fixed timer.

### F7 — the marker hides on every single missed poll → visible flicker

`main()` calls `marker.hide()` whenever a token is not located *or* reports
no rects (lines 862-863, and `draw([])` → `hide()`). During a partly-working
read the highlight therefore blinks out on every miss and returns on the next
hit. This is a strong candidate for the user's "it glitches" (symptom B) on
reads that are otherwise anchored correctly. A short hold (keep the last rect
for ~2-3 polls before hiding) would cost nothing.

### F8 — some reads have no TextPattern anywhere, and the user gets no signal

utt 1 logged **no `cand[]` lines at all** — `candidate_patterns()` yielded
zero candidates including the foreground-window fallback. Fully dark, no
`GIVEUP` (the read ended before the 6s window), nothing to distinguish it
from a crashed highlighter. This is distinct from F3 (anchored to the wrong
document).

### F9 — startup fetch storm

97 failures in 27s at ~12/s while the server was still booting; 12,678 over
the dead-server hour in `.log.1`. It is not a highlighting bug, but it burns
CPU all day whenever the server is down, and it drowns the log. A backoff
when `/now` is unreachable is a few lines.

## 3a. Highlight-vs-audio sync — ANSWERED BY THE USER 2026-07-25: not an issue

**Asked and answered: when the highlight works, the marker sits on the word
being heard.** So the `t0`-at-dequeue lead (Phase 4 item 3) is not costing
anything perceptible and **this whole class is out of scope** — do not spend
time on output-latency offsets. Retained below only so a future session does
not re-derive the mechanism and assume it is untested.

The mechanism, for the record:

Every finding above is log-derived, and `highlighter.log` records only
*where* the marker was drawn — never whether it was drawn **at the moment
that word was audible**. A user complaint of the form "the highlight runs
ahead of the voice" would leave no trace anywhere in §2, and there is a
known, written-down, **never-measured** mechanism for exactly that:

- `/now` stamps `t0` when a chunk is **dequeued**, but `stream.write()`
  returns before the audio is actually audible (output-stream latency), so
  `t` systematically **leads** the sound. Already flagged as Phase 4 item 3
  and never acted on ("if the lead is measurable, subtract a fixed offset").
- `now()` also treats the read as over at `t > dur + 0.3` (`tts_server.py`
  :531); that slack was never checked against the same latency.
- On top of that: `POLL_ACTIVE = 0.08` plus the HTTP round trip adds its own
  lag in the *opposite* direction.

If it is ever reopened, **measure, do not estimate** (AUDIT §4): query the
output latency from the live PortAudio stream, and screen-capture a read
alongside its audio to compare the frame where the marker lands against the
sample where that word starts. As of 2026-07-25 there is no reason to.

## 3b. Two classes that no phase below can fix

- **Ctrl+Alt+T clipboard reads.** Text read from the clipboard need not be
  displayed on screen anywhere, so there is no range to anchor to — not an
  anchoring bug, a definitional one. A D1 case. (Distinct from F8, where
  focus simply exposed no TextPattern; don't merge them, and this sample
  cannot say how often either happens.)
- **Canvas-rendered text** (Google Docs, the terminal) — AUDIT §6, a wall.

## 3. Code-verified but NOT observed in this sample — do not act on these yet

- **RC3** short-first-line / run-boundary heads: the ladder now reaches two
  words and no read in this sample failed for this reason.
- **RC8** one-shot `Select()` per token — superseded by F1; the fallback
  should be *narrowed*, not retried more.
- **RC10** `chunk_seen` keyed by chunk *text* (identical repeated chunks
  reuse stale `token_ranges`). Zero occurrences here.
- **RC6** mid-read wipe: **the 2026-07-21 GRACE fix is holding.** 6 `IDLE` →
  6 `DROP`, zero `RESUME`, zero `HELD`-then-lost. Do not touch `GRACE`.
- **RC9** HTTP timeout: zero `FETCH fail` *during* a read. Still unjustified.
- **RC5** crashes: zero `POLL ERROR`, zero `FATAL`, `highlighter.err` empty.
  The Phase 1 guards are doing their job.
- Chromium extension items — the user reads in Firefox; untested, unchanged.

## 4. Structurally impossible — not in scope (AUDIT §6, do not reopen)

The VS Code integrated terminal. 4 of the 7 reads. The window-level document
does contain the terminal's text, which is why a few tokens land, but it
interleaves it with tab labels, the status bar and the accessible-buffer
hint, so reading order is not document order. **The fix for this surface is
not better anchoring — it is detecting the failure and handing off to the
caption box (D1).** If any plan item reads like "make the window document
work", a rejected option has been reopened.

## 5. Plan

Ordered by (evidence × cheapness), not by severity. Each phase states its
own verification; nothing proceeds on a hunch.

### P0 — Stop destroying the evidence — **DEPLOYED 2026-07-25**

`LOG_KEEP = 5` generations; one `SUMMARY` line per read at `DROP`. Item 3 (a
day of normal use) is the user's step and is the gate before P3/P4/P5.
See AUDIT §8 "DEPLOYED 2026-07-25".

1. Keep **N generations** of `highlighter.log` (e.g. `.1` … `.5`), or
   date-stamp them. One generation is why "two days of issues" is a
   16-minute sample.
2. Emit **one `SUMMARY` line per utterance** at `DROP`: app (`who=`), anchor
   path (selection / findtext / which candidate), tries, tokens
   located/painted/missed, rewinds, whether it ended `DEAD`. Then ranking is
   `Select-String SUMMARY`, not a parser — and the per-token lines can stay
   for detail.
3. Ask the user to use the tool normally for a day **without a debugging
   session skewing it**, so the sample contains the surfaces that matter.

*Verify:* a day of use yields ≥20 SUMMARY lines covering ≥3 distinct apps.

### P0b — Give candidates provenance and identity — **DEPLOYED 2026-07-25**

`candidate_patterns()` currently yields bare `(tp, el)`. Three later phases
each need more than that, and it would be silly to re-derive it three times:

- **P1** must know *how* a candidate was reached (focused element vs
  ancestor vs window document) to gate `Select()`.
- **P5** needs candidates to be comparable, to dedup utt 5's three identical
  `'Just a moment...'` entries.
- **P3** needs the list to be labelled and re-enterable so a rejected anchor
  can be skipped and the next one tried.

Yield `(tp, el, how, ident)` — where `how` is the provenance and `ident` is
something comparable (runtime id, or `doc=` head as a fallback). Do this
before P1.

### P1 — Stop the self-inflicted damage (F1) — **DEPLOYED 2026-07-25**

Gated via `Anchor.can_select` (route must be `focus`) + `Anchor.resync()`.
**Its two verification reads are still outstanding and need the user** — a
Firefox article (expect `sel=0`, no `ANCHOR DEAD`) and a `.md` file in the VS
Code editor (expect `route=focus`, `sel>0`, high `painted=`; if that one is
dark, the gate is too tight).

Gate the `Select()` fallback to the anchor that needs it: only when the
anchor's provenance is **`GetFocusedElement()` itself** — the VS Code editor
case it was written for — never on an ancestor, a window-level document or a
browser page. **Gate on provenance, not on `cand[0]`:** the focused element
is yielded first only if it *has* a TextPattern, so when it doesn't,
`cand[0]` is already an ancestor or a window document — precisely the
situation F1 says is dangerous. Re-derive `remaining` from the anchor after
any `Select()` rather than trusting the pre-existing range.

*Verify:* re-run a Firefox read of a long article. Expect zero
`ANCHOR DEAD`, and the utt-5 pattern (painting → `sel=1` → death) absent.
Expect a `.md` VS Code editor read to still paint (that is the case
`Select()` legitimately serves — if it breaks, the gate is too tight).

### P2 — Make cursor loss recoverable (F2) — **DEPLOYED 2026-07-25**

All four items in (`orig`, `confirm()`-only promotion of `last_good`,
`_degenerate()` refusal at advance time, ladder + `REWIND_CAP = 6`).
Verified live against Notepad at 52/52 painted, 0 rewinds.

1. Retain `self.orig` — the anchor's range as acquired — forever.
2. Set `last_good` **before** the advance, not after (F2), and only from a
   hit that actually produced rects, on a surface that has produced rects
   before.
3. Detect a degenerate `remaining` (empty `GetText`, or compared endpoints)
   and reset immediately rather than after 8 misses.
4. Rewind **ladder**: `last_good` → `orig` → give up on the anchor
   (which feeds P3). Cap total rewinds per read — 112 is a bug indicator,
   not a retry strategy.

*Verify:* the utt 6 read reproduced; expect `rem=''` never to persist beyond
one poll, and rewind count in single digits.

### P3 — Make a useless anchor fall through instead of being held (F3)

Score the anchor over its first N located tokens (e.g. 12): if essentially
nothing is painting, **release it and try the next candidate**, and remember
the rejected one for the rest of the utterance. Requires P2's `orig` and the
candidate list to be re-enterable rather than re-derived from scratch.

*Verify:* a terminal read must reach "no working anchor" within ~2s instead
of holding a 0% anchor for 60s. That state is also D1's trigger.

### P4 — Re-anchor mid-read on the current chunk, not the utterance head (F4)

When rebuilding after `ANCHOR DEAD`, search `/now`'s current chunk text
first (with its own sanitize-tolerant head ladder), falling back to the
utterance head. Keeps the cursor near where the voice is and works on pages
that have re-rendered.

*Verify:* utt 5's Cloudflare case — the anchor dies, and the read recovers
within ~1s instead of losing its tail.

### P5 — Anchor speed and waste (F5, F6 / D3)

1. Dedup candidates before searching them (compare the element, or its
   `doc=` head + runtime id).
2. Retry cadence: fire the next attempt as soon as the previous **finishes**
   for the first ~2s, then back off. Not a fixed 0.5s timer.

*Verify:* utt-2/4-style reads anchor at <0.2s instead of 0.60s. Watch that
a failing attempt's 238ms cost does not overlap attempts.

### P6 — Polish and fallback

1. **Anti-flicker (F7):** hold the last painted rect for 2-3 polls before
   hiding, instead of hiding on every miss.
2. **Fetch backoff (F9):** back off `/now` polling when the server is
   unreachable.
3. **D1 caption box — promoted, see §6.** The user reads terminal/Claude
   Code output about half the time (confirmed 2026-07-25), and that half can
   never work in place. P3 supplies the trigger the D1 design note calls the
   real open question ("the box must appear *only* when in-place isn't
   working"). Do **not** start it before P3 exists — without the trigger it
   silently becomes the bottom transcript the user rejected in 2026-07-17.
   Re-read AUDIT §9 first.

## 6. Recommendation

Scope is now settled by the user's two answers (§3a, §1): **sync is fine —
drop it entirely**, and the daily split is roughly half Firefox web pages,
half Claude Code / terminal output. That splits the work cleanly in two, and
both halves are worth doing.

**Track 1 — the Firefox half, where in-place highlighting genuinely works.**
Do `P0 → P0b → P1 → P2` as one batch. P0 makes every later claim measurable,
P0b is the small shared change P1/P3/P5 all need, and P1/P2 fix the two
mechanisms that turned otherwise-working reads dark. utt 5 is the read to
keep in mind: 98% correct until `Select()` killed it — that is the typical
Firefox read, and P1 alone may recover most of it. Then `P4 → P5`, and
re-measure from a real day's SUMMARY lines before touching anything else.

**Track 2 — the terminal half, which can never work in place.** `P3` (an
anchor that notices it is painting nothing and gives up) then **D1**, the
caption box. This is no longer speculative: it is about half of daily use,
AUDIT §6 proves in-place is impossible there, and D1 is the only thing that
helps. P3 is the hard prerequisite — it supplies the "in-place isn't
working" trigger without which the box becomes the bottom transcript the
user rejected in 2026-07-17.

**Suggested order:** P0, P0b, P1, P2 first — they are small, evidence-backed,
and immediately improve the Firefox half. Then P3, which serves both tracks
(it fixes wrong-anchor reads *and* unlocks D1). Then D1 and the remainder.
