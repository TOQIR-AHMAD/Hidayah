"""Typed configuration loaded from config.yaml (with .env overrides)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar, Literal, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.utils.files import project_root
from app.utils.logging import QVGError

Hex = str


class ProjectSettings(BaseModel):
    name: str = "Surah Al-Fatihah"
    slug: str = "001_Al-Fatihah"


class PathSettings(BaseModel):
    data_file: str = "data/al_fatihah.json"
    fonts_dir: str = "assets/fonts"
    backgrounds_dir: str = "assets/backgrounds"
    branding_dir: str = "assets/branding"
    recitation_dir: str = "assets/audio/recitation"
    urdu_dir: str = "assets/audio/urdu"
    output_dir: str = "output"
    temp_dir: str = "temp"
    logs_dir: str = "logs"


class VideoSettings(BaseModel):
    width: int = 1920
    height: int = 1080
    fps: int = 30
    video_codec: str = "libx264"
    crf: int = 18
    preset: str = "slow"
    pixel_format: str = "yuv420p"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    audio_sample_rate: int = 48000
    faststart: bool = True
    threads: int = 0
    playback_speed: float = 1.0

    @field_validator("width", "height")
    @classmethod
    def _even(cls, value: int) -> int:
        if value % 2 != 0:
            raise ValueError("width and height must be even numbers for H.264")
        if value < 64:
            raise ValueError("width and height must be at least 64 pixels")
        return value

    @field_validator("fps")
    @classmethod
    def _fps(cls, value: int) -> int:
        if not 1 <= value <= 120:
            raise ValueError("fps must be between 1 and 120")
        return value

    @field_validator("playback_speed")
    @classmethod
    def _playback_speed(cls, value: float) -> float:
        # atempo handles 0.5-2.0 in a single pass, which is the useful range
        # here anyway. Outside it the recitation stops sounding like itself.
        if not 0.5 <= value <= 2.0:
            raise ValueError("video.playback_speed must be between 0.5 and 2.0")
        return value


class PreviewSettings(BaseModel):
    width: int = 640
    height: int = 360
    fps: int = 24
    crf: int = 30
    preset: str = "veryfast"
    audio_bitrate: str = "96k"
    max_ayahs: int = 0


class AmbienceSettings(BaseModel):
    enabled: bool = False
    file: str = "assets/audio/ambience.mp3"
    volume_db: float = -30.0
    fade: float = 3.0


class AyahTiming(BaseModel):
    ayah: int
    start: float
    end: float

    @field_validator("start")
    @classmethod
    def _start_in_range(cls, value: float) -> float:
        # A negative start would slip past the renderer's `if trim_start > 0`
        # seek guard: no -ss would be emitted, but -t would still be the full
        # span, so the ayah would play from 0.0 and run into the next one.
        if value < 0:
            raise ValueError("start cannot be negative")
        return value

    @field_validator("end")
    @classmethod
    def _positive_span(cls, value: float, info: Any) -> float:
        start = info.data.get("start", 0.0)
        if value <= start:
            raise ValueError("end must be greater than start")
        return value


class AudioSettings(BaseModel):
    recitation_mode: Literal["per_ayah", "full_surah", "auto"] = "auto"
    full_surah_file: str = "al_fatihah.mp3"
    full_surah_timings: list[AyahTiming] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=lambda: ["mp3", "wav", "m4a", "ogg", "flac"])
    padding_before: float = 0.6
    padding_after: float = 1.1
    gap_between_ayahs: float = 0.7
    min_arabic_seconds: float = 0.0
    min_urdu_seconds: float = 6.5
    urdu_narration: bool = True
    urdu_seconds_per_word: float = 0.42
    normalize: bool = True
    target_lufs: float = -16.0
    max_gain_db: float = 12.0
    true_peak_ceiling_db: float = -1.5
    clip_fade_in: float = 0.08
    clip_fade_out: float = 0.18
    ambience: AmbienceSettings = Field(default_factory=AmbienceSettings)

    @field_validator(
        "padding_before", "padding_after", "gap_between_ayahs",
        "min_arabic_seconds", "min_urdu_seconds",
    )
    @classmethod
    def _non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("padding and minimum-duration values cannot be negative")
        return value


class ContentSettings(BaseModel):
    """Which sections the video is built from."""

    # Show the Urdu translation card after each ayah. With this off the video
    # is Arabic text plus recitation only, and audio.urdu_narration is ignored
    # because there is no card for it to accompany.
    urdu_translation: bool = True

    # Show the closing card - the "surah complete" line and the credits.
    # With this off the outro still runs, but as background only: the last
    # ayah dissolves, the frame fades, and the video ends on the backdrop
    # rather than cutting the instant the reciter stops. Set
    # animation.outro_duration to 0 to remove the tail as well.
    #
    # Credits are not lost when this is off - they stay in the MP4 metadata.
    outro_card: bool = True

    # Light the word currently being recited. Timings come from
    # data/word_timings.json (see WordTimingSettings); without that file they
    # are estimated from letter counts, which drifts on the long final word of
    # each ayah, so `validate` says which of the two is in use.
    word_highlight: bool = True


class WordTimingSettings(BaseModel):
    """Where per-word recitation timings come from.

    Timings only - no audio. The recording itself is whatever you put in
    assets/audio, and nothing here modifies it.
    """

    file: str = "data/word_timings.json"

    # quran.com publishes word segments per recitation. 7 is Mishary Rashid
    # Alafasy, the same reciter the shipped audio_sources template points at;
    # the ids are listed at https://api.quran.com/api/v4/resources/recitations
    recitation_id: int = 7
    reciter_name: str = "Mishary Rashid Alafasy"
    url_template: str = (
        "https://api.quran.com/api/v4/recitations/{recitation_id}"
        "/by_chapter/{surah}?per_page=300&fields=segments"
    )

    # A highlight shorter than this is a flicker rather than a cue, so short
    # words borrow from the gap before the next one starts.
    min_seconds: float = 0.16

    # Carry the highlight into the silence between words instead of dropping it
    # the instant a word ends. 1.0 holds it until the next word begins.
    carry_over: float = 0.65


class ReciterSettings(BaseModel):
    name: str = "RECITER_NAME"
    source: str = "SOURCE_NAME"
    license: str = "LICENSE_INFORMATION"
    rights_confirmed: bool = False

    @property
    def is_configured(self) -> bool:
        return self.name.strip().upper() not in {"", "RECITER_NAME"}


class TranslationCredit(BaseModel):
    urdu_translator: str = ""
    urdu_narrator: str = ""


class AudioSourceSettings(BaseModel):
    enabled: bool = False
    recitation_url_template: str = ""
    urdu_url_template: str = ""


class FontSettings(BaseModel):
    arabic: str = "AmiriQuran-Regular.ttf"
    arabic_fallbacks: list[str] = Field(default_factory=list)
    arabic_display: str = "Amiri-Bold.ttf"
    arabic_display_fallbacks: list[str] = Field(default_factory=list)
    urdu: str = "NotoNastaliqUrdu-Regular.ttf"
    urdu_fallbacks: list[str] = Field(default_factory=list)
    latin: str = "CormorantGaramond-Regular.ttf"
    latin_fallbacks: list[str] = Field(default_factory=list)
    use_system_fonts: bool = True


class ThemeSettings(BaseModel):
    # Which furniture is drawn round the text. Colours and sizes below apply to
    # all three; only the furniture changes.
    #
    #   classic  gold hairline frame with corner ornaments, eight-point star
    #            medallion, diamond rule
    #   ios      iOS's own: a rounded grouped-list panel, a pill badge,
    #            hairline separators, no glow
    #   glass    text set straight over supplied artwork in frosted capsules,
    #            with a rule and a rosette beneath it. Designed to sit on a
    #            background image rather than the generated backdrop.
    style: Literal["classic", "ios", "glass"] = "classic"

    # Dark or light ground. This is not just the palette: the generated
    # backdrop is built by ADDING light to black, which does nothing on a pale
    # ground, so "light" selects a different construction entirely. Set the
    # colours below to match - nothing here recolours them for you.
    appearance: Literal["dark", "light"] = "dark"

    # iOS surfaces. Values are Apple's own dark-mode system colours:
    # systemGray6 for a grouped panel, separator for a hairline.
    panel_color: Hex = "#1C1C1E"
    panel_opacity: float = 0.55
    panel_radius: float = 0.026      # of frame height
    panel_pad_x: float = 0.045       # of frame width
    panel_pad_y: float = 0.055       # of frame height
    panel_border_color: Hex = "#38383A"
    panel_border_opacity: float = 0.65
    panel_border_width: int = 2

    # The ayah number badge that replaces the medallion.
    badge_color: Hex = "#0A84FF"
    badge_opacity: float = 0.18
    badge_text_color: Hex = "#0A84FF"
    badge_height: float = 0.046      # of frame height
    badge_pad_x: float = 0.55        # of badge height

    separator_color: Hex = "#38383A"
    separator_opacity: float = 0.75
    separator_width: int = 2

    # Frosted capsules (the "glass" style). A translucent fill with a hairline
    # edge, sitting over the artwork - the chip that carries "SURAH 001" and
    # the surah name.
    chip_color: Hex = "#FFFFFF"
    chip_opacity: float = 0.14
    chip_border_color: Hex = "#FFFFFF"
    chip_border_opacity: float = 0.38
    chip_border_width: int = 2
    chip_pad_x: float = 1.05        # of the chip's text height
    chip_pad_y: float = 0.62
    # Of the chip height; 0.5 or more gives fully rounded ends.
    chip_radius: float = 0.5
    # Hairlines either side of the top chip. 0 leaves the chip on its own.
    chip_rule_width: float = 0.11   # of frame width, each side
    chip_rule_gap: float = 0.022    # clear space between rule and chip

    # The rule-and-rosette that closes the block.
    ornament_enabled: bool = True
    ornament_size: float = 0.030    # of frame height
    ornament_rule_width: float = 0.20
    ornament_opacity: float = 0.55

    # A soft drop shadow under text set over artwork. Without it, white type
    # over a photograph loses its edges wherever the picture goes pale.
    text_shadow_enabled: bool = False
    text_shadow_color: Hex = "#000000"
    text_shadow_opacity: float = 0.34
    text_shadow_blur: int = 18
    text_shadow_offset: int = 4

    background_color: Hex = "#05090C"
    deep_color: Hex = "#0A1219"
    accent_color: Hex = "#D8B871"
    accent_soft_color: Hex = "#8C7238"
    text_color: Hex = "#F6EEDD"
    text_secondary_color: Hex = "#D9CDB4"
    urdu_text_color: Hex = "#F3EADA"
    muted_color: Hex = "#7E8A93"

    margin_x: float = 0.085
    margin_y: float = 0.095

    size_arabic_max: float = 0.150
    size_arabic_min: float = 0.050
    size_urdu_max: float = 0.088
    size_urdu_min: float = 0.030
    size_surah_label: float = 0.034
    size_ayah_label: float = 0.030
    size_section_label: float = 0.036
    size_intro_arabic: float = 0.130
    size_intro_latin: float = 0.055
    size_outro_arabic: float = 0.080
    size_credit: float = 0.024

    line_spacing_arabic: float = 1.62
    line_spacing_urdu: float = 1.95
    max_lines_arabic: int = 4
    max_lines_urdu: int = 4
    text_width_ratio: float = 1.0

    glow_enabled: bool = True
    glow_radius: int = 26
    glow_opacity: float = 0.30

    # The word currently being recited (content.word_highlight).
    #
    # The lit word is drawn over the same glyphs the card already shows, from
    # the same shaping run, so it covers them exactly. It has to be opaque, or
    # the colour underneath shows through and muddies it.
    highlight_color: Hex = "#FFFFFF"
    highlight_glow_enabled: bool = True
    highlight_glow_radius: int = 22
    highlight_glow_opacity: float = 0.55

    # A capsule behind the lit word.
    highlight_pill_enabled: bool = True
    highlight_pill_color: Hex = "#D8B871"
    highlight_pill_opacity: float = 0.16
    highlight_pill_pad_x: float = 0.26   # of the word's own height
    highlight_pill_pad_y: float = 0.20
    # Of the capsule's height; 0.5 or more gives fully rounded ends.
    highlight_pill_radius: float = 0.5

    frame_enabled: bool = True
    frame_opacity: float = 0.42
    frame_inset: float = 0.045
    frame_line_width: int = 2
    corner_ornament: bool = True

    divider_enabled: bool = True
    divider_width_ratio: float = 0.26
    divider_opacity: float = 0.55

    medallion_enabled: bool = True
    medallion_size: float = 0.085

    vignette: float = 0.62
    grade_contrast: float = 1.04
    grade_saturation: float = 0.94
    grade_brightness: float = -0.010

    logo_file: str = "logo.png"
    logo_enabled: bool = True
    logo_height: float = 0.10
    logo_opacity: float = 0.9

    channel_name: str = ""
    channel_tagline: str = ""

    @field_validator(
        "background_color", "deep_color", "accent_color", "accent_soft_color",
        "text_color", "text_secondary_color", "urdu_text_color", "muted_color",
    )
    @classmethod
    def _hex(cls, value: str) -> str:
        raw = value.strip()
        if not raw.startswith("#") or len(raw) not in (4, 7):
            raise ValueError(f"'{value}' is not a hex colour like #D8B871")
        try:
            int(raw[1:], 16)
        except ValueError as exc:
            raise ValueError(f"'{value}' is not a hex colour like #D8B871") from exc
        return raw


class GeneratedBackground(BaseModel):
    pattern_scale: int = 300
    pattern_opacity: float = 0.075
    glow_opacity: float = 0.55
    grain: float = 0.020


class BackgroundSettings(BaseModel):
    source: str = "auto"
    ken_burns: bool = True
    zoom_start: float = 1.0
    zoom_end: float = 1.11
    pan_x: float = 0.020
    pan_y: float = -0.014
    dim: float = 0.55
    blur: float = 6.0
    loop_video: bool = True
    video_mute: bool = True
    generated: GeneratedBackground = Field(default_factory=GeneratedBackground)

    @field_validator("dim")
    @classmethod
    def _dim_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("background.dim must be between 0 and 1")
        return value


class AnimationSettings(BaseModel):
    speed: float = 1.0
    transition_duration: float = 0.85
    # Kept at or under AudioSettings.padding_before (0.60): a card still fading
    # in cannot carry a word highlight, so a longer ramp here silently drops
    # the opening word of every ayah.
    text_fade_in: float = 0.55
    text_fade_out: float = 0.70
    min_text_fade_out: float = 0.30
    text_rise: float = 16.0
    text_rise_duration: float = 1.40
    intro_duration: float = 4.0
    intro_fade_in: float = 1.00
    outro_duration: float = 6.0
    outro_fade_out: float = 2.00

    pattern_layer_enabled: bool = True
    pattern_opacity: float = 0.055
    pattern_drift_x: float = 30.0
    pattern_drift_y: float = -30.0

    light_enabled: bool = True
    light_opacity: float = 0.30
    light_drift_x: float = -13.0
    light_drift_y: float = 6.0

    particles_enabled: bool = True
    particles_opacity: float = 0.22
    particles_drift_x: float = -30.0
    particles_drift_y: float = -30.0
    particles_drift_x2: float = 30.0
    particles_drift_y2: float = -60.0

    @field_validator("speed")
    @classmethod
    def _speed(cls, value: float) -> float:
        if not 0.1 <= value <= 5.0:
            raise ValueError("animation.speed must be between 0.1 and 5.0")
        return value

    def scaled(self, seconds: float) -> float:
        """Apply the global animation speed multiplier to a duration."""
        return seconds / self.speed


class SubtitleSettings(BaseModel):
    enabled: bool = True
    # Subdirectory of output/ for the .srt and .ass.
    #
    # Players load a subtitle file automatically when it sits next to the video
    # with the same name, which puts the text on screen during local playback
    # even though nothing is burned into the picture. VLC also scans a few
    # subfolders by default - its sub-autodetect-path is
    # "./Subtitles, ./subtitles, ./Subs, ./subs" - so the obvious name for this
    # folder is exactly the wrong one. "captions" is not on that list.
    #
    # Set to "" to write them beside the video instead.
    folder: str = "captions"
    srt: bool = True
    ass: bool = True
    content: Literal["both", "arabic", "urdu"] = "both"
    burn_in: bool = False
    ass_font_size: int = 42


class ThumbnailSettings(BaseModel):
    width: int = 1280
    height: int = 720
    quality: int = 94
    arabic_text: str = ""
    urdu_text: str = ""
    latin_text: str = "Surah Al-Fatihah"
    subtitle_text: str = ""
    size_arabic: float = 0.28
    size_urdu: float = 0.095
    size_latin: float = 0.070
    size_subtitle: float = 0.052


class Config(BaseModel):
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    video: VideoSettings = Field(default_factory=VideoSettings)
    preview: PreviewSettings = Field(default_factory=PreviewSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    content: ContentSettings = Field(default_factory=ContentSettings)
    word_timings: WordTimingSettings = Field(default_factory=WordTimingSettings)
    reciter: ReciterSettings = Field(default_factory=ReciterSettings)
    translation_credit: TranslationCredit = Field(default_factory=TranslationCredit)
    audio_sources: AudioSourceSettings = Field(default_factory=AudioSourceSettings)
    fonts: FontSettings = Field(default_factory=FontSettings)
    theme: ThemeSettings = Field(default_factory=ThemeSettings)
    background: BackgroundSettings = Field(default_factory=BackgroundSettings)
    animation: AnimationSettings = Field(default_factory=AnimationSettings)
    subtitles: SubtitleSettings = Field(default_factory=SubtitleSettings)
    thumbnail: ThumbnailSettings = Field(default_factory=ThumbnailSettings)

    # Populated after loading; not part of the YAML file.
    root: Path = Field(default_factory=project_root, exclude=True)

    model_config = {"arbitrary_types_allowed": True}

    # -- path helpers --------------------------------------------------------
    def path(self, relative: str) -> Path:
        candidate = Path(relative)
        return candidate if candidate.is_absolute() else (self.root / candidate)

    @property
    def data_file(self) -> Path:
        return self.path(self.paths.data_file)

    @property
    def word_timings_file(self) -> Path:
        return self.path(self.word_timings.file)

    @property
    def fonts_dir(self) -> Path:
        return self.path(self.paths.fonts_dir)

    @property
    def backgrounds_dir(self) -> Path:
        return self.path(self.paths.backgrounds_dir)

    @property
    def branding_dir(self) -> Path:
        return self.path(self.paths.branding_dir)

    @property
    def recitation_dir(self) -> Path:
        return self.path(self.paths.recitation_dir)

    @property
    def urdu_dir(self) -> Path:
        return self.path(self.paths.urdu_dir)

    @property
    def output_dir(self) -> Path:
        return self.path(self.paths.output_dir)

    @property
    def temp_dir(self) -> Path:
        return self.path(self.paths.temp_dir)

    @property
    def logs_dir(self) -> Path:
        return self.path(self.paths.logs_dir)

    def output_file(self, extension: str) -> Path:
        return self.output_dir / f"{self.project.slug}.{extension.lstrip('.')}"

    # Folder names VLC scans for subtitles without being asked.
    AUTOLOADED_BY_PLAYERS: ClassVar[tuple[str, ...]] = ("subtitles", "subs")

    def subtitle_file(self, extension: str) -> Path:
        folder = self.subtitles.folder.strip().strip("/\\")
        directory = (self.output_dir / folder) if folder else self.output_dir
        return directory / f"{self.project.slug}.{extension.lstrip('.')}"

    def ensure_dirs(self) -> None:
        for directory in (
            self.fonts_dir, self.backgrounds_dir, self.branding_dir,
            self.recitation_dir, self.urdu_dir, self.output_dir,
            self.temp_dir, self.logs_dir, self.data_file.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def _load_dotenv(root: Path) -> None:
    """Minimal .env reader - avoids an extra dependency."""
    env_file = root / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def load_config(config_path: Optional[Path] = None, root: Optional[Path] = None) -> Config:
    """Read config.yaml into a validated Config object.

    Naming a config file also relocates the project. `--config ..\\other\\config.yaml`
    reads that project's assets and writes that project's output; without this, a
    config from elsewhere would silently drive the directories next to run.py.
    An explicit *root* still overrides, which is what the tests use.
    """
    # QVG_CONFIG can be set in the .env that sits beside run.py, so that file has
    # to be read before the config path is known.
    _load_dotenv(project_root())

    if config_path is None:
        env_path = os.environ.get("QVG_CONFIG", "").strip()
        config_path = Path(env_path) if env_path else project_root() / "config.yaml"

    config_path = Path(config_path)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path

    base = root if root is not None else config_path.parent
    _load_dotenv(base)

    if not config_path.is_file():
        raise QVGError(
            f"Configuration file not found: {config_path}",
            hint="config.yaml should sit next to run.py. If you deleted it, restore it "
            "from the repository or re-run setup.bat.",
        )

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise QVGError(
            f"config.yaml is not valid YAML: {exc}",
            hint="YAML is indentation-sensitive. Check that you used spaces (never tabs) "
            "and that every 'key: value' line has a space after the colon.",
        )
    if not isinstance(raw, dict):
        raise QVGError("config.yaml must contain a mapping of settings at the top level.")

    raw.pop("root", None)
    try:
        config = Config(**raw, root=base)
    except ValidationError as exc:
        details = "\n".join(
            f"  - {'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        raise QVGError(f"config.yaml has invalid settings:\n{details}")

    threads_override = os.environ.get("QVG_THREADS", "").strip()
    if threads_override.isdigit():
        config.video.threads = int(threads_override)

    return config
