r"""Windows notification-area icon for the Kokoro tray.

Split out of tray.py 2026-08-18 so tray.py can be imported on Linux at
all: this module builds ctypes structures against `ctypes.wintypes` at
import time, which raises on any other platform. tray.py imports it only
when sys.platform == "win32".

Nothing here talks to the server or the process table directly -- every
menu action goes through the `app` object it is constructed with, which
is what keeps this file Windows-only and tray.py platform-neutral.
The `app` contract (see tray.App):

    log(msg)                    open_settings()
    menu_status()               stop_speaking()
    reconnect_audio()           restart_server_async()
    restart_highlighter_async() open_folder()
    quit_all()
"""

import ctypes
import traceback
from ctypes import wintypes

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
ID_RECONNECT, ID_DEVICE = 8, 9


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
            self.app.log("wndproc error\n" + traceback.format_exc())
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _command(self, cid):
        a = self.app
        if cid == ID_SETTINGS:
            a.open_settings()
        elif cid == ID_STOP:
            a.stop_speaking()
        elif cid == ID_RECONNECT:
            # POST /devices re-initializes PortAudio and reopens the stream --
            # the fix for "I replugged my headphones and sound went nowhere"
            # without needing the settings panel at all.
            a.reconnect_audio()
        elif cid == ID_RESTART_SRV:
            a.restart_server_async()
        elif cid == ID_RESTART_HL:
            a.restart_highlighter_async()
        elif cid == ID_LOGS:
            a.open_folder()
        elif cid == ID_QUIT:
            a.quit_all()

    def _menu(self):
        status, device = self.app.menu_status()

        m = user32.CreatePopupMenu()
        user32.AppendMenuW(m, MF_STRING | MF_GRAYED, ID_STATUS, status)
        if device:
            user32.AppendMenuW(m, MF_STRING | MF_GRAYED, ID_DEVICE, device)
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(m, MF_STRING, ID_SETTINGS, "Settings...")
        user32.AppendMenuW(m, MF_STRING, ID_STOP, "Stop speaking")
        user32.AppendMenuW(m, MF_STRING, ID_RECONNECT, "Reconnect audio device")
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
        self.app.log(f"tray icon added hwnd={self.hwnd}")

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


