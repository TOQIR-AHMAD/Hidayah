"""Audio discovery, decode validation, loudness gain and plan building."""

from __future__ import annotations

import pytest

from app.services import audio as audiosvc
from app.services.audio import AudioAnalysis
from app.utils.logging import QVGError

from .conftest import write_silent_wav


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def test_finds_every_recitation_file(config_with_audio, surah):
    for ayah in surah.ayahs:
        assert audiosvc.recitation_file(config_with_audio, ayah.number) is not None
        assert audiosvc.urdu_file(config_with_audio, ayah.number) is not None


def test_missing_files_are_reported_individually(config, surah):
    mode, issues = audiosvc.check_audio(config, surah)
    assert mode == "per_ayah"
    assert len(issues) == 14
    assert all(issue.kind == "missing" for issue in issues)


def test_missing_file_report_names_each_path(config, surah):
    _, issues = audiosvc.check_audio(config, surah)
    report = audiosvc.format_issues(issues, config.root)
    for number in range(1, 8):
        assert f"{number:03d}.mp3" in report
    assert "Missing audio files" in report


def test_a_single_missing_file_is_pinpointed(config_with_audio, surah):
    (config_with_audio.recitation_dir / "004.wav").unlink()
    _, issues = audiosvc.check_audio(config_with_audio, surah)
    assert len(issues) == 1
    assert "ayah 4" in issues[0].detail


def test_alternative_extensions_are_accepted(config_with_audio, surah):
    source = config_with_audio.recitation_dir / "002.wav"
    source.rename(config_with_audio.recitation_dir / "002.flac")
    found = audiosvc.recitation_file(config_with_audio, 2)
    assert found is not None and found.suffix == ".flac"


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def test_analyse_reports_the_real_duration(needs_ffmpeg, config_with_audio):
    path = config_with_audio.recitation_dir / "001.wav"
    analysis = audiosvc.analyse(path, config_with_audio, use_cache=False)
    assert analysis.ok
    assert analysis.duration == pytest.approx(1.25, abs=0.06)


def test_analyse_durations_differ_per_file(needs_ffmpeg, config_with_audio):
    durations = [
        audiosvc.analyse(
            config_with_audio.recitation_dir / f"{n:03d}.wav", config_with_audio, use_cache=False
        ).duration
        for n in (1, 4, 7)
    ]
    assert durations[0] < durations[1] < durations[2]


def test_analyse_flags_a_missing_file(config_with_audio):
    analysis = audiosvc.analyse(config_with_audio.recitation_dir / "nope.wav", config_with_audio)
    assert not analysis.ok
    assert "not found" in analysis.error


def test_analyse_flags_a_corrupt_file(needs_ffmpeg, config_with_audio):
    broken = config_with_audio.recitation_dir / "broken.wav"
    broken.write_bytes(b"this is definitely not audio" * 40)
    analysis = audiosvc.analyse(broken, config_with_audio, use_cache=False)
    assert not analysis.ok
    assert analysis.error


def test_corrupt_file_is_reported_by_check_audio(needs_ffmpeg, config_with_audio, surah):
    target = config_with_audio.urdu_dir / "003.wav"
    target.write_bytes(b"\x00" * 512)
    _, issues = audiosvc.check_audio(config_with_audio, surah)
    corrupt = [i for i in issues if i.kind == "corrupt"]
    assert corrupt
    assert "003" in corrupt[0].path.name
    assert "damaged" in audiosvc.format_issues(issues, config_with_audio.root)


def test_analysis_is_cached(needs_ffmpeg, config_with_audio):
    path = config_with_audio.recitation_dir / "001.wav"
    first = audiosvc.analyse(path, config_with_audio)
    second = audiosvc.analyse(path, config_with_audio)
    assert first.duration == second.duration
    assert (config_with_audio.temp_dir / audiosvc.CACHE_NAME).is_file()


# ---------------------------------------------------------------------------
# Loudness gain
# ---------------------------------------------------------------------------

def _analysis(lufs, peak=-20.0):
    """A stand-in measurement. The default peak leaves plenty of headroom so
    that only the tests which set `peak` explicitly exercise the clip guard."""
    from pathlib import Path
    return AudioAnalysis(
        path=Path("x.wav"), duration=5.0, ok=True,
        integrated_lufs=lufs, true_peak_db=peak,
    )


def test_gain_moves_quiet_audio_up(config):
    config.audio.target_lufs = -16.0
    assert audiosvc.compute_gain_db(_analysis(-24.0), config) == pytest.approx(8.0)


def test_gain_moves_loud_audio_down(config):
    config.audio.target_lufs = -16.0
    assert audiosvc.compute_gain_db(_analysis(-10.0), config) == pytest.approx(-6.0)


