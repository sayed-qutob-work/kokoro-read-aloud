// In-place spoken-word highlighting (Speechify-style) for the local Kokoro
// read-aloud server.
//
// How it works: when you select text and hit the read hotkey, this script
// already holds a snapshot of the selection as one Range per word. It polls
// the server's /now (via the background relay) and, while speech is active,
// aligns the chunk's spoken tokens against the snapshot in reading order and
// highlights the current word, its sentence, or both (paint.js draws them).
// The page's own elements are never moved or restyled, so it cannot break
// page layout. Clears itself when speech ends.
//
// The relay (background.js) answers only while the text being read is this
// frame's selection, so text read from other programs never reaches the page.
// A frame with no selection does not poll at all.
//
// Limits: DOM text only - not PDFs in the built-in viewer, not canvas apps
// like Google Docs. Text selected in OTHER apps is never sent here, and
// nothing happens (the overlay still covers those).

const IDLE_MS = 400;
const ACTIVE_MS = 60;

let snap = [];        // Kokoro.words() of the last real selection
let selText = "";     // that selection's text, for the relay's check
let cursor = 0;       // next unmatched snapshot word (reading order)
let chunkKey = null;  // /now text of the chunk currently mapped
let chunkMap = [];    // chunk token index -> snapshot index (-1 = no match)
let current = -1;     // snapshot index of the word being read
let active = false;
let inactivePolls = 0;

let opts = Kokoro.settings();
const painter = new Kokoro.Painter(document);

// While a read is on, the page's selection colour is switched off: the
// highlight is drawn over it, and a blue highlight on a blue selection hid
// the word. The selection itself stays, and shows again when the read ends.
const quiet = new CSSStyleSheet();
quiet.replaceSync(
  "::selection{background-color:transparent!important;color:currentcolor!important}");
function selectionShown(shown) {
  const rest = document.adoptedStyleSheets.filter((s) => s !== quiet);
  document.adoptedStyleSheets = shown ? rest : [...rest, quiet];
}

function redraw() {
  if (current < 0 || current >= snap.length) painter.clear();
  else painter.draw(Kokoro.layers(snap, current, opts), opts);
}

// Settings come from the popup, live.
chrome.storage.local.get("highlight").then((r) => {
  opts = Kokoro.settings(r.highlight);
  redraw();
}, () => {});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.highlight) {
    opts = Kokoro.settings(changes.highlight.newValue);
    redraw();
  }
});

// The boxes sit over the text, so they follow it when anything scrolls.
let queued = false;
const follow = () => {
  if (current < 0 || queued) return;
  queued = true;
  requestAnimationFrame(() => { queued = false; redraw(); });
};
addEventListener("scroll", follow, { capture: true, passive: true });
addEventListener("resize", follow, { passive: true });

document.addEventListener("selectionchange", () => {
  if (active) return; // don't drop the snapshot mid-read
  const sel = document.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return;
  const ws = Kokoro.words(sel.getRangeAt(0));
  if (ws.length) {
    snap = ws;
    selText = sel.toString();
    cursor = 0;
    chunkKey = null;
  }
});

// Align one chunk's spoken tokens to the snapshot. Tokens arrive in reading
// order, so a bounded forward scan from the cursor is enough; sanitized-out
// or unmatched tokens just get -1.
function mapChunk(words) {
  chunkMap = [];
  let c = cursor;
  for (const [w] of words) {
    const n = Kokoro.norm(w);
    let found = -1;
    if (n) {
      const stop = Math.min(c + 12, snap.length);
      for (let i = c; i < stop; i++) {
        if (snap[i].norm === n) { found = i; break; }
      }
    }
    chunkMap.push(found);
    if (found >= 0) c = found + 1;
  }
  cursor = c;
}

function mark(idx) {
  if (idx == null || idx < 0 || idx >= chunkMap.length) return;
  const s = chunkMap[idx];
  if (s < 0) return;
  current = s;
  redraw(); // every poll, so the boxes follow a reflow too
}

async function poll() {
  if (!snap.length) { setTimeout(poll, IDLE_MS); return; }
  let d = null;
  try {
    d = await chrome.runtime.sendMessage({ type: "now", selection: selText });
  } catch {}
  if (d && d.active && snap.length) {
    if (!active) selectionShown(false);
    active = true;
    inactivePolls = 0;
    if (d.text !== chunkKey) { chunkKey = d.text; mapChunk(d.words); }
    mark(d.word);
    setTimeout(poll, ACTIVE_MS);
    return;
  }
  if (active && ++inactivePolls >= 3) {
    current = -1;
    painter.clear();
    selectionShown(true);
    active = false;
    chunkKey = null;
    cursor = 0; // re-reading the same selection starts over
  }
  setTimeout(poll, active ? ACTIVE_MS : IDLE_MS);
}

poll();
