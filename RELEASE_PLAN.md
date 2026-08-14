# Release plan — Windows beta, caption strip, Linux (Fedora)

Written 2026-08-14. Supersedes nothing; `plan.md` (highlighter fixes) and
`AUDIT.md` (source of truth) both stand. This file moves to `docs/` in Phase 1.

**Scope decided with the user 2026-08-14:**

| Decision | Answer |
|---|---|
| Linux target | Fedora on the **same laptop** (i7-11370H + GTX 1650) |
| Internal docs | Move to `docs/`, scrub sensitive info, publish |
| Repo layout | One repo, two release assets (see §5) |
| Caption default (Windows) | **Auto**, with Off / Auto / Always all exposed in Settings |

---

## 0. What already exists (verified, not assumed)

- The GitHub repo **already exists and `main` is pushed**:
  `github.com/sayed-qutob-work/kokoro-read-aloud`. There are **no tags and no
  releases**. So "create a beta release" = tag + Release page, not repo setup.
- `gh` CLI is **not installed** on this machine. Either install it or use the
  web UI for the Release step.
- `tts_server.py` has **no device-selection code** — `KPipeline(lang_code=code)`
  (line 387) takes no device argument. CPU vs GPU is decided *entirely* by which
  torch wheel is installed. The CPU-default fix is packaging-only, zero code.
- The tray **already warns** when the chosen speed exceeds what the machine can
  sustain (`_check_sustain` / `lbl_warn`, `tray.py:891-917`, reading
  `measured_rt`). CPU-only users are already protected by an existing mechanism.
- The server **already knows** where sentences end: `tts_server.py:717`,
  `pause = SENTENCE_PAUSE if final or last in ".!?…" else CUT_PAUSE`.
- The highlighter **already tracks** paint success: `self.painted` (line 808),
  `MODE_PROOF` (line 665), the mode-switch "nothing painted" logic (lines
  977-996), and `stats["painted"]` (line 1338). P3's trigger is closer than
  `plan.md` implies.

---

## 1. The one thing that could sink this

**Fedora Workstation runs GNOME on Wayland, and recent Fedora removed the GNOME
X11 session entirely.** The caption strip is an always-on-top, positioned,
non-focus-stealing overlay — which is precisely the class of thing Wayland is
designed to forbid.

This is load-bearing: on Linux the caption strip **is** the product's
highlighting story. If it can't render, the Linux release is a headless TTS
server with hotkeys.

Options, ranked, to be resolved by a spike **before any other Linux work**:

1. **tkinter `overrideredirect` + `-topmost` under XWayland.** `overlay.py`
   already does exactly this. X11 clients still run under XWayland, and
   override-redirect surfaces are typically composited above normal windows.
   Cheapest by far if it works. **Test this first.**
2. **GNOME Shell extension.** The native way to draw an overlay on GNOME.
   Reliable, but it's JavaScript in a separate extension, a second UI codebase,
   and a review/install story.
3. **KDE Plasma session on Fedora** (still offers X11). Sidesteps the problem
   entirely at the cost of telling users which desktop to run.
4. **`wlr-layer-shell`** — the correct Wayland protocol for overlay panels, and
   **ruled out for GNOME**: mutter does not implement it. Only viable if the
   user is on a wlroots compositor (Sway, Hyprland).

*Do not write Linux tray/hotkey/packaging code before this spike returns.*

---

## 2. Phase 1 — Hygiene → Windows beta `v0.1.0-beta`

