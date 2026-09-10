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
from dataclasses import dataclass
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
    Timeline,
    WordWindow,
    card_windows,
    word_windows,
)
from app.services import video as videosvc
from app.services import word_timings as word_timings_service
from app.services.audio import AudioPlan
from app.utils.files import ensure_dir, find_first_media, is_video, require_free_space
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
    ) -> videosvc.FFmpegJob:
        config = self.config
        theme = config.theme
        animation = config.animation
        total = self.timeline.total_duration
        fps = request.fps
        w, h = request.width, request.height
        scale_to_frame = h / 1080.0

        job = videosvc.FFmpegJob(duration=total)
        # Every full-length layer holds exactly this many frames, so the layers,
        # the card track and the output all agree on the length to the frame.
        total_frames = anim.frame_span(0.0, total, fps)

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
                tile, tile, fps,
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
                float(w), float(h), fps,
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
            frame_in = animation.scaled(animation.intro_fade_in)
            frame_out = animation.scaled(animation.outro_fade_out)
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
                job.add_filter(
                    f"[{index}:v:0]{anim.still_source(fps, total_frames)},"
                    f"{anim.timed_alpha(max(0.0, word.start - lead), word.end + lead, fade, fade)},"
                    f"setsar=1[{label}]"
                )
                segment_start = starts.get(word.segment_index, word.start)
                y_expr = anim.text_rise(segment_start, rise, rise_duration)
                y = f"{asset.y}" if y_expr == "0" else f"{asset.y}+{y_expr}"
                job.add_filter(
                    f"[{current}][{label}]"
                    f"{anim.overlay_filter(str(asset.x), y)}"
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
        final_chain.append(f"trim=duration={total:.3f}")
        final_chain.append("setpts=PTS-STARTPTS")
        job.add_filter(f"[video]{','.join(final_chain)}[vout]")

        # --- audio ----------------------------------------------------------
        audio_labels: list[str] = []
        for position, segment in enumerate(self.timeline.with_audio):
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
        else:
            job.maps = ["-map", "[vout]"]

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
        if request.preview:
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
