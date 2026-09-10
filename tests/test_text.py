"""Arabic and Urdu shaping, wrapping and automatic sizing."""

from __future__ import annotations

import pytest

from app.services import text as textsvc
from app.utils.logging import QVGError

BISMILLAH = "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ"
AYAH_SEVEN = (
    "صِرَٰطَ ٱلَّذِينَ أَنْعَمْتَ عَلَيْهِمْ غَيْرِ ٱلْمَغْضُوبِ عَلَيْهِمْ وَلَا ٱلضَّآلِّينَ"
)
URDU_LONG = "ان لوگوں کے رستے جن پر تو اپنا فضل وکرم کرتا رہا نہ ان کے جن پر غصے ہوتا رہا"


# ---------------------------------------------------------------------------
# Directional run splitting
# ---------------------------------------------------------------------------

def test_pure_arabic_is_one_rtl_run():
    runs = textsvc.split_runs(BISMILLAH)
    assert len(runs) == 1
    assert runs[0][1] is True


def test_pure_latin_in_an_rtl_paragraph_is_ltr():
    runs = textsvc.split_runs("Surah", base_rtl=True)
    assert runs == [("Surah", False)]


def test_mixed_text_splits_into_runs():
    runs = textsvc.split_runs("آیت Surah")
    assert len(runs) == 2
    assert runs[0][1] is True
    assert runs[1][1] is False


def test_arabic_indic_digits_stay_rtl():
    runs = textsvc.split_runs("٧ آیات")
    assert len(runs) == 1
    assert runs[0][1] is True


def test_empty_string_has_no_runs():
    assert textsvc.split_runs("") == []


def test_runs_reconstruct_the_original_string():
    text = "آیت 12 Surah ٣"
    assert "".join(part for part, _ in textsvc.split_runs(text)) == text


# ---------------------------------------------------------------------------
# Shaping and rasterisation
# ---------------------------------------------------------------------------

def test_shaping_backend_is_harfbuzz():
    assert textsvc.shaping_backend() == "harfbuzz", (
        "uharfbuzz/freetype-py should be installed - Arabic joining and harakat "
        "placement depend on them"
    )


def test_arabic_renders_visible_ink(arabic_font):
    rendered = textsvc.render_single_line(arabic_font, BISMILLAH, 64)
    assert rendered.width > 100
    assert rendered.height > 20
    assert rendered.mask.getbbox() is not None


def test_shaping_joins_letters(arabic_font):
    """Joined Arabic is narrower than the same letters rendered in isolation."""
    handle = textsvc._load_font(str(arabic_font), 64)
    joined = textsvc.measure(handle, "بسم")
    separated = sum(textsvc.measure(handle, ch) for ch in "بسم")
    assert joined < separated * 0.85


def test_diacritics_do_not_widen_the_line(arabic_font):
    """Harakat are zero-advance marks: they add height, never width."""
    handle = textsvc._load_font(str(arabic_font), 64)
    with_marks = textsvc.measure(handle, "بِسْمِ")
    without = textsvc.measure(handle, "بسم")
    assert abs(with_marks - without) < without * 0.06


def test_diacritics_add_ink_above_the_letters(arabic_font):
    plain = textsvc.render_single_line(arabic_font, "بسم", 96)
    vowelled = textsvc.render_single_line(arabic_font, "بِسْمِ", 96)
    assert vowelled.height > plain.height


def test_urdu_nastaliq_renders(urdu_font):
    rendered = textsvc.render_single_line(urdu_font, "اردو ترجمہ", 64)
    assert rendered.width > 80
    assert rendered.mask.getbbox() is not None


def test_longer_text_is_wider(arabic_font):
    handle = textsvc._load_font(str(arabic_font), 48)
    assert textsvc.measure(handle, AYAH_SEVEN) > textsvc.measure(handle, BISMILLAH)


