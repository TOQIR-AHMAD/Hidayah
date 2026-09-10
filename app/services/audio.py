"""Audio analysis and the per-ayah audio plan.

The recitation is treated as a recording to be preserved, not material to be
reshaped. The only processing applied anywhere in this project is:

  * a single constant gain per file, derived from an EBU R128 loudness
    measurement, so the Arabic and the Urdu sit at a comparable level
  * a few-millisecond fade at the very start and end of each clip to stop
    digital clicks
  * container/format conversion during muxing

No compression, no EQ, no pitch or time changes, no re-articulation. The timing
of the video follows the audio, never the other way round.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.config import Config
from app.models.surah import Surah
from app.utils import ffmpeg as ff
from app.utils.files import find_audio
from app.utils.logging import QVGError, get_logger

_TIME_RE = re.compile(r"time=(\d+):(\d{2}):(\d{2})\.(\d+)")
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2})\.(\d+)")
_LUFS_RE = re.compile(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.MULTILINE)
_PEAK_RE = re.compile(r"^\s*Peak:\s*(-?\d+(?:\.\d+)?|-inf)\s*dBFS", re.MULTILINE)
_AUDIO_STREAM_RE = re.compile(r"Stream #\d+:\d+.*: Audio: ([^,]+), (\d+) Hz, ([^,]+)")

CACHE_NAME = "audio_analysis.json"


@dataclass(frozen=True)
class AudioAnalysis:
    """Everything learned from one decode pass over an audio file."""

    path: Path
    duration: float
    ok: bool
    error: str = ""
    integrated_lufs: Optional[float] = None
    true_peak_db: Optional[float] = None
    codec: str = ""
    sample_rate: int = 0
    channels: str = ""

    @property
    def is_usable(self) -> bool:
        return self.ok and self.duration > 0.0


@dataclass
class AudioClip:
    """One piece of audio placed on the timeline."""

    path: Path
    duration: float          # seconds of source material
    gain_db: float = 0.0
    trim_start: float = 0.0
    label: str = ""

    @property
    def trim_end(self) -> float:
        return self.trim_start + self.duration

    def played_duration(self, speed: float = 1.0) -> float:
        """How long the clip occupies on the timeline at *speed*.

        The rate is passed in rather than stored: keeping a copy on the clip
        let it drift out of step with config.video.playback_speed, which is
        what the timeline and the filter graph both read.
        """
        return self.duration / max(0.01, speed)


@dataclass
class AudioPlan:
    mode: str
    recitation: dict[int, AudioClip] = field(default_factory=dict)
    urdu: dict[int, AudioClip] = field(default_factory=dict)

    @property
    def total_audio_seconds(self) -> float:
        return sum(c.duration for c in self.recitation.values()) + sum(
            c.duration for c in self.urdu.values()
        )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _parse_timestamp(match: re.Match) -> float:
    hours, minutes, seconds, fraction = match.groups()
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + float(f"0.{fraction}")
    )


def _cache_path(config: Config) -> Path:
    return config.temp_dir / CACHE_NAME


def _load_cache(config: Config) -> dict:
    path = _cache_path(config)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(config: Config, cache: dict) -> None:
    path = _cache_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    except OSError:
        pass


def _cache_key(path: Path) -> str:
    stat = path.stat()
    return f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"


def analyse(path: Path, config: Optional[Config] = None, use_cache: bool = True) -> AudioAnalysis:
    """Decode *path* once and report duration, loudness, peak and validity.

    A full decode is the only way to be certain a file is not truncated or
    corrupt, and it also yields the exact duration FFmpeg will produce when
    muxing - which is what the timeline needs.
    """
    if not path.is_file():
        return AudioAnalysis(path=path, duration=0.0, ok=False, error="file not found")

    cache: dict = {}
    key = ""
    if use_cache and config is not None:
        cache = _load_cache(config)
        try:
            key = _cache_key(path)
        except OSError:
            key = ""
        if key and key in cache:
            cached = cache[key]
            return AudioAnalysis(
                path=path,
                duration=cached["duration"],
                ok=cached["ok"],
                error=cached.get("error", ""),
                integrated_lufs=cached.get("integrated_lufs"),
                true_peak_db=cached.get("true_peak_db"),
                codec=cached.get("codec", ""),
                sample_rate=cached.get("sample_rate", 0),
                channels=cached.get("channels", ""),
            )

    tools = ff.find_ffmpeg()
    import subprocess

    command = [
        str(tools.ffmpeg), "-hide_banner", "-nostdin",
        "-i", str(path),
        "-map", "0:a:0",
        "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    get_logger().debug("analyse: %s", " ".join(command))
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return AudioAnalysis(path=path, duration=0.0, ok=False, error=str(exc))

    stderr = proc.stderr or ""

    if proc.returncode != 0:
        reason = _first_error(stderr)
        # Test the raw stderr, not the extracted reason: FFmpeg's wording here
        # is "Stream map '' matches no streams.", which carries none of the
        # tokens _first_error looks for, so it can never come back from there.
        if (
            "matches no streams" in stderr
            or "does not contain any stream" in stderr
        ):
            reason = "the file contains no audio stream"
        return AudioAnalysis(path=path, duration=0.0, ok=False, error=reason)

    times = _TIME_RE.findall(stderr)
    duration = 0.0
    if times:
        h, m, s, frac = times[-1]
        duration = int(h) * 3600 + int(m) * 60 + int(s) + float(f"0.{frac}")
    if duration <= 0:
        header = _DURATION_RE.search(stderr)
        if header:
            duration = _parse_timestamp(header)

    lufs_match = _LUFS_RE.search(stderr)
    peak_match = _PEAK_RE.search(stderr)
    stream_match = _AUDIO_STREAM_RE.search(stderr)

    peak_value: Optional[float] = None
    if peak_match:
        raw = peak_match.group(1)
        peak_value = None if raw == "-inf" else float(raw)

    lufs_value: Optional[float] = None
    if lufs_match:
        candidate = float(lufs_match.group(1))
        # ebur128 reports -70.0 (or lower) for silence; treat that as unknown.
        lufs_value = candidate if candidate > -70.0 else None

    analysis = AudioAnalysis(
        path=path,
        duration=duration,
        ok=duration > 0.0,
        error="" if duration > 0.0 else "decoded to a zero-length stream",
        integrated_lufs=lufs_value,
        true_peak_db=peak_value,
        codec=stream_match.group(1).strip() if stream_match else "",
        sample_rate=int(stream_match.group(2)) if stream_match else 0,
        channels=stream_match.group(3).strip() if stream_match else "",
    )

    if use_cache and config is not None and key:
        cache[key] = {
            "duration": analysis.duration,
            "ok": analysis.ok,
            "error": analysis.error,
            "integrated_lufs": analysis.integrated_lufs,
            "true_peak_db": analysis.true_peak_db,
            "codec": analysis.codec,
            "sample_rate": analysis.sample_rate,
            "channels": analysis.channels,
        }
        _save_cache(config, cache)

    return analysis


def _first_error(stderr: str) -> str:
    for line in reversed([ln.strip() for ln in stderr.splitlines() if ln.strip()]):
        lowered = line.lower()
        if any(token in lowered for token in
               ("invalid", "error", "no such file", "not found", "failed", "moov atom")):
            return line
    return "FFmpeg could not decode the file"


def compute_gain_db(analysis: AudioAnalysis, config: Config) -> float:
    """Constant gain that moves *analysis* to the target loudness, safely clamped."""
    settings = config.audio
    if not settings.normalize or analysis.integrated_lufs is None:
        return 0.0

    gain = settings.target_lufs - analysis.integrated_lufs
    gain = max(-settings.max_gain_db, min(settings.max_gain_db, gain))

    # Never push the recording into clipping.
    if analysis.true_peak_db is not None:
        headroom = settings.true_peak_ceiling_db - analysis.true_peak_db
        gain = min(gain, headroom)

    return round(gain, 2)


# ---------------------------------------------------------------------------
# Locating the files
# ---------------------------------------------------------------------------

def recitation_file(config: Config, ayah_number: int) -> Optional[Path]:
    return find_audio(config.recitation_dir, f"{ayah_number:03d}", config.audio.extensions)


def urdu_file(config: Config, ayah_number: int) -> Optional[Path]:
    return find_audio(config.urdu_dir, f"{ayah_number:03d}", config.audio.extensions)


def full_surah_file(config: Config) -> Optional[Path]:
    configured = config.recitation_dir / config.audio.full_surah_file
    if configured.is_file():
        return configured
    return find_audio(config.recitation_dir, Path(config.audio.full_surah_file).stem,
                      config.audio.extensions)


def resolve_recitation_mode(config: Config, surah: Surah) -> str:
    """Decide between per-ayah files and one complete recording."""
    requested = config.audio.recitation_mode
    per_ayah_present = [recitation_file(config, a.number) is not None for a in surah.ayahs]
    full_present = full_surah_file(config) is not None
    has_timings = bool(config.audio.full_surah_timings)

    if requested == "per_ayah":
        return "per_ayah"
    if requested == "full_surah":
        return "full_surah"

    if all(per_ayah_present):
        return "per_ayah"
    if full_present and has_timings:
        return "full_surah"
    return "per_ayah"  # report the per-ayah files as missing, which is clearer


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class AudioIssue:
    kind: str          # "missing" | "corrupt" | "config"
    path: Path
    detail: str = ""


def _check_timings(
    config: Config, surah: Surah, path: Path, recording_seconds: float
) -> list[AudioIssue]:
    """Validate hand-measured full-surah timings against the recording.

    These numbers are typed in by hand, so transposed and mistyped values are
    the expected failure mode - and every one of them is silent at render time:
    an overlap replays the tail of the previous ayah under the next ayah's text,
    a gap drops recitation entirely, and both produce a perfectly valid video.
    """
    issues: list[AudioIssue] = []
    timings = {t.ayah: t for t in config.audio.full_surah_timings}

    if not timings:
        return [
            AudioIssue(
                "config", path,
                "audio.recitation_mode is \"full_surah\" but audio.full_surah_timings "
                "is empty - add one {ayah, start, end} entry per ayah",
            )
        ]

    missing = [a.number for a in surah.ayahs if a.number not in timings]
    if missing:
        issues.append(
            AudioIssue(
                "config", path,
                "audio.full_surah_timings has no entry for ayah "
                + ", ".join(str(n) for n in missing),
            )
        )

    for ayah in surah.ayahs:
        timing = timings.get(ayah.number)
        if timing is None:
            continue
        if timing.end > recording_seconds + 0.25:
            issues.append(
                AudioIssue(
                    "config", path,
                    f"ayah {ayah.number} ends at {timing.end:.2f}s but the recording "
                    f"is only {recording_seconds:.2f}s long",
                )
            )

    ordered = [timings[a.number] for a in surah.ayahs if a.number in timings]
    for previous, current in zip(ordered, ordered[1:]):
        if current.start < previous.end - 0.01:
            issues.append(
                AudioIssue(
                    "config", path,
                    f"ayah {current.ayah} starts at {current.start:.2f}s but ayah "
                    f"{previous.ayah} does not end until {previous.end:.2f}s - the "
                    "overlap would replay part of the previous ayah",
                )
            )
        elif current.start > previous.end + 1.5:
            issues.append(
                AudioIssue(
                    "config", path,
                    f"ayah {current.ayah} starts at {current.start:.2f}s, "
                    f"{current.start - previous.end:.2f}s after ayah {previous.ayah} "
                    "ends - that much recitation would be skipped",
                )
            )

    return issues


def check_audio(config: Config, surah: Surah, mode: Optional[str] = None) -> tuple[str, list[AudioIssue]]:
    """Verify that every file the render needs exists and decodes."""
    mode = mode or resolve_recitation_mode(config, surah)
    issues: list[AudioIssue] = []

    if mode == "full_surah":
        path = full_surah_file(config)
        if path is None:
            issues.append(
                AudioIssue(
                    "missing",
                    config.recitation_dir / config.audio.full_surah_file,
                    "complete-surah recitation",
                )
            )
        else:
            analysis = analyse(path, config)
            if not analysis.ok:
                issues.append(AudioIssue("corrupt", path, analysis.error))
            else:
                issues.extend(_check_timings(config, surah, path, analysis.duration))
    else:
        for ayah in surah.ayahs:
            path = recitation_file(config, ayah.number)
            if path is None:
                issues.append(
                    AudioIssue(
                        "missing",
                        config.recitation_dir / f"{ayah.number:03d}.mp3",
                        f"Arabic recitation for ayah {ayah.number}",
                    )
                )
                continue
            analysis = analyse(path, config)
            if not analysis.ok:
                issues.append(AudioIssue("corrupt", path, analysis.error))

    if config.audio.urdu_narration:
        for ayah in surah.ayahs:
            path = urdu_file(config, ayah.number)
            if path is None:
                issues.append(
                    AudioIssue(
                        "missing",
                        config.urdu_dir / f"{ayah.number:03d}.mp3",
                        f"Urdu narration for ayah {ayah.number}",
                    )
                )
                continue
            analysis = analyse(path, config)
            if not analysis.ok:
                issues.append(AudioIssue("corrupt", path, analysis.error))

    ambience = config.audio.ambience
    if ambience.enabled:
        path = config.path(ambience.file)
        if not path.is_file():
            issues.append(
                AudioIssue("missing", path, "ambience bed (audio.ambience.enabled is true)")
            )
        else:
            # The bed is an input to the same single FFmpeg pass as everything
            # else, so a broken one has to be caught here. Checking only that it
            # exists let it kill the render after every card had been drawn.
            analysis = analyse(path, config)
            if not analysis.ok:
                issues.append(AudioIssue("corrupt", path, analysis.error))

    return mode, issues


def format_issues(issues: list[AudioIssue], root: Path) -> str:
    """Group the problems by kind, so the advice can match what went wrong.

    A settings mistake used to be reported under "Missing audio files:" naming a
    file that exists, followed by advice to add it - which cannot help.
    """
    def shown(issue: AudioIssue) -> str:
        try:
            return str(issue.path.resolve().relative_to(root))
        except ValueError:
            return str(issue.path)

    lines: list[str] = []
    for kind, heading in (
        ("missing", "Missing audio files:"),
        ("corrupt", "Unreadable or damaged audio files:"),
        ("config", "Problems in config.yaml:"),
    ):
        selected = [i for i in issues if i.kind == kind]
        if not selected:
            continue
        if lines:
            lines.append("")
        lines.append(heading)
        for issue in selected:
            if kind == "missing":
                detail = f"   ({issue.detail})" if issue.detail else ""
                lines.append(f"  - {shown(issue)}{detail}")
            elif kind == "config":
                lines.append(f"  - {issue.detail}")
            else:
                lines.append(f"  - {shown(issue)}: {issue.detail}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Building the plan
# ---------------------------------------------------------------------------

def build_plan(config: Config, surah: Surah, mode: Optional[str] = None) -> AudioPlan:
    """Analyse every required file and produce the clips the timeline will use."""
    mode, issues = check_audio(config, surah, mode)
    if issues:
        only_config = all(issue.kind == "config" for issue in issues)
        advice = (
            "Correct the settings above, then run `python run.py validate` again."
            if only_config
            else "Add or replace the files listed above, then run "
            "`python run.py validate` again."
        )
        raise QVGError(
            "The audio needed for this render is not all usable.",
            hint=format_issues(issues, config.root) + "\n\n" + advice,
        )

    log = get_logger()
    plan = AudioPlan(mode=mode)

    if mode == "full_surah":
        path = full_surah_file(config)
        assert path is not None  # guaranteed by check_audio
        analysis = analyse(path, config)
        gain = compute_gain_db(analysis, config)
        timings = {t.ayah: t for t in config.audio.full_surah_timings}
        for ayah in surah.ayahs:
            timing = timings[ayah.number]
            plan.recitation[ayah.number] = AudioClip(
                path=path,
                duration=timing.end - timing.start,
                gain_db=gain,
                trim_start=timing.start,
                label=f"recitation {ayah.number:03d}",
            )
    else:
        for ayah in surah.ayahs:
            path = recitation_file(config, ayah.number)
            assert path is not None
            analysis = analyse(path, config)
            plan.recitation[ayah.number] = AudioClip(
                path=path,
                duration=analysis.duration,
                gain_db=compute_gain_db(analysis, config),
                label=f"recitation {ayah.number:03d}",
            )

    if config.audio.urdu_narration:
        for ayah in surah.ayahs:
            path = urdu_file(config, ayah.number)
            assert path is not None
            analysis = analyse(path, config)
            plan.urdu[ayah.number] = AudioClip(
                path=path,
                duration=analysis.duration,
                gain_db=compute_gain_db(analysis, config),
                label=f"urdu {ayah.number:03d}",
            )
    else:
        log.info("Urdu narration is switched off; the translation is shown silently.")

    for clip in list(plan.recitation.values()) + list(plan.urdu.values()):
        if clip.gain_db:
            log.debug("%s: applying %+.2f dB", clip.label, clip.gain_db)

    return plan


def db_to_linear(db: float) -> float:
    return math.pow(10.0, db / 20.0)
