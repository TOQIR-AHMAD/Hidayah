"""FFmpeg command generation, card composition and end-to-end output checks."""

from __future__ import annotations

import re

import pytest

from app.rendering import animations as anim
from app.rendering.compositions import CardBuilder, urdu_number
from app.rendering.renderer import RenderRequest, Renderer
from app.rendering.timeline import build_timeline, card_windows, word_windows
from app.services import video as videosvc
from app.utils.logging import QVGError


@pytest.fixture
def prepared(config_with_audio, surah, needs_ffmpeg):
    from app.services import audio as audiosvc
    plan = audiosvc.build_plan(config_with_audio, surah)
    timeline = build_timeline(config_with_audio, surah, plan)
    return config_with_audio, surah, plan, timeline


@pytest.fixture
def job(prepared, tmp_path):
    config, surah, plan, timeline = prepared
    renderer = Renderer(config, surah, plan, timeline)
    request = RenderRequest(width=192, height=108, fps=10, preview=True,
                            output=tmp_path / "out.mp4")
    asset_dir = renderer._asset_dir(request)
    background, is_video = renderer._resolve_background(request, asset_dir)
    layers = renderer._prepare_layers(request, asset_dir)
    windows = card_windows(config, timeline)
    words = word_windows(config, timeline, renderer.word_timings, windows)
    cards, highlights = renderer._build_cards(request, asset_dir, words)
    return renderer._build_job(
        request, background, is_video, layers, cards, windows, words, highlights
    )


# ---------------------------------------------------------------------------
# Filter expression helpers
# ---------------------------------------------------------------------------

def test_positive_mod_never_returns_a_negative_offset():
    expression = anim.positive_mod("-9*t", 300)
    assert expression.count("mod(") == 2
    assert "+300" in expression


def test_wrapping_drift_produces_negative_offsets():
    drift = anim.wrapping_drift(30.0, -30.0, 300, 300, 30)
    assert drift.x.startswith("-(")
    assert drift.y.startswith("-(")


def test_drift_speeds_snap_to_whole_pixels_per_frame():
    """Regression: overlay positions on integer pixels, so a layer moving less
    than 1 px per frame freezes for several frames and then jumps. Measured at
    9 px/s and 30 fps: 45 of 59 frames identical. Snapped to 1 px/frame the
    motion measured perfectly even - burst 1.0x, no still frames."""
    assert anim.quantise_speed(30.0, 30) == 30.0     # already exact
    assert anim.quantise_speed(60.0, 30) == 60.0     # 2 px/frame
    assert anim.quantise_speed(46.0, 30) == 60.0     # rounds to the nearest
    assert anim.quantise_speed(9.0, 30) == 30.0      # never rounds down to a stop
    assert anim.quantise_speed(-5.0, 30) == -30.0    # sign preserved
    assert anim.quantise_speed(0.0, 30) == 0.0       # off stays off


def test_drift_speed_follows_the_frame_rate():
    """The preview runs at 24 fps, so its smooth speeds differ from 30 fps."""
    assert anim.quantise_speed(30.0, 24) == 24.0
    assert anim.quantise_speed(48.0, 24) == 48.0


def test_a_quantised_drift_lands_exactly_on_its_wrap_period():
    """Whole-pixel steps and a whole-pixel period means the seam is exact."""
    fps, period = 30, 300.0
    speed = anim.quantise_speed(30.0, fps)
    per_frame = speed / fps
    assert per_frame == int(per_frame)
    assert period % per_frame == 0


def test_a_disabled_drift_emits_a_constant():
    drift = anim.wrapping_drift(0.0, 0.0, 300, 300, 30)
    assert drift.x == "0" and drift.y == "0"


def test_sine_drift_centres_the_layer():
    drift = anim.sine_drift(50, 40)
    assert "(W-w)/2" in drift.x
    assert "(H-h)/2" in drift.y


