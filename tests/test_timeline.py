"""Timeline construction: durations come from the audio, never from constants."""

from __future__ import annotations

import pytest

from app.rendering.timeline import build_timeline, card_windows
from app.services.audio import AudioClip, AudioPlan


@pytest.fixture
def plan(tmp_path) -> AudioPlan:
    """Distinct, easily checkable durations for every clip."""
    plan = AudioPlan(mode="per_ayah")
    for number in range(1, 8):
        plan.recitation[number] = AudioClip(
            path=tmp_path / f"r{number}.wav", duration=float(number), label=f"r{number}"
        )
        plan.urdu[number] = AudioClip(
            path=tmp_path / f"u{number}.wav", duration=number + 0.5, label=f"u{number}"
        )
    return plan


def test_segment_order_is_intro_then_ayah_pairs_then_outro(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    kinds = [s.kind for s in timeline]
    assert kinds[0] == "intro"
    assert kinds[-1] == "outro"
    assert kinds[1:-1] == ["arabic", "urdu"] * 7


def test_each_ayah_completes_before_the_next_begins(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    ayah_order = [(s.ayah_number, s.kind) for s in timeline if s.ayah_number]
    expected = [(n, k) for n in range(1, 8) for k in ("arabic", "urdu")]
    assert ayah_order == expected


def test_segment_count(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    assert len(timeline) == 1 + 7 * 2 + 1


def test_arabic_duration_follows_the_recitation_length(config, surah, plan):
    config.audio.min_arabic_seconds = 0.0
    timeline = build_timeline(config, surah, plan)
    audio = config.audio
    for segment in timeline.of_kind("arabic"):
        expected = audio.padding_before + segment.ayah_number + audio.padding_after
        assert segment.duration == pytest.approx(expected)


def test_urdu_duration_follows_the_narration_length(config, surah, plan):
    config.audio.min_urdu_seconds = 0.0
    timeline = build_timeline(config, surah, plan)
    audio = config.audio
    for segment in timeline.of_kind("urdu"):
        expected = (
            audio.padding_before
            + (segment.ayah_number + 0.5)
            + audio.padding_after
            + (0.0 if segment.ayah_number == 7 else audio.gap_between_ayahs)
        )
        assert segment.duration == pytest.approx(expected)


def test_a_very_short_clip_is_held_to_the_minimum(config, surah, tmp_path):
    """A two-second narration must not flash past before it can be read."""
    plan = AudioPlan(mode="per_ayah")
    for number in range(1, 8):
        plan.recitation[number] = AudioClip(path=tmp_path / "r.wav", duration=5.0)
        plan.urdu[number] = AudioClip(path=tmp_path / "u.wav", duration=2.0)
    config.audio.min_urdu_seconds = 6.5
    config.audio.min_arabic_seconds = 0.0

    timeline = build_timeline(config, surah, plan)
    for segment in timeline.of_kind("urdu"):
        body = segment.duration - (0.0 if segment.ayah_number == 7 else config.audio.gap_between_ayahs)
        assert body == pytest.approx(6.5)
    # The Arabic sections are already long enough, so they are untouched.
    for segment in timeline.of_kind("arabic"):
        assert segment.duration == pytest.approx(
            config.audio.padding_before + 5.0 + config.audio.padding_after
        )


def test_the_minimum_never_shortens_a_long_clip(config, surah, tmp_path):
    plan = AudioPlan(mode="per_ayah")
    for number in range(1, 8):
        plan.recitation[number] = AudioClip(path=tmp_path / "r.wav", duration=20.0)
        plan.urdu[number] = AudioClip(path=tmp_path / "u.wav", duration=25.0)
    config.audio.min_urdu_seconds = 6.5
    config.audio.min_arabic_seconds = 6.5

    timeline = build_timeline(config, surah, plan)
    for segment in timeline.of_kind("arabic"):
        assert segment.duration == pytest.approx(
            config.audio.padding_before + 20.0 + config.audio.padding_after
        )


def test_audio_still_starts_after_the_padding_when_extended(config, surah, tmp_path):
    """Extending a section adds trailing silence - it never delays the audio."""
    plan = AudioPlan(mode="per_ayah")
    for number in range(1, 8):
        plan.recitation[number] = AudioClip(path=tmp_path / "r.wav", duration=5.0)
        plan.urdu[number] = AudioClip(path=tmp_path / "u.wav", duration=1.0)
    config.audio.min_urdu_seconds = 9.0

    timeline = build_timeline(config, surah, plan)
    for segment in timeline.of_kind("urdu"):
        assert segment.audio_start == pytest.approx(segment.start + config.audio.padding_before)
        assert segment.audio_end < segment.end


def test_no_two_ayahs_share_a_duration(config, surah, plan):
    """A hard-coded per-ayah length would make these collapse to one value."""
    timeline = build_timeline(config, surah, plan)
    durations = {round(s.duration, 4) for s in timeline.of_kind("arabic")}
    assert len(durations) == 7


def test_segments_are_contiguous(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    for previous, current in zip(timeline.segments, timeline.segments[1:]):
        assert current.start == pytest.approx(previous.end)


def test_total_duration_is_the_sum_of_segments(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    assert timeline.total_duration == pytest.approx(sum(s.duration for s in timeline))


def test_audio_starts_after_the_leading_padding(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    for segment in timeline.with_audio:
        assert segment.audio_start == pytest.approx(
            segment.start + config.audio.padding_before
        )
        assert segment.audio_end <= segment.end + 1e-6


def test_audio_never_overlaps_between_segments(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    spans = sorted((s.audio_start, s.audio_end) for s in timeline.with_audio)
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert start >= end - 1e-9


def test_intro_and_outro_carry_no_audio(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    assert timeline.of_kind("intro")[0].audio is None
    assert timeline.of_kind("outro")[0].audio is None
    assert len(timeline.with_audio) == 14


def test_subtitle_text_matches_the_ayah(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    for segment in timeline.of_kind("arabic"):
        assert segment.subtitle_text == surah.ayah(segment.ayah_number).arabic
        assert segment.subtitle_language == "ar"
    for segment in timeline.of_kind("urdu"):
        assert segment.subtitle_text == surah.ayah(segment.ayah_number).urdu_translation
        assert segment.subtitle_language == "ur"


def test_max_ayahs_truncates(config, surah, plan):
    timeline = build_timeline(config, surah, plan, max_ayahs=2)
    assert len(timeline.of_kind("arabic")) == 2
    assert {s.ayah_number for s in timeline.ayah_segments} == {1, 2}


def test_animation_speed_scales_intro_and_outro(config, surah, plan):
    baseline = build_timeline(config, surah, plan)
    config.animation.speed = 2.0
    faster = build_timeline(config, surah, plan)
    assert faster.of_kind("intro")[0].duration == pytest.approx(
        baseline.of_kind("intro")[0].duration / 2
    )
    # Ayah durations are audio-driven and must not change with animation speed.
    assert faster.of_kind("arabic")[0].duration == pytest.approx(
        baseline.of_kind("arabic")[0].duration
    )


def test_padding_changes_flow_through(config, surah, plan):
    config.audio.min_arabic_seconds = 0.0
    config.audio.min_urdu_seconds = 0.0
    baseline = build_timeline(config, surah, plan).total_duration
    config.audio.padding_before += 1.0
    longer = build_timeline(config, surah, plan).total_duration
    assert longer == pytest.approx(baseline + 14.0)


def test_summary_rows_cover_every_segment(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    assert len(timeline.summary_rows()) == len(timeline)


# ---------------------------------------------------------------------------
# Card windows
# ---------------------------------------------------------------------------

def test_one_window_per_segment(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    assert len(card_windows(config, timeline)) == len(timeline)


def test_windows_tile_the_whole_timeline(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    windows = card_windows(config, timeline)
    assert windows[0].start == 0.0
    assert sum(w.duration for w in windows) == pytest.approx(timeline.total_duration)
    for previous, current in zip(windows, windows[1:]):
        assert current.start == pytest.approx(previous.end)


def test_fades_fit_inside_their_window(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    for window in card_windows(config, timeline):
        assert window.fade_in >= 0
        assert window.fade_out >= 0
        assert window.fade_in + window.fade_out + window.hold_after <= window.duration
        assert window.fade_out_start >= window.fade_in
        assert window.fade_out_start + window.fade_out <= window.duration + 1e-6


def test_a_card_is_fully_gone_before_the_next_appears(config, surah, plan):
    """The beat between ayahs is what stops two ayahs being legible at once."""
    timeline = build_timeline(config, surah, plan)
    windows = card_windows(config, timeline)
    for window in windows[:-1]:
        assert window.hold_after > 0
        assert window.fade_out_start + window.fade_out <= window.duration + 1e-6


def test_last_card_has_no_trailing_hold(config, surah, plan):
    timeline = build_timeline(config, surah, plan)
    assert card_windows(config, timeline)[-1].hold_after == 0.0


def test_very_short_segments_still_produce_valid_fades(config, surah, tmp_path):
    plan = AudioPlan(mode="per_ayah")
    for number in range(1, 8):
        plan.recitation[number] = AudioClip(path=tmp_path / "a.wav", duration=0.05)
        plan.urdu[number] = AudioClip(path=tmp_path / "b.wav", duration=0.05)
    config.audio.padding_before = 0.0
    config.audio.padding_after = 0.0
    config.audio.gap_between_ayahs = 0.0
    timeline = build_timeline(config, surah, plan)
    for window in card_windows(config, timeline):
        assert window.fade_in + window.fade_out + window.hold_after <= window.duration + 1e-9


# ---------------------------------------------------------------------------
# Playback speed
# ---------------------------------------------------------------------------

def test_playback_speed_compresses_the_whole_timeline(config, surah, plan):
    baseline = build_timeline(config, surah, plan).total_duration
    config.video.playback_speed = 1.25
    faster = build_timeline(config, surah, plan).total_duration
    assert faster == pytest.approx(baseline / 1.25, rel=1e-6)


def test_playback_speed_shortens_every_segment_proportionally(config, surah, plan):
    baseline = {s.card_id: s.duration for s in build_timeline(config, surah, plan)}
    config.video.playback_speed = 1.25
    for segment in build_timeline(config, surah, plan):
        assert segment.duration == pytest.approx(baseline[segment.card_id] / 1.25, rel=1e-6)


def test_a_sped_up_clip_still_fits_inside_its_segment(config, surah, plan):
    config.video.playback_speed = 1.25
    for segment in build_timeline(config, surah, plan).with_audio:
        assert segment.audio_end <= segment.end + 1e-6
        assert segment.audio.played_duration(1.25) == pytest.approx(
            segment.audio.duration / 1.25, rel=1e-6
        )


def test_speed_one_leaves_the_timeline_untouched(config, surah, plan):
    baseline = build_timeline(config, surah, plan).total_duration
    config.video.playback_speed = 1.0
    assert build_timeline(config, surah, plan).total_duration == pytest.approx(baseline)


# ---------------------------------------------------------------------------
# Silent Urdu (narration switched off)
# ---------------------------------------------------------------------------

def test_urdu_cards_are_silent_when_narration_is_off(config, surah, plan):
    config.audio.urdu_narration = False
    plan.urdu.clear()
    timeline = build_timeline(config, surah, plan)

    urdu = timeline.of_kind("urdu")
    assert len(urdu) == 7
    assert all(s.audio is None for s in urdu)
    # Only the seven recitations remain on the audio track.
    assert len(timeline.with_audio) == 7
    assert all(s.kind == "arabic" for s in timeline.with_audio)


def test_a_silent_urdu_card_is_held_for_its_reading_time(config, surah, plan):
    """With nothing spoken, a long translation must stay up longer than a short
    one - a flat minimum would leave the long ayahs unreadable."""
    config.audio.urdu_narration = False
    config.audio.min_urdu_seconds = 0.0
    config.audio.urdu_seconds_per_word = 0.5
    plan.urdu.clear()

    timeline = build_timeline(config, surah, plan)
    by_ayah = {s.ayah_number: s for s in timeline.of_kind("urdu")}
    words = {a.number: len(a.urdu_translation.split()) for a in surah.ayahs}

    longest = max(words, key=lambda n: words[n])
    shortest = min(words, key=lambda n: words[n])
    assert words[longest] > words[shortest]
    assert by_ayah[longest].duration > by_ayah[shortest].duration


def test_the_reading_hold_still_respects_the_minimum(config, surah, plan):
    config.audio.urdu_narration = False
    config.audio.min_urdu_seconds = 9.0
    config.audio.urdu_seconds_per_word = 0.01
    plan.urdu.clear()
    for segment in build_timeline(config, surah, plan).of_kind("urdu"):
        body = segment.duration - (
            0.0 if segment.ayah_number == 7 else config.audio.gap_between_ayahs
        )
        assert body >= 9.0 - 1e-6


def test_subtitles_skip_the_silent_urdu_cards(config, surah, plan):
    """A cue with no audio would have no timing to hang on."""
    from app.services import subtitles as subsvc

    config.audio.urdu_narration = False
    plan.urdu.clear()
    timeline = build_timeline(config, surah, plan)
    cues = list(subsvc.iter_cues(config, timeline))
    assert all(end > start for start, end, _, _ in cues)


# ---------------------------------------------------------------------------
# Arabic-only (content.urdu_translation off)
# ---------------------------------------------------------------------------

def test_no_urdu_sections_when_the_translation_is_off(config, surah, plan):
    config.content.urdu_translation = False
    timeline = build_timeline(config, surah, plan)

    assert timeline.of_kind("urdu") == []
    assert [s.kind for s in timeline] == ["intro"] + ["arabic"] * 7 + ["outro"]
    assert len(timeline) == 9
    # Only the recitations remain on the audio track.
    assert len(timeline.with_audio) == 7


def test_the_gap_moves_onto_the_arabic_card_when_there_is_no_urdu(config, surah, plan):
    """The breath between ayahs used to sit at the end of the Urdu section, so
    without one it has to move or the ayahs would butt straight together."""
    config.content.urdu_translation = False
    config.audio.min_arabic_seconds = 0.0
    timeline = build_timeline(config, surah, plan)
    audio = config.audio

    for segment in timeline.of_kind("arabic"):
        expected = audio.padding_before + segment.ayah_number + audio.padding_after
        if segment.ayah_number != 7:
            expected += audio.gap_between_ayahs
        assert segment.duration == pytest.approx(expected)


def test_the_last_ayah_gets_no_trailing_gap(config, surah, plan):
    config.content.urdu_translation = False
    config.audio.min_arabic_seconds = 0.0
    timeline = build_timeline(config, surah, plan)
    audio = config.audio
    last = [s for s in timeline.of_kind("arabic") if s.ayah_number == 7][0]
    assert last.duration == pytest.approx(audio.padding_before + 7 + audio.padding_after)


def test_arabic_only_still_covers_every_ayah_in_order(config, surah, plan):
    config.content.urdu_translation = False
    timeline = build_timeline(config, surah, plan)
    assert [s.ayah_number for s in timeline.ayah_segments] == [1, 2, 3, 4, 5, 6, 7]


def test_subtitles_are_arabic_only_when_the_translation_is_off(config, surah, plan):
    from app.services import subtitles as subsvc

    config.content.urdu_translation = False
    timeline = build_timeline(config, surah, plan)
    cues = list(subsvc.iter_cues(config, timeline))
    assert len(cues) == 7
    assert all(language == "ar" for _, _, _, language in cues)


# ---------------------------------------------------------------------------
# Tight pacing: continuous recitation, no title card
# ---------------------------------------------------------------------------

def test_the_pause_between_recitations_is_the_three_paddings(config, surah, plan):
    """What a listener hears between ayahs is padding_after + gap + padding_before."""
    config.content.urdu_translation = False
    config.audio.padding_before = 0.20
    config.audio.padding_after = 0.20
    config.audio.gap_between_ayahs = 0.0
    config.audio.min_arabic_seconds = 0.0

    timeline = build_timeline(config, surah, plan)
    spans = [(s.audio_start, s.audio_end) for s in timeline.with_audio]
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert start - end == pytest.approx(0.40, abs=1e-6)


def test_zero_gap_really_means_no_extra_pause(config, surah, plan):
    config.content.urdu_translation = False
    config.audio.gap_between_ayahs = 0.0
    config.audio.min_arabic_seconds = 0.0
    timeline = build_timeline(config, surah, plan)
    audio = config.audio
    for segment in timeline.of_kind("arabic"):
        assert segment.duration == pytest.approx(
            audio.padding_before + segment.ayah_number + audio.padding_after
        )


def test_no_intro_segment_when_the_title_card_is_off(config, surah, plan):
    config.animation.intro_duration = 0.0
    timeline = build_timeline(config, surah, plan)
    assert timeline.of_kind("intro") == []
    assert timeline.segments[0].kind == "arabic"
    assert timeline.segments[0].start == 0.0


def test_the_first_recitation_starts_almost_immediately_without_an_intro(config, surah, plan):
    config.animation.intro_duration = 0.0
    config.audio.padding_before = 0.20
    timeline = build_timeline(config, surah, plan)
    assert timeline.with_audio[0].audio_start == pytest.approx(0.20)


def test_a_dissolve_is_never_squeezed_into_a_cut(config, surah, plan):
    """With padding this tight the tail clamp would shrink the fade to about
    three frames, which reads as a hard edit. The floor stops that."""
    config.content.urdu_translation = False
    config.audio.padding_before = 0.20
    config.audio.padding_after = 0.20
    config.audio.gap_between_ayahs = 0.0
    config.animation.min_text_fade_out = 0.30

    for window in card_windows(config, build_timeline(config, surah, plan)):
        if window.segment.audio is None:
            continue
        assert window.fade_out >= 0.30 - 1e-6


def test_generous_padding_still_keeps_the_fade_off_the_audio(config, surah, plan):
    """The floor must not override the guarantee when there IS room for it."""
    config.content.urdu_translation = False
    config.audio.padding_after = 2.0
    config.audio.gap_between_ayahs = 0.0

    for window in card_windows(config, build_timeline(config, surah, plan)):
        segment = window.segment
        if segment.audio is None:
            continue
        audio_end_relative = segment.audio_end - segment.start
        assert window.fade_out_start >= audio_end_relative - 1e-6


def test_ramps_always_fit_inside_the_card(config, surah, plan):
    config.content.urdu_translation = False
    config.audio.padding_before = 0.0
    config.audio.padding_after = 0.0
    config.audio.gap_between_ayahs = 0.0
    for window in card_windows(config, build_timeline(config, surah, plan)):
        assert window.fade_in + window.fade_out + window.hold_after <= window.duration + 1e-6
        assert window.fade_out_start >= 0.0


def test_the_outro_can_be_removed_entirely(config, surah, plan):
    config.animation.outro_duration = 0.0
    timeline = build_timeline(config, surah, plan)
    assert timeline.of_kind("outro") == []
    assert timeline.segments[-1].kind in ("arabic", "urdu")


def test_a_short_outro_still_gives_the_last_card_room_to_fade(config, surah, plan):
    """The tail exists so the last ayah dissolves rather than being cut off."""
    config.content.urdu_translation = False
    config.animation.outro_duration = 1.5
    timeline = build_timeline(config, surah, plan)
    windows = card_windows(config, timeline)

    last_ayah = [w for w in windows if w.segment.kind == "arabic"][-1]
    assert last_ayah.fade_out > 0
    assert last_ayah.fade_out_start + last_ayah.fade_out <= last_ayah.duration + 1e-6
