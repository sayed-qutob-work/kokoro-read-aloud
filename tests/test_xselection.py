r"""Tests for the limits on linux/xselection.py (marketplace review, AUDIT 2026-11-20).

    python3 -B tests/test_xselection.py

Stdlib only, no X server: a fake Xlib plays the selection owner, so a
hostile owner can be scripted exactly, and a fake clock means nothing really
waits. The reviewer's finding was that the INCR loop gave the owner one
second per chunk, kept every chunk, and had no total limit, so an owner that
never ended the transfer held the hotkey and grew the helper.

XSELECTION=<path> runs the same tests against another copy (the code before
the fix: the unbounded cases then raise Unbounded instead of hanging).
"""

import contextlib
import importlib.util
import io
import os
import sys
import types
import unittest

sys.dont_write_bytecode = True     # on Linux nothing may be written next to the code
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.environ.get("XSELECTION") or os.path.join(ROOT, "linux", "xselection.py")

_spec = importlib.util.spec_from_file_location("xselection", PATH)
xsel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(xsel)

# The real values; the tests are written against them.
MAX = 1 << 20
TIMEOUT, TRANSFER = 1.0, 2.0

X = types.SimpleNamespace(
    NONE=0, CurrentTime=0, CopyFromParent=0, PropertyChangeMask=1 << 22,
    SelectionNotify=31, PropertyNotify=28, PropertyNewValue=0, PropertyDelete=1,
    AnyPropertyType=0)


class Unbounded(Exception):
    """The code under test would have gone on for ever (or grown for ever)."""


class Event(types.SimpleNamespace):
    pass


class Display:
    """Just enough of python-xlib's Display, driven by a fake clock."""

    def __init__(self, owner):
        self.now = 0.0
        self.queue = []          # (due, seq, event)
        self.seq = 0
        self.flood = None        # atom: an unrelated PropertyNotify is always pending
        self.served = 0
        self.fetched = 0         # bytes the client was handed by get_property
        self.atoms = {}
        self.owner = owner
        if owner is not None:
            owner.d = self

    def intern_atom(self, name):
        return self.atoms.setdefault(name, 100 + len(self.atoms))

    def get_selection_owner(self, sel):
        return X.NONE if self.owner is None else 1

    def screen(self):
        return types.SimpleNamespace(root=types.SimpleNamespace(
            create_window=lambda *a, **k: Window(self)))

    def flush(self):
        pass

    def send(self, delay, event):
        self.seq += 1
        self.queue.append((self.now + delay, self.seq, event))
        self.queue.sort(key=lambda e: e[:2])

    def _guard(self):
        self.served += 1
        if self.served > 200_000 or self.now > 600:
            raise Unbounded(f"still reading after {self.served} events, {self.now:.0f}s")

    def pending_events(self):
        return int(self.flood is not None) + sum(1 for t, _, _ in self.queue if t <= self.now)

    def next_event(self):
        self._guard()
        if self.queue and self.queue[0][0] <= self.now:
            return self.queue.pop(0)[2]
        if self.flood is not None:
            self.now += 0.001    # an event costs a millisecond to handle
            return Event(type=X.PropertyNotify, atom=self.flood, state=X.PropertyNewValue)
        raise AssertionError("next_event with nothing pending")

    def wait(self, timeout):
        """select(): does an event arrive within `timeout`? Moves the clock."""
        self._guard()
        if self.pending_events():
            return True
        if self.queue and self.queue[0][0] <= self.now + timeout:
            self.now = self.queue[0][0]
            return True
        self.now += timeout
        return False


