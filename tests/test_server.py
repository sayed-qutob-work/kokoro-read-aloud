r"""Tests for tts_server's request guard: the web-page attacks measured in
AUDIT 2026-10-18 must be refused, and every real client's requests must pass.

    Linux:   ~/.local/share/kokoro-read-aloud/venv/bin/python -B tests/test_server.py
    Windows: env\Scripts\python.exe -B tests\test_server.py

GuardTests run in-process against Flask's test client with a stand-in
player: no model, no audio, no server needed. LiveServerTests send the same
attacks over a real socket to the server on 127.0.0.1:5111, and are skipped
when it is not running. They never make it speak or change a setting: the
attacks carry an empty text or an empty config, so even a server that wrongly
accepted them would do nothing.

The header sets are the ones measured: what a real Chromium sent for each
attack (driven over the DevTools protocol), and what each client of ours
sends, read from its code.

Another account on the same machine (AUDIT 2026-11-14): AccountTests stub
the kernel lookup, LookupTests run both lookups on a real connection, and
OtherAccountTests serve the app on a free port and call it from a real
other uid, the first of this account's subordinate uids (unshare
--map-auto, /etc/subuid), in the same network: reading /utterance, and the
hotkey script facing a port that account holds. Skipped without them.
"""

import http.client
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.dont_write_bytecode = True     # on Linux nothing may be written next to the code
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tts_server                  # noqa: E402  (the model loads only under __main__)

HOST = "127.0.0.1:5111"
PAGE = "http://evil.test:8765"     # an ordinary web page somewhere else

# What a real Chromium 152 sent, per attack (AUDIT 2026-10-18).
SIMPLE_POST = {"Origin": PAGE, "Sec-Fetch-Site": "cross-site",
               "Content-Type": "text/plain;charset=UTF-8"}
PREFLIGHT = {"Origin": PAGE, "Sec-Fetch-Site": "cross-site",
             "Access-Control-Request-Method": "POST",
             "Access-Control-Request-Headers": "content-type"}
CROSS_ORIGIN_GET = {"Origin": PAGE, "Sec-Fetch-Site": "cross-site"}
# DNS rebinding: evil.test now resolves to 127.0.0.1, the page is same-origin,
# and over plain http the browser sends no Origin and no Sec-Fetch-* at all.
REBOUND = {"Host": "evil.test:5111"}

# What our own clients send.
JSON = {"Content-Type": "application/json"}                      # curl, urllib, QML
AHK_JSON = {"Content-Type": "application/json; charset=utf-8"}   # WinHTTP
EXTENSION_WORKER = {"Sec-Fetch-Site": "none"}                    # measured: no Origin
USER_TYPED_URL = {"Sec-Fetch-Site": "none"}


def as_own_account(test):
    """Flask's test client has no socket, so no account to look up: say the
    request comes from this account, as every real client of ours does."""
    p = mock.patch.object(tts_server, "_peer_uid", lambda: tts_server.MY_UID)
    p.start()
    test.addCleanup(p.stop)


class FakeEngine:
    name, voice, model_speed, device = "kokoro", "af_heart", 1.15, "cpu"


class FakePlayer:
    """Just enough of tts_server.Player for the routes."""

    def __init__(self):
        self.calls = []
        self.engine = FakeEngine()
        self.now, self.gen, self.chunk_seq, self.play_pos = None, 0, [], -1
        self.source_gen, self.source = 1, "a private document"
        self.device_spec, self.device_name = None, "default"
        self.density, self.rt, self.rt_known = 0.0, 2.0, False

    def speak(self, text):
        self.calls.append(("speak", text))
        return 1

    def stop(self):
        self.calls.append(("stop",))

    def set_device(self, want):
        self.calls.append(("set_device", want))

    def refresh_devices(self):
        self.calls.append(("refresh_devices",))


