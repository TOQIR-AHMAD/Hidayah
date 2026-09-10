# Quran Video Generator — Surah Al-Fatihah

A cinematic Quran video generator built specifically for **Surah Al-Fatihah**.

It turns Quranic text, authentic recitation, and optional Urdu translation into a polished **YouTube-ready video**, with animated typography and word-by-word highlighting synchronized to the reciter.

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
Ayah 7
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

## Current Scope

Currently supports:

> **Surah Al-Fatihah only**

The project is intentionally focused on producing a high-quality result for one Surah before expanding to additional Surahs.

---

<p align="center">
  <i>Beautiful Quran recitation videos, generated from text, audio and precise timing data.</i>
</p>
