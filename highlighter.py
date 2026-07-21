r"""In-place spoken-word highlighter for any app that implements the UI
Automation TextPattern: Notepad, Firefox, the VS Code editor.

Terminals are out of scope and cannot be supported. VS Code's integrated
terminal (xterm.js) paints text to a canvas and exposes it to
accessibility through a hidden DOM mirror parked far off-screen -- it
reports rects at x=-11571 with a 58557px height for a window living in
x=-1928..8. UIA yields the terminal's text but no usable geometry, and
nothing bridges the two. Don't spend time here again.

The point: highlight the word being spoken ON the original text, not a
copy of it. Windows won't let one process restyle another's rendered
text, but UI Automation exposes the exact on-screen rectangle of any
text range, and a layered click-through window can tint just that
rectangle. Visually the word in the source document gets a translucent
marker, Speechify-style. If an app exposes no TextPattern at all, this
process simply does nothing. (The Chromium extension in
C:\kokoro\extension is optional; Firefox works through UIA directly.)

Flow, per utterance (tracked via /now's `utt` counter):
  1. Fetch the original text from /utterance.
  2. Anchor it in the source app. The TextPattern is searched near the
     focused element (itself, then ancestors, then first descendant);
     within each, the current text selection is preferred (editors keep
     it after Ctrl+C), else FindText of the utterance's first line.
     Anchoring retries for a few seconds: Firefox instantiates its
     accessibility engine lazily, so the very first queries after
     startup come back empty and only later ones succeed.
  3. Per spoken token from /now: FindText the token within the not-yet-
     spoken remainder (self-aligning, tolerant of markdown the server
     sanitized away), then draw its bounding rectangles. Rects are
     re-queried every poll, so scrolling moves the marker correctly.
     If a range reports no rectangles, the word is Select()ed once and
     rects re-queried: VS Code only exposes geometry for lines near its
     accessibility "page", and moving the selection moves the page.

Launched hidden by start_tts.vbs with pythonw.exe. Kill it and nothing
else changes.
"""
import ctypes
import json
import os
import re
import time
import traceback
from ctypes import wintypes
from urllib.request import urlopen

import numpy as np
import comtypes  # noqa: F401  (initializes COM)
import comtypes.client

NOW = "http://127.0.0.1:5111/now"
UTTER = "http://127.0.0.1:5111/utterance"
POLL_ACTIVE = 0.08
POLL_IDLE = 0.12       # not lazier: this is the lag before a read is even
                       # noticed, and the voice is already speaking by then,
                       # so a slow idle poll eats the utterance's first word
HTTP_TIMEOUT = 0.15    # per /now or /utterance fetch
DEBUG = os.environ.get("KOKORO_HL_DEBUG")   # path: log anchor decisions +
                                            # per-token rects. Diagnose with
                                            # this, don't guess (AUDIT.md §8)
FETCH_LOG_EVERY = 10.0  # a run of consecutive fetch failures (server down)
                        # logs at most this often; the FIRST failure of a run
                        # is always logged, which is the RC9 case that matters
HL_RGB = (0x3D, 0x5A, 0xFE)   # marker color
HL_ALPHA = 110                # 0-255; text stays readable underneath
PAD = 2                       # px around the word

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA

uia = comtypes.client.CreateObject(UIA.CUIAutomation,
                                   interface=UIA.IUIAutomation)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# 64-bit correctness: without prototypes ctypes truncates handles to c_int
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [wintypes.HDC, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


gdi32.CreateDIBSection.restype = ctypes.c_void_p
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD]
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
    ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
    ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
    ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]

user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PerMonitorV2


# ---------------- layered click-through marker window ----------------