def test_render_is_cropped_to_its_ink(arabic_font):
    rendered = textsvc.render_single_line(arabic_font, BISMILLAH, 80)
    bbox = rendered.mask.getbbox()
    assert bbox == (0, 0, rendered.width, rendered.height)


def test_tinting_produces_rgba_with_alpha(arabic_font):
    rendered = textsvc.render_single_line(arabic_font, BISMILLAH, 48)
    layer = rendered.tinted("#D8B871")
    assert layer.mode == "RGBA"
    assert layer.size == (rendered.width, rendered.height)
    assert layer.getchannel("A").getextrema()[1] > 0


def test_tint_opacity_reduces_alpha(arabic_font):
    rendered = textsvc.render_single_line(arabic_font, BISMILLAH, 48)
    full = rendered.tinted("#FFFFFF", 1.0).getchannel("A").getextrema()[1]
    half = rendered.tinted("#FFFFFF", 0.5).getchannel("A").getextrema()[1]
    assert half < full


def test_glow_is_larger_than_the_text(arabic_font):
    rendered = textsvc.render_single_line(arabic_font, BISMILLAH, 48)
    glow = rendered.glow("#D8B871", 10, 0.4)
    assert glow.width > rendered.width
    assert glow.height > rendered.height


def test_tracking_widens_latin(arabic_font, real_fonts_dir):
    latin = real_fonts_dir / "CormorantGaramond-Regular.ttf"
    if not latin.is_file():
        pytest.skip("Latin font missing")
    tight = textsvc.render_single_line(latin, "SURAH", 60, base_rtl=False)
    loose = textsvc.render_single_line(latin, "SURAH", 60, base_rtl=False, tracking=12)
    assert loose.width > tight.width


# ---------------------------------------------------------------------------
# Wrapping and fitting
# ---------------------------------------------------------------------------

def test_wrap_breaks_long_text(arabic_font):
    handle = textsvc._load_font(str(arabic_font), 72)
    lines = textsvc.wrap_text(handle, AYAH_SEVEN, max_width=600)
    assert len(lines) > 1


def test_wrap_keeps_short_text_on_one_line(arabic_font):
    handle = textsvc._load_font(str(arabic_font), 40)
    assert len(textsvc.wrap_text(handle, "ٱلرَّحْمَٰنِ ٱلرَّحِيمِ", max_width=4000)) == 1


def test_wrap_preserves_every_word(arabic_font):
    handle = textsvc._load_font(str(arabic_font), 72)
    lines = textsvc.wrap_text(handle, AYAH_SEVEN, max_width=500)
    assert " ".join(lines).split() == AYAH_SEVEN.split()


def test_wrap_never_loses_a_word_even_when_too_narrow(arabic_font):
    handle = textsvc._load_font(str(arabic_font), 90)
    lines = textsvc.wrap_text(handle, AYAH_SEVEN, max_width=10)
    assert " ".join(lines).split() == AYAH_SEVEN.split()


@pytest.mark.parametrize("text", [BISMILLAH, AYAH_SEVEN, "ٱلرَّحْمَٰنِ ٱلرَّحِيمِ"])
def test_fit_stays_inside_the_box(arabic_font, text):
    box_w, box_h = 1400, 420
    result = textsvc.fit_text(
        arabic_font, text, box_w, box_h,
        size_max=160, size_min=40, line_spacing=1.62, max_lines=4,
    )
    assert result.rendered.width <= box_w
    assert result.rendered.height <= box_h
    assert 40 <= result.font_size <= 160


def test_fit_stays_inside_the_box_for_urdu(urdu_font):
    box_w, box_h = 1400, 380
    result = textsvc.fit_text(
        urdu_font, URDU_LONG, box_w, box_h,
        size_max=95, size_min=30, line_spacing=1.95, max_lines=4,
    )
    assert result.rendered.width <= box_w
    assert result.rendered.height <= box_h


