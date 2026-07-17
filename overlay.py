r"""Karaoke caption overlay for the read-aloud server.

Polls GET /now on tts_server.py and shows the chunk being spoken in a
small always-on-top strip, highlighting the word currently sounding
(Speechify-style, but source-agnostic: works whether the text came from
a browser, a PDF, a terminal or an editor, because it never touches the
source app). Runs as its own process, launched hidden by start_tts.vbs
with pythonw.exe; the server does not know it exists.

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
WIDTH, HEIGHT = 900, 96

BG = "#1b1b22"
FG = "#e8e8ee"        # not yet spoken
FG_DIM = "#84848f"    # already spoken
HL_BG = "#3d5afe"     # current word
HL_FG = "#ffffff"


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
                            font=tkfont.Font(family="Segoe UI", size=13),
                            state="disabled")
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("done", foreground=FG_DIM)
        self.text.tag_configure("now", background=HL_BG, foreground=HL_FG)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{WIDTH}x{HEIGHT}+{(sw - WIDTH) // 2}+{sh - 190}")
        for w in (self.root, self.text):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<Button-3>", lambda e: self.root.destroy())
        self.chunk = None
        self.spans = []
        self.visible = False
        self.miss = 0
        self.root.after(IDLE_MS, self.poll)

    def _drag_start(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def render(self, textstr, words):
        """Lay out the chunk text and remember each timed word's char span
        (words carry only the spoken tokens; punctuation lives in textstr)."""
        self.chunk = textstr
        self.spans = []
        pos = 0
        for w, _a, _b in words:
            i = textstr.find(w, pos)
            self.spans.append((i, i + len(w)) if i >= 0 else None)
            if i >= 0:
                pos = i + len(w)
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", textstr)
        t.configure(state="disabled")

    def mark(self, idx):
        t = self.text
        t.configure(state="normal")
        t.tag_remove("now", "1.0", "end")
        t.tag_remove("done", "1.0", "end")
        if 0 <= idx < len(self.spans) and self.spans[idx]:
            a, b = self.spans[idx]
            t.tag_add("done", "1.0", f"1.0+{a}c")
            t.tag_add("now", f"1.0+{a}c", f"1.0+{b}c")
        t.configure(state="disabled")

    def poll(self):
        try:
            with urlopen(URL, timeout=0.15) as r:
                d = json.load(r)
        except Exception:
            d = {}
        if d.get("active"):
            self.miss = 0
            if d["text"] != self.chunk:
                self.render(d["text"], d["words"])
            self.mark(d.get("word", -1))
            if not self.visible:
                self.root.deiconify()
                self.visible = True
        elif self.visible:
            self.miss += 1
            if self.miss * POLL_MS >= HIDE_AFTER_MS:
                self.root.withdraw()
                self.visible = False
                self.chunk = None
        self.root.after(POLL_MS if self.visible else IDLE_MS, self.poll)


if __name__ == "__main__":
    Overlay().root.mainloop()
