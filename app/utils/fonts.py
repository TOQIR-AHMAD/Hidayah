"""Font discovery and the OFL font downloader used by `python run.py fonts`."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

from app.utils.logging import QVGError, get_logger

FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")

# SIL Open Font License fonts. The OFL explicitly permits redistribution, so
# these can be downloaded on request. Nothing here is downloaded automatically.
GOOGLE_FONTS_RAW = "https://raw.githubusercontent.com/google/fonts/main/"

BUNDLED_FONTS: tuple[tuple[str, str, str], ...] = (
    # (destination file name, repo path, human description)
    (
        "AmiriQuran-Regular.ttf",
        "ofl/amiriquran/AmiriQuran-Regular.ttf",
        "Amiri Quran - Arabic, tuned for fully vowelled Quranic text",
    ),
    (
        "Amiri-Regular.ttf",
        "ofl/amiri/Amiri-Regular.ttf",
        "Amiri Regular - Arabic naskh",
    ),
    (
        "Amiri-Bold.ttf",
        "ofl/amiri/Amiri-Bold.ttf",
        "Amiri Bold - Arabic headings",
    ),
    (
        "NotoNastaliqUrdu-Regular.ttf",
        "ofl/notonastaliqurdu/NotoNastaliqUrdu[wght].ttf",
        "Noto Nastaliq Urdu - Urdu nastaliq",
    ),
    (
        "CormorantGaramond-Regular.ttf",
        "ofl/cormorantgaramond/CormorantGaramond[wght].ttf",
        "Cormorant Garamond - Latin display serif",
    ),
    # Apple's SF Pro is the real iOS typeface and is licensed for use on Apple
    # platforms only - it is not redistributable, so it is not fetched here.
    # Inter was drawn as an open counterpart to it and is what the iOS theme
    # sets its Latin text in.
    (
        "Inter-Regular.ttf",
        "ofl/inter/Inter[opsz,wght].ttf",
        "Inter - Latin UI sans (the iOS theme's SF Pro stand-in)",
    ),
)

LICENSE_FILES: tuple[tuple[str, str], ...] = (
    ("OFL-Amiri.txt", "ofl/amiri/OFL.txt"),
    ("OFL-AmiriQuran.txt", "ofl/amiriquran/OFL.txt"),
    ("OFL-NotoNastaliqUrdu.txt", "ofl/notonastaliqurdu/OFL.txt"),
    ("OFL-CormorantGaramond.txt", "ofl/cormorantgaramond/OFL.txt"),
    ("OFL-Inter.txt", "ofl/inter/OFL.txt"),
)


def system_font_dirs() -> list[Path]:
    dirs: list[Path] = []
    if sys.platform.startswith("win"):
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(Path(windir) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        dirs += [
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
            Path.home() / "Library" / "Fonts",
        ]
    else:
        dirs += [
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path.home() / ".fonts",
            Path.home() / ".local" / "share" / "fonts",
        ]
    return [d for d in dirs if d.is_dir()]


@dataclass(frozen=True)
class FontChoice:
    """A resolved font file plus the request that produced it."""

    role: str
    path: Path
    requested: str
    is_fallback: bool

    @property
    def name(self) -> str:
        return self.path.name


class FontResolver:
    """Finds font files in assets/fonts/ first, then the system font folders."""

    def __init__(self, fonts_dir: Path, use_system_fonts: bool = True) -> None:
        self.fonts_dir = fonts_dir
        self.use_system_fonts = use_system_fonts
        self._index: Optional[dict[str, Path]] = None

    def _build_index(self) -> dict[str, Path]:
        index: dict[str, Path] = {}
        search_dirs: list[Path] = []
        if self.fonts_dir.is_dir():
            search_dirs.append(self.fonts_dir)
        if self.use_system_fonts:
            search_dirs.extend(system_font_dirs())

        # Earlier directories win, so project fonts override system fonts.
        for directory in reversed(search_dirs):
            try:
                entries = list(directory.rglob("*"))
            except OSError:
                continue
            for entry in entries:
                if entry.is_file() and entry.suffix.lower() in FONT_EXTENSIONS:
                    index[entry.name.lower()] = entry
        return index

    @property
    def index(self) -> dict[str, Path]:
        if self._index is None:
            self._index = self._build_index()
        return self._index

    def refresh(self) -> None:
        self._index = None

    def lookup(self, filename: str) -> Optional[Path]:
        if not filename:
            return None
        direct = self.fonts_dir / filename
        if direct.is_file():
            return direct
        candidate = Path(filename)
        if candidate.is_file():
            return candidate
        return self.index.get(filename.lower())

    def resolve(
        self, role: str, primary: str, fallbacks: Sequence[str] = ()
    ) -> FontChoice:
        found = self.lookup(primary)
        if found is not None:
            return FontChoice(role=role, path=found, requested=primary, is_fallback=False)

        for name in fallbacks:
            found = self.lookup(name)
            if found is not None:
                get_logger().warning(
                    "Font '%s' for %s was not found - falling back to '%s'.",
                    primary, role, found.name,
                )
                return FontChoice(role=role, path=found, requested=primary, is_fallback=True)

        raise QVGError(
            f"No font available for '{role}'. Tried: {', '.join([primary, *fallbacks])}",
            hint=(
                f"Put a suitable font file in {self.fonts_dir}\\ and set fonts.{role} "
                "in config.yaml to its file name.\n"
                "The quickest fix is to download the bundled SIL OFL fonts:\n"
                "    python run.py fonts"
            ),
        )

    def available_names(self, limit: int = 40) -> list[str]:
        local = sorted(
            p.name for p in self.fonts_dir.glob("*") if p.suffix.lower() in FONT_EXTENSIONS
        ) if self.fonts_dir.is_dir() else []
        return local[:limit]


def download_fonts(fonts_dir: Path, force: bool = False) -> list[str]:
    """Download the SIL OFL fonts listed in BUNDLED_FONTS into *fonts_dir*."""
    fonts_dir.mkdir(parents=True, exist_ok=True)
    log = get_logger()
    messages: list[str] = []

    targets: list[tuple[str, str]] = [(dst, src) for dst, src, _ in BUNDLED_FONTS]
    targets += list(LICENSE_FILES)

    for dest_name, repo_path in targets:
        dest = fonts_dir / dest_name
        if dest.is_file() and not force:
            messages.append(f"kept     {dest_name} (already present)")
            continue
        url = GOOGLE_FONTS_RAW + urllib.parse.quote(repo_path)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "quran-video-generator"})
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            messages.append(f"FAILED   {dest_name}: {exc}")
            log.warning("Could not download %s: %s", dest_name, exc)
            continue
        if len(payload) < 1024:
            messages.append(f"FAILED   {dest_name}: response was suspiciously small")
            continue
        dest.write_bytes(payload)
        messages.append(f"saved    {dest_name} ({len(payload) / 1024:.0f} KB)")
        log.info("Downloaded font %s", dest_name)

    return messages



def describe_bundled() -> list[tuple[str, str]]:
    return [(name, description) for name, _, description in BUNDLED_FONTS]


def any_arabic_capable(resolver: FontResolver) -> Iterable[str]:
    """Names of project fonts that at least exist, for diagnostics."""
    return resolver.available_names()
