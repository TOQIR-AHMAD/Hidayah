"""Builds the full-frame RGBA cards that are overlaid on the moving background.

Layout is a centred vertical stack. The three ayah-related cards share the same
header geometry (surah name, ayah medallion, section label) so that only the
body text changes between the Arabic and Urdu halves of an ayah - the eye stays
on the words instead of chasing furniture around the screen.

Every card is transparent apart from its ink, which lets FFmpeg cross-fade one
card into the next over a continuous background.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageChops

from app.config import Config
from app.models.ayah import Ayah, recited_words
from app.models.surah import Surah
from app.rendering import textures
from app.rendering.pagination import AyahPage, single_page, split_pages
from app.services import text as textsvc
from app.utils.fonts import FontResolver
from app.utils.logging import get_logger

# Urdu uses the Extended Arabic-Indic digits (U+06F0..U+06F9); Arabic uses
# U+0660..U+0669. Using the right set for each language matters visually.
URDU_DIGITS = "۰۱۲۳۴۵۶۷۸۹"

SURAH_HEADER_ARABIC = "سُورَةُ الْفَاتِحَةِ"
LABEL_AYAH_URDU = "آیت"
LABEL_URDU_TRANSLATION = "اردو ترجمہ"
OUTRO_ARABIC = "سورۃ الفاتحہ مکمل ہوئی"


def urdu_number(value: int) -> str:
    return "".join(URDU_DIGITS[int(d)] for d in str(value))


def _composite(canvas: Image.Image, layer: Image.Image, x: int, y: int) -> None:
    """alpha_composite *layer* at (x, y), clipping instead of raising.

    Pillow's alpha_composite refuses a negative destination, and a capsule
    round the first or last word of a line can start left of the frame.
    """
    if x >= canvas.width or y >= canvas.height:
        return
    crop_x = max(0, -x)
    crop_y = max(0, -y)
    if crop_x >= layer.width or crop_y >= layer.height:
        return
    visible_w = min(layer.width - crop_x, canvas.width - max(0, x))
    visible_h = min(layer.height - crop_y, canvas.height - max(0, y))
    if visible_w <= 0 or visible_h <= 0:
        return
    if (crop_x, crop_y, visible_w, visible_h) != (0, 0, layer.width, layer.height):
        layer = layer.crop((crop_x, crop_y, crop_x + visible_w, crop_y + visible_h))
    canvas.alpha_composite(layer, (max(0, x), max(0, y)))


@dataclass
class Fonts:
    arabic: Path
    arabic_display: Path
    urdu: Path
    latin: Path

    @classmethod
    def resolve(cls, config: Config) -> "Fonts":
        resolver = FontResolver(config.fonts_dir, config.fonts.use_system_fonts)
        spec = config.fonts
        return cls(
            arabic=resolver.resolve("arabic", spec.arabic, spec.arabic_fallbacks).path,
            arabic_display=resolver.resolve(
                "arabic_display", spec.arabic_display, spec.arabic_display_fallbacks
            ).path,
            urdu=resolver.resolve("urdu", spec.urdu, spec.urdu_fallbacks).path,
            latin=resolver.resolve("latin", spec.latin, spec.latin_fallbacks).path,
        )


@dataclass
class ArabicLayout:
    """The measured position of one ayah's text on its card."""

    fitted: textsvc.FitResult
    words: list[textsvc.WordMask]
    left: int
    top: int
    available: int
    divider_h: int

    def word(self, index: int) -> Optional[textsvc.WordMask]:
        for mask in self.words:
            if mask.index == index:
                return mask
        return None

    @property
    def word_count(self) -> int:
        return len(self.words)


