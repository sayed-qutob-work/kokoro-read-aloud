// In-place spoken-word highlighting (Speechify-style) for the local Kokoro
// read-aloud server.
//
// How it works: when you select text and hit the read hotkey, this script
// already holds a snapshot of the selection as one Range per word. It polls
// the server's /now (via the background relay) and, while speech is active,
// aligns the chunk's spoken tokens against the snapshot in reading order and
// paints the current word with the CSS Custom Highlight API - no DOM
// mutation, so it cannot break page layout. Clears itself when speech ends.
//
// Limits: DOM text only - not PDFs in the built-in viewer, not canvas apps
// like Google Docs. Text selected in OTHER apps simply won't match anything
// here, and nothing happens (the overlay still covers those).

const IDLE_MS = 400;
const ACTIVE_MS = 60;
const HL = "kokoro-word";

let wordRanges = [];  // [{range, norm}] snapshot of the last real selection
let cursor = 0;       // next unmatched snapshot word (reading order)
let chunkKey = null;  // /now text of the chunk currently mapped
let chunkMap = [];    // chunk token index -> snapshot index (-1 = no match)
let active = false;
let inactivePolls = 0;

const sheet = new CSSStyleSheet();
sheet.replaceSync(
  `::highlight(${HL}){background:#3d5afe;color:#fff}`);
document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];

const norm = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

document.addEventListener("selectionchange", () => {
  if (active) return; // don't drop the snapshot mid-read
  const sel = document.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return;
  const r = sel.getRangeAt(0);
  const ranges = [];
  const walker = document.createTreeWalker(
    r.commonAncestorContainer, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!r.intersectsNode(node)) continue;
    const a = node === r.startContainer ? r.startOffset : 0;
    const b = node === r.endContainer ? r.endOffset : node.nodeValue.length;
    for (const m of node.nodeValue
        .slice(a, b).matchAll(/[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu)) {
      const wr = document.createRange();
      wr.setStart(node, a + m.index);
      wr.setEnd(node, a + m.index + m[0].length);
      ranges.push({ range: wr, norm: norm(m[0]) });
    }
  }
  if (ranges.length) {
    wordRanges = ranges;
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
    const n = norm(w);
    let found = -1;
    if (n) {
      const stop = Math.min(c + 12, wordRanges.length);
      for (let i = c; i < stop; i++) {
        if (wordRanges[i].norm === n) { found = i; break; }
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
  CSS.highlights.set(HL, new Highlight(wordRanges[s].range));
}

async function poll() {
  let d = null;
  try { d = await chrome.runtime.sendMessage("now"); } catch {}
  if (d && d.active && wordRanges.length) {
    active = true;
    inactivePolls = 0;
    if (d.text !== chunkKey) { chunkKey = d.text; mapChunk(d.words); }
    mark(d.word);
    setTimeout(poll, ACTIVE_MS);
    return;
  }
  if (active && ++inactivePolls >= 3) {
    CSS.highlights.delete(HL);
    active = false;
    chunkKey = null;
    cursor = 0; // re-reading the same selection starts over
  }
  setTimeout(poll, active ? ACTIVE_MS : IDLE_MS);
}

poll();
