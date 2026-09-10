"""Configuration loading, validation and the CLI wiring."""

from __future__ import annotations

import pytest

from app.config import load_config
from app.main import Validator, build_parser, main
from app.utils.fonts import FontResolver
from app.utils.logging import QVGError


def test_loads_the_shipped_config(config):
    assert config.video.width == 1920
    assert config.video.height == 1080
    assert config.video.fps == 30
    assert config.preview.width == 640
    assert config.thumbnail.width == 1280
    assert config.thumbnail.height == 720


def test_output_paths_use_the_slug(config):
    assert config.output_file("mp4").name == "001_Al-Fatihah.mp4"
    assert config.output_file("jpg").name == "001_Al-Fatihah.jpg"
    assert config.output_file("srt").name == "001_Al-Fatihah.srt"


def test_paths_resolve_under_the_project_root(config, project):
    assert config.data_file == project / "data/al_fatihah.json"
    assert config.recitation_dir == project / "assets/audio/recitation"


def test_ensure_dirs_creates_everything(config):
    config.ensure_dirs()
    for directory in (config.output_dir, config.temp_dir, config.logs_dir,
                      config.recitation_dir, config.urdu_dir, config.fonts_dir):
        assert directory.is_dir()


def test_missing_config_file_is_reported(tmp_path):
    with pytest.raises(QVGError) as excinfo:
        load_config(tmp_path / "absent.yaml", root=tmp_path)
    assert "not found" in excinfo.value.message


def test_broken_yaml_is_reported(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("video:\n  width: [1920\n", encoding="utf-8")
    with pytest.raises(QVGError) as excinfo:
        load_config(path, root=tmp_path)
    assert "not valid YAML" in excinfo.value.message


def test_odd_frame_width_is_rejected(project):
    path = project / "config.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("width: 1920", "width: 1921"),
        encoding="utf-8",
    )
    with pytest.raises(QVGError) as excinfo:
        load_config(path, root=project)
    assert "even numbers" in excinfo.value.message


def test_bad_hex_colour_is_rejected(project):
    import re

    path = project / "config.yaml"
    # Match whatever colour is shipped, so re-theming cannot quietly turn this
    # test into a no-op the way pinning the literal hex did.
    text, count = re.subn(
        r'^(\s*accent_color:)\s*"#[0-9A-Fa-f]{3,8}"', r'\1 "gold"',
        path.read_text(encoding="utf-8"), count=1, flags=re.MULTILINE,
    )
    assert count == 1, "accent_color is no longer in config.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(QVGError) as excinfo:
        load_config(path, root=project)
    assert "hex colour" in excinfo.value.message


def test_negative_padding_is_rejected(project):
    import re

    path = project / "config.yaml"
    # Match whatever the shipped value happens to be, so re-tuning the delivery
    # cannot quietly turn this test into a no-op.
    text, count = re.subn(
        r"^(\s*padding_before:)\s*[\d.]+", r" -1.0",
        path.read_text(encoding="utf-8"), count=1, flags=re.MULTILINE,
    )
    assert count == 1, "padding_before is no longer in config.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(QVGError):
        load_config(path, root=project)


def test_out_of_range_dim_is_rejected(project):
    import re

    path = project / "config.yaml"
    # Match whatever dim is shipped; pinning the literal turned this into a
    # no-op the moment the delivery was re-tuned.
    text, count = re.subn(
        r"^(\s*dim:)\s*[\d.]+", r"\1 4.0",
        path.read_text(encoding="utf-8"), count=1, flags=re.MULTILINE,
    )
    assert count == 1, "background.dim is no longer in config.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(QVGError):
        load_config(path, root=project)


def test_animation_speed_scaling(config):
    config.animation.speed = 2.0
    assert config.animation.scaled(4.0) == pytest.approx(2.0)
    config.animation.speed = 0.5
    assert config.animation.scaled(4.0) == pytest.approx(8.0)


