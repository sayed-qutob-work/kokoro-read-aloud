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
     If a range reports no rectangles AND the anchor is the focused
     element itself, the word is Select()ed once and rects re-queried:
     the VS Code editor exposes geometry only for lines near its
     accessibility "page", and moving the selection moves the page.
     That fallback is confined to that one route on purpose -- anywhere
     else the same call moves the app's real selection and kills the
     anchor (measured 2026-07-25, see Anchor.can_select).

Launched hidden by start_tts.vbs as python.exe inside a hidden cmd, with
stderr redirected to highlighter.err (pythonw has no stderr at all, so an
import/COM-init crash used to leave no trace). Kill it and nothing else
changes.
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
LOG_KEEP = 5            # generations of the debug log to retain. ONE was not
                        # enough: the user reported two days of highlighting
                        # problems and the surviving evidence was 16 minutes,
                        # because every highlighter restart rotated the rest
                        # away (plan.md 2026-07-25 §0). Everything in this
                        # project is ranked from these logs, so keeping them
                        # is not housekeeping -- it is the measurement.
HL_RGB = (0x3D, 0x5A, 0xFE)   # marker color
HL_ALPHA = 110                # 0-255; text stays readable underneath
PAD = 2                       # px around the word

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA

uia = comtypes.client.CreateObject(UIA.CUIAutomation,
                                   interface=UIA.IUIAutomation)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

# 64-bit correctness: without prototypes ctypes truncates handles to c_int
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
user32.GetForegroundWindow.restype = wintypes.HWND
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
    w = seg.split()
    out = []
    # ...down to two words. Measured on a Gmail read that never anchored:
    # every head tried contained '@Ryan H.', a mention chip that is its own
    # text run, so FindText matched nothing -- while plain 'My Cousin' hit
    # with a real rect. Any inline element (mention, link, <b>, emoji) splits
    # a line the same way, so the ladder has to reach short.
    for c in (first[:60], seg[:60], seg[:30], " ".join(w[:6]),
              " ".join(w[:4]), " ".join(w[:3]), " ".join(w[:2])):
        c = c.strip()
        if len(c) >= 8 and c not in out:
            out.append(c)
    if not out:         # a short first line ('Hello.', a one-word heading)
        for c in (first.strip(), seg.strip()):   # still deserves an attempt
            if c and c not in out:
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
    """Rotate the log LOG_KEEP generations deep, then stamp a startup line.

    Rotation rather than truncation is deliberate: if the process died and
    was relaunched, the traceback that killed it survives. Depth matters for
    the same reason -- a single generation meant one restart (or one server
    outage that filled the live log with FETCH failures) destroyed every real
    read before it, which is exactly what happened to the two days of
    evidence behind the 2026-07-25 diagnosis. The startup line is the
    proof-of-life that tells a silent highlighter from a dead one."""
    if not DEBUG:
        return
    try:
        oldest = f"{DEBUG}.{LOG_KEEP - 1}"
        if os.path.exists(oldest):
            os.remove(oldest)
        for i in range(LOG_KEEP - 2, 0, -1):
            src = f"{DEBUG}.{i}"
            if os.path.exists(src):
                os.replace(src, f"{DEBUG}.{i + 1}")
        if os.path.exists(DEBUG):
            os.replace(DEBUG, DEBUG + ".1")
    except Exception:
        pass
    dlog(f"START highlighter pid={os.getpid()} log={DEBUG} keep={LOG_KEEP}")


def _doc_head(tp, n=50):
    try:
        return (tp.DocumentRange.GetText(n) or "").replace("\n", " ")
    except Exception:
        return "<no text>"


_proc_names = {}


def _proc_name(pid):
    """Image name for a pid, cached. Turns 'some document element' into
    'Code.exe' / 'firefox.exe' -- the field that makes the log countable
    per app instead of readable only by eye."""
    if pid in _proc_names:
        return _proc_names[pid]
    name = f"pid{pid}"
    try:
        h = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFO
        if h:
            buf = ctypes.create_unicode_buffer(260)
            n = wintypes.DWORD(260)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                name = buf.value.rsplit("\\", 1)[-1]
            kernel32.CloseHandle(h)
    except Exception:
        pass
    _proc_names[pid] = name
    return name


def _ident(el):
    """Who a candidate TextPattern actually belongs to. DEBUG-only: every
    property read here is a cross-process COM call, and this runs per
    candidate per anchor attempt."""
    if not DEBUG or not el:
        return ""
    try:
        pid = el.CurrentProcessId
    except Exception:
        pid = 0

    def prop(attr):
        try:
            return (getattr(el, attr) or "")[:40]
        except Exception:
            return "?"

    return (f"{_proc_name(pid)} {prop('CurrentName')!r} "
            f"[{prop('CurrentClassName')}]")


def _app_of(el):
    """Image name of the element's owning process, lowercased.

    Deliberately NOT _ident(): that one is DEBUG-only (several cross-process
    property reads per candidate per attempt), so anything gating real behavior
    on it would silently change behavior when logging is off. This reads one
    property, and only for the candidate that actually won the anchor."""
    try:
        return _proc_name(el.CurrentProcessId).lower()
    except Exception:
        return ""


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


def _rid(el):
    """A comparable identity for an element, so two candidates that are the
    same element reached by different routes can be told apart. Measured
    2026-07-25: a Firefox read offered cand[0..2] all resolving to the same
    'Just a moment...' document, each paying a full head ladder."""
    try:
        return tuple(el.GetRuntimeId())
    except Exception:
        return None


# Provenance tags. These are not cosmetic: HOW_FOCUS is the only route on
# which the Select() geometry fallback is legal (see Anchor.can_select), and
# the identity travels with them so duplicates can be collapsed.
HOW_FOCUS = "focus"          # the focused element itself -- the VS Code editor
HOW_ANCESTOR = "ancestor"    # parent walk -- Firefox's document
HOW_FOCUS_SUB = "focus-sub"  # first TextPattern under the focused element
HOW_WINDOW = "window"        # the foreground window element itself
HOW_WINDOW_DOC = "window-doc"    # a Document under it (one per browser tab)
HOW_WINDOW_SUB = "window-sub"    # last resort: any TextPattern under it

