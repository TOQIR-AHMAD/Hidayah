"""Per-word recitation timings, so the video can show which word is being read.

The timings are *published data*, not a guess and not something inferred from
the audio here: the quran.com API returns, for each recitation, the millisecond
span of every word in every ayah. `fetch` downloads them once into
`data/word_timings.json` and every render after that reads the file.

Two things are checked before the file is accepted, because a highlight that
drifts is worse than no highlight at all:

  * the number of timed words must equal the number of words in the ayah text
    already verified in `data/al_fatihah.json`, and
  * no word may end after the recitation file itself does.

If the file is missing, or a particular ayah fails those checks, that ayah falls
back to `estimate()` - time shared out across the words by letter count. It is
approximate, and `source` says so, so the caller can tell the difference.

No audio is downloaded here. The timings describe a recording you supply.
"""

from __future__ import annotations

import json
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from app.config import Config
from app.models.surah import Surah
from app.utils.logging import QVGError, get_logger

USER_AGENT = "quran-video-generator/1.0 (word timings)"


@dataclass(frozen=True)
class WordSpan:
    """One word of one ayah, in the source recording's own timebase."""

    index: int          # 0-based, in reading order (first word = 0)
    text: str
    start: float        # seconds from the start of that ayah's audio file
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class AyahTimings:
    ayah: int
    words: list[WordSpan]
    source: str         # "published" or "estimated"

    @property
    def is_estimated(self) -> bool:
        return self.source == "estimated"


def _spoken_letters(word: str) -> int:
    """Letters that take time to say.

    Harakat, sukun, shadda and the small Quranic annotation marks are combining
    characters: they change how a letter sounds, not how many letters there are,
    so counting them would make heavily-marked words look longer than they are.
    """
    return sum(1 for char in word if not unicodedata.combining(char)) or 1


def estimate(ayah_number: int, text: str, duration: float) -> AyahTimings:
    """Share *duration* out across the words of *text* by letter count.

    A fallback, and deliberately a crude one. Real recitation stretches the
    final word of an ayah far beyond its letter count, so this will run ahead
    of the voice near the end of a line.
    """
    words = text.split()
    weights = [_spoken_letters(w) for w in words]
    total = sum(weights) or 1
    spans: list[WordSpan] = []
    cursor = 0.0
    for index, (word, weight) in enumerate(zip(words, weights)):
        share = duration * weight / total
        spans.append(WordSpan(index=index, text=word, start=cursor, end=cursor + share))
        cursor += share
    return AyahTimings(ayah=ayah_number, words=spans, source="estimated")


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------

