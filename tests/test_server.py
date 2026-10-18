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
"""

import http.client
import os
import sys
import unittest

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


class FakeEngine:
    voice, model_speed, device = "af_heart", 1.15, "cpu"


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