def test_alpha_fade_builds_both_ramps():
    expression = anim.alpha_fade(0.7, 0.55, 4.0)
    assert "fade=t=in:st=0:d=0.7:alpha=1" in expression
    assert "fade=t=out:st=4:d=0.55:alpha=1" in expression


def test_alpha_fade_omits_zero_length_ramps():
    assert "fade=t=in" not in anim.alpha_fade(0.0, 0.5, 3.0)
    assert "fade=t=out" not in anim.alpha_fade(0.5, 0.0, 3.0)


def test_text_rise_is_zero_when_disabled():
    assert anim.text_rise(0.0, 0.0, 1.4) == "0"
    assert anim.text_rise(0.0, 16.0, 0.0) == "0"


def test_text_rise_references_the_card_start():
    assert "t-12.5" in anim.text_rise(12.5, 16.0, 1.4)


def test_clip_filter_places_the_audio_at_its_cue():
    expression = anim.clip_filter(
        start=7.6, gain_db=3.25, fade_in=0.08, fade_out=0.18,
        duration=5.0, sample_rate=48000,
    )
    assert "adelay=7600:all=1" in expression
    assert "volume=3.25dB" in expression
    assert "afade=t=in:st=0:d=0.08" in expression
    assert "afade=t=out:st=4.82:d=0.18" in expression
    assert "sample_rates=48000" in expression


def test_clip_filter_pins_the_channel_layout():
    """amix takes its layout from the first input, so every clip must agree.

    Without this a mono recitation silently folds a stereo narration or
    ambience bed to mono, and -ac 2 re-expands it to dual mono afterwards, so
    the file still reports two channels and nothing is logged.
    """
    expression = anim.clip_filter(0.0, 0.0, 0.0, 0.0, 5.0, 48000)
    assert "channel_layouts=stereo" in expression


def test_clip_filter_omits_a_zero_gain():
    assert "volume=" not in anim.clip_filter(0.0, 0.0, 0.0, 0.0, 5.0, 48000)


def test_clip_filter_never_applies_a_compressor():
    """The recitation is gain-matched only - nothing may reshape it."""
    expression = anim.clip_filter(1.0, -4.0, 0.05, 0.1, 5.0, 48000)
    for forbidden in ("compand", "acompressor", "alimiter", "loudnorm", "atempo", "asetrate"):
        assert forbidden not in expression


def test_background_grade_darkens_with_the_output_white_point():
    steps = ",".join(anim.background_grade(0.30, 1.04, 0.94, -0.01, 0.42))
    assert "lutyuv=" in steps
    assert "16+(val-16)*0.7" in steps      # luma anchored on the black point
    assert "128+(val-128)*0.7" in steps    # chroma anchored on neutral
    assert "vignette=angle=" in steps


def test_the_grade_never_leaves_the_pinned_yuv_format():
    """colorlevels is RGB-only: using it here forced a 420 -> rgb -> 444 -> 420
    round trip on every frame, which is what pinning the format exists to
    prevent. Measured at 1080p: 23 fps with colorlevels, 45 fps without."""
    steps = ",".join(anim.background_grade(0.55, 1.04, 0.94, -0.01, 0.62))
    for rgb_only in ("colorlevels", "colorbalance", "curves", "colorchannelmixer"):
        assert rgb_only not in steps


def test_background_grade_can_be_fully_disabled():
    assert anim.background_grade(0.0, 1.0, 1.0, 0.0, 0.0) == []


def test_stills_are_blurred_once_rather_than_every_frame():
    """A still backdrop is softened in Pillow, so no gblur reaches the graph."""
    steps = ",".join(anim.background_grade(0.30, 1.04, 0.94, -0.01, 0.42))
    assert "gblur" not in steps
    assert anim.gaussian_blur(6) == "gblur=sigma=6"


