"""Chunked rendering: the pieces must tile the video exactly.

A long surah is rendered in pieces and the pieces are joined without
re-encoding. That is only sound if the pieces fit together perfectly - every
card in exactly one piece, no frame rendered twice, none missed, and each piece
still knowing where it sits in the whole video so the background does not jump
at the seams. These tests hold that invariant.
"""

from __future__ import annotations

import pytest

from app.rendering import animations as anim
from app.rendering.renderer import RenderRequest, Renderer
from app.rendering.timeline import build_timeline, card_windows, word_windows
from app.utils.logging import QVGError


@pytest.fixture
def prepared(config_with_audio, surah, needs_ffmpeg):
    from app.services import audio as audiosvc

    config = config_with_audio
    config.render.chunk_highlights = 6
    config.render.chunk_seconds = 9.0
    config.render.workers = 1
    plan = audiosvc.build_plan(config, surah)
    timeline = build_timeline(config, surah, plan)
    return config, surah, plan, timeline


@pytest.fixture
def planned(prepared, tmp_path):
    config, surah, plan, timeline = prepared
    renderer = Renderer(config, surah, plan, timeline)
    request = RenderRequest(width=192, height=108, fps=10, preview=True,
                            output=tmp_path / "out.mp4")
    windows = card_windows(config, timeline)
    words = word_windows(config, timeline, renderer.word_timings, windows)
    chunks = renderer._plan_chunks(request, windows, words, tmp_path / "parts")
    return renderer, request, timeline, windows, words, chunks


def test_a_long_timeline_is_split_into_several_chunks(planned):
    _, _, _, _, _, chunks = planned
    assert len(chunks) > 1


def test_every_segment_lands_in_exactly_one_chunk(planned):
    _, _, timeline, _, _, chunks = planned
    placed = [s.index for chunk in chunks for s in chunk.timeline.segments]
    assert sorted(placed) == [s.index for s in timeline.segments]


def test_chunk_frames_tile_the_whole_video_without_gap_or_overlap(planned):
    _, request, timeline, _, _, chunks = planned
    total = anim.frame_span(0.0, timeline.total_duration, request.fps)

    assert chunks[0].start_frame == 0
    assert chunks[-1].end_frame == total
    for earlier, later in zip(chunks, chunks[1:]):
        assert earlier.end_frame == later.start_frame
    assert sum(chunk.frames for chunk in chunks) == total


def test_a_chunks_own_clock_starts_at_zero(planned):
    _, request, _, _, _, chunks = planned
    for chunk in chunks:
        first = chunk.timeline.segments[0]
        assert first.start == pytest.approx(0.0, abs=1.0 / request.fps)
        # ... and the chunk is exactly as long as the cards it carries.
        last = chunk.timeline.segments[-1]
        assert last.end == pytest.approx(chunk.timeline.total_duration, abs=0.05)


def test_word_highlights_travel_with_their_card(planned):
    _, _, _, _, words, chunks = planned
    assert sum(len(chunk.words) for chunk in chunks) == len(words)
    for chunk in chunks:
        indices = {s.index for s in chunk.timeline.segments}
        for word in chunk.words:
            assert word.segment_index in indices
            assert -0.5 <= word.start <= chunk.timeline.total_duration + 0.5


def test_the_camera_move_continues_across_a_seam(planned):
    """The push-in is one move over the whole video, not one per chunk."""
    renderer, request, _, _, _, chunks = planned
    renderer.config.background.ken_burns = True
    asset_dir = renderer._asset_dir(request)
    background, is_video = renderer._resolve_background(request, asset_dir)
    layers = renderer._prepare_layers(request, asset_dir)
    cards, highlights = renderer._build_cards(request, asset_dir, chunks[0].words)

    second = chunks[1]
    job = renderer._build_job(
        request, background, is_video, layers, cards,
        second.windows, second.words, highlights, chunk=second,
    )
    graph = job.filter_graph
    assert f"on+{second.start_frame}" in graph
    # The drifting layers are shifted by the same amount, in seconds.
    assert f"t+{second.time_offset:.4f}".rstrip("0").rstrip(".") in graph


def test_a_chunk_writes_picture_and_sound_separately(planned):
    renderer, request, _, _, _, chunks = planned
    asset_dir = renderer._asset_dir(request)
    background, is_video = renderer._resolve_background(request, asset_dir)
    layers = renderer._prepare_layers(request, asset_dir)
    cards, highlights = renderer._build_cards(request, asset_dir, chunks[0].words)

    chunk = chunks[0]
    job = renderer._build_job(
        request, background, is_video, layers, cards,
        chunk.windows, chunk.words, highlights, chunk=chunk,
    )
    assert "-an" in job.output_options
    assert ["-frames:v", str(chunk.frames)] == job.output_options[-2:]
    assert len(job.extra_outputs) == 1
    assert job.extra_outputs[0].path.suffix == ".flac"
    # Both are staged under a leading ~ and renamed once FFmpeg exits cleanly.
    assert job.output.name.startswith("~")
    assert job.extra_outputs[0].path.name.startswith("~")


def test_burned_in_subtitles_are_refused_rather_than_misplaced(prepared, tmp_path):
    """Burning in would need the .ass rebased per chunk; say so instead."""
    config, surah, plan, timeline = prepared
    config.subtitles.burn_in = True
    renderer = Renderer(config, surah, plan, timeline)
    subtitle = tmp_path / "subs.ass"
    subtitle.write_text("[Script Info]\n", encoding="utf-8")
    request = RenderRequest(width=192, height=108, fps=10, preview=True,
                            output=tmp_path / "out.mp4", subtitle_file=subtitle)

    with pytest.raises(QVGError, match="burn_in"):
        renderer.render(request)


@pytest.mark.slow
def test_a_chunked_render_produces_one_file_of_the_right_length(prepared, tmp_path):
    config, surah, plan, timeline = prepared
    renderer = Renderer(config, surah, plan, timeline)
    request = RenderRequest(width=192, height=108, fps=10, preview=True,
                            output=tmp_path / "chunked.mp4")

    report = renderer.render(request)

    assert report.path.is_file()
    assert report.duration == pytest.approx(timeline.total_duration, abs=0.25)
    assert report.has_audio
