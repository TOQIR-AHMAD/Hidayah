"""FFmpeg job assembly, live progress reporting and output verification."""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from app.config import Config
from app.utils import ffmpeg as ff
from app.utils.files import human_size
from app.utils.logging import QVGError, console, get_logger


@dataclass
class FFmpegInput:
    """One input file plus the options that must precede -i."""

    path: Path
    options: list[str] = field(default_factory=list)

    def as_args(self) -> list[str]:
        return [*self.options, "-i", str(self.path)]


@dataclass
class FFmpegJob:
    inputs: list[FFmpegInput] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    maps: list[str] = field(default_factory=list)
    output_options: list[str] = field(default_factory=list)
    output: Optional[Path] = None
    # Where `output` should end up once FFmpeg exits cleanly. Encoding straight
    # to the published path leaves a truncated file behind on interrupt.
    final_output: Optional[Path] = None
    duration: float = 0.0

    def add_input(self, path: Path, *options: str) -> int:
        """Register an input and return its index."""
        self.inputs.append(FFmpegInput(path=path, options=list(options)))
        return len(self.inputs) - 1

    def add_filter(self, expression: str) -> None:
        self.filters.append(expression)

    @property
    def filter_graph(self) -> str:
        return ";\n".join(self.filters)

    def build_args(self, filter_script: Optional[Path] = None) -> list[str]:
        args: list[str] = []
        for item in self.inputs:
            args.extend(item.as_args())

        if self.filters:
            if filter_script is not None:
                filter_script.parent.mkdir(parents=True, exist_ok=True)
                filter_script.write_text(self.filter_graph, encoding="utf-8")
                args.extend(["-filter_complex_script", str(filter_script)])
            else:
                args.extend(["-filter_complex", self.filter_graph])

        args.extend(self.maps)
        args.extend(self.output_options)
        if self.output is not None:
            args.append(str(self.output))
        return args


def encoder_options(config: Config, preview: bool) -> list[str]:
    """Video/audio encoding flags for either the final render or the preview."""
    video = config.video
    if preview:
        crf = config.preview.crf
        preset = config.preview.preset
        fps = config.preview.fps
        audio_bitrate = config.preview.audio_bitrate
    else:
        crf = video.crf
        preset = video.preset
        fps = video.fps
        audio_bitrate = video.audio_bitrate

    options = [
        "-c:v", video.video_codec,
        "-crf", str(crf),
        "-preset", preset,
        "-pix_fmt", video.pixel_format,
        "-r", str(fps),
        "-profile:v", "high",
        "-level", "4.1",
        "-c:a", video.audio_codec,
        "-b:a", audio_bitrate,
        "-ar", str(video.audio_sample_rate),
        "-ac", "2",
    ]
    if video.threads:
        options.extend(["-threads", str(video.threads)])
    if video.faststart:
        options.extend(["-movflags", "+faststart"])
    # Colour tags so YouTube does not guess.
    options.extend([
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
    ])
    return options


def metadata_options(config: Config, surah_name: str) -> list[str]:
    reciter = config.reciter
    comment_parts = []
    if reciter.is_configured:
        comment_parts.append(f"Recitation: {reciter.name}")
    if reciter.source and reciter.source != "SOURCE_NAME":
        comment_parts.append(f"Source: {reciter.source}")
    if reciter.license and reciter.license != "LICENSE_INFORMATION":
        comment_parts.append(f"License: {reciter.license}")
    if config.translation_credit.urdu_translator:
        comment_parts.append(f"Urdu translation: {config.translation_credit.urdu_translator}")

    options = [
        "-metadata", f"title={surah_name}",
        "-metadata", "genre=Quran",
    ]
    if comment_parts:
        options.extend(["-metadata", "comment=" + " | ".join(comment_parts)])
    if reciter.is_configured:
        options.extend(["-metadata", f"artist={reciter.name}"])
    return options


# ---------------------------------------------------------------------------
# Execution with a progress bar
# ---------------------------------------------------------------------------

