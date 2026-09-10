"""The Surah model and the loader for data/al_fatihah.json.

This first version deliberately supports Surah Al-Fatihah only.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.models.ayah import Ayah
from app.utils.logging import QVGError

AL_FATIHAH_NUMBER = 1
AL_FATIHAH_AYAH_COUNT = 7


class TextSource(BaseModel):
    """Provenance of the Quranic text and the translation."""

    arabic_edition: str = ""
    arabic_source: str = ""
    urdu_edition: str = ""
    urdu_source: str = ""
    retrieved_at: str = ""
    note: str = ""

    def describe(self) -> list[tuple[str, str]]:
        return [
            ("Arabic edition", self.arabic_edition or "(not recorded)"),
            ("Arabic source", self.arabic_source or "(not recorded)"),
            ("Urdu edition", self.urdu_edition or "(not recorded)"),
            ("Urdu source", self.urdu_source or "(not recorded)"),
            ("Retrieved", self.retrieved_at or "(not recorded)"),
        ]


class Surah(BaseModel):
    surah_number: int
    name_arabic: str
    name_english: str
    name_urdu: str
    ayah_count: int
    revelation_place: str = ""
    ayahs: list[Ayah]
    source: TextSource = Field(default_factory=TextSource)

    @field_validator("surah_number")
    @classmethod
    def _only_al_fatihah(cls, value: int) -> int:
        if value != AL_FATIHAH_NUMBER:
            raise ValueError(
                f"this version supports Surah Al-Fatihah only, but surah_number is {value}"
            )
        return value

    @model_validator(mode="after")
    def _validate_ayahs(self) -> "Surah":
        numbers = [a.number for a in self.ayahs]

        if len(self.ayahs) != AL_FATIHAH_AYAH_COUNT:
            raise ValueError(
                f"Surah Al-Fatihah has {AL_FATIHAH_AYAH_COUNT} ayahs, "
                f"but the file contains {len(self.ayahs)}"
            )
        if self.ayah_count != AL_FATIHAH_AYAH_COUNT:
            raise ValueError(
                f"ayah_count must be {AL_FATIHAH_AYAH_COUNT}, got {self.ayah_count}"
            )
        if numbers != list(range(1, AL_FATIHAH_AYAH_COUNT + 1)):
            raise ValueError(
                "ayah numbers must be exactly 1,2,3,4,5,6,7 in order - got "
                f"{numbers}"
            )
        return self

    # -- convenience ---------------------------------------------------------
    def ayah(self, number: int) -> Ayah:
        for item in self.ayahs:
            if item.number == number:
                return item
        raise KeyError(f"Ayah {number} is not present in {self.name_english}")

    @property
    def padded_number(self) -> str:
        return f"{self.surah_number:03d}"

    def describe(self) -> list[tuple[str, str]]:
        return [
            ("Surah", f"{self.surah_number}. {self.name_english}"),
            ("Arabic name", self.name_arabic),
            ("Urdu name", self.name_urdu),
            ("Ayahs", str(self.ayah_count)),
            ("Revelation", self.revelation_place or "(not recorded)"),
        ]


def load_surah(path: Path) -> Surah:
    """Load and validate the Surah JSON file, with human-readable errors."""
    if not path.is_file():
        raise QVGError(
            f"Quran data file not found: {path}",
            hint="Create it by running:\n    python run.py fetch-text\n"
            "That downloads the verified Uthmani text and an Urdu translation "
            "and writes data/al_fatihah.json.",
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise QVGError(
            f"{path.name} is not valid UTF-8: {exc}",
            hint="Quranic Arabic must be stored as UTF-8. Re-save the file with "
            "UTF-8 encoding (in VS Code: bottom-right encoding selector -> "
            "'Save with Encoding' -> UTF-8).",
        )
    except json.JSONDecodeError as exc:
        raise QVGError(
            f"{path.name} is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})",
            hint="A trailing comma or a missing quote is the usual cause. "
            "VS Code highlights JSON errors as you type.",
        )

    if not isinstance(raw, dict):
        raise QVGError(f"{path.name} must contain a JSON object at the top level.")

    try:
        return Surah(**raw)
    except ValidationError as exc:
        details = "\n".join(
            f"  - {'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        raise QVGError(
            f"{path.name} did not pass validation:\n{details}",
            hint="Do not hand-edit the Quranic Arabic. If the text is damaged, "
            "regenerate the file with:\n    python run.py fetch-text --force",
        )


def save_surah(surah: Surah, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = surah.model_dump(exclude_none=False)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
