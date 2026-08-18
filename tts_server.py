r"""
Local read-aloud server.  POST text, it speaks.

    env\Scripts\python.exe tts_server.py

Endpoints:
    POST /speak    {"text": "..."}
    POST /stop     {}
    POST /config   {"voice":..., "model_speed":..., "playback_speed":..., "pause":...}
    GET  /config
"""

import json
import os
import re
import time
import queue
import threading
from collections import deque

import numpy as np
import sounddevice as sd
from flask import Flask, request, jsonify

_ROOT = os.path.dirname(os.path.abspath(__file__))

# ======================= TUNE THESE =======================
ENGINE = "torch"          # "torch" = kokoro (PyTorch)  -> 4.03x RT
                          # "onnx"  = kokoro-onnx       -> 3.89x RT (slower)
                          # Both measured on the ORIGINAL desktop and NOT
                          # transferable -- see docs/AUDIT.md 4. What holds is
                          # the ordering: onnx was never faster.

ONNX_MODEL  = os.path.join(_ROOT, "kokoro-v1.0.onnx")
ONNX_VOICES = os.path.join(_ROOT, "voices-v1.0.bin")

KOKORO_VOICE = "af_heart"

OUTPUT_DEVICE = None      # None = follow the Windows default output device.
                          # Otherwise "<hostapi>|<device name>", e.g.
                          # "MME|Headphones (Realtek(R) Audio)". Set from the
                          # tray Settings panel; stored in settings.json.

MODEL_SPEED = 1.15        # what Kokoro itself is asked for. KEEP <= 1.3.
PLAYBACK_SPEED = 1.8      # WSOLA time-stretch after synthesis. Pitch-preserving.
                          # Real speed = MODEL_SPEED * PLAYBACK_SPEED

SENTENCE_PAUSE = 0.1      # silence after a real sentence end (never stretched)
CUT_PAUSE = 0.03          # silence after a mid-sentence chunk cut. Kokoro pads
                          # every synthesis with ~280ms leading / ~450ms trailing
                          # silence (measured); it is trimmed off and replaced
                          # with one of these two, so boundaries stay tight.

FIRST_CHUNK_AUDIO = 2.0   # seconds of audio in the opening chunk. This sets
                          # START latency: ~104ms + 248ms per second of audio.
                          # Sized in audio seconds, not chars, because density
                          # swings 4x between texts (same lesson as the ramp).
CHUNK_CHARS = 240         # ceiling on any chunk (speech-weighted chars).
MIN_CHUNK_CHARS = 25      # floor, so a tight budget can't produce 3-word chunks.
SAFETY = 0.7              # spend only this fraction of the playback budget on
                          # the next chunk. Lower = fewer gaps, choppier ramp.

VERBOSE = True            # log synth speed, targets, gaps

HOST, PORT = "127.0.0.1", 5111
PREFETCH = 2
# ==========================================================
# There is NO chunk-ramp multiplier any more. Chunk size is derived at runtime
# from how much unplayed audio is banked, using density (seconds of audio per
# character) and throughput (x realtime) measured live from this machine.
# Both are learned; neither is assumed.
#
# American English 'a'  af_heart af_bella af_nicole af_aoede af_kore af_sarah
#                       af_nova af_sky af_alloy af_jessica af_river
#                       am_michael am_fenrir am_puck am_echo am_eric am_liam
#                       am_onyx am_adam
# British English  'b'  bf_emma bf_isabella bf_alice bf_lily
#                       bm_george bm_fable bm_lewis bm_daniel


SETTINGS_FILE = os.path.join(_ROOT, "settings.json")
CALIB_FILE = os.path.join(_ROOT, "calibration.json")


def load_user_settings():
    """Apply settings.json (written by tray.py) over the constants above.

    /config alone is in-memory, so before this existed every restart silently
    reverted the user's tuning to whatever a past session hardcoded. The tray
    panel is the UI for these; this is what makes them stick."""
    global KOKORO_VOICE, MODEL_SPEED, PLAYBACK_SPEED
    global SENTENCE_PAUSE, FIRST_CHUNK_AUDIO, OUTPUT_DEVICE
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return
    except Exception as e:
        print("settings.json unreadable, using defaults:", e, flush=True)
        return
    try:
        KOKORO_VOICE = str(d.get("voice", KOKORO_VOICE))
        MODEL_SPEED = min(1.3, max(0.5, float(d.get("model_speed", MODEL_SPEED))))
        PLAYBACK_SPEED = max(0.3, float(d.get("playback_speed", PLAYBACK_SPEED)))
        SENTENCE_PAUSE = max(0.0, float(d.get("pause", SENTENCE_PAUSE)))
        FIRST_CHUNK_AUDIO = max(0.3, float(d.get("first_chunk_audio",
                                                 FIRST_CHUNK_AUDIO)))
        OUTPUT_DEVICE = d.get("output_device", OUTPUT_DEVICE) or None
        print(f"settings.json: voice={KOKORO_VOICE} "
              f"speed={MODEL_SPEED}x{PLAYBACK_SPEED} "
              f"pause={SENTENCE_PAUSE} first={FIRST_CHUNK_AUDIO} "
              f"out={OUTPUT_DEVICE or 'system default'}", flush=True)
    except Exception as e:
        print("settings.json has a bad value, using defaults:", e, flush=True)


