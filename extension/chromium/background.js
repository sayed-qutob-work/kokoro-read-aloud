// Relay for the content script: MV3 content scripts are bound by page CORS,
// but the extension context may fetch http://127.0.0.1:5111 (host_permissions).
//
// It answers a frame only while the text being read IS that frame's
// selection, word for word, and only if that frame was let in during the
// read: the frame holding the keyboard focus in the visible tab, judged
// once per tab and read. Any program can make the server speak (a terminal,
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

// One verdict per tab and read: tab id -> the frame let in, or null when the
// tab's first try was refused. Without it a page with the focus could swap
// its selection between polls and test a guess of the whole read every
// 400 ms; now it gets one guess per read. The verdict waits for a frame that
// holds the focus, and a frame let in keeps the read when the focus leaves,
// so a read begun in the browser keeps its highlight if you switch away.
let verdicts = { utt: null, tabs: new Map() };

async function get(path) {
  return (await fetch(SERVER + path)).json();
}

// msg.selection: the selection's text as each kind of read would hold it
// (content.js); msg.focused: whether that frame holds the keyboard focus.
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
  const tab = sender.tab?.id;
  if (tab === undefined) return idle;
  const frame = `${sender.frameId}/${sender.documentId}`;
  const read = spoken.words.length > 0 &&
    texts.some((t) => same(words(t), spoken.words));
  if (verdicts.utt !== d.utt) verdicts = { utt: d.utt, tabs: new Map() };
  if (!verdicts.tabs.has(tab)) {
    if (msg.focused !== true || !sender.tab.active) return idle; // no verdict yet
    verdicts.tabs.set(tab, read ? frame : null);
  }
  if (verdicts.tabs.get(tab) !== frame || !read) return idle;
  return { active: true, text: d.text, words: d.words, word: d.word };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "now") {
    relay(msg, sender).then(sendResponse, () => sendResponse(null));
    return true; // keep sendResponse alive for the async fetch
  }
});