# Where the Select() geometry fallback may run. Naming the app is not per-app
# tuning for its own sake -- Select() *is* an app-specific workaround (the VS
# Code editor materializes geometry only near its accessibility page), so the
# honest gate names the app it was written for. Measured across every surviving
# log generation: Select() has produced geometry only inside VS Code, and never
# once in a browser (0/10 on 2026-07-25, 0/4 on 2026-08-11).
SELECT_APPS = {"code.exe", "code - insiders.exe", "vscodium.exe"}
# ...and never from a window-level route: that is the terminal/chrome document
# (AUDIT §6), where the call moves whatever really holds the selection.
SELECT_ROUTES = (HOW_FOCUS, HOW_ANCESTOR, HOW_FOCUS_SUB)


def candidate_patterns():
    """TextPatterns near the focused element, most specific first: the
    element itself (VS Code's editor lives only here), its ancestors
    (Firefox's document), then the first TextPattern descendant.

    Yields (tp, el, how, rid). `how` records the ROUTE, and callers must
    branch on it rather than on the candidate's index: the focused element is
    yielded first only when it has a TextPattern, so cand[0] is frequently an
    ancestor or a window document instead."""
    try:
        el = uia.GetFocusedElement()
    except Exception:
        return
    tp = _tp_of(el)
    if tp:
        yield tp, el, HOW_FOCUS, _rid(el)
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
            yield tp, p, HOW_ANCESTOR, _rid(p)
    try:
        cond = uia.CreatePropertyCondition(
            UIA.UIA_IsTextPatternAvailablePropertyId, True)
        sub = el.FindFirst(UIA.TreeScope_Subtree, cond)
        tp = _tp_of(sub)
        if tp:
            yield tp, sub, HOW_FOCUS_SUB, _rid(sub)
    except Exception:
        pass
    # Last resort, and deliberately last: the subtree searches below are the
    # expensive calls here (~10-45ms). Firefox sometimes gives focus to a
    # single message block whose document IS that block, with no TextPattern
    # ancestor above it -- measured 2026-07-21, those reads never anchored at
    # all (12 tries, GIVEUP, dark).
    try:
        hwnd = user32.GetForegroundWindow()
        for c in window_candidates(hwnd):
            yield c
    except Exception:
        pass


WINDOW_DOCS = 12        # page documents to offer from the foreground window
                        # (a browser window holds one per open TAB, so this
                        # is a tab count, not a page count -- 6 silently
                        # dropped the Outlook tab on a 7-tab window and the
                        # read never anchored)

try:
    _DOC_COND = uia.CreateAndCondition(
        uia.CreatePropertyCondition(
            UIA.UIA_IsTextPatternAvailablePropertyId, True),
        uia.CreatePropertyCondition(
            UIA.UIA_ControlTypePropertyId, UIA.UIA_DocumentControlTypeId))
except Exception:
    _DOC_COND = None


def window_candidates(hwnd):
    """TextPatterns anywhere in a top-level window, documents first.

    Documents *specifically*, not "the first TextPattern in the window":
    in a browser that first pattern is the URL bar. Measured live on a
    Gmail read -- `who='Search with Google or enter address'
    [urlbar-input]`, doc='mail.google.com/mail/u/1/#inbox/...' -- so the
    read never anchored. Every web page is a Document control type
    (background tabs included, which costs nothing: a wrong tab simply
    fails the head match)."""
    if not hwnd:
        return
    try:
        win = uia.ElementFromHandle(hwnd)
    except Exception:
        return
    if not win:
        return
    tp = _tp_of(win)
    if tp:
        yield tp, win, HOW_WINDOW, _rid(win)
    if _DOC_COND is not None:
        try:
            docs = win.FindAll(UIA.TreeScope_Descendants, _DOC_COND)
            found = []
            for i in range(docs.Length if docs else 0):
                d = docs.GetElement(i)
                try:
                    off = bool(d.CurrentIsOffscreen)
                except Exception:
                    off = True
                found.append((off, d))
            # Visible tab first. A background tab's document is real and
            # searchable but parked off-screen (measured: rects at
            # x=-31942), so anchoring to one paints the marker where nobody
            # can see it -- identical, to the user, to no highlight at all.
            # Stable sort, so tree order survives within each group.
            found.sort(key=lambda t: t[0])
            for off, d in found[:WINDOW_DOCS]:
                tp = _tp_of(d)
                if tp:
                    yield tp, d, HOW_WINDOW_DOC, _rid(d)
        except Exception:
            pass
    try:        # apps whose text isn't a Document control type
        cond = uia.CreatePropertyCondition(
            UIA.UIA_IsTextPatternAvailablePropertyId, True)
        sub = win.FindFirst(UIA.TreeScope_Descendants, cond)
        tp = _tp_of(sub)
        if tp:
            yield tp, sub, HOW_WINDOW_SUB, _rid(sub)
    except Exception:
        pass


FIND_RETRIES = 4        # mid-word hits to skip past before giving up on a token
LOOKAHEAD = 400         # chars past the cursor a token may still be claimed at,
                        # in offset mode. FindText has no such bound and that is
                        # how a cursor ends up in another section: one token that
                        # is not where the voice is matches 3000 chars away and
                        # the cursor never comes back (AUDIT §8 "Bug A"). Tokens
                        # arrive in reading order over the SAME text the anchor
                        # spans, so the honest gap is a few chars -- wide enough
                        # here to cross a stripped heading, list marker or table
                        # row, narrow enough that a far-away homograph loses.
MISS_RESET = 8          # consecutive misses before rewinding the search cursor
DEAD_ERRORS = 3         # consecutive COM failures before the anchor is junk
REWIND_CAP = 6          # rewinds per read before the cursor is declared stuck.
                        # Measured 2026-07-25: one read rewound 112 times and
                        # every one of them restored an equally dead cursor.
                        # Past a handful, rewinding is not a retry strategy --
                        # it is a symptom, and the log should say so once
                        # rather than 112 times.


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _selection_matches(sel_text, utt_text):
    """Is this selection really the text being spoken?

    AUDIT §8 round 4 left this unchecked and flagged it as a latent risk:
    the first non-empty selection anywhere near focus wins, so a stale
    selection elsewhere can hijack the whole read. That was tolerable while
    candidates were only the focused element and its ancestors; now that the
    net includes the foreground window's subtree it is not. Compare
    normalized text both ways -- the selection is usually exactly the
    utterance, but either may be the longer one (the server sanitizes, and a
    selection can run past what got spoken)."""
    a, b = _norm(sel_text)[:40], _norm(utt_text)[:40]
    if not a or not b:
        return False
    n = min(len(a), len(b), 20)
    return a[:n] == b[:n]


