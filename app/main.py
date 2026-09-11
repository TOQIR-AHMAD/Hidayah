"""Command line interface.

    python run.py validate     check everything before rendering
    python run.py preview      fast 640x360 draft
    python run.py render       final 1920x1080 MP4 + thumbnail + subtitles
    python run.py thumbnail    just the 1280x720 thumbnail
    python run.py info         show the computed timeline
    python run.py fetch-text   download the verified Quran text and translation
    python run.py fonts        download the SIL OFL fonts
    python run.py fetch-audio  opt-in recitation downloader (disabled by default)
    python run.py clean        clear temp/
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.table import Table

from app import __version__
from app.config import Config, load_config
from app.models import surah_index
from app.models.surah import Surah, load_surah
from app.rendering.renderer import RenderRequest, Renderer
from app.rendering.timeline import Timeline, build_timeline
from app.services import audio as audiosvc
from app.services import subtitles as subsvc
from app.services import text as textsvc
from app.services import thumbnail as thumbsvc
from app.services import word_timings as word_timings_service
from app.utils import ffmpeg as ff
from app.utils.files import clean_dir, find_first_media, free_space_mb, relative_to_root
from app.utils.fonts import FontResolver, describe_bundled, download_fonts
from app.utils.logging import QVGError, banner, console, get_logger, setup_logging, success

OK = "[green]OK[/green]"
FAIL = "[error]FAIL[/error]"
WARN = "[warn]WARN[/warn]"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class Check:
    name: str
    status: str
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.status == FAIL


class Validator:
    """Runs every pre-flight check and reports them as one table."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.checks: list[Check] = []
        self.surah: Optional[Surah] = None
        self.audio_mode: str = ""
        self.hints: list[str] = []

    def _add(self, name: str, ok: bool, detail: str = "", warn_only: bool = False) -> bool:
        """Record one check.

        `warn_only` means "flag this, but never block the render". Callers pass
        it only when there IS something to flag, so it wins over `ok` - writing
        `OK if ok else ...` printed a green OK next to warnings like "reciter
        rights not confirmed", which is precisely the row meant to stand out.
        """
        if warn_only:
            status = WARN
        else:
            status = OK if ok else FAIL
        self.checks.append(Check(name, status, detail))
        return ok

    def run(self) -> bool:
        self._check_ffmpeg()
        self._check_data()
        self._check_fonts()
        self._check_background()
        self._check_audio()
        self._check_word_timings()
        self._check_disk()
        return not any(check.failed for check in self.checks)

    # -- individual checks ---------------------------------------------------
    def _check_ffmpeg(self) -> None:
        try:
            tools = ff.find_ffmpeg()
        except QVGError as exc:
            self._add("FFmpeg", False, "not found")
            self.hints.append(exc.hint)
            return
        self._add("FFmpeg", True, f"{tools.version}  ({tools.ffmpeg})")
        if not tools.has_ffprobe:
            self._add(
                "ffprobe", True,
                "not found - durations are read from FFmpeg instead",
                warn_only=True,
            )

    def _check_data(self) -> None:
        path = self.config.data_file
        if not path.is_file():
            self._add("Quran data file", False, f"{relative_to_root(path)} is missing")
            self.hints.append("Create it with:  python run.py fetch-text")
            return
        try:
            surah = load_surah(path)
        except QVGError as exc:
            self._add("Quran data file", False, exc.message.splitlines()[0])
            self.hints.append(exc.hint)
            return

        self.surah = surah
        self._add("Quran data file", True, relative_to_root(path))
        self._add("Ayah count", True, f"{surah.ayah_count} ayahs")
        numbers = [a.number for a in surah.ayahs]
        in_order = numbers == list(range(1, surah.ayah_count + 1))
        self._add(
            "Ayah numbering", in_order,
            f"1-{surah.ayah_count} in order" if in_order else "unexpected",
        )
        if surah.surah_number != self.config.project.surah:
            self._add(
                "Surah", False,
                f"config asks for surah {self.config.project.surah}, but "
                f"{relative_to_root(path)} holds surah {surah.surah_number}",
            )
        self._add(
            "Arabic text", all(a.arabic.strip() for a in surah.ayahs),
            f"edition: {surah.source.arabic_edition or 'not recorded'}",
        )
        self._add(
            "Urdu translation", all(a.urdu_translation.strip() for a in surah.ayahs),
            f"edition: {surah.source.urdu_edition or 'not recorded'}",
        )

    def _check_fonts(self) -> None:
        resolver = FontResolver(self.config.fonts_dir, self.config.fonts.use_system_fonts)
        spec = self.config.fonts
        roles = (
            ("arabic", spec.arabic, spec.arabic_fallbacks),
            ("arabic_display", spec.arabic_display, spec.arabic_display_fallbacks),
            ("urdu", spec.urdu, spec.urdu_fallbacks),
            ("latin", spec.latin, spec.latin_fallbacks),
        )
        missing = False
        for role, primary, fallbacks in roles:
            try:
                choice = resolver.resolve(role, primary, fallbacks)
            except QVGError as exc:
                self._add(f"Font: {role}", False, f"'{primary}' not found")
                self.hints.append(exc.hint)
                missing = True
                continue
            detail = choice.name
            if choice.is_fallback:
                detail += f"   (fallback - '{primary}' was not found)"
            self._add(f"Font: {role}", True, detail)

        if not missing and self.surah is not None:
            self._check_glyph_coverage(resolver)

        backend = textsvc.shaping_backend()
        self._add(
            "Text shaping", backend == "harfbuzz",
            "HarfBuzz + FreeType" if backend == "harfbuzz"
            else "reduced fallback - run: pip install uharfbuzz freetype-py",
            warn_only=backend != "harfbuzz",
        )

    def _check_glyph_coverage(self, resolver: FontResolver) -> None:
        """Warn if a font cannot draw some character in the actual text."""
        assert self.surah is not None
        spec = self.config.fonts
        arabic_font = resolver.resolve("arabic", spec.arabic, spec.arabic_fallbacks).path
        urdu_font = resolver.resolve("urdu", spec.urdu, spec.urdu_fallbacks).path

        arabic_text = " ".join(a.arabic for a in self.surah.ayahs)
        urdu_text = " ".join(a.urdu_translation for a in self.surah.ayahs)

        for label, font_path, sample in (
            ("Arabic glyph coverage", arabic_font, arabic_text),
            ("Urdu glyph coverage", urdu_font, urdu_text),
        ):
            missing = textsvc.check_font_covers(font_path, sample)
            if missing:
                self._add(
                    label, False,
                    f"{font_path.name} has no glyph for: {textsvc.describe_missing(missing[:6])}",
                    warn_only=True,
                )
                self.hints.append(
                    f"'{font_path.name}' cannot draw every character in the text. "
                    "Run `python run.py fonts` to install a font that can."
                )
            else:
                self._add(label, True, f"{font_path.name} covers every character")

    def _check_background(self) -> None:
        setting = self.config.background.source.strip()
        if setting.lower() == "generated":
            self._add("Background", True, "generated backdrop (no user asset needed)")
            return
        if setting.lower() == "auto":
            found = find_first_media(self.config.backgrounds_dir)
            if found is None:
                # The glass style has no ground of its own, so an empty folder
                # means the design is missing its main element. Still not a
                # failure - the render works - but it should be said here
                # rather than only in the render log.
                needs_artwork = self.config.theme.style == "glass"
                self._add(
                    "Background", True,
                    f"no file in {relative_to_root(self.config.backgrounds_dir)}/ - "
                    + (
                        "theme.style is 'glass', which is designed to sit on "
                        "your own artwork; falling back to the generated backdrop"
                        if needs_artwork
                        else "using the generated backdrop"
                    ),
                    warn_only=needs_artwork,
                )
                if needs_artwork:
                    self.hints.append(
                        "Put the plain plate (no text on it) in "
                        f"{relative_to_root(self.config.backgrounds_dir)}/ - see "
                        "the README in that folder."
                    )
            else:
                self._add("Background", True, found.name)
            return

        candidate = self.config.backgrounds_dir / setting
        if candidate.is_file():
            self._add("Background", True, candidate.name)
        else:
            self._add("Background", False, f"'{setting}' not found in assets/backgrounds/")

    def _check_audio(self) -> None:
        if self.surah is None:
            self._add("Audio files", False, "skipped - the Quran data file did not load")
            return
        try:
            ff.find_ffmpeg()
        except QVGError:
            self._add("Audio files", False, "skipped - FFmpeg is required to inspect audio")
            return

        mode, issues = audiosvc.check_audio(self.config, self.surah)
        self.audio_mode = mode
        label = "one complete recording" if mode == "full_surah" else "one file per ayah"
        self._add("Recitation mode", True, label)

        if not issues:
            self._add("Arabic recitation", True, "all files present and decodable")
            if not self.config.content.urdu_translation:
                self._add(
                    "Urdu section", True,
                    "off - Arabic text and recitation only "
                    "(content.urdu_translation)",
                    warn_only=True,
                )
            elif self.config.audio.urdu_narration:
                self._add("Urdu narration", True, "all files present and decodable")
            else:
                self._add(
                    "Urdu narration", True,
                    "off - the translation is shown silently "
                    "(audio.urdu_narration)",
                    warn_only=True,
                )
        else:
            missing = [i for i in issues if i.kind == "missing"]
            broken = [i for i in issues if i.kind != "missing"]
            self._add(
                "Audio files", False,
                f"{len(missing)} missing, {len(broken)} unreadable",
            )
            self.hints.append(audiosvc.format_issues(issues, self.config.root))

        reciter = self.config.reciter
        if not reciter.is_configured:
            self._add(
                "Reciter credit", True,
                "reciter.name is still 'RECITER_NAME' in config.yaml",
                warn_only=True,
            )
        elif not reciter.rights_confirmed:
            self._add(
                "Recitation rights", True,
                "reciter.rights_confirmed is false - confirm you may publish this recording",
                warn_only=True,
            )
        else:
            self._add("Reciter credit", True, f"{reciter.name} ({reciter.source})")

    def _check_word_timings(self) -> None:
        """Say whether the highlight is following published timings or a guess.

        Never a failure: the video renders either way. But an estimated ayah
        drifts against the voice, and that is invisible until you watch it, so
        it is worth naming here.
        """

        if not self.config.content.word_highlight:
            self._add("Word highlight", True, "off (content.word_highlight)")
            return
        if self.surah is None:
            self._add("Word highlight", False, "skipped - the Quran data file did not load")
            return

        durations: dict[int, float] = {}
        try:
            plan = audiosvc.build_plan(self.config, self.surah)
            durations = {n: c.duration for n, c in plan.recitation.items()}
        except QVGError:
            # No usable audio; the audio check has already said so.
            pass

        timings = word_timings_service.load(self.config, self.surah, durations)
        estimated = [n for n, t in timings.items() if t.is_estimated]
        detail = word_timings_service.describe(timings)
        self._add("Word highlight", True, detail, warn_only=bool(estimated))
        if estimated:
            self.hints.append(
                "For timings measured against the recitation, run:  "
                "python run.py fetch-word-timings"
            )

    def _check_disk(self) -> None:
        free = free_space_mb(self.config.temp_dir)
        self._add(
            "Disk space", free > 900, f"{free / 1024:.1f} GB free on the temp drive",
            warn_only=900 < free <= 2500,
        )

    # -- output --------------------------------------------------------------
    def report(self) -> None:
        table = Table(box=None, pad_edge=False, show_header=True, header_style="accent")
        table.add_column("Check", style="white", no_wrap=True)
        table.add_column("", no_wrap=True)
        table.add_column("Detail", style="muted", overflow="fold")
        for check in self.checks:
            table.add_row(check.name, check.status, check.detail)
        console.print(table)

        if self.hints:
            console.print()
            for hint in self.hints:
                if hint:
                    console.print(f"[warn]->[/warn] {hint}")