class GuardTests(unittest.TestCase):

    def setUp(self):
        as_own_account(self)
        self.player = tts_server.player = FakePlayer()
        tts_server.list_output_devices = lambda: []
        self.client = tts_server.app.test_client()

    def call(self, method, path, headers=None, body=None):
        h = {"Host": HOST}
        h.update(headers or {})
        r = self.client.open(path, method=method, headers=h, data=body,
                             base_url="http://" + h["Host"])
        self.assertNotIn("Access-Control-Allow-Origin", r.headers,
                         f"{method} {path} granted CORS")
        return r

    # ---- the attacks

    def test_cross_site_simple_post_cannot_speak(self):
        r = self.call("POST", "/speak", SIMPLE_POST, '{"text": "from a page"}')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.player.calls, [])

    def test_cross_site_simple_post_cannot_change_settings(self):
        before = tts_server.SENTENCE_PAUSE
        r = self.call("POST", "/config", SIMPLE_POST, '{"pause": 9}')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(tts_server.SENTENCE_PAUSE, before)

    def test_every_post_endpoint_refuses_a_page(self):
        for path in ("/speak", "/stop", "/config", "/devices"):
            with self.subTest(path=path):
                r = self.call("POST", path, SIMPLE_POST, "{}")
                self.assertEqual(r.status_code, 403)
        self.assertEqual(self.player.calls, [])

    def test_preflight_is_never_granted(self):
        for path in ("/speak", "/config", "/now"):
            with self.subTest(path=path):
                r = self.call("OPTIONS", path, PREFLIGHT)
                self.assertEqual(r.status_code, 403)

    def test_page_cannot_read_cross_origin(self):
        for path in ("/now", "/utterance", "/config", "/devices"):
            with self.subTest(path=path):
                r = self.call("GET", path, CROSS_ORIGIN_GET)
                self.assertEqual(r.status_code, 403)

    def test_dns_rebinding_cannot_read(self):
        for path in ("/utterance", "/now", "/config"):
            with self.subTest(path=path):
                r = self.call("GET", path, REBOUND)
                self.assertEqual(r.status_code, 403)
                self.assertNotIn(b"private", r.data)

    def test_dns_rebinding_cannot_speak(self):
        r = self.call("POST", "/speak", {**REBOUND, **JSON}, '{"text": "x"}')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.player.calls, [])

    def test_sandboxed_page_null_origin(self):
        r = self.call("POST", "/speak", {"Origin": "null", **JSON}, '{"text": "x"}')
        self.assertEqual(r.status_code, 403)

    def test_sec_fetch_site_alone_refuses(self):
        for site in ("cross-site", "same-site"):
            with self.subTest(site=site):
                r = self.call("GET", "/utterance", {"Sec-Fetch-Site": site})
                self.assertEqual(r.status_code, 403)

    def test_post_must_be_json(self):
        # what a page can send without a preflight, even with no Origin
        for ctype in ("text/plain", "application/x-www-form-urlencoded",
                      "multipart/form-data; boundary=x", None):
            with self.subTest(ctype=ctype):
                r = self.call("POST", "/speak",
                              {"Content-Type": ctype} if ctype else {},
                              '{"text": "x"}')
                self.assertEqual(r.status_code, 415)
        self.assertEqual(self.player.calls, [])

    def test_extensions_may_only_read(self):
        ext = {"Origin": "chrome-extension://abcdefghijklmnop"}
        self.assertEqual(self.call("GET", "/now", ext).status_code, 200)
        r = self.call("POST", "/speak", {**ext, **JSON}, '{"text": "x"}')
        self.assertEqual(r.status_code, 403)

    def test_get_changes_nothing(self):
        self.call("GET", "/devices?refresh=1")
        self.call("GET", "/config?voice=bf_emma&pause=9")
        self.assertEqual(self.player.calls, [])
        self.assertEqual(self.player.engine.voice, "af_heart")

    # ---- our own clients still work

    def test_hotkey_script_and_kokoroctl(self):
        r = self.call("POST", "/speak", JSON, '{"text": "selected text"}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.call("POST", "/stop", JSON, "{}").status_code, 200)
        self.assertEqual(self.player.calls, [("speak", "selected text"), ("stop",)])

    def test_autohotkey(self):
        r = self.call("POST", "/speak", AHK_JSON, '{"text": "selected text"}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.call("POST", "/stop", AHK_JSON, "{}").status_code, 200)

    def test_settings_panel_and_widget(self):
        r = self.call("POST", "/config", JSON, '{"voice": "bf_emma"}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.player.engine.voice, "bf_emma")
        self.assertEqual(self.call("POST", "/devices", JSON, "{}").status_code, 200)
        self.assertIn(("refresh_devices",), self.player.calls)

    def test_readers(self):
        # caption strip, highlighter, widget, extension, a URL typed by hand
        for path in ("/now", "/utterance", "/config", "/devices"):
            for h in ({}, EXTENSION_WORKER, USER_TYPED_URL):
                with self.subTest(path=path, headers=h):
                    self.assertEqual(self.call("GET", path, h).status_code, 200)

    def test_localhost_by_name(self):
        r = self.call("GET", "/config", {"Host": "localhost:5111"})
        self.assertEqual(r.status_code, 200)


class ConfigTests(unittest.TestCase):
    """POST /config clamps model_speed the way the boot path does: past 1.3
    Kokoro garbles (AUDIT S6), and the UIs never ask for more."""

    def setUp(self):
        as_own_account(self)
        self.player = tts_server.player = FakePlayer()
        self.client = tts_server.app.test_client()

    def post(self, value):
        r = self.client.post("/config", json={"model_speed": value},
                             base_url="http://" + HOST)
        self.assertEqual(r.status_code, 200)
        return r.get_json()["model_speed"]

    def test_model_speed_is_capped(self):
        self.assertEqual(self.post(2.0), 1.3)
        self.assertEqual(self.post(0.1), 0.5)
        self.assertEqual(self.post(1.15), 1.15)


LINUX_ONLY = unittest.skipUnless(tts_server.CHECK_ACCOUNT,
                                 "the account check is Linux only")


@LINUX_ONLY
class AccountTests(unittest.TestCase):
    """A request from another account is refused whatever it asks for
    (marketplace review, AUDIT 2026-11-14)."""

    def setUp(self):
        self.player = tts_server.player = FakePlayer()
        tts_server.list_output_devices = lambda: []
        self.client = tts_server.app.test_client()

    def calls_as(self, uid):
        p = mock.patch.object(tts_server, "_peer_uid", lambda: uid)
        p.start()
        self.addCleanup(p.stop)
        out = []
        for method, path, body in (("GET", "/utterance", None), ("GET", "/now", None),
                                   ("GET", "/config", None), ("GET", "/devices", None),
                                   ("POST", "/speak", '{"text": "x"}'),
                                   ("POST", "/stop", "{}"),
                                   ("POST", "/config", '{"voice": "bf_emma"}')):
            r = self.client.open(path, method=method, data=body, headers={
                "Host": HOST, **(JSON if body else {})}, base_url="http://" + HOST)
            out.append((method, path, r.status_code, r.get_json()))
        return out

    def test_another_account_refused(self):
        for method, path, status, body in self.calls_as(tts_server.MY_UID + 1):
            with self.subTest(path=path, method=method):
                self.assertEqual(status, 403)
                self.assertIn("another account", body["error"])
                self.assertNotIn("private", str(body))
        self.assertEqual(self.player.calls, [])
        self.assertEqual(self.player.engine.voice, "af_heart")

    def test_unknown_owner_refused(self):
        # no socket, or the kernel would not say: fail closed
        for method, path, status, _ in self.calls_as(None):
            with self.subTest(path=path, method=method):
                self.assertEqual(status, 403)
        self.assertEqual(self.player.calls, [])

    def test_own_account_allowed(self):
        for method, path, status, _ in self.calls_as(tts_server.MY_UID):
            with self.subTest(path=path, method=method):
                self.assertEqual(status, 200)


@LINUX_ONLY
class LookupTests(unittest.TestCase):
    """Both kernel lookups, on a real loopback connection."""

    def setUp(self):
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.cli = socket.create_connection(self.srv.getsockname())
        self.conn, _ = self.srv.accept()
        for s in (self.srv, self.cli, self.conn):
            self.addCleanup(s.close)

    def test_finds_the_client_end(self):
        client, server = self.cli.getsockname(), self.srv.getsockname()
        for lookup in (tts_server._socket_uid_netlink, tts_server._socket_uid_proc):
            with self.subTest(lookup=lookup.__name__):
                self.assertEqual(lookup(client, server), os.getuid())

    def test_no_such_connection(self):
        server = self.srv.getsockname()
        nobody = ("127.0.0.1", 1)          # a port nothing connects from
        for lookup in (tts_server._socket_uid_netlink, tts_server._socket_uid_proc):
            with self.subTest(lookup=lookup.__name__):
                self.assertIsNone(lookup(nobody, server))

    def test_proc_skips_time_wait(self):
        head = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        row = ("   0: 0100007F:D2A4 0100007F:13F7 {st} 00000000:00000000 "
               "00:00000000 00000000  {uid}        0 0 1 0000000000000000\n")
        with tempfile.NamedTemporaryFile("w", suffix=".tcp", delete=False) as f:
            f.write(head + row.format(st="06", uid=0))
        self.addCleanup(os.unlink, f.name)
        client, server = ("127.0.0.1", 0xD2A4), ("127.0.0.1", 0x13F7)
        if socket.htonl(1) == 1:
            self.skipTest("table written for a little-endian host")
        self.assertIsNone(tts_server._socket_uid_proc(client, server, f.name))
        with open(f.name, "a") as t:
            t.write(row.format(st="01", uid=1000))
        self.assertEqual(tts_server._socket_uid_proc(client, server, f.name), 1000)


def other_account(*cmd):
    """cmd as another kernel uid in this network namespace: root of a new
    user namespace is the first of this account's subordinate uids
    (/etc/subuid), which is not this account."""
    return ["unshare", "--user", "--map-auto", "--setuid", "0", "--setgid", "0",
            "--", *cmd]


def other_account_available():
    if not (tts_server.CHECK_ACCOUNT and shutil.which("unshare") and shutil.which("curl")):
        return False
    try:
        r = subprocess.run(other_account("sh", "-c", "id -u"), cwd="/",
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and r.stdout.strip() == "0"


@unittest.skipUnless(other_account_available(),
                     "needs Linux, curl and unshare --map-auto (an /etc/subuid range)")
class OtherAccountTests(unittest.TestCase):
    """The app on a real socket, called by a real other account: what the
    reviewer described, as measured in AUDIT 2026-11-14."""

    @classmethod
    def setUpClass(cls):
        from werkzeug.serving import make_server
        cls.player = tts_server.player = FakePlayer()
        tts_server.list_output_devices = lambda: []
        cls.server = make_server("127.0.0.1", 0, tts_server.app, threaded=True)
        cls.port = cls.server.server_port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def curl(self, path, as_other):
        cmd = ["curl", "-s", "-m", "5", "-H", f"Host: {HOST}",
               "-w", "\n%{http_code}", f"http://127.0.0.1:{self.port}{path}"]
        r = subprocess.run(other_account(*cmd) if as_other else cmd, cwd="/",
                           capture_output=True, text=True, timeout=10)
        body, _, status = r.stdout.rpartition("\n")
        return int(status), body

    def test_another_account_cannot_read(self):
        status, body = self.curl("/utterance", as_other=True)
        self.assertEqual(status, 403)
        self.assertNotIn("private", body)
        self.assertIn("another account", body)

    def test_own_account_reads(self):
        status, body = self.curl("/utterance", as_other=False)
        self.assertEqual(status, 200)
        self.assertIn("a private document", body)

    # read_aloud.sh, copied next to a stand-in xselection.py that "reads" a
    # fixed text, with notify-send stubbed: no real selection, no popup.
    TEXT = "The code is four eight two one"

    def hotkey(self, port):
        if not shutil.which("jq"):
            self.skipTest("read_aloud.sh needs jq")
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        os.makedirs(os.path.join(d, "linux"))
        os.makedirs(os.path.join(d, "bin"))
        shutil.copy(os.path.join(ROOT, "read_aloud.sh"), d)
        with open(os.path.join(d, "linux", "xselection.py"), "w") as f:
            f.write(f"import sys\nsys.stdout.write({self.TEXT!r})\n")
        with open(os.path.join(d, "bin", "notify-send"), "w") as f:
            f.write(f'#!/bin/sh\necho "$*" >> {d}/notes\n')
        os.chmod(os.path.join(d, "bin", "notify-send"), 0o755)
        env = {**os.environ, "KOKORO_SERVER": f"http://127.0.0.1:{port}",
               "KOKORO_READ_LOG": os.path.join(d, "log"), "XDG_RUNTIME_DIR": d,
               "PATH": os.path.join(d, "bin") + os.pathsep + os.environ["PATH"]}
        r = subprocess.run([os.path.join(d, "read_aloud.sh"), "clipboard"],
                           env=env, capture_output=True, text=True, timeout=20)
        try:
            with open(os.path.join(d, "notes")) as f:
                return r.returncode, f.read()
        except FileNotFoundError:
            return r.returncode, ""

    def test_hotkey_sends_nothing_to_another_accounts_port(self):
        # the server is off and another account listens on its port
        squatter = subprocess.Popen(other_account(
            "python3", "-I", "-c",
            "import socket\n"
            "s = socket.create_server(('127.0.0.1', 0)); print(s.getsockname()[1], flush=True)\n"
            "while True:\n"
            "    c, _ = s.accept(); print('GOT', c.recv(65536), flush=True); c.close()\n"),
            cwd="/", stdout=subprocess.PIPE, text=True)
        self.addCleanup(squatter.kill)
        port = int(squatter.stdout.readline())
        rc, notes = self.hotkey(port)
        squatter.kill()
        got = squatter.stdout.read()
        squatter.stdout.close()
        self.assertEqual(rc, 1)
        self.assertNotIn("GOT", got)
        self.assertIn("Another account", notes)

    def test_hotkey_reads_with_own_server(self):
        hosts = {f"127.0.0.1:{self.port}"}
        with mock.patch.object(tts_server, "ALLOWED_HOSTS", tts_server.ALLOWED_HOSTS | hosts):
            rc, notes = self.hotkey(self.port)
        self.assertEqual((rc, notes), (0, ""))
        self.assertIn(("speak", self.TEXT), self.player.calls)


class LiveServerTests(unittest.TestCase):
    """The same attacks against the running server, over a real socket."""

    @classmethod
    def setUpClass(cls):
        try:
            c = http.client.HTTPConnection("127.0.0.1", 5111, timeout=2)
            c.request("GET", "/config")
            c.getresponse().read()
        except OSError:
            raise unittest.SkipTest("no server on 127.0.0.1:5111")

    def call(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", 5111, timeout=3)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        r.read()
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))
        return r.status

    def test_attacks_refused(self):
        # empty text / empty config: harmless even if wrongly accepted
        self.assertEqual(self.call("POST", "/speak", SIMPLE_POST, '{"text": " "}'), 403)
        self.assertEqual(self.call("POST", "/config", SIMPLE_POST, "{}"), 403)
        self.assertEqual(self.call("OPTIONS", "/speak", PREFLIGHT), 403)
        self.assertEqual(self.call("GET", "/utterance", CROSS_ORIGIN_GET), 403)
        self.assertEqual(self.call("GET", "/utterance", REBOUND), 403)
        self.assertEqual(self.call("POST", "/speak", {"Content-Type": "text/plain"},
                                   '{"text": " "}'), 415)

    def test_readers_allowed(self):
        self.assertEqual(self.call("GET", "/config"), 200)
        self.assertEqual(self.call("GET", "/now", EXTENSION_WORKER), 200)
        self.assertEqual(self.call("GET", "/now", {"Host": "localhost:5111"}), 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