class Marker:
    """One small always-on-top window with per-pixel alpha that jumps to
    the current word. WS_EX_TRANSPARENT makes it click-through."""

    def __init__(self):
        ex = (0x00080000 | 0x00000020 | 0x00000008 |    # LAYERED|TRANSP|TOPMOST
              0x00000080 | 0x08000000)                  # TOOLWINDOW|NOACTIVATE
        self.hwnd = user32.CreateWindowExW(
            ex, "STATIC", None, 0x80000000,             # WS_POPUP
            0, 0, 1, 1, None, None, None, None)
        self.shown = False

    def draw(self, rects):
        rects = [(int(l), int(t), int(l + w), int(t + h))
                 for l, t, w, h in rects if w >= 1 and h >= 1]
        if not rects:
            self.hide()
            return
        x0 = min(r[0] for r in rects) - PAD
        y0 = min(r[1] for r in rects) - PAD
        x1 = max(r[2] for r in rects) + PAD
        y1 = max(r[3] for r in rects) + PAD
        w, h = x1 - x0, y1 - y0
        if w > 4000 or h > 800:      # absurd rect: don't paint the screen
            self.hide()
            return

        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER),
                               biWidth=w, biHeight=-h, biPlanes=1,
                               biBitCount=32, biCompression=0)
        bits = ctypes.c_void_p()
        # try/finally, not a straight line: an exception between GetDC and
        # the releases leaks a DC + bitmap every poll, and GDI exhaustion is
        # exactly what makes CreateDIBSection start returning NULL later
        hdc_screen = hdc_mem = hbm = old = None
        try:
            hdc_screen = user32.GetDC(None)
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            hbm = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0,
                                         ctypes.byref(bits), None, 0)
            if not hbm or not bits.value:
                # GDI out of handles / out of memory. from_address(None) here
                # would raise and kill the process (RC5): skip this frame.
                dlog(f"DRAW CreateDIBSection failed w={w} h={h} "
                     f"hbm={hbm} bits={bits.value} -- frame skipped")
                self.hide()
                return
            old = gdi32.SelectObject(hdc_mem, hbm)

            buf = (ctypes.c_ubyte * (w * h * 4)).from_address(bits.value)
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
            arr[:] = 0
            a = HL_ALPHA
            px = (HL_RGB[2] * a // 255, HL_RGB[1] * a // 255,
                  HL_RGB[0] * a // 255, a)              # premultiplied BGRA
            for l, t, r, b in rects:
                arr[max(0, t - PAD - y0):min(h, b + PAD - y0),
                    max(0, l - PAD - x0):min(w, r + PAD - x0)] = px

            pos = wintypes.POINT(x0, y0)
            size = wintypes.SIZE(w, h)
            src = wintypes.POINT(0, 0)
            blend = BLENDFUNCTION(0, 0, 255, 1)         # AC_SRC_ALPHA
            user32.UpdateLayeredWindow(self.hwnd, hdc_screen,
                                       ctypes.byref(pos), ctypes.byref(size),
                                       hdc_mem, ctypes.byref(src), 0,
                                       ctypes.byref(blend), 2)  # ULW_ALPHA
        finally:
            if old:
                gdi32.SelectObject(hdc_mem, old)
            if hbm:
                gdi32.DeleteObject(hbm)
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            if hdc_screen:
                user32.ReleaseDC(None, hdc_screen)
        if not self.shown:
            user32.ShowWindow(self.hwnd, 4)             # SW_SHOWNOACTIVATE
            self.shown = True
        user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0,  # keep HWND_TOPMOST
                            0x0001 | 0x0002 | 0x0010)   # NOSIZE|NOMOVE|NOACTIVATE

    def hide(self):
        if self.shown:
            user32.ShowWindow(self.hwnd, 0)
            self.shown = False


def pump():
    msg = wintypes.MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


# ---------------- anchoring text in the source app via UIA ----------------

_fetch = {"fails": 0, "since": 0.0, "logged": 0.0, "last": ""}


def _fetch_failed(url, e):
    """RC9: 0.15s against a Flask dev server whose CPU is being eaten by the
    next chunk's synthesis reads exactly like 'server gone', and costs the
    poll (flicker) or the whole anchor slot. First failure of a run is logged
    immediately; a continuing outage collapses to one line per
    FETCH_LOG_EVERY so a stopped server cannot flood the log."""
    now = time.time()
    _fetch["last"] = f"{url.rsplit('/', 1)[-1]} {type(e).__name__}: {e}"
    if not _fetch["fails"]:
        _fetch.update(fails=1, since=now, logged=now)
        dlog(f"FETCH fail {_fetch['last']}")
        return
    _fetch["fails"] += 1
    if now - _fetch["logged"] >= FETCH_LOG_EVERY:
        _fetch["logged"] = now
        dlog(f"FETCH fail x{_fetch['fails']} over "
             f"{now - _fetch['since']:.1f}s (last: {_fetch['last']})")