# ---------------------------------------------------------------------------
# Shared preparation
# ---------------------------------------------------------------------------

@dataclass
class Prepared:
    config: Config
    surah: Surah
    plan: audiosvc.AudioPlan
    timeline: Timeline
    # Per-word recitation timings, loaded once here because the pagination
    # needs them too - they are where a split ayah's audio is cut.
    word_timings: dict = field(default_factory=dict)


def _silent_plan(config: Config, surah: Surah) -> audiosvc.AudioPlan:
    """Silent stand-in clips so the layout can be checked before audio exists.

    The durations are estimated from how much text there is, which gives the
    same rhythm a real recitation would - but this is silence, and it is never
    used unless --no-audio is passed explicitly.
    """
    log = get_logger()
    log.warning("Rendering WITHOUT audio: silent placeholder clips are being used.")

    silence_dir = config.temp_dir / "silence"
    silence_dir.mkdir(parents=True, exist_ok=True)
    plan = audiosvc.AudioPlan(mode="silent")

    def silent_clip(seconds: float, label: str) -> audiosvc.AudioClip:
        seconds = round(max(1.5, seconds), 2)
        path = silence_dir / f"silence_{seconds:.2f}s.wav"
        if not path.is_file():
            ff.run_ffmpeg(
                [
                    "-f", "lavfi",
                    "-i", f"anullsrc=r={config.video.audio_sample_rate}:cl=stereo",
                    "-t", f"{seconds:.2f}",
                    str(path),
                ],
                description="Generating silence",
            )
        return audiosvc.AudioClip(path=path, duration=seconds, label=label)

    for ayah in surah.ayahs:
        words = max(1, ayah.word_count)
        plan.recitation[ayah.number] = silent_clip(
            2.2 + words * 0.85, f"silent recitation {ayah.number:03d}"
        )
        urdu_words = max(1, len(ayah.urdu_translation.split()))
        plan.urdu[ayah.number] = silent_clip(
            2.0 + urdu_words * 0.42, f"silent narration {ayah.number:03d}"
        )
    return plan


