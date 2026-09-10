"""Per-word recitation timings, and the highlight windows built from them."""

from __future__ import annotations

import json

import pytest

from app.rendering.timeline import build_timeline, card_windows, word_windows
from app.services import audio as audiosvc
from app.services import word_timings as wt


# ---------------------------------------------------------------------------
# The estimator (the fallback when nothing is published)
# ---------------------------------------------------------------------------

def test_estimated_words_fill_the_whole_clip():
    timings = wt.estimate(1, "one two three", 6.0)
    assert timings.is_estimated
    assert len(timings.words) == 3
    assert timings.words[0].start == pytest.approx(0.0)
    assert timings.words[-1].end == pytest.approx(6.0)


def test_estimated_words_never_overlap():
    timings = wt.estimate(1, "alpha bb cccccc dd", 4.0)
    for earlier, later in zip(timings.words, timings.words[1:]):
        assert earlier.end == pytest.approx(later.start)


def test_a_longer_word_is_given_longer(surah):
    ayah = surah.ayah(7)
    timings = wt.estimate(7, ayah.arabic, 10.0)
    lengths = {w.text: w.duration for w in timings.words}
    longest = max(lengths, key=lambda t: len(t))
    shortest = min(lengths, key=lambda t: len(t))
    assert lengths[longest] > lengths[shortest]


def test_harakat_do_not_count_as_extra_length():
    """Marks change how a letter sounds, not how many letters there are."""
    bare = wt._spoken_letters("بسم")            # b s m
    marked = wt._spoken_letters("بِسْمِ")  # the same, vowelled
    assert bare == marked == 3


def test_the_word_text_matches_the_ayah(surah):
    ayah = surah.ayah(1)
    timings = wt.estimate(1, ayah.arabic, 5.0)
    assert [w.text for w in timings.words] == ayah.arabic.split()


# ---------------------------------------------------------------------------
# Reading the stored file
# ---------------------------------------------------------------------------

def _store(config, surah, ayahs):
    path = config.word_timings_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"surah_number": 1, "ayahs": ayahs}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _good_entry(surah, number, duration):
    words = surah.ayah(number).arabic.split()
    step = duration / len(words)
    return {
        "words": words,
        "timings": [[round(i * step, 3), round((i + 1) * step, 3)] for i in range(len(words))],
    }


def test_published_timings_are_used_when_they_fit(config, surah):
    _store(config, surah, {"1": _good_entry(surah, 1, 5.0)})
    loaded = wt.load(config, surah, {1: 5.0})
    assert loaded[1].source == "published"
    assert not loaded[1].is_estimated


def test_a_missing_file_falls_back_to_estimates(config, surah):
    assert not config.word_timings_file.exists()
    loaded = wt.load(config, surah, {n: 4.0 for n in range(1, 8)})
    assert all(t.is_estimated for t in loaded.values())
    assert set(loaded) == {a.number for a in surah.ayahs}


def test_timings_that_outrun_the_recitation_are_refused(config, surah):
    """A file made for a different recording would drift, silently."""
    _store(config, surah, {"1": _good_entry(surah, 1, 12.0)})
    loaded = wt.load(config, surah, {1: 5.0})
    assert loaded[1].is_estimated


def test_a_word_count_mismatch_is_refused(config, surah):
    entry = _good_entry(surah, 1, 5.0)
    entry["timings"] = entry["timings"][:-1]
    _store(config, surah, {"1": entry})
    assert wt.load(config, surah, {1: 5.0})[1].is_estimated


def test_changed_wording_is_noticed(config, surah):
    entry = _good_entry(surah, 1, 5.0)
    entry["words"] = list(entry["words"])
    entry["words"][0] = "خدا"
    _store(config, surah, {"1": entry})
    assert wt.load(config, surah, {1: 5.0})[1].is_estimated


def test_out_of_order_timings_are_refused(config, surah):
    entry = _good_entry(surah, 1, 5.0)
    entry["timings"][1], entry["timings"][2] = entry["timings"][2], entry["timings"][1]
    _store(config, surah, {"1": entry})
    assert wt.load(config, surah, {1: 5.0})[1].is_estimated


def test_a_corrupt_file_does_not_stop_the_render(config, surah):
    path = config.word_timings_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    loaded = wt.load(config, surah, {n: 4.0 for n in range(1, 8)})
    assert all(t.is_estimated for t in loaded.values())


def test_describe_names_the_estimated_ayahs(config, surah):
    _store(config, surah, {str(n): _good_entry(surah, n, 5.0) for n in range(2, 8)})
    loaded = wt.load(config, surah, {n: 5.0 for n in range(1, 8)})
    assert "ayah 1" in wt.describe(loaded)


def test_describe_says_so_when_everything_is_published(config, surah):
    _store(config, surah, {str(n): _good_entry(surah, n, 5.0) for n in range(1, 8)})
    loaded = wt.load(config, surah, {n: 5.0 for n in range(1, 8)})
    assert "published" in wt.describe(loaded)
    assert "estimated" not in wt.describe(loaded)


# ---------------------------------------------------------------------------
# Segment rows as the API returns them
# ---------------------------------------------------------------------------

def test_three_column_segments_are_read():
    words = ["a", "b"]
    spans = wt._segments_to_spans([[1, 0, 500], [2, 500, 900]], words)
    assert [s.index for s in spans] == [0, 1]
    assert spans[0].end == pytest.approx(0.5)