def get(url):
    try:
        with urlopen(url, timeout=HTTP_TIMEOUT) as r:
            d = json.load(r)
    except Exception as e:
        _fetch_failed(url, e)
        return None
    if _fetch["fails"]:
        dlog(f"FETCH ok after {_fetch['fails']} fail(s) in "
             f"{time.time() - _fetch['since']:.1f}s (last: {_fetch['last']})")
        _fetch["fails"] = 0
    return d


def head_candidates(utt_text):
    """Search strings for locating the utterance, most specific first.

    FindText only matches contiguous text, but the server flattens the
    selection into one line, joining a heading to the paragraph under it
    with whitespace. In the document those are separate text runs, so the
    full head matches nothing -- which is why a read used to work while a
    selection was live (anchored off the selection) and fail once it was
    cleared. Runs of 2+ spaces mark where the flattening happened, so the
    segment before the first such run is a real contiguous run of text.
    Longest first: short fragments risk matching a nav item instead.
    """
    lines = [ln.strip() for ln in utt_text.strip().splitlines() if ln.strip()]
    if not lines:
        return []
    first = lines[0]
    seg = re.split(r"\s{2,}", first)[0]
    out = []
    for c in (first[:60], seg[:60], seg[:30], " ".join(seg.split()[:4])):
        c = c.strip()
        if len(c) >= 8 and c not in out:
            out.append(c)
    return out


