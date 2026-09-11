"""FFmpeg filter expressions for the motion in the video.

Everything moves slowly and continuously. There are no cuts, no wipes and no
spinning elements - just a drifting camera, a lattice and dust that never quite
repeat, and text that fades and settles. All durations and speeds come from the
`animation` and `background` blocks of config.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def _f(value: float, places: int = 4) -> str:
    """Format a float for an FFmpeg expression (no scientific notation)."""
    return f"{value:.{places}f}".rstrip("0").rstrip(".") or "0"


def clock(offset: float = 0.0) -> str:
    """The timeline clock, shifted for a chunk that does not start at zero.

    Inside a filter expression `t` is the time since the START OF THIS ENCODE.
    When the encode is one chunk of a longer video, every expression written
    against the whole video's clock has to be given the chunk's own start.
    """
    return "t" if abs(offset) < 1e-9 else f"(t+{_f(offset)})"


def positive_mod(expression: str, period: float) -> str:
    """FFmpeg's mod() keeps the sign of its first argument; this never does."""
    p = _f(period)
    return f"mod(mod({expression},{p})+{p},{p})"


# ---------------------------------------------------------------------------
# Background camera move
# ---------------------------------------------------------------------------

# Every compositing step runs in this format. Pinning it matters: letting
# `overlay=format=auto` choose per layer makes FFmpeg convert the frame back and
# forth between YUV and RGB for each of the drifting layers, which measured
# about 1.6x slower than doing the whole stack in one format.
COMPOSITE_FORMAT = "yuv420p"
OVERLAY_FORMAT = "yuv420"


def ken_burns(
    width: int,
    height: int,
    fps: int,
    duration: float,
    zoom_start: float,
    zoom_end: float,
    pan_x: float,
    pan_y: float,
    start_frame: int = 0,
    span_frames: int = 0,
) -> str:
    """A slow push-in with a drift, applied to a still background.

    The still is prepared at twice the output size by the renderer before it
    reaches FFmpeg, so zoompan's integer crop origin lands on a half-pixel of
    the final frame - which is what removes the stepping this filter is usually
    blamed for - and no per-frame rescale is needed here.
    """
    # *span_frames* is the length of the WHOLE video and *start_frame* where
    # this piece of it begins; a one-pass render leaves both at their defaults
    # and the move runs from 0 to 1 across the only piece there is.
    total_frames = max(2, span_frames or int(round(duration * fps)))
    if start_frame:
        progress = f"((on+{start_frame})/{total_frames - 1})"
    else:
        progress = f"(on/{total_frames - 1})"

    zoom = f"{_f(zoom_start)}+{_f(zoom_end - zoom_start)}*{progress}"
    x = f"iw/2-(iw/zoom/2)+({_f(pan_x)}*iw)*{progress}"
    y = f"ih/2-(ih/zoom/2)+({_f(pan_y)}*ih)*{progress}"

    return (
        f"zoompan=z='{zoom}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps},"
        f"setsar=1,format={COMPOSITE_FORMAT}"
    )


