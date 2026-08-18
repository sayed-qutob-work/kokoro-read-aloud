"""Kokoro read-aloud -- tray icon + settings panel.

Puts an icon in the notification area (the "Show hidden icons" tray) whose
right-click menu exposes Settings, Stop speaking, restarts, the log folder,
and Quit. Settings is a tkinter panel over the server's live /config, so
every knob applies to the NEXT chunk with no restart and no model reload.

Why this exists
---------------
Until now the speed/rhythm knobs lived only in the tts_server.py config block,
and /config changes were in-memory (AUDIT: "persist by editing the file"). That
was tolerable while the tuned numbers were machine-constants. They are not:
the whole audio pipeline is a race between synthesis throughput and playback
speed, and throughput is a property of the MACHINE. On the old desktop it was
4.03x realtime; on this laptop it measured 1.77x while PLAYBACK_SPEED was 1.8
-- i.e. below break-even, which is why reads stalled for seconds at a time.
So the right reading speed is per-machine and per-person, and it needs to be a
knob the user can turn rather than a constant a past session measured.

The panel therefore shows the machine's measured throughput and the resulting
SUSTAINABLE speed ceiling, and warns when the chosen speed crosses it. That is
the difference between "a settings dialog" and "a settings dialog that stops
you configuring the stall back in".

Two message loops, on purpose
-----------------------------
Win32 messages are per-thread, so the tray icon owns a hidden window and its
own GetMessage loop on a worker thread, while tkinter keeps the main thread.
Menu actions hop back with root.after(0, ...), which is the one tkinter call
that is safe to make from another thread.

Launched hidden by start_tts.vbs, same as the server and highlighter. Killing
it affects nothing else -- the tray is a control surface, not a dependency.
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

import tkinter as tk
from tkinter import ttk

IS_WIN = sys.platform == "win32"

ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(ROOT, "settings.json")
LOG = os.path.join(ROOT, "tray.log")
SERVER = "http://127.0.0.1:5111"
# venvs put the interpreter in different places per platform
PY = os.path.join(ROOT, "env", "Scripts" if IS_WIN else "bin",
                  "python.exe" if IS_WIN else "python")

# Voices, from the tts_server.py config block. lang_code is derived from the
# prefix by the server (af_ -> 'a', bf_ -> 'b'), so this list is all it needs.
VOICES = [
    "af_heart", "af_bella", "af_nicole", "af_aoede", "af_kore", "af_sarah",
    "af_nova", "af_sky", "af_alloy", "af_jessica", "af_river",
    "am_michael", "am_fenrir", "am_puck", "am_echo", "am_eric", "am_liam",
    "am_onyx", "am_adam",
    "bf_emma", "bf_isabella", "bf_alice", "bf_lily",
    "bm_george", "bm_fable", "bm_lewis", "bm_daniel",
]

# AUDIT S3/S6: Kokoro's own speed parameter rescales its duration predictor,
# and past ~1.3 it physically cannot articulate -- garbled onsets and endings.
# The rest of the speed comes from WSOLA time-stretching after synthesis.
MODEL_SPEED_CAP = 1.3

# Fraction of measured throughput we call "sustainable". Synthesis must outrun
# playback or the buffer drains; leaving 15% covers the per-chunk variance
# (measured 1.3x-1.9x RT on this machine depending on chunk size).
SUSTAIN_MARGIN = 0.85

# Shown for output_device = None, i.e. "whatever Windows is using right now".
DEVICE_DEFAULT_LABEL = "Default (follow Windows)"

DEFAULTS = {
    "voice": "af_heart",
    "model_speed": 1.15,
    "playback_speed": 1.8,
    "pause": 0.1,
    "first_chunk_audio": 2.0,
    "output_device": None,      # None = follow the system default
    # caption strip (overlay.py). Read by the strip at startup, not by the
    # server -- /config ignores them, and changing either needs the strip
    # restarted, which the panel does for you.
    "caption_style": "underline",
    "caption_layout": "rows",
    "caption_position": "bottom",
    "caption_monitor": "primary",   # or a connector name, e.g. "DP-1"
    "caption_smooth": "on",         # animate the teleprompter scroll
}

# Taken from overlay.py rather than restated, so the panel can never offer
# a value the strip does not understand.
try:
    import overlay as _overlay
    CAPTION_STYLES = list(_overlay.THEMES)
    CAPTION_LAYOUTS = list(_overlay.LAYOUTS)
    CAPTION_POSITIONS = list(_overlay.POSITIONS)
except Exception:                     # panel still works without the strip
    _overlay = None
    CAPTION_STYLES = ["underline", "terminal", "rail"]
    CAPTION_LAYOUTS = ["rows", "teleprompter"]
    CAPTION_POSITIONS = ["bottom", "center", "top"]

MONITOR_PRIMARY_LABEL = "Primary monitor"
CAPTION_KEYS = ("caption_style", "caption_layout", "caption_position",
                "caption_monitor", "caption_smooth")


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


# ----------------------------------------------------------------- settings

def load_settings():
    """Disk settings merged over DEFAULTS. Unknown keys are dropped so a
    hand-edited file can't inject anything the server won't understand."""
    out = dict(DEFAULTS)
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            d = json.load(f)
        for k in DEFAULTS:
            if k in d:
                out[k] = d[k]
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"settings load failed: {e}")
    return out


def save_settings(d):
    """Persist. This is what makes a tuned speed survive a reboot -- /config
    alone is in-memory, so before this file existed every restart silently
    reverted the user's tuning to whatever was hardcoded."""
    try:
        tmp = SETTINGS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: d[k] for k in DEFAULTS if k in d}, f, indent=2)
        os.replace(tmp, SETTINGS)
        return True
    except Exception as e:
        log(f"settings save failed: {e}")
        return False


