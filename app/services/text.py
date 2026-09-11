"""Arabic / Urdu text shaping and rasterisation.

Pillow's own text layout cannot join Arabic letters or place Quranic diacritics,
and its Windows wheels no longer ship Raqm. So this module does the typography
itself:

    HarfBuzz (uharfbuzz)  ->  correct glyph selection, joining, ligatures and
                              GPOS mark positioning (harakat sit exactly where
                              the font designer anchored them)
    FreeType (freetype-py) ->  anti-aliased glyph bitmaps at any size

Everything is rendered into an 8-bit alpha mask which is then tinted. Glyphs are
combined with a "lighter" (max) blend so that overlapping marks never eat into
each other's anti-aliased edges.

If uharfbuzz/freetype-py are missing, a reduced-quality fallback based on
arabic-reshaper + python-bidi + Pillow is used instead, and a warning is logged.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Sequence

from PIL import Image, ImageChops, ImageFilter

from app.models.ayah import is_annotation
from app.utils.logging import QVGError, get_logger

try:  # pragma: no cover - import guard
    import uharfbuzz as hb
    import freetype

    SHAPER = "harfbuzz"
except ImportError:  # pragma: no cover - exercised only on reduced installs
    hb = None  # type: ignore[assignment]
    freetype = None  # type: ignore[assignment]
    SHAPER = "fallback"


# ---------------------------------------------------------------------------
# Bidirectional run splitting
# ---------------------------------------------------------------------------

def _char_direction(char: str) -> str:
    """'R', 'L' or 'N' (neutral) for a single character.

    Numbers count as left-to-right: digits run left-to-right even inside Arabic
    or Urdu text, so "۱۲۳" must not come out reversed.
    """
    category = unicodedata.bidirectional(char)
    if category in ("R", "AL", "AN"):
        return "R"
    if category in ("L", "EN"):
        return "L"
    return "N"


def split_runs(text: str, base_rtl: bool = True) -> list[tuple[str, bool]]:
    """Split *text* into directional runs: [(substring, is_rtl), ...].

    The one rule from the Unicode bidi algorithm that this project's content
    actually needs is N1/N2: a run of neutrals (spaces, punctuation, combining
    marks) between two strong characters of the SAME direction takes that
    direction, and otherwise takes the paragraph direction.

    Attaching neutrals to whatever sat on their left instead - which is what
    this used to do - put the word space on the wrong side of every direction
    change. In "۷ آیات" the space was swallowed by the leading digit run and
    ended up at the far edge of the line, leaving the digit jammed against the
    word. Resolving it to the paragraph direction attaches it to the Arabic
    instead, and the line sets correctly.
    """
    if not text:
        return []

    base = "R" if base_rtl else "L"
    directions = [_char_direction(char) for char in text]
    index = 0
    while index < len(directions):
        if directions[index] != "N":
            index += 1
            continue
        end = index
        while end < len(directions) and directions[end] == "N":
            end += 1
        before = directions[index - 1] if index > 0 else base
        after = directions[end] if end < len(directions) else base
        resolved = before if before == after else base
        for position in range(index, end):
            directions[position] = resolved
        index = end

    runs: list[tuple[str, bool]] = []
    start = 0
    for index in range(1, len(text) + 1):
        if index == len(text) or directions[index] != directions[start]:
            runs.append((text[start:index], directions[start] == "R"))
            start = index
    return runs


# ---------------------------------------------------------------------------
# Font handles
# ---------------------------------------------------------------------------

@dataclass
class FontHandle:
    path: Path
    size: int
    hb_font: object = None
    ft_face: object = None
    ascender: float = 0.0
    descender: float = 0.0
    upem: int = 1000


@lru_cache(maxsize=64)
def _load_font(path_str: str, size: int) -> FontHandle:
    path = Path(path_str)
    if not path.is_file():
        raise QVGError(f"Font file disappeared: {path}")

    if SHAPER != "harfbuzz":
        return FontHandle(path=path, size=size)

    try:
        blob = hb.Blob.from_file_path(str(path))
        face = hb.Face(blob)
        hb_font = hb.Font(face)
        hb_font.scale = (size * 64, size * 64)
        hb.ot_font_set_funcs(hb_font)

        ft_face = freetype.Face(str(path))
        ft_face.set_pixel_sizes(0, size)
    except Exception as exc:  # font file is unreadable or unsupported
        raise QVGError(
            f"The font '{path.name}' could not be loaded: {exc}",
            hint="The file may be corrupt or in an unsupported format. Replace it, "
            "or run `python run.py fonts` to fetch known-good SIL OFL fonts.",
        )

    # FreeType reports the scaled hhea metrics in 26.6 fixed point. Amiri Quran
    # and Noto Nastaliq Urdu both declare a very tall ascender to make room for
    # marks, which is exactly what keeps the harakat from being clipped.
    ascender = ft_face.size.ascender / 64.0
    descender = ft_face.size.descender / 64.0
    if ascender <= 0:  # a few fonts leave hhea empty
        ascender = size * 0.8
        descender = -size * 0.2

    return FontHandle(
        path=path,
        size=size,
        hb_font=hb_font,
        ft_face=ft_face,
        ascender=ascender,
        descender=descender,
        upem=face.upem,
    )


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------

@dataclass
class PositionedGlyph:
    gid: int
    x: float
    y: float
    # Which word of the line this glyph came from, counting from 0 in reading
    # order. -1 where that is unknown (the Pillow fallback shaper).
    word: int = -1


@dataclass
class ShapedLine:
    text: str
    width: float
    glyphs: list[PositionedGlyph] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def _shape_run(
    handle: FontHandle, text: str, rtl: bool
) -> tuple[list[tuple[int, float, float, float, int]], float]:
    """Shape one directional run. Returns (glyphs, advance) in pixels.

    Each glyph is (glyph_id, x_offset, y_offset, x_advance, cluster), where the
    cluster is the index of the character in *text* the glyph came from.
    HarfBuzz returns RTL runs already in visual order (leftmost glyph first),
    so the caller simply walks the list left to right.
    """
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.direction = "rtl" if rtl else "ltr"
    buffer.script = "arab" if rtl else "latn"
    buffer.language = "ur" if rtl else "en"
    buffer.cluster_level = 1

    # ccmp/liga/calt/mark/mkmk are what make Arabic joining and harakat work.
    hb.shape(handle.hb_font, buffer, {"kern": True, "liga": True, "calt": True})

    glyphs: list[tuple[int, float, float, float, int]] = []
    advance = 0.0
    for info, position in zip(buffer.glyph_infos, buffer.glyph_positions):
        glyphs.append(
            (
                info.codepoint,
                position.x_offset / 64.0,
                position.y_offset / 64.0,
                position.x_advance / 64.0,
                info.cluster,
            )
        )
        advance += position.x_advance / 64.0
    return glyphs, advance


def _word_of_character(text: str) -> list[int]:
    """For every character of *text*, which word it belongs to.

    Whitespace between words is attributed to the word that just ended, so a
    highlight drawn from these indices never reaches into the next word's
    space. Counting matches models.ayah.recited_words, which is what the
    timings are keyed on - so a standalone waqf sign is attributed to the word
    before it rather than taking a number of its own.
    """
    mapping: list[int] = [0] * len(text)
    word = -1
    position = 0
    for token in re.finditer(r"\S+", text):
        if not is_annotation(token.group()):
            word += 1
        start, end = token.span()
        # The gap before this token belongs to whatever came before it.
        for index in range(position, start):
            mapping[index] = max(0, word - 1 if word >= 0 else 0)
        for index in range(start, end):
            mapping[index] = max(0, word)
        position = end
    for index in range(position, len(text)):
        mapping[index] = max(0, word)
    return mapping


def shape_line(
    handle: FontHandle, text: str, base_rtl: bool = True, tracking: float = 0.0
) -> ShapedLine:
    """Shape a whole line, laying its directional runs out visually.

    *tracking* adds extra space after every advancing glyph (used to letterspace
    Latin small-caps headings). Zero-advance glyphs - Arabic combining marks -
    are skipped so harakat stay locked to their base letter.
    """
    runs = split_runs(text, base_rtl=base_rtl)

    # Where each run starts in `text`, so a glyph's cluster - which HarfBuzz
    # reports relative to its own run - can be turned back into a position in
    # the whole line, and from there into a word number.
    offsets: list[int] = []
    cursor_offset = 0
    for run_text, _ in runs:
        offsets.append(cursor_offset)
        cursor_offset += len(run_text)

    if base_rtl:
        # In an RTL paragraph the first logical run sits furthest right, so
        # walking the runs in reverse gives left-to-right placement order.
        runs = list(reversed(runs))
        offsets = list(reversed(offsets))

    word_of_char = _word_of_character(text)

    pen = 0.0
    glyphs: list[PositionedGlyph] = []
    for (run_text, run_rtl), run_offset in zip(runs, offsets):
        run_glyphs, run_advance = _shape_run(handle, run_text, run_rtl)
        cursor = pen
        for gid, x_off, y_off, x_adv, cluster in run_glyphs:
            position = run_offset + int(cluster)
            word = word_of_char[position] if 0 <= position < len(word_of_char) else -1
            glyphs.append(
                PositionedGlyph(gid=gid, x=cursor + x_off, y=y_off, word=word)
            )
            cursor += x_adv + (tracking if x_adv > 0 else 0.0)
        pen = cursor

    return ShapedLine(text=text, width=pen, glyphs=glyphs)


def measure(
    handle: FontHandle, text: str, base_rtl: bool = True, tracking: float = 0.0
) -> float:
    if SHAPER != "harfbuzz":
        return _fallback_measure(handle, text)
    total = 0.0
    for run_text, run_rtl in split_runs(text, base_rtl=base_rtl):
        run_glyphs, advance = _shape_run(handle, run_text, run_rtl)
        total += advance
        if tracking:
            total += tracking * sum(1 for g in run_glyphs if g[3] > 0)
    return total


# ---------------------------------------------------------------------------
# Rasterisation
# ---------------------------------------------------------------------------

@dataclass
class RenderedText:
    """An 8-bit alpha mask holding rendered text, cropped to its ink."""

    mask: Image.Image
    font_size: int
    line_count: int
    lines: list[str]

    @property
    def width(self) -> int:
        return self.mask.width

    @property
    def height(self) -> int:
        return self.mask.height

    def tinted(self, color: str, opacity: float = 1.0) -> Image.Image:
        layer = Image.new("RGBA", self.mask.size, color)
        alpha = self.mask if opacity >= 0.999 else self.mask.point(
            lambda v: int(v * max(0.0, min(1.0, opacity)))
        )
        layer.putalpha(alpha)
        return layer

    def glow(self, color: str, radius: int, opacity: float) -> Image.Image:
        pad = radius * 3
        padded = Image.new("L", (self.width + pad * 2, self.height + pad * 2), 0)
        padded.paste(self.mask, (pad, pad))
        blurred = padded.filter(ImageFilter.GaussianBlur(radius))
        blurred = blurred.point(lambda v: int(min(255, v * 1.9 * opacity)))
        layer = Image.new("RGBA", blurred.size, color)
        layer.putalpha(blurred)
        return layer


def _blit_glyph(canvas: Image.Image, glyph: Image.Image, x: int, y: int) -> None:
    """Max-blend *glyph* into *canvas* at (x, y), clipping at the edges."""
    if glyph.width == 0 or glyph.height == 0:
        return
    cw, ch = canvas.size

    src_x = max(0, -x)
    src_y = max(0, -y)
    dst_x = max(0, x)
    dst_y = max(0, y)
    width = min(glyph.width - src_x, cw - dst_x)
    height = min(glyph.height - src_y, ch - dst_y)
    if width <= 0 or height <= 0:
        return

    piece = glyph.crop((src_x, src_y, src_x + width, src_y + height))
    box = (dst_x, dst_y, dst_x + width, dst_y + height)
    canvas.paste(ImageChops.lighter(canvas.crop(box), piece), box)


def _glyph_bitmap(handle: FontHandle, gid: int) -> tuple[Optional[Image.Image], int, int]:
    face = handle.ft_face
    face.load_glyph(
        gid,
        freetype.FT_LOAD_RENDER | freetype.FT_LOAD_NO_HINTING | freetype.FT_LOAD_TARGET_NORMAL,
    )
    slot = face.glyph
    bitmap = slot.bitmap
    width, rows, pitch = bitmap.width, bitmap.rows, bitmap.pitch
    if width == 0 or rows == 0:
        return None, 0, 0

    buffer = bitmap.buffer
    if pitch == width:
        data = bytes(buffer[: width * rows])
    else:  # rows are padded - copy them one by one
        data = b"".join(bytes(buffer[r * pitch : r * pitch + width]) for r in range(rows))
    return Image.frombytes("L", (width, rows), data), slot.bitmap_left, slot.bitmap_top


@dataclass
class WordMask:
    """One word's ink, positioned inside a RenderedText's cropped mask."""

    index: int          # word number across the whole block, 0 = first read
    text: str
    x: int              # offset of this mask inside the parent mask
    y: int
    mask: Image.Image   # 8-bit alpha, cropped to the word's own ink

    @property
    def width(self) -> int:
        return self.mask.width

    @property
    def height(self) -> int:
        return self.mask.height

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.width, self.y + self.height)


def render_lines(
    handle: FontHandle,
    lines: Sequence[str],
    line_height: float,
    align: str = "center",
    base_rtl: bool = True,
    max_width: Optional[int] = None,
    tracking: float = 0.0,
) -> RenderedText:
    """Rasterise *lines* into a cropped alpha mask."""
    rendered, _ = _rasterise(
        handle, lines, line_height, align, base_rtl, max_width, tracking,
        collect_words=False,
    )
    return rendered


def render_lines_with_words(
    handle: FontHandle,
    lines: Sequence[str],
    line_height: float,
    align: str = "center",
    base_rtl: bool = True,
    max_width: Optional[int] = None,
    tracking: float = 0.0,
) -> tuple[RenderedText, list[WordMask]]:
    """As `render_lines`, plus a separate mask for each word.

    The word masks are rasterised from the SAME shaping run as the block, not
    by shaping each word on its own: joining, ligatures and mark positioning
    all depend on a letter's neighbours, so a word shaped in isolation would
    not land on the same pixels as the same word shaped in its line.

    Word numbers run across the whole block in reading order, so they line up
    with `text.split()` on the unwrapped string - which is what the recitation
    timings are keyed on.
    """
    return _rasterise(
        handle, lines, line_height, align, base_rtl, max_width, tracking,
        collect_words=True,
    )


def _rasterise(
    handle: FontHandle,
    lines: Sequence[str],
    line_height: float,
    align: str,
    base_rtl: bool,
    max_width: Optional[int],
    tracking: float,
    collect_words: bool,
) -> tuple[RenderedText, list[WordMask]]:
    if SHAPER != "harfbuzz":
        return _fallback_render_lines(handle, lines, line_height, align, base_rtl), []

    shaped = [
        shape_line(handle, line, base_rtl=base_rtl, tracking=tracking) for line in lines
    ]
    content_width = max([s.width for s in shaped] + [1.0])
    if max_width:
        content_width = max(content_width, max_width)

    # Generous padding so ascenders, descenders and Nastaliq's deep swashes
    # can never be clipped. The mask is cropped to its ink afterwards.
    pad_x = int(handle.size * 1.2) + 24
    pad_y = int(handle.size * 1.6) + 24
    canvas_w = int(content_width + pad_x * 2)
    canvas_h = int(line_height * max(1, len(shaped)) + pad_y * 2)
    canvas = Image.new("L", (canvas_w, canvas_h), 0)

    # One scratch canvas per word, drawn in step with the block so both come
    # out of the identical glyph positions.
    word_canvases: dict[int, Image.Image] = {}
    word_text: dict[int, str] = {}
    words_before = 0

    baseline = pad_y + handle.ascender
    for shaped_line in shaped:
        if align == "center":
            offset_x = pad_x + (content_width - shaped_line.width) / 2.0
        elif align == "right":
            offset_x = pad_x + (content_width - shaped_line.width)
        else:
            offset_x = float(pad_x)

        line_words = shaped_line.text.split()
        for glyph in shaped_line.glyphs:
            bitmap, left, top = _glyph_bitmap(handle, glyph.gid)
            if bitmap is None:
                continue
            x = int(round(offset_x + glyph.x + left))
            y = int(round(baseline - glyph.y - top))
            _blit_glyph(canvas, bitmap, x, y)

            if collect_words and glyph.word >= 0:
                number = words_before + glyph.word
                target = word_canvases.get(number)
                if target is None:
                    target = Image.new("L", (canvas_w, canvas_h), 0)
                    word_canvases[number] = target
                    if glyph.word < len(line_words):
                        word_text[number] = line_words[glyph.word]
                _blit_glyph(target, bitmap, x, y)

        words_before += len(line_words)
        baseline += line_height

    bbox = canvas.getbbox()
    if bbox is None:  # nothing was drawn (empty string)
        empty = RenderedText(mask=Image.new("L", (1, 1), 0), font_size=handle.size,
                             line_count=len(lines), lines=list(lines))
        return empty, []

    rendered = RenderedText(
        mask=canvas.crop(bbox),
        font_size=handle.size,
        line_count=len(shaped),
        lines=list(lines),
    )

    masks: list[WordMask] = []
    for number in sorted(word_canvases):
        word_canvas = word_canvases[number]
        word_bbox = word_canvas.getbbox()
        if word_bbox is None:
            continue
        masks.append(
            WordMask(
                index=number,
                text=word_text.get(number, ""),
                # Relative to the block's crop, so callers only ever need the
                # position they already paste the block at.
                x=word_bbox[0] - bbox[0],
                y=word_bbox[1] - bbox[1],
                mask=word_canvas.crop(word_bbox),
            )
        )
    return rendered, masks


# ---------------------------------------------------------------------------
# Line breaking and automatic sizing
# ---------------------------------------------------------------------------

def wrap_text(
    handle: FontHandle,
    text: str,
    max_width: float,
    base_rtl: bool = True,
    tracking: float = 0.0,
) -> list[str]:
    """Greedy word wrap using real shaped widths."""
    words = [w for w in text.split() if w]
    if not words:
        return [""]

    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if current and measure(handle, candidate, base_rtl, tracking) > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


@dataclass
class FitResult:
    rendered: RenderedText
    font_size: int
    lines: list[str]
    line_height: float


def fit_text(
    font_path: Path,
    text: str,
    max_width: int,
    max_height: int,
    size_max: int,
    size_min: int,
    line_spacing: float,
    max_lines: int = 4,
    align: str = "center",
    base_rtl: bool = True,
) -> FitResult:
    """Find the largest font size at which *text* fits the given box.

    A binary search on the estimated block height gets close, then the result is
    rendered and its real ink height is checked, shrinking further if needed.
    This is what guarantees "no clipped glyphs" for both Amiri and Nastaliq,
    whose ink extents differ wildly from their nominal metrics.
    """
    if not text.strip():
        raise QVGError("Cannot render an empty string.")
    size_min = max(6, min(size_min, size_max))

    def evaluate(size: int) -> tuple[list[str], float]:
        handle = _load_font(str(font_path), size)
        lines = wrap_text(handle, text, max_width, base_rtl)
        return lines, line_spacing * size * len(lines)

    low, high, best = size_min, size_max, size_min
    while low <= high:
        mid = (low + high) // 2
        lines, height = evaluate(mid)
        if len(lines) <= max_lines and height <= max_height:
            best = mid
            low = mid + 1
        else:
            high = mid - 1

    size = best
    for _ in range(10):
        handle = _load_font(str(font_path), size)
        lines = wrap_text(handle, text, max_width, base_rtl)
        line_height = line_spacing * size
        rendered = render_lines(handle, lines, line_height, align=align, base_rtl=base_rtl)
        if (rendered.height <= max_height and rendered.width <= max_width) or size <= size_min:
            return FitResult(rendered=rendered, font_size=size, lines=lines, line_height=line_height)
        size = max(size_min, int(size * 0.94))

    return FitResult(rendered=rendered, font_size=size, lines=lines, line_height=line_height)


def font_handle(font_path: Path, size: int) -> FontHandle:
    """A loaded, cached font at one size.

    Callers that need to re-rasterise exactly what `fit_text` produced have to
    use the same handle it did, so this exposes the cache rather than letting
    them build their own.
    """
    return _load_font(str(font_path), size)


def render_single_line(
    font_path: Path,
    text: str,
    size: int,
    align: str = "center",
    base_rtl: bool = True,
    line_spacing: float = 1.35,
    tracking: float = 0.0,
) -> RenderedText:
    """Render one line of text at an exact size (used for labels and headings)."""
    handle = _load_font(str(font_path), max(6, int(size)))
    return render_lines(
        handle, [text], line_spacing * handle.size,
        align=align, base_rtl=base_rtl, tracking=tracking,
    )


# ---------------------------------------------------------------------------
# Reduced-quality fallback (no uharfbuzz / freetype-py available)
# ---------------------------------------------------------------------------

_fallback_warned = False


def _fallback_font(handle: FontHandle):
    from PIL import ImageFont

    return ImageFont.truetype(str(handle.path), handle.size)


def _fallback_prepare(text: str) -> str:
    global _fallback_warned
    if not _fallback_warned:
        get_logger().warning(
            "uharfbuzz/freetype-py are not installed - falling back to "
            "arabic-reshaper. Quranic diacritics may be positioned imprecisely. "
            "Fix with: pip install uharfbuzz freetype-py"
        )
        _fallback_warned = True
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(text))
    except ImportError:
        return text


def _fallback_measure(handle: FontHandle, text: str) -> float:
    font = _fallback_font(handle)
    return font.getlength(_fallback_prepare(text))


def _fallback_render_lines(
    handle: FontHandle,
    lines: Sequence[str],
    line_height: float,
    align: str,
    base_rtl: bool,
) -> RenderedText:
    from PIL import ImageDraw

    font = _fallback_font(handle)
    prepared = [_fallback_prepare(line) for line in lines]
    widths = [font.getlength(line) for line in prepared] or [1.0]
    content_width = max(widths)

    pad = int(handle.size * 1.5) + 24
    canvas = Image.new(
        "L", (int(content_width + pad * 2), int(line_height * len(prepared) + pad * 2)), 0
    )
    draw = ImageDraw.Draw(canvas)
    y = float(pad)
    for line, width in zip(prepared, widths):
        if align == "center":
            x = pad + (content_width - width) / 2
        elif align == "right":
            x = pad + (content_width - width)
        else:
            x = float(pad)
        draw.text((x, y), line, font=font, fill=255)
        y += line_height

    bbox = canvas.getbbox()
    mask = canvas.crop(bbox) if bbox else Image.new("L", (1, 1), 0)
    return RenderedText(mask=mask, font_size=handle.size, line_count=len(lines), lines=list(lines))


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def shaping_backend() -> str:
    return SHAPER


def check_font_covers(font_path: Path, text: str) -> list[str]:
    """Return the characters in *text* that the font has no glyph for."""
    if SHAPER != "harfbuzz":
        return []
    try:
        face = freetype.Face(str(font_path))
    except Exception:
        return []
    missing: list[str] = []
    seen: set[str] = set()
    for char in text:
        if char.isspace() or char in seen:
            continue
        seen.add(char)
        if face.get_char_index(ord(char)) == 0:
            missing.append(char)
    return missing


def describe_missing(chars: Iterable[str]) -> str:
    return ", ".join(f"U+{ord(c):04X} ({unicodedata.name(c, '?')})" for c in chars)
