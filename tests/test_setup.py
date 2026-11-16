r"""Tests for Tk being optional on Omarchy (AUDIT 2026-11-16).

    python3 -B tests/test_setup.py

The system Python, stdlib only: kokoro_setup and kokoroctl run there, before
any venv exists. Nothing is installed, started or written: every probe of
the system (packages, systemd, the server) is replaced.

Stock Omarchy lacks one package Kokoro uses, `tk`, and only the settings
window and the caption strip need it. So on Omarchy setup must go through
without Tk, skip the launcher entry (it opens the Tk window), and say what
to install; Captions and More settings must refuse with that command
instead of starting a Tk process that dies. Everywhere else Tk stays
required, because the Tk window is the only settings window there.
"""

import contextlib
import importlib.util
import io
import os
import sys
import unittest
from importlib.machinery import SourceFileLoader
from unittest import mock

sys.dont_write_bytecode = True     # on Linux nothing may be written next to the code
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "linux"))
sys.path.insert(0, ROOT)

import kokoro_setup                # noqa: E402

_loader = SourceFileLoader("kokoroctl", os.path.join(ROOT, "linux", "kokoroctl"))
_spec = importlib.util.spec_from_loader("kokoroctl", _loader)
kokoroctl = importlib.util.module_from_spec(_spec)
_loader.exec_module(kokoroctl)

ARCH = mock.patch.object(kokoro_setup, "_os_release", return_value={"arch"})


def system(tk, on_omarchy, portaudio=True):
    """A machine where every other package check passes."""
    return contextlib.ExitStack(), [
        mock.patch.object(kokoro_setup, "has_tk", return_value=tk),
        mock.patch.object(kokoro_setup, "omarchy", return_value=on_omarchy),
        mock.patch.object(kokoro_setup, "_ok", return_value=True),
        mock.patch.object(kokoro_setup.shutil, "which", side_effect=lambda n: "/usr/bin/" + n),
        mock.patch.object(kokoro_setup.ctypes.util, "find_library",
                          side_effect=lambda n: "libportaudio.so.2" if portaudio else None),
        mock.patch.object(kokoro_setup.platform, "machine", return_value="x86_64"),
        mock.patch.object(kokoro_setup, "VENV_PYTHON", ROOT),   # exists: no disk check
        mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "Hyprland"}),
        ARCH,
    ]


def check(**kw):
    stack, patches = system(**kw)
    with stack:
        for p in patches:
            stack.enter_context(p)
        kokoro_setup.check_system("cpu")


class CheckSystemTests(unittest.TestCase):
    def test_omarchy_without_tk_goes_through(self):
        check(tk=False, on_omarchy=True)

    def test_elsewhere_tk_is_still_required(self):
        with self.assertRaises(kokoro_setup.SetupError) as e:
            check(tk=False, on_omarchy=False)
        self.assertIn("Tk for the settings panel", str(e.exception))
        self.assertIn("sudo pacman -S --needed tk", str(e.exception))

    def test_omarchy_still_requires_the_rest(self):
        with self.assertRaises(kokoro_setup.SetupError) as e:
            check(tk=False, on_omarchy=True, portaudio=False)
        self.assertIn("sudo pacman -S --needed portaudio --", str(e.exception))
        self.assertNotIn(" tk", str(e.exception))

    def test_everything_present(self):
        check(tk=True, on_omarchy=False)
        check(tk=True, on_omarchy=True)

    def test_fedora_command(self):
        with mock.patch.object(kokoro_setup, "_os_release", return_value={"fedora"}):
            self.assertEqual(kokoro_setup.install_command([kokoro_setup.TK_PACKAGE]),
                             "sudo dnf install python3-tkinter")


class Progress:
    def __init__(self):
        self.lines = []

    def update(self, detail=None, fraction=None):
        self.lines.append(detail)


def install_services(tk):
    """Step 5 with the scripts and the server replaced. Returns (message,
    scripts run, progress lines)."""
    scripts, progress = [], Progress()
    not_running = mock.Mock(returncode=1)
    with mock.patch.object(kokoro_setup, "has_tk", return_value=tk), \
            mock.patch.object(kokoro_setup, "_script", side_effect=scripts.append), \
            mock.patch.object(kokoro_setup, "_server_up", return_value=True), \
            mock.patch.object(kokoro_setup, "install_shortcut"), \
            mock.patch.object(kokoro_setup.subprocess, "run", return_value=not_running), \
            mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "test"}), ARCH:
        msg = kokoro_setup.install_services(progress)
    return msg, scripts, progress.lines