def _parse_progress_line(line: str) -> Optional[float]:
    if line.startswith("out_time_ms="):
        raw = line.split("=", 1)[1].strip()
        if raw.isdigit():
            return int(raw) / 1_000_000.0
    elif line.startswith("out_time_us="):
        raw = line.split("=", 1)[1].strip()
        if raw.isdigit():
            return int(raw) / 1_000_000.0
    return None


def run_job(
    job: FFmpegJob,
    description: str,
    filter_script: Optional[Path] = None,
    show_progress: bool = True,
) -> float:
    """Run *job*, streaming FFmpeg's progress into a Rich progress bar."""
    tools = ff.find_ffmpeg()
    args = job.build_args(filter_script=filter_script)
    command = [
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-y",
        "-loglevel", "error",
        "-progress", "pipe:1", "-nostats",
        *args,
    ]

    log = get_logger()
    log.debug("%s: %s", description, " ".join(command))
    if filter_script is not None:
        log.debug("Filter graph written to %s", filter_script)

    started = time.monotonic()
    total = max(0.1, job.duration)

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise QVGError(f"FFmpeg could not be started: {exc}")

    # stderr is drained on its own thread. Reading the two pipes one after the
    # other deadlocks as soon as FFmpeg emits enough warnings to fill the
    # stderr buffer while this side is still blocked on stdout.
    stderr_lines: list[str] = []

    def drain_stderr() -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()

    if show_progress:
        columns = [
            SpinnerColumn(style="accent"),
            TextColumn("[white]{task.description}"),
            BarColumn(complete_style="gold1", finished_style="green"),
            TextColumn("{task.percentage:>5.1f}%"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ]
        with Progress(*columns, console=console, transient=False) as progress:
            task = progress.add_task(description, total=total)
            assert process.stdout is not None
            for line in process.stdout:
                seconds = _parse_progress_line(line.strip())
                if seconds is not None:
                    progress.update(task, completed=min(seconds, total))
            progress.update(task, completed=total)
    else:
        assert process.stdout is not None
        for _ in process.stdout:
            pass

    process.wait()
    stderr_thread.join(timeout=10)
    stderr = "".join(stderr_lines)

    if process.returncode != 0:
        # Never leave a half-written file where the finished one belongs.
        if job.final_output is not None and job.output is not None:
            job.output.unlink(missing_ok=True)
        raise QVGError(
            f"{description} failed (FFmpeg exit code {process.returncode}).",
            hint=_explain_ffmpeg_error(stderr),
        )
    if stderr.strip():
        log.debug("FFmpeg messages:\n%s", stderr.strip())

    if job.final_output is not None and job.output is not None:
        try:
            job.output.replace(job.final_output)
        except OSError as exc:
            raise QVGError(
                f"The render finished but {job.final_output.name} could not be "
                f"written: {exc}",
                hint="If that file is open in a video player, close it and run the "
                "command again. The finished encode is still there as "
                f"{job.output.name}.",
            )

    return time.monotonic() - started


def _explain_ffmpeg_error(stderr: str) -> str:
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    tail = "\n".join(lines[-14:])
    joined = " ".join(lines).lower()

    hints = []
    if "no space left" in joined:
        hints.append("The drive ran out of space while writing. Free some space and retry.")
    elif "permission denied" in joined:
        hints.append(
            "A file could not be written. Close the video if it is open in a player, "
            "then run the command again."
        )
    elif "unknown encoder" in joined:
        hints.append(
            "This FFmpeg build lacks the requested encoder. Set video.video_codec to "
            "'libx264' in config.yaml, or install a full FFmpeg build."
        )
    elif "invalid data found" in joined or "moov atom not found" in joined:
        hints.append(
            "One of the input files is damaged. Run `python run.py validate` to find "
            "out which one."
        )
    elif "no such filter" in joined or "error initializing filter" in joined:
        hints.append(
            "This FFmpeg build is missing a filter used by the render. Install a full "
            "build (see the README), or run: pip install imageio-ffmpeg"
        )

    return ("\n".join(hints) + "\n\n" + tail).strip() if hints else tail


# ---------------------------------------------------------------------------
# Output verification
# ---------------------------------------------------------------------------

@dataclass
class OutputReport:
    path: Path
    size_bytes: int
    duration: float
    has_video: bool
    has_audio: bool
    resolution: str = ""

    def rows(self) -> list[tuple[str, str]]:
        minutes, seconds = divmod(self.duration, 60)
        return [
            ("File", str(self.path)),
            ("Size", human_size(self.size_bytes)),
            ("Duration", f"{int(minutes)}m {seconds:04.1f}s"),
            ("Resolution", self.resolution or "unknown"),
            ("Streams", ("video " if self.has_video else "") + ("audio" if self.has_audio else "")),
        ]


def measure_true_peak(path: Path) -> Optional[float]:
    """True peak of *path* in dBFS, or None if the track is digital silence.

    Worth a second decode at the end of a long render: a filter-graph mistake
    can produce a perfectly valid file whose audio stream is entirely silent,
    and that is not something anyone should discover after uploading.
    """
    tools = ff.find_ffmpeg()
    command = [
        str(tools.ffmpeg), "-hide_banner", "-nostdin",
        "-i", str(path), "-map", "0:a:0", "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None

    peak: Optional[float] = None
    for line in (proc.stderr or "").splitlines():
        if "Peak:" in line:
            raw = line.split("Peak:")[1].replace("dBFS", "").strip()
            if raw in ("-inf", "-inf.0"):
                return None
            try:
                peak = float(raw)
            except ValueError:
                return None
    return peak


def verify_output(path: Path, expected_duration: float, tolerance: float = 1.5) -> OutputReport:
    """Confirm the rendered file actually plays and is roughly the right length."""
    if not path.is_file():
        raise QVGError(f"The render finished but {path} does not exist.")
    size = path.stat().st_size
    if size < 20_000:
        raise QVGError(
            f"{path.name} is only {human_size(size)} - the render produced an empty file.",
            hint="Check logs/ for the FFmpeg output from this run.",
        )

    report_text = ff.identify(path)
    has_video = "Video:" in report_text
    has_audio = "Audio:" in report_text

    duration = 0.0
    for line in report_text.splitlines():
        if "Duration:" in line:
            chunk = line.split("Duration:")[1].split(",")[0].strip()
            try:
                hours, minutes, seconds = chunk.split(":")
                duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            except ValueError:
                duration = 0.0
            break

    resolution = ""
    for line in report_text.splitlines():
        if "Video:" in line:
            for token in line.split(","):
                token = token.strip()
                if "x" in token and token.split("x")[0].strip().isdigit():
                    resolution = token.split(" ")[0]
                    break
            break

    if not has_video:
        raise QVGError(f"{path.name} contains no video stream.")
    if not has_audio:
        raise QVGError(
            f"{path.name} contains no audio stream.",
            hint="The recitation was not muxed in. Check logs/ for FFmpeg warnings.",
        )

    peak = measure_true_peak(path)
    if peak is None:
        raise QVGError(
            f"{path.name} has an audio track but it is completely silent.",
            hint="The audio filter chain produced no signal. This is a bug, not a "
            "configuration problem - the full FFmpeg command is in logs/.",
        )
    if expected_duration and abs(duration - expected_duration) > tolerance:
        get_logger().warning(
            "Rendered duration %.2fs differs from the planned %.2fs",
            duration, expected_duration,
        )

    return OutputReport(
        path=path,
        size_bytes=size,
        duration=duration,
        has_video=has_video,
        has_audio=has_audio,
        resolution=resolution,
    )


def concat_demuxer_file(paths: Sequence[Path], destination: Path) -> Path:
    """Write a concat-demuxer list (kept for tooling and tests)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = ["ffconcat version 1.0"]
    for path in paths:
        escaped = str(path.resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination
