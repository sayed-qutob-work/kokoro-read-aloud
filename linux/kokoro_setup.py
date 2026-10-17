"""`kokoroctl setup`: install everything the code needs to run, outside the
code folder.

    kokoroctl setup [--cpu | --cuda] [--background]

  1. Checks the system packages: Tk for the settings panel, PortAudio,
     wl-clipboard, jq, curl, notify-send. Setup has no root, so it cannot
     install them; it prints the one command that does.
  2. uv: the system's, when it is at least UV_MIN, else the pinned
     UV_VERSION from GitHub, checked against its SHA-256.
  3. Python 3.12 and the server's packages, `uv sync --frozen` from uv.lock:
     the CUDA variant when nvidia-smi works, else the CPU one. Then it checks
     that torch really is the build it asked for -- a bare `uv sync` would
     have installed an untested one from PyPI (AUDIT 2026-10-17).
  4. The model files (model_files.py), each checked against its SHA-256.
  5. The systemd units, the launcher entry and, under Hyprland, the hotkeys.
     Then it restarts the server and waits until it answers.

Everything goes to ~/.local/share/kokoro-read-aloud: uv, uv's own cache,
Python, the venv and the model. The code folder is never written to (on
Omarchy it is the plugin folder, and a write there reloads every plugin),
and `kokoroctl uninstall` can remove all of it without touching other uv
projects. Run it again to repair: each step keeps what is already in place.

Progress goes to paths.SETUP_JSON, which `kokoroctl status` includes for the
bar widget. --background runs setup as a transient systemd unit
(kokoro-setup), so it outlives whatever started it; its output then goes to
paths.SETUP_LOG.

Stdlib only: it runs on the system Python, before any venv exists.
"""

import ctypes.util
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.dont_write_bytecode = True     # no __pycache__ next to the code, see paths.py
sys.path.insert(0, ROOT)
import model_files                 # noqa: E402
import paths                       # noqa: E402

# uv.lock was written by UV_VERSION. UV_MIN is the oldest uv tested against
# it: Arch's 0.12.10 reads the lock as up to date and installs the same 96
# CPU packages (AUDIT 2026-10-17).
UV_VERSION = "0.12.24"
UV_MIN = (0, 12, 10)
UV_TARBALL = "uv-x86_64-unknown-linux-gnu.tar.gz"
UV_SHA256 = "b4dfaef47d491a7296981f8374a4595f55dbf84e8937c8ecd2983574d8bb3da6"
UV_URL = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{UV_TARBALL}"

UV_BIN = os.path.join(paths.DATA, "bin", "uv")
UV_CACHE = os.path.join(paths.DATA, "cache")
PYTHON_DIR = os.path.join(paths.DATA, "python")
VENV_PYTHON = os.path.join(paths.VENV, "bin", "python")

UNIT = "kokoro-setup"
SERVER = "http://127.0.0.1:5111"
STEPS = ["Checking system packages", "Getting uv",
         "Installing Python and packages", "Downloading the model",
         "Starting Kokoro"]
# Free space needed for a first install. CUDA measured at 6.6 GB of venv on
# btrfs, where uv's cache shares its blocks; elsewhere the cache hardlinks.
NEED_GIB = {"cuda": 8, "cpu": 3}


class SetupError(Exception):
    pass


class Progress:
    """Writes paths.SETUP_JSON (atomically, at most ~4 times a second unless
    the step changes) and prints a line per change of step or detail."""

    def __init__(self, variant):
        self.d = {"state": "running", "step": 0, "steps": len(STEPS),
                  "title": "", "detail": "", "fraction": None,
                  "variant": variant, "error": None, "log": paths.SETUP_LOG,
                  "started": int(time.time()), "updated": None}
        self._last_write = 0.0
        self._last_shown = None

    def step(self, n, detail=""):
        self.d.update(step=n, title=STEPS[n - 1], detail=detail, fraction=None)
        print(f"[{n}/{len(STEPS)}] {STEPS[n - 1]}", flush=True)
        self._show()
        self._write(force=True)

    def update(self, detail=None, fraction=None):
        if detail is not None:
            self.d["detail"] = detail
        self.d["fraction"] = fraction
        self._show()
        self._write()

    def finish(self, state, detail="", error=None):
        self.d.update(state=state, detail=detail, error=error, fraction=None)
        self._write(force=True)

    def _show(self):
        frac = self.d["fraction"]
        shown = (self.d["detail"], None if frac is None else int(frac * 10))
        if self.d["detail"] and shown != self._last_shown:
            pct = "" if frac is None else f"{frac * 100:3.0f}%  "
            print(f"      {pct}{self.d['detail']}", flush=True)
        self._last_shown = shown

    def _write(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_write < 0.25:
            return
        self._last_write = now
        self.d["updated"] = int(time.time())
        tmp = paths.SETUP_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.d, f)
        os.replace(tmp, paths.SETUP_JSON)