def test_video_backgrounds_keep_the_per_frame_blur(prepared, tmp_path, monkeypatch):
    """A looped clip cannot be pre-blurred, so it keeps gblur in the chain."""
    config, surah, plan, timeline = prepared
    renderer = Renderer(config, surah, plan, timeline)
    request = RenderRequest(192, 108, 10, True, tmp_path / "v.mp4")
    asset_dir = renderer._asset_dir(request)
    fake_clip = tmp_path / "loop.mp4"
    fake_clip.write_bytes(b"")
    job = renderer._build_job(
        request, fake_clip, True,
        renderer._prepare_layers(request, asset_dir),
        renderer._build_cards(request, asset_dir, [])[0],
        card_windows(config, timeline),
    )
    assert "gblur=sigma=" in job.filter_graph


def test_still_source_holds_one_decoded_frame():
    expression = anim.still_source(30, 375)
    assert "loop=loop=-1:size=1:start=0" in expression
    assert "trim=end_frame=375" in expression


def test_still_source_sets_the_timebase_before_the_timestamps():
    """setpts divides by the INPUT timebase, and the image demuxer hands a PNG
    1/25 whatever rate is asked for - so without settb a 30 fps stream lands on
    25 distinct timestamps a second and the alpha ramps step."""
    expression = anim.still_source(30, 100)
    assert expression.index("settb=1/30") < expression.index("setpts=N/30/TB")


def test_frame_spans_cancel_instead_of_accumulating():
    """Rounding each boundary, not each duration, is what stops the card track
    sliding later and later against the audio."""
    bounds = [0.0, 7.0, 14.73, 21.93, 29.16, 37.54, 43.81, 51.01,
              57.31, 64.51, 72.86, 80.24, 87.48, 94.68, 109.56, 119.12, 128.12]
    spans = [anim.frame_span(bounds[i], bounds[i + 1], 30) for i in range(len(bounds) - 1)]
    assert sum(spans) == anim.frame_span(0.0, bounds[-1], 30)


def test_frame_span_is_always_at_least_one_frame():
    assert anim.frame_span(1.0, 1.0, 30) == 1
    assert anim.frame_span(1.0, 1.001, 30) == 1


def test_the_rise_expression_tests_the_latest_card_first():
    """Regression: the chain used to be wrapped in reverse, which put
    `gte(t, 0)` outermost. That is true on every frame, so every other branch
    was dead code and only the very first card ever rose."""
    class Window:
        def __init__(self, start):
            self.start = start

    class Animation:
        text_rise = 16.0
        text_rise_duration = 1.4
        speed = 1.0

        def scaled(self, value):
            return value

    windows = [Window(0.0), Window(7.0), Window(14.73)]
    expression = Renderer._per_card_rise(windows, Animation(), 1080)

    # The outermost condition must be the LAST card, not the first.
    assert expression.startswith("if(gte(t,14.7300)")
    # ...and every card must still appear somewhere in the chain.
    for window in windows:
        assert f"gte(t,{window.start:.4f})" in expression


def test_every_card_gets_its_own_rise_in_the_graph(job, prepared):
    _, _, _, timeline = prepared
    graph = job.filter_graph
    for segment in timeline.segments:
        assert f"gte(t,{segment.start:.4f})" in graph


def test_windows_paths_are_escaped_for_the_filter_parser():
    escaped = anim.escape_filter_path(r"C:\Users\me\subs\out.ass")
    assert escaped == r"C\:/Users/me/subs/out.ass"
    assert "\\U" not in escaped


def test_burn_subtitles_names_the_file_and_the_fonts_dir():
    expression = anim.burn_subtitles(r"C:\subs\a.ass", r"C:\fonts")
    assert expression.startswith("ass=filename='C\\:/subs/a.ass'")
    assert "fontsdir='C\\:/fonts'" in expression


# ---------------------------------------------------------------------------
# Job / filter graph construction
# ---------------------------------------------------------------------------

def test_job_maps_both_streams(job):
    assert job.maps == ["-map", "[vout]", "-map", "[aout]"]