Ships the current system, highlighter warts and all, to a Release page. No
caption strip yet (see §3 for why that's deliberate).

### 1.1 Stop shipping personal state

- `settings.json` is **tracked** and contains the user's tuned values
  (`playback_speed: 1.737`). It becomes every cloner's default and makes their
  tray edits a permanently dirty tree.
- Fix: `git rm --cached settings.json`, add to `.gitignore`, commit
  `settings.example.json` with the documented defaults.
- **Verified safe:** both readers already handle the file being absent —
  `tts_server.py:93` (`except FileNotFoundError: return`) and `tray.py:112`
  (`except FileNotFoundError: pass`).

### 1.2 Make every file location-independent

This is the step that makes the Phase 4 folder restructure safe, so it comes
first and the move comes later.

- `start_tts.vbs` hardcodes `C:\kokoro` at lines **10, 37, 53, 61**. Derive the
  root from the script's own location. Note the file's own warning at line 13:
  a bad path here fails *silently* — same failure class.
- `tts_server.py:29-30` hardcodes `ONNX_MODEL` / `ONNX_VOICES` as
  `C:\kokoro\...`. Unused while `ENGINE = "torch"`, but wrong the moment anyone
  flips it. Derive from `_ROOT` (already defined, line 76).
- `tray.py` is **already correct** (`ROOT` from `__file__`, line 52) but hardcodes
  the Windows venv layout (`env/Scripts/python.exe`, line 56) and shells to
  `Get-CimInstance` (line 179). Leave for Phase 4; note it.

### 1.3 Fix the install for people who aren't you

- `requirements.txt` currently defaults to `torch==2.13.0+cu126` with an
  `--extra-index-url`. Split it: **CPU wheel by default**, CUDA as a documented
  one-line opt-in.
- Why this matters more than it looks: on this laptop's CPU, synthesis runs
  **1.77x RT while playback drains 1.8x** — below break-even. The out-of-box
  experience on a CPU-only machine is *"it stalls constantly."* The tray warning
  (§0) catches it, but the README must say so up front and the shipped
  `settings.example.json` should carry a conservative `playback_speed` for CPU.

### 1.4 README rewrite

The current README (`README.md:100`) tells people to clone to `C:\kokoro` and
assumes your hardware. It needs:

- **A "what works where" table.** You have better evidence for this than most
  shipped software — AUDIT §8 has per-app status. Without it, the tracker fills
  with "highlighting doesn't work in my terminal", which §6 proves is
  *impossible*, not broken.
- An honest beta banner naming the known highlighter faults (multi-line
  selections, repeated reads of the same statement, occasional stalls).
- CPU vs GPU expectations, with the break-even number.
- Install without a fixed path.

### 1.5 `docs/` move + scrub

Move `AUDIT.md`, `plan.md`, `RELEASE_PLAN.md` (this file) into `docs/`.
`CLAUDE.md` **stays at root** — Claude Code reads it from the project root, and
moving it breaks that for contributors. Update the path references in
`CLAUDE.md` and `README.md` after the move.

**Scrub scope — genuinely light, having scanned:** no emails, no credentials, no
tokens, no `C:\Users\<name>` paths in the docs. What's actually there:

| Found | Where | Action |
|---|---|---|
| `sayed-qutob-work` GitHub org | `AUDIT.md:85`, `README.md:100` | Keep — it's the public repo URL |
| "Logitech G Hub" macro setup | `AUDIT.md:47,343,472,1665`, `README.md:8,137` | Keep — it's a documented feature, not PII |
| `Headphones (Realtek(R) Audio)`, `DELL U2422HE` | `AUDIT.md:1264`, `tts_server.py:158` | Keep — device-name examples in a bug explanation |
| Machine specs (i7-11370H, GTX 1650) | throughout §4/§8 | **Keep and label** — the measurements are worthless without the machine they came from |

Recommend a short "how to read these docs" preface in `docs/` explaining they're
a working engineering log, not user documentation. That converts the biggest
apparent liability (145KB of internal narrative) into the credibility asset it
actually is.

### 1.6 Ship it

Tag `v0.1.0-beta`, GitHub Release, source zip, release notes = the honest status
table from 1.4. **Verify repo visibility is public first.**

---

## 3. Phase 2 — The trigger (P3), because you chose Auto

Choosing **Auto as the default** means the caption strip now depends on a
highlighter change. Stating that plainly: you said highlighter fixes were for
later, and this pulls one of them forward. It's the right call — a caption box
without the trigger silently becomes the bottom transcript rejected on
2026-07-17 (AUDIT §9) — but it is the reason captions ship in Phase 3, not
Phase 1.

**P3** (`plan.md:703`): score the anchor over its first ~12 located tokens; if
essentially nothing paints, release it, try the next candidate, and remember the
rejected one. Its stated prerequisite, P2's `orig` + re-enterable candidate
list, **is deployed** (AUDIT, 2026-07-25).

*Verify:* a terminal read reaches "no working anchor" within ~2s instead of
holding a 0% anchor for 60s.

### 2.1 The state channel — server as the bus

The strip needs to know the highlighter has given up. Two shapes were left
undecided in `plan.md`. **Recommend the server**, not a status file:

- `POST /highlight_state {utt, anchored, painting}` from the highlighter;
  `/now` grows a `highlight_ok` field the strip already sees for free.
- A status file is the exact failure class you were bitten by on 2026-08-11 —
  files held open by running processes breaking git operations. Don't add
  another one.
- Keeps the strip a dumb `/now` consumer, so the Linux build needs no
  highlighter at all: on Linux the server simply always reports
  `highlight_ok: false`, and Auto resolves to "on". Same code, both platforms.

**Use `painted` as a failure detector only, never as a success measure** — AUDIT
2026-08-12 is explicit that `painted=62%` can coexist with highlighting
arbitrary words. `painted≈0` proves failure; a high rate proves nothing.

---

## 4. Phase 3 — Caption strip (D1) → Windows `v0.2.0-beta`

Built once, platform-neutral, shipped to Windows first and inherited by Linux.
`overlay.py` (118 lines) is the starting point — it already has frameless
topmost, bottom-centre, drag-to-move, right-click-close, 80/500ms polling, no
focus steal. It needs four things:

### 3.1 Context: extend `/now`, don't match text

D1 asks for ~4 lines of context; `/now` returns only the current chunk
(`tts_server.py:784`). Matching the chunk back into `/utterance` is a trap —
`/utterance` is **pre-sanitize** text and `/now` is post-sanitize, so the
mapping is lossy.

**Recommend:** the server keeps the utterance's chunk list (it already has it)
and `/now` returns `prev` / `next` chunk text alongside `text`. No matching, no
ambiguity, small change.

### 3.2 Sentence granularity: expose what line 717 already computes

D1 asks for **sentence**-level highlighting, but chunks cut mid-sentence. The
server already decides this per chunk at `tts_server.py:717`. Add
`ends_sentence: bool` to each chunk in `/now`; the strip merges chunks until one
ends a sentence. This is the whole feature, and it's near-free.

### 3.3 Rendering

Multi-line `tk.Text` (already there), sentence highlight tag instead of the
current word tag, ~4 visible lines. Keep: no focus steal, no taskbar coverage,
right-click closes.

### 3.4 Tray: three modes, per your answer

- `captions: "off" | "auto" | "always"`, default `"auto"`.
- Add to `DEFAULTS` in `tray.py` (its `load_settings`/`save_settings` drop
  unknown keys, lines 102-131 — the key must be registered or it's silently
  discarded) and to `load_user_settings` in `tts_server.py:88-104`.
- New Settings section with all three choices and one line of explanation each,
  as you asked. Suggested copy:
  - **Off** — never show the caption box.
  - **Auto** — show it only when in-place highlighting can't work (terminals,
    Claude Code, pages where anchoring fails).
  - **Always** — always show it while reading.