def prepare(config: Config, max_ayahs: int = 0, no_audio: bool = False) -> Prepared:
    log = get_logger()
    log.info("Loading %s", relative_to_root(config.data_file))
    surah = load_surah(config.data_file)
    log.info("Validating %d ayahs", surah.ayah_count)

    if no_audio:
        plan = _silent_plan(config, surah)
    else:
        log.info("Loading audio")
        plan = audiosvc.build_plan(config, surah)
        log.info(
            "Audio ready: %s, %.1fs of recitation and narration",
            "one complete recording" if plan.mode == "full_surah" else "one file per ayah",
            plan.total_audio_seconds,
        )

    timings = word_timings_service.load(
        config, surah, {n: clip.duration for n, clip in plan.recitation.items()}
    )
    pages = _paginate(config, surah)
    timeline = build_timeline(
        config, surah, plan, max_ayahs=max_ayahs, pages=pages, timings=timings
    )
    split = sum(1 for group in pages.values() if len(group) > 1)
    if split:
        log.info(
            "%d ayah(s) are too long for one screen and are shown over several",
            split,
        )
    log.info(
        "Timeline: %d segments, %.1fs total", len(timeline), timeline.total_duration
    )
    return Prepared(
        config=config, surah=surah, plan=plan, timeline=timeline, word_timings=timings
    )