def test_fit_uses_a_larger_size_for_shorter_text(arabic_font):
    kwargs = dict(
        max_width=1400, max_height=420, size_max=160, size_min=40,
        line_spacing=1.62, max_lines=4,
    )
    short = textsvc.fit_text(arabic_font, "ٱلرَّحْمَٰنِ ٱلرَّحِيمِ", **kwargs)
    long = textsvc.fit_text(arabic_font, AYAH_SEVEN, **kwargs)
    assert short.font_size > long.font_size


def test_fit_respects_the_max_line_count(arabic_font):
    result = textsvc.fit_text(
        arabic_font, AYAH_SEVEN, 900, 600,
        size_max=140, size_min=20, line_spacing=1.62, max_lines=2,
    )
    assert len(result.lines) <= 2


def test_fit_rejects_empty_text(arabic_font):
    with pytest.raises(QVGError):
        textsvc.fit_text(arabic_font, "   ", 800, 400, 100, 30, 1.6, 3)


def test_a_narrow_box_still_produces_ink(arabic_font):
    result = textsvc.fit_text(
        arabic_font, AYAH_SEVEN, 320, 200, size_max=120, size_min=14,
        line_spacing=1.6, max_lines=6,
    )
    assert result.rendered.mask.getbbox() is not None


# ---------------------------------------------------------------------------
# Coverage checks
# ---------------------------------------------------------------------------

def test_quran_font_covers_the_uthmani_text(arabic_font):
    assert textsvc.check_font_covers(arabic_font, AYAH_SEVEN + BISMILLAH) == []


def test_urdu_font_covers_the_translation(urdu_font):
    assert textsvc.check_font_covers(urdu_font, URDU_LONG) == []


def test_coverage_check_reports_missing_glyphs(real_fonts_dir):
    latin = real_fonts_dir / "CormorantGaramond-Regular.ttf"
    if not latin.is_file():
        pytest.skip("Latin font missing")
    missing = textsvc.check_font_covers(latin, BISMILLAH)
    assert missing
    assert "U+" in textsvc.describe_missing(missing[:2])


def test_missing_font_file_raises(tmp_path):
    with pytest.raises(QVGError):
        textsvc.render_single_line(tmp_path / "nope.ttf", "بسم", 40)


# ---------------------------------------------------------------------------
# Bidirectional edge cases (regressions)
# ---------------------------------------------------------------------------

def test_a_word_space_after_a_digit_stays_between_the_words():
    """Regression: neutrals used to attach to whatever was on their left, so in
    "7 ayahs" the space was swallowed by the leading digit run and pushed to the
    far edge, leaving the digit jammed against the Arabic word."""
    runs = textsvc.split_runs("۷ آیات", base_rtl=True)
    assert runs == [("۷", False), (" آیات", True)]


def test_a_digit_after_a_word_keeps_its_space_too():
    runs = textsvc.split_runs("آیت ۱", base_rtl=True)
    assert runs == [("آیت ", True), ("۱", False)]


def test_multi_digit_numbers_are_not_reversed():
    """Digits run left-to-right even inside RTL text: ۱۲۳ must not become ۳۲۱."""
    runs = textsvc.split_runs("۱۲۳ آیات", base_rtl=True)
    assert runs[0] == ("۱۲۳", False)


def test_a_neutral_span_between_two_rtl_runs_stays_rtl():
    assert textsvc.split_runs("اردو ترجمہ", base_rtl=True) == [("اردو ترجمہ", True)]


def test_a_neutral_span_between_two_ltr_runs_stays_ltr():
    assert textsvc.split_runs("SURAH 001", base_rtl=False) == [("SURAH 001", False)]


def test_a_string_of_only_neutrals_takes_the_base_direction():
    assert textsvc.split_runs("  ", base_rtl=True) == [("  ", True)]
    assert textsvc.split_runs("  ", base_rtl=False) == [("  ", False)]