Tag `v0.2.0-beta`.

---

## 5. Phase 4 — Linux (Fedora), gated on the §1 spike

### 4.1 Spike (blocking, do first)

On the Fedora install: `echo $XDG_SESSION_TYPE`, then run `overlay.py` unchanged
and observe whether it appears, stays on top, and doesn't steal focus. Branch
per §1. **Record the result in AUDIT before writing any other Linux code.**

### 4.2 GPU, or the whole thing stalls

Same laptop, different OS — the GTX 1650 needs the proprietary driver from RPM
Fusion (`akmod-nvidia`); nouveau gives no CUDA. Verify `nvidia-smi`, then
`torch.cuda.is_available()`, then measure `measured_rt` from `GET /config`.
**Do not carry the Windows 25.03x number over** — `calibration.json` is
per-machine *and* the OS/driver stack differs. Measure it, put it in AUDIT.

### 4.3 Hotkeys — Linux is arguably better here

- **Trigger:** GNOME Settings → Keyboard → Custom Shortcuts, bound to a small
  `kokoro-speak` script. Global hotkey grabbing by an app is not permitted on
  Wayland; this is the supported path and it's clean.
- **Selection:** `wl-paste --primary` returns the current mouse selection
  **without simulating Ctrl+C at all**. This eliminates the entire AHK copy
  dance, the clipboard save/restore, the `KeyWait` timing hack, and the terminal
  special-casing (AUDIT §9's "AHK sends Ctrl+C internally… Windows has no other
  way"). Fall back to `wl-paste` (clipboard) when primary is empty.
- **Verify per-app**, since primary-selection export varies: Firefox, VS Code
  (Electron), GNOME Terminal, a PDF viewer.

### 4.4 Tray — expect friction

`pystray` + AppIndicator, but **GNOME removed the legacy tray**: users need the
AppIndicator shell extension installed. Plan a fallback where the settings panel
is launched from a `.desktop` entry / CLI command, so the product works without
a tray at all.

### 4.5 Process management and autostart

- `tray.py`'s restart machinery is Windows-bound: `env/Scripts/python.exe`
  (line 56) and `Get-CimInstance` (line 179). Abstract to `env/bin/python` +
  `psutil`/`pgrep`.
- Replace `start_tts.vbs` with **systemd user units** (`kokoro-server.service`,
  etc.) — better than the VBS in every way: real restart policy, real logging
  via journald, `systemctl --user enable`.

### 4.6 Now do the folder restructure

Safe at this point because Phase 1 made everything location-independent:

```
/                      README.md, CLAUDE.md, requirements*.txt
  tts_server.py        portable core
  captions.py          portable (was overlay.py)
  extension/           portable
  docs/                AUDIT.md, plan.md, RELEASE_PLAN.md
  platform/windows/    read_aloud.ahk, highlighter.py, tray_win.py, start_tts.vbs
  platform/linux/      kokoro-speak, *.service, tray_linux.py
```

---

## 6. Release mechanics — "same repo, different releases"

To answer the question directly: it's one repo, one tag, **one Release page with
multiple assets**. GitHub Releases attach arbitrary files to a tag.

- Tag `v0.3.0` → Release "v0.3.0" → upload `kokoro-read-aloud-v0.3.0-windows.zip`
  and `kokoro-read-aloud-v0.3.0-linux.tar.gz`.
- Release notes carry a per-platform status table (Windows: in-place + captions;
  Linux: captions only).
- Each asset is the repo minus the other platform's `platform/` subdir — a
  10-line packaging script, or a GitHub Action later. Don't automate this until
  it's been done by hand twice.
- Keep one version number across both platforms. Divergent per-platform versions
  are a support nightmare for a solo maintainer.

---

## 7. Risks, ranked

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| 1 | Overlay can't render on Fedora/GNOME Wayland | **Kills the Linux value proposition** | §4.1 spike before all other Linux work; fallbacks ranked in §1 |
| 2 | NVIDIA/CUDA not working on Fedora | Linux stalls constantly (CPU is below break-even) | §4.2, verify before building anything on top |
| 3 | Auto mode fires wrongly → box appears during good reads | Re-creates the rejected bottom transcript | Trigger on `painted≈0`/`GIVEUP` only, never on partial success |
| 4 | Beta issue reports about known-impossible surfaces | Support burden on a solo maintainer | §1.4 capability table; label them `wontfix-by-design` with the AUDIT §6 link |
| 5 | Primary-selection unavailable in Electron apps on Wayland | Hotkey reads nothing in VS Code | §4.3 per-app verification; clipboard fallback |
| 6 | GNOME AppIndicator extension missing | No tray | §4.4 non-tray fallback path |
| 7 | Restructure breaks the daily driver | You lose your own tool mid-project | Restructure last (§4.6), after location-independence (§1.2) |

---

## 8. Sequencing summary

```
Phase 1  Hygiene + docs/ + README        → v0.1.0-beta   (Windows, no captions)
Phase 2  P3 trigger + /highlight_state   → (no release)
Phase 3  Caption strip + tray 3-mode     → v0.2.0-beta   (Windows, captions)
Phase 4  Fedora spike → GPU → hotkeys →  → v0.3.0        (Windows + Linux)
         tray → systemd → restructure
```

Phase 1 is independent and shippable immediately. Phases 2-3 are one unit.
Phase 4 is gated on its own first step.

---

## 9. Open questions

1. **Fedora session type** — `echo $XDG_SESSION_TYPE` on the Fedora install.
   Determines whether §1 is a five-minute confirmation or a fork in the plan.
2. **Is the GitHub repo currently public or private?** Couldn't check — `gh` is
   not installed here.
3. **Does the Fedora install already have the NVIDIA proprietary driver?**
   Decides whether §4.2 is a check or a yak-shave.
4. **Beta scope confirmation:** Phase 1 ships Windows *without* captions so the
   release isn't blocked behind P3. Confirm that's what you want, or say so and
   I'll fold Phases 1-3 into a single first release.