def test_job_has_one_input_per_asset_and_clip(job, prepared):
    config, surah, _, timeline = prepared
    # background + pattern + light + 2 dust + frame = 6, then one input for
    # every card, every audio clip, and every highlighted word.
    words = sum(len(ayah.arabic.split()) for ayah in surah.ayahs)
    expected = 6 + len(timeline) + 14
    if config.content.word_highlight:
        expected += words
    assert len(job.inputs) == expected


def test_every_audio_clip_is_delayed_to_its_cue(job, prepared):
    _, _, _, timeline = prepared
    delays = sorted(int(m) for m in re.findall(r"adelay=(\d+):all=1", job.filter_graph))
    expected = sorted(int(round(s.audio_start * 1000)) for s in timeline.with_audio)
    assert delays == expected


def test_audio_streams_are_mixed_without_renormalising(job):
    assert "amix=inputs=14:duration=longest:normalize=0" in job.filter_graph


def test_video_is_trimmed_to_the_timeline(job, prepared):
    _, _, _, timeline = prepared
    assert f"trim=duration={timeline.total_duration:.3f}" in job.filter_graph
    assert f"atrim=duration={timeline.total_duration:.3f}" in job.filter_graph


def test_all_cards_are_concatenated(job, prepared):
    _, _, _, timeline = prepared
    assert f"concat=n={len(timeline)}:v=1:a=0[cards]" in job.filter_graph


def test_compositing_format_is_pinned(job):
    assert job.filter_graph.count(f"format={anim.OVERLAY_FORMAT}") >= 5
    assert "format=auto" not in job.filter_graph


def test_output_options_carry_the_codec_and_duration(job, prepared):
    config, _, _, timeline = prepared
    options = job.output_options
    assert "libx264" in options
    assert "-pix_fmt" in options and "yuv420p" in options
    assert f"{timeline.total_duration:.3f}" in options


def test_metadata_records_the_reciter(config, surah):
    config.reciter.name = "Test Reciter"
    config.reciter.source = "Test Source"
    options = videosvc.metadata_options(config, "Al-Fatihah")
    joined = " ".join(options)
    assert "Test Reciter" in joined
    assert "genre=Quran" in joined


def test_metadata_omits_placeholder_credits(config, surah):
    joined = " ".join(videosvc.metadata_options(config, "Al-Fatihah"))
    assert "RECITER_NAME" not in joined


def test_filter_graph_is_written_to_a_script_file(job, tmp_path):
    script = tmp_path / "graph.txt"
    args = job.build_args(filter_script=script)
    assert "-filter_complex_script" in args
    assert script.is_file()
    # Windows caps a command line at ~32k characters; the graph must not be inline.
    assert all(len(str(arg)) < 4096 for arg in args)


def test_burn_in_is_absent_by_default(job):
    assert "ass=filename=" not in job.filter_graph


def test_burn_in_adds_the_ass_filter(prepared, tmp_path):
    from app.services import subtitles as subsvc

    config, surah, plan, timeline = prepared
    subtitle_file = subsvc.write_ass(
        config, timeline, surah, tmp_path / "burn.ass", 192, 108
    )
    renderer = Renderer(config, surah, plan, timeline)
    request = RenderRequest(
        width=192, height=108, fps=10, preview=True,
        output=tmp_path / "out.mp4", subtitle_file=subtitle_file,
    )
    asset_dir = renderer._asset_dir(request)
    background, is_video = renderer._resolve_background(request, asset_dir)
    job = renderer._build_job(
        request, background, is_video,
        renderer._prepare_layers(request, asset_dir),
        renderer._build_cards(request, asset_dir, [])[0],
        card_windows(config, timeline),
    )
    assert "ass=filename=" in job.filter_graph
    assert "fontsdir=" in job.filter_graph


def test_preview_and_final_use_different_encoder_settings(config):
    preview = videosvc.encoder_options(config, preview=True)
    final = videosvc.encoder_options(config, preview=False)
    assert preview[preview.index("-crf") + 1] == str(config.preview.crf)
    assert final[final.index("-crf") + 1] == str(config.video.crf)


