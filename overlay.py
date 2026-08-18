r"""Karaoke caption overlay for the read-aloud server.

Polls GET /now on tts_server.py and shows a few lines of context around
the sentence currently sounding (Speechify-style, but source-agnostic:
works whether the text came from a browser, a PDF, a terminal or an
editor, because it never touches the source app). Runs as its own
process, launched hidden by start_tts.vbs with pythonw.exe on Windows
(read_aloud.sh does not launch it on Linux yet; run it directly); the
server does not know it exists.

Highlights at SENTENCE granularity, not per word (RELEASE_PLAN §3.2):
/now's chunks cut mid-sentence, so consecutive chunks are merged into a
"sentence so far" buffer until one arrives with ends_sentence=True. The
chunk immediately before/after (/now's prev/next, §3.1) is shown as
already-spoken / upcoming context around it, giving ~4 lines total.

Drag to move. Right-click to close. Appears only while speech is
playing; hides itself ~0.7s after playback ends or Ctrl+Alt+S.
"""
import json
import tkinter as tk
import tkinter.font as tkfont
from urllib.request import urlopen

URL = "http://127.0.0.1:5111/now"
POLL_MS = 80          # while visible
IDLE_MS = 500         # while hidden (also covers "server down")
HIDE_AFTER_MS = 700   # inactive time before the strip hides
WIDTH, HEIGHT = 900, 170   # ~4 lines at the font size below

BG = "#1b1b22"
FG = "#e8e8ee"        # upcoming (not yet spoken)
FG_DIM = "#84848f"    # already spoken
HL_BG = "#3d5afe"     # sentence currently sounding
HL_FG = "#ffffff"

# "Segoe UI" (Windows) isn't installed on Linux; Tk silently substitutes
# something for a missing family, but picking a real match keeps the
# look intentional on both platforms instead of leaving it to chance.
_PREFERRED_FONTS = ("Segoe UI", "Cantarell", "Noto Sans", "DejaVu Sans")


def _pick_font(size):
    available = set(tkfont.families())
    for name in _PREFERRED_FONTS:
        if name in available:
            return tkfont.Font(family=name, size=size)
    return tkfont.Font(size=size)  # Tk's platform default


class Overlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.93)
        self.root.configure(bg=BG)
        self.text = tk.Text(self.root, wrap="word", bd=0, padx=14, pady=10,
                            bg=BG, fg=FG, cursor="arrow", highlightthickness=0,
                            font=_pick_font(13), state="disabled")
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("done", foreground=FG_DIM)
        self.text.tag_configure("now", background=HL_BG, foreground=HL_FG)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{WIDTH}x{HEIGHT}+{(sw - WIDTH) // 2}+{sh - HEIGHT - 90}")
        for w in (self.root, self.text):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<Button-3>", lambda e: self.root.destroy())
        self.visible = False
        self.miss = 0
        self._reset_sentence_state()
        self.root.after(IDLE_MS, self.poll)

    def _reset_sentence_state(self):
        self.last_utt = None
        self.last_chunk = None
        self.sentence_buf = ""
        self.prev_ended = True   # the next new chunk starts a fresh sentence

    def _drag_start(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def render(self, prev_text, now_text, next_text):
        """Lay out done/now/upcoming as one block and tag each span.
        Each piece is optional (start or end of an utterance)."""
        parts, spans, cursor = [], {}, 0
        for key, s in (("done", prev_text), ("now", now_text), (None, next_text)):
            if not s:
                continue
            if parts:
                parts.append(" ")
                cursor += 1
            start = cursor
            parts.append(s)
            cursor += len(s)
            if key:
                spans[key] = (start, cursor)
        full = "".join(parts)

        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", full)
        for key, (a, b) in spans.items():
            t.tag_add(key, f"1.0+{a}c", f"1.0+{b}c")
        t.configure(state="disabled")

    def poll(self):
        try:
            with urlopen(URL, timeout=0.15) as r:
                d = json.load(r)
        except Exception:
            d = {}
        if d.get("active"):
            self.miss = 0
            utt = d.get("utt")
            chunk_text = d.get("text", "")
            if utt != self.last_utt:
                self._reset_sentence_state()
                self.last_utt = utt
            if chunk_text != self.last_chunk:
                self.sentence_buf = chunk_text if self.prev_ended else \
                    (self.sentence_buf + " " + chunk_text).strip()
                self.prev_ended = d.get("ends_sentence", False)
                self.last_chunk = chunk_text
                self.render(d.get("prev", ""), self.sentence_buf, d.get("next", ""))
            if not self.visible:
                self.root.deiconify()
                self.visible = True
        elif self.visible:
            self.miss += 1
            if self.miss * POLL_MS >= HIDE_AFTER_MS:
                self.root.withdraw()
                self.visible = False
                self._reset_sentence_state()
        self.root.after(POLL_MS if self.visible else IDLE_MS, self.poll)


if __name__ == "__main__":
    Overlay().root.mainloop()
