// The Chromium extension's relay check (extension/chromium/background.js):
// a frame is told what is being read only while that is its own selection.
// No browser and no server: background.js runs in a Node vm with stand-ins
// for chrome.runtime and fetch.
//
//     node tests/test_extension.mjs
//
// The same cases were measured in headless Chromium 155 against a page that
// reads CSS.highlights (AUDIT 2026-11-13).

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
const ask = (selection) => new Promise((resolve) => {
  const kept = listener({ type: "now", selection }, {}, (r) =>
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

const cases = {
  "a page's bait selection is not told about another read": async () => {
    reading(SECRET);
    assert.deepEqual(await ask(DIGITS), IDLE);
  },
  "a guess one word off is not told either": async () => {
    reading(SECRET);
    assert.deepEqual(await ask("The code is four eight two two"), IDLE);
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
  "one word in 23 may differ": async () => {
    const want = reading(OWN.replace(" aloud", ""));
    assert.deepEqual(await ask(OWN), want);
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
    assert.deepEqual(await ask(undefined), IDLE);
  },
  "server down": async () => {
    server = {};
    assert.equal(await ask(OWN), null);
  },
};

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