def _word_bounded(r, token):
    """Does this FindText hit cover a whole word?

    UIA FindText matches substrings and knows nothing about word
    boundaries: '.md' hits inside 'AUDIT.md' (the editor tab label), 'in'
    inside 'singing'. Measured 2026-07-21: one such hit is *permanent
    damage* -- the cursor only moves forward, so it lands in the app's
    chrome and every later token misses forever (AUDIT §8, `rem=` field).
    Require non-alphanumeric neighbours. Punctuation neighbours stay legal,
    which is what keeps markdown tolerance ('bold' inside '**bold**')."""
    if not token:
        return True
    try:
        before = after = ""
        left = r.Clone()
        if left.MoveEndpointByUnit(UIA.TextPatternRangeEndpoint_Start,
                                   UIA.TextUnit_Character, -1):
            before = (left.GetText(1) or "")[:1]
        right = r.Clone()
        if right.MoveEndpointByUnit(UIA.TextPatternRangeEndpoint_End,
                                    UIA.TextUnit_Character, 1):
            after = ((right.GetText(-1) or "")[-1:])
    except Exception:
        return True          # can't tell: don't reject a possibly-good hit
    return not (before.isalnum() or after.isalnum())


MODE_FIND = "findtext"      # locate tokens with UIA FindText
MODE_OFFSET = "offset"      # ...or by character offset into the range's own text
MODE_PROOF = 3              # painted tokens that count as "this mechanism
                            # works here"; below it, a rewind re-measures once


def _holds(r, token):
    """Does this range actually contain `token`?

    MEASURED 2026-08-12, and it is why .md reads were wrong: on Chromium-
    based providers UIA FindText returns a range of the right LENGTH at the
    WRONG POSITION. Sampling 20 words taken from the provider's own GetText
    and asking FindText to find each one again:

        Obsidian  cm-content (the element .md reads anchor to)   1/20
        Obsidian  window document                                0/20
        VS Code   window / preview documents                   0-1/20
        Firefox   page documents (4 of them)                20/20 each

    So the hit is not merely the wrong occurrence of the word -- the range
    does not contain the word at all. Every caller downstream trusted it:
    rects were real, so the marker painted a real but arbitrary word and the
    cursor advanced to a position unrelated to the text being read. That is
    the "highlighting glitches / jumps around" report, and it also explains
    why `painted=` looked like 40-60% on reads that were visibly wrong --
    the log only ever recorded that a rect was drawn, never that it was
    drawn on the right word (plan.md §1 flagged exactly this caveat).

    One extra GetText per hit is the price of never painting a wrong word."""
    if not token:
        return True
    try:
        got = (r.GetText(len(token) + 4) or "")[:len(token)]
    except Exception:
        return False        # a range that cannot be read cannot be trusted
    return got.lower() == token.lower()


def _range_at(base, off, length):
    """The sub-range [off, off+length) of `base`, by character movement.

    The alternative to FindText, and on the providers where FindText lies
    this is exact: character offsets agree with GetText offsets there
    (measured 20/20 on Obsidian's editor, whole range and sub-range alike).
    The reverse holds on Firefox -- character movement disagrees with
    GetText there while FindText is perfect -- so neither primitive is
    portable and the mode is chosen per anchor by trying it (Anchor._find)."""
    try:
        r = base.Clone()
        if r.MoveEndpointByUnit(UIA.TextPatternRangeEndpoint_Start,
                                UIA.TextUnit_Character, off) != off:
            return None
        r.MoveEndpointByRange(UIA.TextPatternRangeEndpoint_End, r,
                              UIA.TextPatternRangeEndpoint_Start)
        if r.MoveEndpointByUnit(UIA.TextPatternRangeEndpoint_End,
                                UIA.TextUnit_Character, length) != length:
            return None
        return r
    except Exception:
        return None


def _verified_range(base, off, token, drift=0):
    """Build `token`'s range at text offset `off`, tolerating the small
    drift between Python string offsets and UIA character units.

    Markdown editors are full of zero-width characters and embedded objects,
    and those are where the two offset spaces disagree. Measured over a whole
    Obsidian document: 303 words exact, 27 off by **+2**, nothing else inside
    +-2. So try the drift that worked last, then a tight window. Every
    candidate still has to pass _holds(), so a correction can only add hits,
    never a wrong one -- and the window stays tight on purpose, because a
    wide one just finds the same word somewhere else entirely.

    Returns (range or None, drift to remember)."""
    tried = set()
    for d in (drift, 0, 2, -2, 1, -1):
        if d in tried or off + d < 0:
            continue
        tried.add(d)
        r = _range_at(base, off + d, len(token))
        if r is not None and _holds(r, token):
            return r, d
    return None, drift


def _head_range(doc, head):
    """Locate the utterance head by offset, for providers whose FindText
    cannot be trusted to say where it found something.

    Anchoring used a bare FindText, so on Chromium the anchor itself could
    be established at the wrong place -- and then every token afterwards is
    measured from a wrong base. Observed live 2026-08-12 on a head-anchored
    Obsidian read: both primitives scored 1/5 on the resulting range."""
    try:
        text = doc.GetText(-1) or ""
    except Exception:
        return None
    off = text.lower().find(head.lower())
    if off < 0:
        return None
    return _verified_range(doc, off, head)[0]


def _search_word(text, token, lo, hi):
    """Offset of `token` in text[lo:hi] as a whole word, or -1.

    Same boundary rule as _word_bounded -- non-alphanumeric neighbours --
    so it keeps the markdown tolerance the anchor depends on: the spoken
    token is `bold` and the document holds `**bold**`, `2.10` sits after a
    stripped `##`, `claims.md` inside backticks. Done in Python on text we
    already have, which makes the boundary check exact instead of four
    round-trips per token."""
    if not token:
        return -1
    hay, needle = text.lower(), token.lower()
    i = lo
    while True:
        j = hay.find(needle, i, hi)
        if j < 0:
            return -1
        before = text[j - 1] if j > 0 else ""
        after = text[j + len(token)] if j + len(token) < len(text) else ""
        if not (before.isalnum() or after.isalnum()):
            return j
        i = j + 1