def static_fill(width: int, height: int) -> str:
    """Crop-to-fill 16:9 without ever stretching the source."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height},setsar=1,format={COMPOSITE_FORMAT}"
    )


def overlay_filter(
    x_expr: str,
    y_expr: str,
    animated: bool = True,
    enable: Optional[tuple[float, float]] = None,
) -> str:
    """An overlay that stays inside the pinned compositing format.

    overlay's `eval` defaults to `frame`, so a static layer would re-evaluate
    its constant position expressions on every frame unless `init` is asked for
    explicitly.

    *enable* limits the overlay to a (start, end) span of the timeline; outside
    it the frame passes through untouched. `gte`/`lt` rather than `between` so
    that one span ending exactly where the next begins cannot show both layers
    on the shared frame.
    """
    evaluation = "frame" if animated else "init"
    filt = f"overlay=x='{x_expr}':y='{y_expr}':format={OVERLAY_FORMAT}:eval={evaluation}"
    if enable is not None:
        start, end = enable
        filt += f":enable='gte(t,{_f(start)})*lt(t,{_f(end)})'"
    return filt


def escape_filter_path(path) -> str:
    """Quote a Windows path so FFmpeg's filter parser accepts it.

    Inside a filtergraph a backslash starts an escape and a colon separates
    options, so `C:\\subs\\x.ass` has to become `C\\:/subs/x.ass`.
    """
    text = str(path).replace("\\", "/")
    return text.replace(":", r"\:").replace("'", r"\'")


def burn_subtitles(subtitle_file, fonts_dir=None) -> str:
    """Render an ASS subtitle file into the picture (libass)."""
    parts = [f"ass=filename='{escape_filter_path(subtitle_file)}'"]
    if fonts_dir is not None:
        parts.append(f"fontsdir='{escape_filter_path(fonts_dir)}'")
    return ":".join(parts)


def still_source(fps: int, frames: int, rgba: bool = True) -> str:
    """Hold one decoded still for exactly *frames* frames.

    `-loop 1` on the image demuxer re-reads and re-decodes the PNG for every
    single output frame. At 1080p, with five full-frame layers, that decoding
    was about half of the total render time. The loop filter instead caches the
    one decoded frame and repeats it, which measured close to twice as fast.

    Two details keep the result exact:

    * `settb` first. `setpts=N/fps/TB` divides by the INPUT timebase, and the
      image demuxer hands a PNG a 1/25 timebase whatever the requested rate is.
      Without settb, a 30 fps stream lands on 25 distinct timestamps a second
      with duplicates in between, and the alpha ramps step instead of gliding.
    * a frame count, not a duration. `trim=duration=` keeps ceil(d*fps) frames,
      so every clip rounds UP; concatenated, that error accumulates and the card
      track slides later and later against the audio.
    """
    prefix = "format=rgba," if rgba else ""
    return (
        f"{prefix}loop=loop=-1:size=1:start=0,settb=1/{fps},"
        f"setpts=N/{fps}/TB,trim=end_frame={max(1, int(frames))},setpts=PTS-STARTPTS"
    )


def frame_span(start: float, end: float, fps: int) -> int:
    """Whole frames between two timeline positions.

    Rounding each boundary to the nearest frame - rather than rounding each
    duration independently - makes the per-clip errors cancel instead of
    accumulate, so clip k always begins exactly where the timeline says.
    """
    return max(1, int(round(end * fps)) - int(round(start * fps)))


def gaussian_blur(sigma: float) -> str:
    """Per-frame blur. Only used for video backgrounds - a still background is
    blurred once with Pillow instead, which is both faster and sharper, since
    the dust and the lattice then stay crisp over a soft backdrop."""
    return f"gblur=sigma={_f(sigma)}"


def background_grade(dim: float, contrast: float, saturation: float,
                     brightness: float, vignette: float) -> list[str]:
    """Dim, grade and vignette the backdrop so the text always wins."""
    steps: list[str] = []

    if dim and dim > 0:
        # Pull the white point down with lutyuv, not colorlevels. colorlevels
        # accepts only RGB, so it dragged the whole stack out of the pinned
        # yuv420p format and back (420 -> rgb24 -> yuv444p -> 420) on every
        # frame - exactly the conversion the pinned format exists to avoid.
        # Measured at 1080p: 23 fps with colorlevels, 45 fps with this.
        #
        # Scaling R,G,B by k is, in limited-range YUV, scaling luma about 16 and
        # chroma about 128 by the same k. Anchoring on those two points is what
        # keeps blacks black; folding the dim into eq instead crushes them,
        # because eq treats the raw 0-255 range as full-range.
        level = _f(max(0.02, 1.0 - dim))
        steps.append(
            f"lutyuv=y='16+(val-16)*{level}'"
            f":u='128+(val-128)*{level}'"
            f":v='128+(val-128)*{level}'"
        )
    if contrast != 1.0 or saturation != 1.0 or brightness != 0.0:
        steps.append(
            f"eq=contrast={_f(contrast)}:saturation={_f(saturation)}:brightness={_f(brightness)}"
        )
    if vignette and vignette > 0:
        # The vignette filter takes a lens angle in radians; its default is
        # PI/5 (~0.63). Map 0..1 onto a gentle-to-firm range.
        angle = 0.42 + 0.50 * max(0.0, min(1.0, vignette))
        steps.append(f"vignette=angle={_f(angle, 3)}:mode=forward")
    return steps


# ---------------------------------------------------------------------------
# Drifting overlay layers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DriftExpressions:
    x: str
    y: str


def quantise_speed(pixels_per_second: float, fps: int) -> float:
    """Snap a drift speed to a whole number of pixels per frame.

    `overlay` positions on integer pixels, so a layer asked to move less than
    one pixel per frame does not glide - it sits still for several frames and
    then snaps a whole pixel. Measured on the lattice at 9 px/s and 30 fps:
    45 of 59 frames were identical, then one jumped 4.3x the average. That
    stutter is what reads as the video "lagging".

    Snapping to exactly one pixel per frame moves the layer the same distance
    every single frame - measured burst 1.0x with no still frames at all. One
    pixel per frame is therefore the slowest genuinely smooth speed available;
    anything below it has to stutter.
    """
    if not pixels_per_second:
        return 0.0
    per_frame = pixels_per_second / max(1, fps)
    snapped = round(per_frame)
    if snapped == 0:
        snapped = 1 if per_frame > 0 else -1
    return snapped * fps


def wrapping_drift(
    speed_x: float, speed_y: float, period_x: float, period_y: float, fps: int,
    offset: float = 0.0,
) -> DriftExpressions:
    """Endless scroll for a layer that tiles with period (period_x, period_y).

    Speeds are quantised to whole pixels per frame so the scroll is even. The
    wrap stays seamless because the period is a whole number of pixels and the
    layer advances by whole pixels, so it lands exactly on the boundary.
    """
    step_x = quantise_speed(speed_x, fps)
    step_y = quantise_speed(speed_y, fps)
    now = clock(offset)
    x = f"-({positive_mod(f'{_f(step_x)}*{now}', period_x)})" if step_x else "0"
    y = f"-({positive_mod(f'{_f(step_y)}*{now}', period_y)})" if step_y else "0"
    return DriftExpressions(x=x, y=y)


def sine_drift(amplitude_x: float, amplitude_y: float,
               period_x: float = 47.0, period_y: float = 61.0,
               offset: float = 0.0) -> DriftExpressions:
    """Slow, seamless wandering for a single large soft element.

    The two periods are deliberately not multiples of one another, so the
    motion takes minutes to visibly repeat.
    """
    now = clock(offset)
    x = f"(W-w)/2+sin({now}*2*PI/{_f(period_x)})*{_f(amplitude_x)}"
    y = f"(H-h)/2+cos({now}*2*PI/{_f(period_y)})*{_f(amplitude_y)}"
    return DriftExpressions(x=x, y=y)


# ---------------------------------------------------------------------------
# Text cards
# ---------------------------------------------------------------------------

def alpha_fade(fade_in: float, fade_out: float, fade_out_start: float) -> str:
    """Alpha ramps on an RGBA card, expressed in the card's own timebase."""
    parts: list[str] = ["format=rgba"]
    if fade_in > 0:
        parts.append(f"fade=t=in:st=0:d={_f(fade_in)}:alpha=1")
    if fade_out > 0:
        parts.append(f"fade=t=out:st={_f(max(0.0, fade_out_start))}:d={_f(fade_out)}:alpha=1")
    return ",".join(parts)


