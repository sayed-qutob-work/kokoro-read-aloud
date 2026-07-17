r"""In-place spoken-word highlighter for native apps (Notepad, editors,
terminals - anything that implements the UI Automation TextPattern).

The point: highlight the word being spoken ON the original text, not a
copy of it. Windows won't let one process restyle another's rendered
text, but UI Automation exposes the exact on-screen rectangle of any
text range, and a layered click-through window can tint just that
rectangle. Visually the word in the source document gets a translucent
marker, Speechify-style. Browsers are covered separately (and better)
by the extension in C:\kokoro\extension; if an app exposes no
TextPattern, this process simply does nothing.

Flow, per utterance (tracked via /now's `utt` counter):
  1. Fetch the original text from /utterance.
  2. Anchor it in the focused app: the current UIA text selection if it
     still exists (editors keep it after Ctrl+C), else FindText of the
     text's head in the document (covers terminals, which drop
     selections).
  3. Per spoken token from /now: FindText the token within the not-yet-
     spoken remainder (self-aligning, tolerant of markdown the server
     sanitized away), then draw its bounding rectangles. Rects are
     re-queried every poll, so scrolling moves the marker correctly.

Launched hidden by start_tts.vbs with pythonw.exe. Kill it and nothing
else changes.
"""
import ctypes
import json
import time
from ctypes import wintypes
from urllib.request import urlopen

import numpy as np
import comtypes  # noqa: F401  (initializes COM)
import comtypes.client

NOW = "http://127.0.0.1:5111/now"
UTTER = "http://127.0.0.1:5111/utterance"
POLL_ACTIVE = 0.08
POLL_IDLE = 0.5
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
        hdc_screen = user32.GetDC(None)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbm = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(hdc_mem, hbm)

        buf = (ctypes.c_ubyte * (w * h * 4)).from_address(bits.value)
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        arr[:] = 0
        a = HL_ALPHA
        px = (HL_RGB[2] * a // 255, HL_RGB[1] * a // 255,
              HL_RGB[0] * a // 255, a)                  # premultiplied BGRA
        for l, t, r, b in rects:
            arr[max(0, t - PAD - y0):min(h, b + PAD - y0),
                max(0, l - PAD - x0):min(w, r + PAD - x0)] = px

        pos = wintypes.POINT(x0, y0)
        size = wintypes.SIZE(w, h)
        src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)             # AC_SRC_ALPHA
        user32.UpdateLayeredWindow(self.hwnd, hdc_screen,
                                   ctypes.byref(pos), ctypes.byref(size),
                                   hdc_mem, ctypes.byref(src), 0,
                                   ctypes.byref(blend), 2)  # ULW_ALPHA

        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc_mem)
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

def get(url):
    try:
        with urlopen(url, timeout=0.15) as r:
            return json.load(r)
    except Exception:
        return None


def text_pattern_of_focus():
    try:
        el = uia.GetFocusedElement()
        pat = el.GetCurrentPattern(UIA.UIA_TextPatternId)
        if not pat:
            return None
        return pat.QueryInterface(UIA.IUIAutomationTextPattern)
    except Exception:
        return None


class Anchor:
    """The utterance's text range in the source document, plus a cursor:
    tokens are located with FindText inside the not-yet-spoken remainder,
    which keeps alignment even when the server sanitized markdown away."""

    def __init__(self, utt_text):
        self.ok = False
        self.token_ranges = {}      # (chunk_text, idx) -> UIA range or None
        tp = text_pattern_of_focus()
        if tp is None:
            return
        rng = None
        try:
            sel = tp.GetSelection()
            if sel and sel.Length > 0:
                r = sel.GetElement(0)
                if (r.GetText(200) or "").strip():
                    rng = r.Clone()
        except Exception:
            rng = None
        if rng is None:
            head = utt_text.strip()[:60]
            if not head:
                return
            try:
                doc = tp.DocumentRange
                found = doc.FindText(head, False, True)
                if found is None:
                    return
                # only the head matched; the utterance continues past it
                found.MoveEndpointByRange(
                    UIA.TextPatternRangeEndpoint_End, doc,
                    UIA.TextPatternRangeEndpoint_End)
                rng = found
            except Exception:
                return
        self.remaining = rng
        self.ok = True

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
        self.token_ranges[key] = r
        return r


def rects_of(rng):
    try:
        vals = rng.GetBoundingRectangles()
        return [(vals[i], vals[i + 1], vals[i + 2], vals[i + 3])
                for i in range(0, len(vals) - 3, 4)]
    except Exception:
        return []


def main():
    marker = Marker()
    anchor = None
    utt_seen = None
    chunk_seen = None
    resolved = -1               # tokens of current chunk located so far

    while True:
        pump()
        d = get(NOW)
        if not d or not d.get("active"):
            marker.hide()
            if d is not None:   # server reachable: idle, forget the read
                anchor, utt_seen = None, None
            time.sleep(POLL_IDLE)
            continue

        if d.get("utt") != utt_seen:
            utt_seen = d.get("utt")
            chunk_seen = None
            u = get(UTTER)
            anchor = Anchor(u["text"]) if u and u.get("utt") == utt_seen \
                else None
            if anchor is not None and not anchor.ok:
                anchor = None

        if anchor is None:
            marker.hide()
            time.sleep(POLL_ACTIVE)
            continue

        if d["text"] != chunk_seen:
            chunk_seen = d["text"]
            resolved = -1

        idx = d.get("word", -1)
        if idx < 0:
            time.sleep(POLL_ACTIVE)
            continue
        # resolve tokens in order so FindText's cursor advances correctly
        while resolved < idx:
            resolved += 1
            anchor.locate(chunk_seen, resolved, d["words"][resolved][0])
        rng = anchor.token_ranges.get((chunk_seen, idx))
        if rng is None:
            marker.hide()
        else:
            marker.draw(rects_of(rng))
        time.sleep(POLL_ACTIVE)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
