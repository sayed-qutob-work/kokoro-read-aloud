// The Chromium extension's relay check (extension/chromium/background.js):
// a frame is told what is being read only while that is its own selection,
// word for word, and only once it has had the focus during the read.
// No browser and no server: background.js runs in a Node vm with stand-ins
// for chrome.runtime and fetch. Also the highlight settings' validation
// (paint.js).
//
//     node tests/test_extension.mjs
//
// The same cases were measured in headless Chromium 155 against pages that
// log what their scripts can see (AUDIT 2026-11-13 and 2026-11-14).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const src = readFileSync(
  new URL("../extension/chromium/background.js", import.meta.url), "utf8");

let server = {};  // path -> JSON the stand-in server answers
let listener;
vm.runInNewContext(src, {
  chrome: { runtime: { onMessage: { addListener: (f) => (listener = f) } } },
  fetch: async (url) => {
    const body = server[new URL(url).pathname];
    if (body === undefined) throw new TypeError("connection refused");
    return { json: async () => body };
  },
});

// What the content script would get back, as plain JSON (the vm's objects
// have another realm's prototypes, which deepEqual would compare).
// `selection` is a string or the list content.js sends; `frame` stands in
// for chrome.runtime's MessageSender.
const FRAME = { tab: { id: 1, active: true }, frameId: 0, documentId: "D1" };
const ask = (selection, { focused = true, frame = FRAME } = {}) =>
  new Promise((resolve) => {
    if (typeof selection === "string") selection = [selection];
    const kept = listener({ type: "now", selection, focused }, frame, (r) =>
      resolve(JSON.parse(JSON.stringify(r))));
    assert.equal(kept, true);
  });

let utt = 0;
function reading(text, { word = 1, utteranceUtt } = {}) {
  utt++;
  const words = text.split(" ").map((w, i) => [w, i * 0.2, i * 0.2 + 0.2]);
  server = {
    "/now": { active: true, utt, text, words, word, full: text, sentence: text },
    "/utterance": { utt: utteranceUtt ?? utt, text },
  };
  return { active: true, text, words, word };
}

const IDLE = { active: false };
const SECRET = "The code is four eight two one";
const DIGITS = "zero one two three four five six seven eight nine ".repeat(20);
const OWN = "Select a passage in the browser and press the hotkey to hear it " +
  "read aloud. kokoroctl status shows whether the server is running.";
// The reviewer's case: the page knows all of a message but one word.
const CONTEXT = "Hi Sam, thanks for calling the support line today. As promised, " +
  "here is the reference for your account recovery request. Please keep it " +
  "private and do not share it with anyone, including our own staff, because " +
  "we will never ask for it. The code is";

const cases = {
  "a page's bait selection is not told about another read": async () => {
    reading(SECRET);
    assert.deepEqual(await ask(DIGITS), IDLE);
  },
  "a guess one word off is not told either": async () => {
    reading(SECRET);
    assert.deepEqual(await ask("The code is four eight two two"), IDLE);
  },
  "a page that knows all but one word is not told (it passed at 90%)":
    async () => {
      reading(CONTEXT + " seven. Have a good evening.");
      assert.deepEqual(
        await ask(CONTEXT + " three seven nine. Have a good evening."), IDLE);
    },
  "the page whose selection is read gets the chunk, and only the chunk":
    async () => {
      const want = reading(OWN);
      assert.deepEqual(await ask(OWN), want);  // no `full`, no `sentence`
    },
  "whitespace and case do not matter": async () => {
    const want = reading(OWN);
    assert.deepEqual(await ask(OWN.toUpperCase().replace(/ /g, "\n\t")), want);
  },
  "one word missing is not the selection": async () => {
    reading(OWN.replace(" aloud", ""));
    assert.deepEqual(await ask(OWN), IDLE);
  },
  "a copy's alt text matches the second text content.js sends": async () => {
    const want = reading("The speaker small blue speaker icon plays the read.");
    assert.deepEqual(await ask(["The speaker  plays the read.",
      "The speaker  small blue speaker icon   plays the read."]), want);
  },
  "PRIMARY's U+FFFC for an image is not a word": async () => {
    const want = reading("The speaker \ufffc plays the read.");
    assert.deepEqual(await ask("The speaker  plays the read."), want);
  },
  "a frame without the focus is not told, its own text or not": async () => {
    reading(OWN);
    assert.deepEqual(await ask(OWN, { focused: false }), IDLE);
  },
  "a frame in a background tab is not told": async () => {
    reading(OWN);
    const frame = { ...FRAME, tab: { id: 1, active: false } };
    assert.deepEqual(await ask(OWN, { frame }), IDLE);
  },
  "a frame let in keeps the read when the focus leaves": async () => {
    const want = reading(OWN);
    assert.deepEqual(await ask(OWN), want);
    assert.deepEqual(await ask(OWN, { focused: false }), want);
  },
  "... but no other frame gets in on it": async () => {
    reading(OWN);
    assert.equal((await ask(OWN)).active, true);
    const other = { ...FRAME, frameId: 3, documentId: "D2" };
    assert.deepEqual(await ask(OWN, { focused: false, frame: other }), IDLE);
  },
  "... nor the same frame on the next read": async () => {
    reading(OWN);
    assert.equal((await ask(OWN)).active, true);
    reading(OWN);
    assert.deepEqual(await ask(OWN, { focused: false }), IDLE);
  },
  "a frame let in that changes its selection is not told": async () => {
    reading(OWN);
    assert.equal((await ask(OWN)).active, true);
    assert.deepEqual(await ask("The code is four eight two one"), IDLE);
  },
  "a new read starting between the two requests is not matched": async () => {
    reading(OWN, { utteranceUtt: utt + 2 });
    assert.deepEqual(await ask(OWN), IDLE);
  },
  "nothing playing": async () => {
    server = { "/now": { active: false } };
    assert.deepEqual(await ask(OWN), IDLE);
  },
  "no selection sent": async () => {
    reading(OWN);
    assert.deepEqual(await ask([]), IDLE);
    assert.deepEqual(await ask([undefined]), IDLE);
  },
  "server down": async () => {
    server = {};
    assert.equal(await ask(OWN), null);
  },
};

// The highlight settings (paint.js): whatever storage holds, the page gets
// valid values.
const paint = vm.createContext({});
vm.runInContext(readFileSync(
  new URL("../extension/chromium/paint.js", import.meta.url), "utf8"), paint);
const settings = (raw) =>
  JSON.parse(JSON.stringify(vm.runInContext(`Kokoro.settings(${JSON.stringify(raw)})`, paint)));
const DEFAULTS = { unit: "word", color: "#3d5afe", strength: 35, size: 3 };
Object.assign(cases, {
  "settings: nothing stored gives the defaults": () => {
    assert.deepEqual(settings(undefined), DEFAULTS);
  },
  "settings: valid values are kept": () => {
    const s = { unit: "both", color: "#ffd400", strength: 50, size: 7 };
    assert.deepEqual(settings(s), s);
  },
  "settings: bad values fall back or are clamped": () => {
    assert.deepEqual(
      settings({ unit: "page", color: "red); x", strength: 500, size: -4 }),
      { ...DEFAULTS, strength: 70, size: 0 });
  },
});

let failed = 0;
for (const [name, run] of Object.entries(cases)) {
  try {
    await run();
    console.log(`ok    ${name}`);
  } catch (e) {
    failed++;
    console.log(`FAIL  ${name}\n${e.message}`);
  }
}
console.log(`${Object.keys(cases).length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
