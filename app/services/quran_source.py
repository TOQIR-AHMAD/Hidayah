"""Fetch verified Quranic text and an Urdu translation into data/al_fatihah.json.

The application never generates Quranic Arabic. This module downloads published
editions from a Quran text service and records exactly which edition each string
came from, so the provenance travels with the data.

Default editions:
  Arabic : "quran-uthmani"  - the Tanzil Uthmani text
  Urdu   : "ur.junagarhi"   - Muhammad Junagarhi, the translation printed in the
                             King Fahd Complex Urdu mushaf. Uses اللہ throughout.

Run:  python run.py fetch-text
"""

from __future__ import annotations

import json
import unicodedata
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

from app.models.ayah import Ayah, arabic_ratio
from app.models.surah import AL_FATIHAH_AYAH_COUNT, Surah, TextSource, save_surah
from app.utils.logging import QVGError, get_logger

API_BASE = "https://api.alquran.cloud/v1"
DEFAULT_ARABIC_EDITION = "quran-uthmani"
DEFAULT_URDU_EDITION = "ur.junagarhi"
DEFAULT_ENGLISH_EDITION = "en.sahih"

SURAH_NAME_ARABIC = "الفاتحة"          # الفاتحة
SURAH_NAME_URDU = "سورۃ الفاتحہ"  # سورۃ الفاتحہ


