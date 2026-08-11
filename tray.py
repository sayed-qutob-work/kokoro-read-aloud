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

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from ctypes import wintypes

import tkinter as tk
from tkinter import ttk

ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(ROOT, "settings.json")
LOG = os.path.join(ROOT, "tray.log")
SERVER = "http://127.0.0.1:5111"
PY = os.path.join(ROOT, "env", "Scripts", "python.exe")

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

DEFAULTS = {
    "voice": "af_heart",
    "model_speed": 1.15,
    "playback_speed": 1.4,
    "pause": 0.1,
    "first_chunk_audio": 2.0,
}


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
        flags = 0x08000000 | 0x00000008   # CREATE_NO_WINDOW | DETACHED_PROCESS
        out = open(stdout_path, "w") if stdout_path else subprocess.DEVNULL
        subprocess.Popen(args, cwd=ROOT, creationflags=flags,
                         stdout=out, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, close_fds=True)
        return True
    except Exception as e:
        log(f"spawn failed {args}: {e}")
        return False


def kill_script(name):
    """Kill python processes whose command line mentions `name`.

    Filters on the COMMAND LINE, never the image name: server, highlighter and
    tray are all python.exe, and each runs as a venv-launcher + child pair."""
    n = 0
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
              f"Where-Object {{ $_.CommandLine -like '*{name}*' }} | "
              "ForEach-Object { $_.ProcessId }")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=15)
        me = os.getpid()
        for line in r.stdout.split():
            try:
                pid = int(line.strip())
            except ValueError:
                continue
            if pid == me:
                continue
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=10)
            n += 1
    except Exception as e:
        log(f"kill {name} failed: {e}")
    return n


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


# --------------------------------------------------------------- tray icon

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_APP_TRAY = 0x0400 + 17
WM_TASKBARCREATED = None          # registered at runtime; tray survives explorer restart

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
MF_STRING, MF_SEPARATOR, MF_GRAYED = 0x0000, 0x0800, 0x0001

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# ctypes assumes C `int` for anything it has no prototype for, so on 64-bit
# every HWND/HINSTANCE/HMENU silently truncates -- or, as here, raises
# "int too long to convert" from CreateWindowExW's hInstance. AUDIT records
# the same lesson for the highlighter's layered window: declare the
# prototypes, don't rely on defaults.
UINT_PTR = ctypes.c_size_t

user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.RegisterClassW.restype = wintypes.ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.CreatePopupMenu.argtypes = []
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, UINT_PTR,
                               wintypes.LPCWSTR]
user32.TrackPopupMenu.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                  wintypes.LPVOID]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateIconIndirect.restype = wintypes.HICON
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateBitmap.restype = wintypes.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT,
                               wintypes.UINT, wintypes.LPVOID]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATA)]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


