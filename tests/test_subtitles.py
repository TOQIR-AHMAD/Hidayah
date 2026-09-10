"""Subtitle generation and RTL handling."""

from __future__ import annotations

import re

import pytest

from app.rendering.timeline import build_timeline
from app.services import subtitles as subsvc
from app.services.audio import AudioClip, AudioPlan

TIMECODE = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})$"
)


@pytest.fixture
def timeline(config, surah, tmp_path):
    plan = AudioPlan(mode="per_ayah")
    for number in range(1, 8):
        plan.recitation[number] = AudioClip(path=tmp_path / "r.wav", duration=2.0 + number)
        plan.urdu[number] = AudioClip(path=tmp_path / "u.wav", duration=3.0 + number)
    return build_timeline(config, surah, plan)


def _seconds(text: str) -> float:
    h, m, s, ms = TIMECODE.match(text).groups()[:4]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def test_srt_has_one_cue_per_audio_segment(config, timeline, tmp_path):
    path = subsvc.write_srt(config, timeline, tmp_path / "out.srt")
    body = path.read_text(encoding="utf-8-sig")
    assert body.count(" --> ") == 14


def test_srt_cues_are_numbered_from_one(config, timeline, tmp_path):
    path = subsvc.write_srt(config, timeline, tmp_path / "out.srt")
    blocks = [b for b in path.read_text(encoding="utf-8-sig").split("\n\n") if b.strip()]
    numbers = [int(b.strip().splitlines()[0]) for b in blocks]
    assert numbers == list(range(1, 15))


def test_srt_timecodes_are_well_formed(config, timeline, tmp_path):
    path = subsvc.write_srt(config, timeline, tmp_path / "out.srt")
    lines = [ln for ln in path.read_text(encoding="utf-8-sig").splitlines() if "-->" in ln]
    assert len(lines) == 14
    for line in lines:
        assert TIMECODE.match(line), line


def test_cue_timing_follows_the_audio(config, timeline, tmp_path):
    path = subsvc.write_srt(config, timeline, tmp_path / "out.srt")
    lines = [ln for ln in path.read_text(encoding="utf-8-sig").splitlines() if "-->" in ln]
    first_segment = timeline.with_audio[0]
    start = _seconds(lines[0])
    assert start == pytest.approx(first_segment.audio_start, abs=0.01)


def test_cues_never_overlap_and_advance(config, timeline, tmp_path):
    path = subsvc.write_srt(config, timeline, tmp_path / "out.srt")
    lines = [ln for ln in path.read_text(encoding="utf-8-sig").splitlines() if "-->" in ln]
    ends, starts = [], []
    for line in lines:
        groups = TIMECODE.match(line).groups()
        starts.append(int(groups[0]) * 3600 + int(groups[1]) * 60 + int(groups[2]) + int(groups[3]) / 1000)
        ends.append(int(groups[4]) * 3600 + int(groups[5]) * 60 + int(groups[6]) + int(groups[7]) / 1000)
    for index in range(14):
        assert ends[index] > starts[index]
    for index in range(13):
        assert starts[index + 1] >= ends[index] - 1e-6


def test_cues_stay_inside_the_video(config, timeline, tmp_path):
    path = subsvc.write_srt(config, timeline, tmp_path / "out.srt")
    lines = [ln for ln in path.read_text(encoding="utf-8-sig").splitlines() if "-->" in ln]
    groups = TIMECODE.match(lines[-1]).groups()
    last_end = int(groups[4]) * 3600 + int(groups[5]) * 60 + int(groups[6]) + int(groups[7]) / 1000
    assert last_end <= timeline.total_duration + 1e-6


def test_rtl_marks_wrap_every_line(config, timeline, tmp_path):
    path = subsvc.write_srt(config, timeline, tmp_path / "out.srt")
    body = path.read_text(encoding="utf-8-sig")
    assert body.count(subsvc.RLE) == 14
    assert body.count(subsvc.PDF) == 14


def test_srt_contains_the_actual_quran_text(config, timeline, surah, tmp_path):
    path = subsvc.write_srt(config, timeline, tmp_path / "out.srt")
    body = path.read_text(encoding="utf-8-sig")
    for ayah in surah.ayahs:
        assert " ".join(ayah.arabic.split()) in body
        assert " ".join(ayah.urdu_translation.split()) in body


def test_srt_is_written_with_a_utf8_bom(config, timeline, tmp_path):
    path = subsvc.write_srt(config, timeline, tmp_path / "out.srt")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_content_filter_arabic_only(config, timeline, tmp_path):
    config.subtitles.content = "arabic"
    path = subsvc.write_srt(config, timeline, tmp_path / "ar.srt")
    assert path.read_text(encoding="utf-8-sig").count(" --> ") == 7


def test_content_filter_urdu_only(config, timeline, tmp_path):
    config.subtitles.content = "urdu"
    path = subsvc.write_srt(config, timeline, tmp_path / "ur.srt")
    assert path.read_text(encoding="utf-8-sig").count(" --> ") == 7


# ---------------------------------------------------------------------------
# ASS
# ---------------------------------------------------------------------------

def test_ass_has_the_expected_sections(config, timeline, surah, tmp_path):
    path = subsvc.write_ass(config, timeline, surah, tmp_path / "out.ass")
    body = path.read_text(encoding="utf-8")
    assert "[Script Info]" in body
    assert "[V4+ Styles]" in body
    assert "[Events]" in body
    assert body.count("Dialogue:") == 14


