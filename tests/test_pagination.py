"""Ayahs too long for one screen are split, and the recitation runs straight on.

The rule these tests hold is that splitting changes only what is on screen. The
ayah is never reordered, never repeated and never cut short; the recitation is
not paused at a page turn; and the finished video is exactly as long as it was
before the ayah was split.
"""

from __future__ import annotations

import pytest

from app.models.ayah import recited_words
from app.rendering.pagination import AyahPage, single_page, split_pages
from app.rendering.timeline import build_timeline


# ---------------------------------------------------------------------------
# The splitter
# ---------------------------------------------------------------------------

def test_text_that_fits_is_left_as_one_page():
    words = ["one", "two", "three"]
    pages = split_pages(2, words, fits=lambda text: True)
    assert len(pages) == 1
    assert pages[0].first_word == 0
    assert pages[0].last_word == 2
    assert pages[0].is_only_page


def test_a_long_ayah_is_split_into_the_fewest_pages_that_fit():
    words = [f"w{n}" for n in range(10)]
    # Anything up to four words fits.
    pages = split_pages(2, words, fits=lambda text: len(text.split()) <= 4)
    assert [p.word_count for p in pages] == [4, 4, 2]
    assert [p.count for p in pages] == [3, 3, 3]


def test_the_pages_cover_every_word_once_and_in_order():
    words = [f"w{n}" for n in range(37)]
    pages = split_pages(2, words, fits=lambda text: len(text.split()) <= 6)

    covered = []
    for page in pages:
        covered.extend(range(page.first_word, page.last_word + 1))
    assert covered == list(range(37))
    assert " ".join(p.text for p in pages) == " ".join(words)


def test_a_single_word_too_big_for_the_screen_still_gets_a_page():
    """Dropping it would drop scripture; the fitter shrinks it instead."""
    pages = split_pages(2, ["enormous", "word"], fits=lambda text: False)
    assert [p.word_count for p in pages] == [1, 1]


def test_a_page_knows_which_words_are_its_own():
    page = AyahPage(ayah=2, index=1, count=2, first_word=5, last_word=9, text="...")
    assert not page.contains(4)
    assert page.contains(5) and page.contains(9)
    assert not page.contains(10)
    assert page.local_index(7) == 2


# ---------------------------------------------------------------------------
# The timeline
# ---------------------------------------------------------------------------

@pytest.fixture
def plan(tmp_path):
    from app.services.audio import AudioClip, AudioPlan

    plan = AudioPlan(mode="per_ayah")
    for number in range(1, 8):
        plan.recitation[number] = AudioClip(
            path=tmp_path / f"r{number}.wav", duration=12.0, label=f"r{number}"
        )
    return plan


@pytest.fixture
def timings(surah):
    """Even, published-looking timings: word k of every ayah starts at k seconds."""
    from app.services.word_timings import AyahTimings, WordSpan

    result = {}
    for ayah in surah.ayahs:
        words = recited_words(ayah.arabic)
        step = 12.0 / len(words)
        result[ayah.number] = AyahTimings(
            ayah=ayah.number,
            words=[
                WordSpan(index=i, text=w, start=i * step, end=(i + 1) * step)
                for i, w in enumerate(words)
            ],
            source="published",
        )
    return result


def _split_every_ayah_in_two(surah):
    pages = {}
    for ayah in surah.ayahs:
        words = recited_words(ayah.arabic)
        half = max(1, len(words) // 2)
        if len(words) < 2:
            pages[ayah.number] = [single_page(ayah.number, ayah.arabic, len(words))]
            continue
        pages[ayah.number] = [
            AyahPage(ayah=ayah.number, index=0, count=2, first_word=0,
                     last_word=half - 1, text=" ".join(words[:half])),
            AyahPage(ayah=ayah.number, index=1, count=2, first_word=half,
                     last_word=len(words) - 1, text=" ".join(words[half:])),
        ]
    return pages


def test_splitting_an_ayah_does_not_change_how_long_the_video_is(config, surah, plan, timings):
    config.content.urdu_translation = False
    plain = build_timeline(config, surah, plan)
    paged = build_timeline(
        config, surah, plan, pages=_split_every_ayah_in_two(surah), timings=timings
    )

    assert len(paged.segments) > len(plain.segments)
    assert paged.total_duration == pytest.approx(plain.total_duration, abs=0.01)


def test_the_recitation_is_not_paused_at_a_page_turn(config, surah, plan, timings):
    config.content.urdu_translation = False
    paged = build_timeline(
        config, surah, plan, pages=_split_every_ayah_in_two(surah), timings=timings
    )

    for ayah in surah.ayahs:
        parts = [s for s in paged.segments if s.ayah_number == ayah.number]
        if len(parts) < 2:
            continue
        for first, second in zip(parts, parts[1:]):
            # The next page's audio begins exactly where this one's ends.
            assert second.audio_start == pytest.approx(first.audio_end, abs=0.02)


def test_the_pages_play_the_whole_recitation_between_them(config, surah, plan, timings):
    config.content.urdu_translation = False
    paged = build_timeline(
        config, surah, plan, pages=_split_every_ayah_in_two(surah), timings=timings
    )

    for ayah in surah.ayahs:
        parts = [s for s in paged.segments if s.ayah_number == ayah.number]
        played = sum(s.audio.duration for s in parts)
        assert played == pytest.approx(plan.recitation[ayah.number].duration, abs=0.02)
        # ... starting at the top of the file and ending at the end of it.
        assert parts[0].audio.trim_start == pytest.approx(0.0)
        assert parts[-1].audio.trim_end == pytest.approx(
            plan.recitation[ayah.number].duration, abs=0.02
        )


def test_a_word_is_only_lit_on_the_page_that_shows_it(config, surah, plan, timings):
    from app.rendering.timeline import card_windows, word_windows

    config.content.urdu_translation = False
    config.content.word_highlight = True
    pages = _split_every_ayah_in_two(surah)
    paged = build_timeline(config, surah, plan, pages=pages, timings=timings)
    windows = card_windows(config, paged)
    words = word_windows(config, paged, timings, windows)

    by_index = {s.index: s for s in paged.segments}
    for word in words:
        segment = by_index[word.segment_index]
        assert segment.page is None or segment.page.contains(word.word_index)
