# Every browser tab could talk to our "local" server

*How three web-page attacks were measured against Kokoro Read-Aloud's
localhost server, and how they were closed. 2026-10-18.*

Kokoro Read-Aloud keeps a text-to-speech model loaded in a small HTTP server.
You press a hotkey, a script sends the selected text to
`http://127.0.0.1:5111/speak`, and the server reads it aloud. Other parts ask
the server what it is saying (`GET /now`, `GET /utterance`) so they can show
captions, or tint the spoken word.

The server listens only on 127.0.0.1, so no other machine can reach it. It
is easy to stop there and call it private. It isn't, because there is one
program on the machine that runs code from every website you visit: the
browser. A page in any tab can make requests to 127.0.0.1 too.

This page shows what such a page could do, measured in a real browser before
and after the fix.

## How it was measured

Guesses about browser behaviour are easy to get wrong, so every attack was
run in a real browser:
- **The browser:** Chromium 152, started headless with a throwaway profile
  and driven over the DevTools protocol from a small Node script.
- **An attacker's site:** `--host-resolver-rules="MAP evil.test 127.0.0.1"`
  makes the hostname `evil.test` resolve to this machine. An ordinary page
  was served at `http://evil.test:8765/attack.html`. To the browser that is a
  different site from `127.0.0.1:5111`, as an attacker's real site would be.
- **The extension,** Kokoro's Chromium in-page highlighter, was loaded too,
  to check that its own requests still work.
- **What to watch:** the DevTools protocol records the exact request headers
  the browser sent. The server's state was checked from a separate local
  program. The audio went to a silent output.

The page-side code is a few lines of `fetch`, quoted below.

## Attack 1: the cross-site "simple" request

**What it is.** The same-origin policy stops a page from *reading* the
response from another site. It does not stop the page from *sending* certain
requests there. A GET, or a POST whose body is plain text, a form or
multipart data, is a "simple" request. The browser sends it without asking
anyone, and only hides the answer from the page.

So a page can do this:

```js
fetch("http://127.0.0.1:5111/speak", {
  method: "POST",
  mode: "no-cors",
  body: JSON.stringify({text: "A web page made this machine speak."}),
});
```

The browser sent it with:

```text
Origin: http://evil.test:8765
Sec-Fetch-Site: cross-site
Content-Type: text/plain;charset=UTF-8
```

**Before the fix,** the server ignored the content type: it parsed any body
as JSON. A new read started, and the machine spoke the page's sentence. The
same kind of request to `/config` changed the pause between sentences from
0.1 to 0.15 s. Any page could make the machine talk, or quietly change its
settings.

**After the fix,** both are refused with `403`, and the server logs why:

```text
refused (origin): POST /speak host='127.0.0.1:5111' origin='http://evil.test:8765' site='cross-site' type='text/plain'
```

## Attack 2: the preflight, a defence that already worked

**What it is.** A page can only send a non-simple request after asking
permission. Sending JSON with `Content-Type: application/json` is one. The
browser first sends an `OPTIONS` request, the "preflight". It sends the real
request only if the server answers with `Access-Control-Allow-*` headers that
say yes.

```js
fetch("http://127.0.0.1:5111/speak", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({text: "Preflighted."}),
});
```

**Before the fix,** the browser sent the `OPTIONS`, got no permission back,
and gave up. The page saw `TypeError: Failed to fetch`, and the POST itself
never left the browser. Reading `/utterance` from the page failed the same
way: the server never sent `Access-Control-Allow-Origin`, so the browser
hid the response.

That is the useful lesson of attack 2. A request the page cannot send
without a preflight is safe, as long as the server never grants one. The fix
builds on it: the server now requires `application/json` on every POST. A
page can only send that by asking first, and the answer is always no.

**After the fix,** the `OPTIONS` itself is refused (`403`, on its `Origin`),
and still no `Access-Control-*` header is sent anywhere.

## Attack 3: DNS rebinding

**What it is.** Attack 2 relies on the browser treating the page and the
server as different origins. DNS rebinding makes them the same origin:
1. **The victim opens the attacker's page,** at
   `http://evil.test:5111/`. Its DNS answer points to the attacker's own
   server and has a very short lifetime.
2. **The attacker switches the DNS answer** to `127.0.0.1`.
3. **The page's script fetches `/utterance`.** The browser looks up
   `evil.test` again, gets `127.0.0.1`, and connects to *our* server. The
   origin, `http://evil.test:5111`, is the same as the page's, so the browser
   lets the page read the response.