class Anchor:
    """The utterance's text range in the source document, plus a cursor:
    tokens are located with FindText inside the not-yet-spoken remainder,
    which keeps alignment even when the server sanitized markdown away."""

    def __init__(self, utt_text):
        self.ok = False
        self.token_ranges = {}      # (chunk_text, idx) -> located UIA range
        self.select_tried = set()   # keys whose Select() fallback already ran
        self.orig = None            # the range AS ACQUIRED, kept for the whole
                                    # read. `last_good` can die together with
                                    # the cursor; this cannot, so it is the
                                    # rewind target of last resort (plan.md
                                    # Phase 5 asked for exactly this and the
                                    # 2026-07-21 implementation dropped it)
        self.last_good = None       # cursor after the last hit that PAINTED
        self.pending_good = None    # ...promoted to last_good once it does
        self.painted = False        # has this anchor ever produced rects?
        self.paints = 0             # ...how many times, for MODE_PROOF
        self.misses = 0             # consecutive locate() failures
        self.rewinds = 0            # cumulative, for SUMMARY
        self.chunk_rewinds = 0      # ...the cap is per CHUNK, see new_chunk()
        self.rewind_stage = 0       # 0 = try last_good, 1 = try orig, 2 = out
        self.stuck = False          # cursor unrecoverable; stop burning polls
        self.errors = 0             # consecutive COM failures on the range
        self.last_error = ""
        self.route = None           # HOW_* provenance of the winning candidate
        self.who = ""               # ...and who it belonged to, for SUMMARY
        self.app = ""               # ...and its image name, which unlike `who`
                                    # is resolved even with DEBUG off, because
                                    # can_select branches on it
        self.select_dead = False    # a Select() probe produced no geometry, so
                                    # this surface does not respond to it --
                                    # stop paying the scroll for nothing
        self.cand = -1
        self.mode = None            # MODE_FIND / MODE_OFFSET, measured once on
                                    # first use by _pick_mode() -- neither
                                    # primitive works everywhere, see _holds()
        self.base = None            # range offsets in `text` are measured from
        self.text = None            # ...its text, cached (refetched on failure)
        self.pos = 0                # offset-mode cursor: index into self.text
        self.drift = 0              # ...UIA character units minus Python ones
        self.pos_good = 0           # ...its confirm()ed rewind target
        self.pending_pos = 0        # ...promoted to pos_good by confirm()
        self.remode_tried = False   # the one re-measurement _rewind() allows
        heads = head_candidates(utt_text)
        for i, (tp, el, route, rid) in enumerate(candidate_patterns()):
            rng, how = None, None
            try:
                sel = tp.GetSelection()
                if sel and sel.Length > 0:
                    r = sel.GetElement(0)
                    stext = r.GetText(200) or ""
                    if stext.strip():
                        if _selection_matches(stext, utt_text):
                            rng, how = r.Clone(), "selection"
                        else:
                            how = "selection-REJECTED"
                            dlog(f"  cand[{i}] stale selection ignored: "
                                 f"{stext[:40]!r}")
            except Exception:
                rng = None
            for h in (heads if rng is None else []):
                try:
                    doc = tp.DocumentRange
                    found = doc.FindText(h, False, True)
                    # `not found`, not `found is None`: a fruitless FindText
                    # can hand back a NULL COM pointer, which is not None but
                    # raises on every use (seen live 2026-07-21)
                    via = "findtext"
                    if found and not _holds(found, h):
                        found = None    # right length, wrong place: the whole
                        via = "offset"  # read would be measured from there
                    if not found:
                        found = _head_range(doc, h)
                    if found:
                        # only the head matched; the utterance continues
                        found.MoveEndpointByRange(
                            UIA.TextPatternRangeEndpoint_End, doc,
                            UIA.TextPatternRangeEndpoint_End)
                        rng, how = found, f"{via}[{h[:24]!r}]"
                        break
                except Exception:
                    pass
            who = _ident(el)
            dlog(f"  cand[{i}] how={how} route={route} who={who} "
                 f"doc={_doc_head(tp)!r}")
            if rng is not None:
                self.remaining = rng
                self.orig = rng.Clone()
                self.last_good = rng.Clone()    # rewind target, see locate()
                self.base = rng.Clone()         # offset mode measures from here
                self.misses = 0
                self.ok = True
                self.route, self.who, self.cand = route, who, i
                self.app = _app_of(el)
                dlog(f"ANCHOR ok via={how} route={route} cand={i}")
                return
        dlog(f"ANCHOR FAILED heads={heads}")

    @property
    def can_select(self):
        """May the Select() geometry fallback run against this anchor?

        Only on the focused element itself. Select() is a VS Code *editor*
        workaround: that editor materializes geometry only near its
        accessibility "page", and selecting a range moves the page onto it.
        Nothing else behaves that way. On a window-level document or a live
        browser page the same call instead moves the target app's REAL
        selection, forcing a scroll and a re-render -- and Firefox rebuilds
        its accessibility tree on re-render, killing every range into the old
        one.

        Measured 2026-07-25 (plan.md F1), 7 real reads: 10 Select() calls,
        geometry produced ZERO times, and 8 of the 10 were followed within
        1-3 polls by found=0 or ANCHOR DEAD. The clean case is utt 5 --
        117 consecutive painting polls in Firefox, one Select(), and the very
        next poll returned UIA_E_ELEMENTNOTAVAILABLE ('Object is not
        connected to server'), then 9 failed re-anchors and a dark tail.
        Gate on the ROUTE, never on cand[0]: the focused element is offered
        first only when it has a TextPattern, so cand[0] is often already an
        ancestor or a window document -- exactly the dangerous case.

        CORRECTED 2026-08-11 -- the route gate above was wrong in BOTH
        directions, and the SUMMARY lines say so:

        - Too loose. `route == focus` only means "the focused element happened
          to expose a TextPattern", which is routinely true in Firefox. Real
          reads: `route=focus sel=4 who=firefox.exe`, i.e. four Select() calls
          into a live page, each moving the real selection and scrolling it.
          That is the user-reported "it keeps jumping from one section to
          another" -- the exact harm this docstring already predicted, let
          through by the test meant to prevent it.
        - Too tight. The VS Code *editor* is frequently reached by `ancestor`,
          not `focus`. Those reads (08-10) logged painted=0/13, 0/12, 0/6,
          0/4 with tokens located but never painted: the gate blocked the one
          case Select() exists for. AUDIT §8 predicted this too ("if that read
          is dark, the gate is too tight").

        So gate on the APP, which is what the workaround was ever about, and
        exclude window-level routes. Then require Select() to prove itself:
        one probe per anchor, and if it yields no geometry the surface does
        not respond to it and we stop -- bounding the worst case to a single
        scroll instead of one per token."""
        if self.select_dead:
            return False
        if self.route not in SELECT_ROUTES:
            return False
        return self.app in SELECT_APPS

    def _degenerate(self, r):
        """Have this range's endpoints collapsed onto each other?

        FindText on a zero-length range can never match, so one such advance
        ends the read: measured 2026-07-25, utt 6 logged rem='' on 639
        consecutive polls and painted 3 tokens out of 259. Nothing used to
        check for this -- the collapse was simply adopted as the new cursor."""
        if r is None:
            return True
        try:
            return r.CompareEndpoints(
                UIA.TextPatternRangeEndpoint_Start, r,
                UIA.TextPatternRangeEndpoint_End) == 0
        except Exception:
            return False        # can't tell: don't discard a usable cursor

    def _rewind(self, why):
        """Ladder: last_good -> orig -> stuck.

        The 2026-07-21 version rewound only to `last_good`, and assigned
        `last_good` AFTER the advance -- so when the advance was what killed
        the cursor, the rewind target was equally dead. All 245 rewinds
        observed on 2026-07-25 were futile for that reason. Two changes fix
        it: `last_good` is now promoted only by confirm() (a hit that
        actually painted), and `orig` is always available behind it."""
        if self.stuck:
            return False
        self.rewinds += 1
        self.chunk_rewinds += 1
        if self.chunk_rewinds > REWIND_CAP:
            self.stuck = True
            dlog(f"CURSOR stuck after {self.chunk_rewinds - 1} rewinds in this "
                 f"chunk ({why}) -- not tracking the spoken text; will retry "
                 f"at the next chunk")
            return False
        if self.paints < MODE_PROOF and not self.remode_tried:
            # Next to nothing has painted with the primitive picked for this
            # anchor. The pick is a measurement taken on a document that can
            # be mid-render, so before rewinding a cursor that may simply be
            # built on the wrong mechanism, measure once more. Bounded to a
            # single retry per anchor -- if the second look agrees, the ladder
            # below runs as usual on the next miss.
            #
            # MODE_PROOF, not "has it ever painted": one lucky rect is not
            # evidence a mechanism works (measured -- a VS Code read located
            # 1 token of 80 and that single paint was enough to suppress the
            # retry). A mode that is genuinely working clears this in the
            # first second of a read.
            self.remode_tried = True
            self.text = None
            try:
                m = self._pick_mode()
            except Exception:
                m = self.mode
            if m != self.mode:
                dlog(f"MODE switch {self.mode} -> {m} ({why}, nothing painted "
                     f"yet)")
                self.mode = m
            # pos_good belongs to the cursor the old mode was keeping; it
            # means nothing in the new one, and a stale value ahead of the
            # cursor would only ever be a rewind FORWARD.
            self.pos, self.drift, self.pos_good = 0, 0, 0
            try:
                if self.orig is not None:
                    self.remaining = self.orig.Clone()
            except Exception:
                pass
            self.misses = 0
            return True
        if self.mode == MODE_OFFSET:
            # Same ladder, integers instead of ranges: back to the last
            # position that painted, then to the anchor's start. Also drop
            # the text snapshot -- in a virtualized editor (CodeMirror,
            # Monaco) a run of misses is often the exposed text having moved
            # under us, and a stale snapshot cannot be searched out of.
            self.text = None
            if self.rewind_stage == 0 and self.pos_good < self.pos:
                self.pos, name = self.pos_good, "pos_good"
                self.rewind_stage = 1
            elif self.rewind_stage <= 1 and self.pos > 0:
                self.pos, name = 0, "anchor start"
                self.rewind_stage = 2
            else:
                self.stuck = True
                dlog(f"CURSOR unrecoverable ({why}) -- offset cursor already "
                     f"at the anchor start")
                return False
            self.misses = 0
            dlog(f"CURSOR rewind #{self.rewinds} to {name} ({why})")
            return True
        tgt, name = None, ""
        if self.rewind_stage == 0 and not self._degenerate(self.last_good):
            tgt, name = self.last_good, "last_good"
            self.rewind_stage = 1
        elif self.rewind_stage <= 1 and not self._degenerate(self.orig):
            tgt, name = self.orig, "orig"
            self.rewind_stage = 2
        if tgt is None:
            self.stuck = True
            dlog(f"CURSOR unrecoverable ({why}) -- last_good and orig are "
                 f"both degenerate or exhausted")
            return False
        try:
            self.remaining = tgt.Clone()
        except Exception:
            return False
        self.misses = 0
        dlog(f"CURSOR rewind #{self.rewinds} to {name} ({why})")
        return True

    def new_chunk(self):
        """A chunk boundary is a natural place to forgive a lost cursor.

        `stuck` exists so a hopeless cursor stops paying a FindText per token
        for the rest of a read -- but making it permanent would turn "mostly
        dark with occasional hits" into "guaranteed dark from here", which is
        strictly worse for the user than the behaviour it replaced. The cap
        is therefore per chunk (one every few seconds), not per read: waste
        stays bounded, and a cursor that was only temporarily confused still
        gets to self-heal. Permanently giving up on an anchor is P3's job,
        and it will have the paint rate to decide it properly."""
        if self.stuck:
            dlog("CURSOR unstuck at chunk boundary -- retrying")
        self.stuck = False
        self.chunk_rewinds = 0
        self.misses = 0
        self.rewind_stage = 0

    def confirm(self):
        """The current token produced real on-screen rectangles, so the
        cursor sitting after it is a position worth returning to.

        Promotion happens HERE and nowhere else. A hit that matched but
        painted nothing is precisely the wrong-document hit that drags the
        cursor into an app's chrome -- enshrining it as the rewind target is
        what made recovery impossible."""
        self.painted = True
        self.paints += 1
        self.rewind_stage = 0
        self.pos_good = self.pending_pos
        if self.pending_good is not None:
            self.last_good = self.pending_good
            self.pending_good = None

    def resync(self, r):
        """Rebuild the cursor from `orig` and a range that was just
        Select()-ed. Select() moves the app's selection and can shift what
        the old `remaining` refers to, so deriving it afresh from the one
        range we still trust is safer than carrying the old one forward.

        `locate()` has already advanced past `r` this poll, so today both
        routes land on the same position and this is belt-and-braces. It
        REPLACES `pending_good` rather than leaving it: if the two ever
        disagree, the stale pre-Select() cursor must not be the one that
        confirm() promotes a couple of lines later."""
        if self.mode == MODE_OFFSET:
            # Select() moves VS Code's accessibility page, and the page IS
            # what the text snapshot was taken from -- so the snapshot and
            # every offset measured against it can shift underneath us.
            # Refetch, then re-find the word just selected near where the
            # cursor already was, so the integer cursor survives the move.
            try:
                tok = (r.GetText(64) or "").strip()
            except Exception:
                tok = ""
            was = self.pos
            self.text = None
            text = self._base_text()
            if tok and text:
                lo = max(0, was - LOOKAHEAD)
                off = _search_word(text, tok, lo,
                                   min(len(text), was + LOOKAHEAD))
                if off >= 0:
                    self.pos = self.pending_pos = off + len(tok)
                    return
            self.pos = self.pending_pos = min(was, len(text))
            return
        if self.orig is None:
            return
        try:
            nxt = self.orig.Clone()
            nxt.MoveEndpointByRange(UIA.TextPatternRangeEndpoint_Start, r,
                                    UIA.TextPatternRangeEndpoint_End)
            if not self._degenerate(nxt):
                self.remaining = nxt
                self.pending_good = nxt.Clone()
        except Exception:
            pass

    @property
    def broken(self):
        """The range no longer belongs to a live element. Firefox rebuilds
        its accessibility tree as a page re-renders and every range into the
        old tree starts raising; measured 2026-07-21, `rem='<err>'` on every
        poll for the rest of a read while the cursor rewound 194 times
        against an equally dead `last_good`. Rewinding cannot fix this --
        only re-anchoring can."""
        return self.errors >= DEAD_ERRORS

    def _find_text(self, token):
        """FindText within the remainder, skipping hits that land inside a
        longer word. Bounded: a token that only ever matches mid-word is
        given up on for this poll rather than scanned to the end."""
        scan = self.remaining
        for _ in range(FIND_RETRIES):
            try:
                r = scan.FindText(token, False, True)
                self.errors = 0     # the range answered: it is alive
                if not r:           # None *or* a NULL COM pointer
                    return None
                if not _holds(r, token):
                    # The provider handed back a range that does not contain
                    # what was searched for. Treat it as a miss: painting it
                    # marks the wrong word and advancing past it scatters the
                    # cursor, which is precisely the damage this fix exists to
                    # stop. _pick_mode() keeps whole surfaces off this path;
                    # this guard catches the individual hit.
                    return None
                if _word_bounded(r, token):
                    return r
                nxt = scan.Clone()      # resume past the bogus hit -- this one
                nxt.MoveEndpointByRange(UIA.TextPatternRangeEndpoint_Start,
                                        r, UIA.TextPatternRangeEndpoint_End)
                scan = nxt              # is real, just inside a longer word
            except Exception as e:
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"
                return None
        return None

    def _base_text(self):
        if self.text is None and self.base is not None:
            try:
                self.text = self.base.GetText(-1) or ""
            except Exception as e:
                # Leave the snapshot UNSET, not empty: caching "" would make
                # every later token miss without ever retrying, and it would
                # also stop `errors` accumulating -- which is what promotes a
                # dead element to `broken` and triggers the mid-read
                # re-anchor. A genuinely dead range now reaches DEAD_ERRORS.
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"
                return ""
        return self.text or ""

    def _find_offset(self, token):
        """Locate the token by searching the anchor's own text, then build
        the range by character movement.

        Bounded by LOOKAHEAD, which is the substantive difference from
        FindText: a token that is not near the cursor is given up on rather
        than claimed hundreds of characters away. Cursor runaway -- one bad
        hit dragging the read into another section permanently -- is not
        recoverable after the fact, so it is prevented here instead."""
        text = self._base_text()
        if not text:
            return None
        off = _search_word(text, token, self.pos,
                           min(len(text), self.pos + LOOKAHEAD + len(token)))
        if off < 0:
            return None
        r, self.drift = _verified_range(self.base, off, token, self.drift)
        if r is None:
            self.text = None    # our snapshot disagrees with the live range:
            return None         # drop it, the next poll refetches
        self.errors = 0
        self.pending_pos = off + len(token)
        return r

    def _pick_mode(self):
        """Ask the surface which primitive tells the truth, before trusting
        either with a read.

        Sampling matters more than it looks. FindText's displacement on a
        Chromium provider GROWS with distance into the document -- measured
        deltas of 64, 69, 88, 96 characters at increasing depth -- so it is
        very often correct for the FIRST word and wrong for everything after.
        Deciding on the opening token therefore picks FindText on exactly the
        surfaces it fails on (observed: Obsidian's editor locked to findtext
        and then located 17 of 80 tokens; sampled at depth it picks offset
        and locates 77). Take words from across the whole range instead.

        The two cursors are separate -- `remaining` for FindText, an integer
        for offsets -- so the mode cannot be mixed per token without one of
        them going stale. It is chosen once, here."""
        text = self._base_text()
        words = [(m.start(), m.group(0))
                 for m in re.finditer(r"[A-Za-z]{5,}", text)]
        if len(text) < 200 or len(words) < 5:
            return MODE_FIND        # too small to measure; the status quo
        # 8, not 3 or 4: these documents mutate while they are probed (a
        # CodeMirror/Monaco viewport re-renders under the walk), so a small
        # sample is noisy -- measured 0/5 vs 1/5 on a surface that scored
        # 0/30 vs 8/30 moments later. Still ~50 COM calls, paid once per
        # anchor, against a head ladder that can cost 238ms.
        picks = [words[int(len(words) * (i + .5) / 8)] for i in range(8)]
        find_ok = off_ok = 0
        for off, w in picks:
            try:
                r = self.base.FindText(w, False, True)
            except Exception:
                r = None
            if r and _holds(r, w):
                find_ok += 1
            r2 = _range_at(self.base, off, len(w))
            if r2 is not None and _holds(r2, w):
                off_ok += 1
        mode = MODE_FIND if find_ok >= off_ok else MODE_OFFSET
        dlog(f"MODE {mode} (findtext {find_ok}/{len(picks)}, "
             f"offset {off_ok}/{len(picks)} on sampled words)")
        return mode

    def _find(self, token):
        """Locate a token with whichever primitive this surface honours.

        Neither is portable: FindText is exact on Firefox and returns ranges
        that do not contain the search string on Chromium; character offsets
        are exact on Chromium and disagree with GetText on Firefox (all
        measured, see _holds). Asking the surface beats naming the app."""
        if self.mode is None:
            try:
                self.mode = self._pick_mode()
            except Exception:
                self.mode = MODE_FIND
        if self.mode == MODE_OFFSET:
            return self._find_offset(token)
        return self._find_text(token)

    def locate(self, chunk_text, idx, token):
        key = (chunk_text, idx)
        if key in self.token_ranges:
            return self.token_ranges[key]
        if self.stuck:      # the cursor is provably lost; stop paying for
            return None     # a FindText per token for the rest of the read
        r = self._find(token)
        if r is None:
            # A run of misses means the cursor is lost -- it was dragged
            # somewhere the text isn't, and since it only moves forward the
            # read would stay dark to the end. Rewind rather than accept that.
            self.misses += 1
            if self.misses >= MISS_RESET:
                self._rewind(f"{self.misses} misses, tok={token!r}")
            return None                 # misses are NOT cached: a later poll
        self.misses = 0                 # may succeed once the app warms up
        if self.mode == MODE_OFFSET:
            # The cursor is an integer here, so there is no collapsed-range
            # case to defend against and no COM call to advance it.
            self.pos = self.pending_pos
            self.token_ranges[key] = r
            return r
        try:
            nxt = self.remaining.Clone()
            nxt.MoveEndpointByRange(UIA.TextPatternRangeEndpoint_Start,
                                    r, UIA.TextPatternRangeEndpoint_End)
            if self._degenerate(nxt):
                # Do NOT adopt a collapsed cursor -- that is the state utt 6
                # never came back from (639 polls of rem=''). Keep the cursor
                # we had, which can still match the next token, and rewind.
                dlog(f"CURSOR collapse refused on tok={token!r}")
                self._rewind(f"advance collapsed on {token!r}")
            else:
                self.remaining = nxt
                # NOT last_good: this hit has matched, but nothing yet says it
                # matched the right place. confirm() promotes it once it paints.
                self.pending_good = nxt.Clone()
        except Exception:
            pass
        self.token_ranges[key] = r
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
RESUME_WINDOW = 5.0     # the same utt reappearing this soon after a DROP is
                        # one read continuing (RC6), not a new read