def test_missing_background_file_is_reported(config_with_audio, surah, tmp_path, needs_ffmpeg):
    from app.services import audio as audiosvc
    config_with_audio.background.source = "does_not_exist.jpg"
    plan = audiosvc.build_plan(config_with_audio, surah)
    timeline = build_timeline(config_with_audio, surah, plan)
    renderer = Renderer(config_with_audio, surah, plan, timeline)
    request = RenderRequest(192, 108, 10, True, tmp_path / "x.mp4")
    with pytest.raises(QVGError) as excinfo:
        renderer._resolve_background(request, renderer._asset_dir(request))
    assert "does_not_exist.jpg" in excinfo.value.message


# ---------------------------------------------------------------------------
# Card composition
# ---------------------------------------------------------------------------

def test_cards_are_full_frame_and_transparent(config, surah, real_fonts_dir):
    builder = CardBuilder(config, surah, 640, 360)
    card = builder.arabic_card(surah.ayah(1))
    assert card.size == (640, 360)
    assert card.mode == "RGBA"
    assert card.getchannel("A").getextrema() == (0, 255)


@pytest.mark.parametrize("number", range(1, 8))
def test_every_ayah_card_has_ink_inside_the_margins(config, surah, real_fonts_dir, number):
    builder = CardBuilder(config, surah, 640, 360)
    for card in (builder.arabic_card(surah.ayah(number)), builder.urdu_card(surah.ayah(number))):
        bbox = card.getchannel("A").getbbox()
        assert bbox is not None
        left, top, right, bottom = bbox
        assert left >= 0 and top >= 0
        assert right <= 640 and bottom <= 360


def test_intro_and_outro_cards_render(config, surah, real_fonts_dir):
    builder = CardBuilder(config, surah, 640, 360)
    for card in (builder.intro_card(), builder.outro_card()):
        assert card.size == (640, 360)
        assert card.getchannel("A").getbbox() is not None


def test_arabic_and_urdu_cards_differ(config, surah, real_fonts_dir):
    builder = CardBuilder(config, surah, 640, 360)
    arabic = builder.arabic_card(surah.ayah(1)).tobytes()
    urdu = builder.urdu_card(surah.ayah(1)).tobytes()
    assert arabic != urdu


def test_urdu_digits():
    assert urdu_number(1) == "۱"
    assert urdu_number(7) == "۷"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_full_render_produces_a_playable_file(config_with_audio, surah, tmp_path, needs_ffmpeg,
                                              real_fonts_dir):
    """A miniature render of the whole pipeline, verified with FFmpeg."""
    from app.services import audio as audiosvc

    config = config_with_audio
    config.animation.intro_duration = 1.0
    config.animation.outro_duration = 1.0
    config.audio.padding_before = 0.1
    config.audio.padding_after = 0.2
    config.audio.gap_between_ayahs = 0.1
    config.preview.crf = 40
    config.preview.preset = "ultrafast"

    plan = audiosvc.build_plan(config, surah)
    timeline = build_timeline(config, surah, plan, max_ayahs=2)
    renderer = Renderer(config, surah, plan, timeline)
    request = RenderRequest(
        width=192, height=108, fps=10, preview=True, output=tmp_path / "mini.mp4"
    )

    report = renderer.render(request)

    assert report.path.is_file()
    assert report.has_video and report.has_audio
    assert report.resolution == "192x108"
    assert report.duration == pytest.approx(timeline.total_duration, abs=0.5)
    assert report.size_bytes > 20_000


def test_verify_output_rejects_a_missing_file(tmp_path):
    with pytest.raises(QVGError):
        videosvc.verify_output(tmp_path / "nothing.mp4", 10.0)


