"""Ayah counts for all 114 surahs, and the global ayah numbering built on them.

Two different numberings appear in Quran data services:

* the LOCAL number - the ayah's place inside its own surah (2:255 -> 255), which
  is what this project shows on screen and names its audio files by, and
* the GLOBAL number - its place in the whole mushaf (2:255 -> 262), which the
  Islamic Network audio CDN indexes by.

For Surah Al-Fatihah the two coincide, which is why the first version of this
project never had to tell them apart. They part company from Al-Baqarah onward,
so the conversion lives here.

The counts are the standard Kufan division used by the Uthmani mushaf and by
every edition this project fetches; `python run.py fetch-text` checks the ayah
count it receives against this table before writing anything.
"""

from __future__ import annotations

from app.utils.logging import QVGError

# Ayahs per surah, 1..114.
AYAH_COUNTS: tuple[int, ...] = (
    7, 286, 200, 176, 120, 165, 206, 75, 129, 109,
    123, 111, 43, 52, 99, 128, 111, 110, 98, 135,
    112, 78, 118, 64, 77, 227, 93, 88, 69, 60,
    34, 30, 73, 54, 45, 83, 182, 88, 75, 85,
    54, 53, 89, 59, 37, 35, 38, 29, 18, 45,
    60, 49, 62, 55, 78, 96, 29, 22, 24, 13,
    14, 11, 11, 18, 12, 12, 30, 52, 52, 44,
    28, 28, 20, 56, 40, 31, 50, 40, 46, 42,
    29, 19, 36, 25, 22, 17, 19, 26, 30, 20,
    15, 21, 11, 8, 8, 19, 5, 8, 8, 11,
    11, 8, 3, 9, 5, 4, 7, 3, 6, 3,
    5, 4, 5, 6,
)

SURAH_COUNT = len(AYAH_COUNTS)
TOTAL_AYAHS = sum(AYAH_COUNTS)  # 6236

# Surahs whose first ayah is preceded by the Bismillah in the mushaf but which
# do not count it as an ayah. Al-Fatihah counts it as ayah 1; At-Tawbah has none.
_BISMILLAH_IS_AYAH_ONE = 1
_NO_BISMILLAH = 9


def validate_number(surah_number: int) -> int:
    if not isinstance(surah_number, int) or not 1 <= surah_number <= SURAH_COUNT:
        raise QVGError(
            f"There is no surah {surah_number}; the Quran has {SURAH_COUNT} surahs.",
        )
    return surah_number


def ayah_count(surah_number: int) -> int:
    """How many ayahs surah *surah_number* has."""
    return AYAH_COUNTS[validate_number(surah_number) - 1]


def global_offset(surah_number: int) -> int:
    """Ayahs in the whole mushaf BEFORE this surah starts.

    Al-Fatihah -> 0, Al-Baqarah -> 7.
    """
    return sum(AYAH_COUNTS[: validate_number(surah_number) - 1])


def global_ayah(surah_number: int, ayah_number: int) -> int:
    """Mushaf-wide ayah number, 1..6236."""
    count = ayah_count(surah_number)
    if not 1 <= ayah_number <= count:
        raise QVGError(
            f"Surah {surah_number} has {count} ayahs; there is no ayah {ayah_number}."
        )
    return global_offset(surah_number) + ayah_number


def has_bismillah_prefix(surah_number: int) -> bool:
    """True when an edition may prepend the Bismillah to this surah's first ayah.

    Quran text services return the Bismillah as part of ayah 1 for every surah
    that opens with it but does not count it as an ayah - that is, everything
    except Al-Fatihah (where it IS ayah 1) and At-Tawbah (which has none).
    Leaving it in would put a line on screen that the reciter never says in that
    ayah's recording, and would push every word highlight one word along.
    """
    validate_number(surah_number)
    return surah_number not in (_BISMILLAH_IS_AYAH_ONE, _NO_BISMILLAH)
