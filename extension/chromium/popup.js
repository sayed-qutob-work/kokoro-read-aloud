// The settings popup (also the extension's options page). Settings live in
// chrome.storage.local under "highlight"; every open page listens for
// changes, so they apply at once, mid-read too.

"use strict";

const $ = (id) => document.getElementById(id);
const SWATCHES = {
  "#3d5afe": "Blue", "#ffd400": "Yellow", "#00c853": "Green",
  "#ff4081": "Pink", "#ff9100": "Orange",
};

let opts = Kokoro.settings();
const painter = new Kokoro.Painter(document);
const sample = $("preview");
let sampleWords = null;

for (const [color, name] of Object.entries(SWATCHES)) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "swatch";
  b.style.background = color;
  b.title = name;
  b.setAttribute("aria-label", name);
  b.dataset.color = color;
  b.addEventListener("click", () => save({ color }));
  $("swatches").insertBefore(b, $("swatches").lastElementChild);
}

function preview() {
  if (!sampleWords) {
    const r = document.createRange();
    r.selectNodeContents(sample);
    sampleWords = Kokoro.words(r);
  }
  const i = sampleWords.findIndex((w) => w.norm === "follows");
  painter.draw(Kokoro.layers(sampleWords, i, opts), opts);
}

function show() {
  $(`unit-${opts.unit}`).checked = true;
  for (const b of document.querySelectorAll(".swatch"))
    b.setAttribute("aria-pressed", String(b.dataset.color === opts.color));
  $("color").value = opts.color;
  $("strength").value = opts.strength;
  $("strength-out").value = `${opts.strength}%`;
  $("size").value = opts.size;
  $("size-out").value = String(opts.size);
  preview();
}

function save(patch) {
  opts = Kokoro.settings({ ...opts, ...patch });
  show();
  chrome.storage.local.set({ highlight: opts });
}

for (const r of document.querySelectorAll('input[name="unit"]'))
  r.addEventListener("change", () => save({ unit: r.value }));
$("color").addEventListener("input", (e) => save({ color: e.target.value }));
$("strength").addEventListener("input", (e) =>
  save({ strength: Number(e.target.value) }));
$("size").addEventListener("input", (e) => save({ size: Number(e.target.value) }));
$("reset").addEventListener("click", () => save(Kokoro.DEFAULTS));

show();
chrome.storage.local.get("highlight").then((r) => {
  opts = Kokoro.settings(r.highlight);
  show();
});
