"""The iOS and glass card styles, and the layer that lights the word being recited."""

from __future__ import annotations

import pytest

from app.rendering import textures
from app.rendering.compositions import CardBuilder

SIZE = (640, 360)


@pytest.fixture
def ios(config):
    config.theme.style = "ios"
    config.content.word_highlight = True
    return config


@pytest.fixture
def classic(config):
    config.theme.style = "classic"
    return config


def _ink(image):
    return image.getchannel("A").getbbox()


# ---------------------------------------------------------------------------
# Rounded rectangles
# ---------------------------------------------------------------------------

def test_a_rounded_rect_has_transparent_corners():
    box = textures.make_rounded_rect(200, 80, 40, fill="#FFFFFF", fill_opacity=1.0)
    assert box.getpixel((0, 0))[3] < 40         # corner cut away
    assert box.getpixel((100, 40))[3] > 200     # middle filled


def test_a_capsule_radius_is_clamped_to_the_shape():
    """Asking for a radius larger than the box must not error or invert it."""
    box = textures.make_rounded_rect(60, 200, 9999, fill="#FFFFFF")
    assert box.size == (60, 200)
    assert box.getpixel((30, 100))[3] > 200


def test_a_border_only_rect_is_hollow():
    box = textures.make_rounded_rect(200, 80, 20, border="#FFFFFF", border_width=2)
    assert box.getpixel((100, 40))[3] == 0
    assert box.getchannel("A").getbbox() is not None


def test_a_rounded_rect_is_antialiased():
    """Pillow's own rounded_rectangle is not, which is why it is supersampled."""
    box = textures.make_rounded_rect(200, 80, 30, fill="#FFFFFF", fill_opacity=1.0)
    alphas = {box.getpixel((x, 2))[3] for x in range(0, 60)}
    assert any(0 < a < 255 for a in alphas)


# ---------------------------------------------------------------------------
# The iOS style
# ---------------------------------------------------------------------------

def test_the_ios_card_renders(ios, surah, real_fonts_dir):
    card = CardBuilder(ios, surah, *SIZE).arabic_card(surah.ayah(1))
    assert card.size == SIZE
    assert _ink(card) is not None


def test_the_ios_card_puts_a_panel_behind_the_text(ios, surah, real_fonts_dir):
    builder = CardBuilder(ios, surah, *SIZE)
    with_panel = builder.arabic_card(surah.ayah(1))

    ios.theme.panel_opacity = 0.0
    bare = CardBuilder(ios, surah, *SIZE).arabic_card(surah.ayah(1))

    # The panel is a large translucent surface, so it lights up far more pixels.
    assert _count_ink(with_panel) > _count_ink(bare)


def _count_ink(image):
    return sum(1 for value in image.getchannel("A").getdata() if value > 0)


def test_the_ios_frame_is_a_rounded_container(ios, surah, real_fonts_dir):
    frame = CardBuilder(ios, surah, *SIZE).frame_layer()
    assert frame is not None and frame.size == SIZE
    # Rounded, so the very corner of the inset box is clear.
    inset = int(min(SIZE) * ios.theme.frame_inset)
    assert frame.getpixel((inset, inset))[3] == 0


def test_the_classic_frame_is_still_available(classic, surah, real_fonts_dir):
    frame = CardBuilder(classic, surah, *SIZE).frame_layer()
    assert frame is not None
    assert _ink(frame) is not None


def test_both_styles_produce_a_different_card(config, surah, real_fonts_dir):
    config.theme.style = "classic"
    old = CardBuilder(config, surah, *SIZE).arabic_card(surah.ayah(2))
    config.theme.style = "ios"
    new = CardBuilder(config, surah, *SIZE).arabic_card(surah.ayah(2))
    assert list(old.getdata()) != list(new.getdata())


