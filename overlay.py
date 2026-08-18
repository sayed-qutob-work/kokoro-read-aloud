r"""Karaoke caption overlay for the read-aloud server.

Polls GET /now on tts_server.py and shows a few lines of context around
the sentence currently sounding (Speechify-style, but source-agnostic:
works whether the text came from a browser, a PDF, a terminal or an
editor, because it never touches the source app). Runs as its own
process, launched hidden by start_tts.vbs with pythonw.exe on Windows
(read_aloud.sh does not launch it on Linux yet; run it directly); the
server does not know it exists.

Highlights at SENTENCE granularity, not per word (RELEASE_PLAN §3.2).
The server does the sentence grouping (/now returns `sentence` with
`prev`/`next` around it, §3.1); this only paints it.

The three regions are three fixed ROWS -- one line of already-spoken
context, the current sentence, one line of upcoming text -- so the
highlighted sentence always starts at the same point on screen. Flowing
them as one paragraph instead made the sentence begin wherever the last
one ended, moving on every advance, and it was hard to follow.

Three visual styles (settings.json "caption_style", or KOKORO_CAPTION_STYLE
to override) came out of a design pass with three genuinely different
directions on the table -- see THEMES below. All three were judged good
enough to keep, so the choice is the user's, not baked into the code.

Drag to move. Right-click to close. Appears only while speech is
playing; hides itself ~0.7s after playback ends or Ctrl+Alt+S.
"""
import json
import os
import re
import subprocess
import time
import tkinter as tk
import tkinter.font as tkfont
from urllib.request import urlopen

URL = "http://127.0.0.1:5111/now"
POLL_MS = 80          # while visible
IDLE_MS = 500         # while hidden (also covers "server down")
HIDE_AFTER_MS = 700   # inactive time before the strip hides
WIDTH = 900
# "rows": one context line + up to ~3 lines of sentence + one context
# line. "teleprompter": TELE_ROWS lines of running text with the current
# sentence pinned to row TELE_ANCHOR. Both plus indicator row and hint.
ROWS_HEIGHT = 220
TELE_HEIGHT = 258
TELE_ROWS, TELE_ANCHOR = 7, 2
MARGIN_EDGE = 90          # gap between the strip and the screen edge

MAX_ALPHA = 0.94
FADE_MS = 160          # show/hide fade duration
FADE_STEP_MS = 16      # ~60fps, the tick for every animation here
SCROLL_MS = 280        # teleprompter glide between reading positions

_ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(_ROOT, "settings.json")

# "Segoe UI"/"Consolas" (Windows) aren't installed on Linux; Tk silently
# substitutes something for a missing family, but picking a real match
# keeps the look intentional on both platforms instead of leaving it to
# chance.
_SANS = ("Segoe UI", "Cantarell", "Noto Sans", "DejaVu Sans")
_MONO = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Liberation Mono")


def _pick_font(families, size, **kw):
    available = set(tkfont.families())
    for name in families:
        if name in available:
            return tkfont.Font(family=name, size=size, **kw)
    return tkfont.Font(size=size, **kw)  # Tk's platform default


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout


def _monitors_from_xrandr():
    out = []
    for line in _run(["xrandr", "--query"]).splitlines():
        if " connected" not in line:
            continue
        m = re.search(r"(\d+)x(\d+)\+(\d+)\+(\d+)", line)
        if m:
            w, h, x, y = (int(g) for g in m.groups())
            out.append({"name": line.split()[0], "x": x, "y": y,
                        "w": w, "h": h, "primary": " primary " in line})
    return out


