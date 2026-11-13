// Drawing the highlight, shared by the page script (content.js) and the
// settings popup's preview (popup.js).
//
// The highlight is a set of translucent boxes drawn over the text, inside a
// closed shadow root. It used to be the CSS Custom Highlight API, which
// takes colours only: no padding, no rounded corners, so it could only paint
// the glyphs' own tight box. Boxes on top of the text are translucent so the
// text shows through. They never move or restyle the page's own elements.

"use strict";

const Kokoro = (() => {
  const DEFAULTS = { unit: "word", color: "#3d5afe", strength: 35, size: 3 };
  const UNITS = ["word", "sentence", "both"];
  const SENTENCE_SHARE = 0.45; // "both": the sentence at 45% of the word's strength

  // Whatever storage holds, made valid.
  function settings(raw) {
    const s = { ...DEFAULTS };
    if (!raw || typeof raw !== "object") return s;
    if (UNITS.includes(raw.unit)) s.unit = raw.unit;
    if (/^#[0-9a-f]{6}$/i.test(raw.color)) s.color = raw.color.toLowerCase();
    if (Number.isFinite(raw.strength))
      s.strength = Math.min(70, Math.max(10, Math.round(raw.strength)));
    if (Number.isFinite(raw.size))
      s.size = Math.min(10, Math.max(0, Math.round(raw.size)));
    return s;
  }

  const WORD = /[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu;
  const norm = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

  // The words of `range`, in reading order, as [{range, norm, node, at}].
  // `nodes` on the result lists every text node the range touches, words or
  // not, since a sentence's full stop can sit alone in one.
  function words(range) {
    const doc = range.startContainer.ownerDocument;
    const root = range.commonAncestorContainer;
    const nodes = [];
    if (root.nodeType === Node.TEXT_NODE) {
      nodes.push(root); // a selection inside one text node
    } else {
      const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode())
        if (range.intersectsNode(n)) nodes.push(n);
    }
    const out = [];
    for (const node of nodes) {
      const a = node === range.startContainer ? range.startOffset : 0;
      const b = node === range.endContainer ? range.endOffset
                                            : node.nodeValue.length;
      for (const m of node.nodeValue.slice(a, b).matchAll(WORD)) {
        const r = doc.createRange();
        r.setStart(node, a + m.index);
        r.setEnd(node, a + m.index + m[0].length);
        out.push({ range: r, norm: norm(m[0]), node, at: a + m.index });
      }
    }
    out.nodes = nodes;
    return out;
  }

  // ICU, behind Intl.Segmenter, ends a sentence at "Dr. Smith" and at an
  // initial ("J. Smith"); these never end one here.
  const TITLE = /(?:^|[^\p{L}])(?:mrs?|ms|dr|prof|sr|jr|st|mt|rev|gen|col|lt|sgt|capt|hon|vs|no|fig|approx)\.\s*$/iu;
  const INITIAL = /(?:^|[^\p{L}])\p{Lu}\.\s*$/u;

  function blockOf(node, cache) {
    for (let e = node.parentElement; e; e = e.parentElement) {
      let block = cache.get(e);
      if (block === undefined) {
        const d = getComputedStyle(e).display;
        block = !(d.startsWith("inline") || d === "contents");
        cache.set(e, block);
      }
      if (block) return e;
    }
    return null;
  }

  // Number each word's sentence (w.sentence). Done once per read, not per
  // selection change: it asks for computed styles.
  function sentences(ws) {
    if (ws.sentences) return;
    // The text of every node, with a line break where the block changes, so
    // a heading or a list item never runs on into the next one.
    let text = "";
    const start = new Map();
    const cache = new Map();
    let last;
    for (const node of ws.nodes) {
      const block = blockOf(node, cache);
      if (last !== undefined && block !== last) text += "\n";
      last = block;
      start.set(node, text.length);
      text += node.nodeValue;
    }
    const bounds = [];
    let prev = "";
    const seg = new Intl.Segmenter(undefined, { granularity: "sentence" });
    for (const s of seg.segment(text)) {
      if (!bounds.length || !(TITLE.test(prev) || INITIAL.test(prev)))
        bounds.push(s.index);
      prev = s.segment;
    }
    let k = 0;
    for (const w of ws) {
      const at = start.get(w.node) + w.at;
      while (k + 1 < bounds.length && bounds[k + 1] <= at) k++;
      w.sentence = k;
    }
    ws.sentences = true;
  }

  // The boxes to draw for word `i` of `ws`, as layers [{ranges, alpha}].
  function layers(ws, i, opts) {
    const alpha = opts.strength / 100;
    const word = { ranges: [ws[i].range], alpha };
    if (opts.unit === "word") return [word];
    sentences(ws);
    let a = i;
    let b = i;
    while (a > 0 && ws[a - 1].sentence === ws[i].sentence) a--;
    while (b + 1 < ws.length && ws[b + 1].sentence === ws[i].sentence) b++;
    const ranges = ws.slice(a, b + 1).map((w) => w.range);
    if (opts.unit === "sentence") return [{ ranges, alpha }];
    return [{ ranges, alpha: alpha * SENTENCE_SHARE }, word];
  }

  // One rect per line: the words of a sentence on the same line merge, and
  // a word broken over two lines keeps two.
  function lineRects(ranges) {
    const lines = [];
    for (const r of ranges) {
      for (const b of r.getClientRects()) {
        if (!b.width || !b.height) continue;
        const line = lines.find((l) =>
          Math.min(l.bottom, b.bottom) - Math.max(l.top, b.top) >
          0.5 * Math.min(l.bottom - l.top, b.height));
        if (line) {
          line.left = Math.min(line.left, b.left);
          line.right = Math.max(line.right, b.right);
          line.top = Math.min(line.top, b.top);
          line.bottom = Math.max(line.bottom, b.bottom);
        } else {
          lines.push({ left: b.left, right: b.right, top: b.top,
                       bottom: b.bottom });
        }
      }
    }
    return lines;
  }

  // Draws layers in a fixed, click-through overlay at the top of the page.
  // Each layer is one group with the layer's opacity, so the boxes of one
  // sentence never darken where they overlap.
  class Painter {
    constructor(doc) {
      this.doc = doc;
      this.host = null;
    }

    _ensure() {
      if (this.host && this.host.isConnected) return;
      const doc = this.doc;
      const host = doc.createElement("kokoro-highlight");
      host.style.cssText =
        "all:initial!important;display:block!important;" +
        "position:fixed!important;inset:0!important;overflow:hidden!important;" +
        "pointer-events:none!important;z-index:2147483647!important";
      const shadow = host.attachShadow({ mode: "closed" });
      this.groups = [0, 1].map(() => {
        const g = doc.createElement("div");
        g.style.cssText = "position:absolute;inset:0";
        shadow.append(g);
        return g;
      });
      doc.documentElement.append(host);
      this.host = host;
    }

    draw(layerList, opts) {
      this._ensure();
      const o = this.host.getBoundingClientRect();
      this.groups.forEach((g, n) => {
        const layer = layerList[n];
        if (!layer) { g.replaceChildren(); return; }
        g.style.opacity = String(layer.alpha);
        g.replaceChildren(...lineRects(layer.ranges).map((r) => {
          const h = r.bottom - r.top;
          const pad = (opts.size * h) / 20; // scales with the text
          const box = this.doc.createElement("div");
          const s = box.style;
          s.position = "absolute";
          s.left = `${r.left - o.left - pad}px`;
          s.top = `${r.top - o.top - pad * 0.6}px`;
          s.width = `${r.right - r.left + 2 * pad}px`;
          s.height = `${h + 1.2 * pad}px`;
          s.borderRadius = `${Math.min(h * 0.3, 2 + pad)}px`;
          s.background = opts.color;
          return box;
        }));
      });
    }

    clear() {
      if (this.host) this.groups.forEach((g) => g.replaceChildren());
    }
  }

  return { DEFAULTS, settings, norm, words, sentences, layers, Painter };
})();