def test_reciter_placeholder_is_detected(config):
    config.reciter.name = "RECITER_NAME"
    assert not config.reciter.is_configured
    config.reciter.name = "   "
    assert not config.reciter.is_configured
    config.reciter.name = "Someone"
    assert config.reciter.is_configured


def test_an_explicit_config_relocates_the_project(tmp_path, project):
    """--config pointing elsewhere must use THAT folder's assets, not this one's."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "config.yaml").write_text(
        (project / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    loaded = load_config(elsewhere / "config.yaml")
    assert loaded.root == elsewhere
    assert loaded.recitation_dir == elsewhere / "assets/audio/recitation"
    assert loaded.output_dir == elsewhere / "output"


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

def test_font_resolver_prefers_project_fonts(config, real_fonts_dir):
    resolver = FontResolver(config.fonts_dir, use_system_fonts=True)
    choice = resolver.resolve("arabic", "AmiriQuran-Regular.ttf", [])
    assert choice.path.parent == config.fonts_dir
    assert not choice.is_fallback


def test_font_resolver_falls_back(config, real_fonts_dir):
    resolver = FontResolver(config.fonts_dir, use_system_fonts=True)
    choice = resolver.resolve("arabic", "NoSuchFont.ttf", ["Amiri-Regular.ttf"])
    assert choice.is_fallback
    assert choice.name == "Amiri-Regular.ttf"


def test_font_resolver_error_is_actionable(tmp_path):
    resolver = FontResolver(tmp_path / "empty", use_system_fonts=False)
    with pytest.raises(QVGError) as excinfo:
        resolver.resolve("arabic", "Nope.ttf", ["AlsoNope.ttf"])
    assert "run.py fonts" in excinfo.value.hint


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validation_fails_without_audio(config, real_fonts_dir):
    validator = Validator(config)
    assert validator.run() is False
    failed = [c.name for c in validator.checks if c.failed]
    assert "Audio files" in failed


def test_validation_passes_with_audio(config_with_audio, real_fonts_dir, needs_ffmpeg):
    validator = Validator(config_with_audio)
    ok = validator.run()
    assert ok, [c.name for c in validator.checks if c.failed]


def test_validation_checks_the_expected_items(config_with_audio, real_fonts_dir, needs_ffmpeg):
    validator = Validator(config_with_audio)
    validator.run()
    names = {c.name for c in validator.checks}
    for expected in (
        "FFmpeg", "Quran data file", "Ayah count", "Ayah numbering",
        "Arabic text", "Urdu translation", "Font: arabic", "Font: urdu",
        "Background", "Disk space",
    ):
        assert expected in names


def test_validation_reports_a_broken_data_file(config_with_audio, real_fonts_dir, needs_ffmpeg):
    config_with_audio.data_file.write_text("{ not json", encoding="utf-8")
    validator = Validator(config_with_audio)
    assert validator.run() is False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_parser_exposes_the_documented_commands():
    parser = build_parser()
    for command in ("validate", "preview", "render", "thumbnail"):
        assert parser.parse_args([command]).command == command


def test_render_accepts_the_no_audio_flag():
    args = build_parser().parse_args(["render", "--no-audio"])
    assert args.no_audio is True


def test_preview_accepts_an_ayah_limit():
    assert build_parser().parse_args(["preview", "--ayahs", "3"]).ayahs == 3


def test_unknown_command_exits(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["definitely-not-a-command"])


def test_validate_command_returns_one_when_audio_is_missing(project, monkeypatch):
    monkeypatch.chdir(project)
    assert main(["--config", str(project / "config.yaml"), "validate"]) == 1


def test_fetch_audio_refuses_while_disabled(project, monkeypatch):
    monkeypatch.chdir(project)
    assert main(["--config", str(project / "config.yaml"), "fetch-audio"]) == 1


# ---------------------------------------------------------------------------
# Regressions
# ---------------------------------------------------------------------------

def test_a_warn_only_check_shows_as_warn_not_ok(config):
    """Regression: `OK if ok else (WARN if warn_only ...)` meant a passing check
    marked warn_only printed a green OK, so "rights not confirmed" looked fine."""
    from app.main import FAIL, OK, WARN

    validator = Validator(config)
    validator._add("passing", True)
    validator._add("flagged", True, warn_only=True)
    validator._add("soft failure", False, warn_only=True)
    validator._add("hard failure", False)

    assert [c.status for c in validator.checks] == [OK, WARN, WARN, FAIL]
    # Only the hard failure may block a render.
    assert [c.name for c in validator.checks if c.failed] == ["hard failure"]


def test_clean_refuses_to_empty_the_project_directory(tmp_path):
    """paths.temp_dir is user-editable; "" or "." would make `clean` delete
    the project."""
    from app.utils.files import clean_dir, project_root

    with pytest.raises(QVGError) as excinfo:
        clean_dir(project_root())
    assert "project directory" in excinfo.value.message

    with pytest.raises(QVGError):
        clean_dir(project_root().parent)


def test_clean_still_empties_a_real_temp_directory(config):
    from app.utils.files import clean_dir

    config.temp_dir.mkdir(parents=True, exist_ok=True)
    (config.temp_dir / "junk.txt").write_text("x", encoding="utf-8")
    (config.temp_dir / "sub").mkdir(exist_ok=True)
    clean_dir(config.temp_dir)
    assert list(config.temp_dir.iterdir()) == []


def test_validate_reports_the_narration_as_off_not_present(config_with_audio, real_fonts_dir, needs_ffmpeg):
    """Saying "all files present and decodable" when the narration is switched
    off is misleading - it is not being used at all."""
    from app.main import WARN

    config_with_audio.audio.urdu_narration = False
    validator = Validator(config_with_audio)
    assert validator.run() is True
    row = next(c for c in validator.checks if c.name == "Urdu narration")
    assert row.status == WARN
    assert "off" in row.detail


def test_validate_says_when_the_whole_urdu_section_is_off(config_with_audio, real_fonts_dir, needs_ffmpeg):
    """Reporting on the narration would be misleading when there is no Urdu
    card for it to accompany."""
    from app.main import WARN

    config_with_audio.content.urdu_translation = False
    validator = Validator(config_with_audio)
    assert validator.run() is True
    row = next(c for c in validator.checks if c.name == "Urdu section")
    assert row.status == WARN
    assert "Arabic text and recitation only" in row.detail
    assert not any(c.name == "Urdu narration" for c in validator.checks)


def test_validate_does_not_require_urdu_files_when_narration_is_off(config, real_fonts_dir, needs_ffmpeg):
    """With no narration, missing Urdu audio must not block the render."""
    from app.services import audio as audiosvc
    from app.models.surah import load_surah

    config.audio.urdu_narration = False
    surah = load_surah(config.data_file)
    # Only the recitation is missing in this fixture, so every issue must be
    # about recitation - none about the Urdu narration.
    _, issues = audiosvc.check_audio(config, surah)
    assert issues
    assert all("Urdu" not in issue.detail for issue in issues)


def test_the_shipped_config_names_a_background_that_exists():
    """A named background that is not there fails at render time, not at load.

    Worth its own check because the shipped config pins one deliberately: the
    backgrounds folder can hold more than one file, and "auto" would take the
    first it finds.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config.yaml", root=root)
    setting = config.background.source.strip()
    if setting.lower() in ("auto", "generated", ""):
        pytest.skip("no specific background is named")

    candidate = config.backgrounds_dir / setting
    assert candidate.is_file() or config.path(setting).is_file(), (
        f"config.yaml names background '{setting}', which is not in "
        f"{config.backgrounds_dir}"
    )


def test_the_shipped_config_does_not_point_at_a_pre_lettered_plate():
    """The generator draws the surah name and every ayah itself, so a plate
    that already carries text puts two sets on screen."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config.yaml", root=root)
    if config.theme.style != "glass":
        pytest.skip("only the glass style sets its text over artwork")

    setting = config.background.source.strip().lower()
    assert setting not in ("auto", ""), (
        "the glass style needs a named plate: 'auto' takes the first file in "
        "assets/backgrounds/, which may be a composed design with text on it"
    )
