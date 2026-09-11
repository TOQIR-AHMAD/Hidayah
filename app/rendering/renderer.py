"""The render orchestrator: assets -> filter graph -> MP4.

The whole video is produced by a single FFmpeg pass, which is what keeps the
background continuous from the first frame to the last. The graph is:

    background (a still with a slow push-in, or a looped clip)
      + drifting geometric lattice
      + slowly wandering soft light
      + two layers of floating dust
      -> blur / dim / grade / vignette
      + the hairline frame
      + the card track  (every card concatenated, each with its own alpha ramps)
      = video

    every recitation and narration clip, gain-matched and delayed to its cue
      -> mixed
      = audio

Cards fade out completely before the next fades in, so two different ayahs are
never legible at the same time. The background never cuts, so the handover
reads as a breath rather than an edit.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from PIL import Image, ImageFilter

from app.config import Config
from app.models.surah import Surah
from app.rendering import animations as anim
from app.rendering import textures
from app.rendering.compositions import CardBuilder
from app.rendering.timeline import (
    CardWindow,
    Segment,
    Timeline,
    WordWindow,
    card_windows,
    word_windows,
)
from app.services import video as videosvc
from app.services import word_timings as word_timings_service
from app.services.audio import AudioPlan
from app.utils.files import (
    available_memory_mb,
    ensure_dir,
    find_first_media,
    free_space_mb,
    is_video,
    require_free_space,
)
from app.utils.logging import QVGError, get_logger


@dataclass(frozen=True)
class HighlightAsset:
    """A cropped word-highlight PNG and where it belongs on the frame."""

    path: Path
    x: int
    y: int


@dataclass
class MotionLayers:
    """Paths to the generated drifting layers (None when disabled in config)."""

    pattern: Optional[Path] = None
    pattern_tile: int = 300
    light: Optional[Path] = None
    dust_a: Optional[Path] = None
    dust_b: Optional[Path] = None


@dataclass(frozen=True)
class Chunk:
    """One piece of a chunked render.

    A chunk covers whole cards - it never cuts inside one - and its boundaries
    are whole frames of the finished video, so the pieces join without a gap or
    an overlap. Its timeline, card windows and word windows are the global ones
    rebased to start at zero, which is the clock FFmpeg will use inside this
    piece; `start_frame` and `span_frames` are what it needs to place itself
    back into the whole video, for the camera move and the drifting layers.
    """

    index: int
    count: int
    fps: int
    start_frame: int
    end_frame: int
    span_frames: int
    timeline: Timeline
    windows: list[CardWindow]
    words: list[WordWindow]
    video: Path
    audio: Path

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def time_offset(self) -> float:
        """Where this chunk begins in the whole video, in seconds."""
        return self.start_frame / max(1, self.fps)

    @property
    def is_first(self) -> bool:
        return self.index == 0

    @property
    def is_last(self) -> bool:
        return self.index == self.count - 1

    @property
    def label(self) -> str:
        ayahs = [s.ayah_number for s in self.timeline.segments if s.ayah_number]
        if not ayahs:
            return f"chunk {self.index + 1}/{self.count}"
        span = f"{min(ayahs)}" if min(ayahs) == max(ayahs) else f"{min(ayahs)}-{max(ayahs)}"
        return f"chunk {self.index + 1}/{self.count} (ayah {span})"


@dataclass
class RenderRequest:
    width: int
    height: int
    fps: int
    preview: bool
    output: Path
    # Set only when subtitles.burn_in is true; an .ass file authored at this
    # resolution, rendered into the picture by libass.
    subtitle_file: Optional[Path] = None


class Renderer:
    def __init__(
        self,
        config: Config,
        surah: Surah,
        plan: AudioPlan,
        timeline: Timeline,
        word_timings: Optional[dict] = None,
    ) -> None:
        self.config = config
        self.surah = surah
        self.plan = plan
        self.timeline = timeline
        self.log = get_logger()

        # Loaded here when the caller did not, so a Renderer built directly -
        # in a test, or from a script - still highlights words.
        if word_timings is None and config.content.word_highlight:
            word_timings = word_timings_service.load(
                config,
                surah,
                {number: clip.duration for number, clip in plan.recitation.items()},
            )
        self.word_timings = word_timings or {}

    # -- asset preparation ---------------------------------------------------
    def _asset_dir(self, request: RenderRequest) -> Path:
        name = "preview" if request.preview else "render"
        return ensure_dir(self.config.temp_dir / f"{name}_{request.width}x{request.height}")

    def _theme_fingerprint(self, request: RenderRequest) -> str:
        payload = (
            self.config.theme.model_dump_json()
            + self.config.background.model_dump_json()
            + self.config.animation.model_dump_json()
            + f"{request.width}x{request.height}"
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]

    def _still_blur_sigma(self, request: RenderRequest) -> float:
        """Blur radius for a still background, in the still's own pixels.

        The still is prepared at twice the output size, so the sigma is doubled
        to land at the configured strength once it is scaled down.
        """
        return self.config.background.blur * (request.height / 1080.0) * 2.0

    def _prepare_still(self, source: Path, request: RenderRequest, asset_dir: Path) -> Path:
        """Cover-crop a still to 16:9 at twice the output size, and soften it.

        Both steps happen once, in Pillow, rather than on every frame in the
        filter graph. FFmpeg decodes the same looped image thousands of times,
        so a per-frame lanczos resize and gaussian blur are pure waste - and
        blurring only the backdrop leaves the drifting dust and lattice crisp
        on top of it, which is what the look wants anyway.
        """
        target_w, target_h = request.width * 2, request.height * 2
        sigma = self._still_blur_sigma(request)
        stamp = f"{source.stat().st_mtime_ns:x}"
        cache = asset_dir / f"bg_{source.stem}_{target_w}x{target_h}_b{sigma:.1f}_{stamp}.png"
        if cache.is_file():
            return cache

        try:
            image = Image.open(source).convert("RGB")
        except (OSError, ValueError) as exc:
            raise QVGError(
                f"The background image '{source.name}' could not be read: {exc}",
                hint="Supported still formats are JPG, PNG, WebP and BMP. Re-save the "
                "file, or point background.source at a different one.",
            )

        scale = max(target_w / image.width, target_h / image.height)
        resized = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.LANCZOS,
        )
        left = (resized.width - target_w) // 2
        top = (resized.height - target_h) // 2
        cropped = resized.crop((left, top, left + target_w, top + target_h))
        if sigma > 0:
            cropped = cropped.filter(ImageFilter.GaussianBlur(sigma))
        self.log.info(
            "Prepared background %s (%dx%d -> %dx%d)",
            source.name, image.width, image.height, target_w, target_h,
        )
        textures.save_png(cropped, cache)
        return cache

    def _resolve_background(self, request: RenderRequest, asset_dir: Path) -> tuple[Path, bool]:
        """Return (path, is_video). Generates a backdrop when none is supplied."""
        setting = self.config.background.source.strip()

        if setting and setting.lower() not in ("auto", "generated"):
            candidate = self.config.backgrounds_dir / setting
            if not candidate.is_file():
                candidate = self.config.path(setting)
            if not candidate.is_file():
                raise QVGError(
                    f"The background '{setting}' was not found.",
                    hint=f"Put the file in {self.config.backgrounds_dir}\\ , or set "
                    "background.source to 'auto' in config.yaml to use the generated "
                    "backdrop.",
                )
            if is_video(candidate):
                return candidate, True
            return self._prepare_still(candidate, request, asset_dir), False

        if setting.lower() != "generated":
            found = find_first_media(self.config.backgrounds_dir)
            if found is not None:
                self.log.info("Using background %s", found.name)
                if is_video(found):
                    return found, True
                return self._prepare_still(found, request, asset_dir), False

        if self.config.theme.style == "glass" and setting.lower() != "generated":
            # This style draws no ground of its own - it is text over artwork -
            # so falling through to the generated backdrop means the design is
            # missing its main element, and that is easy to miss in a preview.
            self.log.warning(
                "theme.style is 'glass', which is designed to sit on your own "
                "artwork, but %s/ is empty. Put the plain plate (no text on "
                "it) there - see the README in that folder.",
                self.config.backgrounds_dir.name,
            )

        cache = asset_dir / f"backdrop_{self._theme_fingerprint(request)}.png"
        if cache.is_file():
            return cache, False

        self.log.info(
            "Generating the backdrop (%dx%d)", request.width * 2, request.height * 2
        )
        theme = self.config.theme
        generated = self.config.background.generated
        scale = 2
        backdrop = textures.make_backdrop(
            request.width * scale,
            request.height * scale,
            theme.background_color,
            theme.deep_color,
            theme.accent_color,
            pattern_scale=max(48, int(generated.pattern_scale * request.height / 1080 * scale)),
            pattern_opacity=generated.pattern_opacity,
            glow_opacity=generated.glow_opacity,
            grain=generated.grain,
            appearance=theme.appearance,
        )
        sigma = self._still_blur_sigma(request)
        if sigma > 0:
            backdrop = backdrop.filter(ImageFilter.GaussianBlur(sigma))
        textures.save_png(backdrop, cache)
        return cache, False

    def _prepare_layers(self, request: RenderRequest, asset_dir: Path) -> MotionLayers:
        """Generate the drifting motion layers, cached by theme fingerprint."""
        theme = self.config.theme
        animation = self.config.animation
        key = self._theme_fingerprint(request)
        layers = MotionLayers()
        w, h = request.width, request.height

        if animation.pattern_layer_enabled and animation.pattern_opacity > 0:
            tile = max(48, int(self.config.background.generated.pattern_scale * h / 1080))
            path = asset_dir / f"pattern_{key}.png"
            if not path.is_file():
                layer, _ = textures.make_pattern_layer(
                    w, h, tile, theme.accent_color, animation.pattern_opacity
                )
                textures.save_png(layer, path)
            layers.pattern = path
            layers.pattern_tile = tile

        if animation.light_enabled and animation.light_opacity > 0:
            path = asset_dir / f"light_{key}.png"
            if not path.is_file():
                textures.save_png(
                    textures.make_light_layer(
                        int(w * 0.95), int(h * 1.05),
                        theme.accent_color, animation.light_opacity,
                    ),
                    path,
                )
            layers.light = path

        if animation.particles_enabled and animation.particles_opacity > 0:
            count = max(24, int(90 * (h / 1080)))
            near = asset_dir / f"dust_a_{key}.png"
            far = asset_dir / f"dust_b_{key}.png"
            if not near.is_file():
                textures.save_png(
                    textures.make_particle_layer(
                        w, h, count, theme.text_color, animation.particles_opacity,
                        seed=11, min_radius=1.6 * h / 1080, max_radius=5.5 * h / 1080,
                    ),
                    near,
                )
            if not far.is_file():
                textures.save_png(
                    textures.make_particle_layer(
                        w, h, int(count * 1.4), theme.accent_color,
                        animation.particles_opacity * 0.7,
                        seed=29, min_radius=1.0 * h / 1080, max_radius=3.0 * h / 1080,
                    ),
                    far,
                )
            layers.dust_a = near
            layers.dust_b = far

        return layers

    def _build_highlights(
        self,
        builder: CardBuilder,
        words: list[WordWindow],
        cards_dir: Path,
    ) -> dict[tuple[int, int], "HighlightAsset"]:
        """One small PNG per word, cropped to its ink.

        Cropping matters: a full-frame layer per word would be 8 MB decoded and
        there are one of them for every word in the surah. The crop plus its
        offset is a few hundred kilobytes and composites to the same pixels.
        """
        assets: dict[tuple[int, int], HighlightAsset] = {}
        wanted = sorted({(w.ayah_number, w.word_index) for w in words})
        if not wanted:
            return assets

        highlights_dir = ensure_dir(cards_dir / "words")
        for ayah_number, word_index in wanted:
            layer = builder.highlight_layer(self.surah.ayah(ayah_number), word_index)
            if layer is None:
                continue
            box = layer.getbbox()
            if box is None:
                continue
            destination = highlights_dir / f"a{ayah_number:03d}_w{word_index:02d}.png"
            textures.save_png(layer.crop(box), destination)
            assets[(ayah_number, word_index)] = HighlightAsset(
                path=destination, x=box[0], y=box[1]
            )

        self.log.info("Rendered %d word highlights", len(assets))
        return assets

    def _build_cards(
        self,
        request: RenderRequest,
        asset_dir: Path,
        words: list[WordWindow],
    ) -> tuple[dict[str, Path], dict[tuple[int, int], "HighlightAsset"]]:
        """Render every card PNG. This is where the typography happens.

        The highlights are built from the same CardBuilder as the cards, so
        they share its measured layout - a second builder could fit the text at
        a different size and the highlight would sit off the word.
        """
        builder = CardBuilder(self.config, self.surah, request.width, request.height)
        cards_dir = ensure_dir(asset_dir / "cards")
        paths: dict[str, Path] = {}
        ayah_total = len(self.surah.ayahs)

        frame = builder.frame_layer()
        if frame is not None:
            frame_path = cards_dir / "frame.png"
            textures.save_png(frame, frame_path)
            paths["__frame__"] = frame_path

        for segment in self.timeline.segments:
            destination = cards_dir / f"{segment.index:02d}_{segment.card_id}.png"
            if segment.kind == "intro":
                self.log.info("Rendering the intro card")
                image = builder.intro_card()
            elif segment.kind == "outro":
                self.log.info("Rendering the outro card")
                image = builder.outro_card()
            elif segment.kind == "arabic":
                self.log.info("Rendering Ayah %d/%d - Arabic", segment.ayah_number, ayah_total)
                image = builder.arabic_card(self.surah.ayah(segment.ayah_number))
            else:
                self.log.info(
                    "Rendering Ayah %d/%d - Urdu translation", segment.ayah_number, ayah_total
                )
                image = builder.urdu_card(self.surah.ayah(segment.ayah_number))

            textures.save_png(image, destination)
            paths[segment.card_id] = destination

        highlights = self._build_highlights(builder, words, cards_dir)
        return paths, highlights

    # -- filter graph --------------------------------------------------------
    def _build_job(
        self,
        request: RenderRequest,
        background: Path,
        background_is_video: bool,
        layers: MotionLayers,
        cards: dict[str, Path],
        windows: list[CardWindow],
        words: Optional[list[WordWindow]] = None,
        highlights: Optional[dict[tuple[int, int], HighlightAsset]] = None,
        chunk: Optional[Chunk] = None,
    ) -> videosvc.FFmpegJob:
        config = self.config
        theme = config.theme
        animation = config.animation
        timeline = chunk.timeline if chunk is not None else self.timeline
        total = timeline.total_duration
        fps = request.fps
        w, h = request.width, request.height
        scale_to_frame = h / 1080.0

        job = videosvc.FFmpegJob(duration=total)
        # Every full-length layer holds exactly this many frames, so the layers,
        # the card track and the output all agree on the length to the frame.
        total_frames = chunk.frames if chunk is not None else anim.frame_span(0.0, total, fps)
        # Where this piece sits in the whole video. Both are zero for a
        # one-pass render, which leaves every expression below unchanged.
        offset = chunk.time_offset if chunk is not None else 0.0
        start_frame = chunk.start_frame if chunk is not None else 0
        span_frames = chunk.span_frames if chunk is not None else 0

        # --- background -----------------------------------------------------
        if background_is_video:
            index = job.add_input(background, "-stream_loop", "-1", "-t", f"{total:.3f}")
            chain = f"{anim.static_fill(w, h)},fps={fps}"
            if config.background.blur > 0:
                chain += "," + anim.gaussian_blur(config.background.blur * scale_to_frame)
            job.add_filter(f"[{index}:v:0]{chain}[bg_raw]")
        else:
            index = job.add_input(background)
            hold = anim.still_source(fps, total_frames, rgba=False)
            if config.background.ken_burns:
                chain = anim.ken_burns(
                    w, h, fps, total,
                    config.background.zoom_start, config.background.zoom_end,
                    config.background.pan_x, config.background.pan_y,
                    start_frame=start_frame, span_frames=span_frames,
                )
            else:
                chain = anim.static_fill(w, h)
            job.add_filter(f"[{index}:v:0]{hold},{chain}[bg_raw]")

        current = "bg_raw"

        # --- drifting lattice ----------------------------------------------
        if layers.pattern is not None:
            tile = float(layers.pattern_tile)
            index = job.add_input(layers.pattern)
            drift = anim.wrapping_drift(
                animation.pattern_drift_x * scale_to_frame,
                animation.pattern_drift_y * scale_to_frame,
                tile, tile, fps, offset=offset,
            )
            job.add_filter(f"[{index}:v:0]{anim.still_source(fps, total_frames)}[pattern]")
            job.add_filter(
                f"[{current}][pattern]{anim.overlay_filter(drift.x, drift.y)}[bg_pattern]"
            )
            current = "bg_pattern"

        # --- wandering light ------------------------------------------------
        if layers.light is not None:
            index = job.add_input(layers.light)
            drift = anim.sine_drift(
                abs(animation.light_drift_x) * 9 * scale_to_frame,
                abs(animation.light_drift_y) * 9 * scale_to_frame,
                offset=offset,
            )
            job.add_filter(f"[{index}:v:0]{anim.still_source(fps, total_frames)}[light]")
            job.add_filter(
                f"[{current}][light]{anim.overlay_filter(drift.x, drift.y)}[bg_light]"
            )
            current = "bg_light"

        # --- floating dust --------------------------------------------------
        for name, speed_x, speed_y in (
            ("dust_a", animation.particles_drift_x, animation.particles_drift_y),
            ("dust_b", animation.particles_drift_x2, animation.particles_drift_y2),
        ):
            layer_path = getattr(layers, name)
            if layer_path is None:
                continue
            index = job.add_input(layer_path)
            drift = anim.wrapping_drift(
                speed_x * scale_to_frame, speed_y * scale_to_frame,
                float(w), float(h), fps, offset=offset,
            )
            job.add_filter(f"[{index}:v:0]{anim.still_source(fps, total_frames)}[{name}]")
            job.add_filter(
                f"[{current}][{name}]{anim.overlay_filter(drift.x, drift.y)}[bg_{name}]"
            )
            current = f"bg_{name}"

        # --- grade ----------------------------------------------------------
        grade = anim.background_grade(
            config.background.dim,
            theme.grade_contrast,
            theme.grade_saturation,
            theme.grade_brightness,
            theme.vignette,
        )
        if grade:
            job.add_filter(f"[{current}]{','.join(grade)}[bg_graded]")
            current = "bg_graded"

        # --- hairline frame -------------------------------------------------
        if "__frame__" in cards:
            index = job.add_input(cards["__frame__"])
            # The hairline frame fades in with the first card of the VIDEO and
            # out with its last. A chunk in the middle has neither, and must
            # hold the frame at full opacity from edge to edge.
            first = chunk is None or chunk.is_first
            last = chunk is None or chunk.is_last
            frame_in = animation.scaled(animation.intro_fade_in) if first else 0.0
            frame_out = animation.scaled(animation.outro_fade_out) if last else 0.0
            job.add_filter(
                f"[{index}:v:0]{anim.still_source(fps, total_frames)},"
                f"{anim.alpha_fade(frame_in, frame_out, max(0.0, total - frame_out))}[frame]"
            )
            job.add_filter(
                f"[{current}][frame]{anim.overlay_filter('0', '0', animated=False)}[bg_framed]"
            )
            current = "bg_framed"

        # --- card track -----------------------------------------------------
        card_labels: list[str] = []
        for position, window in enumerate(windows):
            path = cards[window.segment.card_id]
            index = job.add_input(path)
            label = f"card{position}"
            card_frames = anim.frame_span(window.start, window.end, fps)
            job.add_filter(
                f"[{index}:v:0]{anim.still_source(fps, card_frames)},"
                f"{anim.alpha_fade(window.fade_in, window.fade_out, window.fade_out_start)},"
                f"setsar=1[{label}]"
            )
            card_labels.append(f"[{label}]")

        job.add_filter(f"{''.join(card_labels)}concat=n={len(card_labels)}:v=1:a=0[cards]")

        rise_expr = self._per_card_rise(windows, animation, h)
        job.add_filter(
            f"[{current}][cards]{anim.overlay_filter('0', rise_expr)}[video]"
        )
        current = "video"

        # --- word highlights -------------------------------------------------
        # One overlay per word, faded in and out over its own span rather than
        # switched on. Each rides the same rise as the card underneath it, so
        # the lit word stays locked to the glyphs it is colouring while the card
        # is still settling.
        if highlights and words:
            starts = {w.segment.index: w.start for w in windows}
            rise = animation.text_rise * h / 1080.0
            rise_duration = animation.scaled(animation.text_rise_duration)
            speed = max(0.01, config.video.playback_speed)
            fade = max(0.0, theme.highlight_fade) / speed

            for position, word in enumerate(words):
                asset = highlights.get((word.ayah_number, word.word_index))
                if asset is None:
                    continue
                index = job.add_input(asset.path)
                label = f"hl{position}"
                # The ramps are placed on the timeline's own clock, so no
                # `enable` is needed - the envelope is the window.
                #
                # The window is widened by half a fade at each end so the ramps
                # STRADDLE the boundaries rather than sitting inside them.
                # Consecutive words are contiguous, so ramps kept inside would
                # leave the line briefly unlit between every pair; straddling
                # them means one word is still going out as the next comes in,
                # and the colour travels along the line.
                lead = fade / 2.0
                lit_from = max(0.0, word.start - lead)
                lit_until = word.end + lead
                job.add_filter(
                    f"[{index}:v:0]{anim.still_source(fps, total_frames)},"
                    f"{anim.timed_alpha(lit_from, lit_until, fade, fade)},"
                    f"setsar=1[{label}]"
                )
                segment_start = starts.get(word.segment_index, word.start)
                y_expr = anim.text_rise(segment_start, rise, rise_duration)
                y = f"{asset.y}" if y_expr == "0" else f"{asset.y}+{y_expr}"
                # `enable` gates the BLEND, not the envelope: the alpha ramps
                # above still shape the edges, and this only tells FFmpeg not to
                # composite a layer that is transparent anyway. Every word in
                # the chunk is otherwise blended on every frame, and a chunk
                # holds hundreds of them.
                job.add_filter(
                    f"[{current}][{label}]"
                    f"{anim.overlay_filter(str(asset.x), y, enable=(lit_from, lit_until))}"
                    f"[hlout{position}]"
                )
                current = f"hlout{position}"

            job.add_filter(f"[{current}]null[video]")

        final_chain = [f"format={config.video.pixel_format}", f"fps={fps}"]
        if request.subtitle_file is not None and request.subtitle_file.is_file():
            self.log.info("Burning subtitles into the picture")
            final_chain.append(
                anim.burn_subtitles(request.subtitle_file, config.fonts_dir)
            )
            final_chain.append(f"format={config.video.pixel_format}")
        if chunk is not None:
            final_chain.append(f"trim=end_frame={total_frames}")
        else:
            final_chain.append(f"trim=duration={total:.3f}")
        final_chain.append("setpts=PTS-STARTPTS")
        job.add_filter(f"[video]{','.join(final_chain)}[vout]")

        # --- audio ----------------------------------------------------------
        audio_labels: list[str] = []
        for position, segment in enumerate(timeline.with_audio):
            clip = segment.audio
            assert clip is not None
            options = ["-accurate_seek"]
            if clip.trim_start > 0:
                options += ["-ss", f"{clip.trim_start:.4f}"]
            options += ["-t", f"{clip.duration:.4f}"]
            index = job.add_input(clip.path, *options)

            label = f"a{position}"
            job.add_filter(
                f"[{index}:a:0]"
                + anim.clip_filter(
                    start=segment.audio_start,
                    gain_db=clip.gain_db,
                    fade_in=config.audio.clip_fade_in,
                    fade_out=config.audio.clip_fade_out,
                    duration=clip.duration,
                    sample_rate=config.video.audio_sample_rate,
                    speed=config.video.playback_speed,
                )
                + f"[{label}]"
            )
            audio_labels.append(f"[{label}]")

        ambience = config.audio.ambience
        if ambience.enabled:
            index = job.add_input(
                config.path(ambience.file), "-stream_loop", "-1", "-t", f"{total:.3f}"
            )
            job.add_filter(
                f"[{index}:a:0]aresample={config.video.audio_sample_rate},"
                f"volume={ambience.volume_db}dB,"
                f"afade=t=in:st=0:d={ambience.fade:.2f},"
                f"afade=t=out:st={max(0.0, total - ambience.fade):.2f}:d={ambience.fade:.2f}"
                f"[ambience]"
            )
            audio_labels.append("[ambience]")

        if audio_labels:
            job.add_filter(
                f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:"
                f"duration=longest:normalize=0:dropout_transition=0[amixed]"
            )
            # asetpts FIRST, then pad and trim. atrim measures against the
            # incoming timestamps, and atempo rescales those - so trimming
            # before the timestamps are normalised discarded the entire mix and
            # produced a silent track. Renumbering from the sample count gives
            # atrim a clean zero-based scale to cut against, and because adelay
            # inserts real silence samples the cue alignment is preserved.
            #
            # No limiter or compressor is applied anywhere: the per-clip gain
            # was already clamped to keep the true peak under the configured
            # ceiling.
            job.add_filter(
                f"[amixed]asetpts=N/SR/TB,apad,atrim=duration={total:.3f}[aout]"
            )
            job.maps = ["-map", "[vout]", "-map", "[aout]"]
        elif chunk is not None:
            # A chunk that carries no recitation at all - the outro, say - still
            # has to produce its own stretch of sound. Leaving the track out
            # would make this piece shorter than the picture once the parts are
            # joined, and everything after it would run early against the video.
            job.add_filter(
                f"anullsrc=r={config.video.audio_sample_rate}:cl=stereo,"
                f"atrim=duration={total:.6f},asetpts=N/SR/TB[aout]"
            )
            job.maps = ["-map", "[vout]", "-map", "[aout]"]
        else:
            job.maps = ["-map", "[vout]"]

        if chunk is not None:
            # A chunk is written as picture and sound in separate files: the
            # video parts are concatenated without re-encoding, and the sound
            # is kept lossless (FLAC) until the one final mux, so joining the
            # pieces cannot cost a generation of quality.
            #
            # Both are cut to a FRAME COUNT rather than to a duration. The
            # boundaries are whole frames of the finished video and the sample
            # rate is a whole multiple of the frame rate, so the parts abut
            # exactly - a duration in seconds would round, and the error would
            # accumulate across twenty joins.
            job.maps = ["-map", "[vout]"]
            job.output_options = [
                *videosvc.encoder_options(config, request.preview, audio=False),
                "-frames:v", str(total_frames),
            ]
            job.extra_outputs = [
                videosvc.ExtraOutput(
                    maps=["-map", "[aout]"],
                    options=[
                        "-c:a", "flac", "-compression_level", "5",
                        "-ar", str(config.video.audio_sample_rate), "-ac", "2",
                        # Pinned, not left to the encoder. The concat demuxer
                        # takes its parameters from the FIRST file and reads
                        # the rest as if they matched, so a chunk that happens
                        # to encode at a different depth - silence comes out
                        # s16 where a mix comes out s32 - turns every part
                        # after it into silence. Identical parameters
                        # everywhere is what makes the join safe.
                        "-sample_fmt", "s16",
                        "-t", f"{total:.6f}",
                    ],
                    path=chunk.audio.with_name("~" + chunk.audio.name),
                )
            ]
            job.output = chunk.video.with_name("~" + chunk.video.name)
            job.final_output = None
            return job

        job.output_options = [
            *videosvc.encoder_options(config, request.preview),
            *videosvc.metadata_options(
                config, f"Surah {self.surah.surah_number} - {self.surah.name_english}"
            ),
            "-t", f"{total:.3f}",
        ]
        # Encode beside the destination, then move into place once FFmpeg has
        # exited cleanly. An interrupted render otherwise leaves a truncated
        # file sitting at the published path, looking finished.
        #
        # The real extension has to stay LAST: FFmpeg picks its muxer from it,
        # and a name ending in ".part" fails with "Unable to find a suitable
        # output format".
        job.output = request.output.with_name(
            f"~{request.output.stem}.part{request.output.suffix}"
        )
        job.final_output = request.output
        return job

    @staticmethod
    def _per_card_rise(windows: list[CardWindow], animation, height: int) -> str:
        """A y-offset that eases each card upward as it appears.

        The card track is one continuous stream, so the ease has to be restarted
        at every card boundary; nested conditionals do that in a single
        expression evaluated per frame.

        Build order matters. The chain has to test the LATEST start first and
        fall through to progressively earlier cards, so it must be assembled
        front to back - wrapping in reverse puts `gte(t, 0)` outermost, which is
        true on every frame and leaves every other branch dead, so only the
        first card would ever rise.
        """
        rise = animation.text_rise * height / 1080.0
        duration = animation.scaled(animation.text_rise_duration)
        if rise <= 0 or duration <= 0:
            return "0"

        expression = "0"
        for window in windows:
            piece = anim.text_rise(window.start, rise, duration)
            expression = f"if(gte(t,{window.start:.4f}),{piece},{expression})"
        return expression

    # -- chunked rendering ---------------------------------------------------
    # Megabytes of H.264 per second of 1920x1080 at 30 fps and CRF 21, measured
    # on this project's own material: a still photograph under drifting light,
    # dust and typography. Grainless footage would encode smaller and live
    # action larger, but nothing this renders is live action.
    _MB_PER_SECOND_AT_CRF21 = 0.33
    _CRF_STEP = 1.19  # each point of CRF is worth about this much size

    # Lossless 16-bit stereo FLAC of recitation, measured over the same files.
    _AUDIO_MB_PER_SECOND = 0.10

    def _chunked_space_estimate(self, request: RenderRequest) -> float:
        """Megabytes needed at the fullest moment of a chunked render.

        That moment is the join: every chunk is still on disk, its sound beside
        it, and the finished file is being written from them.
        """
        video = self.config.video
        crf = self.config.preview.crf if request.preview else video.crf
        pixels = (request.width * request.height * request.fps) / (1920 * 1080 * 30)
        per_second = (
            self._MB_PER_SECOND_AT_CRF21 * pixels * self._CRF_STEP ** (21 - crf)
        )

        seconds = self.timeline.total_duration
        chunks = seconds * per_second
        sound = seconds * self._AUDIO_MB_PER_SECOND
        finished = chunks + seconds * 0.024  # the same picture, plus AAC
        cards = 300 + seconds * 0.05         # the PNGs, which stay for the run

        return cards + chunks + sound + finished

    # A render that runs the drive dry leaves nothing usable behind, so it stops
    # while there is still room to write what it has.
    _SPACE_FLOOR_MB = 600.0


    def _render_fingerprint(self, request: RenderRequest) -> str:
        """Identifies everything a finished chunk depends on.

        Chunks are kept between runs so an interrupted render resumes instead of
        starting again. That is only safe while nothing they were built from has
        changed, so the fingerprint covers the look, the timing and the sound -
        change any of it and the parts land in a new directory.
        """
        config = self.config
        payload = (
            self._theme_fingerprint(request)
            + config.audio.model_dump_json()
            + config.video.model_dump_json()
            + config.content.model_dump_json()
            + config.word_timings.model_dump_json()
            + f"{self.timeline.total_duration:.4f}/{len(self.timeline.segments)}"
            + f"/{request.fps}/{request.preview}"
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]

    def _plan_chunks(
        self,
        request: RenderRequest,
        windows: list[CardWindow],
        words: list[WordWindow],
        parts_dir: Path,
    ) -> list[Chunk]:
        """Split the timeline into pieces that FFmpeg can actually build.

        A chunk always holds whole cards, and its edges are whole frames, so the
        pieces abut exactly. Two budgets decide where they fall: the number of
        word highlights (which is what makes a graph unmanageable) and the
        length in seconds (which is what makes one process take all evening).
        """
        settings = self.config.render
        fps = request.fps
        segments = self.timeline.segments
        span_frames = anim.frame_span(0.0, self.timeline.total_duration, fps)

        per_segment: dict[int, int] = {}
        for word in words:
            per_segment[word.segment_index] = per_segment.get(word.segment_index, 0) + 1

        groups: list[list[int]] = []
        current: list[int] = []
        highlights = 0
        seconds = 0.0
        for position, segment in enumerate(segments):
            count = per_segment.get(segment.index, 0)
            over_highlights = (
                settings.chunk_highlights > 0
                and current
                and highlights + count > settings.chunk_highlights
            )
            over_seconds = (
                settings.chunk_seconds > 0
                and current
                and seconds + segment.duration > settings.chunk_seconds
            )
            if over_highlights or over_seconds:
                groups.append(current)
                current, highlights, seconds = [], 0, 0.0
            current.append(position)
            highlights += count
            seconds += segment.duration
        if current:
            groups.append(current)

        window_by_index = {w.segment.index: w for w in windows}
        chunks: list[Chunk] = []
        for number, group in enumerate(groups):
            first, last = segments[group[0]], segments[group[-1]]
            start_frame = int(round(first.start * fps))
            end_frame = (
                span_frames if number == len(groups) - 1
                else int(round(last.end * fps))
            )
            offset = start_frame / fps

            moved: dict[int, Segment] = {}
            for position in group:
                segment = segments[position]
                moved[segment.index] = replace(segment, start=segment.start - offset)

            chunk_timeline = Timeline(
                segments=list(moved.values()),
                total_duration=(end_frame - start_frame) / fps,
                mode=self.timeline.mode,
            )
            chunk_windows = [
                replace(
                    window_by_index[index],
                    segment=segment,
                    start=window_by_index[index].start - offset,
                )
                for index, segment in moved.items()
                if index in window_by_index
            ]
            chunk_words = [
                replace(word, start=word.start - offset, end=word.end - offset)
                for word in words
                if word.segment_index in moved
            ]

            chunks.append(
                Chunk(
                    index=number,
                    count=len(groups),
                    fps=fps,
                    start_frame=start_frame,
                    end_frame=end_frame,
                    span_frames=span_frames,
                    timeline=chunk_timeline,
                    windows=chunk_windows,
                    words=chunk_words,
                    video=parts_dir / f"part_{number:03d}.mp4",
                    audio=parts_dir / f"part_{number:03d}.flac",
                )
            )
        return chunks

    @staticmethod
    def _chunk_is_done(chunk: Chunk) -> bool:
        """A chunk counts as finished only once BOTH of its files are in place.

        Each is written under a leading ~ and renamed when FFmpeg exits cleanly,
        so a file at its final name is a complete file.
        """
        return (
            chunk.video.is_file() and chunk.video.stat().st_size > 0
            and chunk.audio.is_file() and chunk.audio.stat().st_size > 0
        )

    def _finish_chunk(self, chunk: Chunk) -> None:
        """Move a chunk's two temporary files to their final names."""
        for path in (chunk.video, chunk.audio):
            staged = path.with_name("~" + path.name)
            if not staged.is_file():
                raise QVGError(
                    f"{chunk.label} reported success but {staged.name} is missing."
                )
            staged.replace(path)

    def _render_chunked(
        self,
        request: RenderRequest,
        asset_dir: Path,
        background: Path,
        background_is_video: bool,
        layers: MotionLayers,
        cards: dict[str, Path],
        windows: list[CardWindow],
        words: list[WordWindow],
        highlights: dict[tuple[int, int], HighlightAsset],
    ) -> videosvc.OutputReport:
        config = self.config
        total = self.timeline.total_duration
        parts_dir = ensure_dir(
            asset_dir / f"parts_{self._render_fingerprint(request)}"
        )
        chunks = self._plan_chunks(request, windows, words, parts_dir)

        if request.subtitle_file is not None and request.subtitle_file.is_file():
            raise QVGError(
                "subtitles.burn_in cannot be used with a chunked render.",
                hint="Set subtitles.burn_in to false in the config. The .srt and "
                ".ass files are still written, and YouTube will show them as "
                "selectable captions.",
            )

        done = [chunk for chunk in chunks if self._chunk_is_done(chunk)]
        todo = [chunk for chunk in chunks if chunk not in done]
        memory = available_memory_mb()
        workers = min(config.render.worker_count(memory), len(todo)) or 1
        if memory is not None:
            self.log.info(
                "%.1f GB of memory free; encoding %d chunk(s) at a time",
                memory / 1024, workers,
            )

        self.log.info(
            "Chunked render: %d chunks over %.1fs, %d worker(s)%s",
            len(chunks), total, workers,
            f", {len(done)} already rendered" if done else "",
        )
        for chunk in done:
            self.log.info("Reusing %s", chunk.label)

        def check_space(_task, _seconds) -> None:
            """Stop the batch while the finished chunks are still safe."""
            if free_space_mb(parts_dir) < self._SPACE_FLOOR_MB:
                raise QVGError(
                    "The drive is nearly full, so the render stopped rather than "
                    "writing a chunk it could not finish.",
                    hint="Free some space and run the same command again - the "
                    "chunks already rendered are kept and will not be redone.",
                )

        tasks = []
        by_task: dict[int, Chunk] = {}
        for chunk in todo:
            job = self._build_job(
                request, background, background_is_video, layers, cards,
                chunk.windows, chunk.words, highlights, chunk=chunk,
            )
            # Every worker gets its own share of the cores. Left at 0 each
            # encoder would start as many threads as there are cores, and four
            # of those on four cores spend their time changing places.
            threads = max(1, (os.cpu_count() or workers) // workers)
            job.output_options.extend(["-threads", str(threads)])
            task = videosvc.ParallelTask(
                job=job,
                label=chunk.label,
                filter_script=parts_dir / f"part_{chunk.index:03d}.filters.txt",
                weight=chunk.frames / max(1, request.fps),
            )
            by_task[id(task)] = chunk
            tasks.append(task)

        if tasks:
            elapsed = videosvc.run_parallel(
                tasks,
                workers=workers,
                description=f"Rendering {len(tasks)} chunk(s)",
                on_done=lambda task, seconds: (
                    self._finish_chunk(by_task[id(task)]),
                    check_space(task, seconds),
                ),
            )
            self.log.info("All chunks rendered in %.1fs", elapsed)

        self.log.info("Joining %d chunks", len(chunks))
        video_list = videosvc.write_concat_list(
            [chunk.video for chunk in chunks], parts_dir / "video_parts.txt"
        )
        audio_list = videosvc.write_concat_list(
            [chunk.audio for chunk in chunks], parts_dir / "audio_parts.txt"
        )

        self.log.info("Finalizing MP4")
        elapsed = videosvc.run_job(
            self._build_join_job(request, video_list, audio_list, total),
            description="Writing the finished file",
        )
        self.log.info("Joined and muxed in %.1fs", elapsed)

        if not config.render.keep_parts:
            shutil.rmtree(parts_dir, ignore_errors=True)

        return videosvc.verify_output(request.output, total)

    def _build_join_job(
        self, request: RenderRequest, video_list: Path, audio_list: Path, total: float
    ) -> videosvc.FFmpegJob:
        """Join the chunks and write the finished MP4 in one pass.

        Both inputs are concat lists, so the parts are read end to end without
        an intermediate file. The picture is copied through - it was encoded
        once, in the chunks, and is not touched again - and only the sound is
        encoded, once, from the lossless parts.
        """
        config = self.config
        job = videosvc.FFmpegJob(duration=total)
        job.add_input(video_list, "-f", "concat", "-safe", "0")
        job.add_input(audio_list, "-f", "concat", "-safe", "0")
        job.maps = ["-map", "0:v:0", "-map", "1:a:0"]
        bitrate = (
            config.preview.audio_bitrate if request.preview else config.video.audio_bitrate
        )
        job.output_options = [
            "-c:v", "copy",
            "-c:a", config.video.audio_codec,
            "-b:a", bitrate,
            "-ar", str(config.video.audio_sample_rate),
            "-ac", "2",
            *videosvc.metadata_options(
                config, f"Surah {self.surah.surah_number} - {self.surah.name_english}"
            ),
        ]
        if config.video.faststart:
            job.output_options.extend(["-movflags", "+faststart"])
        job.output = request.output.with_name(
            f"~{request.output.stem}.part{request.output.suffix}"
        )
        job.final_output = request.output
        return job

    # -- entry point ---------------------------------------------------------
    def render(self, request: RenderRequest) -> videosvc.OutputReport:
        config = self.config
        asset_dir = self._asset_dir(request)
        ensure_dir(request.output.parent)

        # Generated PNGs (backdrop, layers, one card per segment) plus the
        # encoded file. Measured: a 128 s 1080p render writes ~50 MB of assets
        # and a ~39 MB MP4, so 0.30 MB/s. The allowance below is roughly 5x that
        # for headroom - the previous 26 MB/s figure demanded 3.4 GB and would
        # have refused renders that comfortably fit.
        if config.render.chunked:
            estimated_mb = self._chunked_space_estimate(request)
        elif request.preview:
            estimated_mb = 40 + self.timeline.total_duration * 0.3
        else:
            estimated_mb = 120 + self.timeline.total_duration * 1.5
        require_free_space(config.temp_dir, estimated_mb)

        background, background_is_video = self._resolve_background(request, asset_dir)
        layers = self._prepare_layers(request, asset_dir)

        # The card windows are needed before the cards themselves: they decide
        # when each card is fully opaque, which is the only stretch a word
        # highlight is allowed to occupy.
        windows = card_windows(config, self.timeline)
        words = word_windows(config, self.timeline, self.word_timings, windows)
        cards, highlights = self._build_cards(request, asset_dir, words)

        if config.render.chunked:
            return self._render_chunked(
                request, asset_dir, background, background_is_video, layers,
                cards, windows, words, highlights,
            )

        job = self._build_job(
            request, background, background_is_video, layers, cards, windows,
            words, highlights,
        )

        label = "Preview" if request.preview else "Final render"
        self.log.info(
            "%s: %dx%d at %d fps, %.1fs, %d inputs",
            label, request.width, request.height, request.fps,
            self.timeline.total_duration, len(job.inputs),
        )

        self.log.info("Finalizing MP4")
        filter_script = asset_dir / "filter_graph.txt"
        elapsed = videosvc.run_job(
            job,
            description=f"{label} {request.width}x{request.height}",
            filter_script=filter_script,
        )
        self.log.info("Encoding finished in %.1fs", elapsed)

        return videosvc.verify_output(request.output, self.timeline.total_duration)
