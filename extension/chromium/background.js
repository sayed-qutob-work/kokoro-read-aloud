// Relay for the content script: MV3 content scripts are bound by page CORS,
// but the extension context may fetch http://127.0.0.1:5111 (host_permissions).
//
// It answers a frame only while the text being read IS that frame's
// selection, word for word, and only once that frame has had the keyboard
// focus during the read. Any program can make the server speak (a terminal,
// a password manager's clipboard), and a page can hold a selection of its
// own choosing. Relaying every read to every frame let such a page watch
// which of its words lit up; an overlap of 90% still let a page that knew
// most of a message learn the word it did not know (marketplace review,
// AUDIT 2026-11-13 and 2026-11-14). The check runs here, in the extension's
// own process, so the text of any other read never reaches a page's process.

const SERVER = "http://127.0.0.1:5111";

const norm = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
const words = (s) =>
  [...s.matchAll(/[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu)].map((m) => norm(m[0]));
const same = (a, b) => a.length === b.length && a.every((w, i) => w === b[i]);

let spoken = null; // {utt, words} of the utterance last fetched
// Frames let in during the current read. A frame needs the focus to be let
// in, not to stay in: a read begun in the browser keeps its highlight when
// you switch to another window while it plays.
let granted = { utt: null, frames: new Set() };

async function get(path) {
  return (await fetch(SERVER + path)).json();
}

// msg.selection: the selection's text as each kind of read would hold it
// (content.js); msg.focused: document.hasFocus() in that frame.
async function relay(msg, sender) {
  const idle = { active: false };
  const texts = Array.isArray(msg.selection)
    ? msg.selection.filter((t) => typeof t === "string") : [];
  if (!texts.length) return idle;
  const d = await get("/now");
  if (!d.active) return idle;
  if (!spoken || spoken.utt !== d.utt) {
    const u = await get("/utterance");
    if (u.utt !== d.utt) return idle; // a new read began between the calls
    spoken = { utt: u.utt, words: words(u.text) };
  }
  if (granted.utt !== d.utt) granted = { utt: d.utt, frames: new Set() };
  const frame = `${sender.tab?.id}/${sender.frameId}/${sender.documentId}`;
  if (!granted.frames.has(frame) && !(msg.focused === true && sender.tab?.active))
    return idle;
  if (!spoken.words.length || !texts.some((t) => same(words(t), spoken.words)))
    return idle;
  granted.frames.add(frame);
  return { active: true, text: d.text, words: d.words, word: d.word };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "now") {
    relay(msg, sender).then(sendResponse, () => sendResponse(null));
    return true; // keep sendResponse alive for the async fetch
  }
});
