"""Procedurally generated artwork: backdrop, geometric lattice, light and dust.

Every texture here is drawn with Pillow, so the project renders a complete,
good-looking video with no third-party imagery at all. Anything the user drops
into assets/backgrounds/ takes precedence over the generated backdrop.

All shapes are drawn supersampled and then downscaled, which is what keeps the
thin gold hairlines smooth instead of jagged.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Optional

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SUPERSAMPLE = 4


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = value.strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgba(value: str, alpha: float) -> tuple[int, int, int, int]:
    r, g, b = hex_to_rgb(value)
    return (r, g, b, max(0, min(255, int(round(alpha * 255)))))


def mix(color_a: str, color_b: str, amount: float) -> tuple[int, int, int]:
    a, b = hex_to_rgb(color_a), hex_to_rgb(color_b)
    return tuple(int(round(a[i] + (b[i] - a[i]) * amount)) for i in range(3))  # type: ignore[return-value]


def mix_hex(color_a: str, color_b: str, amount: float) -> str:
    r, g, b = mix(color_a, color_b, amount)
    return f"#{r:02X}{g:02X}{b:02X}"


# ---------------------------------------------------------------------------
# Geometry primitives
# ---------------------------------------------------------------------------

def star_points(
    cx: float, cy: float, outer: float, inner: float, points: int = 8, rotation: float = 0.0
) -> list[tuple[float, float]]:
    """Vertices of an n-pointed star (the classic Islamic khatam when n = 8)."""
    vertices: list[tuple[float, float]] = []
    step = math.pi / points
    for index in range(points * 2):
        radius = outer if index % 2 == 0 else inner
        angle = rotation + index * step
        vertices.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return vertices


def polygon_points(
    cx: float, cy: float, radius: float, sides: int, rotation: float = 0.0
) -> list[tuple[float, float]]:
    return [
        (
            cx + radius * math.cos(rotation + i * 2 * math.pi / sides),
            cy + radius * math.sin(rotation + i * 2 * math.pi / sides),
        )
        for i in range(sides)
    ]


def _draw_motif(draw: ImageDraw.ImageDraw, cx: float, cy: float, unit: float,
                color: tuple[int, int, int, int], width: int) -> None:
    """One lattice node: an eight-point star inside an octagon."""
    draw.polygon(star_points(cx, cy, unit * 0.46, unit * 0.20, 8, math.pi / 8),
                 outline=color, width=width)
    draw.polygon(star_points(cx, cy, unit * 0.27, unit * 0.12, 8, 0.0),
                 outline=color, width=width)
    draw.polygon(polygon_points(cx, cy, unit * 0.50, 8, math.pi / 8),
                 outline=color, width=max(1, width - 1))


def make_pattern_tile(
    tile: int, color: str, opacity: float, line_width: int = 2
) -> Image.Image:
    """A seamlessly tileable Islamic lattice tile, RGBA."""
    scale = SUPERSAMPLE
    size = tile * scale
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    ink = rgba(color, opacity)
    width = max(1, line_width * scale // 2)
    unit = float(size)

    # Motifs at the centre and at all four corners keep the tile seamless.
    for cx, cy in ((0, 0), (size, 0), (0, size), (size, size), (size / 2, size / 2)):
        _draw_motif(draw, cx, cy, unit, ink, width)

    # Diagonal lattice threads linking the nodes.
    thin = max(1, width // 2)
    draw.line([(0, 0), (size, size)], fill=ink, width=thin)
    draw.line([(size, 0), (0, size)], fill=ink, width=thin)

    return canvas.resize((tile, tile), Image.LANCZOS)


def tile_pattern(tile_image: Image.Image, width: int, height: int) -> Image.Image:
    """Repeat *tile_image* until it covers width x height."""
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    tw, th = tile_image.size
    for y in range(0, height, th):
        for x in range(0, width, tw):
            canvas.alpha_composite(tile_image, (x, y))
    return canvas


# ---------------------------------------------------------------------------
# Gradients
# ---------------------------------------------------------------------------

def radial_gradient(
    width: int,
    height: int,
    center: tuple[float, float],
    radius: float,
    inner: float = 1.0,
    outer: float = 0.0,
    falloff: float = 1.6,
) -> Image.Image:
    """An 'L' mask holding a smooth radial falloff.

    Built small and then upscaled: a quarter-scale gradient resampled bicubically
    is indistinguishable from a full-resolution one and is an order of magnitude
    faster to compute in Python.
    """
    small_w = max(16, width // 4)
    small_h = max(16, height // 4)
    scale_x = small_w / width
    scale_y = small_h / height
    cx, cy = center[0] * scale_x, center[1] * scale_y
    rx, ry = max(1.0, radius * scale_x), max(1.0, radius * scale_y)

    pixels = bytearray(small_w * small_h)
    for y in range(small_h):
        dy = (y - cy) / ry
        row = y * small_w
        for x in range(small_w):
            dx = (x - cx) / rx
            distance = math.sqrt(dx * dx + dy * dy)
            value = 1.0 - min(1.0, distance) ** falloff
            pixels[row + x] = int(max(0.0, min(1.0, outer + (inner - outer) * value)) * 255)

    small = Image.frombytes("L", (small_w, small_h), bytes(pixels))
    return small.resize((width, height), Image.BICUBIC)


def linear_gradient(width: int, height: int, top: float, bottom: float) -> Image.Image:
    column = Image.new("L", (1, height))
    for y in range(height):
        t = y / max(1, height - 1)
        column.putpixel((0, y), int(max(0.0, min(1.0, top + (bottom - top) * t)) * 255))
    return column.resize((width, height), Image.BICUBIC)


def add_grain(image: Image.Image, amount: float, seed: int = 7) -> Image.Image:
    """Fine film grain. Generated at 1/2 scale and upsampled to look organic."""
    if amount <= 0:
        return image
    rng = random.Random(seed)
    w, h = image.size
    sw, sh = max(2, w // 2), max(2, h // 2)
    noise = Image.frombytes(
        "L", (sw, sh), bytes(rng.randrange(96, 160) for _ in range(sw * sh))
    ).resize((w, h), Image.BICUBIC)

    strength = max(0.0, min(1.0, amount))
    overlay = noise.point(lambda v: int(128 + (v - 128) * strength * 2))
    layer = Image.merge("RGB", (overlay, overlay, overlay))
    return Image.blend(image.convert("RGB"), layer, strength * 0.5).convert("RGB")


# ---------------------------------------------------------------------------
# Composite textures
# ---------------------------------------------------------------------------

def arch_outline(
    cx: float, base_y: float, width: float, height: float, samples: int = 64
) -> list[tuple[float, float]]:
    """Outline of a two-centre pointed arch - the mihrab silhouette.

    Each side is struck from a centre offset towards the opposite springing
    point, which is how a classical pointed arch is set out. The apex therefore
    sits a fixed 0.707 x width above the springline, and the shape reads as
    architecture rather than as a rounded rectangle.
    """
    half = width / 2.0
    radius = width * 0.75
    offset = radius - half
    apex_rise = math.sqrt(max(0.0, radius * radius - offset * offset))
    spring_y = base_y - max(apex_rise * 0.35, height - apex_rise)

    limit = math.acos(max(-1.0, min(1.0, -offset / radius)))

    sweep = math.pi - limit  # angular span of each half, measured from its centre

    points: list[tuple[float, float]] = [(cx - half, base_y), (cx - half, spring_y)]
    # Left half: struck from a centre offset to the right, swept from the left
    # springing point (angle PI) up to the apex.
    for index in range(samples + 1):
        angle = math.pi - sweep * index / samples
        points.append((cx + offset + radius * math.cos(angle), spring_y - radius * math.sin(angle)))
    # Right half: struck from a centre offset to the left, swept back down from
    # the apex (angle = sweep) to the right springing point (angle 0).
    for index in range(samples + 1):
        angle = sweep * (1.0 - index / samples)
        points.append((cx - offset + radius * math.cos(angle), spring_y - radius * math.sin(angle)))
    points.append((cx + half, base_y))
    return points


def make_mihrab(
    width: int,
    height: int,
    fill_color: str,
    accent_color: str,
    fill_opacity: float = 0.20,
    line_opacity: float = 0.16,
) -> Image.Image:
    """A soft, backlit prayer-niche arch to sit behind the text."""
    scale = 2
    big_w, big_h = width * scale, height * scale
    canvas = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    cx = big_w / 2.0
    arch_w = big_w * 0.34
    arch_h = big_h * 0.80
    base_y = big_h * 1.02

    outer = arch_outline(cx, base_y, arch_w, arch_h)
    inner = arch_outline(cx, base_y, arch_w * 0.88, arch_h * 0.95)

    draw.polygon(outer, fill=rgba(fill_color, fill_opacity))
    draw.line(outer + [outer[0]], fill=rgba(accent_color, line_opacity), width=max(1, scale))
    draw.line(inner + [inner[0]], fill=rgba(accent_color, line_opacity * 0.6), width=max(1, scale))

    arch = canvas.resize((width, height), Image.LANCZOS)
    # Blur it into the background so it reads as light, not as a drawn shape.
    arch = arch.filter(ImageFilter.GaussianBlur(max(2.0, height * 0.008)))

    # Fade the arch out towards the top so it dissolves instead of stopping.
    fade = linear_gradient(width, height, 0.10, 1.0)
    arch.putalpha(Image.composite(arch.getchannel("A"), Image.new("L", (width, height), 0), fade))
    return arch


def make_light_backdrop(
    width: int,
    height: int,
    background_color: str,
    deep_color: str,
    accent_color: str,
    pattern_scale: int = 300,
    pattern_opacity: float = 0.075,
    grain: float = 0.0,
) -> Image.Image:
    """A near-flat pale ground, for the light appearance.

    Deliberately far plainer than the dark backdrop. iOS in light mode has no
    decorative ground at all - a grouped list sits on flat systemGray6 - so
    every device that makes the dark version work (an added bloom, a rim light,
    a lit niche) reads as grubby here. Light is subtracted rather than added:
    on a pale ground the only way to model depth is to shade it.
    """
    base = Image.new("RGB", (width, height), background_color)

    # A whisper of lift towards the top, so the frame is not dead flat.
    vertical = linear_gradient(width, height, 0.0, 0.55)
    base = Image.composite(
        Image.new("RGB", (width, height), background_color),
        Image.new("RGB", (width, height), deep_color),
        vertical,
    )

    # The lattice, if it is wanted at all, has to be drawn in ink rather than
    # in light, and much fainter - a dark line on a pale ground carries far
    # more contrast than a pale line on a dark one at the same opacity.
    if pattern_opacity > 0:
        tile = make_pattern_tile(
            pattern_scale, mix_hex(background_color, "#000000", 0.75),
            pattern_opacity * 0.5, line_width=2,
        )
        lattice = tile_pattern(tile, width, height)
        centre_mask = radial_gradient(
            width, height, (width * 0.5, height * 0.5), height * 0.95,
            inner=0.05, outer=1.0, falloff=1.3,
        )
        lattice.putalpha(
            Image.composite(lattice.getchannel("A"), Image.new("L", (width, height), 0), centre_mask)
        )
        base = Image.alpha_composite(base.convert("RGBA"), lattice).convert("RGB")

    # A very soft shade towards the corners. Not a vignette in the cinematic
    # sense - just enough separation to stop the panel floating on nothing.
    shade_mask = radial_gradient(
        width, height, (width * 0.5, height * 0.5), height * 1.05,
        inner=0.0, outer=0.10, falloff=1.5,
    )
    shade = Image.new("RGB", (width, height), mix_hex(background_color, "#000000", 0.55))
    base = Image.composite(shade, base, shade_mask)

    return add_grain(base, grain) if grain > 0 else base


def make_backdrop(
    width: int,
    height: int,
    background_color: str,
    deep_color: str,
    accent_color: str,
    pattern_scale: int = 300,
    pattern_opacity: float = 0.075,
    glow_opacity: float = 0.55,
    grain: float = 0.02,
    appearance: str = "dark",
) -> Image.Image:
    """The generated 'Midnight Mihrab' backdrop used when the user supplies none.

    Layers, bottom to top: a deep vertical gradient, a geometric lattice that
    thins out towards the centre, a lit prayer-niche arch, a warm bloom behind
    where the text sits, a cool rim light, and film grain.

    The two light sources are added rather than composited over. Replacing
    pixels with a warm tone turns the whole frame olive; adding light to it
    keeps the deep blue underneath and reads as illumination.

    None of that survives on a pale ground - adding light to something already
    near white does nothing - so the light appearance is built separately.
    """
    if appearance == "light":
        return make_light_backdrop(
            width, height, background_color, deep_color, accent_color,
            pattern_scale=pattern_scale, pattern_opacity=pattern_opacity,
            grain=grain,
        )

    black = Image.new("RGB", (width, height), (0, 0, 0))

    def add_light(target: Image.Image, colour: tuple[int, int, int], mask: Image.Image) -> Image.Image:
        layer = Image.new("RGB", (width, height), colour)
        return ImageChops.add(target, Image.composite(layer, black, mask))

    base = Image.new("RGB", (width, height), background_color)

    # Deep blue lift towards the upper middle of the frame.
    vertical = linear_gradient(width, height, 0.85, 0.10)
    base = Image.composite(Image.new("RGB", (width, height), deep_color), base, vertical)

    # Geometric lattice, thinned towards the centre so it never fights the text.
    if pattern_opacity > 0:
        tile = make_pattern_tile(pattern_scale, accent_color, pattern_opacity, line_width=2)
        lattice = tile_pattern(tile, width, height)
        centre_mask = radial_gradient(
            width, height, (width * 0.5, height * 0.5), height * 0.95,
            inner=0.05, outer=1.0, falloff=1.3,
        )
        lattice.putalpha(
            Image.composite(lattice.getchannel("A"), Image.new("L", (width, height), 0), centre_mask)
        )
        base = Image.alpha_composite(base.convert("RGBA"), lattice).convert("RGB")

    # The mihrab niche: the one piece of architecture in the frame.
    base = Image.alpha_composite(
        base.convert("RGBA"),
        make_mihrab(
            width, height,
            mix_hex(deep_color, accent_color, 0.30), accent_color,
            fill_opacity=0.16, line_opacity=0.34,
        ),
    ).convert("RGB")

    # Warm bloom behind where the text will sit.
    base = add_light(
        base,
        mix(background_color, accent_color, 0.38),
        radial_gradient(
            width, height, (width * 0.5, height * 0.58), height * 0.80,
            inner=glow_opacity, outer=0.0, falloff=1.9,
        ),
    )

    # Cool rim light in the upper-left corner for depth.
    base = add_light(
        base,
        mix(background_color, "#6FA6C8", 0.50),
        radial_gradient(
            width, height, (width * 0.10, height * 0.02), height * 0.80,
            inner=0.30, outer=0.0, falloff=2.2,
        ),
    )

    return add_grain(base, grain)


def make_light_layer(width: int, height: int, color: str, opacity: float) -> Image.Image:
    """A large soft blob of warm light that slowly drifts across the frame."""
    mask = radial_gradient(
        width, height, (width * 0.5, height * 0.5), min(width, height) * 0.52,
        inner=opacity, outer=0.0, falloff=2.4,
    )
    layer = Image.new("RGBA", (width, height), (*hex_to_rgb(color), 255))
    layer.putalpha(mask)
    return layer


def make_particle_layer(
    width: int,
    height: int,
    count: int,
    color: str,
    opacity: float,
    seed: int = 11,
    min_radius: float = 1.2,
    max_radius: float = 5.0,
) -> Image.Image:
    """Soft floating dust, drawn so that it tiles seamlessly when it wraps.

    Each mote is also drawn at its wrapped positions (+/- width, +/- height) so
    the layer can be scrolled forever without a visible seam.
    """
    scale = 2
    canvas = Image.new("L", (width * scale, height * scale), 0)
    draw = ImageDraw.Draw(canvas)
    rng = random.Random(seed)

    for _ in range(count):
        x = rng.uniform(0, width) * scale
        y = rng.uniform(0, height) * scale
        radius = rng.uniform(min_radius, max_radius) * scale
        brightness = int(rng.uniform(70, 255) * max(0.0, min(1.0, opacity)))
        for dx in (-width * scale, 0, width * scale):
            for dy in (-height * scale, 0, height * scale):
                cx, cy = x + dx, y + dy
                if -radius * 2 <= cx <= width * scale + radius * 2 and \
                   -radius * 2 <= cy <= height * scale + radius * 2:
                    draw.ellipse(
                        [cx - radius, cy - radius, cx + radius, cy + radius],
                        fill=brightness,
                    )

    canvas = canvas.resize((width, height), Image.LANCZOS).filter(ImageFilter.GaussianBlur(1.4))

    # Tile 2x2 so the drifting overlay always covers the frame.
    tiled = Image.new("L", (width * 2, height * 2), 0)
    for x in (0, width):
        for y in (0, height):
            tiled.paste(canvas, (x, y))

    layer = Image.new("RGBA", tiled.size, (*hex_to_rgb(color), 255))
    layer.putalpha(tiled)
    return layer


def make_pattern_layer(
    width: int, height: int, tile_size: int, color: str, opacity: float
) -> tuple[Image.Image, int]:
    """A drifting lattice layer plus the tile size (its wrap period)."""
    tile = make_pattern_tile(tile_size, color, opacity, line_width=2)
    layer = tile_pattern(tile, width + tile_size * 2, height + tile_size * 2)
    return layer, tile_size


# ---------------------------------------------------------------------------
# Decorative elements used by the card compositions
# ---------------------------------------------------------------------------

def make_medallion(
    size: int, accent_color: str, fill_color: str, opacity: float = 1.0
) -> Image.Image:
    """An eight-point star medallion that holds the ayah number."""
    scale = SUPERSAMPLE
    big = size * scale
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    cx = cy = big / 2

    draw.polygon(
        star_points(cx, cy, big * 0.48, big * 0.30, 8, math.pi / 8),
        fill=rgba(fill_color, 0.55 * opacity),
        outline=rgba(accent_color, 0.85 * opacity),
        width=max(1, 2 * scale // 2),
    )
    draw.polygon(
        star_points(cx, cy, big * 0.36, big * 0.24, 8, 0.0),
        outline=rgba(accent_color, 0.45 * opacity),
        width=max(1, scale // 2),
    )
    return canvas.resize((size, size), Image.LANCZOS)


def make_rosette(size: int, color: str, opacity: float = 1.0) -> Image.Image:
    """A small solid eight-point star, used as a full stop between rules.

    Solid rather than outlined: at the size this is set it reads as a mark, and
    an outline at 20-odd pixels turns into a grey smudge.
    """
    scale = SUPERSAMPLE
    big = max(4, size) * scale
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    cx = cy = big / 2
    ink = rgba(color, opacity)

    # A shallow inner radius is what makes this a flower rather than a spiky
    # star: at 0.2 the points are needles, at 0.38 they read as petals.
    draw.polygon(star_points(cx, cy, big * 0.50, big * 0.38, 8, math.pi / 8), fill=ink)
    # A second star turned 22.5 degrees fills the gaps between the petals.
    draw.polygon(star_points(cx, cy, big * 0.44, big * 0.33, 8, 0.0), fill=ink)
    # A small hole at the centre, as in the printed rosettes this borrows from.
    draw.ellipse(
        [cx - big * 0.085, cy - big * 0.085, cx + big * 0.085, cy + big * 0.085],
        fill=(0, 0, 0, 0),
    )
    return canvas.resize((max(4, size), max(4, size)), Image.LANCZOS)


def make_rule_with_ornament(
    width: int,
    color: str,
    opacity: float = 0.55,
    ornament: float = 0.0,
    line_width: int = 2,
    gap: float = 1.6,
) -> Image.Image:
    """A hairline rule broken in the middle, optionally around a rosette.

    *ornament* is the rosette's size in pixels; 0 leaves a plain gap. *gap* is
    the clear space either side of it, as a multiple of the rosette size.
    """
    ornament = max(0.0, ornament)
    height = max(int(ornament), max(1, line_width))
    canvas = Image.new("RGBA", (max(8, width), height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    ink = rgba(color, opacity)
    mid_y = height / 2.0
    half_gap = (ornament * gap) / 2.0 if ornament else max(8.0, width * 0.04)
    thickness = max(1, line_width)

    draw.rectangle(
        [0, mid_y - thickness / 2, max(0.0, width / 2 - half_gap), mid_y + thickness / 2 - 1],
        fill=ink,
    )
    draw.rectangle(
        [min(float(width), width / 2 + half_gap), mid_y - thickness / 2, width, mid_y + thickness / 2 - 1],
        fill=ink,
    )

    if ornament >= 4:
        star = make_rosette(int(ornament), color, min(1.0, opacity * 1.6))
        canvas.alpha_composite(
            star, (int(width / 2 - star.width / 2), int(mid_y - star.height / 2))
        )
    return canvas


def make_divider(
    width: int, height: int, accent_color: str, opacity: float = 0.55
) -> Image.Image:
    """A hairline rule with a small diamond at its centre."""
    scale = SUPERSAMPLE
    big_w, big_h = width * scale, max(height, 3) * scale
    canvas = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    ink = rgba(accent_color, opacity)
    mid_y = big_h / 2
    diamond = big_h * 0.42
    gap = diamond * 1.9

    line_w = max(1, scale // 2)
    draw.line([(0, mid_y), (big_w / 2 - gap, mid_y)], fill=rgba(accent_color, opacity * 0.7), width=line_w)
    draw.line([(big_w / 2 + gap, mid_y), (big_w, mid_y)], fill=rgba(accent_color, opacity * 0.7), width=line_w)
    draw.polygon(
        [
            (big_w / 2, mid_y - diamond),
            (big_w / 2 + diamond, mid_y),
            (big_w / 2, mid_y + diamond),
            (big_w / 2 - diamond, mid_y),
        ],
        fill=ink,
    )
    return canvas.resize((width, max(height, 3)), Image.LANCZOS)


def make_rounded_rect(
    width: int,
    height: int,
    radius: float,
    fill: Optional[str] = None,
    fill_opacity: float = 1.0,
    border: Optional[str] = None,
    border_opacity: float = 1.0,
    border_width: int = 1,
) -> Image.Image:
    """A rounded rectangle, drawn oversized and downsampled for clean corners.

    Pass a radius of half the height (or more) to get a capsule. Pillow's own
    rounded_rectangle is not antialiased, so at final size the corners come out
    visibly stepped - hence the supersample.
    """
    width = max(1, int(width))
    height = max(1, int(height))
    radius = max(0.0, min(float(radius), min(width, height) / 2.0))

    scale = SUPERSAMPLE
    big = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    box = (0, 0, width * scale - 1, height * scale - 1)
    draw.rounded_rectangle(
        box,
        radius=radius * scale,
        fill=rgba(fill, fill_opacity) if fill else None,
        outline=rgba(border, border_opacity) if border else None,
        width=max(1, border_width * scale) if border else 0,
    )
    return big.resize((width, height), Image.LANCZOS)


def make_frame(
    width: int,
    height: int,
    inset: int,
    accent_color: str,
    opacity: float,
    line_width: int = 2,
    corners: bool = True,
) -> Image.Image:
    """A hairline border with small corner ornaments."""
    scale = 2
    big_w, big_h = width * scale, height * scale
    canvas = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    ink = rgba(accent_color, opacity)
    soft = rgba(accent_color, opacity * 0.45)
    pad = inset * scale
    stroke = max(1, line_width * scale // 2)

    draw.rectangle(
        [pad, pad, big_w - pad, big_h - pad], outline=ink, width=stroke
    )
    inner = pad + int(9 * scale)
    draw.rectangle(
        [inner, inner, big_w - inner, big_h - inner], outline=soft, width=max(1, stroke // 2)
    )

    if corners:
        arm = int(min(big_w, big_h) * 0.052)
        for corner_x, corner_y, sx, sy in (
            (pad, pad, 1, 1),
            (big_w - pad, pad, -1, 1),
            (pad, big_h - pad, 1, -1),
            (big_w - pad, big_h - pad, -1, -1),
        ):
            draw.line(
                [(corner_x, corner_y + sy * arm * 0.55), (corner_x + sx * arm * 0.55, corner_y)],
                fill=ink, width=stroke,
            )
            star = star_points(
                corner_x + sx * arm * 0.42, corner_y + sy * arm * 0.42,
                arm * 0.20, arm * 0.09, 8, math.pi / 8,
            )
            draw.polygon(star, outline=ink, width=max(1, stroke // 2))

    return canvas.resize((width, height), Image.LANCZOS)


def save_png(image: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    return path


def save_jpeg(image: Image.Image, path: Path, quality: int = 94) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, "JPEG", quality=quality, subsampling=0, optimize=True)
    return path