# ------------------------------------------------------------------- server

def api(path, payload=None, timeout=2.0):
    """GET, or POST when payload is given. Returns parsed JSON or None --
    the tray must stay alive and usable while the server is down."""
    try:
        url = SERVER + path
        if payload is None:
            req = urllib.request.Request(url)
        else:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
        return json.loads(body) if body.strip() else {}
    except Exception:
        return None


def server_up():
    return api("/config", timeout=0.8) is not None


def spawn_hidden(args, stdout_path=None):
    """Start a detached, window-less child, mirroring start_tts.vbs."""
    try:
        out = open(stdout_path, "w") if stdout_path else subprocess.DEVNULL
        kw = {}
        if IS_WIN:
            kw["creationflags"] = 0x08000000 | 0x00000008   # NO_WINDOW|DETACHED
        else:
            # detach from this process group so quitting the tray (or the
            # terminal that started it) does not take the server with it
            kw["start_new_session"] = True
        subprocess.Popen(args, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, close_fds=True, **kw)
        return True
    except Exception as e:
        log(f"spawn failed {args}: {e}")
        return False


def _pids_windows(name):
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          f"Where-Object {{ $_.CommandLine -like '*{name}*' }} | "
          "ForEach-Object { $_.ProcessId }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=15)
    return r.stdout.split()


def _pids_posix(name):
    """pgrep -f, then keep only real python processes.

    -f matches the whole command line, which also matches any SHELL whose
    command line happens to quote the script name -- killing those takes
    out the terminal that launched us. Checking the executable name is
    what makes this safe."""
    r = subprocess.run(["pgrep", "-f", name], capture_output=True,
                       text=True, timeout=10)
    out = []
    for pid in r.stdout.split():
        try:
            with open(f"/proc/{int(pid)}/comm", encoding="utf-8") as f:
                if f.read().strip().startswith("python"):
                    out.append(pid)
        except (OSError, ValueError):
            continue
    return out


def kill_script(name):
    """Kill python processes whose command line mentions `name`.

    Filters on the COMMAND LINE, never the image name: server, highlighter and
    tray are all python.exe, and each runs as a venv-launcher + child pair."""
    n = 0
    try:
        me = os.getpid()
        for line in (_pids_windows(name) if IS_WIN else _pids_posix(name)):
            try:
                pid = int(line.strip())
            except ValueError:
                continue
            if pid == me:
                continue
            if IS_WIN:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=10)
            else:
                try:
                    os.kill(pid, 15)          # SIGTERM; these all exit cleanly
                except OSError:
                    continue
            n += 1
    except Exception as e:
        log(f"kill {name} failed: {e}")
    return n


