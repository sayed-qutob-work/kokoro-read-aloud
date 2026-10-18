"""The reading settings and the rules about them, in one place for both
settings UIs: the Tk panel (tray.py) and the Omarchy widget, which reaches
them through `kokoroctl settings --json` and `kokoroctl set`. One copy means
the two cannot disagree about the voices, the speed range, what this machine
can sustain, or what gets saved.

Stdlib only and no Tk: kokoroctl imports it on the system Python.
"""

import json
import os

import paths

# Every English voice the server can load; model_files.py ships all 28.
# (name, accent, gender, overall grade in Kokoro's VOICES.md at f3ff357).
# The server derives the pipeline from the prefix (af_ -> 'a', bf_ -> 'b').
# Grouped by accent and gender, roughly best first within each group.
_VOICES = [
    ("af_heart", "American", "female", "A"),
    ("af_bella", "American", "female", "A-"),
    ("af_nicole", "American", "female", "B-"),
    ("af_aoede", "American", "female", "C+"),
    ("af_kore", "American", "female", "C+"),
    ("af_sarah", "American", "female", "C+"),
    ("af_nova", "American", "female", "C"),
    ("af_sky", "American", "female", "C-"),
    ("af_alloy", "American", "female", "C"),
    ("af_jessica", "American", "female", "D"),
    ("af_river", "American", "female", "D"),
    ("am_michael", "American", "male", "C+"),
    ("am_fenrir", "American", "male", "C+"),
    ("am_puck", "American", "male", "C+"),
    ("am_echo", "American", "male", "D"),
    ("am_eric", "American", "male", "D"),
    ("am_liam", "American", "male", "D"),
    ("am_onyx", "American", "male", "D"),
    ("am_adam", "American", "male", "F+"),
    ("am_santa", "American", "male", "D-"),
    ("bf_emma", "British", "female", "B-"),
    ("bf_isabella", "British", "female", "C"),
    ("bf_alice", "British", "female", "D"),
    ("bf_lily", "British", "female", "D"),
    ("bm_george", "British", "male", "C"),
    ("bm_fable", "British", "male", "C"),
    ("bm_lewis", "British", "male", "D+"),
    ("bm_daniel", "British", "male", "D"),
]
VOICES = [v[0] for v in _VOICES]
# VOICES.md's grades, best first.
GRADES = ("A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F+", "F")
# "Recommended" = graded B- or better: af_heart, af_bella, af_nicole and
# bf_emma. No male voice reaches it, so the male voices with the best male
# grade are marked "best male" instead: am_michael, am_fenrir and am_puck (C+).
RECOMMENDED_GRADES = GRADES[:GRADES.index("B-") + 1]
BEST_MALE_GRADE = min((g for _n, _a, gender, g in _VOICES if gender == "male"), key=GRADES.index)

# AUDIT S3/S6: Kokoro's own speed parameter rescales its duration predictor,
# and past ~1.3 it physically cannot articulate -- garbled onsets and endings.
# The rest of the speed comes from WSOLA time-stretching after synthesis.
MODEL_SPEED_CAP = 1.3

# Fraction of measured throughput we call "sustainable". Synthesis must outrun
# playback or the buffer drains; leaving 15% covers the per-chunk variance
# (measured 1.3x-1.9x RT on the GTX 1650 laptop's CPU, by chunk size).
SUSTAIN_MARGIN = 0.85

# The reading-speed control, in effective speed (model x playback).
SPEED_MIN, SPEED_MAX, SPEED_STEP = 0.8, 3.0, 0.05

DEFAULTS = {
    "voice": "af_heart",
    "model_speed": 1.15,
    "playback_speed": 1.8,
    "pause": 0.1,
    "first_chunk_audio": 2.0,
    "output_device": None,      # None = follow the system default
    # caption strip (overlay.py). Read by the strip at startup, not by the
    # server -- /config ignores them, and changing either needs the strip
    # restarted, which the panel does for you.
    "caption_style": "underline",
    "caption_layout": "teleprompter",
    "caption_position": "bottom",
    "caption_monitor": "primary",   # or a connector name, e.g. "DP-1"
    "caption_scroll": "continuous",  # continuous | line | off
}

SAMPLE = ("This is how the reading voice sounds at the speed you just chose. "
          "If it stutters or pauses in the middle, the speed is set higher "
          "than this machine can synthesize.")


def voice_catalog():
    """The voices for a picker: grouped by accent and gender in VOICES
    order, with a display label and the recommended mark."""
    out = []
    for name, accent, gender, grade in _VOICES:
        rec = grade in RECOMMENDED_GRADES
        best_male = gender == "male" and grade == BEST_MALE_GRADE and not rec
        out.append({"name": name, "group": f"{accent} {gender}",
                    "label": f"{accent} {gender} · {name.split('_', 1)[1].capitalize()}"
                             + (" ★" if rec else "") + (" · best male" if best_male else ""),
                    "grade": grade, "recommended": rec, "best_male": best_male})
    return out


def load_settings(log=None):
    """Disk settings merged over DEFAULTS. Unknown keys are dropped so a
    hand-edited file can't inject anything the server won't understand."""
    out = dict(DEFAULTS)
    try:
        with open(paths.SETTINGS, encoding="utf-8") as f:
            d = json.load(f)
        for k in DEFAULTS:
            if k in d:
                out[k] = d[k]
    except FileNotFoundError:
        pass
    except Exception as e:
        if log:
            log(f"settings load failed: {e}")
    return out


def save_settings(d, log=None):
    """Persist. This is what makes a tuned speed survive a reboot -- /config
    alone is in-memory, so before this file existed every restart silently
    reverted the user's tuning to whatever was hardcoded."""
    try:
        tmp = paths.SETTINGS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: d[k] for k in DEFAULTS if k in d}, f, indent=2)
        os.replace(tmp, paths.SETTINGS)
        return True
    except Exception as e:
        if log:
            log(f"settings save failed: {e}")
        return False


def split(effective, model):
    """Split a target effective speed into (model, playback).

    Only playback_speed drives the synthesis race -- the model produces
    FEWER audio seconds at higher model_speed, so leaning on it costs less
    throughput. AUDIT S3 nonetheless keeps model_speed near 1.15 for
    quality, so the user's Advanced choice is honored and playback takes
    the remainder."""
    model = max(1.0, min(MODEL_SPEED_CAP, model))
    return model, max(0.3, effective / model)


def sustain_ceiling(rt, model):
    """The fastest effective reading speed this machine keeps up with."""
    return model * rt * SUSTAIN_MARGIN


def sustain_level(effective, model, rt):
    """"over" (reads will stall), "close" (occasional stutter) or "ok";
    None without a measured throughput."""
    if not rt:
        return None
    _model, playback = split(effective, model)
    if playback >= rt:
        return "over"
    if playback >= rt * SUSTAIN_MARGIN:
        return "close"
    return "ok"