def list_output_devices():
    """Every device that can play audio, with a stable spec string.

    The same physical output shows up once per host API (MME, DirectSound,
    WASAPI, WDM-KS), so the host API is part of the identity, not noise."""
    try:
        default_idx = sd.default.device[1]
    except Exception:
        default_idx = None
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_output_channels", 0) <= 0:
            continue
        try:
            api = sd.query_hostapis(d["hostapi"])["name"]
        except Exception:
            api = "?"
        out.append({"index": i, "name": d["name"], "hostapi": api,
                    "spec": f"{api}|{d['name']}",
                    "channels": d["max_output_channels"],
                    "is_default": i == default_idx})
    return out


def resolve_device(spec):
    """Device INDEX for a stored spec, or None to follow the Windows default.

    Specs are stored as "<hostapi>|<name>", never as an index. PortAudio
    indices shift whenever a device appears or disappears -- which on a laptop
    is every time headphones are plugged in, i.e. exactly the situation this
    setting exists to survive. A spec that no longer resolves falls back to
    the system default rather than failing to speak at all."""
    if not spec or str(spec).strip().lower() in ("default", "none", ""):
        return None
    api, sep, name = str(spec).partition("|")
    if not sep:
        api, name = "", api
    devs = list_output_devices()
    for d in devs:
        if d["hostapi"] == api and d["name"] == name:
            return d["index"]
    for d in devs:
        if d["name"] == name:
            return d["index"]
    # MME truncates device names to 31 characters ("DELL U2422HE (HD Audio
    # Driver f"), so an exact match can legitimately fail across host APIs.
    for d in devs:
        if d["name"][:31] == name[:31]:
            return d["index"]
    print(f"output device {spec!r} not found -- using system default",
          flush=True)
    return None


