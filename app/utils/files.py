"""Filesystem helpers: directory creation, disk-space checks, media discovery."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional

from app.utils.logging import QVGError

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus"}


def project_root() -> Path:
    """Root of the project (the directory that contains run.py)."""
    return Path(__file__).resolve().parents[2]


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def find_first_media(directory: Path) -> Optional[Path]:
    """First usable background file in *directory* (images before videos)."""
    if not directory.is_dir():
        return None
    candidates = sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and (is_image(p) or is_video(p)) and not p.name.startswith(".")
    )
    images = [p for p in candidates if is_image(p)]
    videos = [p for p in candidates if is_video(p)]
    if images:
        return images[0]
    if videos:
        return videos[0]
    return None


def find_audio(directory: Path, stem: str, extensions: Iterable[str]) -> Optional[Path]:
    """Locate ``<stem>.<ext>`` in *directory*, trying each extension in order."""
    for ext in extensions:
        candidate = directory / f"{stem}.{ext.lstrip('.')}"
        if candidate.is_file():
            return candidate
    return None


def available_memory_mb() -> Optional[float]:
    """Physical memory this machine could give a new process, in megabytes.

    None when it cannot be determined, which callers should read as "assume
    nothing" rather than "assume plenty".
    """
    if sys.platform == "win32":
        import ctypes

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return status.ullAvailPhys / (1024 * 1024)

    try:
        return (os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")) / (1024 * 1024)
    except (AttributeError, ValueError, OSError):
        return None


def free_space_mb(path: Path) -> float:
    target = path
    while not target.exists() and target.parent != target:
        target = target.parent
    return shutil.disk_usage(target).free / (1024 * 1024)


def require_free_space(path: Path, needed_mb: float) -> None:
    available = free_space_mb(path)
    if available < needed_mb:
        raise QVGError(
            f"Not enough free disk space on {path.drive or path.anchor or path}: "
            f"{available:.0f} MB available, about {needed_mb:.0f} MB needed.",
            hint="Free up some space (or point paths.temp_dir at another drive in "
            "config.yaml) and run the command again.",
        )


def clean_dir(path: Path, keep: Iterable[str] = ()) -> None:
    """Remove the contents of *path*, keeping the named entries.

    Refuses to run on the project root or any ancestor of it. `paths.temp_dir`
    is user-editable, and setting it to "" or "." would otherwise turn
    `python run.py clean` into "delete the project".
    """
    if not path.is_dir():
        return

    resolved = path.resolve()
    root = project_root().resolve()
    if resolved == root or resolved in root.parents:
        raise QVGError(
            f"Refusing to empty {resolved} - that is the project directory itself.",
            hint="paths.temp_dir in config.yaml must point at a subdirectory, "
            "such as \"temp\".",
        )

    keep_set = set(keep)
    for entry in path.iterdir():
        if entry.name in keep_set or entry.name.startswith("."):
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except OSError:
                pass


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


def relative_to_root(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root()))
    except ValueError:
        return str(path)