def test_the_ayah_number_is_shown_in_both_styles(config, surah, real_fonts_dir):
    for style in ("classic", "ios"):
        config.theme.style = style
        card = CardBuilder(config, surah, *SIZE).arabic_card(surah.ayah(5))
        # The badge / medallion sits above the text, in the top third.
        top_strip = card.crop((0, 0, SIZE[0], SIZE[1] // 3))
        assert top_strip.getchannel("A").getbbox() is not None, style


# ---------------------------------------------------------------------------
# The highlight layer
# ---------------------------------------------------------------------------

def test_a_highlight_layer_is_produced_for_every_word(ios, surah, real_fonts_dir):
    builder = CardBuilder(ios, surah, *SIZE)
    ayah = surah.ayah(1)
    for index in range(len(ayah.arabic.split())):
        layer = builder.highlight_layer(ayah, index)
        assert layer is not None, index
        assert _ink(layer) is not None


def test_asking_for_a_word_that_is_not_there_returns_nothing(ios, surah, real_fonts_dir):
    builder = CardBuilder(ios, surah, *SIZE)
    assert builder.highlight_layer(surah.ayah(1), 99) is None


def test_each_word_is_highlighted_somewhere_different(ios, surah, real_fonts_dir):
    builder = CardBuilder(ios, surah, *SIZE)
    ayah = surah.ayah(7)
    boxes = [
        builder.highlight_layer(ayah, i).getchannel("A").getbbox()
        for i in range(len(ayah.arabic.split()))
    ]
    assert len(set(boxes)) == len(boxes)


def test_the_highlight_covers_the_word_it_names(ios, surah, real_fonts_dir):
    """It is drawn over the card's own glyphs, so it must land on top of them,
    not beside them - the whole point of sharing one shaping run."""
    builder = CardBuilder(ios, surah, *SIZE)
    ayah = surah.ayah(1)
    layout = builder._arabic_layout(ayah)

    for index, word in enumerate(layout.words):
        layer = builder.highlight_layer(ayah, index)
        left, top, right, bottom = layer.getchannel("A").getbbox()
        expected_left = layout.left + word.x
        expected_top = layout.top + word.y
        # The pill and the glow extend past the glyphs, never fall short.
        assert left <= expected_left
        assert top <= expected_top
        assert right >= expected_left + word.width
        assert bottom >= expected_top + word.height


def test_the_highlight_is_opaque_over_the_glyphs(ios, surah, real_fonts_dir):
    """A translucent highlight would let the card's own colour bleed through."""
    ios.theme.highlight_pill_enabled = False
    ios.theme.highlight_glow_enabled = False
    builder = CardBuilder(ios, surah, *SIZE)
    layer = builder.highlight_layer(surah.ayah(1), 0)
    assert layer.getchannel("A").getextrema()[1] == 255


def test_the_pill_can_be_switched_off(ios, surah, real_fonts_dir):
    ios.theme.highlight_glow_enabled = False
    with_pill = CardBuilder(ios, surah, *SIZE).highlight_layer(surah.ayah(1), 0)

    ios.theme.highlight_pill_enabled = False
    without = CardBuilder(ios, surah, *SIZE).highlight_layer(surah.ayah(1), 0)

    assert _count_ink(with_pill) > _count_ink(without)


def test_no_highlight_layers_are_built_when_the_feature_is_off(
    config, surah, real_fonts_dir
):
    config.content.word_highlight = False
    builder = CardBuilder(config, surah, *SIZE)
    layout = builder._arabic_layout(surah.ayah(1))
    assert layout.words == []
    assert builder.highlight_layer(surah.ayah(1), 0) is None


def test_the_layout_is_measured_once_per_ayah(ios, surah, real_fonts_dir):
    """The card and its highlights must agree on the position to the pixel."""
    builder = CardBuilder(ios, surah, *SIZE)
    first = builder._arabic_layout(surah.ayah(3))
    second = builder._arabic_layout(surah.ayah(3))
    assert first is second


# ---------------------------------------------------------------------------
# The glass style (text over supplied artwork)
# ---------------------------------------------------------------------------

@pytest.fixture
def glass(config):
    config.theme.style = "glass"
    config.theme.frame_enabled = True   # glass overrides this itself
    config.theme.glow_enabled = False
    config.theme.text_shadow_enabled = True
    config.content.word_highlight = True
    return config


def test_glass_draws_no_frame(glass, surah, real_fonts_dir):
    """The artwork is the frame; a border would fight the picture."""
    assert CardBuilder(glass, surah, *SIZE).frame_layer() is None


def test_the_glass_title_card_renders(glass, surah, real_fonts_dir):
    card = CardBuilder(glass, surah, *SIZE).intro_card()
    assert card.size == SIZE
    assert _ink(card) is not None


def test_the_glass_ayah_card_renders(glass, surah, real_fonts_dir):
    card = CardBuilder(glass, surah, *SIZE).arabic_card(surah.ayah(7))
    assert card.size == SIZE
    assert _ink(card) is not None


def test_the_chip_encloses_its_text(glass, surah, real_fonts_dir):
    """The capsule has to be wider and taller than the words inside it."""
    from app.services import text as textsvc

    builder = CardBuilder(glass, surah, *SIZE)
    canvas = builder._blank()
    label = textsvc.render_single_line(
        builder.fonts.latin, "SURAH 001", 24, base_rtl=False
    )
    bottom = builder._chip(canvas, label, 40, "#FFFFFF")

    assert bottom > 40 + label.height
    left, top, right, _ = canvas.getchannel("A").getbbox()
    assert right - left > label.width
    assert top <= 40


def test_the_top_chip_carries_rules_either_side(glass, surah, real_fonts_dir):
    from app.services import text as textsvc

    builder = CardBuilder(glass, surah, *SIZE)
    label = textsvc.render_single_line(
        builder.fonts.latin, "SURAH 001", 24, base_rtl=False
    )

    plain = builder._blank()
    builder._chip(plain, label, 40, "#FFFFFF", rules=False)
    ruled = builder._blank()
    builder._chip(ruled, label, 40, "#FFFFFF", rules=True)

    assert _width(ruled) > _width(plain)


def _width(image):
    box = image.getchannel("A").getbbox()
    return 0 if box is None else box[2] - box[0]


def test_the_rosette_closes_the_block(glass, surah, real_fonts_dir):
    builder = CardBuilder(glass, surah, *SIZE)
    with_ornament = builder.arabic_card(surah.ayah(3))

    glass.theme.ornament_enabled = False
    without = CardBuilder(glass, surah, *SIZE).arabic_card(surah.ayah(3))

    assert _count_ink(with_ornament) > _count_ink(without)


def test_the_rosette_is_a_flower_not_a_spike():
    """A shallow inner radius is what separates the two; at 0.2 the points are
    needles and the mark reads as a smudge at the size it is set."""
    star = textures.make_rosette(64, "#FFFFFF")
    row = [star.getpixel((x, 32))[3] for x in range(64)]
    lit = sum(1 for value in row if value > 100)
    # A petal-shaped rosette fills most of its own width across the middle.
    assert lit > 32


def test_the_shadow_only_appears_when_asked(glass, surah, real_fonts_dir):
    builder = CardBuilder(glass, surah, *SIZE)
    with_shadow = builder.arabic_card(surah.ayah(3))

    glass.theme.text_shadow_enabled = False
    without = CardBuilder(glass, surah, *SIZE).arabic_card(surah.ayah(3))

    assert _count_ink(with_shadow) > _count_ink(without)


def test_glass_and_ios_produce_different_cards(config, surah, real_fonts_dir):
    config.theme.style = "ios"
    ios_card = CardBuilder(config, surah, *SIZE).arabic_card(surah.ayah(1))
    config.theme.style = "glass"
    glass_card = CardBuilder(config, surah, *SIZE).arabic_card(surah.ayah(1))
    assert list(ios_card.getdata()) != list(glass_card.getdata())


def test_the_capsule_does_not_erase_the_line_above(config, surah, real_fonts_dir):
    """A solid capsule sits over the finished card, so without repair it takes
    the tops off the harakat of the line above it."""
    config.theme.style = "glass"
    config.content.word_highlight = True
    config.theme.highlight_pill_enabled = True
    config.theme.highlight_pill_opacity = 1.0
    config.theme.highlight_glow_enabled = False

    builder = CardBuilder(config, surah, *SIZE)
    ayah = surah.ayah(7)                    # the only multi-line ayah
    layout = builder._arabic_layout(ayah)
    assert layout.fitted.rendered.line_count > 1

    # A word on the second line, so the capsule reaches up into the first.
    target = max(layout.words, key=lambda w: w.y)
    layer = builder.highlight_layer(ayah, target.index)

    pad_y = int(target.height * config.theme.highlight_pill_pad_y)
    pill_top = layout.top + target.y - pad_y
    strip = layer.crop((0, pill_top, SIZE[0], pill_top + max(2, pad_y)))

    # Some neighbouring ink was redrawn inside the capsule's top edge.
    assert strip.getchannel("A").getbbox() is not None


def test_validate_warns_when_glass_has_no_artwork(config):
    """The style has no ground of its own, so an empty folder means the design
    is missing its main element - easy to miss in a small preview."""
    from app.main import Validator

    config.theme.style = "glass"
    config.background.source = "auto"
    assert not list(config.backgrounds_dir.glob("*.jpg"))

    validator = Validator(config)
    validator._check_background()
    check = next(c for c in validator.checks if c.name == "Background")
    assert not check.failed          # it still renders
    assert "glass" in check.detail
    assert any("plain plate" in hint for hint in validator.hints)


def test_validate_is_quiet_about_artwork_in_other_styles(config):
    from app.main import Validator

    config.theme.style = "ios"
    config.background.source = "auto"

    validator = Validator(config)
    validator._check_background()
    check = next(c for c in validator.checks if c.name == "Background")
    assert "glass" not in check.detail
    assert not any("plain plate" in hint for hint in validator.hints)


def test_the_shipped_highlight_is_bare_coloured_text():
    """What was asked for: the word's own letters change colour, with nothing
    drawn around it - no capsule, no box, no border."""
    from pathlib import Path

    from app.config import load_config

    root = Path(__file__).resolve().parents[1]
    theme = load_config(root / "config.yaml", root=root).theme

    assert theme.highlight_pill_enabled is False
    # An amber/yellow, not the text colour: it has to read as a different word.
    red, green, blue = (
        int(theme.highlight_color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)
    )
    assert red > 200 and green > 150 and blue < 140, theme.highlight_color
    assert red - blue > 80, "the highlight is not distinctly warm"


def test_the_shipped_highlight_fades_rather_than_snapping():
    from pathlib import Path

    from app.config import load_config

    root = Path(__file__).resolve().parents[1]
    assert load_config(root / "config.yaml", root=root).theme.highlight_fade > 0