def _get_json(url: str, timeout: float = 45.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "quran-video-generator/1.0", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise QVGError(
            f"The Quran text service returned HTTP {exc.code} for {url}",
            hint="Check the edition identifier, or write data/al_fatihah.json by hand "
            "from a printed mushaf. See the README section 'Quran text'.",
        )
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise QVGError(
            f"Could not reach the Quran text service: {exc}",
            hint="This command needs an internet connection. If you are offline, copy "
            "the verified text into data/al_fatihah.json manually - the file format "
            "is documented in the README.",
        )
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QVGError(f"The Quran text service returned something that is not JSON: {exc}")


# Transport artefacts that some Quran feeds prepend to the first ayah. These are
# not part of the Quranic text - they are byte-order marks and invisible
# formatting codepoints - so removing them does not alter a single letter.
_ARTEFACTS = "﻿​‎‏⁠"


def _clean(text: str) -> str:
    cleaned = "".join(c for c in text if c not in _ARTEFACTS)
    return " ".join(cleaned.split())


def _unify_presentation_forms(text: str) -> str:
    """Replace legacy Arabic presentation forms with their normal letters.

    Some translation editions carry characters from the Arabic Presentation
    Forms blocks - Junagarhi, for instance, writes U+FEFB (LAM WITH ALEF
    ISOLATED FORM) instead of the two letters lam + alef. Those codepoints are
    a pre-shaped legacy encoding: they carry no joining behaviour, so the
    preceding letter cannot connect to them and the word renders broken in
    Nastaliq. Their compatibility decomposition is exactly the plain letters,
    which the shaper then joins correctly.

    This is applied to TRANSLATIONS ONLY. Uthmani Quranic orthography depends
    on exact codepoints, and normalising it would silently alter scripture.
    """
    out = []
    for char in text:
        code = ord(char)
        if 0xFB50 <= code <= 0xFDFF or 0xFE70 <= code <= 0xFEFF:
            out.append(unicodedata.normalize("NFKC", char))
        else:
            out.append(char)
    return "".join(out)


def _clean_translation(text: str) -> str:
    return _unify_presentation_forms(_clean(text))


def _fetch_edition(edition: str, scripture: bool = False) -> tuple[list[str], str]:
    """Return (texts for ayahs 1..7, human-readable edition name).

    Set *scripture* for the Quranic Arabic: it is then cleaned of transport
    artefacts only, never normalised.
    """
    data = _get_json(f"{API_BASE}/surah/1/{edition}")
    if data.get("code") != 200 or "data" not in data:
        raise QVGError(f"Unexpected response for edition '{edition}': {data.get('status')}")

    payload = data["data"]
    ayahs = payload.get("ayahs") or []
    if len(ayahs) != AL_FATIHAH_AYAH_COUNT:
        raise QVGError(
            f"Edition '{edition}' returned {len(ayahs)} ayahs for Surah Al-Fatihah, "
            f"expected {AL_FATIHAH_AYAH_COUNT}."
        )

    clean = _clean if scripture else _clean_translation
    texts = [clean(str(a.get("text", ""))) for a in ayahs]
    for index, text in enumerate(texts, start=1):
        if not text:
            raise QVGError(f"Edition '{edition}' returned empty text for ayah {index}.")

    info = payload.get("edition") or {}
    name = info.get("name") or edition
    english_name = info.get("englishName")
    label = f"{name} ({english_name})" if english_name and english_name != name else str(name)
    return texts, label


def fetch_al_fatihah(
    destination: Path,
    arabic_edition: str = DEFAULT_ARABIC_EDITION,
    urdu_edition: str = DEFAULT_URDU_EDITION,
    english_edition: str = DEFAULT_ENGLISH_EDITION,
    force: bool = False,
) -> Surah:
    log = get_logger()

    if destination.is_file() and not force:
        raise QVGError(
            f"{destination} already exists.",
            hint="Pass --force to overwrite it:\n    python run.py fetch-text --force",
        )

    log.info("Fetching Arabic edition '%s'", arabic_edition)
    arabic_texts, arabic_label = _fetch_edition(arabic_edition, scripture=True)

    log.info("Fetching Urdu edition '%s'", urdu_edition)
    urdu_texts, urdu_label = _fetch_edition(urdu_edition)

    english_texts: list[str] = []
    english_label = ""
    if english_edition:
        try:
            english_texts, english_label = _fetch_edition(english_edition)
        except QVGError as exc:  # English is a nice-to-have, never fatal
            log.warning("Skipping the English edition: %s", exc.message)

    # Sanity check before anything is written: the Arabic must actually be Arabic.
    for index, text in enumerate(arabic_texts, start=1):
        ratio = arabic_ratio(text)
        if ratio < 0.9:
            raise QVGError(
                f"Ayah {index} from edition '{arabic_edition}' is only {ratio:.0%} Arabic "
                "characters. Refusing to write a file that may hold corrupted Quranic text.",
            )

    ayahs = [
        Ayah(
            number=index,
            arabic=arabic_texts[index - 1],
            urdu_translation=urdu_texts[index - 1],
            english_translation=english_texts[index - 1] if english_texts else None,
            recitation=f"assets/audio/recitation/{index:03d}.mp3",
            urdu_audio=f"assets/audio/urdu/{index:03d}.mp3",
        )
        for index in range(1, AL_FATIHAH_AYAH_COUNT + 1)
    ]

    surah = Surah(
        surah_number=1,
        name_arabic=SURAH_NAME_ARABIC,
        name_english="Al-Fatihah",
        name_urdu=SURAH_NAME_URDU,
        ayah_count=AL_FATIHAH_AYAH_COUNT,
        revelation_place="Makkah",
        ayahs=ayahs,
        source=TextSource(
            arabic_edition=arabic_label,
            arabic_source=f"{API_BASE}/surah/1/{arabic_edition}",
            urdu_edition=urdu_label,
            urdu_source=f"{API_BASE}/surah/1/{urdu_edition}",
            retrieved_at=date.today().isoformat(),
            note=(
                "Quranic Arabic and the Urdu translation are stored verbatim from the "
                "editions named above. They are never generated, paraphrased or edited "
                "by this application. Verify against a printed mushaf before publishing."
                + (f" English gloss: {english_label}." if english_label else "")
            ),
        ),
    )

    save_surah(surah, destination)
    log.info("Wrote %s", destination)
    return surah