def open_folder():
    """Show the install folder in the desktop's file manager."""
    try:
        if IS_WIN:
            os.startfile(ROOT)                       # noqa: S606 - Windows only
        else:
            subprocess.Popen(["xdg-open", ROOT], start_new_session=True)
        return True
    except Exception as e:
        log(f"open folder failed: {e}")
        return False


def restart_server():
    kill_script("tts_server.py")
    time.sleep(1.2)
    ok = spawn_hidden([PY, "tts_server.py"], os.path.join(ROOT, "server.log"))
    log(f"server restart spawned={ok}")
    return ok


def restart_highlighter():
    kill_script("highlighter.py")
    time.sleep(0.6)
    env_dbg = os.path.join(ROOT, "highlighter.log")
    os.environ["KOKORO_HL_DEBUG"] = env_dbg
    ok = spawn_hidden([PY, "highlighter.py"],
                      os.path.join(ROOT, "highlighter.err"))
    log(f"highlighter restart spawned={ok}")
    return ok


def restart_overlay():
    """Restart the caption strip. It reads caption_style/caption_layout once
    at startup, so changing either only takes effect through here."""
    kill_script("overlay.py")
    time.sleep(0.4)
    ok = spawn_hidden([PY, "overlay.py"], os.path.join(ROOT, "overlay.err"))
    log(f"overlay restart spawned={ok}")
    return ok


# --------------------------------------------------------------- tray icon

def make_tray(app):
    """The notification-area icon, where the desktop has one.

    Windows gets the Shell_NotifyIcon implementation in tray_win32.py.
    GNOME removed the legacy tray: an icon there needs the third-party
    AppIndicator shell extension AND PyGObject, and Fedora builds `gi`
    only for the system Python while this venv must be 3.12 (kokoro
    requires <3.13), so the venv cannot import it at all. Rather than
    depend on a source build plus an extension the user must keep
    enabled, Linux has no icon and reaches the panel through the
    desktop entry / `tray.py --settings` instead (RELEASE_PLAN 4.4's
    planned fallback)."""
    if not IS_WIN:
        return None
    try:
        import tray_win32
        return tray_win32.Tray(app)
    except Exception:
        log("tray icon unavailable\n" + traceback.format_exc())
        return None


# ------------------------------------------------------------ settings panel