def _monitors_from_mutter():
    """GNOME/Wayland, where xrandr is often not installed at all."""
    out = _run(["gdbus", "call", "--session",
                "--dest", "org.gnome.Mutter.DisplayConfig",
                "--object-path", "/org/gnome/Mutter/DisplayConfig",
                "--method", "org.gnome.Mutter.DisplayConfig.GetCurrentState"])
    if not out:
        return []
    mons = []
    # logical monitor: (x, y, scale, transform, primary, [(connector,...)], {})
    # gdbus prints the type tag only on the FIRST tuple ("uint32 0" then bare
    # "0"), so that prefix has to be optional or only one monitor is ever found
    for lm in re.finditer(r"\((\d+), (\d+), ([\d.]+), (?:uint32 )?\d+, "
                          r"(true|false), \[\('([^']+)'", out):
        x, y, scale = int(lm.group(1)), int(lm.group(2)), float(lm.group(3))
        primary, connector = lm.group(4) == "true", lm.group(5)
        # the logical monitor carries no size; its connector's CURRENT mode does
        try:
            blk = out[out.index(f"(('{connector}',"):]
        except ValueError:
            continue
        for m in re.finditer(r"\('[^']+', (\d+), (\d+), [\d.]+, [\d.]+, "
                             r"\[[^\]]*\], \{([^}]*)\}\)", blk):
            if "'is-current': <true>" in m.group(3):
                mons.append({"name": connector, "x": x, "y": y,
                             "w": round(int(m.group(1)) / scale),
                             "h": round(int(m.group(2)) / scale),
                             "primary": primary})
                break
    return mons


def list_monitors(root=None):
    """Every monitor as {name, x, y, w, h, primary}.

    Tk only ever reports the union of them all - measured 3840x1080
    across two screens - so anything positional has to come from here.
    Both probes are best-effort; the union is the fallback and is right
    on a single-monitor desktop (and on Windows, where Tk reports the
    primary monitor anyway). Also used by the tray to populate the
    monitor dropdown, which is why it takes no Tk objects when `root`
    is not given."""
    for probe in (_monitors_from_xrandr, _monitors_from_mutter):
        try:
            got = [m for m in probe() if m["w"] > 0 and m["h"] > 0]
        except Exception:
            got = []
        if got:
            if not any(m["primary"] for m in got):
                got[0]["primary"] = True
            return got
    if root is not None:
        return [{"name": "Screen", "x": 0, "y": 0,
                 "w": root.winfo_screenwidth(), "h": root.winfo_screenheight(),
                 "primary": True}]
    return []


def pick_monitor(monitors, want):
    """`want` is a connector name, or "primary"/anything unknown."""
    if not monitors:
        return None
    for m in monitors:
        if m["name"] == want:
            return m
    return next((m for m in monitors if m["primary"]), monitors[0])


