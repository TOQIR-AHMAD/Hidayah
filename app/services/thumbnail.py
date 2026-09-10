"""1280x720 YouTube thumbnail generation.

Same palette, same lattice, same gold hairlines as the video, so a viewer who
clicks the thumbnail lands somewhere that looks like the same production. No
clickbait: the Arabic name of the surah is the subject, and everything else
supports it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image

from app.config import Config
from app.models.surah import Surah
from app.rendering import textures
from app.rendering.compositions import Fonts, urdu_number
from app.services import text as textsvc
from app.utils.logging import get_logger


def build(config: Config, surah: Surah) -> Image.Image:
    theme = config.theme
    spec = config.thumbnail
    width, height = spec.width, spec.height
    fonts = Fonts.resolve(config)

    def px(fraction: float) -> int:
        return max(8, int(round(height * fraction)))

    if theme.style == "glass":
        # The glass title card IS the thumbnail design, so it is composed once
        # in CardBuilder and used for both. Two implementations of the same
        # layout drift apart the first time either is adjusted.
        return _glass_thumbnail(config, surah, width, height)

    # --- backdrop -----------------------------------------------------------
    is_ios = theme.style == "ios"
    generated = config.background.generated
    # The lattice is lifted for the thumbnail, which is seen small and static
    # and can carry more texture than the video. The iOS treatment wants the
    # opposite: a near-flat ground, so it is pulled back instead.
    pattern_boost = 0.55 if is_ios else 1.5
    canvas = textures.make_backdrop(
        width,
        height,
        theme.background_color,
        theme.deep_color,
        theme.accent_color,
        pattern_scale=max(48, int(generated.pattern_scale * height / 1080)),
        pattern_opacity=generated.pattern_opacity * pattern_boost,
        glow_opacity=generated.glow_opacity * (0.55 if is_ios else 1.0),
        grain=generated.grain,
        appearance=theme.appearance,
    ).convert("RGBA")

    if is_ios and theme.appearance == "dark":
        # Bring the generated art down towards the video's own ground. Left
        # bright, the thumbnail reads as a different design from the film it
        # is advertising - and the accent being systemBlue tints all of it.
        # On a pale ground there is nothing to bring down.
        shade = Image.new("RGBA", (width, height),
                          (0, 0, 0, int(min(1.0, config.background.dim * 1.4) * 255)))
        canvas = Image.alpha_composite(canvas, shade)

    # A user background, if there is one, replaces the generated art.
    user_background = _user_background(config, width, height)
    if user_background is not None:
        canvas = user_background

    # --- frame --------------------------------------------------------------
    if theme.frame_enabled:
        inset = int(min(width, height) * theme.frame_inset)
        if is_ios:
            # The same rounded container the video uses, so the thumbnail and
            # the opening frame are recognisably the same design.
            frame = textures.make_rounded_rect(
                width - inset * 2, height - inset * 2,
                int(height * theme.panel_radius * 1.6),
                border=theme.panel_border_color,
                border_opacity=min(1.0, theme.panel_border_opacity * 0.8),
                border_width=max(1, int(theme.panel_border_width * height / 1080 * 1.5)),
            )
            canvas.alpha_composite(frame, (inset, inset))
        else:
            frame = textures.make_frame(
                width, height, inset,
                theme.accent_color,
                min(1.0, theme.frame_opacity * 1.35),
                line_width=max(1, int(theme.frame_line_width * height / 1080 * 1.5)),
                corners=theme.corner_ornament,
            )
            canvas.alpha_composite(frame)

    margin_y = int(height * 0.10)
    content_width = int(width * (1 - 2 * theme.margin_x))

    # --- top label ----------------------------------------------------------
    top = margin_y
    surah_label = textsvc.render_single_line(
        fonts.latin,
        f"SURAH {surah.surah_number:03d}",
        px(0.042),
        base_rtl=False,
        tracking=px(0.042) * 0.22,
    )
    _paste(canvas, surah_label, top, theme.accent_color, 0.80)
    top += surah_label.height + px(0.030)

    # --- the Arabic name, the subject of the image -------------------------
    arabic_text = spec.arabic_text or surah.name_arabic
    arabic = textsvc.fit_text(
        fonts.arabic_display,
        arabic_text,
        content_width,
        px(0.40),
        px(spec.size_arabic),
        px(spec.size_arabic * 0.5),
        1.25,
        max_lines=1,
    )
    if theme.glow_enabled and theme.appearance == "dark":
        glow = arabic.rendered.glow(theme.accent_color, max(8, px(0.028)), 0.45)
        canvas.alpha_composite(
            glow,
            ((width - glow.width) // 2, top - (glow.height - arabic.rendered.height) // 2),
        )
    _paste(canvas, arabic.rendered, top, theme.text_color)
    top += arabic.rendered.height + px(0.028)

    # --- divider ------------------------------------------------------------
    divider_w = int(width * 0.34)
    if is_ios:
        divider = Image.new(
            "RGBA",
            (divider_w, max(1, int(theme.separator_width * height / 1080))),
            textures.rgba(theme.separator_color, theme.separator_opacity),
        )
    else:
        divider = textures.make_divider(
            divider_w, max(4, px(0.018)), theme.accent_color, theme.divider_opacity + 0.2
        )
    canvas.alpha_composite(divider, ((width - divider_w) // 2, top))
    top += divider.height + px(0.030)

    # --- Urdu name ----------------------------------------------------------
    urdu_text = spec.urdu_text or surah.name_urdu
    urdu = textsvc.fit_text(
        fonts.urdu, urdu_text, content_width, px(0.16),
        px(spec.size_urdu), px(spec.size_urdu * 0.5), 1.5, max_lines=1,
    )
    _paste(canvas, urdu.rendered, top, theme.urdu_text_color)
    top += urdu.rendered.height + px(0.024)

    # --- Latin name ---------------------------------------------------------
    latin = textsvc.render_single_line(
        fonts.latin,
        spec.latin_text or f"Surah {surah.name_english}",
        px(spec.size_latin),
        base_rtl=False,
        tracking=px(spec.size_latin) * 0.06,
    )
    _paste(canvas, latin, top, theme.accent_color, 0.95)
    top += latin.height

    # --- bottom strap -------------------------------------------------------
    if spec.subtitle_text:
        strap = textsvc.render_single_line(fonts.urdu, spec.subtitle_text, px(spec.size_subtitle))
        strap_top = height - margin_y - strap.height
        if strap_top > top + px(0.02):
            _paste(canvas, strap, strap_top, theme.text_secondary_color, 0.9)

    # --- ayah count badge ---------------------------------------------------
    badge = textsvc.render_single_line(
        fonts.urdu, f"{urdu_number(surah.ayah_count)} آیات", px(0.040)
    )
    badge_layer = badge.tinted(theme.muted_color, 0.85)
    canvas.alpha_composite(
        badge_layer,
        (int(width * theme.margin_x * 0.9), height - margin_y - badge_layer.height),
    )

    # --- logo ---------------------------------------------------------------
    logo = _load_logo(config, px(0.11))
    if logo is not None:
        canvas.alpha_composite(
            logo,
            (
                width - int(width * theme.margin_x * 0.9) - logo.width,
                height - margin_y - logo.height,
            ),
        )

    return canvas.convert("RGB")


def _glass_thumbnail(config: Config, surah: Surah, width: int, height: int) -> Image.Image:
    """Artwork, then the same title card the video opens on.

    Falls back to the generated backdrop when no artwork has been supplied, so
    `thumbnail` still produces something rather than failing - but this style
    is designed around a background image and looks bare without one.
    """
    from app.rendering.compositions import CardBuilder

    theme = config.theme
    canvas = _user_background(config, width, height)
    if canvas is None:
        generated = config.background.generated
        canvas = textures.make_backdrop(
            width, height,
            theme.background_color, theme.deep_color, theme.accent_color,
            pattern_scale=max(48, int(generated.pattern_scale * height / 1080)),
            pattern_opacity=generated.pattern_opacity,
            glow_opacity=generated.glow_opacity,
            grain=generated.grain,
            appearance=theme.appearance,
        ).convert("RGBA")
        get_logger().warning(
            "theme.style is 'glass' but no artwork was found in %s - the "
            "thumbnail is falling back to the generated backdrop.",
            config.backgrounds_dir.name,
        )

    canvas.alpha_composite(
        CardBuilder(config, surah, width, height).intro_card()
    )

    logo = _load_logo(config, int(height * 0.11))
    if logo is not None:
        margin = int(width * theme.margin_x * 0.9)
        canvas.alpha_composite(
            logo,
            (width - margin - logo.width, height - int(height * 0.10) - logo.height),
        )
    return canvas.convert("RGB")


def _paste(
    canvas: Image.Image,
    rendered: textsvc.RenderedText,
    top: int,
    color: str,
    opacity: float = 1.0,
) -> None:
    layer = rendered.tinted(color, opacity)
    canvas.alpha_composite(layer, ((canvas.width - layer.width) // 2, top))


def _user_background(config: Config, width: int, height: int) -> Optional[Image.Image]:
    """Use the still background the video uses, cropped to 16:9, if there is one."""
    from app.utils.files import find_first_media, is_image

    setting = config.background.source.strip().lower()
    if setting == "generated":
        return None

    candidate = None
    if setting not in ("auto", "generated", ""):
        path = config.backgrounds_dir / config.background.source
        candidate = path if path.is_file() else None
    else:
        candidate = find_first_media(config.backgrounds_dir)

    if candidate is None or not is_image(candidate):
        return None

    try:
        image = Image.open(candidate).convert("RGBA")
    except (OSError, ValueError) as exc:
        get_logger().warning("Could not use %s for the thumbnail: %s", candidate.name, exc)
        return None

    # Cover-crop to 16:9 without distorting.
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        # round, not truncate: truncating can leave the scaled image a pixel
        # short of the target, which crops in a black edge along one side.
        (max(width, round(image.width * scale)), max(height, round(image.height * scale))),
        Image.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    cropped = resized.crop((left, top, left + width, top + height))

    # Match the video's dim so the thumbnail and the opening frame agree.
    dim = config.background.dim
    if dim > 0:
        shade = Image.new("RGBA", (width, height), (0, 0, 0, int(dim * 255)))
        cropped = Image.alpha_composite(cropped, shade)
    return cropped


def _load_logo(config: Config, target_height: int) -> Optional[Image.Image]:
    theme = config.theme
    if not theme.logo_enabled or not theme.logo_file:
        return None
    path = config.branding_dir / theme.logo_file
    if not path.is_file():
        return None
    try:
        logo = Image.open(path).convert("RGBA")
    except (OSError, ValueError):
        return None
    target_w = max(1, int(logo.width * target_height / max(1, logo.height)))
    return logo.resize((target_w, target_height), Image.LANCZOS)


def generate(config: Config, surah: Surah, destination: Optional[Path] = None) -> Path:
    destination = destination or config.output_file("jpg")
    image = build(config, surah)
    textures.save_jpeg(image, destination, config.thumbnail.quality)
    get_logger().info(
        "Wrote %s (%dx%d)", destination.name, config.thumbnail.width, config.thumbnail.height
    )
    return destination
