"""Splitting an ayah that is too long to read on one screen.

Every ayah wants to be one card. Most are: the fitter finds a size at which the
whole ayah sits on the frame, and that is the end of it. A few are not - ayah
2:282 is 128 words, and the only way to get it onto one card is to set it so
small that nobody would read it.

Those are split here, into as few pages as will each hold at a size worth
reading. A page is a run of consecutive words, so the ayah is never reordered
and never repeated; the recitation is not cut either, because each page is
shown for exactly the span in which its own words are recited, and the reciter
carries straight on into the next page.

The split is decided by measurement, not by counting words: the caller passes a
`fits` test that lays the candidate text out in the real font at the real size
and says whether it would be readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence


@dataclass(frozen=True)
class AyahPage:
    """One screen of an ayah.

    An ayah that fits has exactly one page, covering all of its words - which
    is the case this whole module is built to leave alone.
    """

    ayah: int
    index: int          # 0-based, in reading order
    count: int          # pages this ayah has in total
    first_word: int     # 0-based index into the ayah's recited words
    last_word: int      # inclusive
    text: str

    @property
    def word_count(self) -> int:
        return self.last_word - self.first_word + 1

    @property
    def is_only_page(self) -> bool:
        return self.count == 1

    def contains(self, word_index: int) -> bool:
        return self.first_word <= word_index <= self.last_word

    def local_index(self, word_index: int) -> int:
        """Where a word of the ayah sits within THIS page's own text."""
        return word_index - self.first_word

    def label(self) -> str:
        if self.is_only_page:
            return f"ayah {self.ayah}"
        return f"ayah {self.ayah} page {self.index + 1}/{self.count}"


def split_pages(
    ayah_number: int,
    words: Sequence[str],
    fits: Callable[[str], bool],
) -> list[AyahPage]:
    """Break *words* into the fewest pages that each satisfy *fits*.

    Greedy from the front, and the longest prefix that fits is found by binary
    search rather than by trying one word at a time - each trial lays real text
    out in a real font, so a 128-word ayah would otherwise cost hundreds of
    layouts.

    A single word that does not fit is still emitted as its own page. Nothing
    can be done for it here, and dropping it would drop scripture; the fitter
    will shrink it as far as it is allowed to instead.
    """
    total = len(words)
    if total == 0:
        raise ValueError(f"ayah {ayah_number} has no words to lay out")

    spans: list[tuple[int, int]] = []
    start = 0
    while start < total:
        remaining = total - start
        if fits(" ".join(words[start:])):
            spans.append((start, total - 1))
            break

        # The largest count that fits is somewhere in 1..remaining-1; anything
        # at or below `low` is known to fit, anything above `high` is known not
        # to. One word always "fits" by the rule above, so low starts there.
        low, high = 1, remaining - 1
        best = 1
        while low <= high:
            mid = (low + high) // 2
            if fits(" ".join(words[start:start + mid])):
                best = mid
                low = mid + 1
            else:
                high = mid - 1

        spans.append((start, start + best - 1))
        start += best

    return [
        AyahPage(
            ayah=ayah_number,
            index=position,
            count=len(spans),
            first_word=first,
            last_word=last,
            text=" ".join(words[first:last + 1]),
        )
        for position, (first, last) in enumerate(spans)
    ]


def single_page(ayah_number: int, text: str, word_count: int) -> AyahPage:
    """The whole ayah as one page - what every ayah gets when paging is off."""
    return AyahPage(
        ayah=ayah_number,
        index=0,
        count=1,
        first_word=0,
        last_word=max(0, word_count - 1),
        text=text,
    )