def test_the_digit_and_the_word_do_not_collide_when_rendered(urdu_font):
    """The visible symptom of the bug: with the space on the wrong side the
    rendered line was measurably narrower than the same words spaced properly."""
    handle = textsvc._load_font(str(urdu_font), 64)
    spaced = textsvc.measure(handle, "۷ آیات")
    unspaced = textsvc.measure(handle, "۷آیات")
    assert spaced > unspaced


# ---------------------------------------------------------------------------
# Per-word masks (used to light the word being recited)
# ---------------------------------------------------------------------------

def _words(font, text, size=72, width=1600):
    handle = textsvc._load_font(str(font), size)
    lines = textsvc.wrap_text(handle, text, width)
    return textsvc.render_lines_with_words(handle, lines, size * 1.62)


def test_one_mask_per_word(arabic_font):
    _, masks = _words(arabic_font, BISMILLAH)
    assert len(masks) == len(BISMILLAH.split())


def test_word_masks_are_in_reading_order(arabic_font):
    """Index 0 must be the first word READ, which in Arabic is the rightmost."""
    _, masks = _words(arabic_font, BISMILLAH)
    assert [m.index for m in masks] == list(range(len(masks)))
    assert [m.text for m in masks] == BISMILLAH.split()
    # First word read sits furthest right on the line.
    assert masks[0].x > masks[-1].x


def test_word_masks_are_numbered_across_wrapped_lines(arabic_font):
    """A word's number has to match text.split() on the UNWRAPPED string,
    because that is what the recitation timings are keyed on."""
    rendered, masks = _words(arabic_font, AYAH_SEVEN, size=96, width=1200)
    assert rendered.line_count > 1
    assert [m.text for m in masks] == AYAH_SEVEN.split()


def test_the_word_masks_rebuild_the_block_exactly(arabic_font):
    """The highlight is drawn over the card's own glyphs, so it has to come
    from the same shaping run - not from re-shaping the word on its own."""
    from PIL import Image, ImageChops

    block, masks = _words(arabic_font, AYAH_SEVEN, size=88, width=1400)
    rebuilt = Image.new("L", block.mask.size, 0)
    for mask in masks:
        region = rebuilt.crop(mask.box)
        rebuilt.paste(ImageChops.lighter(region, mask.mask), (mask.x, mask.y))

    difference = ImageChops.difference(rebuilt, block.mask)
    assert max(difference.getextrema()) == 0


def test_word_masks_sit_inside_the_block(arabic_font):
    block, masks = _words(arabic_font, BISMILLAH)
    for mask in masks:
        assert mask.x >= 0 and mask.y >= 0
        assert mask.x + mask.width <= block.width
        assert mask.y + mask.height <= block.height


def test_word_masks_do_not_overlap_horizontally_on_one_line(arabic_font):
    block, masks = _words(arabic_font, BISMILLAH, width=4000)
    assert block.line_count == 1
    spans = sorted((m.x, m.x + m.width) for m in masks)
    for (_, first_end), (second_start, _) in zip(spans, spans[1:]):
        assert first_end <= second_start


def test_asking_for_words_does_not_change_the_block(arabic_font):
    from PIL import ImageChops

    handle = textsvc._load_font(str(arabic_font), 72)
    lines = textsvc.wrap_text(handle, BISMILLAH, 1600)
    plain = textsvc.render_lines(handle, lines, 72 * 1.62)
    with_words, _ = textsvc.render_lines_with_words(handle, lines, 72 * 1.62)
    assert plain.mask.size == with_words.mask.size
    assert max(ImageChops.difference(plain.mask, with_words.mask).getextrema()) == 0


def test_a_glyph_knows_which_word_it_came_from(arabic_font):
    handle = textsvc._load_font(str(arabic_font), 72)
    shaped = textsvc.shape_line(handle, BISMILLAH)
    numbers = {g.word for g in shaped.glyphs}
    assert numbers == set(range(len(BISMILLAH.split())))