def _paginate(config: Config, surah: Surah) -> dict[int, list]:
    """Which ayahs need more than one screen, and where they divide.

    Measured at the FINAL resolution whatever is being rendered: every size in
    the theme is a fraction of the frame height, so the split a preview would
    find is the same one, and pinning it here keeps a preview honest about what
    the final render will do.
    """
    if config.theme.size_arabic_page_min <= 0:
        return {}
    from app.rendering.compositions import CardBuilder

    builder = CardBuilder(config, surah, config.video.width, config.video.height)
    return {ayah.number: builder.paginate(ayah) for ayah in surah.ayahs}


def _require_valid(config: Config, no_audio: bool) -> None:
    validator = Validator(config)
    ok = validator.run()
    if ok:
        return
    # An audio failure is expected and fine when rendering deliberately silent.
    fatal = [c for c in validator.checks if c.failed]
    if no_audio:
        fatal = [c for c in fatal if "audio" not in c.name.lower()]
    if not fatal:
        return
    validator.report()
    raise QVGError(
        "Validation failed, so nothing was rendered.",
        hint="Fix the items marked FAIL above and run `python run.py validate` again.",
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_validate(config: Config, args: argparse.Namespace) -> int:
    banner("Validating the project", str(config.root))
    validator = Validator(config)
    ok = validator.run()
    validator.report()
    console.print()
    if ok:
        success("Everything checks out. Next: python run.py preview")
        return 0
    console.print("[error]Validation failed.[/error] Fix the items marked FAIL above.")
    return 1


def cmd_info(config: Config, args: argparse.Namespace) -> int:
    prepared = prepare(config, no_audio=args.no_audio)
    surah = prepared.surah

    console.print()
    table = Table(title="Surah", title_style="accent", box=None, pad_edge=False)
    table.add_column("", style="muted", no_wrap=True)
    table.add_column("", style="white")
    for key, value in surah.describe() + surah.source.describe():
        table.add_row(key, value)
    console.print(table)

    console.print()
    timeline_table = Table(title="Timeline", title_style="accent", box=None, pad_edge=False)
    timeline_table.add_column("Segment", style="white", no_wrap=True)
    timeline_table.add_column("Start", style="muted", no_wrap=True)
    timeline_table.add_column("End", style="muted", no_wrap=True)
    timeline_table.add_column("Length", style="gold1", no_wrap=True)
    for row in prepared.timeline.summary_rows():
        timeline_table.add_row(*row)
    console.print(timeline_table)

    minutes, seconds = divmod(prepared.timeline.total_duration, 60)
    console.print(
        f"\n[accent]Total:[/accent] {int(minutes)}m {seconds:04.1f}s  "
        f"[muted]({len(prepared.timeline)} segments, "
        f"{subsvc.cue_count(config, prepared.timeline)} subtitle cues)[/muted]"
    )
    return 0


def _render(config: Config, args: argparse.Namespace, preview: bool) -> int:
    _require_valid(config, args.no_audio)

    max_ayahs = config.preview.max_ayahs if preview else 0
    if getattr(args, "ayahs", 0):
        max_ayahs = args.ayahs

    prepared = prepare(config, max_ayahs=max_ayahs, no_audio=args.no_audio)

    if preview:
        width, height, fps = config.preview.width, config.preview.height, config.preview.fps
        output = config.output_dir / f"{config.project.slug}_preview.mp4"
    else:
        width, height, fps = config.video.width, config.video.height, config.video.fps
        output = config.output_file("mp4")

    # Burned-in subtitles have to be authored at the render resolution, so the
    # .ass file is written before the encode rather than after it.
    burned: Optional[Path] = None
    if config.subtitles.enabled and config.subtitles.burn_in:
        burned = subsvc.write_ass(
            config, prepared.timeline, prepared.surah,
            config.temp_dir / f"burn_{width}x{height}.ass", width, height,
        )

    request = RenderRequest(
        width=width, height=height, fps=fps,
        preview=preview, output=output, subtitle_file=burned,
    )

    renderer = Renderer(
        config, prepared.surah, prepared.plan, prepared.timeline,
        word_timings=prepared.word_timings,
    )
    report = renderer.render(request)

    written: list[Path] = [report.path]
    if not preview:
        get_logger().info("Generating subtitles")
        written.extend(subsvc.generate(config, prepared.timeline, prepared.surah))
        get_logger().info("Generating thumbnail")
        written.append(thumbsvc.generate(config, prepared.surah))

    console.print()
    table = Table(box=None, pad_edge=False, show_header=False)
    table.add_column("", style="muted", no_wrap=True)
    table.add_column("", style="white")
    for key, value in report.rows():
        table.add_row(key, value)
    console.print(table)

    console.print()
    for path in written:
        console.print(f"  [accent]->[/accent] {relative_to_root(path)}")
    console.print()
    success(relative_to_root(report.path))

    if preview:
        console.print(
            "[muted]Check the Arabic, the Urdu and the timing, then run: "
            "python run.py render[/muted]"
        )
    return 0


def cmd_preview(config: Config, args: argparse.Namespace) -> int:
    banner("Preview render", f"{config.preview.width}x{config.preview.height}, fast encode")
    return _render(config, args, preview=True)


def cmd_render(config: Config, args: argparse.Namespace) -> int:
    banner(
        "Final render",
        f"{config.video.width}x{config.video.height} @ {config.video.fps}fps, "
        f"CRF {config.video.crf}, preset {config.video.preset}",
    )
    return _render(config, args, preview=False)


def cmd_thumbnail(config: Config, args: argparse.Namespace) -> int:
    banner("Thumbnail", f"{config.thumbnail.width}x{config.thumbnail.height}")
    surah = load_surah(config.data_file)
    path = thumbsvc.generate(config, surah)
    success(relative_to_root(path))
    return 0


def cmd_subtitles(config: Config, args: argparse.Namespace) -> int:
    banner("Subtitles", "timings taken from the real audio durations")
    prepared = prepare(config, no_audio=args.no_audio)
    written = subsvc.generate(config, prepared.timeline, prepared.surah)
    if not written:
        console.print("[warn]subtitles.enabled is false in config.yaml.[/warn]")
        return 0
    for path in written:
        console.print(f"  [accent]->[/accent] {relative_to_root(path)}")
    success(f"{len(written)} subtitle file(s) written")
    return 0


def cmd_fetch_text(config: Config, args: argparse.Namespace) -> int:
    from app.services.quran_source import fetch_surah

    number = args.surah or config.project.surah
    banner(
        f"Fetching the Quran text for surah {number}",
        "verified published editions - never generated",
    )
    surah = fetch_surah(
        number,
        config.data_file,
        arabic_edition=args.arabic_edition,
        urdu_edition=args.urdu_edition,
        force=args.force,
    )
    table = Table(box=None, pad_edge=False, show_header=False)
    table.add_column("", style="muted", no_wrap=True)
    table.add_column("", style="white")
    for key, value in surah.source.describe():
        table.add_row(key, value)
    console.print(table)
    console.print(
        "\n[warn]Please check the text against a printed mushaf before publishing.[/warn]"
    )
    success(relative_to_root(config.data_file))
    return 0


def cmd_fetch_word_timings(config: Config, args: argparse.Namespace) -> int:
    from app.services import word_timings as word_timings_service

    banner(
        "Fetching word timings",
        "published timings only - no audio is downloaded here",
    )
    surah = load_surah(config.data_file)
    destination = word_timings_service.fetch(
        config, surah, recitation_id=args.recitation_id
    )

    # Read it straight back so the report reflects what a render will actually
    # accept, not merely what was written. The durations come from the real
    # files when they are present, because that is one of the checks.
    try:
        plan = audiosvc.build_plan(config, surah)
        durations = {n: c.duration for n, c in plan.recitation.items()}
    except QVGError:
        durations = {}
    timings = word_timings_service.load(config, surah, durations)
    console.print(f"  [white]{word_timings_service.describe(timings)}[/white]")
    console.print(
        "\n[muted]These describe the recording in assets/audio. If you change "
        "reciter, re-run this with the matching --recitation-id.[/muted]"
    )
    success(relative_to_root(destination))
    return 0


def cmd_fonts(config: Config, args: argparse.Namespace) -> int:
    banner("Fonts", "SIL Open Font License - redistribution is permitted")
    for name, description in describe_bundled():
        console.print(f"  [muted]{name:<34}[/muted] {description}")
    console.print()
    messages = download_fonts(config.fonts_dir, force=args.force)
    for message in messages:
        style = "error" if message.startswith("FAILED") else "white"
        console.print(f"  [{style}]{message}[/{style}]")

    failures = [m for m in messages if m.startswith("FAILED")]
    console.print()
    if failures:
        # Reporting SUCCESS here while every download failed sent people on to
        # `validate`, which then complained about missing fonts.
        console.print(
            f"[error]{len(failures)} of {len(messages)} downloads failed.[/error]\n"
            "[muted]Check your internet connection and run `python run.py fonts` "
            "again. Fonts already in assets/fonts/ were left untouched.[/muted]"
        )
        return 1
    success(f"Fonts are in {relative_to_root(config.fonts_dir)}/")
    return 0


def cmd_fetch_audio(config: Config, args: argparse.Namespace) -> int:
    """Opt-in downloader. Off by default; never runs as part of a render."""
    banner("Recitation downloader", "opt-in - nothing is downloaded automatically")
    sources = config.audio_sources

    if not sources.enabled:
        console.print(
            "[warn]audio_sources.enabled is false in config.yaml.[/warn]\n\n"
            "This project never downloads recitation on its own. To use it:\n"
            "  1. Find a recitation you are allowed to use and copy its URL pattern.\n"
            "  2. In config.yaml set audio_sources.enabled: true and fill in\n"
            "     recitation_url_template / urdu_url_template, using {ayah:03d}\n"
            "     where the ayah number goes.\n"
            "  3. Record who the reciter is under the `reciter:` block.\n"
            "  4. Re-run:  python run.py fetch-audio --accept-source-license\n\n"
            "Or simply copy your own files into assets/audio/recitation/ and\n"
            "assets/audio/urdu/ as 001.mp3 ... 007.mp3."
        )
        return 1

    if not args.accept_source_license:
        console.print(
            "[warn]Confirm your right to use these recordings.[/warn]\n\n"
            f"  Reciter : {config.reciter.name}\n"
            f"  Source  : {config.reciter.source}\n"
            f"  License : {config.reciter.license}\n\n"
            "Quran recitations are recordings with their own copyright. Only "
            "download material you own, have permission for, or that is offered "
            "under a licence permitting reuse.\n\n"
            "Re-run with:  python run.py fetch-audio --accept-source-license"
        )
        return 1

    log = get_logger()
    downloaded = 0
    failed: list[str] = []

    jobs = []
    if sources.recitation_url_template:
        jobs.append((sources.recitation_url_template, config.recitation_dir, "recitation"))
    if sources.urdu_url_template:
        jobs.append((sources.urdu_url_template, config.urdu_dir, "Urdu narration"))
    if not jobs:
        raise QVGError(
            "No URL templates are configured.",
            hint="Set audio_sources.recitation_url_template in config.yaml.",
        )

    surah_number = config.project.surah
    total = surah_index.ayah_count(surah_number)
    offset = surah_index.global_offset(surah_number)
    if offset:
        console.print(
            f"  [muted]surah {surah_number}: ayahs 1-{total}, "
            f"mushaf-wide {offset + 1}-{offset + total}[/muted]"
        )

    for template, directory, label in jobs:
        directory.mkdir(parents=True, exist_ok=True)
        for number in range(1, total + 1):
            # str.format understands the format spec, so a template can use
            # either {ayah} or {ayah:03d}. {global_ayah} is the mushaf-wide
            # number, which is what the Islamic Network CDN indexes by.
            try:
                url = template.format(ayah=number, global_ayah=offset + number)
            except (KeyError, IndexError, ValueError) as exc:
                raise QVGError(
                    f"The URL template '{template}' could not be filled in: {exc}",
                    hint="Use {ayah} or {ayah:03d} for the number inside the surah, "
                    "or {global_ayah} for the mushaf-wide number, for example:\n"
                    '  recitation_url_template: "https://example.org/audio/{ayah:03d}.mp3"',
                )
            destination = directory / f"{number:03d}.mp3"
            if destination.is_file() and not args.force:
                console.print(f"  [muted]kept    {destination.name} ({label})[/muted]")
                continue
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "quran-video-generator/1.0"}
                )
                with urllib.request.urlopen(request, timeout=90) as response:
                    payload = response.read()
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                failed.append(f"{destination.name} ({label}): {exc}")
                console.print(f"  [error]FAILED  {destination.name}[/error]  {exc}")
                continue
            if len(payload) < 2048:
                failed.append(f"{destination.name}: the response was too small to be audio")
                continue
            destination.write_bytes(payload)
            downloaded += 1
            console.print(f"  saved   {destination.name}  ({len(payload) / 1024:.0f} KB)")
            log.info("Downloaded %s", destination)

    console.print()
    if failed:
        console.print(f"[warn]{len(failed)} file(s) could not be downloaded.[/warn]")
    if downloaded:
        success(f"{downloaded} file(s) downloaded. Next: python run.py validate")
    return 0 if not failed else 1


