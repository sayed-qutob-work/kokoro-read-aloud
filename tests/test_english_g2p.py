r"""Tests for english_g2p: KPipeline's English path without the kokoro
package, which the Paradee engine reads through (AUDIT 2026-11-01).

    Linux:   ~/.local/share/kokoro-read-aloud/venv/bin/python -B tests/test_english_g2p.py
    Windows: env\Scripts\python.exe -B tests\test_english_g2p.py

SameAsKokoroTests compare it with kokoro's own KPipeline, phonemes, chunks
and word times, and are skipped where kokoro is not installed (the CPU
build). LimitTests run everywhere.
"""

import copy
import importlib.util
import os
import random
import sys
import unittest

sys.dont_write_bytecode = True     # on Linux nothing may be written next to the code
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import english_g2p                 # noqa: E402

LONG = " ".join(["The committee reviewed the proposals from the northern district, "
                 "then the southern one; after that, it published a report. "
                 "Nobody was surprised!"] * 6)
TEXTS = ["Hello world.",
         "In 2024, revenue grew 34.7% to $1.2 billion — roughly.",
         "Line one.\n\nLine two, after a gap.\nLine three!",
         "Dr. Smith's U.S.A. trip (in March) cost “a lot”…",
         "rhythm psst hmm ok",
         LONG,
         "   "]
HAS_KOKORO = importlib.util.find_spec("kokoro") is not None


def durations(rng, phonemes):
    """Made-up frames per phoneme, with a pad at each end, like the model's."""
    return [rng.randint(1, 12) for _ in range(len(phonemes) + 2)]


@unittest.skipUnless(HAS_KOKORO, "kokoro is not installed (the CPU build)")
class SameAsKokoroTests(unittest.TestCase):
    def check(self, british):
        import torch
        import model_files
        from kokoro import KPipeline
        theirs = KPipeline(lang_code="b" if british else "a",
                           repo_id=model_files.REPO, model=False)
        ours = english_g2p.EnglishG2P(british=british)
        rng = random.Random(1)
        for text in TEXTS:
            with self.subTest(text=text[:40]):
                a = [(r.phonemes, [(t.text, t.phonemes, t.whitespace) for t in r.tokens])
                     for r in theirs(text)]
                b = [(ps, [(t.text, t.phonemes, t.whitespace) for t in tks])
                     for ps, tks in ours(text)]
                self.assertEqual(a, b)
                for r, (ps, tks) in zip(theirs(text), ours(text)):
                    dur = durations(rng, ps)
                    t1, t2 = copy.deepcopy(list(r.tokens)), copy.deepcopy(tks)
                    KPipeline.join_timestamps(t1, torch.tensor(dur))
                    english_g2p.join_timestamps(t2, dur)
                    self.assertEqual([(t.start_ts, t.end_ts) for t in t1],
                                     [(t.start_ts, t.end_ts) for t in t2])

    def test_american(self):
        self.check(british=False)

    def test_british(self):
        self.check(british=True)


class LimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g2p = english_g2p.EnglishG2P()

    def test_long_text_is_split_within_the_model_limit(self):
        chunks = list(self.g2p(LONG))
        self.assertGreater(len(chunks), 1)
        for ps, _tks in chunks:
            self.assertLessEqual(len(ps), english_g2p.MAX_PHONEMES)

    def test_word_times_run_forward(self):
        ps, tks = next(self.g2p("The quick brown fox jumps over the lazy dog."))
        english_g2p.join_timestamps(tks, durations(random.Random(2), ps))
        times = [(t.start_ts, t.end_ts) for t in tks if t.start_ts is not None]
        self.assertTrue(times)
        for (s1, e1), (s2, _e2) in zip(times, times[1:]):
            self.assertLessEqual(s1, e1)
            self.assertLessEqual(e1, s2)

    def test_blank_text_yields_nothing(self):
        self.assertEqual(list(self.g2p(" \n\n ")), [])


if __name__ == "__main__":
    unittest.main()