SAMPLE = ("This is how the reading voice sounds at the speed you just chose. "
          "If it stutters or pauses in the middle, the speed is set higher "
          "than this machine can synthesize.")


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.win = None
        self.cfg = load_settings()
        self.tray = make_tray(self)
        self._push_job = None     # debounce handle for slider drags
        self._rt = None           # last measured throughput, or None
        self._device_map = {DEVICE_DEFAULT_LABEL: None}   # label -> spec
        self._ui_q = queue.Queue()
        self.root.after(60, self._pump_ui)

    def ui(self, fn):
        """Run `fn` on the tk thread, from any thread.

        NOT root.after(): that calls into Tcl from the *calling* thread
        (createcommand), which only works while the mainloop happens to be
        spinning and raises "main thread is not in main loop" otherwise. Every
        HTTP call here runs on a worker, so this is the only safe handoff."""
        self._ui_q.put(fn)

    def _pump_ui(self):
        while True:
            try:
                fn = self._ui_q.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log("ui callback failed\n" + traceback.format_exc())
        self.root.after(60, self._pump_ui)

    # ---- lifecycle
    def start(self, open_panel=False):
        if self.tray is not None:
            threading.Thread(target=self._tray_thread, daemon=True).start()
        elif not open_panel:
            # no icon and no panel would leave nothing on screen at all
            open_panel = True
        self.push(self.cfg, save=False)      # apply persisted settings on boot
        if open_panel:
            self.open_settings()
        self.root.mainloop()

    def _tray_thread(self):
        try:
            self.tray.run()
        except Exception:
            log("tray thread died\n" + traceback.format_exc())

    def quit_all(self):
        def go():
            kill_script("tts_server.py")
            kill_script("highlighter.py")
            kill_script("overlay.py")
            if self.tray is not None:
                self.tray.remove()
            try:
                self.root.quit()
            except Exception:
                pass
            os._exit(0)
        self.ui(go)

    def open_settings(self):
        self.ui(self._open_settings)

    # ---- actions the tray menu calls (see tray_win32.Tray's app contract)
    def log(self, msg):
        log(msg)

    def menu_status(self):
        """(status line, device line) for the top of the tray menu."""
        cfg = api("/config", timeout=0.6)
        if not cfg:
            return "Server: NOT RUNNING", None
        return (f"Server: running  -  {cfg.get('effective_speed', '?')}x, "
                f"{cfg.get('voice', '?')}",
                f"Output: {cfg.get('output_device_name') or '?'}")

    def stop_speaking(self):
        api("/stop", payload={})

    def reconnect_audio(self):
        threading.Thread(target=api, args=("/devices",),
                         kwargs={"payload": {}, "timeout": 15.0},
                         daemon=True).start()

    def restart_server_async(self):
        threading.Thread(target=restart_server, daemon=True).start()

    def restart_highlighter_async(self):
        threading.Thread(target=restart_highlighter, daemon=True).start()

    def restart_overlay_async(self):
        threading.Thread(target=restart_overlay, daemon=True).start()

    def open_folder(self):
        open_folder()

    # ---- server round trips
    def push(self, cfg, save=True):
        """Send to the live server and optionally persist.

        The caption keys are deliberately not sent: the server has no use
        for them and the strip reads them off disk at startup."""
        api("/config", payload={k: cfg[k] for k in DEFAULTS
                                if k in cfg and k not in CAPTION_KEYS})
        if save:
            save_settings(cfg)

    def measured(self):
        """(rt, density, ok) from the server. rt is the number that decides
        whether a chosen speed is physically sustainable on this machine."""
        d = api("/config", timeout=1.0)
        if not d:
            return None, None, False
        return d.get("measured_rt"), d.get("measured_density"), True

    # ---- the panel
    def _open_settings(self):
        if self.win is not None and tk.Toplevel.winfo_exists(self.win):
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            return

        self.cfg = load_settings()
        w = self.win = tk.Toplevel(self.root)
        w.title("Kokoro read-aloud - settings")
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", self._close)

        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(w, padding=14)
        frm.grid(sticky="nsew")

        # Rows are handed out by a counter rather than hardcoded, so inserting
        # a control doesn't mean renumbering every widget below it.
        rows = iter(range(200))

        # --- speed
        r = next(rows)
        ttk.Label(frm, text="Reading speed", font=("Segoe UI", 10, "bold")
                  ).grid(row=r, column=0, sticky="w", **pad)
        self.v_speed = tk.DoubleVar(
            value=round(self.cfg["model_speed"] * self.cfg["playback_speed"], 2))
        self.lbl_speed = ttk.Label(frm, text="")
        self.lbl_speed.grid(row=r, column=2, sticky="e", **pad)
        ttk.Scale(frm, from_=0.8, to=3.0, variable=self.v_speed,
                  command=lambda _=None: self._speed_changed()
                  ).grid(row=next(rows), column=0, columnspan=3,
                         sticky="ew", **pad)

        self.lbl_sustain = ttk.Label(frm, text="", foreground="#555")
        self.lbl_sustain.grid(row=next(rows), column=0, columnspan=3,
                              sticky="w", **pad)
        self.lbl_warn = ttk.Label(frm, text="", foreground="#b00020",
                                  wraplength=430, justify="left")
        self.lbl_warn.grid(row=next(rows), column=0, columnspan=3,
                           sticky="w", **pad)

        ttk.Separator(frm).grid(row=next(rows), column=0, columnspan=3,
                                sticky="ew", pady=10)

        # --- voice
        r = next(rows)
        ttk.Label(frm, text="Voice").grid(row=r, column=0, sticky="w", **pad)
        self.v_voice = tk.StringVar(value=self.cfg["voice"])
        ttk.Combobox(frm, textvariable=self.v_voice, values=VOICES,
                     state="readonly", width=18
                     ).grid(row=r, column=1, columnspan=2, sticky="e", **pad)

        # --- output device
        r = next(rows)
        ttk.Label(frm, text="Play through").grid(row=r, column=0,
                                                 sticky="w", **pad)
        self.v_device = tk.StringVar(value=DEVICE_DEFAULT_LABEL)
        self.cmb_device = ttk.Combobox(frm, textvariable=self.v_device,
                                       values=[DEVICE_DEFAULT_LABEL],
                                       state="readonly", width=34)
        self.cmb_device.grid(row=r, column=1, sticky="e", **pad)
        self.cmb_device.bind("<<ComboboxSelected>>",
                             lambda _e: self._device_changed())
        ttk.Button(frm, text="Rescan", width=7,
                   command=lambda: self._load_devices(refresh=True)
                   ).grid(row=r, column=2, sticky="e", **pad)
        self.lbl_device = ttk.Label(frm, text="", foreground="#555",
                                    wraplength=430, justify="left")
        self.lbl_device.grid(row=next(rows), column=0, columnspan=3,
                             sticky="w", **pad)

        # --- pause
        r = next(rows)
        ttk.Label(frm, text="Pause after sentences").grid(row=r, column=0,
                                                          sticky="w", **pad)
        self.v_pause = tk.DoubleVar(value=self.cfg["pause"])
        self.lbl_pause = ttk.Label(frm, text="")
        self.lbl_pause.grid(row=r, column=2, sticky="e", **pad)
        ttk.Scale(frm, from_=0.0, to=0.6, variable=self.v_pause,
                  command=lambda _=None: self._labels()
                  ).grid(row=next(rows), column=0, columnspan=3,
                         sticky="ew", **pad)

        # --- start buffer
        r = next(rows)
        ttk.Label(frm, text="Buffer before speaking").grid(row=r, column=0,
                                                           sticky="w", **pad)
        self.v_first = tk.DoubleVar(value=self.cfg["first_chunk_audio"])
        self.lbl_first = ttk.Label(frm, text="")
        self.lbl_first.grid(row=r, column=2, sticky="e", **pad)
        ttk.Scale(frm, from_=0.8, to=4.0, variable=self.v_first,
                  command=lambda _=None: self._labels()
                  ).grid(row=next(rows), column=0, columnspan=3,
                         sticky="ew", **pad)
        ttk.Label(frm, text="Larger = slower to start, less likely to stutter.",
                  foreground="#555").grid(row=next(rows), column=0,
                                          columnspan=3, sticky="w", **pad)

        ttk.Separator(frm).grid(row=next(rows), column=0, columnspan=3,
                                sticky="ew", pady=10)

        # --- caption strip. These two are the only settings the SERVER
        # never sees: overlay.py reads them off disk when it starts, so
        # they need it restarted rather than a /config push.
        cap = ttk.LabelFrame(frm, text="Caption strip", padding=10)
        cap.grid(row=next(rows), column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(cap, text="Look").grid(row=0, column=0, sticky="w")
        self.v_cap_style = tk.StringVar(value=self.cfg["caption_style"])
        ttk.Combobox(cap, textvariable=self.v_cap_style, values=CAPTION_STYLES,
                     state="readonly", width=16
                     ).grid(row=0, column=1, sticky="e", pady=2)
        ttk.Label(cap, text="Layout").grid(row=1, column=0, sticky="w")
        self.v_cap_layout = tk.StringVar(value=self.cfg["caption_layout"])
        ttk.Combobox(cap, textvariable=self.v_cap_layout, values=CAPTION_LAYOUTS,
                     state="readonly", width=16
                     ).grid(row=1, column=1, sticky="e", pady=2)

        ttk.Label(cap, text="Show on").grid(row=2, column=0, sticky="w")
        self._monitor_map = {MONITOR_PRIMARY_LABEL: "primary"}
        self.v_cap_monitor = tk.StringVar(value=MONITOR_PRIMARY_LABEL)
        self.cmb_monitor = ttk.Combobox(cap, textvariable=self.v_cap_monitor,
                                        values=[MONITOR_PRIMARY_LABEL],
                                        state="readonly", width=16)
        self.cmb_monitor.grid(row=2, column=1, sticky="e", pady=2)

        ttk.Label(cap, text="Position").grid(row=3, column=0, sticky="w")
        self.v_cap_pos = tk.StringVar(value=self.cfg["caption_position"])
        ttk.Combobox(cap, textvariable=self.v_cap_pos, values=CAPTION_POSITIONS,
                     state="readonly", width=16
                     ).grid(row=3, column=1, sticky="e", pady=2)

        self.v_cap_smooth = tk.StringVar(value=self.cfg["caption_smooth"])
        ttk.Checkbutton(cap, text="Animate scrolling",
                        variable=self.v_cap_smooth, onvalue="on",
                        offvalue="off").grid(row=4, column=0, columnspan=2,
                                             sticky="w", pady=(6, 0))

        ttk.Label(cap, wraplength=400, foreground="#555", justify="left",
                  text=("rows keeps the sentence in a fixed block; "
                        "teleprompter scrolls the whole passage up past a "
                        "fixed reading line. Save applies all of these.")
                  ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        cap.columnconfigure(1, weight=1)
        self._load_monitors()

        # --- advanced
        adv = ttk.LabelFrame(frm, text="Advanced", padding=10)
        adv.grid(row=next(rows), column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(adv, text="Voice speed vs. stretch").grid(row=0, column=0,
                                                            sticky="w")
        self.v_model = tk.DoubleVar(value=self.cfg["model_speed"])
        self.lbl_model = ttk.Label(adv, text="")
        self.lbl_model.grid(row=0, column=2, sticky="e")
        ttk.Scale(adv, from_=1.0, to=MODEL_SPEED_CAP, variable=self.v_model,
                  command=lambda _=None: self._speed_changed()
                  ).grid(row=1, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Label(adv, wraplength=400, foreground="#555", justify="left",
                  text=("How much of the speed comes from the model itself "
                        "rather than stretching the audio afterwards. Higher "
                        "needs less synthesis work (fewer stutters) but the "
                        "voice articulates less cleanly. Capped at 1.3.")
                  ).grid(row=2, column=0, columnspan=3, sticky="w")

        # --- buttons
        btns = ttk.Frame(frm)
        btns.grid(row=next(rows), column=0, columnspan=3, sticky="ew",
                  pady=(14, 0))
        ttk.Button(btns, text="Test", command=self._test).pack(side="left")
        ttk.Button(btns, text="Reset", command=self._reset).pack(side="left",
                                                                 padx=6)
        ttk.Button(btns, text="Close", command=self._close).pack(side="right")
        ttk.Button(btns, text="Save", command=self._save).pack(side="right",
                                                               padx=6)

        self.status = ttk.Label(frm, text="", foreground="#0a7")
        self.status.grid(row=next(rows), column=0, columnspan=3, sticky="w",
                         pady=(8, 0))

        frm.columnconfigure(1, weight=1)
        self._labels()
        self._load_devices()
        self._refresh_sustain()
        w.update_idletasks()
        w.geometry(f"+{w.winfo_screenwidth() // 2 - w.winfo_width() // 2}"
                   f"+{w.winfo_screenheight() // 3}")

    # ---- panel helpers
    def _split(self, effective, model):
        """Split a target effective speed into (model, playback).

        Only playback_speed drives the synthesis race -- the model produces
        FEWER audio seconds at higher model_speed, so leaning on it costs less
        throughput. AUDIT S3 nonetheless keeps model_speed near 1.15 for
        quality, so the user's Advanced choice is honored and playback takes
        the remainder."""
        model = max(1.0, min(MODEL_SPEED_CAP, model))
        return model, max(0.3, effective / model)

    def _labels(self):
        self.lbl_speed.config(text=f"{self.v_speed.get():.2f}x")
        self.lbl_pause.config(text=f"{self.v_pause.get():.2f}s")
        self.lbl_first.config(text=f"{self.v_first.get():.1f}s")
        self.lbl_model.config(text=f"{self.v_model.get():.2f}x")

    def _speed_changed(self):
        self._labels()
        self._check_sustain()
        self._apply_live()

    def _apply_live(self):
        model, playback = self._split(self.v_speed.get(), self.v_model.get())
        cfg = {"voice": self.v_voice.get(), "model_speed": round(model, 3),
               "playback_speed": round(playback, 3),
               "pause": round(self.v_pause.get(), 3),
               "first_chunk_audio": round(self.v_first.get(), 2),
               "output_device": self._device_map.get(self.v_device.get()),
               "caption_style": self.v_cap_style.get(),
               "caption_layout": self.v_cap_layout.get(),
               "caption_position": self.v_cap_pos.get(),
               "caption_monitor": self._monitor_map.get(
                   self.v_cap_monitor.get(), "primary"),
               "caption_smooth": self.v_cap_smooth.get()}
        self.cfg = cfg
        self._push_debounced(cfg)
        return cfg

    def _push_debounced(self, cfg, delay=180):
        """A slider drag fires this on every pixel. Doing a blocking POST per
        event would freeze the panel and flood the server, so coalesce to one
        request once the drag settles, and make that request off the UI thread."""
        if self._push_job is not None:
            try:
                self.root.after_cancel(self._push_job)
            except Exception:
                pass
        self._push_job = self.root.after(
            delay,
            lambda: threading.Thread(target=self.push, args=(cfg,),
                                     kwargs={"save": False},
                                     daemon=True).start())

    def _load_monitors(self):
        """Fill the monitor dropdown from the same enumeration the strip
        uses, so the two can never disagree about what exists."""
        mons = []
        if _overlay is not None:
            try:
                mons = _overlay.list_monitors(self.root)
            except Exception as e:
                log(f"monitor list failed: {e}")
        labels = [MONITOR_PRIMARY_LABEL]
        self._monitor_map = {MONITOR_PRIMARY_LABEL: "primary"}
        for m in mons:
            label = (f"{m['name']}  {m['w']}x{m['h']}"
                     + ("  (primary)" if m["primary"] else ""))
            self._monitor_map[label] = m["name"]
            labels.append(label)
        self.cmb_monitor.config(values=labels)
        want = self.cfg.get("caption_monitor", "primary")
        self.v_cap_monitor.set(
            next((lab for lab, name in self._monitor_map.items()
                  if name == want and lab != MONITOR_PRIMARY_LABEL),
                 MONITOR_PRIMARY_LABEL))

    def _load_devices(self, refresh=False):
        """Populate the output-device list, off the UI thread.

        `refresh` re-initializes PortAudio server-side. That is the only way a
        device plugged in *after* the server started becomes visible, which is
        the whole point of the Rescan button: PortAudio enumerates once."""
        if refresh:
            self.status.config(text="Rescanning audio devices...")

        def work():
            d = api("/devices" + ("?refresh=1" if refresh else ""),
                    timeout=10.0 if refresh else 3.0)
            self.ui(lambda: self._devices_ready(d, refresh))
        threading.Thread(target=work, daemon=True).start()

    def _devices_ready(self, d, refreshed=False):
        if self.win is None:
            return
        if not d:
            self.lbl_device.config(
                text="Server not running - cannot list output devices.")
            return
        labels = [DEVICE_DEFAULT_LABEL]
        self._device_map = {DEVICE_DEFAULT_LABEL: None}
        for dev in d.get("devices", []):
            # the same physical output appears once per host API; they behave
            # differently, so keep them all but label which is which
            label = f"{dev['name']}  [{dev['hostapi']}]"
            self._device_map[label] = dev["spec"]
            labels.append(label)
        self.cmb_device.config(values=labels)

        cur = d.get("current")
        sel = DEVICE_DEFAULT_LABEL
        if cur:
            for label, spec in self._device_map.items():
                if spec == cur:
                    sel = label
                    break
            else:
                sel = f"{cur}  (not connected)"
                self.cmb_device.config(values=labels + [sel])
        self.v_device.set(sel)
        self.lbl_device.config(
            text=f"Currently playing through: {d.get('current_name') or '?'}")
        if refreshed:
            self.status.config(text="Audio devices rescanned.")

    def _device_changed(self):
        cfg = self._apply_live()
        self.status.config(text="Switching output device...")

        def work():
            self.push(cfg, save=False)
            d = api("/devices", timeout=8.0)

            def done():
                self._devices_ready(d)
                self.status.config(text="Output device changed - "
                                        "press Save to keep it.")
            self.ui(done)
        threading.Thread(target=work, daemon=True).start()

    def _refresh_sustain(self):
        """Poll the server's measured throughput, off the UI thread.

        rt is an EMA the server learns per chunk, so it drifts as the read
        goes on -- which is exactly why this repeats rather than sampling
        once when the panel opens."""
        def work():
            rt, _dens, ok = self.measured()
            self.ui(lambda: self._sustain_ready(rt, ok))
        threading.Thread(target=work, daemon=True).start()
        if self.win is not None:
            self.win.after(4000, self._refresh_sustain)

    def _sustain_ready(self, rt, ok):
        if self.win is None:
            return
        self._rt = rt if ok else None
        if not ok or not rt:
            self.lbl_sustain.config(text="Server not running - cannot measure.")
        else:
            ceiling = self.v_model.get() * rt * SUSTAIN_MARGIN
            self.lbl_sustain.config(
                text=(f"This machine synthesizes at {rt:.2f}x realtime, so it "
                      f"sustains about {ceiling:.2f}x reading speed."))
        self._check_sustain()

    def _check_sustain(self):
        rt = getattr(self, "_rt", None)
        if not rt:
            self.lbl_warn.config(text="")
            return
        model, playback = self._split(self.v_speed.get(), self.v_model.get())
        if playback >= rt:
            self.lbl_warn.config(
                text=("Above this machine's limit. Audio is consumed faster "
                      "than it can be synthesized, so reads will stall for "
                      "seconds at a time. Lower the speed, or raise the "
                      "Advanced slider so less of the speed comes from "
                      "stretching."))
        elif playback >= rt * SUSTAIN_MARGIN:
            self.lbl_warn.config(
                text="Close to this machine's limit - occasional stutter likely.")
        else:
            self.lbl_warn.config(text="")

    def _test(self):
        self._apply_live()
        # after the debounce, so the sample is spoken with the new settings
        self.root.after(260, lambda: threading.Thread(
            target=api, args=("/speak",), kwargs={"payload": {"text": SAMPLE}},
            daemon=True).start())
        self.status.config(text="Speaking a sample...")

    def _save(self):
        was = load_settings()          # compare against disk, before the write
        cfg = self._apply_live()
        ok = save_settings(cfg)
        if not ok:
            self.status.config(
                text="Could not write settings.json (see tray.log).")
            return
        if any(was.get(k) != cfg.get(k) for k in CAPTION_KEYS):
            # the strip reads these once at startup; nothing else applies them
            self.restart_overlay_async()
            self.status.config(text="Saved - restarting the caption strip.")
        else:
            self.status.config(
                text="Saved - these settings now survive a restart.")

    def _reset(self):
        self.v_speed.set(round(DEFAULTS["model_speed"] * DEFAULTS["playback_speed"], 2))
        self.v_model.set(DEFAULTS["model_speed"])
        self.v_pause.set(DEFAULTS["pause"])
        self.v_first.set(DEFAULTS["first_chunk_audio"])
        self.v_voice.set(DEFAULTS["voice"])
        self.v_device.set(DEVICE_DEFAULT_LABEL)
        self.v_cap_style.set(DEFAULTS["caption_style"])
        self.v_cap_layout.set(DEFAULTS["caption_layout"])
        self.v_cap_pos.set(DEFAULTS["caption_position"])
        self.v_cap_monitor.set(MONITOR_PRIMARY_LABEL)
        self.v_cap_smooth.set(DEFAULTS["caption_smooth"])
        self._speed_changed()
        self.status.config(text="Reset to defaults (not saved yet).")

    def _close(self):
        w, self.win = self.win, None
        try:
            w.destroy()
        except Exception:
            pass


def main():
    """--settings opens the panel straight away, which is how the desktop
    entry launches it where there is no tray icon to click."""
    try:
        App().start(open_panel="--settings" in sys.argv[1:])
    except Exception:
        log("FATAL\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
