"""Quran JSON loading and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.ayah import Ayah, arabic_ratio, strip_tashkeel, to_arabic_digits
from app.models.surah import load_surah, save_surah
from app.utils.logging import QVGError


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_loads_valid_surah(config, surah):
    assert surah.surah_number == 1
    assert surah.ayah_count == 7
    assert len(surah.ayahs) == 7
    assert [a.number for a in surah.ayahs] == [1, 2, 3, 4, 5, 6, 7]


def test_every_ayah_has_arabic_and_urdu(surah):
    for ayah in surah.ayahs:
        assert ayah.arabic.strip()
        assert ayah.urdu_translation.strip()
        assert arabic_ratio(ayah.arabic) > 0.9


def test_missing_file_gives_actionable_error(tmp_path):
    with pytest.raises(QVGError) as excinfo:
        load_surah(tmp_path / "nope.json")
    assert "fetch-text" in excinfo.value.hint


def test_rejects_six_ayahs(tmp_path, surah_payload):
    surah_payload["ayahs"] = surah_payload["ayahs"][:6]
    surah_payload["ayah_count"] = 6
    path = _write(tmp_path / "six.json", surah_payload)
    with pytest.raises(QVGError) as excinfo:
        load_surah(path)
    assert "7 ayahs" in excinfo.value.message


def test_rejects_eight_ayahs(tmp_path, surah_payload):
    extra = dict(surah_payload["ayahs"][-1])
    extra["number"] = 8
    surah_payload["ayahs"].append(extra)
    path = _write(tmp_path / "eight.json", surah_payload)
    with pytest.raises(QVGError):
        load_surah(path)


def test_rejects_out_of_order_numbering(tmp_path, surah_payload):
    surah_payload["ayahs"][2]["number"] = 5
    surah_payload["ayahs"][4]["number"] = 3
    path = _write(tmp_path / "shuffled.json", surah_payload)
    with pytest.raises(QVGError) as excinfo:
        load_surah(path)
    assert "1,2,3,4,5,6,7" in excinfo.value.message


def test_rejects_mismatched_ayah_count_field(tmp_path, surah_payload):
    surah_payload["ayah_count"] = 5
    path = _write(tmp_path / "count.json", surah_payload)
    with pytest.raises(QVGError):
        load_surah(path)


def test_rejects_empty_arabic(tmp_path, surah_payload):
    surah_payload["ayahs"][3]["arabic"] = "   "
    path = _write(tmp_path / "empty.json", surah_payload)
    with pytest.raises(QVGError) as excinfo:
        load_surah(path)
    assert "arabic" in excinfo.value.message.lower()


def test_rejects_latin_text_in_the_arabic_field(tmp_path, surah_payload):
    surah_payload["ayahs"][0]["arabic"] = "Bismillah ir-Rahman ir-Raheem"
    path = _write(tmp_path / "latin.json", surah_payload)
    with pytest.raises(QVGError) as excinfo:
        load_surah(path)
    assert "Arabic script" in excinfo.value.message


def test_rejects_empty_urdu_translation(tmp_path, surah_payload):
    surah_payload["ayahs"][6]["urdu_translation"] = ""
    path = _write(tmp_path / "nourdu.json", surah_payload)
    with pytest.raises(QVGError):
        load_surah(path)


def test_rejects_other_surahs(tmp_path, surah_payload):
    surah_payload["surah_number"] = 2
    path = _write(tmp_path / "baqarah.json", surah_payload)
    with pytest.raises(QVGError) as excinfo:
        load_surah(path)
    assert "Al-Fatihah only" in excinfo.value.message


def test_rejects_broken_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"surah_number": 1,,}', encoding="utf-8")
    with pytest.raises(QVGError) as excinfo:
        load_surah(path)
    assert "not valid JSON" in excinfo.value.message


def test_round_trip_preserves_text(tmp_path, surah):
    destination = tmp_path / "round_trip.json"
    save_surah(surah, destination)
    reloaded = load_surah(destination)
    for original, copy in zip(surah.ayahs, reloaded.ayahs):
        assert original.arabic == copy.arabic
        assert original.urdu_translation == copy.urdu_translation


def test_saved_file_is_utf8_without_escapes(tmp_path, surah):
    destination = tmp_path / "utf8.json"
    save_surah(surah, destination)
    raw = destination.read_text(encoding="utf-8")
    assert "\\u" not in raw
    assert surah.ayahs[0].arabic in raw


def test_arabic_indic_digits():
    assert to_arabic_digits(1) == "١"
    assert to_arabic_digits(7) == "٧"
    assert to_arabic_digits(114) == "١١٤"


def test_strip_tashkeel_keeps_letters(surah):
    plain = strip_tashkeel(surah.ayahs[0].arabic)
    assert "ب" in plain
    assert len(plain) < len(surah.ayahs[0].arabic)


def test_ayah_helpers(surah):
    ayah = surah.ayah(3)
    assert ayah.number == 3
    assert ayah.padded_number == "003"
    assert ayah.arabic_number == "٣"
    assert ayah.word_count >= 2


def test_ayah_lookup_rejects_unknown_number(surah):
    with pytest.raises(KeyError):
        surah.ayah(9)


def test_source_provenance_is_recorded(surah):
    described = dict(surah.source.describe())
    assert described["Arabic edition"] != "(not recorded)"
    assert described["Urdu edition"] != "(not recorded)"


def test_arabic_ratio_discriminates():
    assert arabic_ratio("بِسْمِ ٱللَّهِ") > 0.95
    assert arabic_ratio("Hello world") == 0.0
    assert arabic_ratio("") == 0.0


def test_ayah_model_rejects_zero_number():
    with pytest.raises(Exception):
        Ayah(number=0, arabic="بِسْمِ ٱللَّهِ", urdu_translation="شروع الله کا نام")


# ---------------------------------------------------------------------------
# Text cleaning at fetch time
# ---------------------------------------------------------------------------

def test_legacy_ligatures_in_a_translation_are_unpacked():
    """Regression: Junagarhi's edition writes U+FEFB (LAM WITH ALEF ISOLATED
    FORM) instead of lam + alef. A presentation form carries no joining
    behaviour, so the preceding letter cannot connect to it and the word
    renders broken in Nastaliq."""
    from app.services.quran_source import _clean_translation

    cleaned = _clean_translation("رحم وا\ufefb ہے")
    assert "\ufefb" not in cleaned
    assert "والا" in cleaned


def test_translation_cleaning_leaves_ordinary_letters_alone():
    from app.services.quran_source import _clean_translation

    text = "بدلے کے دن (یعنی قیامت) کا مالک ہے"
    assert _clean_translation(text) == text


def test_quranic_arabic_is_never_normalised():
    """Uthmani orthography depends on exact codepoints, so scripture is cleaned
    of transport artefacts only - normalising it would alter the text."""
    from app.services.quran_source import _clean

    # Superscript alef and alef wasla must survive untouched.
    scripture = "ٱلرَّحْمَٰنِ"
    assert _clean(scripture) == scripture
    assert "\u0670" in _clean(scripture)   # superscript alef
    assert "\u0671" in _clean(scripture)   # alef wasla


def test_byte_order_marks_are_stripped():
    from app.services.quran_source import _clean

    assert _clean("\ufeffبِسْمِ") == "بِسْمِ"


def test_whitespace_is_collapsed():
    from app.services.quran_source import _clean

    assert _clean("  بڑا   مہربان  ") == "بڑا مہربان"