def test_verify_output_rejects_a_truncated_file(tmp_path):
    stub = tmp_path / "tiny.mp4"
    stub.write_bytes(b"\x00" * 100)
    with pytest.raises(QVGError) as excinfo:
        videosvc.verify_output(stub, 10.0)
    assert "empty file" in excinfo.value.message


def test_concat_list_uses_forward_slashes(tmp_path):
    listing = videosvc.concat_demuxer_file(
        [tmp_path / "a.mp4", tmp_path / "b.mp4"], tmp_path / "list.txt"
    )
    body = listing.read_text(encoding="utf-8")
    assert body.startswith("ffconcat version 1.0")
    assert "\\" not in body
    assert body.count("file '") == 2


def test_the_encode_goes_to_a_temporary_name_first(job, tmp_path):
    """An interrupted render must not leave a truncated file at the published
    path, looking finished."""
    assert job.final_output == tmp_path / "out.mp4"
    assert job.output != job.final_output
    assert job.output.name.endswith(".part.mp4")
    # FFmpeg picks its muxer from the extension, so the real one must be last.
    assert job.output.suffix == job.final_output.suffix


def test_atempo_is_used_for_speed_not_a_resample():
    """atempo changes tempo but leaves pitch alone, so the reciter is not
    transposed. A resample (asetrate) would raise the pitch of the voice."""
    expression = anim.clip_filter(0.0, 0.0, 0.0, 0.0, 6.0, 48000, speed=1.25)
    assert "atempo=1.25" in expression
    for transposing in ("asetrate", "rubberband=pitch", "aresample=96000"):
        assert transposing not in expression


def test_speed_one_leaves_the_audio_chain_untouched():
    expression = anim.clip_filter(0.0, 0.0, 0.0, 0.0, 6.0, 48000, speed=1.0)
    assert "atempo" not in expression


def test_fades_are_placed_in_the_re_timed_scale():
    """atempo comes before the fades, so the fade-out sits at duration/speed."""
    expression = anim.clip_filter(
        0.0, 0.0, 0.0, 0.5, duration=10.0, sample_rate=48000, speed=2.0
    )
    assert expression.index("atempo") < expression.index("afade=t=out")
    assert "afade=t=out:st=4.5" in expression   # 10/2 - 0.5


def test_timestamps_are_reset_before_the_mix_is_trimmed(job):
    """Regression: atrim measures against the incoming timestamps, and atempo
    rescales those - so `apad,atrim,asetpts` discarded the whole mix and shipped
    a silent track. asetpts has to come first."""
    graph = job.filter_graph
    tail = next(line for line in graph.split(";") if "[aout]" in line)
    assert tail.index("asetpts") < tail.index("atrim"), tail


def test_verify_output_rejects_a_silent_track(tmp_path, needs_ffmpeg):
    """A silent render is a valid file and an unusable one - it must not pass."""
    from app.utils import ffmpeg as ff

    silent = tmp_path / "silent.mp4"
    ff.run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=black:s=192x108:d=2:r=10",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(silent),
    ], description="silent fixture")

    with pytest.raises(QVGError) as excinfo:
        videosvc.verify_output(silent, 2.0)
    assert "silent" in excinfo.value.message


def test_verify_output_accepts_a_track_with_signal(tmp_path, needs_ffmpeg):
    from app.utils import ffmpeg as ff

    tone = tmp_path / "tone.mp4"
    ff.run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=black:s=192x108:d=2:r=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(tone),
    ], description="tone fixture")

    report = videosvc.verify_output(tone, 2.0)
    assert report.has_audio
    assert videosvc.measure_true_peak(tone) is not None


def test_the_outro_credits_only_what_the_video_actually_contains(config, surah, real_fonts_dir):
    """Crediting a translator whose words never appear, or a narrator who is
    never heard, would be a false claim on screen."""
    config.translation_credit.urdu_translator = "Some Translator"
    config.translation_credit.urdu_narrator = "Some Narrator"

    config.content.urdu_translation = False
    arabic_only = CardBuilder(config, surah, 640, 360).outro_card()

    config.content.urdu_translation = True
    config.audio.urdu_narration = True
    with_urdu = CardBuilder(config, surah, 640, 360).outro_card()

    # More credit lines means a taller block of ink.
    assert with_urdu.getchannel("A").getbbox()[3] > arabic_only.getchannel("A").getbbox()[3]


