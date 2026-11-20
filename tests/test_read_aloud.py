r"""Tests for the limits on read_aloud.sh's Wayland reads (AUDIT 2026-11-20).

    python3 -B tests/test_read_aloud.py

Stdlib only. The real read_aloud.sh runs against stand-ins on PATH:
wl-paste plays the selection owner, and notify-send, systemctl and hyprctl
only record or answer. Nothing reaches a server: KOKORO_SERVER names a port
nobody listens on, so the script refuses to send. DISPLAY is unset, so the
X11 reader steps aside (exit 2) and every read goes through wl-paste, the
path every Omarchy read takes.

An owner can stream for ever or never finish. Before, wl-paste read until
the owner closed the pipe, into a shell variable, and the watcher's drain
waited just as long.

READ_ALOUD=<path> runs the same tests against another copy.
"""

import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True     # on Linux nothing may be written next to the code
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.environ.get("READ_ALOUD") or os.path.join(ROOT, "read_aloud.sh")
LIMIT = 15        # seconds before a run counts as never ending

FAKES = {
    "wl-paste": r"""#!/bin/bash
case $FAKE_WL in
  text)    printf 'Hello there' ;;
  endless) # 64 KiB every 10 ms: an owner that never closes the pipe
           exec /usr/bin/python3 -c '
import sys, time
b = b"x" * 65536
try:
    while True:
        sys.stdout.buffer.write(b); sys.stdout.flush(); time.sleep(0.01)
except BrokenPipeError:
    pass' ;;
  hang)    printf 'partial'; exec sleep 60 ;;
esac
""",
    "notify-send": '#!/bin/bash\nprintf "%s|" "$@" >>"$FAKE_DIR/notes"; echo >>"$FAKE_DIR/notes"\n',
    "systemctl": "#!/bin/bash\nexit 1\n",
    "hyprctl": '#!/bin/bash\necho "Window 55d1c0 -> kitty:"\n',
}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ReadAloud(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        bin_ = os.path.join(self.dir, "bin")
        os.mkdir(bin_)
        for name, body in FAKES.items():
            path = os.path.join(bin_, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, 0o755)
        self.log = os.path.join(self.dir, "read_aloud.log")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE")}
        self.env.update(
            PATH=bin_ + os.pathsep + os.environ["PATH"], FAKE_DIR=self.dir,
            XDG_RUNTIME_DIR=self.dir, KOKORO_READ_LOG=self.log,
            KOKORO_SERVER=f"http://127.0.0.1:{free_port()}",
            KOKORO_SETTLE_TRIES="1", KOKORO_GRAB_SECS="1")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args, stdin=subprocess.DEVNULL, **env):
        """-> seconds taken. Kills the whole group if it never ends."""
        p = subprocess.Popen(["bash", SCRIPT, *args], env={**self.env, **env},
                             stdin=stdin, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        t0 = time.monotonic()
        try:
            p.wait(timeout=LIMIT)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            self.fail(f"still running after {LIMIT}s")
        return time.monotonic() - t0

    def read_line(self):
        with open(self.log) as f:
            return next(line for line in f if " try=0 " in line)

    def test_ordinary_read(self):
        took = self.run_script("selection", FAKE_WL="text")
        line = self.read_line()
        self.assertIn("via=wl rc=0 len=11 ", line)
        self.assertLess(took, 1)

    def test_owner_that_never_stops_is_cut(self):
        took = self.run_script("selection", FAKE_WL="endless")
        line = self.read_line()
        self.assertIn(" len=1048576 ", line)      # GRAB_BYTES of 'x', then cut
        self.assertLess(took, 1)                   # 16 x 64 KiB at 10 ms each

    def test_owner_that_never_finishes_gives_nothing(self):
        took = self.run_script("selection", FAKE_WL="hang")
        line = self.read_line()
        self.assertRegex(line, r"rc=(124|137) len=0 ")
        self.assertIn("no end after 1s", line)
        self.assertLess(took, 3)
        with open(os.path.join(self.dir, "notes")) as f:
            self.assertIn("nothing selected", f.read())

    def test_watcher_drain_ends(self):
        # primary-changed is run by `wl-paste --watch` with the selection on
        # stdin; an owner that never stops must not keep the window unnoted.
        with subprocess.Popen(["bash", "-c", "exec yes"], stdout=subprocess.PIPE) as src:
            try:
                took = self.run_script("primary-changed", stdin=src.stdout)
            finally:
                src.kill()
        with open(os.path.join(self.dir, "kokoro-read-aloud", "primary-owner")) as f:
            self.assertTrue(re.match(r"55d1c0 \d+$", f.read().strip()))
        self.assertLess(took, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