# ---------------------------------------------------------------- 1. system

def _os_release():
    ids = set()
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                key, _, val = line.strip().partition("=")
                if key in ("ID", "ID_LIKE"):
                    ids.update(val.strip('"').split())
    except OSError:
        pass
    return ids


def _ok(cmd):
    try:
        return subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def check_system(variant):
    if platform.machine() != "x86_64":
        raise SetupError(f"this needs an x86_64 machine, not {platform.machine()}: "
                         "uv.lock covers Linux x86_64 only")
    gnome = "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "")
    # (what it is for, present?, Arch package, Fedora package)
    needs = [
        ("Tk for the settings panel and caption strip",
         _ok([paths.GUI_PYTHON, "-c", "import tkinter"]), "tk", "python3-tkinter"),
        ("PortAudio, for audio output",
         ctypes.util.find_library("portaudio") is not None, "portaudio", "portaudio"),
        ("wl-paste, to read the selection",
         shutil.which("wl-paste") is not None, "wl-clipboard", "wl-clipboard"),
        ("jq", shutil.which("jq") is not None, "jq", "jq"),
        ("curl", shutil.which("curl") is not None, "curl", "curl"),
        ("notify-send", shutil.which("notify-send") is not None, "libnotify", "libnotify"),
        ("a systemd user session", _ok(["systemctl", "--user", "is-system-running"])
         or _ok(["systemctl", "--user", "show-environment"]), "systemd", "systemd"),
    ]
    if gnome:   # reads go through Xwayland there (AUDIT 2026-09-30)
        needs.append(("python-xlib for the system Python, to read the selection on GNOME",
                      _ok([paths.GUI_PYTHON, "-c", "import Xlib"]),
                      "python-xlib", "python3-xlib"))
    missing = [n for n in needs if not n[1]]
    if missing:
        ids = _os_release()
        if "arch" in ids:
            cmd = "sudo pacman -S --needed " + " ".join(m[2] for m in missing)
        elif ids & {"fedora", "rhel"}:
            cmd = "sudo dnf install " + " ".join(m[3] for m in missing)
        else:
            cmd = "your package manager: " + ", ".join(m[2] for m in missing)
        raise SetupError("missing: " + "; ".join(m[0] for m in missing)
                         + f". Install them with: {cmd} -- then run setup again")

    if not os.path.exists(VENV_PYTHON):
        probe = paths.DATA
        while not os.path.exists(probe):
            probe = os.path.dirname(probe)
        free = shutil.disk_usage(probe).free / 2**30
        if free < NEED_GIB[variant]:
            raise SetupError(f"{free:.1f} GiB free in {probe}; the {variant} install "
                             f"needs about {NEED_GIB[variant]} GiB")


def pick_variant():
    """cuda when nvidia-smi can talk to a GPU, else cpu."""
    return "cuda" if shutil.which("nvidia-smi") and _ok(["nvidia-smi", "-L"]) else "cpu"


# -------------------------------------------------------------------- 2. uv

