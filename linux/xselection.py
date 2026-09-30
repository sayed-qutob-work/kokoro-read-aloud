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

Usage:  xselection.py [primary|clipboard]   -> raw UTF-8 on stdout
Exit:   0 = text written   1 = selection empty/unowned/not text
        2 = X unavailable (no DISPLAY, no Xlib) -- caller falls back to wl-paste
"""
import os
import select
import sys
import time

TIMEOUT = 1.0  # seconds for the owner to answer; mutter answers in ~ms


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
        while not d.pending_events():
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([d], [], [], left)[0]:
                return None
        return d.next_event()

    while True:
        ev = next_event()
        if ev is None:
            return 1
        if ev.type == X.SelectionNotify:
            break
    if ev.property == X.NONE:  # owner refused: no text form
        return 1

    r = win.get_full_property(prop, X.AnyPropertyType, sizehint=1 << 20)
    win.delete_property(prop)
    d.flush()
    if r is None:
        return 1
    if r.property_type != incr:
        data = r.value
    else:
        # Large selection: owner streams chunks, one PropertyNotify(NewValue)
        # each; a zero-length chunk ends it. We delete to ask for the next.
        chunks = []
        while True:
            deadline = time.monotonic() + TIMEOUT
            ev = next_event()
            if ev is None:
                return 1
            if ev.type != X.PropertyNotify or ev.atom != prop or ev.state != X.PropertyNewValue:
                continue
            r = win.get_full_property(prop, X.AnyPropertyType, sizehint=1 << 20)
            win.delete_property(prop)
            d.flush()
            if r is None or not r.value:
                break
            chunks.append(r.value)
        data = b"".join(chunks)

    if isinstance(data, str):
        data = data.encode()
    if not data:
        return 1
    sys.stdout.buffer.write(bytes(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
