r"""Tests for what tts_server reads of a URL: its host alone (AUDIT 2026-11-14).

    Linux:   ~/.local/share/kokoro-read-aloud/venv/bin/python -B tests/test_sanitize.py
    Windows: env\Scripts\python.exe -B tests\test_sanitize.py

No model, no audio, no server: sanitize() and split_atoms() are plain text
functions. The cases that must NOT change are the ones a URL rule could
mistake for a host: emails, file paths, file names, dotted code names,
version numbers and a sentence with no space after its full stop.
"""

import os
import sys
import unittest

sys.dont_write_bytecode = True     # on Linux nothing may be written next to the code
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tts_server                  # noqa: E402  (the model loads only under __main__)


def said(text):
    """What sanitize() hands the model, with spacing collapsed."""
    return " ".join(tts_server.sanitize(text).split())


class UrlTests(unittest.TestCase):
    def test_read_as_host(self):
        cases = {
            "See https://github.com/sayed-qutob-work/kokoro-read-aloud/blob/main/docs/AUDIT.md for details.":
                "See github dot com for details.",
            "Go to https://www.example.com/ now.": "Go to example dot com now.",
            "https://www.google.com/search?q=kokoro+tts&oq=kokoro&ie=UTF-8": "google dot com",
            "Read https://en.wikipedia.org/wiki/Text-to-speech first.":
                "Read en dot wikipedia dot org first.",
            "The server is at http://localhost:5111/config today.":
                "The server is at localhost today.",
            "Log in with https://user:secret@git.example.dev:8443/repo.git now.":
                "Log in with git dot example dot dev now.",
            "HTTPS://WWW.EXAMPLE.COM/PAGE in capitals.": "example dot com in capitals.",
            "A ftp://files.example.org/pub/a.tar.gz mirror.":
                "A files dot example dot org mirror.",
        }
        for text, want in cases.items():
            self.assertEqual(said(text), want, text)

    def test_without_a_scheme(self):
        cases = {
            "It was in www.nytimes.com/2026/11/14/tech/ai-voice.html yesterday.":
                "It was in nytimes dot com yesterday.",
            "Clone github.com/sayed-qutob-work/kokoro-read-aloud and run it.":
                "Clone github dot com and run it.",
            "Go to github.com to see it.": "Go to github dot com to see it.",
            "The BBC is at bbc.co.uk, and Booking.com is a site.":
                "The BBC is at bbc dot co dot uk, and booking dot com is a site.",
        }
        for text, want in cases.items():
            self.assertEqual(said(text), want, text)

    def test_punctuation_after_a_url_stays(self):
        cases = {
            "Watch https://youtu.be/dQw4w9WgXcQ. Then stop.": "Watch youtu dot be. Then stop.",
            "(see https://example.com/a/b_c~d)": "(see example dot com)",
            "It is https://en.wikipedia.org/wiki/Python_(programming_language), a language.":
                "It is en dot wikipedia dot org, a language.",
            "Open <https://omarchyplugins.com/plugin.html?id=x> in a browser.":
                "Open omarchyplugins dot com in a browser.",
            "**https://docs.python.org/3/library/re.html** is bold.":
                "docs dot python dot org is bold.",
        }
        for text, want in cases.items():
            self.assertEqual(said(text), want, text)

    def test_sentence_end_after_a_url_splits(self):
        # misaki took the full stop as part of the URL, so the two
        # sentences were read as one
        self.assertEqual(tts_server.split_atoms("Watch https://youtu.be/xyz. Then stop."),
                         ["Watch youtu dot be.", "Then stop."])

    def test_markdown_links_read_as_their_text(self):
        cases = {
            "Read [the install guide](https://example.com/docs/install.md) first.":
                "Read the install guide first.",
            "See [Python](https://en.wikipedia.org/wiki/Python_(programming_language)) here.":
                "See Python here.",
            "[![Latest release](https://img.shields.io/github/v/release/a/b)](https://github.com/a/b/releases) out":
                "Latest release out",
        }
        for text, want in cases.items():
            self.assertEqual(said(text), want, text)

    def test_not_urls(self):
        for text in [
            "Mail someone@example.com about it.",
            "Files live in ~/.config/kokoro-read-aloud/settings.json on Linux.",
            "Run linux/install-hyprland.sh then model-files.py and README.md.",
            "The node.js and Vue.js projects, and os.path.join.",
            "It ended.Then a new sentence started.e.g. this, i.e. that, U.S.A. too.",
            "Version 3.14.1 of python3.12 is out.",
            "Anything starting www. is a URL.",
            "Press Ctrl+Alt+R to read.",
        ]:
            self.assertEqual(tts_server.say_urls(text), text)

    def test_relative_markdown_link_left_to_misaki(self):
        # misaki drops a target that is not a URL itself (AUDIT 2026-11-14)
        self.assertEqual(tts_server.say_urls("See [the guide](docs/install.md)."),
                         "See [the guide](docs/install.md).")


if __name__ == "__main__":
    unittest.main(verbosity=2)