def make_icon(size=32):
    """Build the tray icon in memory: a rounded accent-blue tile with three
    white 'text lines'. Drawn rather than shipped as a .ico so there is no
    binary asset to lose, and no file path to get wrong at startup.

    #3d5afe is the same accent the in-place highlighter paints words with."""
    px = bytearray(size * size * 4)          # BGRA, premultiplied not required
    r_out = size // 2 - 1
    cx = cy = (size - 1) / 2.0
    R, G, B = 0x3d, 0x5a, 0xfe

    def rounded(x, y, radius=6.0):
        """Inside a rounded square of half-width r_out with corner `radius`."""
        dx, dy = abs(x - cx), abs(y - cy)
        if dx > r_out or dy > r_out:
            return False
        ix, iy = r_out - radius, r_out - radius
        if dx <= ix or dy <= iy:
            return True
        return (dx - ix) ** 2 + (dy - iy) ** 2 <= radius ** 2

    # three text lines, as (top, bottom, left, right) in fractions of size
    lines = [(0.30, 0.38, 0.26, 0.66),
             (0.46, 0.54, 0.26, 0.74),
             (0.62, 0.70, 0.26, 0.58)]

    for y in range(size):
        for x in range(size):
            if not rounded(x, y):
                continue
            on_line = any(size * t <= y < size * bt and size * l <= x < size * rr
                          for t, bt, l, rr in lines)
            i = (y * size + x) * 4
            if on_line:
                px[i:i + 4] = bytes((255, 255, 255, 255))
            else:
                px[i:i + 4] = bytes((B, G, R, 255))

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = size
    bi.biHeight = -size                      # top-down
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0                     # BI_RGB

    bits = ctypes.c_void_p()
    dib = gdi32.CreateDIBSection(None, ctypes.byref(bi), 0,
                                 ctypes.byref(bits), None, 0)
    if not dib or not bits.value:
        raise OSError("CreateDIBSection failed for tray icon")
    ctypes.memmove(bits.value, bytes(px), len(px))

    # 1bpp AND mask: all zero = "use the color bitmap's alpha everywhere"
    mask = gdi32.CreateBitmap(size, size, 1, 1, bytes(size * size // 8))
    ii = ICONINFO(True, 0, 0, mask, dib)
    hicon = user32.CreateIconIndirect(ctypes.byref(ii))
    gdi32.DeleteObject(dib)
    gdi32.DeleteObject(mask)
    if not hicon:
        raise OSError("CreateIconIndirect failed for tray icon")
    return hicon


# Menu command ids
ID_STATUS, ID_SETTINGS, ID_STOP = 1, 2, 3
ID_RESTART_SRV, ID_RESTART_HL, ID_LOGS, ID_QUIT = 4, 5, 6, 7


class Tray:
    """Hidden message window + notification-area icon, on its own thread."""

    def __init__(self, app):
        self.app = app
        self.hwnd = None
        self.hicon = None
        self._proc = WNDPROC(self._wndproc)   # must outlive the window

    # ---- win32 plumbing
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_APP_TRAY:
                low = lparam & 0xFFFF
                if low in (WM_RBUTTONUP, WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    if low == WM_RBUTTONUP:
                        self._menu()
                    else:
                        self.app.open_settings()
                return 0
            if msg == WM_COMMAND:
                self._command(wparam & 0xFFFF)
                return 0
            if WM_TASKBARCREATED and msg == WM_TASKBARCREATED:
                # explorer.exe restarted and took the tray with it
                self._add()
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            log("wndproc error\n" + traceback.format_exc())
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _command(self, cid):
        a = self.app
        if cid == ID_SETTINGS:
            a.open_settings()
        elif cid == ID_STOP:
            api("/stop", payload={})
        elif cid == ID_RESTART_SRV:
            threading.Thread(target=restart_server, daemon=True).start()
        elif cid == ID_RESTART_HL:
            threading.Thread(target=restart_highlighter, daemon=True).start()
        elif cid == ID_LOGS:
            os.startfile(ROOT)
        elif cid == ID_QUIT:
            a.quit_all()

    def _menu(self):
        cfg = api("/config", timeout=0.6)
        if cfg:
            status = (f"Server: running  -  {cfg.get('effective_speed', '?')}x, "
                      f"{cfg.get('voice', '?')}")
        else:
            status = "Server: NOT RUNNING"

        m = user32.CreatePopupMenu()
        user32.AppendMenuW(m, MF_STRING | MF_GRAYED, ID_STATUS, status)
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(m, MF_STRING, ID_SETTINGS, "Settings...")
        user32.AppendMenuW(m, MF_STRING, ID_STOP, "Stop speaking")
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(m, MF_STRING, ID_RESTART_SRV, "Restart TTS server")
        user32.AppendMenuW(m, MF_STRING, ID_RESTART_HL, "Restart highlighter")
        user32.AppendMenuW(m, MF_STRING, ID_LOGS, "Open Kokoro folder")
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(m, MF_STRING, ID_QUIT, "Quit Kokoro")

        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # Required dance: without the foreground grab the menu never closes
        # when the user clicks elsewhere, and it swallows the next click.
        user32.SetForegroundWindow(self.hwnd)
        cid = user32.TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(m)
        if cid:
            self._command(cid)

    def _nid(self, flags=NIF_MESSAGE | NIF_ICON | NIF_TIP):
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = self.hicon or 0
        nid.szTip = "Kokoro read-aloud"
        return nid

    def _add(self):
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid()))

    def run(self):
        """Create the window + icon and pump messages. Blocks its thread."""
        global WM_TASKBARCREATED
        hinst = kernel32.GetModuleHandleW(None)
        cls = WNDCLASS()
        cls.lpfnWndProc = self._proc
        cls.hInstance = hinst
        cls.lpszClassName = "KokoroTrayWnd"
        # IDC_ARROW is a MAKEINTRESOURCE ordinal, not a string pointer
        cls.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))
        if not user32.RegisterClassW(ctypes.byref(cls)):
            if ctypes.get_last_error() != 1410:           # already registered
                raise OSError(f"RegisterClassW: {ctypes.get_last_error()}")

        self.hwnd = user32.CreateWindowExW(0, "KokoroTrayWnd", "Kokoro",
                                           0, 0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowExW: {ctypes.get_last_error()}")

        WM_TASKBARCREATED = user32.RegisterWindowMessageW("TaskbarCreated")
        self.hicon = make_icon()
        self._add()
        log(f"tray icon added hwnd={self.hwnd}")

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def remove(self):
        try:
            if self.hwnd:
                shell32.Shell_NotifyIconW(NIM_DELETE,
                                          ctypes.byref(self._nid(NIF_MESSAGE)))
                user32.PostMessageW(self.hwnd, WM_DESTROY, 0, 0)
        except Exception:
            pass


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
        self.tray = Tray(self)
        self._push_job = None     # debounce handle for slider drags
        self._rt = None           # last measured throughput, or None

    # ---- lifecycle
    def start(self):
        threading.Thread(target=self._tray_thread, daemon=True).start()
        self.push(self.cfg, save=False)      # apply persisted settings on boot
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
            self.tray.remove()
            try:
                self.root.quit()
            except Exception:
                pass
            os._exit(0)
        self.root.after(0, go)

    def open_settings(self):
        self.root.after(0, self._open_settings)

    # ---- server round trips
    def push(self, cfg, save=True):
        """Send to the live server and optionally persist."""
        api("/config", payload={k: cfg[k] for k in DEFAULTS if k in cfg})
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

        # --- speed
        ttk.Label(frm, text="Reading speed", font=("Segoe UI", 10, "bold")
                  ).grid(row=0, column=0, sticky="w", **pad)
        self.v_speed = tk.DoubleVar(
            value=round(self.cfg["model_speed"] * self.cfg["playback_speed"], 2))
        self.lbl_speed = ttk.Label(frm, text="")
        self.lbl_speed.grid(row=0, column=2, sticky="e", **pad)
        s = ttk.Scale(frm, from_=0.8, to=3.0, variable=self.v_speed,
                      command=lambda _=None: self._speed_changed())
        s.grid(row=1, column=0, columnspan=3, sticky="ew", **pad)

        self.lbl_sustain = ttk.Label(frm, text="", foreground="#555")
        self.lbl_sustain.grid(row=2, column=0, columnspan=3, sticky="w", **pad)
        self.lbl_warn = ttk.Label(frm, text="", foreground="#b00020",
                                  wraplength=430, justify="left")
        self.lbl_warn.grid(row=3, column=0, columnspan=3, sticky="w", **pad)

        ttk.Separator(frm).grid(row=4, column=0, columnspan=3,
                                sticky="ew", pady=10)

        # --- voice
        ttk.Label(frm, text="Voice").grid(row=5, column=0, sticky="w", **pad)
        self.v_voice = tk.StringVar(value=self.cfg["voice"])
        ttk.Combobox(frm, textvariable=self.v_voice, values=VOICES,
                     state="readonly", width=18
                     ).grid(row=5, column=1, columnspan=2, sticky="e", **pad)

        # --- pause
        ttk.Label(frm, text="Pause after sentences").grid(row=6, column=0,
                                                          sticky="w", **pad)
        self.v_pause = tk.DoubleVar(value=self.cfg["pause"])
        self.lbl_pause = ttk.Label(frm, text="")
        self.lbl_pause.grid(row=6, column=2, sticky="e", **pad)
        ttk.Scale(frm, from_=0.0, to=0.6, variable=self.v_pause,
                  command=lambda _=None: self._labels()
                  ).grid(row=7, column=0, columnspan=3, sticky="ew", **pad)

        # --- start buffer
        ttk.Label(frm, text="Buffer before speaking").grid(row=8, column=0,
                                                           sticky="w", **pad)
        self.v_first = tk.DoubleVar(value=self.cfg["first_chunk_audio"])
        self.lbl_first = ttk.Label(frm, text="")
        self.lbl_first.grid(row=8, column=2, sticky="e", **pad)
        ttk.Scale(frm, from_=0.8, to=4.0, variable=self.v_first,
                  command=lambda _=None: self._labels()
                  ).grid(row=9, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(frm, text="Larger = slower to start, less likely to stutter.",
                  foreground="#555").grid(row=10, column=0, columnspan=3,
                                          sticky="w", **pad)

        ttk.Separator(frm).grid(row=11, column=0, columnspan=3,
                                sticky="ew", pady=10)

        # --- advanced
        adv = ttk.LabelFrame(frm, text="Advanced", padding=10)
        adv.grid(row=12, column=0, columnspan=3, sticky="ew", **pad)
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
        btns.grid(row=13, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        ttk.Button(btns, text="Test", command=self._test).pack(side="left")
        ttk.Button(btns, text="Reset", command=self._reset).pack(side="left",
                                                                 padx=6)
        ttk.Button(btns, text="Close", command=self._close).pack(side="right")
        ttk.Button(btns, text="Save", command=self._save).pack(side="right",
                                                               padx=6)

        self.status = ttk.Label(frm, text="", foreground="#0a7")
        self.status.grid(row=14, column=0, columnspan=3, sticky="w", pady=(8, 0))

        frm.columnconfigure(1, weight=1)
        self._labels()
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
               "first_chunk_audio": round(self.v_first.get(), 2)}
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

    def _refresh_sustain(self):
        """Poll the server's measured throughput, off the UI thread.

        rt is an EMA the server learns per chunk, so it drifts as the read
        goes on -- which is exactly why this repeats rather than sampling
        once when the panel opens."""
        def work():
            rt, _dens, ok = self.measured()
            self.root.after(0, lambda: self._sustain_ready(rt, ok))
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
        cfg = self._apply_live()
        ok = save_settings(cfg)
        self.status.config(
            text="Saved - these settings now survive a restart." if ok
                 else "Could not write settings.json (see tray.log).")

    def _reset(self):
        self.v_speed.set(round(DEFAULTS["model_speed"] * DEFAULTS["playback_speed"], 2))
        self.v_model.set(DEFAULTS["model_speed"])
        self.v_pause.set(DEFAULTS["pause"])
        self.v_first.set(DEFAULTS["first_chunk_audio"])
        self.v_voice.set(DEFAULTS["voice"])
        self._speed_changed()
        self.status.config(text="Reset to defaults (not saved yet).")

    def _close(self):
        w, self.win = self.win, None
        try:
            w.destroy()
        except Exception:
            pass


def main():
    try:
        App().start()
    except Exception:
        log("FATAL\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