def test_the_narrator_is_not_credited_when_the_narration_is_off(config, surah, real_fonts_dir):
    config.translation_credit.urdu_translator = "Some Translator"
    config.translation_credit.urdu_narrator = "Some Narrator"
    config.content.urdu_translation = True

    config.audio.urdu_narration = True
    spoken = CardBuilder(config, surah, 640, 360).outro_card()
    config.audio.urdu_narration = False
    silent = CardBuilder(config, surah, 640, 360).outro_card()

    assert spoken.getchannel("A").getbbox()[3] > silent.getchannel("A").getbbox()[3]


def test_the_closing_card_can_be_blank(config, surah, real_fonts_dir):
    """With content.outro_card off the outro still occupies its time, but as
    background only - so the video fades out instead of cutting dead."""
    config.content.outro_card = False
    card = CardBuilder(config, surah, 640, 360).outro_card()
    assert card.size == (640, 360)
    assert card.getchannel("A").getbbox() is None, "the closing card should be empty"


def test_the_closing_card_still_renders_when_enabled(config, surah, real_fonts_dir):
    config.content.outro_card = True
    card = CardBuilder(config, surah, 640, 360).outro_card()
    assert card.getchannel("A").getbbox() is not None


def test_the_reciter_is_still_credited_in_the_metadata_without_a_card(config, surah):
    """Turning the card off must not silently drop the attribution."""
    config.content.outro_card = False
    config.reciter.name = "Mishary Rashid Alafasy"
    joined = " ".join(videosvc.metadata_options(config, "Al-Fatihah"))
    assert "Mishary Rashid Alafasy" in joined


# ---------------------------------------------------------------------------
# Word highlights in the filter graph
# ---------------------------------------------------------------------------

def test_each_highlighted_word_is_switched_on_for_its_own_span(job, prepared):
    config, surah, _, timeline = prepared
    if not config.content.word_highlight:
        pytest.skip("word highlighting is off in this config")

    spans = _highlight_spans(job.filter_graph)
    words = sum(len(a.arabic.split()) for a in surah.ayahs)
    assert len(spans) == words
    for start, end in spans:
        assert end > start


def _highlight_spans(graph):
    """(fade-in start, fade-out start) for every word highlight in the graph.

    The highlight is gated by its alpha envelope, not by `enable`: a hard
    on/off makes the colour snap from word to word.

    Scoped to chains that end in an [hlN] label - the card track uses the same
    two fade filters, so matching them across the whole graph would count the
    cards as highlights too.
    """
    pattern = (
        r"fade=t=in:st=([\d.]+):d=[\d.]+:alpha=1,"
        r"fade=t=out:st=([\d.]+):d=[\d.]+:alpha=1"
        r"[^;]*?\[hl\d+\]"
    )
    return [(float(a), float(b)) for a, b in re.findall(pattern, graph)]


def test_highlight_spans_are_bounded_by_the_video(job, prepared):
    _, _, _, timeline = prepared
    for start, end in _highlight_spans(job.filter_graph):
        assert 0.0 <= start < timeline.total_duration
        assert 0.0 < end <= timeline.total_duration + 0.001


def test_the_highlight_crossfades_between_words(job, prepared):
    """Consecutive words are contiguous, so ramps kept inside their own window
    would leave the line briefly unlit between every pair. Straddling the
    boundary means one word is still going out as the next comes in."""
    config, _, _, _ = prepared
    if not config.content.word_highlight or config.theme.highlight_fade <= 0:
        pytest.skip("the highlight is not faded in this config")

    spans = _highlight_spans(job.filter_graph)
    assert len(spans) > 1
    # A word's ramp-down begins before the next word's ramp-up finishes.
    overlaps = sum(
        1 for (_, out_a), (in_b, _) in zip(spans, spans[1:]) if out_a <= in_b
    )
    assert overlaps == len(spans) - 1