def cmd_clean(config: Config, args: argparse.Namespace) -> int:
    banner("Cleaning", relative_to_root(config.temp_dir))
    clean_dir(config.temp_dir)
    success("temp/ is empty")
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python run.py",
        description="Quran video generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Typical order:  validate  ->  preview  ->  render",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    parser.add_argument(
        "--log-level", default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="how much detail to print",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        return subparsers.add_parser(name, help=help_text, description=help_text)

    add("validate", "Check data, fonts, audio, background and FFmpeg")

    for name, help_text in (
        ("preview", "Fast low-resolution draft for checking layout and timing"),
        ("render", "Final 1920x1080 MP4, plus the thumbnail and subtitles"),
    ):
        sub = add(name, help_text)
        sub.add_argument(
            "--no-audio", action="store_true",
            help="render with silence instead of recitation (layout check only)",
        )
        sub.add_argument(
            "--ayahs", type=int, default=0, metavar="N",
            help="render only the first N ayahs",
        )

    add("thumbnail", "Generate output/<slug>.jpg only")

    sub = add("subtitles", "Generate the .srt and .ass files only")
    sub.add_argument("--no-audio", action="store_true", help=argparse.SUPPRESS)

    sub = add("info", "Print the surah details and the computed timeline")
    sub.add_argument("--no-audio", action="store_true", help="use silent placeholder durations")

    sub = add("fetch-text", "Download the verified Quran text and Urdu translation")
    sub.add_argument(
        "--surah", type=int, default=0, metavar="N",
        help="surah to download, 1-114 (default: project.surah from config.yaml)",
    )
    sub.add_argument("--force", action="store_true", help="overwrite the data file")
    sub.add_argument("--arabic-edition", default="quran-uthmani")
    sub.add_argument("--urdu-edition", default="ur.junagarhi")

    sub = add("fetch-word-timings", "Download per-word recitation timings")
    sub.add_argument(
        "--recitation-id", type=int, default=0,
        help="quran.com recitation id (default: the one in config.yaml)",
    )

    sub = add("fonts", "Download the SIL OFL Arabic, Urdu and Latin fonts")
    sub.add_argument("--force", action="store_true", help="re-download fonts that already exist")

    sub = add("fetch-audio", "Opt-in recitation downloader (disabled by default)")
    sub.add_argument(
        "--accept-source-license", action="store_true",
        help="confirm you have the right to use the configured recordings",
    )
    sub.add_argument("--force", action="store_true", help="overwrite existing audio files")

    add("clean", "Delete everything in temp/")
    return parser


COMMANDS = {
    "validate": cmd_validate,
    "preview": cmd_preview,
    "render": cmd_render,
    "thumbnail": cmd_thumbnail,
    "subtitles": cmd_subtitles,
    "info": cmd_info,
    "fetch-text": cmd_fetch_text,
    "fetch-word-timings": cmd_fetch_word_timings,
    "fonts": cmd_fonts,
    "fetch-audio": cmd_fetch_audio,
    "clean": cmd_clean,
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except QVGError as exc:
        exc.render()
        return 2

    config.ensure_dirs()
    setup_logging(config.logs_dir, args.log_level)

    handler = COMMANDS[args.command]
    try:
        return handler(config, args)
    except QVGError as exc:
        get_logger().error("%s", exc.message)
        console.print()
        exc.render()
        return 1
    except KeyboardInterrupt:
        console.print("\n[warn]Interrupted.[/warn]")
        return 130
    except Exception as exc:  # unexpected: show the traceback, it is a bug
        get_logger().exception("Unhandled error in '%s'", args.command)
        console.print()
        console.print(
            f"[error]Unexpected error:[/error] {type(exc).__name__}: {exc}\n"
            f"[muted]The full traceback is in {relative_to_root(config.logs_dir)}/[/muted]"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
