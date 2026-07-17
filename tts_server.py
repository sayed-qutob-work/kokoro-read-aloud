r"""
Local read-aloud server.  POST text, it speaks.

    env\Scripts\python.exe tts_server.py

Endpoints:
    POST /speak    {"text": "..."}
    POST /stop     {}
    POST /config   {"voice":..., "model_speed":..., "playback_speed":..., "pause":...}
    GET  /config
"""

import re
import time
import queue
import threading
from collections import deque

import numpy as np
import sounddevice as sd
from flask import Flask, request, jsonify

# ======================= TUNE THESE =======================
ENGINE = "torch"          # "torch" = kokoro (PyTorch)  -> measured 4.03x RT
                          # "onnx"  = kokoro-onnx       -> measured 3.89x RT (slower)

ONNX_MODEL  = r"C:\kokoro\kokoro-v1.0.onnx"
ONNX_VOICES = r"C:\kokoro\voices-v1.0.bin"

KOKORO_VOICE = "af_heart"

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
        self.density = 0.075                 # sec of audio per character
        self.rt = 4.0                        # synthesis throughput, x realtime
        self.now = None                      # chunk being played, for /now

        self.lock = threading.Lock()
        self.cv = threading.Condition()
        self.stream = sd.OutputStream(samplerate=24000, channels=1,
                                      dtype="float32", blocksize=1024,
                                      latency="low")
        self.stream.start()
        # PortAudio is NOT thread-safe: abort() during write() can hang it.
        self.audio_lock = threading.Lock()
        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    def _reset_audio(self):
        with self.audio_lock:
            try:
                self.stream.abort()
                self.stream.start()
            except Exception as e:
                print("audio reset failed:", e, flush=True)

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
        little audio that the following one can't be synthesized in time
        (the 2.24x constraint, §4 of the audit).
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
            pause = SENTENCE_PAUSE if final or last in ".!?…" else CUT_PAUSE
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


@app.get("/now")
def now():
    """What is being spoken right now, for the caption overlay: the chunk
    text, its word timings, and which word is sounding at this instant."""
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
    return jsonify(active=True, text=s["text"], words=s["words"],
                   word=idx, t=round(t, 3))


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
    return jsonify(voice=player.engine.voice,
                   model_speed=player.engine.model_speed,
                   playback_speed=PLAYBACK_SPEED,
                   effective_speed=round(player.engine.model_speed * PLAYBACK_SPEED, 2),
                   pause=SENTENCE_PAUSE,
                   first_chunk_audio=FIRST_CHUNK_AUDIO,
                   measured_density=round(player.density, 4),
                   measured_rt=round(player.rt, 2))


if __name__ == "__main__":
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
