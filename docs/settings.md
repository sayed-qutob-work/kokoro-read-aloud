# Settings

- [Where to change them](#where-to-change-them)
- [The settings file](#the-settings-file)
- [Voices](#voices)
- [Reading speed](#reading-speed)
- [Caption strip](#caption-strip)
- [Changing settings over HTTP](#changing-settings-over-http)

## Where to change them

**The Settings panel** has everything on this page. It also shows the
fastest reading speed your machine keeps up with, and warns before you pick a
faster one. To open it:
- **Windows:** the tray icon.
- **Omarchy:** More settings, in the bar panel.
- **GNOME:** "Kokoro Settings" in the app grid.
- **Any Linux:** `kokoroctl settings`.

**On Omarchy, the bar panel** changes the voice and the reading speed
directly ([omarchy.md](omarchy.md#the-panel)). So does
`kokoroctl set voice=NAME speed=X` from a terminal.

All of them save to the same file, with the same rules (`kokoro_settings.py`).

## The settings file

`settings.json` is loaded by the server at startup:
- on Linux: `~/.config/kokoro-read-aloud/settings.json`;
- on Windows: next to the code, untracked by git.

[settings.example.json](../settings.example.json) shows its format and the
shipped defaults.

| Key | What |
|---|---|
| `voice` | One of the [voices](#voices), e.g. `af_heart` |
| `playback_speed` | The speed-up applied after synthesis, keeping pitch. Set it above what the machine can synthesize and reads stall |
| `model_speed` | The speed Kokoro itself is asked for. The server keeps it between 0.5 and 1.3: above 1.3 the voice garbles |
| `pause` | Silence after a sentence, in seconds |
| `first_chunk_audio` | Seconds of audio in the opening chunk. Lower starts faster but sounds choppier |
| `output_device` | `null` follows the system default |
| `caption_*` | The [caption strip](#caption-strip) |

## Voices

All 28 English voices of Kokoro-82M. The grade is the overall grade in
Kokoro's own [VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md),
at the pinned revision.
- **★ recommended:** graded B- or better.
- **"best male":** no male voice is graded that high, so this marks the best
  male grade, C+.

| Voice | Accent and gender | Grade | Mark |
|---|---|---|---|
| `af_heart` | American female | A | ★ recommended |
| `af_bella` | American female | A- | ★ recommended |
| `af_nicole` | American female | B- | ★ recommended |
| `af_aoede` | American female | C+ | |
| `af_kore` | American female | C+ | |
| `af_sarah` | American female | C+ | |
| `af_nova` | American female | C | |
| `af_sky` | American female | C- | |
| `af_alloy` | American female | C | |
| `af_jessica` | American female | D | |
| `af_river` | American female | D | |
| `am_michael` | American male | C+ | best male |
| `am_fenrir` | American male | C+ | best male |
| `am_puck` | American male | C+ | best male |
| `am_echo` | American male | D | |
| `am_eric` | American male | D | |
| `am_liam` | American male | D | |
| `am_onyx` | American male | D | |
| `am_adam` | American male | F+ | |
| `am_santa` | American male | D- | |
| `bf_emma` | British female | B- | ★ recommended |
| `bf_isabella` | British female | C | |
| `bf_alice` | British female | D | |
| `bf_lily` | British female | D | |
| `bm_george` | British male | C | |
| `bm_fable` | British male | C | |
| `bm_lewis` | British male | D+ | |
| `bm_daniel` | British male | D | |

The voice's accent also picks the pronunciation: American for `af_`/`am_`,
British for `bf_`/`bm_`.

## Reading speed

The reading speed you set is split in two:
- **`model_speed`:** what Kokoro itself is asked for, kept at 1.3 or
  below;
- **`playback_speed`:** a time-stretch after synthesis, which keeps the
  voice's pitch.

Your reading speed is the product of the two.

Playback uses up `playback_speed` seconds of audio per second, and synthesis
has to produce them faster than that. Otherwise long reads stall. The panel
compares your speed against your machine's measured synthesis speed and warns
when it is close or over. See [how-it-works.md](how-it-works.md#cpu-or-gpu)
for measured numbers.

## Caption strip

`overlay.py` shows the sentence being read in a strip that appears while
speech plays. It hides about 0.7 s after speech stops. Drag it to move it,
right-click it to close it. It gets the text from the server, so it works
for terminals, PDFs and everything else.

It is off by default. To start it:
- **Omarchy:** the Captions button in the bar panel.
- **Any Linux:** `systemctl --user enable --now kokoro-overlay`, which also
  starts it at every login.
- **Windows:** `env\Scripts\pythonw.exe overlay.py`. The current version of
  the strip has not been run on Windows yet.

Its options are in the Settings panel:

| Setting | Values | |
|---|---|---|
| `caption_layout` | `teleprompter`, `rows` | Teleprompter wraps the whole passage and scrolls it past a fixed reading line. Rows is a static three-line strip |
| `caption_scroll` | `continuous`, `line`, `off` | Teleprompter only. `continuous` creeps at reading pace, `line` glides a line at a time, `off` snaps |
| `caption_style` | `underline`, `terminal`, `rail` | Three visual themes |
| `caption_position` | `bottom`, `center`, `top` | |
| `caption_monitor` | `primary` or a connector name (`DP-1`) | Monitors are listed by the compositor |

The strip reads these when it starts, so changing one restarts it. The
Settings panel does that for you when you save.

**On Hyprland, "Primary monitor"** means the first monitor `hyprctl monitors`
lists, because Hyprland has no primary monitor. Pick a connector such as
`DP-3` to be explicit.

## Changing settings over HTTP

Everything except the `caption_*` keys can be changed live:

```bash
curl -X POST http://127.0.0.1:5111/config -H 'Content-Type: application/json' \
     -d '{"playback_speed":2.0}'
curl http://127.0.0.1:5111/config          # current values, device and measured speed
```

Changes made this way last until the server restarts; the panel or
`kokoroctl set` saves them. Every POST must be `application/json`, see
[how-it-works.md](how-it-works.md#who-may-talk-to-the-server).
