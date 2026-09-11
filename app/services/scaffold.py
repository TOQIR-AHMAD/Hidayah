"""Create the config file for a new surah.

Every surah this project renders is one config file. They differ in a handful of
values - which surah, where its text and recitation live, what the thumbnail
says - and agree on everything else: the typography, the colour, the motion,
the encoder settings. That shared part is long, carefully tuned and heavily
commented, and it is the same file every time.

So a new surah is made by copying the existing config and changing only the
values that belong to the surah. Copying it as TEXT rather than re-emitting it
from parsed YAML is deliberate: the comments are most of what that file is, and
a YAML round-trip would throw all of them away.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.models.surah_index import ayah_count, global_offset, validate_number
from app.utils.logging import QVGError, get_logger

# The CDN behind `fetch-audio` indexes by the mushaf-wide ayah number.
RECITATION_URL = (
    "https://cdn.islamic.network/quran/audio/128/ar.alafasy/{global_ayah}.mp3"
)


def _set_value(text: str, key: str, value: str, indent: str = "  ") -> str:
    """Replace the value of one `key:` line, keeping the rest of the file.

    Only the first occurrence is touched, and only at the given indent, so a key
    named in a comment or nested deeper is left alone.
    """
    pattern = re.compile(
        rf"^{re.escape(indent)}{re.escape(key)}:[ \t]*.*$", re.MULTILINE
    )
    if not pattern.search(text):
        raise QVGError(
            f"The template config has no '{key}:' line to fill in.",
            hint="config.yaml is the template. If it has been restructured, "
            "update app/services/scaffold.py to match.",
        )
    return pattern.sub(f"{indent}{key}: {value}", text, count=1)


def _slug(surah_number: int, english_name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", "-", english_name).strip("-")
    return f"{surah_number:03d}_{clean}"


def _stem(english_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", english_name.lower()).strip("_")


def config_path(root: Path, english_name: str) -> Path:
    return root / f"config.{re.sub(r'[^a-z0-9]+', '-', english_name.lower()).strip('-')}.yaml"


def create_config(
    surah_number: int,
    english_name: str,
    arabic_name: str,
    urdu_name: str,
    template: Path,
    destination: Path,
    force: bool = False,
) -> Path:
    """Write the config for *surah_number*, derived from *template*."""
    validate_number(surah_number)

    if destination.is_file() and not force:
        raise QVGError(
            f"{destination.name} already exists.",
            hint="Pass --force to overwrite it, or edit it by hand.",
        )
    if not template.is_file():
        raise QVGError(f"The template config {template} is missing.")

    text = template.read_text(encoding="utf-8")
    slug = _slug(surah_number, english_name)
    stem = _stem(english_name)
    offset = global_offset(surah_number)
    count = ayah_count(surah_number)

    text = _set_value(text, "name", f'"Surah {english_name}"')
    text = _set_value(text, "slug", f'"{slug}"')
    text = _set_value(text, "surah", str(surah_number))
    text = _set_value(text, "data_file", f'"data/{stem}.json"')
    # One directory per surah: the files inside are numbered by their place in
    # THIS surah, so two surahs cannot share a directory.
    text = _set_value(text, "recitation_dir", f'"assets/audio/recitation/{surah_number:03d}"')
    text = _set_value(text, "urdu_dir", f'"assets/audio/urdu/{surah_number:03d}"')
    text = _set_value(text, "full_surah_file", f'"{stem}.mp3"')
    text = _set_value(text, "file", f'"data/word_timings_{surah_number:03d}.json"')
    text = _set_value(text, "recitation_url_template", f'"{RECITATION_URL}"')
    text = _set_value(text, "urdu_url_template", '""')
    text = _set_value(text, "arabic_text", f'"{arabic_name}"')
    text = _set_value(text, "urdu_text", f'"{urdu_name}"')
    text = _set_value(text, "latin_text", f'"Surah {english_name}"')

    header = (
        f"# ============================================================================\n"
        f"#  Quran Video Generator - Surah {english_name} ({surah_number})\n"
        f"#\n"
        f"#  {count} ayahs. In the mushaf they are numbered "
        f"{offset + 1}-{offset + count}, which is what the recitation CDN indexes\n"
        f"#  by; on screen and in assets/audio they are 1-{count}.\n"
        f"#\n"
        f"#  Generated from config.yaml by `python run.py new-surah {surah_number}`.\n"
        f"#  Everything not listed above is shared with that file on purpose.\n"
    )
    # Replace the template's own banner, which names the surah it was written for.
    lines = text.splitlines(keepends=True)
    end = 0
    while end < len(lines) and lines[end].startswith("#"):
        end += 1
    text = header + "".join(lines[end:])

    destination.write_text(text, encoding="utf-8")
    get_logger().info("Wrote %s", destination)
    return destination
