"""Shared pytest fixtures.

The fixtures build a complete throwaway project in a tmp directory: config,
Quran JSON and real (silent) WAV files, so the tests exercise the same code
paths a real render does.
"""

from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

import pytest

from app.config import Config, load_config
from app.models.surah import Surah, load_surah
from app.utils import ffmpeg as ff

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Verified Uthmani text of Surah Al-Fatihah, used as a fixture only.
# The application itself never hard-codes Quranic text; it reads
# data/al_fatihah.json, which `python run.py fetch-text` writes from a
# published edition.
FIXTURE_ARABIC = [
    "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ",
    "ٱلْحَمْدُ لِلَّهِ رَبِّ ٱلْعَٰلَمِينَ",
    "ٱلرَّحْمَٰنِ ٱلرَّحِيمِ",
    "مَٰلِكِ يَوْمِ ٱلدِّينِ",
    "إِيَّاكَ نَعْبُدُ وَإِيَّاكَ نَسْتَعِينُ",
    "ٱهْدِنَا ٱلصِّرَٰطَ ٱلْمُسْتَقِيمَ",
    "صِرَٰطَ ٱلَّذِينَ أَنْعَمْتَ عَلَيْهِمْ غَيْرِ ٱلْمَغْضُوبِ عَلَيْهِمْ وَلَا ٱلضَّآلِّينَ",
]

FIXTURE_URDU = [
    "شروع الله کا نام لے کر جو بڑا مہربان نہایت رحم والا ہے",
    "سب طرح کی تعریف خدا ہی کو سزاوار ہے جو تمام مخلوقات کا پروردگار ہے",
    "بڑا مہربان نہایت رحم والا",
    "انصاف کے دن کا حاکم",
    "اے پروردگار ہم تیری ہی عبادت کرتے ہیں اور تجھ ہی سے مدد مانگتے ہیں",
    "ہم کو سیدھے رستے چلا",
    "ان لوگوں کے رستے جن پر تو اپنا فضل وکرم کرتا رہا",
]