GRACE = 2.0             # /now reports active:false whenever playback slips
                        # >0.3s past a chunk's end -- a starvation gap or an
                        # awkward chunk boundary, not necessarily the end of
                        # the read. Hold the anchor this long before believing
                        # it. Measured 2026-07-21: real mid-read gaps were
                        # 0.12-0.74s, and re-anchoring after one cost the
                        # whole rest of the read because focus had moved on
                        # (AUDIT §8). The marker still hides immediately --
                        # nothing is sounding -- only the state is kept.


def _new_stats(utt):
    return {"utt": utt, "tries": 0, "acq": "", "who": "?", "route": None,
            "cand": -1, "seen": set(), "located": set(), "painted": set(),
            "sel": 0, "dead": 0, "rewinds": 0, "stuck": 0, "mode": None,
            "done": False}


def _summary(st, anchor, why):
    """One line per read: which app, which route, how fast it anchored, and
    how much of it actually painted.

    The per-token lines stay for detail, but ranking a day of real use should
    be `Select-String SUMMARY` -- answering "which reads worked, and why not"
    for the 2026-07-25 diagnosis took a purpose-written parser, which is why
    nobody had done it in the two days the problems were being noticed."""
    if not st or st["done"]:
        return
    st["done"] = True
    if anchor is not None:
        st["rewinds"] += anchor.rewinds
        st["stuck"] += 1 if anchor.stuck else 0
        st["mode"] = anchor.mode or st["mode"]
    seen, loc, pai = len(st["seen"]), len(st["located"]), len(st["painted"])
    pct = f"{100 * pai // seen}%" if seen else "n/a"
    dlog(f"SUMMARY utt={st['utt']} {why} painted={pai}/{seen} ({pct}) "
         f"located={loc} missed={seen - loc} route={st['route']} "
         f"cand={st['cand']} tries={st['tries']} "
         f"anchor={st['acq'] or 'NEVER'} mode={st['mode']} sel={st['sel']} "
         f"rewinds={st['rewinds']} stuck={st['stuck']} dead={st['dead']} "
         f"who={st['who']}")


