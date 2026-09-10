"""FFmpeg / FFprobe discovery and process execution.

Resolution order:
  1. QVG_FFMPEG / QVG_FFPROBE environment variables
  2. ffmpeg / ffprobe on PATH
  3. the binary bundled with the ``imageio-ffmpeg`` package
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

from app.utils.logging import QVGError, get_logger

_INSTALL_HINT = (
    "Install FFmpeg and make sure it is reachable, then try again.\n"
    "  Windows (easiest):  pip install imageio-ffmpeg\n"
    "  Windows (manual) :  download a build from https://www.gyan.dev/ffmpeg/builds/,\n"
    "                      unzip it, and add its bin\\ folder to your PATH\n"
    "  Or set QVG_FFMPEG=C:\\path\\to\\ffmpeg.exe in your .env file"
)


@dataclass(frozen=True)
class FFmpegTools:
    ffmpeg: Path
    ffprobe: Optional[Path]
    version: str

    @property
    def has_ffprobe(self) -> bool:
        return self.ffprobe is not None


def _from_env(var: str) -> Optional[Path]:
    value = os.environ.get(var, "").strip().strip('"')
    if not value:
        return None
    path = Path(value)
    return path if path.is_file() else None


def _bundled_ffmpeg() -> Optional[Path]:
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        path = Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:  # pragma: no cover - depends on the local install
        return None
    return path if path.is_file() else None


def _sibling_ffprobe(ffmpeg: Path) -> Optional[Path]:
    """imageio-ffmpeg ships ffmpeg only; look for an ffprobe next to it."""
    suffix = ffmpeg.suffix
    for name in ("ffprobe", "ffprobe" + suffix):
        candidate = ffmpeg.with_name(name)
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def find_ffmpeg() -> FFmpegTools:
    ffmpeg = _from_env("QVG_FFMPEG")
    if ffmpeg is None:
        which = shutil.which("ffmpeg")
        ffmpeg = Path(which) if which else None
    if ffmpeg is None:
        ffmpeg = _bundled_ffmpeg()
    if ffmpeg is None:
        raise QVGError("FFmpeg was not found on this system.", hint=_INSTALL_HINT)

    ffprobe = _from_env("QVG_FFPROBE")
    if ffprobe is None:
        which = shutil.which("ffprobe")
        ffprobe = Path(which) if which else None
    if ffprobe is None:
        ffprobe = _sibling_ffprobe(ffmpeg)

    try:
        out = subprocess.run(
            [str(ffmpeg), "-version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError as exc:
        raise QVGError(f"FFmpeg at {ffmpeg} could not be started: {exc}", hint=_INSTALL_HINT)

    first_line = (out.stdout or out.stderr or "").splitlines()[:1]
    match = re.search(r"ffmpeg version (\S+)", first_line[0]) if first_line else None
    version = match.group(1) if match else "unknown"
    return FFmpegTools(ffmpeg=ffmpeg, ffprobe=ffprobe, version=version)


def run_ffmpeg(
    args: Sequence[str],
    *,
    description: str = "FFmpeg",
    timeout: Optional[float] = None,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run FFmpeg with *args* (without the executable) and raise on failure."""
    tools = find_ffmpeg()
    command = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-y", *map(str, args)]
    log = get_logger()
    log.debug("%s command: %s", description, " ".join(command))

    try:
        proc = subprocess.run(
            command,
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise QVGError(
            f"{description} timed out after {timeout:.0f}s.",
            hint="Try a faster preset in config.yaml (video.preset), or render the "
            "preview first with `python run.py preview`.",
        )
    except OSError as exc:
        raise QVGError(f"{description} could not be started: {exc}", hint=_INSTALL_HINT)

    if proc.returncode != 0:
        raise QVGError(
            f"{description} failed (exit code {proc.returncode}).",
            hint=_tail(proc.stderr) or "Re-run with QVG_LOG_LEVEL=DEBUG for the full command.",
        )
    return proc


def probe_json(args: Sequence[str], *, description: str = "FFprobe") -> str:
    """Run ffprobe and return raw stdout. Falls back to ffmpeg when needed."""
    tools = find_ffmpeg()
    if tools.ffprobe is None:
        raise QVGError(
            "ffprobe was not found (ffmpeg was found, but ffprobe is missing).",
            hint="Install a full FFmpeg build that includes ffprobe.exe, or set "
            "QVG_FFPROBE in your .env file. See the README for a walkthrough.",
        )
    command = [str(tools.ffprobe), "-hide_banner", *map(str, args)]
    get_logger().debug("%s command: %s", description, " ".join(command))
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise QVGError(
            f"{description} failed (exit code {proc.returncode}).",
            hint=_tail(proc.stderr),
        )
    return proc.stdout


def identify(path: Path) -> str:
    """Return FFmpeg's own report for *path*.

    ``ffmpeg -i <file>`` always exits non-zero ("At least one output file must
    be specified") but prints a full stream description on stderr. This is the
    fallback used when ffprobe is unavailable - notably with the ffmpeg binary
    bundled by imageio-ffmpeg, which ships ffmpeg only.
    """
    tools = find_ffmpeg()
    command = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-i", str(path)]
    get_logger().debug("identify command: %s", " ".join(command))
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    except OSError as exc:
        raise QVGError(f"FFmpeg could not be started: {exc}", hint=_INSTALL_HINT)
    return (proc.stderr or "") + (proc.stdout or "")


def decode_check(path: Path) -> tuple[bool, str]:
    """Fully decode *path* to null output to prove the file is not corrupt."""
    tools = find_ffmpeg()
    command = [
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error",
        "-i", str(path), "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, _tail(proc.stderr, 6) or f"exit code {proc.returncode}"
    return True, ""


def _tail(text: Optional[str], lines: int = 14) -> str:
    if not text:
        return ""
    kept = [ln for ln in text.strip().splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])