class InstallServicesTests(unittest.TestCase):
    def test_without_tk_no_launcher_and_says_what_to_install(self):
        msg, scripts, lines = install_services(tk=False)
        self.assertNotIn("install-desktop.sh", scripts)
        self.assertEqual(scripts, ["install-systemd.sh", "install-hyprland.sh"])
        self.assertIn("launcher entry: skipped, the settings window needs Tk", lines)
        self.assertEqual(msg, "Ready. Select text and press Ctrl+Alt+R. Captions and "
                              "More settings also need Tk: sudo pacman -S --needed tk")

    def test_with_tk_unchanged(self):
        msg, scripts, _ = install_services(tk=True)
        self.assertEqual(scripts, ["install-systemd.sh", "install-desktop.sh",
                                   "install-hyprland.sh"])
        self.assertEqual(msg, "Ready. Select text and press Ctrl+Alt+R.")


def ctl(*args, tk):
    """kokoroctl main() with nothing started. Returns (exit status, stdout,
    stderr, spawned argv lists, commands run)."""
    spawned, ran = [], []
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(kokoro_setup, "has_tk", return_value=tk), \
            mock.patch.object(kokoroctl.paths, "prepare"), \
            mock.patch.object(kokoroctl, "pids", return_value=[]), \
            mock.patch.object(kokoroctl, "managed", return_value=False), \
            mock.patch.object(kokoroctl, "ping_widget"), \
            mock.patch.object(kokoroctl, "spawn", side_effect=lambda a, *r, **k: spawned.append(a)), \
            mock.patch.object(kokoroctl, "run", side_effect=lambda a, **k: ran.append(a) or (0, "")), \
            mock.patch.object(kokoroctl.shutil, "which",
                              side_effect=lambda n: "/usr/bin/notify-send" if n == "notify-send" else None), \
            mock.patch.object(kokoroctl, "settings_json", return_value={"voice": "af_heart"}), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), ARCH:
        rc = kokoroctl.main(["kokoroctl", *args])
    return rc, out.getvalue(), err.getvalue(), spawned, ran


class KokoroctlTests(unittest.TestCase):
    def test_captions_without_tk_refuses(self):
        rc, _out, err, spawned, ran = ctl("captions", "on", tk=False)
        self.assertEqual(rc, 1)
        self.assertEqual(err.strip(), "The caption strip needs Tk, which is not installed: "
                                      "sudo pacman -S --needed tk")
        self.assertEqual(spawned, [])
        self.assertEqual(ran, [])          # the widget shows stderr: no notification

    def test_settings_without_tk_refuses_and_notifies(self):
        rc, _out, err, spawned, ran = ctl("settings", tk=False)
        self.assertEqual(rc, 1)
        self.assertIn("The settings window needs Tk", err)
        self.assertEqual(spawned, [])
        self.assertEqual(len(ran), 1)
        self.assertEqual(ran[0][0], "notify-send")
        self.assertIn("sudo pacman -S --needed tk", ran[0][-1])

    def test_captions_off_needs_no_tk(self):
        rc, out, _err, _s, _r = ctl("captions", "off", tk=False)
        self.assertEqual((rc, out.strip()), (0, "captions off"))

    def test_with_tk_they_start(self):
        rc, _o, _e, spawned, _r = ctl("captions", "on", tk=True)
        self.assertEqual(rc, 0)
        self.assertTrue(spawned[0][-1].endswith("overlay.py"))
        rc, _o, _e, spawned, _r = ctl("settings", tk=True)
        self.assertEqual(rc, 0)
        self.assertEqual(spawned[0][1:], [os.path.join(ROOT, "tray.py"), "--settings"])

    def test_settings_json_unchanged(self):
        rc, out, _e, spawned, _r = ctl("settings", "--json", tk=False)
        self.assertEqual((rc, out.strip(), spawned), (0, '{"voice":"af_heart"}', []))


if __name__ == "__main__":
    unittest.main()
