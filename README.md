# Quran Video Generator

A cinematic Quran video generator.

It turns Quranic text, authentic recitation, and optional Urdu translation into a polished **YouTube-ready video**, with animated typography and word-by-word highlighting synchronized to the reciter.

Two surahs are built and shipped: **Al-Fatihah** (`config.yaml`) and **Al-Baqarah** (`config.al-baqarah.yaml`).

## What it does

* Displays the **Arabic Quran text** ayah by ayah
* Plays the corresponding **Quran recitation**
* Highlights each **word as it is recited**
* Optionally displays an **Urdu translation**
* Synchronizes text, audio, and animation automatically
* Uses proper Arabic shaping for **Quranic diacritics and ligatures**
* Creates cinematic backgrounds, typography, transitions, and subtle motion
* Generates **YouTube subtitles** (`.srt` / `.ass`)
* Generates a matching **thumbnail**
* Exports a **1920×1080 H.264 video**

## Video Flow

```text
Ayah 1
Arabic + Recitation
       ↓
Word-by-word highlighting
       ↓
Ayah 2
Arabic + Recitation
       ↓
...
       ↓
Ayah N
Arabic + Recitation
       ↓
Fade Out
```

When Urdu translation is enabled:

```text
Arabic + Recitation
       ↓
Urdu Translation
       ↓
Next Ayah
```

## Highlights

**🎙️ Real Recitation**
Uses recorded Quran recitation rather than text-to-speech or generated voices.

**✨ Synchronized Highlighting**
Words light up according to published per-word recitation timings instead of estimated animation timing.

**🕌 Quranic Typography**
Arabic is shaped with HarfBuzz and rendered with Quran-compatible fonts to preserve joining, ligatures, and diacritics.

**📖 One Ayah, One Screen**
An ayah is shown whole wherever it fits. The few that are too long - 2:282 is 128 words - are split across screens at a word boundary instead of being shrunk unread: each screen is held for exactly the span in which its own words are recited, and the reciter carries straight on through the turn.

**🎨 Cinematic Design**
Supports multiple visual styles, animated backgrounds, custom artwork, and channel branding.

**🇵🇰 Urdu Translation**
Optional Urdu translation can be displayed beneath each ayah.

## Output

```text
001_Al-Fatihah.mp4
001_Al-Fatihah.jpg
captions/
├── 001_Al-Fatihah.srt
└── 001_Al-Fatihah.ass
```

The result is a clean Quran video designed for **YouTube and other video platforms**.

## Rendering another Surah

Each surah is one config file, and `new-surah` writes it for you - copied from
`config.yaml`, with only the values that belong to the surah changed:

```bash
python run.py new-surah 112                                   # writes config.al-ikhlas.yaml
python run.py --config config.al-ikhlas.yaml fetch-text
python run.py --config config.al-ikhlas.yaml fetch-audio --accept-source-license
python run.py --config config.al-ikhlas.yaml fetch-word-timings
python run.py --config config.al-ikhlas.yaml validate
python run.py --config config.al-ikhlas.yaml render
```

Built so far: **Al-Fatihah (1)**, **Al-Adiyat (100)** through **An-Nas (114)**,
and a config for **Al-Baqarah (2)** whose recitation downloads on demand.

**Long surahs.** Al-Fatihah is a single FFmpeg pass — 7 cards and 29 word
highlights. Al-Baqarah is 286 cards and 6115 highlights, which as one graph
would need some 6700 open inputs. Above the thresholds in the `render:` block
the picture is built in chunks that each cut on a card boundary and are joined
without re-encoding; the chunks are kept, so an interrupted render resumes
where it stopped. A 98-minute render needs roughly **5 GB of free disk** while
it runs, and several hours on a four-core machine.

---

<p align="center">
  <i>Beautiful Quran recitation videos, generated from text, audio and precise timing data.</i>
</p>