def test_the_highlight_gate_never_clips_its_own_fades(job):
    """`enable` is binary, so it must not cut a ramp short.

    The colour is shaped by the alpha ramps, not by the gate: the gate exists
    only so FFmpeg can skip compositing a layer that is fully transparent
    anyway - which, in a chunk carrying hundreds of words, is nearly all of
    them on nearly every frame. It is therefore only correct while it opens no
    later than the fade-in begins and closes no earlier than the fade-out ends.
    """
    fades = re.findall(
        r"fade=t=in:st=([\d.]+):d=([\d.]+):alpha=1,"
        r"fade=t=out:st=([\d.]+):d=([\d.]+):alpha=1,setsar=1\[hl\d+\]",
        job.filter_graph,
    )
    gates = re.findall(r"enable='gte\(t,([\d.]+)\)\*lt\(t,([\d.]+)\)'", job.filter_graph)
    assert fades and len(fades) == len(gates)

    for (in_start, in_len, out_start, out_len), (opens, closes) in zip(fades, gates):
        assert float(opens) <= float(in_start) + 1e-6
        assert float(closes) >= float(out_start) + float(out_len) - 1e-6
        # ... and the ramps are real ramps, not cuts.
        assert float(in_len) > 0 and float(out_len) > 0


def test_highlights_are_overlaid_after_the_cards(job):
    """They sit on top of the card's own glyphs, so they have to come later."""
    graph = job.filter_graph
    assert graph.index("[cards]") < graph.index("[hl0]")


def test_a_highlight_rides_the_same_rise_as_its_card(job):
    """Both move together while the card settles, or the lit word drifts off
    the glyphs it is covering."""
    rises = re.findall(r"\[hl\d+\]overlay=x='\d+':y='\d+\+([^']+)'", job.filter_graph)
    assert rises, "no highlight overlay carried a rise expression"
    # The same easing the card track uses: a clamped ramp off the card's start.
    assert all("min(max((t-" in expression for expression in rises)


def test_no_highlight_inputs_when_the_feature_is_off(prepared, tmp_path):
    config, surah, plan, timeline = prepared
    config.content.word_highlight = False
    renderer = Renderer(config, surah, plan, timeline)
    request = RenderRequest(width=192, height=108, fps=10, preview=True,
                            output=tmp_path / "off.mp4")
    asset_dir = renderer._asset_dir(request)
    background, is_video = renderer._resolve_background(request, asset_dir)
    windows = card_windows(config, timeline)
    words = word_windows(config, timeline, renderer.word_timings, windows)
    cards, highlights = renderer._build_cards(request, asset_dir, words)

    assert words == [] and highlights == {}
    graph = renderer._build_job(
        request, background, is_video, renderer._prepare_layers(request, asset_dir),
        cards, windows, words, highlights,
    ).filter_graph
    assert "[hl0]" not in graph


def test_highlight_pngs_are_cropped_not_full_frame(prepared, tmp_path):
    """One full-frame layer per word would be 8 MB decoded, each."""
    from PIL import Image

    config, surah, plan, timeline = prepared
    if not config.content.word_highlight:
        pytest.skip("word highlighting is off in this config")
    renderer = Renderer(config, surah, plan, timeline)
    request = RenderRequest(width=640, height=360, fps=10, preview=True,
                            output=tmp_path / "crop.mp4")
    asset_dir = renderer._asset_dir(request)
    windows = card_windows(config, timeline)
    words = word_windows(config, timeline, renderer.word_timings, windows)
    _, highlights = renderer._build_cards(request, asset_dir, words)

    assert highlights
    for asset in highlights.values():
        with Image.open(asset.path) as image:
            assert image.width < 640 and image.height < 360