class Window:
    def __init__(self, d):
        self.d = d
        self.props = {}

    def convert_selection(self, sel, target, prop, time):
        self.d.owner.on_convert(self, prop)

    def get_property(self, prop, ptype, offset, length):
        if prop not in self.props:
            return None
        kind, value = self.props[prop]
        v = value[offset * 4:offset * 4 + length * 4]
        self.d.fetched += len(v)
        if self.d.fetched > 64 * MAX:
            raise Unbounded(f"fetched {self.d.fetched} bytes")
        return types.SimpleNamespace(property_type=kind, format=8, value=v,
                                     bytes_after=len(value) - offset * 4 - len(v))

    def delete_property(self, prop):
        if self.props.pop(prop, None) is not None:
            self.d.owner.on_delete(self, prop)

    # The two python-xlib calls the old code used, for XSELECTION=<old copy>.
    def get_full_property(self, prop, ptype, sizehint=10):
        r = self.get_property(prop, ptype, 0, sizehint)
        if r is not None and r.bytes_after:
            more = self.get_property(prop, ptype, sizehint, r.bytes_after // 4 + 1)
            r.value += more.value
        return r


class Owner:
    d = None

    def on_convert(self, win, prop):
        pass                                             # never answers

    def on_delete(self, win, prop):
        pass


class Plain(Owner):
    def __init__(self, data):
        self.data = data

    def on_convert(self, win, prop):
        win.props[prop] = (self.d.intern_atom("UTF8_STRING"), self.data)
        self.d.send(0.001, Event(type=X.SelectionNotify, property=prop))


class Refuses(Owner):
    def on_convert(self, win, prop):
        self.d.send(0.001, Event(type=X.SelectionNotify, property=X.NONE))


class Incr(Owner):
    """Streams `chunks` (any iterable, endless is fine), one per delete,
    each `delay` seconds after the delete; then the zero-length end marker."""

    def __init__(self, chunks, delay=0.001, flood=False):
        self.chunks, self.delay, self.flood, self.done = iter(chunks), delay, flood, False

    def on_convert(self, win, prop):
        win.props[prop] = (self.d.intern_atom("INCR"), b"\0\0\0\0")
        self.d.send(0.001, Event(type=X.SelectionNotify, property=prop))
        if self.flood:
            self.d.flood = self.d.intern_atom("SOMETHING_ELSE")

    def on_delete(self, win, prop):
        if self.done or self.flood:
            return
        chunk = next(self.chunks, b"")
        self.done = not chunk
        win.props[prop] = (self.d.intern_atom("UTF8_STRING"), chunk)
        self.d.send(self.delay, Event(type=X.PropertyNotify, atom=prop, state=X.PropertyNewValue))


def endless(chunk):
    while True:
        yield chunk


def run(owner):
    """main() against `owner` -> (exit code, stdout bytes, stderr, display)."""
    d = Display(owner)
    fake = types.ModuleType("Xlib")
    fake.X = X
    fake.display = types.SimpleNamespace(Display=lambda: d)
    fake.error = types.SimpleNamespace()
    out, err = io.TextIOWrapper(io.BytesIO()), io.StringIO()
    saved = {k: sys.modules.get(k) for k in ("Xlib",)}
    old = xsel.time, xsel.select, sys.argv, os.environ.get("DISPLAY")
    sys.modules["Xlib"] = fake
    xsel.time = types.SimpleNamespace(monotonic=lambda: d.now)
    xsel.select = types.SimpleNamespace(
        select=lambda r, w, x, timeout: (r, [], []) if d.wait(timeout) else ([], [], []))
    sys.argv, os.environ["DISPLAY"] = ["xselection.py", "primary"], ":99"
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = xsel.main()
        out.flush()
        return rc, out.buffer.getvalue(), err.getvalue(), d
    finally:
        xsel.time, xsel.select, sys.argv = old[:3]
        if old[3] is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = old[3]
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class OrdinaryReads(unittest.TestCase):
    """What every read does: unchanged by the limits."""

    def test_small_selection(self):
        rc, out, err, _ = run(Plain("Hello, wörld".encode()))
        self.assertEqual((rc, out, err), (0, "Hello, wörld".encode(), ""))

    def test_incr_transfer(self):
        rc, out, _, _ = run(Incr([b"one ", b"two ", b"three"]))
        self.assertEqual((rc, out), (0, b"one two three"))

    def test_nothing_selected(self):
        self.assertEqual(run(None)[0], 1)

    def test_owner_refuses(self):
        self.assertEqual(run(Refuses())[0], 1)


class HostileOwners(unittest.TestCase):
    """The owner is any X client. Whatever it does, the read ends within
    TIMEOUT + TRANSFER seconds and holds at most MAX bytes."""

    def assertQuick(self, d):
        self.assertLessEqual(d.now, TIMEOUT + TRANSFER + 0.01)

    def test_owner_never_answers(self):
        rc, _, _, d = run(Owner())
        self.assertEqual(rc, 1)
        self.assertAlmostEqual(d.now, TIMEOUT)

    def test_incr_never_ends(self):
        # The finding: chunks for ever, each well within a second.
        rc, out, err, d = run(Incr(endless(b"x" * 65536)))
        self.assertEqual(rc, 0)
        self.assertEqual(out, b"x" * MAX)
        self.assertLessEqual(d.fetched, MAX + 8)       # + the INCR size itself
        self.assertIn(f"cut at {MAX} bytes", err)
        self.assertQuick(d)

    def test_incr_drips(self):
        # Ten bytes every 0.9 s: under one second per chunk, so the old
        # per-chunk deadline never fired.
        rc, _, _, d = run(Incr(endless(b"0123456789"), delay=0.9))
        self.assertEqual(rc, 1)
        self.assertQuick(d)

    def test_event_flood_during_transfer(self):
        # Unrelated PropertyNotify events, always pending: the old loop never
        # looked at the clock while an event was waiting.
        rc, _, _, d = run(Incr([], flood=True))
        self.assertEqual(rc, 1)
        self.assertQuick(d)

    def test_huge_property(self):
        # Not INCR: the whole selection in one property, larger than MAX.
        data = b"a" * (MAX - 1) + "é and the rest".encode() + b"b" * MAX
        rc, out, err, d = run(Plain(data))
        self.assertEqual(rc, 0)
        self.assertEqual(out, b"a" * (MAX - 1))        # é would straddle MAX: left out
        self.assertLessEqual(d.fetched, MAX + 4)
        self.assertIn("cut at", err)


class Utf8Prefix(unittest.TestCase):
    def test_never_ends_inside_a_character(self):
        self.assertEqual(xsel.utf8_prefix("aé".encode(), 2), b"a")
        self.assertEqual(xsel.utf8_prefix("a😀".encode(), 4), b"a")
        self.assertEqual(xsel.utf8_prefix("a😀".encode(), 5), "a😀".encode())

    def test_bytes_that_are_not_utf8_pass_through(self):
        self.assertEqual(xsel.utf8_prefix(b"\xff\xfeab", 4), b"\xff\xfeab")


if __name__ == "__main__":
    unittest.main(verbosity=2)