def test_ass_uses_a_style_per_language(config, timeline, surah, tmp_path):
    path = subsvc.write_ass(config, timeline, surah, tmp_path / "out.ass")
    body = path.read_text(encoding="utf-8")
    assert body.count(",Arabic,") == 7
    assert body.count(",Urdu,") == 7


def test_ass_declares_the_play_resolution(config, timeline, surah, tmp_path):
    path = subsvc.write_ass(config, timeline, surah, tmp_path / "out.ass", 1920, 1080)
    body = path.read_text(encoding="utf-8")
    assert "PlayResX: 1920" in body
    assert "PlayResY: 1080" in body


def test_ass_colour_conversion():
    assert subsvc._ass_colour("#D8B871") == "&H0071B8D8"
    assert subsvc._ass_colour("#000000") == "&H00000000"
    assert subsvc._ass_colour("#FFFFFF") == "&H00FFFFFF"


def test_ass_font_family_derivation(tmp_path):
    from pathlib import Path
    assert subsvc._font_family(Path("NotoNastaliqUrdu-Regular.ttf"), "x") == "Noto Nastaliq Urdu"
    assert subsvc._font_family(Path("AmiriQuran-Regular.ttf"), "x") == "Amiri Quran"
    assert subsvc._font_family(None, "Arial") == "Arial"


def test_ass_escapes_brace_characters(config, timeline, surah, tmp_path):
    timeline.segments[1].subtitle_text = "{\\an8}injected"
    path = subsvc.write_ass(config, timeline, surah, tmp_path / "out.ass")
    body = path.read_text(encoding="utf-8")
    assert "{\\an8}" not in body


def test_generate_writes_both_formats(config, timeline, surah):
    written = subsvc.generate(config, timeline, surah)
    assert {p.suffix for p in written} == {".srt", ".ass"}
    assert all(p.is_file() for p in written)


def test_generate_respects_the_enabled_flag(config, timeline, surah):
    config.subtitles.enabled = False
    assert subsvc.generate(config, timeline, surah) == []


def test_cue_count_matches(config, timeline):
    assert subsvc.cue_count(config, timeline) == 14


def test_timestamps_never_emit_sixty_seconds():
    """Regression: splitting into fields before rounding let 59.9996 s become
    "00:00:60,000", which is not a valid SRT timestamp."""
    for value in (59.9996, 59.9999, 119.9999, 3599.9999, 0.9999):
        srt = subsvc._srt_timestamp(value)
        assert ",60" not in srt.replace(",", ":") or True
        seconds_field = srt.split(":")[2].split(",")[0]
        minutes_field = srt.split(":")[1]
        assert int(seconds_field) < 60, srt
        assert int(minutes_field) < 60, srt

        ass = subsvc._ass_timestamp(value)
        assert int(ass.split(":")[2].split(".")[0]) < 60, ass
        assert int(ass.split(":")[1]) < 60, ass


def test_timestamps_carry_into_the_next_field():
    assert subsvc._srt_timestamp(59.9996) == "00:01:00,000"
    assert subsvc._srt_timestamp(3599.9996) == "01:00:00,000"
    assert subsvc._ass_timestamp(59.999) == "0:01:00.00"


def test_timestamps_round_trip_ordinary_values():
    assert subsvc._srt_timestamp(0.0) == "00:00:00,000"
    assert subsvc._srt_timestamp(7.6) == "00:00:07,600"
    assert subsvc._srt_timestamp(3661.25) == "01:01:01,250"
    assert subsvc._ass_timestamp(7.6) == "0:00:07.60"


# ---------------------------------------------------------------------------
# Where the files land
# ---------------------------------------------------------------------------

def test_subtitles_do_not_sit_beside_the_video_by_default(config, timeline, surah):
    """A subtitle file with the video's name in the video's folder is picked up
    automatically by VLC and most players, which puts the text on screen during
    playback even though nothing is burned into the picture."""
    written = subsvc.generate(config, timeline, surah)
    assert written
    for path in written:
        assert path.parent != config.output_dir
        assert path.is_file()


def test_the_subtitle_folder_is_not_one_players_scan(config):
    """VLC's sub-autodetect-path defaults to "./Subtitles, ./subtitles, ./Subs,
    ./subs", so naming the folder the obvious thing defeats the whole point."""
    from app.config import Config

    folder = config.subtitles.folder.strip().lower()
    assert folder not in Config.AUTOLOADED_BY_PLAYERS
    assert folder, "an empty folder puts them right next to the video"


def test_no_file_shares_the_videos_name_and_folder(config, timeline, surah):
    """A player that finds "<video>.srt" beside "<video>.mp4" loads it without
    being asked, which is exactly the burned-in-looking overlay we removed."""
    subsvc.generate(config, timeline, surah)
    video = config.output_file("mp4")

    # Every extension a player will pick up from a same-named sibling.
    subtitle_suffixes = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sbv"}
    clashes = [
        p.name
        for p in config.output_dir.iterdir()
        if p.is_file() and p.stem == video.stem and p.suffix.lower() in subtitle_suffixes
    ]
    assert not clashes, f"auto-loaded beside the video: {clashes}"

    # And nothing in output/ itself carries a subtitle extension at all.
    assert not list(config.output_dir.glob("*.srt"))
    assert not list(config.output_dir.glob("*.ass"))


def test_the_folder_can_be_set_back_to_beside_the_video(config, timeline, surah):
    config.subtitles.folder = ""
    written = subsvc.generate(config, timeline, surah)
    for path in written:
        assert path.parent == config.output_dir


def test_subtitle_file_path_uses_the_slug(config):
    assert config.subtitle_file("srt").name == "001_Al-Fatihah.srt"
    assert config.subtitle_file(".ass").name == "001_Al-Fatihah.ass"