def write_silent_wav(path: Path, seconds: float, sample_rate: int = 22050) -> Path:
    """A real, decodable WAV file - quiet but not digital silence.

    ebur128 reports -70 LUFS or lower for absolute silence and the loudness
    code treats that as "unknown", so the fixture carries a very low tone to
    keep the gain path exercised.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        samples = bytearray()
        for index in range(frames):
            value = int(3000 * math.sin(2 * math.pi * 220 * index / sample_rate))
            samples += struct.pack("<h", value)
        handle.writeframes(bytes(samples))
    return path


@pytest.fixture(scope="session")
def ffmpeg_available() -> bool:
    try:
        ff.find_ffmpeg()
        return True
    except Exception:
        return False


@pytest.fixture
def needs_ffmpeg(ffmpeg_available: bool) -> None:
    if not ffmpeg_available:
        pytest.skip("FFmpeg is not installed")


@pytest.fixture
def surah_payload() -> dict:
    return {
        "surah_number": 1,
        "name_arabic": "الفاتحة",
        "name_english": "Al-Fatihah",
        "name_urdu": "سورۃ الفاتحہ",
        "ayah_count": 7,
        "revelation_place": "Makkah",
        "source": {
            "arabic_edition": "Test edition (Uthmani)",
            "arabic_source": "fixture",
            "urdu_edition": "Test Urdu edition",
            "urdu_source": "fixture",
            "retrieved_at": "2026-01-01",
        },
        "ayahs": [
            {
                "number": index + 1,
                "arabic": FIXTURE_ARABIC[index],
                "urdu_translation": FIXTURE_URDU[index],
                "recitation": f"assets/audio/recitation/{index + 1:03d}.wav",
                "urdu_audio": f"assets/audio/urdu/{index + 1:03d}.wav",
            }
            for index in range(7)
        ],
    }


@pytest.fixture
def project(tmp_path: Path, surah_payload: dict) -> Path:
    """A complete throwaway project directory."""
    for relative in (
        "assets/audio/recitation", "assets/audio/urdu", "assets/backgrounds",
        "assets/fonts", "assets/branding", "data", "output", "temp", "logs",
    ):
        (tmp_path / relative).mkdir(parents=True, exist_ok=True)

    (tmp_path / "data/al_fatihah.json").write_text(
        json.dumps(surah_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Reuse the real fonts so text tests exercise real shaping.
    real_fonts = PROJECT_ROOT / "assets/fonts"
    if real_fonts.is_dir():
        for font in real_fonts.glob("*.ttf"):
            (tmp_path / "assets/fonts" / font.name).write_bytes(font.read_bytes())

    config_text = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
    config_text = config_text.replace(
        'extensions: ["mp3", "wav", "m4a", "ogg", "flac"]',
        'extensions: ["wav", "mp3", "m4a", "ogg", "flac"]',
    )
    (tmp_path / "config.yaml").write_text(config_text, encoding="utf-8")
    return tmp_path


@pytest.fixture
def project_with_audio(project: Path) -> Path:
    for number in range(1, 8):
        write_silent_wav(project / f"assets/audio/recitation/{number:03d}.wav", 1.0 + number * 0.25)
        write_silent_wav(project / f"assets/audio/urdu/{number:03d}.wav", 1.5 + number * 0.2)
    return project


def _neutral(config: Config) -> Config:
    """Reset the settings the shipped config.yaml tunes for THIS video.

    config.yaml is tuned for one particular delivery: Arabic only, no title
    card, 1.25x, and pauses squeezed almost to nothing. Tests that assert the
    core behaviour - the full intro/ayah/outro structure, durations following
    the audio, a beat between cards - need the canonical values instead, or
    they break every time the delivery is re-tuned.

    Each of the shipped choices is covered by its own test.
    """
    config.video.playback_speed = 1.0
    config.audio.urdu_narration = True
    config.content.urdu_translation = True
    config.content.outro_card = True
    config.animation.intro_duration = 7.0
    config.animation.outro_duration = 9.0
    config.animation.outro_fade_out = 2.20
    config.audio.padding_before = 0.60
    config.audio.padding_after = 1.10
    config.audio.gap_between_ayahs = 0.70
    config.animation.text_fade_in = 0.55
    config.animation.intro_fade_in = 1.60
    config.theme.style = "classic"
    config.theme.appearance = "dark"
    config.theme.frame_enabled = True
    config.theme.glow_enabled = True
    config.theme.text_shadow_enabled = False
    config.background.dim = 0.30
    config.background.blur = 6
    # The delivery names a specific plate in assets/backgrounds/; the throwaway
    # project has none, so the tests fall back to the generated backdrop.
    config.background.source = "auto"
    return config


@pytest.fixture
def config(project: Path) -> Config:
    return _neutral(load_config(project / "config.yaml", root=project))


@pytest.fixture
def config_with_audio(project_with_audio: Path) -> Config:
    return _neutral(
        load_config(project_with_audio / "config.yaml", root=project_with_audio)
    )


@pytest.fixture
def shipped_config(project: Path) -> Config:
    """The config exactly as it ships, without the neutralising above."""
    return load_config(project / "config.yaml", root=project)


@pytest.fixture
def surah(config: Config) -> Surah:
    return load_surah(config.data_file)


@pytest.fixture(scope="session")
def real_fonts_dir() -> Path:
    directory = PROJECT_ROOT / "assets/fonts"
    if not directory.is_dir() or not list(directory.glob("*.ttf")):
        pytest.skip("Fonts are not installed - run: python run.py fonts")
    return directory


@pytest.fixture(scope="session")
def arabic_font(real_fonts_dir: Path) -> Path:
    for name in ("AmiriQuran-Regular.ttf", "Amiri-Regular.ttf"):
        candidate = real_fonts_dir / name
        if candidate.is_file():
            return candidate
    pytest.skip("No Arabic font available")


@pytest.fixture(scope="session")
def urdu_font(real_fonts_dir: Path) -> Path:
    candidate = real_fonts_dir / "NotoNastaliqUrdu-Regular.ttf"
    if candidate.is_file():
        return candidate
    pytest.skip("No Urdu font available")
