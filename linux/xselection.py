#!/usr/bin/python3
"""Read PRIMARY or CLIPBOARD through Xwayland, without stealing focus.

Why this exists (AUDIT 2026-09-30): mutter exposes no data-control protocol,
so wl-paste has to map a 1x1 window and be *given* keyboard focus before the
compositor hands it the selection offer. mutter 50.5 stopped granting that
focus (wl-paste presents with timestamp 0 and no activation token), so every
read stalled for seconds, returned the previous selection, and GNOME posted
'"wl-clipboard" is ready'. X11 has no such rule: mutter bridges the Wayland
selections to Xwayland and answers ConvertSelection for anyone, no window
mapped, no focus taken.

Runs under the SYSTEM python (/usr/bin/python3) -- python-xlib ships with
input-remapper there; the venv does not have it.

The owner of a selection can be any X client, so nothing here trusts it
(marketplace review, AUDIT 2026-11-20): the whole read ends within TIMEOUT +
TRANSFER seconds, however the owner paces its answer or floods us with
events, and holds at most MAX_BYTES of it. Text past that is cut, at a
character boundary, and stderr says so (read_aloud.sh logs it).

Usage:  xselection.py [primary|clipboard]   -> raw UTF-8 on stdout
Exit:   0 = text written   1 = selection empty/unowned/not text, or too slow
        2 = X unavailable (no DISPLAY, no Xlib) -- caller falls back to wl-paste
"""
import codecs
import os
import select
import sys
import time

TIMEOUT = 1.0        # seconds for the owner to answer; mutter answers in ~ms
TRANSFER = 2.0       # seconds for a whole INCR transfer, however it is paced
# 1 MiB: 145 times the largest read in read_aloud.log (7,234 characters,
# 2026-11-20). A selection past it is cut, not refused.
MAX_BYTES = 1 << 20


def utf8_prefix(data, limit):
    """At most `limit` bytes of `data`, never ending inside a UTF-8 sequence.
    Bytes that are not UTF-8 pass through unchanged (surrogateescape)."""
    text = codecs.getincrementaldecoder("utf-8")("surrogateescape").decode(
        bytes(data[:limit]), final=False)      # holds back a half sequence
    return text.encode("utf-8", "surrogateescape")


def as_bytes(value):
    return value.encode() if isinstance(value, str) else bytes(value)


def main():
    which = (sys.argv[1] if len(sys.argv) > 1 else "primary").upper()
    if which not in ("PRIMARY", "CLIPBOARD") or not os.environ.get("DISPLAY"):
        return 2
    try:
        from Xlib import X, display, error
    except ImportError:
        return 2
    try:
        d = display.Display()
    except Exception:
        return 2

    atom = lambda name: d.intern_atom(name)
    sel, utf8, incr = atom(which), atom("UTF8_STRING"), atom("INCR")
    prop = atom("KOKORO_SEL")
    if d.get_selection_owner(sel) == X.NONE:
        return 1

    # Never mapped: an unmapped window is enough to receive SelectionNotify.
    win = d.screen().root.create_window(0, 0, 1, 1, 0, X.CopyFromParent,
                                        event_mask=X.PropertyChangeMask)
    win.convert_selection(sel, utf8, prop, X.CurrentTime)
    d.flush()

    deadline = time.monotonic() + TIMEOUT

    def next_event():
        # The deadline holds while events keep coming too: any client may
        # change a property on our window, and each change is an event.
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            if d.pending_events():
                return d.next_event()
            if not select.select([d], [], [], left)[0]:
                return None

    def take(limit):
        """The property, at most `limit` bytes of it (get_property counts in
        4-byte units), then delete it: for INCR that asks for the next chunk."""
        r = win.get_property(prop, X.AnyPropertyType, 0, limit // 4 + 1)
        win.delete_property(prop)
        d.flush()
        return r

    while True:
        ev = next_event()
        if ev is None:
            return 1
        if ev.type == X.SelectionNotify:
            break
    if ev.property == X.NONE:  # owner refused: no text form
        return 1

    r = take(MAX_BYTES)
    if r is None:
        return 1
    if r.property_type != incr:
        data = as_bytes(r.value)
        cut = r.bytes_after > 0 or len(data) > MAX_BYTES
    else:
        # Large selection: owner streams chunks, one PropertyNotify(NewValue)
        # each; a zero-length chunk ends it. We delete to ask for the next.
        # One deadline and one byte budget for the whole transfer: it used to
        # be one second per chunk, with every chunk kept, so an owner that
        # never ended it held the hotkey and grew this process for as long as
        # it liked.
        deadline = time.monotonic() + TRANSFER
        data, cut = bytearray(), False
        while True:
            ev = next_event()
            if ev is None:
                return 1
            if ev.type != X.PropertyNotify or ev.atom != prop or ev.state != X.PropertyNewValue:
                continue
            r = take(MAX_BYTES - len(data))
            chunk = as_bytes(r.value) if r is not None else b""
            if not chunk:
                break
            data += chunk
            if len(data) >= MAX_BYTES:   # stopped before the end marker
                cut = True
                break    # the owner is left waiting for a delete that never comes

    if not data:
        return 1
    if cut:
        data = utf8_prefix(data, MAX_BYTES)
        print(f"xselection: cut at {len(data)} bytes", file=sys.stderr)
    sys.stdout.buffer.write(bytes(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
