"""Where Kokoro read-aloud keeps the files it writes while it runs.

Windows: next to the code, as always -- start_tts.vbs, the tray menu's
"open folder" and the Windows README all point there.

Linux: the XDG base directories, never the code folder.

    ~/.config/kokoro-read-aloud/       settings.json, the user's tuning
    ~/.local/state/kokoro-read-aloud/  logs and calibration.json

On Omarchy the code folder is about to become the plugin folder
(~/.config/omarchy/plugins/<id>), and the shell watches that folder: any
file written inside it makes the shell unload and reload every plugin
(AUDIT 2026-10-17). So nothing may write there, which is also why every
entry point sets sys.dont_write_bytecode before importing this module:
a __pycache__ would be a write too.

read_aloud.sh is bash and cannot import this; it repeats the state path.

Stdlib only: the system python3 imports it (kokoroctl, the settings
panel, the caption strip) as well as the server's venv.
"""

import os
import shutil
import sys

APP = "kokoro-read-aloud"
ROOT = os.path.dirname(os.path.abspath(__file__))
IS_WIN = sys.platform == "win32"


def _xdg(var, default):
    return os.path.join(os.environ.get(var) or os.path.expanduser(default), APP)


if IS_WIN:
    CONFIG = STATE = ROOT
else:
    CONFIG = _xdg("XDG_CONFIG_HOME", "~/.config")
    STATE = _xdg("XDG_STATE_HOME", "~/.local/state")

SETTINGS = os.path.join(CONFIG, "settings.json")       # written by tray.py
CALIBRATION = os.path.join(STATE, "calibration.json")  # written by the server
SERVER_LOG = os.path.join(STATE, "server.log")
OVERLAY_LOG = os.path.join(STATE, "overlay.log")
OVERLAY_ERR = os.path.join(STATE, "overlay.err")
TRAY_LOG = os.path.join(STATE, "tray.log")


def prepare():
    """Create the folders, and move the files an older version kept next
    to the code to where they live now. Cheap and idempotent, so every
    entry point calls it before touching any of the paths above."""
    if IS_WIN:
        return
    os.makedirs(CONFIG, exist_ok=True)
    os.makedirs(STATE, exist_ok=True)
    for new in (SETTINGS, CALIBRATION):
        old = os.path.join(ROOT, os.path.basename(new))
        if os.path.exists(old) and not os.path.exists(new):
            try:
                shutil.move(old, new)
            except OSError:
                pass        # another process at login moved it first