def main(marker):
    anchor = None
    stats = None
    utt_seen = None
    chunk_seen = None
    resolved = -1               # tokens of current chunk located so far
    utt_t0 = 0.0
    anchor_until = 0.0
    anchor_next = 0.0
    tries = 0                   # anchor attempts for this utterance
    gaveup = False              # ANCHOR_WINDOW expiry already logged
    idle_since = 0.0            # when /now first went inactive (0 = active)
    wiped = None                # (utt, wallclock) of the last dropped anchor
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
                marker.hide()       # nothing is sounding: never paint here
                if d is not None and utt_seen is not None:
                    # ...but do NOT throw the anchor away yet (RC6). It is
                    # still a valid range; the read has probably just stalled
                    # between chunks. Rebuilding it means re-running
                    # candidate_patterns(), and by then focus may have moved
                    # to another app entirely -- measured: that loses the
                    # rest of the read, not just a frame.
                    t = time.time()
                    if not idle_since:
                        idle_since = t
                        dlog(f"IDLE utt={utt_seen} holding anchor "
                             f"(anchored={anchor is not None} "
                             f"chunk={(chunk_seen or '')[:24]!r})")
                    elif t - idle_since >= GRACE:
                        dlog(f"DROP utt={utt_seen} idle {t - idle_since:.2f}s "
                             f"-- read is over, anchor released")
                        _summary(stats, anchor, "end")
                        wiped = (utt_seen, t)
                        anchor, utt_seen, idle_since = None, None, 0.0
                time.sleep(POLL_IDLE)
                continue

            if idle_since:
                # sound is back within the grace window
                held = d.get("utt") == utt_seen
                dlog(f"{'HELD' if held else 'NEWUTT'} after "
                     f"{time.time() - idle_since:.2f}s idle "
                     f"(utt={d.get('utt')} prev={utt_seen})"
                     f"{' -- RC6 avoided, no re-anchor' if held else ''}")
                idle_since = 0.0

            if d.get("utt") != utt_seen:
                # a read that never DROPped (stopped, or a new /speak landed
                # on top of it) still deserves its one line
                _summary(stats, anchor, "superseded")
                utt_seen = d.get("utt")
                if wiped and wiped[0] == utt_seen and \
                        time.time() - wiped[1] <= RESUME_WINDOW:
                    # same gen resurfacing AFTER the grace already expired:
                    # RC6 got through anyway, and GRACE is too short.
                    dlog(f"RESUME utt={utt_seen} "
                         f"{time.time() - wiped[1]:.2f}s past the {GRACE}s "
                         f"grace -- RC6 survived; re-anchoring mid-read")
                wiped = None
                chunk_seen = None
                anchor = None
                stats = _new_stats(utt_seen)
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
                    if stats:
                        stats["tries"] = tries
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
                            if stats:
                                stats.update(route=a.route, cand=a.cand,
                                             who=a.who or "?",
                                             acq=f"try#{tries}"
                                                 f"/{time.time() - utt_t0:.2f}s")
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
                anchor.new_chunk()      # forgive a stuck cursor; see new_chunk

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
            if anchor.broken:
                # Not the RC6 case: this anchor is provably dead, not merely
                # idle, so holding it is pointless. Rebuild it -- and re-arm
                # the retry window, because the utterance's original one has
                # usually expired by now.
                dlog(f"ANCHOR DEAD utt={utt_seen} ({anchor.last_error}) "
                     f"-- re-anchoring mid-read")
                if stats:       # fold the dying anchor's counters in before
                    stats["dead"] += 1          # it is replaced, so SUMMARY
                    stats["rewinds"] += anchor.rewinds      # stays complete
                    stats["stuck"] += 1 if anchor.stuck else 0
                anchor, resolved = None, -1
                utt_t0 = time.time()
                anchor_until = utt_t0 + ANCHOR_WINDOW
                anchor_next = 0.0
                tries = 0
                gaveup = False
                marker.hide()
                time.sleep(POLL_ACTIVE)
                continue
            rng = anchor.token_ranges.get((chunk_seen, idx))
            rr = []                 # reset every poll: a stale rr would make
            sel = 0                 # a failed lookup log as the last success
            rem = ""
            if rng is None:
                marker.hide()
                # Where is the search cursor? Every observed all-miss read
                # located a few tokens and then missed EVERY subsequent one,
                # which is the signature of `remaining` having collapsed or
                # jumped past the text. '' here = collapsed; text far beyond
                # the spoken word = a bad FindText hit dragged it forward.
                if DEBUG:
                    try:
                        if anchor.mode == MODE_OFFSET:
                            rem = (anchor.text or "")[anchor.pos:
                                                      anchor.pos + 30]
                        else:
                            rem = anchor.remaining.GetText(30) or ""
                        rem = rem.replace("\n", " ")
                    except Exception as e:
                        rem = f"<err {type(e).__name__}: {e}>"[:60]
            else:
                rr = rects_of(rng)
                if rr:
                    # real geometry: this hit is verified, so the cursor
                    # sitting after it becomes the rewind target (P2)
                    anchor.confirm()
                elif anchor.can_select and \
                        (chunk_seen, idx) not in anchor.select_tried:
                    sel = 1
                    # VS Code EDITOR only (see Anchor.can_select): geometry
                    # exists only near its accessibility page; selecting the
                    # word moves the page onto it. Everywhere else this call
                    # moves the app's real selection and kills the anchor.
                    anchor.select_tried.add((chunk_seen, idx))
                    if stats:
                        stats["sel"] += 1
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
                        anchor.resync(rng)      # the selection moved; derive
                        if rr:                  # the cursor from orig, not
                            anchor.confirm()    # from the disturbed remainder
                        else:
                            # The probe bought nothing. This surface does not
                            # materialize geometry on selection, so every
                            # further Select() would be a pure scroll of the
                            # user's document -- one per anchor is the cap.
                            anchor.select_dead = True
                            dlog(f"SELECT dead app={anchor.app!r} "
                                 f"route={anchor.route}: probe produced no "
                                 f"geometry, fallback off for this read")
                    except Exception:
                        pass
                marker.draw(rr)
            if stats is not None:
                k = (chunk_seen, idx)
                stats["seen"].add(k)
                if rng is not None:
                    stats["located"].add(k)
                    if rr:
                        stats["painted"].add(k)
            # found=0 (FindText could not locate the token in the remainder:
            # an alignment/anchor problem) is a COMPLETELY different failure
            # from found=1 rects=[] (located, but the app materializes no
            # geometry for it) -- both used to log as an empty rect list.
            # sel=1 marks the poll where the Select() fallback ran.
            dlog(f"chunk={chunk_seen[:20]!r} idx={idx} "
                 f"tok={words[idx][0]!r} found={0 if rng is None else 1} "
                 f"m={anchor.mode} sel={sel} rects={rr}"
                 f"{f' rem={rem!r}' if rng is None else ''}")
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