def _uv_version(exe):
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.match(r"uv (\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else None


def get_uv(progress):
    system = shutil.which("uv")
    if system:
        v = _uv_version(system)
        if v and v >= UV_MIN:
            progress.update(f"using the system's uv {'.'.join(map(str, v))} ({system})")
            return system
        progress.update(f"the system's uv is older than {'.'.join(map(str, UV_MIN))}; "
                        f"getting uv {UV_VERSION}")
    pinned = tuple(int(x) for x in UV_VERSION.split("."))
    if _uv_version(UV_BIN) == pinned:
        progress.update(f"using uv {UV_VERSION} ({UV_BIN})")
        return UV_BIN

    os.makedirs(os.path.dirname(UV_BIN), exist_ok=True)
    part = os.path.join(os.path.dirname(UV_BIN), UV_TARBALL + ".part")
    req = urllib.request.Request(UV_URL, headers={"User-Agent": "kokoro-read-aloud"})
    h, done = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            while chunk := r.read(1 << 20):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                progress.update(f"downloading uv {UV_VERSION}",
                                done / total if total else None)
    except (urllib.error.URLError, OSError) as e:
        raise SetupError(f"could not download uv from github.com "
                         f"({getattr(e, 'reason', e)}). Check the connection, "
                         f"or install uv yourself, then run setup again")
    try:
        if h.hexdigest() != UV_SHA256:
            raise SetupError(f"the uv download does not match its pinned SHA-256 "
                             f"({h.hexdigest()})")
        # Only the one binary is read out of the archive, under a name of
        # our choosing, so the archive's own paths are never used.
        with tarfile.open(part) as t:
            src = t.extractfile(t.getmember(f"{UV_TARBALL[:-7]}/uv"))
            with open(UV_BIN + ".part", "wb") as out:
                shutil.copyfileobj(src, out)
        os.chmod(UV_BIN + ".part", 0o755)
        os.replace(UV_BIN + ".part", UV_BIN)
    finally:
        os.remove(part)
    progress.update(f"installed uv {UV_VERSION} ({UV_BIN})")
    return UV_BIN


# -------------------------------------------------------------- 3. packages

def uv_env():
    """uv's settings for every call: its own cache and Python under DATA,
    the venv outside the code folder, and no user uv.toml to change what
    gets installed."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "PYTHONHOME")}
    env.update(UV_NO_CONFIG="1", UV_CACHE_DIR=UV_CACHE,
               UV_PYTHON_INSTALL_DIR=PYTHON_DIR,
               UV_PYTHON_PREFERENCE="only-managed",
               UV_PROJECT_ENVIRONMENT=paths.VENV)
    return env


_SIZE = {"KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "B": 1}


def install_packages(uv, variant, progress):
    env = uv_env()
    progress.update("Python 3.12")
    r = subprocess.run([uv, "python", "install", "3.12", "--no-bin"],
                       env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise SetupError("uv could not install Python 3.12: " + r.stderr.strip()[-400:])

    # uv announces every large download before the first one finishes, then
    # prints " Downloaded <name>" for each: done/announced is the fraction.
    env["UV_PYTHON_DOWNLOADS"] = "never"
    cmd = [uv, "sync", "--frozen", "--extra", variant, "--compile-bytecode",
           "--project", ROOT]
    sizes, done, tail = {}, 0, []
    p = subprocess.Popen(cmd, env=env, cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in p.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-12:]
        m = re.match(r"Downloading (\S+) \(([\d.]+)(KiB|MiB|GiB|B)\)", line)
        if m:
            sizes[m.group(1)] = float(m.group(2)) * _SIZE[m.group(3)]
            continue
        m = re.match(r"\s*Downloaded (\S+)", line)
        if m and sizes:
            done += sizes.get(m.group(1), 0)
            total = sum(sizes.values())
            progress.update(f"packages: {done / 2**20:.0f} of {total / 2**20:.0f} MiB "
                            f"({variant})", done / total)
        elif re.match(r"(Prepared|Installed|Uninstalled|Bytecode compiled|Audited) ", line):
            progress.update(line)
    if p.wait() != 0:
        raise SetupError("uv sync failed:\n" + "\n".join(tail))

    # The build that came out, not the one asked for: without an extra, or
    # with a stray index, uv would quietly install another torch.
    env.pop("CUDA_VISIBLE_DEVICES", None)
    r = subprocess.run([VENV_PYTHON, "-c", "import torch; print(torch.__version__); "
                        "print(torch.cuda.is_available())"],
                       env=env, capture_output=True, text=True)
    lines = r.stdout.split()
    want = "+cu126" if variant == "cuda" else "+cpu"
    if r.returncode != 0 or not lines or not lines[0].endswith(want):
        raise SetupError(f"expected a torch ending in {want}, got: "
                         f"{(r.stdout + r.stderr).strip()[-300:]}")
    note = f"torch {lines[0]}"
    if variant == "cuda" and lines[-1] != "True":
        note += (" -- but it cannot see the GPU, so the server will run on the "
                 "CPU. Check the NVIDIA driver, or use `kokoroctl setup --cpu` "
                 "for the much smaller CPU build")
    progress.update(note)
    return note


# ------------------------------------------------------------------ 4. model

def fetch_model(progress):
    if not model_files.verify():
        progress.update("all files in place and verified")
        return

    def show(done, total, name):
        progress.update(f"{name}: {done / 2**20:.0f} of {total / 2**20:.0f} MiB",
                        done / total)
    try:
        model_files.fetch(show)
    except (urllib.error.URLError, OSError) as e:
        raise SetupError(f"could not download the model from huggingface.co "
                         f"({getattr(e, 'reason', e)}). Check the connection, "
                         f"then run setup again: it keeps what it already has")
    progress.update("downloaded and verified")


# --------------------------------------------------------------- 5. services

def _script(name):
    r = subprocess.run([os.path.join(ROOT, "linux", name)], capture_output=True,
                       text=True)
    print("\n".join("        " + l for l in (r.stdout + r.stderr).splitlines()), flush=True)
    if r.returncode != 0:
        raise SetupError(f"{name} failed: " + (r.stderr or r.stdout).strip()[-400:])


def _server_up():
    try:
        with urllib.request.urlopen(SERVER + "/config", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def install_services(progress):
    was_running = subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                                  "kokoro-server.service"]).returncode == 0
    progress.update("systemd units")
    _script("install-systemd.sh")      # enables the server, and starts it if it was not
    progress.update("launcher entry")
    _script("install-desktop.sh")
    hyprland = bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))
    if hyprland:
        progress.update("Hyprland hotkeys")
        _script("install-hyprland.sh")
    # One that was already running still has its old Python and code.
    progress.update("starting the server")
    if was_running:
        subprocess.run(["systemctl", "--user", "restart", "kokoro-server.service"],
                       capture_output=True, timeout=60)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if _server_up():
            break
        time.sleep(0.5)
    else:
        raise SetupError(f"the server did not answer within 2 minutes; see "
                         f"{paths.SERVER_LOG}")
    if hyprland:
        return "Ready. Select text and press Ctrl+Alt+R."
    return ("Ready. Bind the hotkeys in Settings > Keyboard > Custom Shortcuts: "
            f"{ROOT}/read_aloud.sh selection | clipboard | stop")


# ------------------------------------------------------------------- driver

def _notify(title, body):
    """A desktop notification, for a background run: nobody may be watching
    the panel by the time it ends."""
    if os.environ.get("KOKORO_SETUP_BACKGROUND") and shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Kokoro read aloud", title, body[:300]],
                       capture_output=True, timeout=10)

def run(variant=None):
    """Foreground setup. Returns the exit status."""
    paths.prepare()
    lock = open(os.path.join(paths.STATE, "setup.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("setup is already running", file=sys.stderr)
        return 1
    variant = variant or pick_variant()
    progress = Progress(variant)
    try:
        progress.step(1, f"{variant} variant")
        check_system(variant)
        progress.step(2)
        uv = get_uv(progress)
        progress.step(3)
        install_packages(uv, variant, progress)
        progress.step(4)
        fetch_model(progress)
        progress.step(5)
        msg = install_services(progress)
    except Exception as e:      # SetupError, but network and disk too: report it
        err = str(e) if isinstance(e, SetupError) else f"{type(e).__name__}: {e}"
        progress.finish("failed", error=err)
        print(f"\nSetup failed: {err}", file=sys.stderr)
        _notify("Kokoro setup failed", err)
        return 1
    progress.finish("done", detail=msg)
    print(f"\n{msg}", flush=True)
    _notify("Kokoro is ready", msg)
    return 0


def start_background(args):
    """setup as a transient systemd unit: it outlives the bar widget or the
    terminal that started it, and its output goes to paths.SETUP_LOG."""
    paths.prepare()
    active = subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                             UNIT + ".service"]).returncode == 0
    if active:
        return "setup is already running"
    with open(paths.SETUP_JSON + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"state": "running", "step": 0, "steps": len(STEPS),
                   "title": "Starting", "detail": "", "fraction": None,
                   "error": None, "log": paths.SETUP_LOG,
                   "started": int(time.time()), "updated": int(time.time())}, f)
    os.replace(paths.SETUP_JSON + ".tmp", paths.SETUP_JSON)
    r = subprocess.run(
        ["systemd-run", "--user", "--unit", UNIT, "--collect", "--quiet",
         "--setenv=KOKORO_SETUP_BACKGROUND=1",
         "-p", f"StandardOutput=truncate:{paths.SETUP_LOG}",
         "-p", "StandardError=inherit",
         "/usr/bin/python3", os.path.join(ROOT, "linux", "kokoroctl"), "setup", *args],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SetupError("systemd-run failed: " + r.stderr.strip())
    return f"setup started in the background; progress: kokoroctl status, log: {paths.SETUP_LOG}"


def read_progress():
    """setup.json, for `kokoroctl status`. A setup that was killed (logout,
    OOM) leaves "running" behind for good; it is reported as failed once
    no setup holds the lock and the file has been quiet for 15 s -- the
    grace covers --background's own write before its unit takes the lock."""
    try:
        with open(paths.SETUP_JSON, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if d.get("state") == "running" and time.time() - (d.get("updated") or 0) > 15:
        try:
            with open(os.path.join(paths.STATE, "setup.lock"), "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)   # free: nobody runs
            d.update(state="failed", error="Setup was interrupted. Run it again: "
                     "it keeps what it already has.")
        except BlockingIOError:
            pass                        # held: it is running, just quiet
        except OSError:
            pass
    return d


# ---------------------------------------------------------------- uninstall

SERVICES = ("kokoro-server.service", "kokoro-overlay.service")


def _rmtree(path):
    """shutil.rmtree that also removes what uv leaves read-only in its cache."""
    def retry(func, p, _exc):
        os.chmod(os.path.dirname(p), 0o755)
        if os.path.isdir(p) and not os.path.islink(p):
            os.chmod(p, 0o755)
        func(p)
    if os.path.lexists(path):
        shutil.rmtree(path, onexc=retry)


def _size(path, follow=False):
    """Bytes under `path`, each file once however many links reach it.
    follow=True counts what symlinks point at: the Hugging Face cache keeps
    a model's files as links (from snapshots/ and blobs/) into a shared
    hub/blobs/."""
    total, seen = 0, set()
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                st = (os.stat if follow else os.lstat)(os.path.join(root, f))
            except OSError:
                continue
            if (st.st_dev, st.st_ino) not in seen:
                seen.add((st.st_dev, st.st_ino))
                total += st.st_size
    return total


def _our_pids(script):
    """Python processes running this code folder's `script`."""
    r = subprocess.run(["pgrep", "-f", os.path.join(ROOT, script)],
                       capture_output=True, text=True)
    out = []
    for pid in r.stdout.split():
        try:
            with open(f"/proc/{int(pid)}/comm", encoding="utf-8") as f:
                if f.read().startswith("python") and int(pid) != os.getpid():
                    out.append(int(pid))
        except (OSError, ValueError):
            pass
    return out


def _unhook_hyprland(removed):
    hypr = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "hypr")
    dest, main = os.path.join(hypr, "kokoro.lua"), os.path.join(hypr, "hyprland.lua")
    line = f'dofile("{dest}")'
    try:
        with open(main, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = None
    if text is not None and line in text:
        # What install-hyprland.sh appended, as whole lines: its comment and
        # the dofile line, plus the blank line before them if it is still
        # there. Anchored on lines, so no neighbouring line is ever joined.
        new = re.sub(r"(?m)(?:^\n)?^-- Kokoro read-aloud hotkeys \(generated by [^\n]*\)\.\n"
                     + re.escape(line) + r"(?:\n|\Z)", "", text)
        new = re.sub(r"(?m)^" + re.escape(line) + r"(?:\n|\Z)", "", new)
        backup = f"{main}.bak.{int(time.time())}"
        shutil.copy2(main, backup)
        with open(main, "w", encoding="utf-8") as f:
            f.write(new)
        removed.append(f"the Kokoro lines in {main} (backup: {backup})")
    if os.path.exists(dest):
        os.remove(dest)
        removed.append(dest)
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        subprocess.run(["hyprctl", "reload"], capture_output=True)
        errors = subprocess.run(["hyprctl", "configerrors"], capture_output=True,
                                text=True).stdout.strip()
        if errors:
            print(f"Hyprland reports config errors after the change:\n{errors}",
                  file=sys.stderr)


def uninstall(assume_yes=False, purge=False):
    """Remove everything setup installed. The code folder is left alone:
    it is the Omarchy plugin (omarchy plugin remove) or a git clone."""
    interactive = sys.stdin.isatty()
    if not assume_yes:
        if not interactive:
            print("uninstall removes the venv, the model and the services; "
                  "pass --yes to confirm", file=sys.stderr)
            return 2
        ans = input(f"Remove Kokoro read-aloud's services, hotkeys, venv and model "
                    f"(everything in {paths.DATA})? [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("nothing removed")
            return 1

    removed, kept = [], []
    subprocess.run(["systemctl", "--user", "stop", UNIT + ".service"], capture_output=True)
    subprocess.run(["systemctl", "--user", "disable", "--now", *SERVICES], capture_output=True)
    units = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                         "systemd", "user")
    for name in SERVICES:
        f = os.path.join(units, name)
        try:
            with open(f, encoding="utf-8") as fh:
                ours = "Kokoro read-aloud" in fh.read()
        except OSError:
            continue
        if ours:
            os.remove(f)
            removed.append(f)
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    dropin = os.path.join(runtime, "systemd", "user", "kokoro-server.service.d")
    if os.path.isdir(dropin):
        shutil.rmtree(dropin)
        removed.append(dropin + " (CPU mode)")
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    subprocess.run(["systemctl", "--user", "reset-failed", *SERVICES], capture_output=True)

    for script in ("tts_server.py", "overlay.py", "tray.py"):
        for pid in _our_pids(script):
            try:
                os.kill(pid, 15)
            except OSError:
                pass

    apps = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                        "applications")
    entry = os.path.join(apps, "kokoro-settings.desktop")
    if os.path.exists(entry):
        os.remove(entry)
        removed.append(entry)
        if shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", apps], capture_output=True)

    _unhook_hyprland(removed)

    for path, what in ((paths.DATA, "venv, Python, model, uv and its cache"),
                       (paths.STATE, "logs and calibration")):
        if os.path.basename(path) == paths.APP and os.path.exists(path):
            _rmtree(path)
            removed.append(f"{path} ({what})")

    if os.path.exists(paths.CONFIG):
        drop = purge
        if not purge and interactive and not assume_yes:
            drop = input(f"Also delete your settings ({paths.CONFIG})? [y/N] "
                         ).strip().lower() in ("y", "yes")
        if drop:
            _rmtree(paths.CONFIG)
            removed.append(f"{paths.CONFIG} (settings)")
        else:
            kept.append(f"{paths.CONFIG} (your settings; --purge removes them)")

    hf = os.path.expanduser("~/.cache/huggingface/hub/models--hexgrad--Kokoro-82M")
    if os.path.exists(hf):
        kept.append(f"{hf} ({_size(hf, follow=True) / 2**20:.0f} MiB, downloaded before setup "
                    "existed; the Hugging Face cache is shared with other apps)")

    print("Removed:")
    print("\n".join(f"  {r}" for r in removed) or "  nothing")
    if kept:
        print("Kept:")
        print("\n".join(f"  {k}" for k in kept))
    plugins = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                           "omarchy", "plugins")
    if os.path.realpath(ROOT).startswith(os.path.realpath(plugins) + os.sep):
        print(f"\nTo remove the code and the bar widget: omarchy plugin remove "
              f"{os.path.basename(ROOT)}")
    else:
        print(f"\nThe code is still in {ROOT}; delete that folder to remove it too.")
    return 0