def test_gain_is_clamped(config):
    config.audio.target_lufs = -16.0
    config.audio.max_gain_db = 6.0
    assert audiosvc.compute_gain_db(_analysis(-40.0, peak=-40.0), config) == pytest.approx(6.0)


def test_gain_never_causes_clipping(config):
    """A quiet-but-peaky recording must not be pushed over the ceiling."""
    config.audio.target_lufs = -16.0
    config.audio.true_peak_ceiling_db = -1.5
    gain = audiosvc.compute_gain_db(_analysis(-30.0, peak=-2.0), config)
    assert gain == pytest.approx(0.5)


def test_no_gain_when_normalisation_is_off(config):
    config.audio.normalize = False
    assert audiosvc.compute_gain_db(_analysis(-30.0), config) == 0.0


def test_no_gain_when_loudness_is_unknown(config):
    assert audiosvc.compute_gain_db(_analysis(None), config) == 0.0


def test_db_to_linear():
    assert audiosvc.db_to_linear(0.0) == pytest.approx(1.0)
    assert audiosvc.db_to_linear(6.0) == pytest.approx(1.995, abs=0.01)
    assert audiosvc.db_to_linear(-6.0) == pytest.approx(0.501, abs=0.01)


# ---------------------------------------------------------------------------
# Modes and plan building
# ---------------------------------------------------------------------------

def test_auto_mode_prefers_per_ayah_files(config_with_audio, surah):
    assert audiosvc.resolve_recitation_mode(config_with_audio, surah) == "per_ayah"


def test_auto_mode_falls_back_to_a_full_recording(config, surah, needs_ffmpeg):
    write_silent_wav(config.recitation_dir / "al_fatihah.wav", 6.0)
    config.audio.full_surah_file = "al_fatihah.wav"
    config.audio.full_surah_timings = [
        {"ayah": n, "start": (n - 1) * 0.8, "end": n * 0.8} for n in range(1, 8)
    ]
    from app.config import AyahTiming
    config.audio.full_surah_timings = [AyahTiming(**t) for t in config.audio.full_surah_timings]
    assert audiosvc.resolve_recitation_mode(config, surah) == "full_surah"


def test_full_surah_mode_slices_by_the_configured_timings(config, surah, needs_ffmpeg):
    from app.config import AyahTiming
    write_silent_wav(config.recitation_dir / "al_fatihah.wav", 8.0)
    for number in range(1, 8):
        write_silent_wav(config.urdu_dir / f"{number:03d}.wav", 1.0)
    config.audio.recitation_mode = "full_surah"
    config.audio.full_surah_file = "al_fatihah.wav"
    config.audio.full_surah_timings = [
        AyahTiming(ayah=n, start=(n - 1) * 1.0, end=n * 1.0) for n in range(1, 8)
    ]

    plan = audiosvc.build_plan(config, surah)
    assert plan.mode == "full_surah"
    for number in range(1, 8):
        clip = plan.recitation[number]
        assert clip.trim_start == pytest.approx((number - 1) * 1.0)
        assert clip.duration == pytest.approx(1.0)
        assert clip.path.name == "al_fatihah.wav"


def test_full_surah_mode_rejects_timings_past_the_end(config, surah, needs_ffmpeg):
    from app.config import AyahTiming
    write_silent_wav(config.recitation_dir / "al_fatihah.wav", 3.0)
    for number in range(1, 8):
        write_silent_wav(config.urdu_dir / f"{number:03d}.wav", 1.0)
    config.audio.recitation_mode = "full_surah"
    config.audio.full_surah_file = "al_fatihah.wav"
    config.audio.full_surah_timings = [
        AyahTiming(ayah=n, start=(n - 1) * 5.0, end=n * 5.0) for n in range(1, 8)
    ]
    _, issues = audiosvc.check_audio(config, surah)
    assert any("only" in issue.detail for issue in issues)


def test_build_plan_uses_measured_durations(needs_ffmpeg, config_with_audio, surah):
    plan = audiosvc.build_plan(config_with_audio, surah)
    assert plan.mode == "per_ayah"
    assert len(plan.recitation) == 7 and len(plan.urdu) == 7
    assert plan.recitation[1].duration < plan.recitation[7].duration
    assert plan.total_audio_seconds > 0


def test_build_plan_refuses_to_run_with_missing_audio(config, surah):
    with pytest.raises(QVGError) as excinfo:
        audiosvc.build_plan(config, surah)
    assert "001.mp3" in excinfo.value.hint


def test_ambience_must_exist_when_enabled(config_with_audio, surah, needs_ffmpeg):
    config_with_audio.audio.ambience.enabled = True
    config_with_audio.audio.ambience.file = "assets/audio/nope.mp3"
    _, issues = audiosvc.check_audio(config_with_audio, surah)
    assert any("ambience" in issue.detail for issue in issues)