def test_four_column_segments_are_read():
    """quran.com prefixes a segment index; the millisecond pair is at the end."""
    words = ["a", "b"]
    spans = wt._segments_to_spans([[0, 1, 60, 610], [1, 2, 620, 1310]], words)
    assert [s.index for s in spans] == [0, 1]
    assert spans[0].start == pytest.approx(0.06)
    assert spans[1].end == pytest.approx(1.31)


def test_segments_for_words_that_do_not_exist_are_dropped():
    spans = wt._segments_to_spans([[1, 0, 500], [9, 500, 900]], ["only one"])
    assert len(spans) == 1


# ---------------------------------------------------------------------------
# Placing the highlights on the timeline
# ---------------------------------------------------------------------------

def _place(config, surah):
    config.content.word_highlight = True
    plan = audiosvc.build_plan(config, surah)
    timeline = build_timeline(config, surah, plan)
    cards = card_windows(config, timeline)
    timings = wt.load(config, surah, {n: c.duration for n, c in plan.recitation.items()})
    return config, timeline, cards, word_windows(config, timeline, timings, cards)


@pytest.fixture
def placed(config_with_audio, surah):
    # The fade-in has to fit inside the padding before the recitation, or the
    # opening word of each ayah is spoken over a card that is still arriving.
    config_with_audio.animation.text_fade_in = config_with_audio.audio.padding_before
    config_with_audio.animation.intro_fade_in = config_with_audio.audio.padding_before
    return _place(config_with_audio, surah)


def test_every_word_gets_a_window(placed, surah):
    _, _, _, words = placed
    expected = sum(len(a.arabic.split()) for a in surah.ayahs)
    assert len(words) == expected


def test_the_shipped_settings_light_every_word(config_with_audio, surah):
    """The delivered config must not swallow the first word of each ayah."""
    from app.config import load_config

    shipped = load_config(
        config_with_audio.root / "config.yaml", root=config_with_audio.root
    )
    _, _, _, words = _place(shipped, surah)
    assert len(words) == sum(len(a.arabic.split()) for a in surah.ayahs)


def test_a_fade_in_longer_than_the_padding_loses_words_and_says_so(
    config_with_audio, surah, caplog
):
    config = config_with_audio
    config.audio.padding_before = 0.20
    config.animation.text_fade_in = 1.60
    with caplog.at_level("WARNING"):
        _, _, _, words = _place(config, surah)

    expected = sum(len(a.arabic.split()) for a in surah.ayahs)
    assert len(words) < expected
    assert "text_fade_in" in caplog.text


def test_windows_are_ordered_and_never_overlap(placed):
    _, _, _, words = placed
    for earlier, later in zip(words, words[1:]):
        assert earlier.start <= later.start
        if earlier.segment_index == later.segment_index:
            assert earlier.end <= later.start + 1e-6


def test_no_window_starts_before_its_recitation(placed):
    _, timeline, _, words = placed
    starts = {s.index: s.audio_start for s in timeline.segments}
    for word in words:
        assert word.start >= starts[word.segment_index] - 1e-6


def test_no_window_outlives_its_card(placed):
    _, timeline, _, words = placed
    ends = {s.index: s.end for s in timeline.segments}
    for word in words:
        assert word.end <= ends[word.segment_index] + 1e-6


def test_a_word_is_never_lit_over_a_card_that_is_still_fading_in(placed):
    """Otherwise the lit word arrives before the ayah it belongs to."""
    _, _, cards, words = placed
    opaque = {c.segment.index: c.start + c.fade_in for c in cards}
    for word in words:
        assert word.start >= opaque[word.segment_index] - 1e-6


def test_a_word_is_never_lit_over_a_card_that_has_started_fading_out(placed):
    _, _, cards, words = placed
    gone = {c.segment.index: c.start + c.fade_out_start for c in cards}
    for word in words:
        assert word.end <= gone[word.segment_index] + 1e-6


def test_nothing_is_placed_when_the_highlight_is_switched_off(config_with_audio, surah):
    config = config_with_audio
    config.content.word_highlight = False
    plan = audiosvc.build_plan(config, surah)
    timeline = build_timeline(config, surah, plan)
    timings = wt.load(config, surah, {n: c.duration for n, c in plan.recitation.items()})
    assert word_windows(config, timeline, timings) == []


def test_only_the_arabic_segments_are_highlighted(placed, surah):
    _, timeline, _, words = placed
    kinds = {s.index: s.kind for s in timeline.segments}
    assert {kinds[w.segment_index] for w in words} == {"arabic"}


def test_playback_speed_compresses_the_windows(config_with_audio, surah):
    """A word said 2 s in is 1.6 s in once the whole timeline runs at 1.25x."""
    config = config_with_audio
    config.content.word_highlight = True
    plan = audiosvc.build_plan(config, surah)
    timings = wt.load(config, surah, {n: c.duration for n, c in plan.recitation.items()})

    config.video.playback_speed = 1.0
    slow = word_windows(config, build_timeline(config, surah, plan), timings)
    config.video.playback_speed = 1.25
    fast = word_windows(config, build_timeline(config, surah, plan), timings)

    assert len(slow) == len(fast)
    assert fast[-1].start < slow[-1].start