def dlog(msg):
    """Timestamped diagnostic line; no-op unless KOKORO_HL_DEBUG is set."""
    if not DEBUG:
        return
    try:
        with open(DEBUG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def log_init():
    """Rotate the previous log one generation aside, then stamp a startup
    line. The rotation (rather than truncation) is deliberate: if the
    process died and was relaunched, `highlighter.log.1` still holds the
    traceback that killed it. The startup line is the proof-of-life that
    tells a silent-highlighter session apart from a dead-process one."""
    if not DEBUG:
        return
    try:
        if os.path.exists(DEBUG):
            os.replace(DEBUG, DEBUG + ".1")
    except Exception:
        pass
    dlog(f"START highlighter pid={os.getpid()} log={DEBUG}")


def _doc_head(tp, n=50):
    try:
        return (tp.DocumentRange.GetText(n) or "").replace("\n", " ")
    except Exception:
        return "<no text>"


def _tp_of(el):
    try:
        if not el:
            return None
        pat = el.GetCurrentPattern(UIA.UIA_TextPatternId)
        if not pat:
            return None
        return pat.QueryInterface(UIA.IUIAutomationTextPattern)
    except Exception:
        return None


def candidate_patterns():
    """TextPatterns near the focused element, most specific first: the
    element itself (VS Code's editor lives only here), its ancestors
    (Firefox's document), then the first TextPattern descendant."""
    try:
        el = uia.GetFocusedElement()
    except Exception:
        return
    tp = _tp_of(el)
    if tp:
        yield tp
    p = el
    walker = uia.ControlViewWalker
    for _ in range(8):
        try:
            p = walker.GetParentElement(p)
        except Exception:
            break
        if not p:
            break
        tp = _tp_of(p)
        if tp:
            yield tp
    try:
        cond = uia.CreatePropertyCondition(
            UIA.UIA_IsTextPatternAvailablePropertyId, True)
        tp = _tp_of(el.FindFirst(UIA.TreeScope_Subtree, cond))
        if tp:
            yield tp
    except Exception:
        pass


class Anchor:
    """The utterance's text range in the source document, plus a cursor:
    tokens are located with FindText inside the not-yet-spoken remainder,
    which keeps alignment even when the server sanitized markdown away."""

    def __init__(self, utt_text):
        self.ok = False
        self.token_ranges = {}      # (chunk_text, idx) -> located UIA range
        self.select_tried = set()   # keys whose Select() fallback already ran
        heads = head_candidates(utt_text)
        for i, tp in enumerate(candidate_patterns()):
            rng, how = None, None
            try:
                sel = tp.GetSelection()
                if sel and sel.Length > 0:
                    r = sel.GetElement(0)
                    if (r.GetText(200) or "").strip():
                        rng, how = r.Clone(), "selection"
            except Exception:
                rng = None
            for h in (heads if rng is None else []):
                try:
                    doc = tp.DocumentRange
                    found = doc.FindText(h, False, True)
                    if found is not None:
                        # only the head matched; the utterance continues
                        found.MoveEndpointByRange(
                            UIA.TextPatternRangeEndpoint_End, doc,
                            UIA.TextPatternRangeEndpoint_End)
                        rng, how = found, f"findtext[{h[:24]!r}]"
                        break
                except Exception:
                    pass
            dlog(f"  cand[{i}] how={how} doc={_doc_head(tp)!r}")
            if rng is not None:
                self.remaining = rng
                self.ok = True
                dlog(f"ANCHOR ok via={how} cand={i}")
                return
        dlog(f"ANCHOR FAILED heads={heads}")

    def locate(self, chunk_text, idx, token):
        key = (chunk_text, idx)
        if key in self.token_ranges:
            return self.token_ranges[key]
        r = None
        try:
            r = self.remaining.FindText(token, False, True)
            if r is not None:
                nxt = self.remaining.Clone()
                nxt.MoveEndpointByRange(UIA.TextPatternRangeEndpoint_Start,
                                        r, UIA.TextPatternRangeEndpoint_End)
                self.remaining = nxt
        except Exception:
            r = None
        if r is not None:           # misses are NOT cached: a later poll
            self.token_ranges[key] = r   # may succeed once the app warms up
        return r


def rects_of(rng):
    try:
        vals = rng.GetBoundingRectangles()
        return [(vals[i], vals[i + 1], vals[i + 2], vals[i + 3])
                for i in range(0, len(vals) - 3, 4)]
    except Exception:
        return []


ANCHOR_WINDOW = 6.0     # keep retrying the anchor this long per utterance
ANCHOR_RETRY = 0.5      # ...but attempt at most every this often
RESUME_WINDOW = 5.0     # the same utt reappearing this soon after a wipe is
                        # one read continuing (RC6), not a new read


def main(marker):
    anchor = None
    utt_seen = None
    chunk_seen = None
    resolved = -1               # tokens of current chunk located so far
    utt_t0 = 0.0
    anchor_until = 0.0
    anchor_next = 0.0
    tries = 0                   # anchor attempts for this utterance
    gaveup = False              # ANCHOR_WINDOW expiry already logged
    wiped = None                # (utt, wallclock) of the last active:false
    errors, last_error = 0, 0.0

    while True:
        # One bad poll must never end the process (RC5): everything below is
        # best-effort and the next poll re-derives all of it, so a raised
        # exception is logged and dropped rather than allowed to kill the
        # highlighter for the rest of the session.
        try:
            pump()
            d = get(NOW)
            if not d or not d.get("active"):
                marker.hide()
                if d is not None:   # server reachable: idle, forget the read
                    if utt_seen is not None:
                        # RC6: /now says active:false whenever playback slips
                        # >0.3s past the chunk's end -- a starvation GAP or an
                        # awkward chunk boundary is indistinguishable here
                        # from "the read finished", and wipes mid-read state
                        dlog(f"WIPE utt={utt_seen} "
                             f"anchored={anchor is not None} "
                             f"chunk={(chunk_seen or '')[:24]!r}")
                        wiped = (utt_seen, time.time())
                    anchor, utt_seen = None, None
                time.sleep(POLL_IDLE)
                continue

            if d.get("utt") != utt_seen:
                utt_seen = d.get("utt")
                if wiped and wiped[0] == utt_seen and \
                        time.time() - wiped[1] <= RESUME_WINDOW:
                    # same gen as before the wipe => the read never ended, and
                    # we are about to re-anchor and reset the cursor to the
                    # utterance head mid-read. This line IS the RC6 evidence.
                    dlog(f"RESUME utt={utt_seen} after "
                         f"{time.time() - wiped[1]:.2f}s dark -- RC6 "
                         f"(re-anchor + cursor reset mid-read)")
                wiped = None
                chunk_seen = None
                anchor = None
                utt_t0 = time.time()
                anchor_until = utt_t0 + ANCHOR_WINDOW
                anchor_next = 0.0
                tries = 0
                gaveup = False
                dlog(f"UTT {utt_seen} begins")

            if anchor is None:
                # retry across polls: Firefox's accessibility engine warms up
                # lazily, so early attempts return empty selections/misses
                t = time.time()
                if anchor_next <= t < anchor_until:
                    anchor_next = t + ANCHOR_RETRY
                    tries += 1
                    u = get(UTTER)
                    if not u:
                        dlog(f"ANCHOR try#{tries} +{t - utt_t0:.2f}s "
                             f"skipped: /utterance unreachable")
                    elif u.get("utt") != utt_seen:
                        dlog(f"ANCHOR try#{tries} +{t - utt_t0:.2f}s skipped: "
                             f"utt mismatch (/utterance={u.get('utt')} "
                             f"/now={utt_seen})")
                    else:
                        dlog(f"ANCHOR try#{tries} +{t - utt_t0:.2f}s")
                        a = Anchor(u.get("text") or "")
                        if a.ok:
                            anchor = a
                            dlog(f"ANCHOR acquired on try#{tries}, "
                                 f"{time.time() - utt_t0:.2f}s after utt start")
                elif t >= anchor_until and not gaveup:
                    gaveup = True
                    dlog(f"GIVEUP utt={utt_seen} unanchored after {tries} "
                         f"tries / {ANCHOR_WINDOW}s -- RC2: read stays dark")
                if anchor is None:
                    marker.hide()
                    time.sleep(POLL_ACTIVE)
                    continue

            text = d.get("text") or ""
            if text != chunk_seen:
                chunk_seen = text
                resolved = -1

            idx = d.get("word", -1)
            words = d.get("words") or []
            if idx < 0 or idx >= len(words):
                time.sleep(POLL_ACTIVE)
                continue
            # resolve tokens in order so FindText's cursor advances correctly;
            # already-passed tokens get one attempt, the current one retries
            while resolved < idx - 1:
                resolved += 1
                anchor.locate(chunk_seen, resolved, words[resolved][0])
            if resolved < idx and \
                    anchor.locate(chunk_seen, idx, words[idx][0]) is not None:
                resolved = idx
            rng = anchor.token_ranges.get((chunk_seen, idx))
            rr = []                 # reset every poll: a stale rr would make
            if rng is None:         # a failed lookup log as the last success
                marker.hide()
            else:
                rr = rects_of(rng)
                if not rr and (chunk_seen, idx) not in anchor.select_tried:
                    # VS Code: geometry exists only near its accessibility
                    # page; selecting the word moves the page onto it
                    anchor.select_tried.add((chunk_seen, idx))
                    try:
                        rng.Select()
                        rr = rects_of(rng)
                        # rects are harvested; now collapse to a bare caret so
                        # VS Code stops painting its own selection over the
                        # word. Without this the first word of every line keeps
                        # a blue block until the next line's Select() moves it.
                        caret = rng.Clone()
                        caret.MoveEndpointByRange(
                            UIA.TextPatternRangeEndpoint_End, caret,
                            UIA.TextPatternRangeEndpoint_Start)
                        caret.Select()
                    except Exception:
                        pass
                marker.draw(rr)
            dlog(f"chunk={chunk_seen[:20]!r} idx={idx} "
                 f"tok={words[idx][0]!r} rects={rr}")
            time.sleep(POLL_ACTIVE)
        except Exception:
            now = time.time()
            errors = errors + 1 if now - last_error < 5.0 else 1
            last_error = now
            dlog(f"POLL ERROR #{errors} (recovered, loop continues):\n"
                 f"{traceback.format_exc().rstrip()}")
            try:
                marker.hide()
            except Exception:
                pass
            # a persistently failing poll must not spin the CPU or the log
            time.sleep(min(0.25 * errors, 2.0))


if __name__ == "__main__":
    log_init()
    marker = None
    while True:
        try:
            if marker is None:
                marker = Marker()
            main(marker)
        except KeyboardInterrupt:
            break
        except Exception:
            # main()'s own guard already survives per-poll failures, so
            # reaching here means something structural broke (COM died, the
            # marker window is gone). Log it and rebuild rather than exit:
            # a dead highlighter is invisible and stays dead all session.
            dlog(f"FATAL -- restarting in 2s:\n"
                 f"{traceback.format_exc().rstrip()}")
            try:
                marker.hide()   # don't leave a stale tint on screen
            except Exception:
                pass
            marker = None
            time.sleep(2.0)