def _wrap(text, font, px):
    """Greedy word wrap to a list of lines that each fit `px`.

    Done here rather than by the Text widget so the teleprompter knows
    exactly how many lines everything occupies - that is what lets the
    current sentence sit on a fixed row."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}" if line else word
        if line and font.measure(trial) > px:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def _clip_line(text, font, px, keep="head"):
    """Trim `text` so it occupies exactly one display line of `px` pixels.

    The context rows MUST NOT wrap: a two-line neighbour would push the
    highlighted sentence to a different height and the reader would have
    to hunt for it again (see `render`). keep="tail" keeps the END of the
    text (the words just spoken), keep="head" keeps the beginning."""
    if not text or font.measure(text) <= px:
        return text
    ell = "…"
    if keep == "tail":
        lo, hi = 0, len(text)
        while lo < hi:                      # smallest suffix that fits
            mid = (lo + hi) // 2
            if font.measure(ell + text[mid:]) <= px:
                hi = mid
            else:
                lo = mid + 1
        return ell + text[lo:].lstrip()
    lo, hi = 0, len(text)
    while lo < hi:                          # longest prefix that fits
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + ell) <= px:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ell


# Three directions from the design pass (kokoro-caption-strip-directions
# artifact), all kept -- none of them was a clear loser, so this is a
# setting, not a decision made for the user.
THEMES = {
    # A -- no fill on the current sentence at all: bold text plus a
    # colored underline, and a dot+label instead of a top bar.
    "underline": dict(
        fonts=_SANS, font_size=14,
        bg="#16161d", border="#2c2c38",
        fg_done="#55555f", fg_now="#f4f4f8", fg_next="#9a9aa8",
        now_bold=True, now_style="underline",
        accent="#ffb454", label="R E A D I N G", label_color="#c98a3c",
        hint_color="#3d3d4c", indicator="dot", rail=False,
    ),
    # B -- monospace, near-black, phosphor green; a caret marks where
    # speech is, small equalizer bars replace the top bar.
    "terminal": dict(
        fonts=_MONO, font_size=13,
        bg="#0c0c0f", border="#23231f",
        fg_done="#3a4a42", fg_now="#eafff5", fg_next="#7a9a8a",
        now_bold=False, now_style="underline", caret=True,
        accent="#5fffb0", label="reading -- live", label_color="#3f7a5e",
        hint_color="#2c2c28", indicator="equalizer", rail=False,
    ),
    # C -- left accent rail instead of a top bar; current sentence gets
    # a muted low-contrast tint, not a loud solid block.
    "rail": dict(
        fonts=_SANS, font_size=14,
        bg="#17171f", border="#2a2a35",
        fg_done="#63636f", fg_now="#f2f0ff", fg_next="#c7c5d6",
        now_bold=False, now_style="fill", now_bg="#2a2440",
        accent="#b48cff", label="Reading", label_color="#8f8fa3",
        hint_color="#414150", indicator="waveform", rail=True,
    ),
}
DEFAULT_STYLE = "underline"


LAYOUTS = ("rows", "teleprompter")
DEFAULT_LAYOUT = "teleprompter"
POSITIONS = ("bottom", "center", "top")
DEFAULT_POSITION = "bottom"

# How the teleprompter moves. "continuous" is what a real prompter does:
# the text creeps at reading pace instead of holding still and then
# jumping a line. "line" glides a line at a time; "off" snaps, for anyone
# who does not want motion at all.
SCROLLS = ("continuous", "line", "off")
DEFAULT_SCROLL = "continuous"

# Continuous scrolling is a velocity model, not a chase. The reading rate
# reported by the server is genuinely uneven (word durations vary, so the
# cursor moved 5.8-15.7 chars per sample over one measured read), and the
# poll only lands every POLL_MS. Chasing that directly reproduced both the
# jitter and a decelerate-then-stall pulse 12x a second. So: estimate the
# pace, smooth it hard, and predict between polls.
VEL_SMOOTH = 0.88         # weight kept from the previous pace estimate
VEL_MAX = 20.0            # lines/s; above this it is a seek, not reading
FOLLOW_GAIN = 0.08        # per-frame correction toward the predicted point
PREDICT_CAP = 0.6         # seconds of extrapolation if polls stop arriving
JUMP_LINES = 6            # further than this = new utterance, place directly


def _raw_setting(key):
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f).get(key)
    except (FileNotFoundError, ValueError):
        return None


def _setting(env, key, allowed, default):
    val = os.environ.get(env) or _raw_setting(key)
    return val if val in allowed else default


class Overlay:
    def __init__(self):
        self.theme = THEMES[_setting("KOKORO_CAPTION_STYLE", "caption_style",
                                     THEMES, DEFAULT_STYLE)]
        self.layout = _setting("KOKORO_CAPTION_LAYOUT", "caption_layout",
                               LAYOUTS, DEFAULT_LAYOUT)
        self.position = _setting("KOKORO_CAPTION_POSITION", "caption_position",
                                 POSITIONS, DEFAULT_POSITION)
        # any connector name ("HDMI-1"); "primary" and unknown names both
        # fall back to the primary monitor
        self.monitor = (os.environ.get("KOKORO_CAPTION_MONITOR")
                        or _raw_setting("caption_monitor") or "primary")
        # motion can be unwelcome (vestibular sensitivity, or just taste),
        # and it is the kind of thing that must be switchable off
        self.scroll = _setting("KOKORO_CAPTION_SCROLL", "caption_scroll",
                               SCROLLS, DEFAULT_SCROLL)
        self.smooth = self.scroll != "off"
        self.height = TELE_HEIGHT if self.layout == "teleprompter" else ROWS_HEIGHT
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.0)   # fades in on first show
        self._build_ui()

        self.root.geometry(self._geometry())
        for w in self._draggable:
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<Button-3>", lambda e: self.root.destroy())
        self.visible = False
        self.miss = 0
        self._alpha = 0.0
        self._fade_job = None
        self._scroll_job = None
        self._scroll_pos = 0.0
        self._scroll_target = 0.0
        self._vel = 0.0            # reading pace, wrapped lines per second
        self._target_time = None
        self._line_h = None
        self._tele_full = None
        self._tele_lines, self._tele_offsets = [], []
        self._reset_sentence_state()
        self.root.after(IDLE_MS, self.poll)
        self.root.after(FADE_STEP_MS, self._tick_scroll)

    def _build_ui(self):
        th = self.theme
        bg, border, accent = th["bg"], th["border"], th["accent"]

        self.root.configure(bg=border)          # 1px border shows through the pad
        outer = tk.Frame(self.root, bg=border)
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        card = tk.Frame(outer, bg=bg)
        card.pack(fill="both", expand=True)

        left = tk.Frame(card, bg=accent, width=4) if th["rail"] else None
        if left:
            left.pack(fill="y", side="left")
        else:
            tk.Frame(card, bg=accent, height=3).pack(fill="x", side="top")

        body = tk.Frame(card, bg=bg)
        body.pack(fill="both", expand=True, side="left" if th["rail"] else "top")

        indicator_row = tk.Frame(body, bg=bg)
        indicator_row.pack(fill="x", side="top",
                           padx=(18 if th["rail"] else 20, 20),
                           pady=(14, 0))
        self._build_indicator(indicator_row)

        pad = 18 if th["rail"] else 20
        tele = self.layout == "teleprompter"
        text_font = _pick_font(th["fonts"], th["font_size"])
        # The teleprompter pre-wraps the passage with `text_font` and then
        # scrolls those fixed lines, so the highlight MUST NOT change font
        # metrics: bold is ~14px wider over a line, and a sentence losing
        # bold as the read moves on dragged the following sentence -- which
        # usually shares that wrapped line -- visibly to the left. Colour,
        # underline and background are all metric-neutral; weight is not.
        now_font = text_font if tele else _pick_font(
            th["fonts"], th["font_size"],
            weight="bold" if th["now_bold"] else "normal")
        self.font = text_font
        # usable text width, for the one-line clamp and the wrapper
        self.text_px = WIDTH - 2 - (4 if th["rail"] else 0) - 2 * pad
        # teleprompter pre-wraps its own lines so it can count them, so
        # the widget must not wrap on top of that
        self.text = tk.Text(body, wrap="none" if tele else "word", bd=0,
                            padx=pad, pady=14,
                            bg=bg, fg=th["fg_next"], cursor="arrow",
                            highlightthickness=0, font=text_font,
                            spacing2=0 if tele else 5, state="disabled")
        self.text.pack(fill="both", expand=True, side="top")
        self.text.tag_configure("done", foreground=th["fg_done"])
        self.text.tag_configure("caret", foreground=accent, font=now_font)
        self.text.tag_configure("upcoming", spacing1=0 if tele else 8)
        now_cfg = dict(foreground=th["fg_now"], font=now_font,
                       spacing1=0 if tele else 8, spacing3=0 if tele else 2)
        if th["now_style"] == "fill":
            self.text.tag_configure("now", background=th["now_bg"], **now_cfg)
        else:
            self.text.tag_configure("now", underline=True, underlinefg=accent,
                                    **now_cfg)

        hint = tk.Label(body, text="drag to move  ·  right-click to close",
                        bg=bg, fg=th["hint_color"],
                        font=_pick_font(th["fonts"], 9), anchor="e")
        hint.pack(fill="x", side="bottom", padx=(18 if th["rail"] else 16, 20),
                  pady=(0, 8))

        self._draggable = [self.root, outer, card, body, indicator_row,
                           self.text, hint] + ([left] if left else [])

    def _build_indicator(self, row):
        th = self.theme
        kind = th["indicator"]
        if kind == "dot":
            c = tk.Canvas(row, width=8, height=8, bg=th["bg"],
                         highlightthickness=0)
            c.create_oval(1, 1, 7, 7, fill=th["accent"], outline="")
            c.pack(side="left")
        elif kind in ("equalizer", "waveform"):
            heights = (6, 11, 8, 14, 5) if kind == "equalizer" else (4, 10, 14, 8, 4)
            w = tk.Canvas(row, width=len(heights) * 4, height=14,
                         bg=th["bg"], highlightthickness=0)
            for i, h in enumerate(heights):
                x = i * 4
                w.create_rectangle(x, 14 - h, x + 2, 14, fill=th["accent"],
                                   outline="")
            w.pack(side="left")
        lbl = tk.Label(row, text=th["label"], bg=th["bg"], fg=th["label_color"],
                       font=_pick_font(th["fonts"], 11 if th["indicator"] == "dot"
                                       else 12))
        lbl.pack(side="left", padx=(6, 0))

    def _geometry(self):
        """Where the strip opens: horizontally centred on the chosen
        monitor, and vertically per `caption_position`."""
        mon = pick_monitor(list_monitors(self.root), self.monitor)
        if mon is None:
            return f"{WIDTH}x{self.height}+100+100"
        x = mon["x"] + (mon["w"] - WIDTH) // 2
        if self.position == "top":
            y = mon["y"] + MARGIN_EDGE
        elif self.position == "center":
            y = mon["y"] + (mon["h"] - self.height) // 2
        else:
            y = mon["y"] + mon["h"] - self.height - MARGIN_EDGE
        return f"{WIDTH}x{self.height}+{x}+{y}"

    def _fade_to(self, target, on_done=None):
        if self._fade_job:
            self.root.after_cancel(self._fade_job)
            self._fade_job = None
        steps = max(1, FADE_MS // FADE_STEP_MS)
        start = self._alpha

        def step(n=0):
            # ease-in-out, so the strip appears and leaves without a snap
            p = 1.0 if n >= steps else (n / steps)
            p = p * p * (3 - 2 * p)                    # smoothstep
            self._alpha = start + (target - start) * p
            self.root.attributes("-alpha", max(0.0, min(MAX_ALPHA, self._alpha)))
            if n < steps:
                self._fade_job = self.root.after(FADE_STEP_MS, step, n + 1)
            else:
                self._fade_job = None
                if on_done:
                    on_done()

        step()

    def _reset_sentence_state(self):
        self.last_render = None   # last /now fields painted
        self._tele_full = None    # forget the wrapped passage
        self._scroll_pos = self._scroll_target = 0.0
        self._vel = 0.0
        self._target_time = None

    def _drag_start(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def render(self, d):
        """`d` is a /now payload. The two layouts read different fields:
        rows wants the three sentences, teleprompter wants the whole
        passage plus where the sentence sits inside it."""
        if self.layout == "teleprompter":
            self._render_teleprompter(d.get("full", ""),
                                      d.get("span") or [0, 0])
        else:
            self._render_rows(d.get("prev", ""),
                              d.get("sentence", d.get("text", "")),
                              d.get("next", ""))

    def _tele_wrap(self, full):
        """Wrap the passage once and lay it into the widget, padded so the
        first line can still sit on the anchor row and the last can scroll
        up past it. Cached: this only redoes itself as more of the passage
        arrives, never on every poll."""
        if full == self._tele_full:
            return self._tele_lines, self._tele_offsets
        lines, offsets, cursor = [], [], 0
        for para in full.split("\n"):
            for line in (_wrap(para, self.font, self.text_px) or [""]):
                i = full.find(line, cursor)
                if i < 0:
                    i = cursor
                lines.append(line)
                offsets.append(i)
                cursor = i + len(line)
        if not lines:
            lines, offsets = [""], [0]
        self._tele_full, self._tele_lines, self._tele_offsets = \
            full, lines, offsets

        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", "\n".join([""] * TELE_ANCHOR + lines + [""] * TELE_ROWS))
        t.configure(state="disabled")
        self._line_h = None
        return lines, offsets

    def _tele_line_h(self):
        """Measured, not computed from font metrics: tag spacing and the
        widget's own padding both feed into it."""
        if not self._line_h:
            self.root.update_idletasks()
            a = self.text.dlineinfo("1.0")
            b = self.text.dlineinfo("2.0")
            self._line_h = (b[1] - a[1]) if (a and b) else \
                self.font.metrics("linespace")
        return self._line_h

    def _place_scroll(self, pos):
        """Put content line `pos` (fractional) at the top of the widget."""
        self._scroll_pos = pos
        h = self._tele_line_h()
        self.text.yview_moveto(0.0)
        self.text.yview_scroll(int(round(max(0.0, pos) * h)), "pixels")

    def _line_pos(self, cursor):
        """Char offset in the passage -> fractional wrapped-line position.

        The fraction is what makes the motion continuous: as the voice
        crosses a line the view creeps that same fraction downward, so the
        text is always moving rather than holding still between lines."""
        lines, offsets = self._tele_lines, self._tele_offsets
        if not lines:
            return 0.0
        n = max((k for k, o in enumerate(offsets) if o <= cursor), default=0)
        width = len(lines[n]) or 1
        return n + min(1.0, max(0.0, (cursor - offsets[n]) / width))

    def _set_reading_point(self, line):
        """A fresh reading position from the server: update the pace
        estimate, which is what the frames in between are drawn from."""
        now = time.monotonic()
        if self._target_time is not None:
            dt = now - self._target_time
            if 0.02 < dt < 1.0:
                v = (line - self._scroll_target) / dt
                if v < 0 or v > VEL_MAX:
                    self._vel = 0.0          # jumped: no useful pace here
                else:
                    self._vel = VEL_SMOOTH * self._vel + (1 - VEL_SMOOTH) * v
        self._scroll_target = line
        self._target_time = now

    def _tick_scroll(self):
        """Move the view every frame, for `continuous`.

        Not a chase toward the last poll: that target goes stale between
        polls, so the motion decelerated into it and stalled ~12x a second,
        which is what read as rigid. Instead the reading point is
        EXTRAPOLATED at the smoothed pace, so it keeps advancing on frames
        where no poll has landed, and the view follows that. The heavy
        velocity smoothing is what absorbs the unevenness of the underlying
        word timings."""
        if (self.visible and self.layout == "teleprompter"
                and self.scroll == "continuous" and self._target_time):
            ahead = min(PREDICT_CAP, max(0.0, time.monotonic() - self._target_time))
            predicted = self._scroll_target + self._vel * ahead
            gap = predicted - self._scroll_pos
            if abs(gap) > JUMP_LINES:
                self._vel = 0.0
                self._place_scroll(predicted)
            elif abs(gap) > 0.0005:
                self._place_scroll(self._scroll_pos + gap * FOLLOW_GAIN)
        self.root.after(FADE_STEP_MS, self._tick_scroll)

    def _scroll_to(self, line):
        """Glide the reading position to `line`, a line at a time."""
        if self._scroll_job:
            self.root.after_cancel(self._scroll_job)
            self._scroll_job = None
        start, target = self._scroll_pos, float(line)
        if not self.smooth or abs(target - start) < 0.02:
            self._place_scroll(target)
            return
        steps = max(1, SCROLL_MS // FADE_STEP_MS)

        def step(n=1):
            p = 1 - (1 - n / steps) ** 3          # ease-out cubic
            self._place_scroll(start + (target - start) * p)
            if n < steps:
                self._scroll_job = self.root.after(FADE_STEP_MS, step, n + 1)
            else:
                self._place_scroll(target)
                self._scroll_job = None

        step()

    def _render_teleprompter(self, full, span):
        """The whole passage, scrolling upward past a fixed reading line.

        The first draft of this only had one sentence of context either
        side, which made it look almost identical to the rows layout. A
        teleprompter's defining behaviour is that the TEXT MOVES: the
        entire passage goes into the widget and the WIDGET is scrolled, so
        the motion can be animated between reading positions rather than
        the content being re-sliced each time."""
        start, end = span
        lines, offsets = self._tele_wrap(full)

        # first and last wrapped line the current sentence touches
        first = max((n for n, o in enumerate(offsets) if o <= start), default=0)
        last = max((n for n, o in enumerate(offsets) if o < max(end, start + 1)),
                   default=first)
        # no caret marker here: the reading row is fixed, so it would only
        # ever sit in the same place, and drawing it meant inserting a
        # character that shifted the line it was on

        t = self.text
        t.configure(state="normal")
        for tag in ("done", "upcoming", "now", "caret"):
            t.tag_remove(tag, "1.0", "end")
        for n, (txt, off) in enumerate(zip(lines, offsets)):
            if not txt:
                continue
            row = n + TELE_ANCHOR + 1                  # padded line number
            # whole row as context first; `now` is configured last so it
            # has the higher tag priority and paints over this
            t.tag_add("done" if off < start else "upcoming",
                      f"{row}.0", f"{row}.end")
            # highlight only the characters of the sentence itself, so a
            # line shared with the neighbouring sentence is not swept up
            a, b = max(start, off), min(end, off + len(txt))
            if a < b:
                t.tag_add("now", f"{row}.{a - off}", f"{row}.{b - off}")
        t.configure(state="disabled")
        # continuous mode is driven by the cursor in _tick_scroll instead,
        # so that the view keeps moving *within* a sentence too
        if self.scroll != "continuous":
            self._scroll_to(first)

    def _render_rows(self, prev_text, now_text, next_text):
        """Paint the three regions as three fixed ROWS, not one flowed
        paragraph.

        Flowing them together meant the highlighted sentence began
        wherever the previous one happened to end, so it landed at a
        different place on every advance and the reader had to find it
        again each time. Here the context rows are clamped to exactly one
        line each, so the current sentence always starts at the same
        point and only its own length varies."""
        t = self.text
        caret = self.theme.get("caret") and now_text
        prev_line = _clip_line(prev_text, self.font, self.text_px, keep="tail")
        next_line = _clip_line(next_text, self.font, self.text_px, keep="head")
        now_line = ("▌ " + now_text) if caret else now_text

        t.configure(state="normal")
        t.delete("1.0", "end")
        # the rows stay present even when empty: a collapsed row would
        # move the sentence, which is the whole thing being fixed here
        t.insert("1.0", f"{prev_line}\n{now_line}\n{next_line}")
        t.tag_add("done", "1.0", "1.end")
        t.tag_add("now", "2.2" if caret else "2.0", "2.end")
        if caret:
            t.tag_add("caret", "2.0", "2.1")
        t.tag_add("upcoming", "3.0", "3.end")
        t.configure(state="disabled")

    def poll(self):
        try:
            with urlopen(URL, timeout=0.15) as r:
                d = json.load(r)
        except Exception:
            d = {}
        if d.get("active"):
            self.miss = 0
            # the server groups chunks into sentences (/now `sentence`);
            # merging them here as well is what used to paint the opening
            # of a multi-chunk sentence twice. `full` is in the key so the
            # teleprompter repaints as more of the passage is synthesized.
            now = (d.get("prev", ""), d.get("sentence", d.get("text", "")),
                   d.get("next", ""), d.get("full", ""),
                   tuple(d.get("span") or ()))
            if now != self.last_render:
                self.last_render = now
                self.render(d)
            # every poll, not just on a repaint: the reading position moves
            # continuously through a sentence, which is the whole point
            if self.layout == "teleprompter" and self._tele_lines:
                cur = d.get("cursor")
                if cur is not None:
                    self._set_reading_point(self._line_pos(cur))
            if not self.visible:
                self.root.deiconify()
                self.visible = True
                self._fade_to(MAX_ALPHA)
        elif self.visible:
            self.miss += 1
            if self.miss * POLL_MS >= HIDE_AFTER_MS:
                self.visible = False
                self._reset_sentence_state()

                def _hidden():
                    self.root.withdraw()

                self._fade_to(0.0, on_done=_hidden)
        self.root.after(POLL_MS if self.visible else IDLE_MS, self.poll)


if __name__ == "__main__":
    Overlay().root.mainloop()