def timed_alpha(start: float, end: float, fade_in: float, fade_out: float) -> str:
    """Alpha envelope for a layer that should only be visible from start to end.

    Used instead of `overlay=...:enable=`, which is a hard on/off: a word would
    snap into colour and snap out again. A still built by `still_source` carries
    the timeline's own timestamps, so the ramps can be placed at absolute times
    here, and `fade` holds alpha at zero outside them - one pair of ramps
    therefore behaves as a window with soft edges.

    Where consecutive words meet, one word's ramp down overlaps the next one's
    ramp up, so the colour flows along the line instead of jumping.
    """
    span = max(0.0, end - start)
    fade_in = max(0.0, fade_in)
    fade_out = max(0.0, fade_out)

    # Overlapping ramps would mean the layer never reaches full opacity.
    total = fade_in + fade_out
    if total > span and total > 0:
        shrink = span / total
        fade_in *= shrink
        fade_out *= shrink

    # fade rejects a zero duration, and a ramp shorter than a frame is a cut
    # anyway - but the filter still has to be present to gate the layer.
    floor = 0.001
    return ",".join([
        "format=rgba",
        f"fade=t=in:st={_f(start)}:d={_f(max(fade_in, floor))}:alpha=1",
        f"fade=t=out:st={_f(max(0.0, end - fade_out))}:d={_f(max(fade_out, floor))}:alpha=1",
    ])


