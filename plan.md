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
