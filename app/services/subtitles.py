"""SRT and ASS subtitle generation.

Timings come from the same timeline the video uses, so the subtitles line up
with the recitation to the frame.

Right-to-left handling differs between the two formats:

  * SRT has no styling, so each RTL line is wrapped in an explicit Unicode
    RIGHT-TO-LEFT EMBEDDING / POP DIRECTIONAL FORMATTING pair. Players that
    honour the bidi algorithm then lay the line out correctly even when the
    surrounding UI is left-to-right.
  * ASS is rendered by libass, which shapes Arabic properly on its own; the
    style just needs the right font and alignment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from app.config import Config
from app.models.surah import Surah
from app.rendering.timeline import Segment, Timeline
from app.utils.fonts import FontResolver
from app.utils.logging import get_logger

RLE = "‫"  # RIGHT-TO-LEFT EMBEDDING
PDF = "‬"  # POP DIRECTIONAL FORMATTING


def _srt_timestamp(seconds: float) -> str:
    """Round to milliseconds FIRST, then split into fields.

    Splitting first and rounding after lets a value like 59.9996 s produce
    "00:00:60,000" - which is not a valid SRT timestamp, and which some players
    reject outright. Carrying happens naturally in integer milliseconds.
    """
    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _ass_timestamp(seconds: float) -> str:
    """Same rounding-first rule; ASS counts in centiseconds."""
    total_cs = int(round(max(0.0, seconds) * 100))
    hours, remainder = divmod(total_cs, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, centis = divmod(remainder, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _selected(config: Config, segment: Segment) -> bool:
    if not segment.subtitle_text:
        return False
    wanted = config.subtitles.content
    if wanted == "arabic":
        return segment.subtitle_language == "ar"
    if wanted == "urdu":
        return segment.subtitle_language == "ur"
    return True


def _cues(config: Config, timeline: Timeline) -> list[tuple[float, float, str, str]]:
    """(start, end, text, language) for every subtitle line."""
    cues: list[tuple[float, float, str, str]] = []
    for segment in timeline.segments:
        if not _selected(config, segment):
            continue
        start = segment.audio_start if segment.audio else segment.start
        end = segment.audio_end if segment.audio else segment.end
        # Hold the line for the trailing pause so it does not blink away
        # the instant the reciter stops.
        end = min(segment.end, end + config.audio.padding_after * 0.6)
        if end - start < 0.4:
            end = start + 0.4
        cues.append((start, end, " ".join(segment.subtitle_text.split()), segment.subtitle_language))
    return cues


def write_srt(config: Config, timeline: Timeline, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for number, (start, end, text, _language) in enumerate(_cues(config, timeline), start=1):
        blocks.append(
            f"{number}\n"
            f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n"
            f"{RLE}{text}{PDF}\n"
        )
    # A BOM helps Windows players detect UTF-8 in .srt files.
    destination.write_text("\n".join(blocks), encoding="utf-8-sig")
    get_logger().info("Wrote %s (%d cues)", destination.name, len(blocks))
    return destination


ASS_HEADER = """[Script Info]
Title: {title}
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Arabic,{arabic_font},{arabic_size},{primary},&H000000FF,{outline},&H64000000,0,0,0,0,100,100,0,0,1,{border},1,2,{margin},{margin},{margin_v},1
Style: Urdu,{urdu_font},{urdu_size},{urdu_primary},&H000000FF,{outline},&H64000000,0,0,0,0,100,100,0,0,1,{border},1,2,{margin},{margin},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_colour(hex_colour: str) -> str:
    """#RRGGBB -> &H00BBGGRR (ASS stores colours as AABBGGRR, little-endian)."""
    raw = hex_colour.strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    red, green, blue = raw[0:2], raw[2:4], raw[4:6]
    return f"&H00{blue}{green}{red}".upper()


def _font_family(path: Optional[Path], fallback: str) -> str:
    """libass matches by family name, so derive one from the file name."""
    if path is None:
        return fallback
    stem = path.stem
    for marker in ("-Regular", "-Bold", "[wght]", "-Medium"):
        stem = stem.replace(marker, "")
    # "NotoNastaliqUrdu" -> "Noto Nastaliq Urdu"
    spaced = ""
    for index, char in enumerate(stem):
        if char.isupper() and index > 0 and not stem[index - 1].isupper():
            spaced += " "
        spaced += char
    return spaced.strip() or fallback


def write_ass(
    config: Config, timeline: Timeline, surah: Surah, destination: Path,
    width: Optional[int] = None, height: Optional[int] = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    theme = config.theme
    width = width or config.video.width
    height = height or config.video.height

    resolver = FontResolver(config.fonts_dir, config.fonts.use_system_fonts)
    try:
        arabic_path = resolver.resolve("arabic", config.fonts.arabic, config.fonts.arabic_fallbacks).path
    except Exception:
        arabic_path = None
    try:
        urdu_path = resolver.resolve("urdu", config.fonts.urdu, config.fonts.urdu_fallbacks).path
    except Exception:
        urdu_path = None

    base_size = config.subtitles.ass_font_size * height / 1080

    header = ASS_HEADER.format(
        title=f"Surah {surah.surah_number} - {surah.name_english}",
        width=width,
        height=height,
        arabic_font=_font_family(arabic_path, "Arial"),
        urdu_font=_font_family(urdu_path, "Arial"),
        arabic_size=int(base_size * 1.15),
        urdu_size=int(base_size),
        primary=_ass_colour(theme.text_color),
        urdu_primary=_ass_colour(theme.urdu_text_color),
        outline=_ass_colour("#000000"),
        border=max(1, int(2 * height / 1080)),
        margin=int(width * theme.margin_x),
        margin_v=int(height * 0.055),
    )

    lines = [header]
    for start, end, text, language in _cues(config, timeline):
        style = "Arabic" if language == "ar" else "Urdu"
        safe = text.replace("\n", " ").replace("{", "(").replace("}", ")")
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},{style},,0,0,0,,{safe}"
        )

    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    get_logger().info("Wrote %s", destination.name)
    return destination


def generate(config: Config, timeline: Timeline, surah: Surah) -> list[Path]:
    """Write whichever subtitle formats are enabled in config.yaml."""
    if not config.subtitles.enabled:
        return []
    written: list[Path] = []
    if config.subtitles.srt:
        written.append(write_srt(config, timeline, config.subtitle_file("srt")))
    if config.subtitles.ass:
        written.append(write_ass(config, timeline, surah, config.subtitle_file("ass")))
    return written


def cue_count(config: Config, timeline: Timeline) -> int:
    return len(_cues(config, timeline))


def iter_cues(config: Config, timeline: Timeline) -> Iterable[tuple[float, float, str, str]]:
    return iter(_cues(config, timeline))