def text_rise(start: float, rise_pixels: float, rise_duration: float) -> str:
    """Overlay y-expression: the card settles upward as it fades in.

    `t` inside an overlay expression is the timeline clock, so the card's own
    start time has to be subtracted here.
    """
    if rise_pixels <= 0 or rise_duration <= 0:
        return "0"
    elapsed = f"(t-{_f(start)})"
    ramp = f"(1-min(max({elapsed},0)/{_f(rise_duration)},1))"
    # Ease-out so the movement decelerates instead of stopping dead.
    return f"{_f(rise_pixels)}*{ramp}*{ramp}"


def shift_to(start: float) -> str:
    """Move an input's timestamps so that its first frame lands at *start*."""
    return f"setpts=PTS+{_f(start)}/TB"


# ---------------------------------------------------------------------------
# Audio placement
# ---------------------------------------------------------------------------

def clip_filter(
    start: float,
    gain_db: float,
    fade_in: float,
    fade_out: float,
    duration: float,
    sample_rate: int,
    speed: float = 1.0,
) -> str:
    """Gain, edge fades, re-timing and delay for one audio clip.

    *duration* is the SOURCE length; at speed != 1 the clip occupies
    duration/speed on the timeline, and the fades are placed in that re-timed
    scale because atempo comes before them in the chain.

    atempo, not a resample: it changes tempo while leaving pitch alone, so the
    reciter is not transposed. It is still a change to the recording, and the
    default speed of 1.0 leaves the audio completely untouched.
    """
    # aformat rather than plain aresample: amix negotiates ONE channel layout
    # across all of its inputs, resolved from the first, so a mono recitation
    # would silently fold a stereo narration or ambience bed to mono - and the
    # encoder's -ac 2 would then re-expand it to dual mono, hiding the loss.
    # Pinning the layout here makes the mix independent of input order.
    # (Plain aresample, not soxr: that engine is absent from some FFmpeg builds
    # and the default converter is transparent at these rates anyway.)
    steps = [f"aformat=sample_rates={sample_rate}:channel_layouts=stereo"]
    if abs(gain_db) > 0.01:
        steps.append(f"volume={_f(gain_db, 2)}dB")

    played = duration
    if abs(speed - 1.0) > 1e-6:
        steps.append(f"atempo={_f(speed, 4)}")
        played = duration / speed

    if fade_in > 0:
        steps.append(f"afade=t=in:st=0:d={_f(fade_in, 3)}")
    if fade_out > 0 and played > fade_out:
        steps.append(f"afade=t=out:st={_f(played - fade_out, 3)}:d={_f(fade_out, 3)}")
    delay_ms = int(round(max(0.0, start) * 1000))
    if delay_ms > 0:
        steps.append(f"adelay={delay_ms}:all=1")
    return ",".join(steps)
