# Quran Video Generator — Surah Al-Fatihah

Makes one YouTube-ready video of **Surah Al-Fatihah**, ayah by ayah.

The project supports two shapes, chosen with `content.urdu_translation`:

```
Arabic only (what ships)          With the Urdu translation
                                  content.urdu_translation: true
  ayah 1  Arabic + recitation       ayah 1  Arabic + recitation
  ayah 2  Arabic + recitation               →  Urdu translation
  …                                 ayah 2  Arabic + recitation
  ayah 7  Arabic + recitation               →  Urdu translation
fade out                                  …
                                    ayah 7  Arabic + recitation
                                            →  Urdu translation
                                  fade out
about 40s                         about 1m 29s
```

Each word lights up as the reciter reaches it, from published per-word
timings rather than a guess — see
[Highlighting the word being recited](#highlighting-the-word-being-recited).

The shipped config opens straight on ayah 1 — no title card — and ends on a
short fade rather than a closing card. Both are switches, not hard-coded:
`animation.intro_duration` and `content.outro_card`
([section 10](#10-customising-the-look-configyaml)).

The Urdu section is off in the shipped config; see
[section 12](#12-how-the-audio-is-handled) for why, and how to turn it on.

It produces:

```
output/001_Al-Fatihah.mp4              1920x1080, 30 fps, H.264 + AAC
output/001_Al-Fatihah.jpg              1280x720 thumbnail
output/captions/001_Al-Fatihah.srt     subtitles, right-to-left
output/captions/001_Al-Fatihah.ass     styled subtitles
```

Nothing is burned into the picture. The subtitles live one folder down on
purpose — see [Subtitles](#subtitles).

This first version supports **Surah Al-Fatihah only**. No batch mode, no other
surahs, no uploading.

---

## Table of contents

1. [What you need](#1-what-you-need)
2. [Install Python](#2-install-python)
3. [Install FFmpeg](#3-install-ffmpeg)
4. [Set the project up](#4-set-the-project-up)
5. [Add your audio](#5-add-your-audio)
6. [Fonts](#6-fonts)
7. [Backgrounds and branding](#7-backgrounds-and-branding)
8. [Make the video](#8-make-the-video)
9. [All the commands](#9-all-the-commands)
10. [Customising the look](#10-customising-the-look-configyaml)
11. [Where the Quran text comes from](#11-where-the-quran-text-comes-from)
12. [How the audio is handled](#12-how-the-audio-is-handled)
13. [Troubleshooting](#13-troubleshooting)
14. [How it works inside](#14-how-it-works-inside)
15. [Running the tests](#15-running-the-tests)

---

## 1. What you need

| | |
|---|---|
| Windows 10 / 11 (macOS and Linux also work) | |
| Python 3.11 or newer | [step 2](#2-install-python) |
| FFmpeg | [step 3](#3-install-ffmpeg) — the setup script can handle this for you |
| An Arabic recitation of Al-Fatihah, split into 7 files | [step 5](#5-add-your-audio) |
| An Urdu narration of the translation, split into 7 files | [step 5](#5-add-your-audio) |
| About 2 GB of free disk space | |

Fonts, the Quran text and the background artwork are all handled for you.

---

## 2. Install Python

1. Go to <https://www.python.org/downloads/> and download Python 3.12 (or newer).
2. Run the installer.
3. **Important:** on the first screen, tick **“Add python.exe to PATH”**, then
   click *Install Now*.
4. Check it worked. Open VS Code, then **Terminal → New Terminal**, and type:

   ```powershell
   python --version
   ```

   You should see something like `Python 3.12.4`. If you instead see
   *“python is not recognized”*, Python was installed without being added to
   PATH — re-run the installer, choose *Modify*, and tick the PATH box.

---

## 3. Install FFmpeg

FFmpeg is the tool that actually encodes the video. You have two options.

### Option A — let pip do it (easiest)

Nothing to do. `requirements.txt` includes `imageio-ffmpeg`, which ships a
working FFmpeg binary. `setup.bat` installs it, and the project finds it
automatically.

> That bundled build includes `ffmpeg` but not `ffprobe`. The project does not
> need `ffprobe` — it reads durations from FFmpeg itself — so this is fine.

### Option B — install FFmpeg properly (recommended if you use FFmpeg elsewhere)

1. Go to <https://www.gyan.dev/ffmpeg/builds/> and download
   **ffmpeg-release-essentials.zip**.
2. Unzip it to `C:\ffmpeg`. You should end up with `C:\ffmpeg\bin\ffmpeg.exe`.
3. Add it to your PATH:
   - Press <kbd>Win</kbd>, type *environment variables*, open
     **Edit the system environment variables**.
   - Click **Environment Variables…**
   - Under *User variables*, select **Path** → **Edit** → **New**.
   - Enter `C:\ffmpeg\bin` and click OK on every window.
4. **Close and reopen VS Code**, then check:

   ```powershell
   ffmpeg -version
   ```

If you would rather not touch PATH, copy `.env.example` to `.env` and set:

```
QVG_FFMPEG=C:\ffmpeg\bin\ffmpeg.exe
QVG_FFPROBE=C:\ffmpeg\bin\ffprobe.exe
```

---

## 4. Set the project up

Open this folder in VS Code (**File → Open Folder…**), open a terminal
(**Terminal → New Terminal**) and run:

```powershell
.\setup.bat
```

On macOS or Linux:

```bash
chmod +x setup.sh && ./setup.sh
```

The script:

- creates a virtual environment in `.venv`
- installs everything in `requirements.txt`
- downloads the SIL OFL Arabic, Urdu and Latin fonts
- downloads the verified Quran text into `data/al_fatihah.json`

Afterwards, every command in this README starts with `.venv\Scripts\python`.
To avoid typing that each time, activate the environment once per terminal:

```powershell
.venv\Scripts\Activate.ps1
```

Then `python run.py …` works directly.

> If PowerShell refuses with *“running scripts is disabled on this system”*, run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, answer **Y**, and
> try again. Or just keep typing the full `.venv\Scripts\python` path.

**Tip:** tell VS Code to use this environment — press
<kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd>, choose
*Python: Select Interpreter*, and pick the one inside `.venv`.

---

## 5. Add your audio

### The quickest route

`config.yaml` ships pointed at a verse-by-verse source, so one command fills both
folders:

```powershell
python run.py fetch-audio --accept-source-license
```

That downloads 7 Arabic files and 7 Urdu files:

| | |
|---|---|
| Arabic recitation | **Mishary Rashid Alafasy** — murattal, edition `ar.alafasy` |
| Urdu narration | **Shamshad Ali Khan** — edition `ur.khan` |
| Served by | `cdn.islamic.network`, the audio service behind the AlQuran Cloud API |

> **The Urdu narration is switched off by default** (`audio.urdu_narration:
> false`), and the files above are downloaded but unused. The reason is in
> [section 12](#12-how-the-audio-is-handled): the only published Urdu
> ayah-by-ayah narration recites a *different* translation from the one shown
> on screen. Set it to `true` to use it anyway.

Both flags are deliberate: nothing downloads unless `audio_sources.enabled` is
true **and** you pass `--accept-source-license`. Quran recitation is a recording
with its own copyright — it is distributed widely for da'wah and study, but
confirm the reciter's terms before monetising a channel with it. When you have,
set `reciter.rights_confirmed: true`.

To use a different reciter, change the edition id in
`audio_sources.recitation_url_template`. Verified working: `ar.alafasy`,
`ar.husary`, `ar.minshawi`, `ar.mahermuaiqly`. The full list is at
<https://api.alquran.cloud/v1/edition?format=audio>.

Everything below describes supplying your own files instead.

### The Arabic recitation

Put **one file per ayah** in `assets/audio/recitation/`:

```
assets/audio/recitation/001.mp3      ← بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ
assets/audio/recitation/002.mp3
assets/audio/recitation/003.mp3
assets/audio/recitation/004.mp3
assets/audio/recitation/005.mp3
assets/audio/recitation/006.mp3
assets/audio/recitation/007.mp3
```

`.wav`, `.m4a`, `.ogg` and `.flac` work too — just keep the `001`…`007` names.

This must be a **real recording of a real reciter**. Do not use text-to-speech
or a voice clone for Quranic Arabic.

### Or: one complete recording

If you have a single file of the whole surah, put it at
`assets/audio/recitation/al_fatihah.mp3` and write down where each ayah starts
and ends in `config.yaml`:

```yaml
audio:
  recitation_mode: "full_surah"
  full_surah_file: "al_fatihah.mp3"
  full_surah_timings:
    - {ayah: 1, start: 0.00,  end: 6.42}
    - {ayah: 2, start: 6.42,  end: 12.10}
    - {ayah: 3, start: 12.10, end: 15.80}
    - {ayah: 4, start: 15.80, end: 19.95}
    - {ayah: 5, start: 19.95, end: 26.30}
    - {ayah: 6, start: 26.30, end: 31.05}
    - {ayah: 7, start: 31.05, end: 42.60}
```

Measure those numbers yourself — in Audacity, or any player that shows
timecodes. `run.py validate` checks that they fit inside the recording.

Leave `recitation_mode: "auto"` and the project prefers the per-ayah files and
falls back to the complete recording.

### The Urdu narration

Put the spoken Urdu translation in `assets/audio/urdu/`, same naming:

```
assets/audio/urdu/001.mp3 … 007.mp3
```

Each file should correspond to the Urdu text shown for that ayah.

### Credit the reciter

Open `config.yaml` and fill in:

```yaml
reciter:
  name: "Full name of the reciter"
  source: "Where the recording came from"
  license: "The licence or permission you have"
  rights_confirmed: true
```

This goes into the MP4's metadata, and onto the closing card when
`content.outro_card` is on (it ships off). Recitations are recordings with their
own copyright — use only material you own, have permission for, or that is
offered under a licence permitting reuse.

### Downloading audio

The project **never** downloads recitation on its own. If you have a source you
are allowed to use, set it up in `config.yaml`:

```yaml
audio_sources:
  enabled: true
  recitation_url_template: "https://example.org/reciter/001{ayah:03d}.mp3"
  urdu_url_template: ""
```

and then run, explicitly:

```powershell
python run.py fetch-audio --accept-source-license
```

### No audio yet?

You can still see the design, the typography and the pacing:

```powershell
python run.py preview --no-audio
```

That renders with silence and estimated durations. It is a layout check, not a
finished video.

---

## 6. Fonts

`setup.bat` already downloaded these into `assets/fonts/`:

| Font | Used for | Licence |
|---|---|---|
| Amiri Quran | the Quranic Arabic | SIL OFL |
| Amiri Regular / Bold | Arabic headings | SIL OFL |
| Noto Nastaliq Urdu | the Urdu translation | SIL OFL |
| Inter | Latin text in the `ios` style | SIL OFL |
| Cormorant Garamond | Latin text in the `classic` style | SIL OFL |

Every one of these is under the SIL Open Font License, which permits
redistribution — which is why they can be downloaded for you. **SF Pro, the
typeface iOS itself uses, is not**: Apple licenses it for use on Apple
platforms only, so it is neither shipped nor fetched here. Inter stands in for
it.

To fetch them again, or after deleting one:

```powershell
python run.py fonts
```

To use a **different** font, drop the `.ttf` or `.otf` into `assets/fonts/` and
name it in `config.yaml`:

```yaml
fonts:
  arabic: "MyQuranFont.ttf"
  urdu: "JameelNooriNastaleeq.ttf"
```

`run.py validate` then checks that your font actually contains every character
in the text, and tells you which ones are missing if it does not. Only use fonts
you are licensed to use.

---

## 7. Backgrounds and branding

> **The shipped theme needs one.** `theme.style: "glass"` sets its text over
> artwork you supply — put the **plain plate** (no text on it) at
> `assets/backgrounds/fatihah.jpg`. With the folder empty it falls back to the
> generated backdrop and warns. See
> [The `glass` style needs a background image](#the-glass-style-needs-a-background-image).

With any other style you do not need one at all: the project draws its own
"Midnight Mihrab" backdrop — a deep blue-black field, an eight-point-star
lattice, a lit prayer-niche arch and a warm bloom.

To use your own, drop one file into `assets/backgrounds/`:

- **Images** — `.jpg`, `.png`, `.webp`, `.bmp`. Cropped to 16:9 (never
  stretched) and given a slow push-in and drift.
- **Video** — `.mp4`, `.mov`, `.mkv`, `.webm`. Looped if it is shorter than the
  render, and cropped to 16:9.

The first file found is used. To choose a specific one:

```yaml
background:
  source: "mosque-night.jpg"
```

Use only images and footage you are licensed to use. Nothing is ever downloaded
for you.

For a logo on the thumbnail — and on the closing card, if you turn
`content.outro_card` on — put a transparent PNG at
`assets/branding/logo.png`. To add a channel name:

```yaml
theme:
  channel_name: "Your Channel"
  channel_tagline: "Quran with Urdu translation"
```

Leave them empty and the closing card stays clean.

---

## 8. Make the video

Three commands, in this order.

### Step 1 — validate

```powershell
python run.py validate
```

Checks the Quran data, that there are exactly 7 ayahs numbered 1–7, that every
Arabic and Urdu string is present, that all 14 audio files exist and decode,
that the fonts exist and cover the text, that FFmpeg works, whether the word
timings are published or estimated, and that there is enough disk space.
Nothing renders until this passes.

### Step 2 — preview

```powershell
python run.py preview
```

A fast 640×360 draft at `output/001_Al-Fatihah_preview.mp4`. Watch it and check:

- the Arabic joins correctly and the harakat sit right
- the Urdu Nastaliq reads properly
- the recitation and the narration line up with the text
- the pacing feels right

Then adjust `config.yaml` and preview again. To iterate faster, render only the
first two ayahs:

```powershell
python run.py preview --ayahs 2
```

### Step 3 — render

```powershell
python run.py render
```

The full 1920×1080 video, plus the thumbnail and the subtitles. This takes a
while — the terminal shows a live progress bar with a time estimate.

While it runs you will see a `~001_Al-Fatihah.part.mp4` in `output/`. That is
the encode in progress; it is renamed to the real name only once FFmpeg has
finished cleanly, so an interrupted render never leaves a truncated file sitting
at the published path. If a `~…part.mp4` is left behind, the render did not
finish — delete it and run the command again.

```
output/001_Al-Fatihah.mp4
output/001_Al-Fatihah.jpg
output/captions/001_Al-Fatihah.srt
output/captions/001_Al-Fatihah.ass
```

Upload the MP4 to YouTube, set the JPG as the thumbnail, and add the SRT as a
subtitle track.

---

## 9. All the commands

| Command | What it does |
|---|---|
| `python run.py validate` | Pre-flight checks. Run this first. |
| `python run.py preview` | Fast 640×360 draft. |
| `python run.py render` | Final 1080p MP4 + thumbnail + subtitles. |
| `python run.py thumbnail` | Just the 1280×720 thumbnail. |
| `python run.py subtitles` | Just the `.srt` and `.ass` files. |
| `python run.py info` | Print the surah details and the computed timeline. |
| `python run.py fetch-text` | Download the verified Quran text and Urdu translation. |
| `python run.py fetch-word-timings` | Download per-word recitation timings (no audio). |
| `python run.py fonts` | Download the SIL OFL fonts. |
| `python run.py fetch-audio` | Opt-in recitation downloader (off by default). |
| `python run.py clean` | Empty `temp/`. |

Useful flags:

| Flag | Works with | Effect |
|---|---|---|
| `--no-audio` | `preview`, `render`, `info` | Silence instead of recitation — layout check only. |
| `--ayahs N` | `preview`, `render` | Only the first N ayahs. |
| `--force` | `fetch-text`, `fonts`, `fetch-audio` | Overwrite existing files. |
| `--recitation-id N` | `fetch-word-timings` | Use a different reciter's timings. |
| `--log-level DEBUG` | any | Verbose output, including the FFmpeg command. |
| `--config path.yaml` | any | Use a different config file. |

Every run writes a full transcript to `logs/`.

---

## 10. Customising the look (`config.yaml`)

`config.yaml` is commented throughout. The settings people change most:

### Pacing

```yaml
audio:
  padding_before: 0.20      # silence before each clip starts
  padding_after: 0.20       # silence after each clip ends
  gap_between_ayahs: 0.00   # extra breath before the next ayah

  min_arabic_seconds: 0.0   # floor on time-on-screen for the Arabic
  min_urdu_seconds: 6.5     # floor on time-on-screen for the Urdu
```

The length of each ayah on screen is **always** the real length of its audio
plus this padding. Nothing is hard-coded.

**These three are the pause you hear between ayahs.** Added together and divided
by `playback_speed`, they are the silence between one recitation ending and the
next beginning:

```
(padding_after + gap_between_ayahs + padding_before) / playback_speed
(0.20         + 0.00              + 0.20          ) / 1.25  =  0.32 s
```

Measured in the shipped render, the gaps come out at 0.365–0.379 s — the extra
few hundredths are the room tone at the head and tail of each mp3. Raise all
three for a more measured pace; the defaults are set for continuous recitation.

Squeezing the padding this far leaves no silent window for the card dissolve, so
`animation.min_text_fade_out` (0.30 s) stops the clamp shrinking it into a hard
cut. Past that floor the dissolve is allowed to begin a fraction before the clip
ends — 0.08 s at these settings, which is not perceptible.

The two minimums are the one exception, and they only ever *extend*. Some Urdu
lines are narrated in under two seconds — "انصاف کے دن کا حاکم" is four words —
which would leave the translation on screen too briefly to read. The floor holds
the card a little longer; the extra time is trailing silence, so it reads as a
pause rather than a stall. The audio still starts at `padding_before` either way.
Set both to `0` to follow the clip durations exactly.

### Style

Three treatments of the same layout, chosen with one setting:

```yaml
theme:
  style: "glass"      # or "ios", or "classic"
```

| | `glass` (what ships) | `ios` | `classic` |
|---|---|---|---|
| Ground | **your own artwork** | iOS dark: black, systemGray6 | deep blue-black |
| Ayah sits on | the picture | a rounded grouped panel | the bare background |
| Ayah number | a frosted capsule | a systemBlue pill badge | an eight-point star medallion |
| Rules | hairline + rosette | hairline separators | gold rule with a centre diamond |
| Border | none — the artwork is the frame | a rounded container | a gold frame with corner ornaments |
| Text | white with a soft shadow | no glow | a warm halo behind it |
| Latin text | Inter | Inter | Cormorant Garamond |

Only the furniture changes — every colour, size and margin below applies to
all three, and the Quranic Arabic is set in Amiri Quran whichever you choose.

### The `glass` style needs a background image

It draws no ground of its own: it sets its text straight over a picture you
supply, in frosted capsules, closing with a hairline and a rosette.

```
assets/backgrounds/fatihah.jpg
```

Use the **plain plate** — the artwork with no text on it. The project draws
`SURAH 001`, the surah name, the ayah number and every ayah itself; a plate
that already carries text ends up with two sets. Leave the middle of the frame
relatively quiet, since that is where the text sits.

If the folder holds more than one file, name the plate explicitly rather than
leaving `source: "auto"`, which takes the first file it finds:

```yaml
background:
  source: "fatihah.png"
```

The vignette and colour grade are also **off** for this style
(`theme.vignette: 0`, `grade_*` neutral). They exist to push a *generated*
backdrop back so the text wins; run over finished artwork they only darken and
dull it. Legibility comes from `theme.text_shadow_*` instead.

With the folder empty, `render` and `thumbnail` both warn and fall back to the
generated backdrop — which works, but is not what this style is for.

Because the artwork *is* the design, it is shown close to as supplied:
`background.dim` ships at `0.12` and `background.blur` at `0`, rather than the
`0.30` / `6` the generated backdrop uses. If your plate is busy behind the
centre, raise either — or strengthen `theme.text_shadow_opacity`, which is what
keeps white type legible over a photograph.

> **A note on the typeface.** iOS itself is set in **SF Pro**, which Apple
> licenses for use on Apple platforms only and does not permit redistributing.
> This project therefore does not ship it. The Latin text is **Inter**, drawn
> as an open counterpart to SF Pro and released under the SIL OFL — which is
> as close as a licensed font gets. If you have SF Pro installed and your own
> use permits it, point `fonts.latin` at it.

### Colours

In the `glass` style everything is white over your artwork, and the capsules
are frosted white, so the palette is mostly a matter of how strong the frosting
is:

```yaml
theme:
  text_color: "#FFFFFF"
  chip_color: "#FFFFFF"
  chip_opacity: 0.14          # the capsule fill
  chip_border_opacity: 0.38   # its hairline edge
  text_shadow_color: "#0B2A4A"
  text_shadow_opacity: 0.38   # what keeps white type legible on the picture
```

The `background_color` and `deep_color` only show if no artwork is supplied.

For the `ios` treatment, Apple's own dark-mode system colours:

```yaml
theme:
  background_color: "#000000"     # iOS dark background
  accent_color: "#0A84FF"         # systemBlue (dark)
  text_color: "#FFFFFF"           # label
  text_secondary_color: "#EBEBF5" # secondaryLabel
  muted_color: "#8E8E93"          # systemGray
  panel_color: "#1C1C1E"          # systemGray6 — the ayah panel
  separator_color: "#38383A"      # separator — hairlines
```

And for `classic`, the original palette:

```yaml
theme:
  background_color: "#05090C"
  accent_color: "#D8B871"     # gold — hairlines, medallion, headings
  text_color: "#F6EEDD"       # cream — the Arabic
  urdu_text_color: "#F3EADA"
```

### Type size

Sizes are fractions of the frame **height**, so the layout is identical at 360p
and 1080p. The Arabic is auto-fitted between the two bounds:

```yaml
theme:
  size_arabic_max: 0.150     # 162 px at 1080p
  size_arabic_min: 0.050
  max_lines_arabic: 4
```

### Motion

```yaml
animation:
  speed: 1.0                 # 0.5 = everything slower, 2.0 = faster
  transition_duration: 0.45
  text_fade_in: 0.70
  text_fade_out: 0.55
  intro_duration: 0.0        # seconds before playback_speed
  outro_duration: 1.5
```

### Opening and ending

`intro_duration` is a title card in front of ayah 1. Anything above zero delays
the recitation by `intro_duration / playback_speed` plus `padding_before`, so
`4.0` at 1.25x means the viewer waits about 3.4 seconds before the first word.
**It ships at `0.0`** — the video opens straight on ayah 1 and the recitation
starts immediately.

The ending is two separate settings, and they do different things:

```yaml
content:
  outro_card: false          # the "surah complete" card and the credits
animation:
  outro_duration: 1.5        # how long the video runs past the last syllable
  outro_fade_out: 1.20
```

With `outro_card: false` the closing screen is gone, but the segment still
occupies its time as **background only** — so the last ayah dissolves and the
video fades out on the moving backdrop instead of cutting dead on the reciter's
final syllable. The reciter is still credited in the MP4 metadata either way.

Set `outro_duration: 0` as well to end the file the moment the fade-out of the
last ayah finishes. Set `outro_card: true` to bring the closing card back.

Turn individual layers off if you want it plainer:

```yaml
animation:
  particles_enabled: false
  pattern_layer_enabled: false
background:
  ken_burns: false
```

### Why the drift speeds look like odd round numbers

FFmpeg positions overlays on **whole pixels**. A layer asked to drift slower
than one pixel per frame cannot glide — it sits still for a few frames, then
snaps a whole pixel, and that stutter is very visible on the crisp gold lattice.

So drift speeds are snapped to a whole number of pixels per frame. At 30 fps
the smooth speeds are 30, 60, 90 … px/s, which is why the defaults are 30 and
60 rather than 9 and 18. Whatever you set is rounded to the nearest of those,
and never down to a standstill — set a speed to `0` if you want a layer to hold
still.

Measured on the lattice: at 9 px/s, 45 of 59 frames were identical and one
jumped 4.3× the average. At exactly 1 px/frame every frame moves the same
distance — no still frames at all.

### Speed vs. file size

```yaml
video:
  crf: 18          # lower = better quality, bigger file (16–20 is the useful range)
  preset: "medium" # "slow" is ~2x slower for a slightly smaller file
```

Turning off `particles_enabled` and `light_enabled` makes the render noticeably
faster if you are in a hurry.

**Roughly what to expect** (measured on a mid-range laptop, for the shipped
40-second video):

| | |
|---|---|
| `preview` at 640×360 | about 20 seconds |
| `render` at 1920×1080 | about 6 minutes |

Render time scales with the length of your audio, so a 4-minute video takes
roughly six times as long. Almost all of it is compositing: the four drifting
background layers, and one overlay per highlighted word. The H.264 encode is
nearly free because it runs alongside them.

`content.word_highlight: false` roughly halves the render time — it removes 29
overlays from the filter graph. Turning off `particles_enabled` and
`light_enabled` saves a good deal more.

---

## 11. Where the Quran text comes from

**The Quranic Arabic in this project is never generated, paraphrased or edited.**

`python run.py fetch-text` downloads two published editions and stores them
verbatim in `data/al_fatihah.json`:

- **Arabic** — the Tanzil Uthmani text (`quran-uthmani`)
- **Urdu** — Muhammad Junagarhi's translation (`ur.junagarhi`), the one printed
  in the King Fahd Complex Urdu mushaf

Other Urdu translations are one flag away — `ur.qadri` (Tahir ul Qadri),
`ur.maududi`, `ur.kanzuliman` (Ahmed Raza Khan), `ur.najafi`, `ur.ahmedali`,
`ur.jalandhry`, `ur.jawadi`:

```powershell
python run.py fetch-text --urdu-edition ur.qadri --force
```

Then update `translation_credit.urdu_translator` in `config.yaml` so the credit
names the right person.

Translations differ more than you might expect. For مَٰلِكِ يَوْمِ ٱلدِّينِ,
Junagarhi has بدلے کے دن (یعنی قیامت) کا مالک ہے while Jalandhry has
انصاف کے دن کا حاکم — and Jalandhry writes خدا where the others write اللہ.
Pick the one you actually want before rendering.

Translations are cleaned of legacy Arabic presentation-form characters when
they are fetched. Some editions encode لا as the single pre-shaped codepoint
U+FEFB, which carries no joining behaviour and renders broken in Nastaliq;
it is unpacked into plain lam + alef so the shaper can join it. **The Quranic
Arabic is never normalised** — Uthmani orthography depends on exact codepoints,
so it is only stripped of byte-order marks.

The exact edition, the source URL and the retrieval date are recorded inside the
JSON file, so the provenance travels with the data:

```json
"source": {
  "arabic_edition": "القرآن الكريم برسم العثماني (uthmani) (Uthmani)",
  "arabic_source": "https://api.alquran.cloud/v1/surah/1/quran-uthmani",
  "urdu_edition": "محمد جوناگڑھی (Muhammad Junagarhi)",
  "retrieved_at": "2026-09-09"
}
```

The loader refuses any file where the Arabic field is not actually Arabic
script, where there are not exactly 7 ayahs, or where they are not numbered
1–7 in order.

**Please still check the text against a printed mushaf before you publish.**

To write the file by hand instead, follow this shape:

```json
{
  "surah_number": 1,
  "name_arabic": "الفاتحة",
  "name_english": "Al-Fatihah",
  "name_urdu": "سورۃ الفاتحہ",
  "ayah_count": 7,
  "ayahs": [
    {
      "number": 1,
      "arabic": "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ",
      "urdu_translation": "شروع الله کا نام لے کر …",
      "recitation": "assets/audio/recitation/001.mp3",
      "urdu_audio": "assets/audio/urdu/001.mp3"
    }
  ]
}
```

Save it as **UTF-8**. In VS Code: click the encoding in the bottom-right status
bar → *Save with Encoding* → *UTF-8*.

---

## 12. How the audio is handled

The recitation is treated as a recording to be preserved. The only processing
applied anywhere is:

1. **One constant gain per file.** Each file's loudness is measured with EBU
   R128, and a single unchanging gain moves it to the target level so the
   Arabic and the Urdu sit at a comparable volume. The gain is clamped so the
   peak can never cross the ceiling.
2. **A few milliseconds of fade** at the very start and end of each clip, so
   there is no digital click.
3. **Format conversion** during muxing.

There is no compression, no EQ, no pitch or tempo change, no limiter, and no
re-articulation. Turn even the gain off with:

```yaml
audio:
  normalize: false
```

### The Urdu section is off by default

`content.urdu_translation: false` ships as the default, so the video is Arabic
text plus recitation only. Turn it on to get the translation cards back:

```yaml
content:
  urdu_translation: true
```

The closing-card credits, the subtitles and the thumbnail all follow that switch
— the video never credits a translator whose words are not on screen, and the
thumbnail never promises a translation the video does not contain.

### And the narration, if you turn the section back on

The only freely published Urdu ayah-by-ayah narration is Shamshad Ali Khan's
(`ur.khan` on the CDN, and the same recording on everyayah.com). It recites
**Fateh Muhammad Jalandhry's** wording — خدا for Allah, سزاوار, رستے — and no
narration exists for any other Urdu translation.

The on-screen text is Muhammad Junagarhi's, which uses اللہ throughout. So the
voice and the text would say the same thing in noticeably different words, and
the mismatch is audible to anyone who reads Urdu.

Rather than have the voice contradict the text, the translation is shown
**silently**, held for as long as it takes to read:

```yaml
audio:
  urdu_narration: false
  urdu_seconds_per_word: 0.42   # reading pace for the silent hold
  min_urdu_seconds: 6.5         # floor, so short ayahs do not flash past
```

Two ways to bring the voice back, both a config change away:

1. `audio.urdu_narration: true` and accept the different wording, or
2. `audio.urdu_narration: true` **and** switch the text to Jalandhry so the two
   agree: `python run.py fetch-text --urdu-edition ur.jalandhry --force`

### Playing faster

```yaml
video:
  playback_speed: 1.25
```

This compresses the whole timeline — padding, transitions, intro, outro — and
re-times every clip with `atempo`, which changes tempo **without transposing
pitch**, so the reciter's voice is not raised. `1.0` renders at natural pace and
leaves the audio completely untouched. Range 0.5 to 2.0.

It is still a change to the recording. If you want the recitation exactly as it
was recorded, set `playback_speed: 1.0` and shorten the pauses instead.

Background ambience is **off by default** so the recitation is always the
primary audio. To add a quiet bed, put a file at `assets/audio/ambience.mp3` and:

```yaml
audio:
  ambience:
    enabled: true
    volume_db: -30.0
```

### Highlighting the word being recited

Each word lights up as the reciter reaches it — a systemBlue capsule under it
and the word itself in white.

```yaml
content:
  word_highlight: true
```

**The timings are measured, not guessed.** quran.com publishes, for each
recitation, the millisecond span of every word in every ayah. One command
stores them:

```powershell
python run.py fetch-word-timings
```

That writes `data/word_timings.json`. It is **timing data only** — no audio is
downloaded, and nothing about your recording is changed. It describes the
recitation already in `assets/audio/`.

Two checks run before any of it is used, because a highlight that drifts is
worse than none at all:

- the number of timed words must equal the number of words in the ayah text in
  `data/al_fatihah.json`, and
- no word may end after its recitation file does.

Any ayah that fails is **estimated** instead — its time shared out across the
words by letter count. That is only ever a fallback: real recitation stretches
the final word of an ayah far past its letter count, so an estimated ayah runs
ahead of the voice near the end of the line. `validate` says which is in use:

```
Word highlight   OK    29 words timed from published data
Word highlight   WARN  29 words timed; ayah 3, 5 estimated
```

The reciter has to match. Recitation id `7` is Mishary Rashid Alafasy, the same
reciter `audio_sources` points at, so the timings and the recording are one
performance. Change both together:

```yaml
word_timings:
  recitation_id: 7
  reciter_name: "Mishary Rashid Alafasy"
```

The ids are listed at <https://api.quran.com/api/v4/resources/recitations>.

Two settings shape how the highlight behaves rather than when it fires:

```yaml
word_timings:
  min_seconds: 0.16   # a shorter highlight is a flicker, not a cue
  carry_over: 0.65    # how far it holds into the silence after a word
```

> **`text_fade_in` must not exceed `padding_before`.** A word can only be lit
> while its card is fully opaque, so if the card is still fading in when the
> reciter starts, the opening word of every ayah is never highlighted. The
> shipped values are 0.20 and 0.20. If you lengthen the fade, the render logs
> a warning naming the first word it had to drop.

---

## 13. Troubleshooting

### “FFmpeg was not found on this system.”

Run `.venv\Scripts\python -m pip install imageio-ffmpeg`, or follow
[step 3](#3-install-ffmpeg) Option B. If FFmpeg is installed but not found,
you probably did not restart VS Code after editing PATH.

### “Missing audio files: assets\audio\recitation\001.mp3 …”

The error lists exactly which files are missing. Check that:

- the names are `001.mp3` … `007.mp3` — not `1.mp3`, not `001 .mp3`
- Windows is not hiding the real extension. In File Explorer:
  **View → Show → File name extensions**. A file shown as `001.mp3` might
  really be `001.mp3.mp3`.

### “Unreadable or damaged audio files”

The file exists but FFmpeg cannot decode it — usually a partial download or a
file that has been renamed to `.mp3` without being converted. Re-export it and
try again.

### “No font available for 'arabic'”

Run `python run.py fonts`. If you set a custom font, check the spelling in
`config.yaml` against the actual file name in `assets/fonts/`.

### The Arabic letters are separated, or the harakat are in the wrong place

The shaping library is missing. Run:

```powershell
.venv\Scripts\python -m pip install uharfbuzz freetype-py
```

`python run.py validate` shows a **Text shaping** row — it should read
*HarfBuzz + FreeType*.

### Some characters render as empty boxes

`validate` reports this as a glyph-coverage failure and names the missing
characters. Your font does not contain them; use one of the bundled fonts, or
another that covers Quranic Arabic.

### “Not enough free disk space”

A 1080p render writes roughly 50 MB of generated artwork into `temp/` plus the
encoded MP4 (about 0.3 MB per second of video), and the check asks for a few
times that as headroom. Run `python run.py clean`, or point `paths.temp_dir` at
another drive in `config.yaml`.

`clean` refuses to run if `paths.temp_dir` points at the project directory
itself — otherwise a stray `.` or empty value there would delete your work.

### The render is very slow

- Use `python run.py preview` while you are still adjusting things.
- Set `video.preset: "veryfast"` to test, and put it back to `"medium"` for the
  final render.
- Set `animation.particles_enabled: false` and `animation.light_enabled: false`.

### The video looks too dark, or too bright

```yaml
background:
  dim: 0.30       # lower = brighter background
theme:
  vignette: 0.42  # lower = less corner darkening
```

### Nothing changed after I edited `config.yaml`

Generated artwork is cached in `temp/`, keyed by your theme settings, so a
colour change does regenerate it. If something looks stale, run
`python run.py clean`.

### The ayah text appears twice — once large, once small at the bottom

That small line is your **player** drawing the subtitle file, not something in
the video. VLC and most players automatically load a subtitle file that sits in
the same folder with the same name as the video.

Nothing is burned into the picture (`subtitles.burn_in: false`), and the MP4
carries no subtitle stream — only video and audio. That is why the subtitles are
written to `output/captions/` rather than beside the MP4: same files, ready to
upload, but no player picks them up on its own.

The folder name matters. VLC's `sub-autodetect-path` defaults to
`./Subtitles, ./subtitles, ./Subs, ./subs`, so a folder actually called
`subtitles/` would still be found automatically — which is why this one is
`captions/`. A test asserts the configured name is not one VLC scans.

If you would rather have them next to the video, set `subtitles.folder: ""`.
To stop generating them at all, set `subtitles.enabled: false`. For YouTube,
keep them — you upload the `.srt` as a caption track, which is better than
burning text into the frame.

### Subtitles look backwards in a player

The `.srt` wraps every line in right-to-left marks, which most players honour.
VLC does; some browser players do not. Use the `.ass` file, or rely on the
on-screen text, which is always shaped correctly.

### Something else

Check the newest file in `logs/` — every run records the full FFmpeg command and
its output. Re-running with `--log-level DEBUG` adds more detail.

---

## 14. How it works inside

```
run.py
└── app/
    ├── main.py            the CLI, and every pre-flight check
    ├── config.py          config.yaml -> validated settings (pydantic)
    ├── models/
    │   ├── ayah.py        one ayah; refuses text that is not Arabic script
    │   └── surah.py       the surah; enforces exactly 7 ayahs numbered 1-7
    ├── services/
    │   ├── text.py        HarfBuzz shaping + FreeType rasterising
    │   ├── audio.py       decode checks, durations, EBU R128 loudness
    │   ├── quran_source.py downloads the verified Quran text
    │   ├── word_timings.py per-word timings, checked against text and audio
    │   ├── subtitles.py   SRT and ASS
    │   ├── video.py       FFmpeg job assembly, progress, output verification
    │   └── thumbnail.py   the 1280x720 image
    ├── rendering/
    │   ├── timeline.py    audio durations -> segments, card and word windows
    │   ├── textures.py    the generated backdrop, lattice, arch, dust
    │   ├── compositions.py the on-screen cards (Pillow)
    │   ├── animations.py  the FFmpeg filter expressions
    │   └── renderer.py    builds and runs the single FFmpeg pass
    └── utils/             fonts, files, FFmpeg discovery, logging
```

Two decisions shape everything else:

**Typography is done by hand.** Pillow cannot join Arabic letters or place
Quranic diacritics, and its Windows wheels no longer ship Raqm. So `text.py`
shapes each line with HarfBuzz — which gives correct joining, ligatures and
GPOS mark positioning, so harakat sit exactly where the type designer anchored
them — and rasterises the resulting glyphs with FreeType into an alpha mask.
Overlapping marks are combined with a max blend so their anti-aliased edges
never eat into each other.

**The video is one FFmpeg pass.** The background, the drifting lattice, the
wandering light, two layers of dust, the hairline frame and the concatenated
card track are all composited in a single filter graph. That is what keeps the
background continuous from the first frame to the last — there is no cut
anywhere in the video, only text fading in and out over a moving field.

---

## 15. Running the tests

```powershell
.venv\Scripts\python -m pytest
```

339 tests covering the JSON validation, the 7-ayah rule, audio file discovery
and decode checks, duration and timeline arithmetic, subtitle generation and
timing, Arabic and Urdu shaping and auto-fitting, the FFmpeg command that gets
generated, the per-word masks and the highlight windows built from them, all
three card styles, and — in one end-to-end test — a miniature real render that
is played back and verified.

To skip the slow end-to-end render:

```powershell
.venv\Scripts\python -m pytest -m "not slow"
```

---

## Licence and attribution

- The **code** in this repository is yours to use.
- The **fonts** in `assets/fonts/` are under the SIL Open Font License; their
  `OFL-*.txt` licence files sit alongside them.
- The **Quran text and translation** come from the published editions recorded
  in `data/al_fatihah.json`.
- The **recitation, the narration, any background you add and any logo** are
  yours to source and to clear. Nothing is downloaded on your behalf.