def _get_json(url: str, timeout: float = 45.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:  # pragma: no cover - network
        raise QVGError(f"{url} returned HTTP {error.code}") from error
    except urllib.error.URLError as error:  # pragma: no cover - network
        raise QVGError(f"Could not reach {url}: {error.reason}") from error


def _segments_to_spans(segments: list, words: list[str]) -> list[WordSpan]:
    """Turn the API's segment rows into spans, in word order.

    A row is either [word_number, start_ms, end_ms] or
    [segment_index, word_number, start_ms, end_ms]; both shapes are in the wild,
    so the millisecond pair is taken from the end of the row and the word number
    from the position before it.
    """
    spans: list[WordSpan] = []
    for row in segments:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        start_ms, end_ms = row[-2], row[-1]
        number = row[-3]
        try:
            index = int(number) - 1
            start = float(start_ms) / 1000.0
            end = float(end_ms) / 1000.0
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(words):
            continue
        spans.append(WordSpan(index=index, text=words[index], start=start, end=end))
    spans.sort(key=lambda s: s.index)
    return spans


def fetch(
    config: Config,
    surah: Surah,
    recitation_id: Optional[int] = None,
    destination: Optional[Path] = None,
) -> Path:
    """Download the word timings and write them next to the Quran text."""
    log = get_logger()
    settings = config.word_timings
    recitation_id = recitation_id or settings.recitation_id
    destination = destination or config.word_timings_file

    url = settings.url_template.format(
        recitation_id=recitation_id, surah=surah.surah_number
    )
    log.info("Fetching word timings from %s", url)
    payload = _get_json(url)

    files = payload.get("audio_files") or []
    if not files:
        raise QVGError(f"{url} returned no audio_files; nothing to read timings from")

    by_ayah: dict[str, list] = {}
    for entry in files:
        key = str(entry.get("verse_key", ""))
        if ":" not in key:
            continue
        chapter, _, verse = key.partition(":")
        if chapter != str(surah.surah_number):
            continue
        segments = entry.get("segments")
        if segments:
            by_ayah[verse] = segments

    written: dict[str, dict] = {}
    for ayah in surah.ayahs:
        segments = by_ayah.get(str(ayah.number))
        words = ayah.arabic.split()
        if segments is None:
            log.warning("No word timings published for ayah %d", ayah.number)
            continue
        spans = _segments_to_spans(segments, words)
        if len(spans) != len(words):
            log.warning(
                "Ayah %d: %d timed words but %d words of text - skipped",
                ayah.number, len(spans), len(words),
            )
            continue
        written[str(ayah.number)] = {
            "words": words,
            "timings": [[round(s.start, 3), round(s.end, 3)] for s in spans],
        }

    if not written:
        raise QVGError(
            "No usable word timings were returned. The video still renders; "
            "set content.word_highlight to false to stop asking for them."
        )

    document = {
        "surah_number": surah.surah_number,
        "source": {
            "name": "Quran.com API v4",
            "url": url,
            "recitation_id": recitation_id,
            "reciter": settings.reciter_name,
            "fetched_on": date.today().isoformat(),
            "note": (
                "Word timings only. No audio is taken from this source; they "
                "describe the recitation you supply in assets/audio."
            ),
        },
        "ayahs": written,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Wrote %s (%d ayahs timed)", destination.name, len(written))
    return destination


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load(
    config: Config,
    surah: Surah,
    durations: Optional[dict[int, float]] = None,
) -> dict[int, AyahTimings]:
    """Read the timings file, checking it against the text and the audio.

    *durations* maps ayah number to the length of its recitation file. Any ayah
    whose timings do not fit that file - or whose word count no longer matches
    the text - is estimated instead, so a stale file degrades rather than
    desynchronises.
    """
    log = get_logger()
    durations = durations or {}
    path = config.word_timings_file

    document: dict = {}
    if path.is_file():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            log.warning("Could not read %s (%s); estimating instead", path.name, error)
            document = {}
    elif config.content.word_highlight:
        log.warning(
            "%s is missing - word timings will be estimated. Run "
            "'python run.py fetch-word-timings' for the published ones.",
            path.name,
        )

    stored = document.get("ayahs") or {}
    result: dict[int, AyahTimings] = {}

    for ayah in surah.ayahs:
        words = ayah.arabic.split()
        duration = durations.get(ayah.number, 0.0)
        spans = _accept(ayah.number, stored.get(str(ayah.number)), words, duration, log)
        if spans is None:
            result[ayah.number] = estimate(ayah.number, ayah.arabic, duration)
        else:
            result[ayah.number] = AyahTimings(
                ayah=ayah.number, words=spans, source="published"
            )
    return result


def _accept(
    ayah_number: int,
    entry: Optional[dict],
    words: list[str],
    duration: float,
    log,
) -> Optional[list[WordSpan]]:
    """Validate one stored ayah, returning its spans or None to fall back."""
    if not entry:
        return None

    timings = entry.get("timings") or []
    if len(timings) != len(words):
        log.warning(
            "Ayah %d: timings file has %d words, the text has %d - estimating",
            ayah_number, len(timings), len(words),
        )
        return None

    # The stored word list is kept purely so a changed text is noticed. It is a
    # different edition, or a re-fetch, if these no longer line up.
    stored_words = entry.get("words")
    if stored_words and list(stored_words) != words:
        log.warning(
            "Ayah %d: the timings were made for different wording - estimating",
            ayah_number,
        )
        return None

    spans: list[WordSpan] = []
    previous_end = -1.0
    for index, pair in enumerate(timings):
        try:
            start, end = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            log.warning("Ayah %d: malformed timing at word %d - estimating",
                        ayah_number, index + 1)
            return None
        if end < start or start < previous_end - 0.001:
            log.warning(
                "Ayah %d: word %d is out of order (%.3f-%.3f) - estimating",
                ayah_number, index + 1, start, end,
            )
            return None
        previous_end = end
        spans.append(WordSpan(index=index, text=words[index], start=start, end=end))

    if duration > 0 and spans and spans[-1].end > duration + 0.05:
        log.warning(
            "Ayah %d: timings run to %.2fs but the recitation is %.2fs long "
            "- estimating",
            ayah_number, spans[-1].end, duration,
        )
        return None

    return spans


def describe(timings: dict[int, AyahTimings]) -> str:
    """A one-line summary for `validate`."""
    if not timings:
        return "no word timings"
    estimated = sorted(n for n, t in timings.items() if t.is_estimated)
    words = sum(len(t.words) for t in timings.values())
    if not estimated:
        return f"{words} words timed from published data"
    if len(estimated) == len(timings):
        return f"{words} words, all estimated by letter count"
    listed = ", ".join(str(n) for n in estimated)
    return f"{words} words timed; ayah {listed} estimated"