def load_calibration():
    """Last session's learned (density, rt), or (None, None).

    These are MEASURED properties of this machine, not preferences, and they
    take ~10 chunks of EMA to converge. Starting every boot from a hardcoded
    guess is what made the first read of a session mis-plan: the old default
    was 4.0x RT, measured on a 6-core desktop, while this laptop does ~1.7x.
    An rt guessed too HIGH over-fills chunks and starves playback, so a stale
    value is clamped rather than trusted outright."""
    try:
        with open(CALIB_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return float(d["density"]), float(d["rt"])
    except Exception:
        return None, None


def save_calibration(density, rt):
    try:
        tmp = CALIB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"density": round(density, 5), "rt": round(rt, 3),
                       "saved": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
        os.replace(tmp, CALIB_FILE)
    except Exception:
        pass


_CLAUSE = re.compile(r"[,;:]\s")

# Words a chunk must not end on when cut at a bare word boundary. Kokoro
# treats any cut as a sentence end and lengthens the final word 2-4x
# (measured 2026-07-17: "the" 0.087s whole -> 0.350s cut); a drawn-out
# stranded function word is the worst-sounding case, so the cut backs up
# past these.
_STOP_TAIL = {"a", "an", "the", "of", "to", "in", "on", "at", "for", "and",
              "or", "but", "nor", "with", "that", "as", "by", "from", "is",
              "are", "was", "were", "be", "been", "his", "her", "its",
              "their", "this", "these", "those", "my", "your", "our", "he",
              "she", "it", "they", "we", "i", "you", "not", "so", "if",
              "than", "then", "when", "while", "which", "who", "whose"}

# Digits and currency symbols expand ~5-7x when read aloud ("2024" ->
# "twenty twenty four", "%" -> "percent"). This is the main source of the
# 4x audio-per-char swing, so all chunk sizing uses weighted chars.
_CHAR_WEIGHT = {c: 5 for c in "0123456789"}
_CHAR_WEIGHT.update({c: 7 for c in "$%€£"})


def wlen(text):
    """Length in speech-weighted characters."""
    return sum(_CHAR_WEIGHT.get(c, 1) for c in text)


def windex(text, wlimit):
    """Index where cumulative speech weight exceeds wlimit."""
    acc = 0
    for i, c in enumerate(text):
        acc += _CHAR_WEIGHT.get(c, 1)
        if acc > wlimit:
            return i
    return len(text)


def cut_point(text, limit):
    """Where to slice an oversized sentence: the last clause boundary in the
    back half of the window if there is one, else the last space. Kokoro
    treats a cut as a sentence end and drops pitch, which is least damaging
    at a natural pause."""
    best = -1
    for m in _CLAUSE.finditer(text, 0, limit):
        best = m.end() - 1
    if best >= limit // 2:
        return best
    cut = text.rfind(" ", 0, limit)
    while cut > limit // 4:
        prev = text.rfind(" ", 0, cut)
        if text[prev + 1:cut].lower().strip("\"'(),;:") not in _STOP_TAIL:
            break
        cut = prev
    return cut


def sanitize(text):
    """Strip markdown/TUI noise before synthesis. Inline **bold** and `code`
    are inert to Kokoro (measured - identical audio), but list markers add
    real pauses and box-drawing/table chars come along with terminal text."""
    text = re.sub(r"^[\s>]*[-*+•●○▪‣]+\s+", " ", text, flags=re.M)  # list markers
    text = re.sub(r"^#{1,6}\s+", " ", text, flags=re.M)             # md headers
    text = re.sub(r"[*_`~|]+", " ", text)                           # inline md, table pipes
    text = re.sub(r"[─-▟→←↑↓✔✖✅❌]", " ", text)           # box drawing, arrows
    return text


def split_atoms(text):
    """Clauses are the packing unit: sentences are split at , ; : as well as
    sentence ends, so chunk boundaries only ever land where the voice would
    pause anyway. A bare mid-clause cut lengthens the cut word 2-4x
    (measured) - user-audible as random slowing - so it is reserved for
    single clauses that alone exceed CHUNK_CHARS. Atoms are rejoined before
    synthesis (clauses keep their punctuation), so Kokoro still sees whole
    sentences and joins stay free."""
    text = re.sub(r"\s+", " ", sanitize(text)).strip()
    if not text:
        return []
    atoms = []
    for p in re.split(r"(?<=[.!?])\s+", text):
        for c in re.split(r"(?<=[,;:])\s+", p.strip()):
            c = c.strip()
            while wlen(c) > CHUNK_CHARS:
                cut = cut_point(c, windex(c, CHUNK_CHARS))
                if cut <= 0:
                    cut = windex(c, CHUNK_CHARS)
                atoms.append(c[:cut].strip())
                c = c[cut:].strip()
            if c:
                atoms.append(c)
    return atoms


def trim_silence(x, sr, thresh=0.01, keep_ms=50):
    """Cut Kokoro's leading/trailing silence padding, keeping keep_ms of
    natural breath on each side. Boundary pauses are then controlled by
    SENTENCE_PAUSE / CUT_PAUSE alone. Returns (audio, lead_seconds); lead
    is what was cut from the front, needed to keep word timestamps aligned."""
    idx = np.where(np.abs(x) > thresh)[0]
    if len(idx) == 0:
        return x, 0.0
    keep = int(keep_ms / 1000 * sr)
    a = max(0, idx[0] - keep)
    return x[a:min(len(x), idx[-1] + keep)], a / sr


def time_stretch(x, sr, factor):
    """WSOLA. factor > 1 = faster, pitch preserved."""
    if abs(factor - 1.0) < 0.02 or len(x) < sr // 20:
        return x
    N = int(0.030 * sr)
    Hs = N // 2
    Ha = int(round(Hs * factor))
    delta = int(0.010 * sr)
    win = np.hanning(N).astype(np.float32)

    target = int(len(x) / factor)
    out_len = target + N
    xp = np.concatenate([np.zeros(delta, np.float32),
                         x.astype(np.float32),
                         np.zeros(N + Ha + 2 * delta, np.float32)])
    y = np.zeros(out_len + N, np.float32)
    wsum = np.zeros(out_len + N, np.float32)

    a, s, tail = delta, 0, None
    while s + N < out_len and a + N + delta < len(xp):
        if tail is None:
            best = a
        else:
            lo = max(0, a - delta)
            seg = xp[lo:a + delta + N]
            if len(seg) < N:
                break
            best = lo + int(np.argmax(np.correlate(seg, tail, mode="valid")))
        y[s:s + N] += xp[best:best + N] * win
        wsum[s:s + N] += win
        tail = xp[best + Hs:best + Hs + N]
        if len(tail) < N:
            break
        s += Hs
        a += Ha
    wsum[wsum < 1e-6] = 1.0
    return (y[:target] / wsum[:target]).astype(np.float32)


def _xjoin(a, b, sr, ms=4):
    """Concatenate with a short crossfade so splices can't click."""
    n = min(int(ms * sr // 1000), len(a), len(b))
    if n <= 0:
        return np.concatenate([a, b])
    f = np.linspace(0.0, 1.0, n, dtype=np.float32)
    return np.concatenate([a[:-n], a[-n:] * (1 - f) + b[:n] * f, b[n:]])


def compress_final_word(audio, sr, words):
    """A bare mid-sentence cut makes Kokoro lengthen the chunk's last word
    2-4x, as if the sentence ended there (measured 2026-07-17; clause cuts
    at , ; : are clean). Compress that word back to the chunk's own
    per-phoneme rate so cuts don't sound like the voice randomly slowing.
    words = [(text, n_phonemes, start_ts, end_ts)] in raw-synthesis time.
    Returns (audio, words) with the final word's end_ts updated to match."""
    if len(words) < 4:
        return audio, words
    *rest, (wtext, nph, t0, t1) = words
    rates = [(b - a) / n for _, n, a, b in rest if n and b > a]
    if not rates or not nph or t1 <= t0:
        return audio, words
    factor = (t1 - t0) / max(float(np.median(rates)) * nph, 1e-3)
    if factor < 1.3:
        return audio, words
    factor = min(factor, 2.5)
    i0, i1 = int(t0 * sr), min(int(t1 * sr), len(audio))
    if i1 - i0 < sr // 20:
        return audio, words
    if VERBOSE:
        print(f"cutfix '{wtext}' {t1 - t0:.2f}s / {factor:.2f}", flush=True)
    seg = time_stretch(audio[i0:i1], sr, factor)
    audio = _xjoin(_xjoin(audio[:i0], seg, sr), audio[i1:], sr)
    return audio, rest + [(wtext, nph, t0, t0 + (t1 - t0) / factor)]


class KokoroEngine:
    name = "kokoro"

    def __init__(self):
        self.pipes = {}
        self.voice = KOKORO_VOICE
        self.model_speed = MODEL_SPEED
        self._pipe_for(self.voice)

    def _pipe_for(self, voice):
        code = "b" if voice.startswith("b") else "a"
        if code not in self.pipes:
            from kokoro import KPipeline
            self.pipes[code] = KPipeline(lang_code=code)
        return self.pipes[code]

    def synth(self, sentence):
        """Returns (audio, sr, words); words carry per-word timestamps
        (text, n_phonemes, start_ts, end_ts) in raw-synthesis time."""
        pipe = self._pipe_for(self.voice)
        parts, words, offset = [], [], 0.0
        for r in pipe(sentence, voice=self.voice, speed=self.model_speed):
            if r.audio is None:
                continue
            a = np.asarray(r.audio, dtype=np.float32)
            for t in (r.tokens or []):
                if (t.start_ts is not None and t.end_ts is not None
                        and any(c.isalnum() for c in t.text)):
                    words.append((t.text, len(t.phonemes or ""),
                                  offset + t.start_ts, offset + t.end_ts))
            offset += len(a) / 24000
            parts.append(a)
        if not parts:
            return None, 24000, []
        return np.concatenate(parts), 24000, words


class KokoroOnnxEngine:
    name = "kokoro-onnx"

    def __init__(self):
        from kokoro_onnx import Kokoro
        self.k = Kokoro(ONNX_MODEL, ONNX_VOICES)
        self.voice = KOKORO_VOICE
        self.model_speed = MODEL_SPEED

    def synth(self, sentence):
        lang = "en-gb" if self.voice.startswith("b") else "en-us"
        samples, sr = self.k.create(sentence, voice=self.voice,
                                    speed=self.model_speed, lang=lang)
        return np.asarray(samples, dtype=np.float32), sr, []


class Player:
    def __init__(self, engine):
        self.engine = engine
        self.pending = deque()               # (gen, atom)
        self.audio_q = queue.Queue(maxsize=PREFETCH)
        self.gen = 0
        self.t_speak = time.perf_counter()

        # --- the two numbers that replace CHUNK_RAMP, both learned live ---
        self.play_until = 0.0                # wallclock when banked audio ends
        # Both are learned live, but seeded from the last session's measured
        # values: the EMA needs ~10 chunks to converge, and until it does the
        # chunk planner is working from a guess. The old hardcoded 4.0 was the
        # OLD desktop's throughput (6-core, measured 4.03x RT) and is ~2.3x
        # optimistic on this laptop. Over-estimating rt over-fills chunks and
        # starves playback, so a missing/absurd value falls back LOW, which
        # only costs a slightly choppier ramp.
        dens, rt = load_calibration()
        self.density = dens if dens else 0.075
        self.rt = min(rt, 8.0) if rt else 2.0
        self._calib_n = 0
        self.now = None                      # chunk being played, for /now
        self.source = ""                     # original text of the utterance
        self.source_gen = 0
        # chunk text/ends_sentence in synthesis order, for the caption strip's
        # prev/next context (RELEASE_PLAN §3.1/3.2). Reset per gen; appended
        # in _synth_loop, walked by position in _play_loop. Same race
        # tolerance as the rest of this class: a stale-gen entry can slip in
        # between the check and the append, self-corrects next chunk.
        self.chunk_seq = []
        self.play_pos = -1

        self.lock = threading.Lock()
        self.cv = threading.Condition()
        # PortAudio is NOT thread-safe: abort() during write() can hang it.
        # Built before the stream, because opening one now takes this lock.
        self.audio_lock = threading.Lock()
        self.device_spec = OUTPUT_DEVICE
        self.device_index = None
        self.device_name = ""
        self.stream = None
        with self.audio_lock:
            self._open_stream_locked()
        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    def _open_stream_locked(self):
        """Open the output stream on the configured device.

        Caller must hold audio_lock. _play_loop re-reads self.stream on every
        block *inside* that lock, so swapping the object here is safe."""
        idx = resolve_device(self.device_spec)
        self.device_index = idx
        try:
            probe = idx if idx is not None else sd.default.device[1]
            self.device_name = sd.query_devices(probe)["name"]
        except Exception:
            self.device_name = "?"
        self.stream = sd.OutputStream(samplerate=24000, channels=1,
                                      dtype="float32", blocksize=1024,
                                      latency="low", device=idx)
        self.stream.start()
        print(f"audio out: {self.device_name!r}"
              f"{' (system default)' if idx is None else ''}", flush=True)

    def _hard_reinit_locked(self):
        """Tear PortAudio down and bring it back, then reopen the stream.

        PortAudio enumerates devices ONCE at initialization, so a headset
        plugged in after the server started is invisible -- and a headset
        unplugged and replugged can come back as a different index behind the
        same name. Nothing short of re-initializing sees that. Caller holds
        audio_lock."""
        try:
            sd._terminate()
            sd._initialize()
        except Exception as e:
            print("portaudio reinit failed:", e, flush=True)
        self._open_stream_locked()

    def _reset_audio(self):
        """Called before every utterance. Escalates: cheap abort/start, then
        a fresh stream, then a full PortAudio re-init.

        The last rung is what recovers from a device replug: the old handle is
        bound to a device index that may no longer mean the same thing, and
        re-opening alone still reads a stale device list."""
        with self.audio_lock:
            try:
                self.stream.abort()
                self.stream.start()
                return
            except Exception as e:
                print("audio reset failed:", e, flush=True)
            try:
                self.stream.close()
            except Exception:
                pass
            try:
                self._open_stream_locked()
                print("audio stream recreated ok", flush=True)
                return
            except Exception as e:
                print("audio stream recreate failed:", e, flush=True)
            try:
                self._hard_reinit_locked()
                print("audio recovered after portaudio reinit", flush=True)
            except Exception as e:
                print("portaudio reinit recovery failed:", e, flush=True)

    def _ensure_default_device(self):
        """When following the system default, notice that Windows has switched
        output (headphones pulled, monitor speakers taking over) and move the
        stream with it.

        Honest limitation: this reads PortAudio's *cached* default, and that
        cache is only rebuilt by a re-init, so it catches the case where the
        default flips while the device list is otherwise unchanged and can
        miss a genuine unplug. The tray's "Reconnect audio device" does the
        unconditional re-init and remains the guaranteed path."""
        if self.device_spec is not None:
            return
        try:
            current = sd.query_devices(kind="output")["name"]
        except Exception:
            return
        if current and current != self.device_name:
            print(f"default output changed: {self.device_name!r} -> "
                  f"{current!r} -- reopening", flush=True)
            with self.audio_lock:
                try:
                    self.stream.close()
                except Exception:
                    pass
                try:
                    self._hard_reinit_locked()
                except Exception as e:
                    print("reopen on default change failed:", e, flush=True)

    def set_device(self, spec):
        """Switch output device (spec, or None to follow the Windows default).

        Goes through the full re-init so a device plugged in *since* startup
        can be selected -- otherwise it would not be in PortAudio's list to
        resolve against."""
        with self.lock:
            self.gen += 1              # stop playback before the swap
        self._drain(self.audio_q)
        with self.cv:
            self.pending.clear()
            self.play_until = 0.0
        with self.audio_lock:
            try:
                self.stream.close()
            except Exception:
                pass
            self.device_spec = spec or None
            self._hard_reinit_locked()
        return self.device_name

    def refresh_devices(self):
        """Re-enumerate without changing the selection (settings panel opens
        and the tray's Reconnect action)."""
        return self.set_device(self.device_spec)

    @staticmethod
    def _drain(q):
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def speak(self, text):
        atoms = split_atoms(text)
        with self.lock:
            self.gen += 1
            gen = self.gen
            self.t_speak = time.perf_counter()
            self.source = text          # original, pre-sanitize: /utterance
            self.source_gen = gen
            self.chunk_seq = []
            self.play_pos = -1
        self._ensure_default_device()
        self._reset_audio()
        self._drain(self.audio_q)
        with self.cv:
            self.pending.clear()
            for a in atoms:
                self.pending.append((gen, a))
            self.play_until = 0.0
            self.cv.notify_all()

    def stop(self):
        with self.lock:
            self.gen += 1
            self.chunk_seq = []
            self.play_pos = -1
        self.now = None
        self._reset_audio()
        self._drain(self.audio_q)
        with self.cv:
            self.pending.clear()
            self.play_until = 0.0

    def _target_chars(self):
        """How many characters can we afford to synthesize right now?
        Nothing playing -> start small. Otherwise spend SAFETY of the banked
        playback time, converted to chars via the density we've measured."""
        budget = self.play_until - time.perf_counter()
        if budget <= 0.05:
            # nothing banked: buy FIRST_CHUNK_AUDIO seconds at the learned
            # density, so START is constant regardless of how dense the text is
            return int(max(15, min(CHUNK_CHARS,
                           FIRST_CHUNK_AUDIO / max(self.density, 1e-4))))
        audio_affordable = budget * SAFETY * self.rt
        return int(max(MIN_CHUNK_CHARS,
                       min(CHUNK_CHARS, audio_affordable / max(self.density, 1e-4))))

    def _take(self):
        """Pack whole clause atoms up to the current budget. Atoms are never
        sliced here: a chunk boundary inside a clause costs audible prosody,
        an overshoot only costs START/bank time. An underfilled chunk (below
        half target) also takes the next atom whole - a tiny chunk banks so
        little audio that the following one can't be synthesized in time.
        The audit calls this "the 2.24x constraint", but 2.24 was never a
        property of this algorithm: it is rt / PLAYBACK_SPEED on the ORIGINAL
        desktop (4.03 / 1.8). The real constraint is that ratio on whatever
        machine is running, and it must stay above 1.0 or no packing strategy
        helps - see the 2026-08-11 entry in AUDIT §8.
        Returns (gen, buf, target, final) - final marks the utterance end."""
        with self.cv:
            while not self.pending:
                self.cv.wait()
            gen = self.pending[0][0]
            target = self._target_chars()
            buf = ""
            while self.pending and self.pending[0][0] == gen:
                atom = self.pending[0][1]
                if (buf and wlen(buf) >= target // 2
                        and wlen(buf) + wlen(atom) + 1 > target):
                    break
                self.pending.popleft()
                buf = (buf + " " + atom).strip() if buf else atom
            final = not (self.pending and self.pending[0][0] == gen)
            return gen, buf, target, final

    def _synth_loop(self):
        while True:
            gen, buf, target, final = self._take()
            if not buf or gen != self.gen:
                continue
            try:
                t0 = time.perf_counter()
                audio, sr, words = self.engine.synth(buf)
                dt = time.perf_counter() - t0
            except Exception as e:
                print("synth failed:", e, flush=True)
                continue
            if audio is None or gen != self.gen:
                continue

            d_raw = len(audio) / sr
            audio, lead = trim_silence(audio, sr)
            d = len(audio) / sr
            words = [(t, n, min(max(s - lead, 0.0), d),
                            min(max(e - lead, 0.0), d))
                     for t, n, s, e in words]
            # learn from what actually happened. density is SPEECH seconds
            # per weighted char - Kokoro's fixed ~0.7s silence padding used
            # to be counted, which inflated density after every short read
            # and shrank all later chunks. rt stays on raw audio (the §4 fit).
            w = wlen(buf)
            self.density = 0.7 * self.density + 0.3 * (d / max(w, 1))
            self.rt = 0.7 * self.rt + 0.3 * (d_raw / max(dt, 1e-6))
            # persist so the NEXT boot starts calibrated. Early chunks are
            # written too: a session that only ever does short reads would
            # otherwise never reach the periodic save, and boot from the
            # conservative seed forever.
            self._calib_n += 1
            if self._calib_n <= 3 or self._calib_n % 20 == 0:
                save_calibration(self.density, self.rt)
            if VERBOSE:
                print(f"synth {len(buf):3d}ch {w:3d}w -> {d:5.1f}s audio in "
                      f"{dt*1000:6.0f}ms ({d_raw/max(dt,1e-6):5.1f}x RT) "
                      f"[want {target:3d}w, dens {self.density:.3f}]", flush=True)

            # only a bare mid-clause cut (rare: oversized clause) needs the
            # final-word repair; a chunk that ends the utterance keeps its
            # natural final lengthening even without punctuation
            last = buf.rstrip('"\')')[-1:]
            if not final and last not in ".!?…,;:":
                audio, words = compress_final_word(audio, sr, words)
            audio = time_stretch(audio, sr, PLAYBACK_SPEED)
            # word times in playback coordinates, for the /now endpoint
            wordmap = [(t, round(s / PLAYBACK_SPEED, 3),
                           round(e / PLAYBACK_SPEED, 3))
                       for t, _, s, e in words]
            # a real sentence end - or the end of the whole selection - earns
            # a real pause; clause boundaries and cuts get almost none
            ends_sentence = final or last in ".!?…"
            pause = SENTENCE_PAUSE if ends_sentence else CUT_PAUSE
            if pause > 0:
                audio = np.concatenate(
                    [audio, np.zeros(int(pause * sr), np.float32)])
            now = time.perf_counter()
            with self.lock:
                # a /speak that arrived after the gen check above must not
                # inherit this chunk's playback time as its budget
                if gen != self.gen:
                    continue
                self.play_until = max(self.play_until, now) + len(audio) / sr
                # caption context (RELEASE_PLAN §3.1/3.2): index this chunk
                # by synthesis order, walked positionally in _play_loop
                self.chunk_seq.append({"text": buf, "ends_sentence": ends_sentence})
            self.audio_q.put((gen, audio, sr, buf, wordmap))

    def _play_loop(self):
        last_gen = None
        while True:
            starved = self.audio_q.empty()
            t0 = time.perf_counter()
            gen, audio, sr, buf, wordmap = self.audio_q.get()
            waited = time.perf_counter() - t0
            if gen != self.gen:
                continue
            self.play_pos += 1
            # what /now reports; replaced whole so reads stay consistent
            self.now = {"gen": gen, "t0": time.perf_counter(),
                        "dur": len(audio) / sr, "text": buf, "words": wordmap}
            if gen != last_gen:
                last_gen = gen
                if VERBOSE:
                    lat = (time.perf_counter() - self.t_speak) * 1000
                    print(f"START  first sound {lat:6.0f}ms after hotkey", flush=True)
            elif VERBOSE and starved and waited > 0.05:
                print(f"GAP    {waited*1000:6.0f}ms mid-stream", flush=True)
            try:
                for i in range(0, len(audio), 2048):
                    if gen != self.gen:
                        break
                    with self.audio_lock:
                        if gen != self.gen:
                            break
                        self.stream.write(audio[i:i + 2048])
            except Exception as e:
                print("playback error:", e, flush=True)


app = Flask(__name__)
player = None

# the overlay polls /now up to 12x/s all day; keep it out of server.log
import logging
logging.getLogger("werkzeug").addFilter(lambda r: "/now" not in r.getMessage())


@app.post("/speak")
def speak():
    text = (request.get_json(force=True, silent=True) or {}).get("text", "")
    if not text.strip():
        return jsonify(ok=False, error="empty"), 400
    player.speak(text)
    return jsonify(ok=True, chars=len(text))


@app.post("/stop")
def stop():
    player.stop()
    return jsonify(ok=True)


# end of sentence = terminal punctuation (plus any closing quote/bracket)
# followed by whitespace. Sentence ends land in the MIDDLE of chunks, so
# this runs over the text, never over chunk boundaries - see below.
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"')\]]*\s+")


def _word_char_end(text, words, idx):
    """Char offset in `text` just past the word at `idx`.

    `words` carries only the spoken tokens; punctuation and spacing live
    in `text`, so the tokens are walked forward through it the same way
    the caption strip used to map them."""
    if idx < 0:
        return 0
    p = 0
    for i, (w, _a, _b) in enumerate(words):
        j = text.find(w, p)
        if j >= 0:
            p = j + len(w)
        if i == idx:
            return p
    return len(text)


def _sentence_context(seq, pos, within=None):
    """Return (previous sentence, current sentence, next sentence) around
    the point currently being spoken, for the caption strip.

    `within` is how many characters of the chunk at `pos` have actually
    been spoken (from the word timings). Without it the whole chunk
    counts as played, and since one chunk here can hold several whole
    sentences, the highlight would jump straight to the last sentence of
    the chunk and sit there while the earlier ones are still being read.

    Two things this deliberately does NOT do, each a bug that shipped:

    - It does not take context from the neighbouring CHUNKS. Chunks cut
      mid-sentence, so the chunk before `pos` is usually part of the very
      sentence being highlighted, and the strip painted it twice: once
      dimmed as "already spoken", once inside the highlight.
    - It does not group by each chunk's `ends_sentence` flag either. That
      flag only reports whether a chunk's LAST character is terminal
      punctuation (_synth_loop), but chunks are packed to a character
      budget and routinely swallow several sentence ends mid-chunk. On a
      fast machine the chunks are large enough that no boundary is found
      until the utterance ends, and the whole passage highlights as one
      "sentence".

    So sentences are found by splitting the reconstructed text, and the
    current one is truncated at the chunk actually being played - that
    truncation is what makes it the sentence "so far".

    `seq` runs ahead of `pos` (synthesis leads playback), which is what
    makes the upcoming text available at all."""
    if not (0 <= pos < len(seq)):
        return "", "", ""
    texts = [c["text"] for c in seq]
    full = " ".join(texts)
    if within is None:
        played_end = len(" ".join(texts[:pos + 1]))
    else:
        head = len(" ".join(texts[:pos]))            # start of this chunk
        played_end = (head + 1 if pos else 0) + within

    # sentence boundaries as offsets into `full`
    bounds = [0] + [m.end() for m in _SENTENCE_END.finditer(full)] + [len(full)]
    i = next((k for k in range(len(bounds) - 1)
              if bounds[k] < played_end <= bounds[k + 1]), len(bounds) - 2)

    start, end = bounds[i], bounds[i + 1]
    # the WHOLE current sentence highlights, not just the spoken part of
    # it: the strip is meant to be read slightly ahead of the voice, and
    # a highlight that grew word by word reflowed the text constantly.
    # `within` decides WHICH sentence is current, nothing more.
    prev_text = full[bounds[i - 1]:start] if i > 0 else ""
    nend = bounds[i + 2] if i + 2 < len(bounds) else len(full)
    return prev_text.strip(), full[start:end].strip(), full[end:nend].strip()


@app.get("/now")
def now():
    """What is being spoken right now, for the caption overlay: the chunk
    text, its word timings, which word is sounding, and the sentence
    being spoken with one sentence of context on either side
    (RELEASE_PLAN 3.1/3.2). The strip renders `sentence` directly - it
    does no chunk merging of its own."""
    s = player.now
    if not s or s["gen"] != player.gen:
        return jsonify(active=False)
    t = time.perf_counter() - s["t0"]
    if t > s["dur"] + 0.3:
        return jsonify(active=False)
    idx = -1
    for i, (_, a, _b) in enumerate(s["words"]):
        if a <= t:
            idx = i
        else:
            break
    seq, pos = player.chunk_seq, player.play_pos
    # how far into THIS chunk the voice has actually got, so a chunk
    # holding several sentences still highlights the right one. Only
    # trustworthy while chunk_seq[pos] is the chunk /now is describing -
    # the two are written by different threads.
    within = (_word_char_end(s["text"], s["words"], idx)
              if 0 <= pos < len(seq) and seq[pos]["text"] == s["text"]
              else None)
    prev_text, sentence, next_text = _sentence_context(seq, pos, within)
    ends_sentence = seq[pos]["ends_sentence"] if 0 <= pos < len(seq) else False
    return jsonify(active=True, text=s["text"], words=s["words"],
                   word=idx, t=round(t, 3), utt=s["gen"],
                   sentence=sentence, prev=prev_text, next=next_text,
                   ends_sentence=ends_sentence)


@app.get("/utterance")
def utterance():
    """Original (pre-sanitize) text of the current utterance, so the native
    highlighter can anchor it inside the source document via UI Automation."""
    return jsonify(utt=player.source_gen, text=player.source)


@app.route("/config", methods=["GET", "POST"])
def config():
    global SENTENCE_PAUSE, PLAYBACK_SPEED, FIRST_CHUNK_AUDIO
    if request.method == "POST":
        d = request.get_json(force=True, silent=True) or {}
        if "voice" in d:
            player.engine.voice = d["voice"]
        if "model_speed" in d:
            player.engine.model_speed = float(d["model_speed"])
        if "playback_speed" in d:
            PLAYBACK_SPEED = float(d["playback_speed"])
        if "pause" in d:
            SENTENCE_PAUSE = float(d["pause"])
        if "first_chunk_audio" in d:
            FIRST_CHUNK_AUDIO = float(d["first_chunk_audio"])
        if "output_device" in d:
            # reopens the stream (and re-enumerates), so only touch it when
            # the value actually changed -- the tray pushes the whole config
            # on every slider drag
            want = d["output_device"] or None
            if want != player.device_spec:
                player.set_device(want)
    return jsonify(voice=player.engine.voice,
                   model_speed=player.engine.model_speed,
                   playback_speed=PLAYBACK_SPEED,
                   effective_speed=round(player.engine.model_speed * PLAYBACK_SPEED, 2),
                   pause=SENTENCE_PAUSE,
                   first_chunk_audio=FIRST_CHUNK_AUDIO,
                   output_device=player.device_spec,
                   output_device_name=player.device_name,
                   measured_density=round(player.density, 4),
                   measured_rt=round(player.rt, 2))


@app.route("/devices", methods=["GET", "POST"])
def devices():
    """List output devices. POST (or ?refresh=1) re-initializes PortAudio
    first, which is the only way a device plugged in since startup appears."""
    if request.method == "POST" or request.args.get("refresh") == "1":
        player.refresh_devices()
    return jsonify(devices=list_output_devices(),
                   current=player.device_spec,
                   current_name=player.device_name)


if __name__ == "__main__":
    # before the engine: it reads KOKORO_VOICE and MODEL_SPEED at construction
    load_user_settings()
    engine = KokoroOnnxEngine() if ENGINE == "onnx" else KokoroEngine()
    # the first synth after model load runs 2-3x slower than steady state
    # (measured: 2.4-3.1x RT vs 4.0x warm). Pay that cost now, not on the
    # first hotkey press of the day.
    t0 = time.perf_counter()
    engine.synth("Warm up.")
    print(f"[{engine.name}] warmed up in "
          f"{(time.perf_counter() - t0) * 1000:.0f}ms", flush=True)
    player = Player(engine)
    print(f"[{engine.name}] ready on http://{HOST}:{PORT}", flush=True)
    app.run(host=HOST, port=PORT, threaded=True)