class CardBuilder:
    """Creates every on-screen card for one render at one resolution."""

    def __init__(self, config: Config, surah: Surah, width: int, height: int) -> None:
        self.config = config
        self.surah = surah
        self.width = width
        self.height = height
        self.theme = config.theme
        self.fonts = Fonts.resolve(config)
        self.log = get_logger()

        self.margin_x = int(width * self.theme.margin_x)
        self.margin_y = int(height * self.theme.margin_y)
        self.content_width = int((width - 2 * self.margin_x) * self.theme.text_width_ratio)
        self.content_height = height - 2 * self.margin_y

        self._arabic_layouts: dict[tuple[int, int], ArabicLayout] = {}
        self._pages: dict[int, list[AyahPage]] = {}

    # -- helpers -------------------------------------------------------------
    def _px(self, fraction: float) -> int:
        return max(8, int(round(self.height * fraction)))

    def _blank(self) -> Image.Image:
        return Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))

    def _paste_centered(
        self,
        canvas: Image.Image,
        rendered: textsvc.RenderedText,
        top: int,
        color: str,
        opacity: float = 1.0,
        glow: bool = False,
    ) -> int:
        """Paste *rendered* horizontally centred at *top*; returns its bottom edge."""
        left = (self.width - rendered.width) // 2

        # The shadow goes down first so the glow, if any, sits on top of it.
        self._shadow(canvas, rendered, left, top)

        if glow and self.theme.glow_enabled:
            radius = max(2, int(self.theme.glow_radius * self.height / 1080))
            glow_layer = rendered.glow(
                self.theme.accent_color, radius, self.theme.glow_opacity
            )
            gx = (self.width - glow_layer.width) // 2
            gy = top - (glow_layer.height - rendered.height) // 2
            canvas.alpha_composite(glow_layer, (max(0, gx), max(0, gy)))

        layer = rendered.tinted(color, opacity)
        canvas.alpha_composite(layer, ((self.width - layer.width) // 2, top))
        return top + layer.height

    @property
    def is_ios(self) -> bool:
        return self.theme.style == "ios"

    @property
    def is_glass(self) -> bool:
        return self.theme.style == "glass"

    def _divider(self, canvas: Image.Image, top: int, width_ratio: Optional[float] = None) -> int:
        if not self.theme.divider_enabled:
            return top
        ratio = width_ratio if width_ratio is not None else self.theme.divider_width_ratio
        divider_w = max(40, int(self.width * ratio))

        if self.is_ios:
            # A plain hairline. iOS separates content with a rule of exactly one
            # hairline, never with an ornament.
            return self._separator(canvas, top, ratio)

        divider_h = max(4, self._px(0.014))
        divider = textures.make_divider(
            divider_w, divider_h, self.theme.accent_color, self.theme.divider_opacity
        )
        canvas.alpha_composite(divider, ((self.width - divider_w) // 2, top))
        return top + divider_h

    # -- iOS furniture -------------------------------------------------------
    def _separator(self, canvas: Image.Image, top: int, width_ratio: float = 0.30) -> int:
        theme = self.theme
        line_w = max(40, int(self.width * width_ratio))
        line_h = max(1, int(theme.separator_width * self.height / 1080))
        rule = Image.new(
            "RGBA",
            (line_w, line_h),
            textures.rgba(theme.separator_color, theme.separator_opacity),
        )
        canvas.alpha_composite(rule, ((self.width - line_w) // 2, top))
        return top + line_h

    # -- glass furniture -----------------------------------------------------
    def _chip(
        self,
        canvas: Image.Image,
        rendered: textsvc.RenderedText,
        top: int,
        color: str,
        opacity: float = 1.0,
        rules: bool = False,
    ) -> int:
        """Set *rendered* inside a frosted capsule, centred. Returns the bottom.

        With *rules*, a hairline runs out to either side of the capsule - the
        `— ( SURAH 001 ) —` treatment.
        """
        theme = self.theme
        pad_x = int(rendered.height * theme.chip_pad_x)
        pad_y = int(rendered.height * theme.chip_pad_y)
        chip_w = rendered.width + pad_x * 2
        chip_h = rendered.height + pad_y * 2
        left = (self.width - chip_w) // 2

        chip = textures.make_rounded_rect(
            chip_w,
            chip_h,
            chip_h * theme.chip_radius,
            fill=theme.chip_color if theme.chip_opacity > 0 else None,
            fill_opacity=theme.chip_opacity,
            border=theme.chip_border_color if theme.chip_border_opacity > 0 else None,
            border_opacity=theme.chip_border_opacity,
            border_width=max(1, int(theme.chip_border_width * self.height / 1080)),
        )
        _composite(canvas, chip, left, top)

        if rules and theme.chip_rule_width > 0:
            rule_w = int(self.width * theme.chip_rule_width)
            gap = int(self.width * theme.chip_rule_gap)
            thickness = max(1, int(theme.separator_width * self.height / 1080))
            bar = Image.new(
                "RGBA", (rule_w, thickness),
                textures.rgba(theme.chip_border_color, theme.chip_border_opacity),
            )
            mid = top + chip_h // 2 - thickness // 2
            _composite(canvas, bar, left - gap - rule_w, mid)
            _composite(canvas, bar, left + chip_w + gap, mid)

        layer = rendered.tinted(color, opacity)
        _composite(canvas, layer, (self.width - layer.width) // 2, top + pad_y)
        return top + chip_h

    def _ornament_rule(self, canvas: Image.Image, top: int) -> int:
        """The hairline-and-rosette that closes the block."""
        theme = self.theme
        if not theme.ornament_enabled:
            return top
        rule = textures.make_rule_with_ornament(
            int(self.width * theme.ornament_rule_width),
            theme.chip_border_color,
            theme.ornament_opacity,
            ornament=self._px(theme.ornament_size),
            line_width=max(1, int(theme.separator_width * self.height / 1080)),
        )
        _composite(canvas, rule, (self.width - rule.width) // 2, top)
        return top + rule.height

    def _shadow(self, canvas: Image.Image, rendered: textsvc.RenderedText, left: int, top: int) -> None:
        """A soft drop shadow, so white type keeps its edges over artwork."""
        theme = self.theme
        if not theme.text_shadow_enabled or theme.text_shadow_opacity <= 0:
            return
        radius = max(1, int(theme.text_shadow_blur * self.height / 1080))
        offset = int(theme.text_shadow_offset * self.height / 1080)
        shadow = rendered.glow(
            theme.text_shadow_color, radius, theme.text_shadow_opacity
        )
        _composite(
            canvas,
            shadow,
            left - (shadow.width - rendered.width) // 2,
            top - (shadow.height - rendered.height) // 2 + offset,
        )

    def _panel(self, canvas: Image.Image, box: tuple[int, int, int, int]) -> None:
        """The grouped-content panel the ayah sits on."""
        theme = self.theme
        left, top, right, bottom = box
        width = max(2, right - left)
        height = max(2, bottom - top)
        panel = textures.make_rounded_rect(
            width,
            height,
            self._px(theme.panel_radius),
            fill=theme.panel_color,
            fill_opacity=theme.panel_opacity,
            border=theme.panel_border_color if theme.panel_border_opacity > 0 else None,
            border_opacity=theme.panel_border_opacity,
            border_width=max(1, int(theme.panel_border_width * self.height / 1080)),
        )
        canvas.alpha_composite(panel, (left, top))

    def _badge(self, canvas: Image.Image, top: int, number: int) -> int:
        """A pill carrying the ayah number, in place of the star medallion."""
        theme = self.theme
        height = self._px(theme.badge_height)
        digits = textsvc.render_single_line(
            self.fonts.arabic_display,
            urdu_number(number),
            int(height * 0.52),
            base_rtl=True,
        )
        pad_x = int(height * theme.badge_pad_x)
        width = max(height, digits.width + pad_x * 2)

        pill = textures.make_rounded_rect(
            width, height, height / 2.0,
            fill=theme.badge_color,
            fill_opacity=theme.badge_opacity,
        )
        left = (self.width - width) // 2
        canvas.alpha_composite(pill, (left, top))

        glyph = digits.tinted(theme.badge_text_color)
        canvas.alpha_composite(
            glyph,
            ((self.width - glyph.width) // 2, top + (height - glyph.height) // 2),
        )
        return top + height

    def _medallion(self, canvas: Image.Image, top: int, number: int) -> int:
        """Eight-point star with the ayah number set inside it."""
        if not self.theme.medallion_enabled:
            return top
        if self.is_ios:
            return self._badge(canvas, top, number)

        size = self._px(self.theme.medallion_size)

        star = textures.make_medallion(size, self.theme.accent_color, self.theme.deep_color)
        canvas.alpha_composite(star, ((self.width - size) // 2, top))

        digits = textsvc.render_single_line(
            self.fonts.arabic_display, urdu_number(number), int(size * 0.42), base_rtl=True
        )
        glyph = digits.tinted(self.theme.text_color)
        canvas.alpha_composite(
            glyph,
            ((self.width - glyph.width) // 2, top + (size - glyph.height) // 2),
        )
        return top + size

    # -- persistent furniture ------------------------------------------------
    def frame_layer(self) -> Optional[Image.Image]:
        """The hairline border. Rendered once and held for the whole video."""
        if not self.theme.frame_enabled or self.is_glass:
            # Glass sets its text straight over the artwork; a border round the
            # edge would fight the picture it is sitting on.
            return None
        inset = int(min(self.width, self.height) * self.theme.frame_inset)
        if self.is_ios:
            # A rounded container rather than a bordered plate: iOS puts content
            # on rounded surfaces and never draws a decorative rule round the
            # edge of the screen.
            container = textures.make_rounded_rect(
                self.width - inset * 2,
                self.height - inset * 2,
                self._px(self.theme.panel_radius * 1.6),
                border=self.theme.panel_border_color,
                border_opacity=self.theme.panel_border_opacity * 0.55,
                border_width=max(1, int(self.theme.panel_border_width * self.height / 1080)),
            )
            canvas = self._blank()
            canvas.alpha_composite(container, (inset, inset))
            return canvas
        return textures.make_frame(
            self.width,
            self.height,
            inset,
            self.theme.accent_color,
            self.theme.frame_opacity,
            line_width=max(1, int(self.theme.frame_line_width * self.height / 1080)),
            corners=self.theme.corner_ornament,
        )

    # -- cards ---------------------------------------------------------------
    def _glass_title_card(self) -> Image.Image:
        """The title layout: chip, the surah name large, chip, Latin, rosette.

            --- ( SURAH 001 ) ---
                  الفاتحة
              ( سورة الفاتحة )
              Surah Al-Fatihah
                --- * ---
        """
        canvas = self._blank()
        theme = self.theme
        spec = self.config.thumbnail

        size = self._px(theme.size_surah_label * 0.80)
        label = textsvc.render_single_line(
            self.fonts.latin,
            f"SURAH {self.surah.surah_number:03d}",
            size,
            base_rtl=False,
            tracking=size * 0.24,
        )
        display = textsvc.fit_text(
            self.fonts.arabic_display,
            spec.arabic_text or self.surah.name_arabic,
            self.content_width,
            self._px(0.34),
            self._px(theme.size_intro_arabic),
            self._px(theme.size_intro_arabic * 0.45),
            1.25,
            max_lines=1,
        )
        name_urdu = textsvc.render_single_line(
            self.fonts.urdu,
            spec.urdu_text or self.surah.name_urdu,
            self._px(theme.size_intro_latin * 0.86),
            base_rtl=True,
        )
        name_latin = textsvc.render_single_line(
            self.fonts.latin,
            spec.latin_text or self.surah.name_english,
            self._px(theme.size_intro_latin),
            base_rtl=False,
            tracking=self._px(theme.size_intro_latin) * 0.03,
        )

        gap = self._px(0.030)
        chip_h = lambda r: r.height + 2 * int(r.height * theme.chip_pad_y)  # noqa: E731
        block = (
            chip_h(label) + gap + display.rendered.height + gap
            + chip_h(name_urdu) + self._px(0.018) + name_latin.height
            + gap + self._px(theme.ornament_size)
        )
        top = (self.height - block) // 2

        top = self._chip(canvas, label, top, theme.text_color, rules=True)
        top = self._paste_centered(
            canvas, display.rendered, top + gap, theme.text_color, glow=True
        )
        top = self._chip(canvas, name_urdu, top + gap, theme.text_color, 0.95)
        top = self._paste_centered(
            canvas, name_latin, top + self._px(0.018), theme.text_color, 0.95
        )
        self._ornament_rule(canvas, top + gap)
        return canvas

    def intro_card(self) -> Image.Image:
        if self.is_glass:
            return self._glass_title_card()

        canvas = self._blank()
        theme = self.theme

        name_urdu = textsvc.fit_text(
            self.fonts.urdu,
            self.surah.name_urdu,
            self.content_width,
            self._px(0.30),
            self._px(theme.size_intro_arabic),
            self._px(theme.size_intro_arabic * 0.45),
            theme.line_spacing_urdu,
            max_lines=1,
        )
        name_latin = textsvc.render_single_line(
            self.fonts.latin,
            self.config.thumbnail.latin_text or self.surah.name_english,
            self._px(theme.size_intro_latin),
            base_rtl=False,
            tracking=self._px(theme.size_intro_latin) * 0.09,
        )
        detail = textsvc.render_single_line(
            self.fonts.urdu,
            f"{urdu_number(self.surah.ayah_count)} آیات",
            self._px(theme.size_credit * 1.25),
            base_rtl=True,
        )

        gap = self._px(0.035)
        block_h = (
            name_urdu.rendered.height + gap + name_latin.height + gap
            + max(4, self._px(0.014)) + gap + detail.height
        )
        top = (self.height - block_h) // 2

        top = self._paste_centered(canvas, name_urdu.rendered, top, theme.text_color, glow=True)
        top = self._paste_centered(canvas, name_latin, top + gap, theme.accent_color, 0.92)
        top = self._divider(canvas, top + gap, width_ratio=theme.divider_width_ratio * 1.3)
        self._paste_centered(canvas, detail, top + gap, theme.muted_color, 0.9)
        return canvas

    def _header(self, canvas: Image.Image, ayah: Ayah, section_label: str) -> int:
        """Shared header: surah name, medallion, section label. Returns body top."""
        theme = self.theme
        top = self.margin_y

        if self.is_glass:
            # The design's top line: a hairline, a frosted chip carrying
            # "SURAH 001", a hairline. The ayah number goes in a second chip
            # under it, so the two read as one stack.
            size = self._px(theme.size_surah_label * 0.80)
            label = textsvc.render_single_line(
                self.fonts.latin,
                f"SURAH {self.surah.surah_number:03d}",
                size,
                base_rtl=False,
                tracking=size * 0.24,
            )
            top = self._chip(canvas, label, top, theme.text_color, rules=True)

            number = textsvc.render_single_line(
                self.fonts.urdu, section_label, self._px(theme.size_ayah_label * 0.86)
            )
            top = self._chip(
                canvas, number, top + self._px(0.026), theme.text_color, 0.92
            )
            return top + self._px(0.050)

        if self.is_ios:
            # A navigation-bar title: the surah in Latin small caps, tracked
            # out, in the secondary label colour, with the badge beneath it.
            size = self._px(theme.size_surah_label * 0.92)
            title = textsvc.render_single_line(
                self.fonts.latin,
                (self.config.thumbnail.latin_text or self.surah.name_english).upper(),
                size,
                base_rtl=False,
                tracking=size * 0.18,
            )
            top = self._paste_centered(
                canvas, title, top, theme.text_secondary_color, 0.78
            )
            top = self._badge(canvas, top + self._px(0.030), ayah.number)
            return top + self._px(0.046)

        header = textsvc.render_single_line(
            self.fonts.arabic_display, SURAH_HEADER_ARABIC, self._px(theme.size_surah_label)
        )
        top = self._paste_centered(canvas, header, top, theme.accent_color, 0.88)

        top = self._medallion(canvas, top + self._px(0.024), ayah.number)

        label = textsvc.render_single_line(
            self.fonts.urdu, section_label, self._px(theme.size_ayah_label)
        )
        top = self._paste_centered(canvas, label, top + self._px(0.020), theme.text_secondary_color, 0.85)
        return top + self._px(0.040)

    def _body_top(self, ayah: Ayah) -> int:
        """Where the ayah's text may start, just under the header.

        The header is measured on a scratch canvas: only its height matters,
        and drawing it twice would double its glow.
        """
        return self._header(
            self._blank(), ayah, f"{LABEL_AYAH_URDU} {urdu_number(ayah.number)}"
        )

    def _divider_height(self) -> int:
        """Room kept below the text for whatever closes the card.

        In the iOS style nothing is drawn under the text, so the space the
        classic divider would have taken is given back to the ayah. The glass
        style closes with a rosette, which needs its own room.
        """
        if self.is_ios:
            return 0
        if self.is_glass:
            return self._px(self.theme.ornament_size)
        return max(4, self._px(0.014))

    def _body_height(self, ayah: Ayah) -> int:
        """How tall the text may be on this ayah's card."""
        bottom_limit = (
            self.height - self.margin_y - self._divider_height() - self._px(0.030)
        )
        return max(self._px(0.12), bottom_limit - self._body_top(ayah))

    # -- pagination ----------------------------------------------------------
    def paginate(self, ayah: Ayah) -> list[AyahPage]:
        """How many screens this ayah needs, and which words go on each.

        One screen unless the ayah cannot be laid out at
        `theme.size_arabic_page_min`, which is the smallest size still worth
        reading. Measured with the real font against the real card, so the
        answer matches what the renderer will actually draw.
        """
        cached = self._pages.get(ayah.number)
        if cached is not None:
            return cached

        floor = self._px(self.theme.size_arabic_page_min)
        words = recited_words(ayah.arabic)
        if floor <= 0 or len(words) < 2:
            pages = [single_page(ayah.number, ayah.arabic, ayah.word_count)]
        else:
            available = self._body_height(ayah)

            def fits(text: str) -> bool:
                fitted = textsvc.fit_text(
                    self.fonts.arabic,
                    text,
                    self.content_width,
                    available,
                    self._px(self.theme.size_arabic_max),
                    floor,
                    self.theme.line_spacing_arabic,
                    max_lines=self.theme.max_lines_arabic,
                )
                # The line budget counts too. fit_text enforces it while it is
                # searching, but falls back to whatever fits the box once it
                # reaches the floor - so without this a page could "fit" by
                # wrapping to more lines than the theme allows.
                return (
                    fitted.font_size >= floor
                    and len(fitted.lines) <= self.theme.max_lines_arabic
                    and fitted.rendered.height <= available
                    and fitted.rendered.width <= self.content_width
                )

            pages = split_pages(ayah.number, words, fits)
            if len(pages) > 1:
                self.log.info(
                    "Ayah %d does not fit one screen at %dpx; splitting it over "
                    "%d screens", ayah.number, floor, len(pages),
                )

        self._pages[ayah.number] = pages
        return pages

    def _arabic_layout(
        self, ayah: Ayah, page: Optional[AyahPage] = None
    ) -> "ArabicLayout":
        """Where this page's text sits on its card, and where each word sits.

        Worked out once and cached, because `arabic_card` and every
        `highlight_layer` for the same page must agree on the position to the
        pixel - the highlight is drawn over the card's own glyphs.

        *page* is a stretch of the ayah; without one the whole ayah is laid out,
        which is what an ayah that fits on a single screen gets.
        """
        if page is None:
            page = single_page(ayah.number, ayah.arabic, ayah.word_count)
        key = (ayah.number, page.index)
        cached = self._arabic_layouts.get(key)
        if cached is not None:
            return cached

        theme = self.theme
        body_top = self._body_top(ayah)
        divider_h = self._divider_height()
        available = self._body_height(ayah)

        fitted = textsvc.fit_text(
            self.fonts.arabic,
            page.text,
            self.content_width,
            available,
            self._px(theme.size_arabic_max),
            self._px(theme.size_arabic_min),
            theme.line_spacing_arabic,
            max_lines=theme.max_lines_arabic,
        )
        self.log.debug(
            "%s fitted at %dpx over %d line(s)",
            page.label().capitalize(), fitted.font_size, len(fitted.lines),
        )

        # Re-rasterise the identical lines to get the per-word masks. Same
        # handle, same line height, same alignment - so the ink is identical.
        words: list[textsvc.WordMask] = []
        if self.config.content.word_highlight:
            handle = textsvc.font_handle(self.fonts.arabic, fitted.font_size)
            _, words = textsvc.render_lines_with_words(
                handle, fitted.lines, fitted.line_height
            )

        # Optically centre the ayah in the space left below the header.
        top = body_top + max(0, (available - fitted.rendered.height) // 2)
        left = (self.width - fitted.rendered.width) // 2

        layout = ArabicLayout(
            fitted=fitted,
            words=words,
            left=left,
            top=top,
            available=available,
            divider_h=divider_h,
        )
        self._arabic_layouts[key] = layout
        return layout

    def arabic_card(self, ayah: Ayah, page: Optional[AyahPage] = None) -> Image.Image:
        canvas = self._blank()
        theme = self.theme
        layout = self._arabic_layout(ayah, page)

        # The panel goes down before anything else, so the text and the badge
        # both sit on top of it.
        if self.is_ios and theme.panel_opacity > 0:
            self._panel(canvas, self._panel_box(layout))

        self._header(canvas, ayah, f"{LABEL_AYAH_URDU} {urdu_number(ayah.number)}")

        bottom = self._paste_centered(
            canvas, layout.fitted.rendered, layout.top, theme.text_color, glow=True
        )
        if self.is_glass:
            self._ornament_rule(canvas, min(
                bottom + self._px(0.052),
                self.height - self.margin_y - self._px(theme.ornament_size),
            ))
        elif not self.is_ios:
            self._divider(
                canvas,
                min(bottom + self._px(0.045),
                    self.height - self.margin_y - layout.divider_h),
            )
        return canvas

    def _panel_box(self, layout: "ArabicLayout") -> tuple[int, int, int, int]:
        """The rounded surface behind an ayah.

        Sized from the text rather than fixed, so a one-line ayah gets a compact
        card and the seven-word one gets a tall card - which is how a grouped
        list behaves - while the left and right edges stay put so the panel does
        not jump about between ayahs.
        """
        theme = self.theme
        pad_x = int(self.width * theme.panel_pad_x)
        pad_y = int(self.height * theme.panel_pad_y)
        left = max(0, self.margin_x - pad_x)
        right = min(self.width, self.width - self.margin_x + pad_x)
        top = max(0, layout.top - pad_y)
        bottom = min(self.height, layout.top + layout.fitted.rendered.height + pad_y)
        return (left, top, right, bottom)

    def _restore_neighbours(
        self,
        canvas: Image.Image,
        layout: "ArabicLayout",
        word: textsvc.WordMask,
        box: tuple[int, int, int, int],
    ) -> None:
        """Redraw whatever text the capsule just covered.

        The capsule is drawn over the finished card, and a solid one erases
        anything of its neighbours that falls inside it - on a tightly set ayah
        that means the harakat of the line above lose their tops. Every word
        except the lit one is drawn back, clipped to the capsule, so the
        capsule ends up behind its neighbours while still covering its own
        word.

        Clipping to the capsule matters: redrawing a glyph that is already on
        the card would composite its antialiased edge twice and thicken it.
        """
        left, top, right, bottom = box
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return

        patch = Image.new("L", (width, height), 0)
        touched = False
        for other in layout.words:
            if other.index == word.index:
                continue
            ox = layout.left + other.x - left
            oy = layout.top + other.y - top
            if ox >= width or oy >= height:
                continue
            if ox + other.width <= 0 or oy + other.height <= 0:
                continue
            region = patch.crop((ox, oy, ox + other.width, oy + other.height))
            patch.paste(ImageChops.lighter(region, other.mask), (ox, oy))
            touched = True

        if not touched:
            return
        layer = Image.new("RGBA", patch.size, self.theme.text_color)
        layer.putalpha(patch)
        _composite(canvas, layer, left, top)

    def highlight_layer(
        self, ayah: Ayah, word_index: int, page: Optional[AyahPage] = None
    ) -> Optional[Image.Image]:
        """A full-frame layer lighting one word of *ayah*, or None if there
        is no such word.

        This is a separate layer rather than a second copy of the card so that
        the card keeps its own fade schedule: the highlight simply switches on
        and off over the top of it while the card is at full opacity.

        *word_index* counts from the start of the AYAH. On a paged ayah the card
        holds only some of those words, so it is converted to the page's own
        numbering here - the layout knows nothing of the words before it.
        """
        layout = self._arabic_layout(ayah, page)
        if page is not None:
            if not page.contains(word_index):
                return None
            word_index = page.local_index(word_index)
        word = layout.word(word_index)
        if word is None:
            return None

        theme = self.theme
        canvas = self._blank()
        # Where this word's ink sits on the finished frame.
        x = layout.left + word.x
        y = layout.top + word.y

        if theme.highlight_pill_enabled and theme.highlight_pill_opacity > 0:
            pad_x = int(word.height * theme.highlight_pill_pad_x)
            pad_y = int(word.height * theme.highlight_pill_pad_y)
            pill_w = word.width + pad_x * 2
            pill_h = word.height + pad_y * 2
            pill = textures.make_rounded_rect(
                pill_w,
                pill_h,
                pill_h * theme.highlight_pill_radius,
                fill=theme.highlight_pill_color,
                fill_opacity=theme.highlight_pill_opacity,
            )
            box = (x - pad_x, y - pad_y, x - pad_x + pill_w, y - pad_y + pill_h)
            _composite(canvas, pill, box[0], box[1])
            self._restore_neighbours(canvas, layout, word, box)

        rendered = textsvc.RenderedText(
            mask=word.mask,
            font_size=layout.fitted.font_size,
            line_count=1,
            lines=[word.text],
        )
        if theme.highlight_glow_enabled and theme.highlight_glow_opacity > 0:
            radius = max(2, int(theme.highlight_glow_radius * self.height / 1080))
            glow = rendered.glow(
                theme.highlight_color, radius, theme.highlight_glow_opacity
            )
            canvas.alpha_composite(
                glow,
                (x - (glow.width - word.width) // 2, y - (glow.height - word.height) // 2),
            )

        canvas.alpha_composite(rendered.tinted(theme.highlight_color), (x, y))
        return canvas

    def urdu_card(self, ayah: Ayah) -> Image.Image:
        canvas = self._blank()
        theme = self.theme
        body_top = self._header(canvas, ayah, LABEL_URDU_TRANSLATION)

        divider_h = max(4, self._px(0.014))
        bottom_limit = self.height - self.margin_y - divider_h - self._px(0.030)
        available = max(self._px(0.12), bottom_limit - body_top)

        fitted = textsvc.fit_text(
            self.fonts.urdu,
            ayah.urdu_translation,
            self.content_width,
            available,
            self._px(theme.size_urdu_max),
            self._px(theme.size_urdu_min),
            theme.line_spacing_urdu,
            max_lines=theme.max_lines_urdu,
        )
        self.log.debug(
            "Ayah %d Urdu fitted at %dpx over %d line(s)",
            ayah.number, fitted.font_size, len(fitted.lines),
        )

        top = body_top + max(0, (available - fitted.rendered.height) // 2)
        bottom = self._paste_centered(canvas, fitted.rendered, top, theme.urdu_text_color, glow=True)
        self._divider(canvas, min(bottom + self._px(0.045), self.height - self.margin_y - divider_h))
        return canvas

    def outro_card(self) -> Image.Image:
        canvas = self._blank()
        theme = self.theme

        if not self.config.content.outro_card:
            # Deliberately blank. The segment still occupies its time, so the
            # video fades out on the moving backdrop instead of cutting the
            # moment the recitation ends.
            return canvas

        closing = textsvc.fit_text(
            self.fonts.urdu,
            OUTRO_ARABIC,
            self.content_width,
            self._px(0.24),
            self._px(theme.size_outro_arabic),
            self._px(theme.size_outro_arabic * 0.45),
            theme.line_spacing_urdu,
            max_lines=2,
        )

        # Each credit carries its own colour and opacity. These are attribution
        # for a published video, so they stay legible; the channel name is the
        # only line allowed the gold accent, keeping the outro a credit roll
        # rather than an advert.
        credits: list[tuple[textsvc.RenderedText, str, float]] = []
        credit_size = self._px(theme.size_credit)

        def add_credit(
            text: str,
            color: str,
            opacity: float = 0.78,
            size: int = credit_size,
            tracking: float = 0.0,
        ) -> None:
            credits.append(
                (
                    textsvc.render_single_line(
                        self.fonts.latin, text, size, base_rtl=False, tracking=tracking
                    ),
                    color,
                    opacity,
                )
            )

        reciter = self.config.reciter
        if reciter.is_configured:
            add_credit(f"Recitation:  {reciter.name}", theme.text_color, 0.92)

        # Only credit the translation if it is actually on screen, and the
        # narrator only if the narration is actually heard.
        shows_urdu = self.config.content.urdu_translation
        translator = self.config.translation_credit.urdu_translator
        if translator and shows_urdu:
            add_credit(f"Urdu translation:  {translator}", theme.text_secondary_color)

        narrator = self.config.translation_credit.urdu_narrator
        if (
            narrator
            and narrator.upper() != "NARRATOR_NAME"
            and shows_urdu
            and self.config.audio.urdu_narration
        ):
            add_credit(f"Urdu narration:  {narrator}", theme.text_secondary_color)

        if theme.channel_name:
            add_credit(
                theme.channel_name, theme.accent_color, 0.95,
                size=int(credit_size * 1.35), tracking=credit_size * 0.10,
            )
        if theme.channel_tagline:
            add_credit(theme.channel_tagline, theme.text_secondary_color, 0.72)

        logo = self._load_logo()

        gap = self._px(0.030)
        credit_gap = self._px(0.014)
        block_h = closing.rendered.height + gap + max(4, self._px(0.014))
        if logo is not None:
            block_h += gap + logo.height
        if credits:
            block_h += (
                gap
                + sum(rendered.height for rendered, _, _ in credits)
                + credit_gap * (len(credits) - 1)
            )

        top = (self.height - block_h) // 2
        top = self._paste_centered(canvas, closing.rendered, top, theme.text_color, glow=True)
        top = self._divider(canvas, top + gap, width_ratio=theme.divider_width_ratio * 1.3)

        if logo is not None:
            top += gap
            canvas.alpha_composite(logo, ((self.width - logo.width) // 2, top))
            top += logo.height

        if credits:
            top += gap
            for rendered, color, opacity in credits:
                top = self._paste_centered(canvas, rendered, top, color, opacity) + credit_gap

        return canvas

    def _load_logo(self) -> Optional[Image.Image]:
        theme = self.theme
        if not theme.logo_enabled or not theme.logo_file:
            return None
        path = self.config.branding_dir / theme.logo_file
        if not path.is_file():
            return None
        try:
            logo = Image.open(path).convert("RGBA")
        except (OSError, ValueError) as exc:
            self.log.warning("Could not read the logo %s: %s", path.name, exc)
            return None

        target_h = self._px(theme.logo_height)
        target_w = max(1, int(logo.width * target_h / max(1, logo.height)))
        logo = logo.resize((target_w, target_h), Image.LANCZOS)
        if theme.logo_opacity < 0.999:
            alpha = logo.getchannel("A").point(lambda v: int(v * theme.logo_opacity))
            logo.putalpha(alpha)
        return logo