The test skips the switch: `evil.test` already resolves to 127.0.0.1. The
page was opened at `http://evil.test:5111/now`, and from there it ran:

```js
fetch("/utterance").then(r => r.text());
```

The browser sent:

```text
Host: evil.test:5111
```

There was **no `Origin` and no `Sec-Fetch-*` header.** The request is
same-origin, so no Origin is needed. And Chromium sends the Fetch metadata
headers only to "potentially trustworthy" URLs: https, or 127.0.0.1 and
localhost by name. Plain http to `evil.test` is neither.

**Before the fix,** the page got `200` and the text being read aloud:

```text
{"text":"A web page made this machine speak.","utt":72}
```

That text came from attack 1. In real use it would be whatever you had just
selected and asked to hear: an email, a document, a password reset message.

**After the fix:**

```text
403 {"error":"refused: host","ok":false}
```

The `Host` header is the one thing that still carries the attacker's name,
so it is the check that stops rebinding. A server that checked only `Origin`
or `Sec-Fetch-Site` would have answered.

## The fix

One check runs before every request (`_only_local_programs` in
`tts_server.py`). It refuses:

| Rule | Status | Stops |
|---|---|---|
| `Host` is not `127.0.0.1:5111` or `localhost:5111` | 403 | DNS rebinding (attack 3) |
| An `Origin` from a web page, including `null` from sandboxed frames. Browser extensions may only read | 403 | Attack 1, every preflight, cross-origin reads |
| `Sec-Fetch-Site: cross-site` or `same-site` | 403 | Requests a browser itself says came from another site, even ones without an Origin |
| A POST that is not `application/json` | 415 | Everything a page can send without a preflight |

And two rules about responses:
- **No `Access-Control-*` header is ever sent,** so no preflight is ever
  granted.
- **No GET changes anything.** A GET can be triggered by an image tag, with
  no script at all. The one GET that used to change state,
  `/devices?refresh=1`, is gone; a re-scan is a POST now.

**The extension needs no exemption.** Its background worker sent no `Origin`,
and `Sec-Fetch-Site: none`, the value the Fetch Metadata spec uses for
requests the user started. Its permission for `127.0.0.1:5111` also exempts
the worker from CORS. It got `200` from `/now` before and after the
fix.

## Checking that nothing else broke

Every legitimate client is a local program, or that extension. None of them
sends an `Origin`. Two of them sent POSTs that the new rules refuse:
- the stop hotkey on Linux posted with no body;
- the settings panel's Rescan button used the old GET.

Both now send JSON. Then every client was run against the restarted server:
- the Linux hotkey script, for selection and stop;
- the Omarchy bar widget, which polls and stops through Qt's XMLHttpRequest;
- `kokoroctl`;
- the settings panel's calls;
- the extension in Chromium.

The server log showed no refusals. The Windows hotkeys (AutoHotkey) were
checked by reading the code: WinHTTP sends JSON with no `Origin`.

## Tests that replay the attacks

`tests/test_server.py` turns the measurements into permanent tests, using
the exact headers Chromium sent:
- **18 run in-process,** with Flask's test client and a stand-in for the
  audio player, so no model is needed. They cover every attack above, plus
  `Origin: null`, form and multipart bodies, `Sec-Fetch-Site` on its own,
  "extensions may only read", "a GET changes nothing", each client's own
  headers, and "no `Access-Control-Allow-Origin` on any response".
- **2 replay the attacks** against the running server over a real socket.
  They send empty text, so even a server that wrongly accepted them would
  stay silent.

## What is still true

- **Any program running as you can use the server.** There is no token or
  password. That is the same trust as anything else you run, and this fix
  does not change it.
- **Browsers are adding their own protection.** Chrome's Local Network
  Access asks before a public site may reach the local machine. Not every
  browser does this, and the server does not rely on it.
- **The test page was itself on a loopback address,** so it measures the
  server's defence, not the browsers' protections.

## If you run a server on localhost

- **Check `Host`.** It is the only thing that defeats DNS rebinding.
- **Refuse requests that carry a web-page `Origin`,** unless a web page is
  meant to use the server.
- **Require `application/json` on writes,** and never send CORS headers you
  don't need. The browser's preflight then does the rest.
- **Never change state on a GET.**
- **Test in a real browser.** Header behaviour differs by browser, by scheme
  and by host. A guess would have missed attack 3's missing headers.
