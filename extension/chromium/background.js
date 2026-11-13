// Relay for the content script: MV3 content scripts are bound by page CORS,
// but the extension context may fetch http://127.0.0.1:5111 (host_permissions).
//
// It answers a frame only while the text being read IS that frame's
// selection. Any program can make the server speak (a terminal, a password
// manager's clipboard), and a page can hold a selection of its own choosing,
// such as a hidden list of digits. Relaying every read to every frame let
// such a page watch which of its words lit up (CSS.highlights is shared with
// the page). The check runs here, in the extension's own process, so the
// text of any other read never reaches a page's process at all.

const SERVER = "http://127.0.0.1:5111";

// Share of words that must agree, in reading order, both ways. What the
// hotkey sends can differ from Selection.toString() by a few words (a copy
// adds images' alt text, toString() does not), and at 0.9 a page must
// already know nine words in ten of what is being read before it is shown
// anything.
const SAME = 0.9;

const norm = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
const words = (s) =>
  [...s.matchAll(/[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu)].map((m) => norm(m[0]));

// Words of `said` found in `sel` in reading order: the same bounded forward
// scan content.js uses to place the highlight.
function matched(sel, said) {
  let c = 0;
  let hits = 0;
  for (const w of said) {
    const stop = Math.min(c + 12, sel.length);
    for (let i = c; i < stop; i++) {
      if (sel[i] === w) { hits++; c = i + 1; break; }
    }
  }
  return hits;
}

let spoken = null; // {utt, words} of the utterance last fetched

async function get(path) {
  return (await fetch(SERVER + path)).json();
}

async function relay(selection) {
  const idle = { active: false };
  if (typeof selection !== "string") return idle;
  const d = await get("/now");
  if (!d.active) return idle;
  if (!spoken || spoken.utt !== d.utt) {
    const u = await get("/utterance");
    if (u.utt !== d.utt) return idle; // a new read began between the calls
    spoken = { utt: u.utt, words: words(u.text) };
  }
  const sel = words(selection);
  const need = SAME * Math.max(sel.length, spoken.words.length);
  if (!spoken.words.length || matched(sel, spoken.words) < need) return idle;
  return { active: true, text: d.text, words: d.words, word: d.word };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "now") {
    relay(msg.selection).then(sendResponse, () => sendResponse(null));
    return true; // keep sendResponse alive for the async fetch
  }
});
