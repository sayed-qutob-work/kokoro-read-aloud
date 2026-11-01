"""English text to Kokoro phonemes, without the `kokoro` package.

Paradee-8M reads Kokoro's own phonemes, and the server used to get them
from kokoro's KPipeline with no model behind it. But importing kokoro
imports torch, which is most of a GPU install, and Paradee needs none of
it (AUDIT 2026-11-01). misaki, the G2P underneath, does not need torch
either with trf=False, which is what KPipeline passes too.

So this is KPipeline's English path, as the server uses it: misaki's G2P
with its eSpeak NG fallback, the split into chunks of at most 510
phonemes, and join_timestamps, which turns per-phoneme durations into
per-word times. Adapted from kokoro 0.9.4's kokoro/pipeline.py
(https://github.com/hexgrad/kokoro), Copyright hexgrad, Apache License
2.0 (http://www.apache.org/licenses/LICENSE-2.0). Changed: the class
around it, and no torch types.
"""

import re

MAX_PHONEMES = 510      # the model's 512 tokens, less a pad at each end


class EnglishG2P:
    def __init__(self, british=False):
        from misaki import en, espeak
        fallback = espeak.EspeakFallback(british=british)
        self.g2p = en.G2P(trf=False, british=british, fallback=fallback, unk="")

    def __call__(self, text):
        """Yields (phonemes, tokens) per chunk of at most MAX_PHONEMES,
        splitting on newlines first as KPipeline does."""
        for graphemes in re.split(r"\n+", text.strip()):
            if not graphemes.strip():
                continue
            _, tokens = self.g2p(graphemes)
            for ps, tks in _chunks(tokens):
                if ps:
                    yield ps[:MAX_PHONEMES], tks


def _tokens_to_ps(tokens):
    return "".join(t.phonemes + (" " if t.whitespace else "") for t in tokens).strip()


def _waterfall_last(tokens, next_count, waterfall=("!.?…", ":;", ",—"),
                    bumps=(")", "”")):
    """Where to cut an over-long chunk: after its last sentence end, else
    after its last colon or semicolon, else its last comma or dash."""
    for w in waterfall:
        z = next((i for i, t in reversed(list(enumerate(tokens)))
                  if t.phonemes in set(w)), None)
        if z is None:
            continue
        z += 1
        if z < len(tokens) and tokens[z].phonemes in bumps:
            z += 1
        if next_count - len(_tokens_to_ps(tokens[:z])) <= MAX_PHONEMES:
            return z
    return len(tokens)


def _chunks(tokens):
    tks, pcount = [], 0
    for t in tokens:
        t.phonemes = "" if t.phonemes is None else t.phonemes
        next_ps = t.phonemes + (" " if t.whitespace else "")
        next_pcount = pcount + len(next_ps.rstrip())
        if next_pcount > MAX_PHONEMES:
            z = _waterfall_last(tks, next_pcount)
            yield _tokens_to_ps(tks[:z]), tks[:z]
            tks = tks[z:]
            pcount = len(_tokens_to_ps(tks))
            if not tks:
                next_ps = next_ps.lstrip()
        tks.append(t)
        pcount += len(next_ps)
    if tks:
        yield _tokens_to_ps(tks), tks


def join_timestamps(tokens, pred_dur):
    """Sets start_ts and end_ts (seconds) on each token from pred_dur, the
    frames per phoneme including the pad at each end. A frame is 600
    samples at 24 kHz; the counts run in half-frames, so a space's
    duration can be split between the words either side of it."""
    divisor = 80
    if not tokens or len(pred_dur) < 3:
        return
    left = right = 2 * max(0, int(pred_dur[0]) - 3)
    i = 1
    for t in tokens:
        if i >= len(pred_dur) - 1:
            break
        if not t.phonemes:
            if t.whitespace:
                i += 1
                left = right + int(pred_dur[i])
                right = left + int(pred_dur[i])
                i += 1
            continue
        j = i + len(t.phonemes)
        if j >= len(pred_dur):
            break
        t.start_ts = left / divisor
        token_dur = int(sum(pred_dur[i:j]))
        space_dur = int(pred_dur[j]) if t.whitespace else 0
        left = right + (2 * token_dur) + space_dur
        t.end_ts = left / divisor
        right = left + space_dur
        i = j + (1 if t.whitespace else 0)
