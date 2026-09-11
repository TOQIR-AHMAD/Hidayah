"""Turns the audio plan into a timeline of segments.

Nothing here is a fixed number of seconds. Every ayah's on-screen time is
derived from the real duration of its recording, plus the configured padding.
The sequence is strictly one ayah at a time:

    intro
    ayah 1 Arabic -> ayah 1 Urdu
    ayah 2 Arabic -> ayah 2 Urdu
    ...
    ayah 7 Arabic -> ayah 7 Urdu
    outro
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterator, Literal, Optional

from app.config import Config
from app.models.surah import Surah
from app.rendering.pagination import AyahPage
from app.services.audio import AudioClip, AudioPlan
from app.utils.logging import get_logger

SegmentKind = Literal["intro", "arabic", "urdu", "outro"]


@dataclass
class Segment:
    """One card on screen for one span of time."""

    index: int
    kind: SegmentKind
    start: float
    duration: float
    card_id: str
    ayah_number: Optional[int] = None
    audio: Optional[AudioClip] = None
    audio_offset: float = 0.0
    audio_speed: float = 1.0
    subtitle_text: str = ""
    subtitle_language: str = ""
    # Set only on an ayah too long for one screen. The page says which of the
    # ayah's words this card carries; the offset says how far into the ayah's
    # recording its clip was cut, which is what the word timings are measured
    # against.
    page: Optional[AyahPage] = None
    audio_source_offset: float = 0.0

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def audio_start(self) -> float:
        return self.start + self.audio_offset

    @property
    def audio_end(self) -> float:
        # played, not source: a clip re-timed by playback_speed occupies less
        # of the timeline than its source length.
        if self.audio is None:
            return self.audio_start
        return self.audio_start + self.audio.played_duration(self.audio_speed)

    def label(self) -> str:
        if self.ayah_number is None:
            return self.kind.capitalize()
        if self.page is not None and not self.page.is_only_page:
            return (
                f"Ayah {self.ayah_number} {self.kind} "
                f"{self.page.index + 1}/{self.page.count}"
            )
        return f"Ayah {self.ayah_number} {self.kind}"

    def holds_word(self, word_index: int) -> bool:
        """Whether this card shows the ayah's *word_index*-th recited word."""
        return self.page is None or self.page.contains(word_index)


@dataclass
class Timeline:
    segments: list[Segment] = field(default_factory=list)
    total_duration: float = 0.0
    mode: str = "per_ayah"

    def __iter__(self) -> Iterator[Segment]:
        return iter(self.segments)

    def __len__(self) -> int:
        return len(self.segments)

    @property
    def with_audio(self) -> list[Segment]:
        return [s for s in self.segments if s.audio is not None]

    @property
    def ayah_segments(self) -> list[Segment]:
        return [s for s in self.segments if s.ayah_number is not None]

    def of_kind(self, kind: SegmentKind) -> list[Segment]:
        return [s for s in self.segments if s.kind == kind]

    def summary_rows(self) -> list[tuple[str, str, str, str]]:
        rows = []
        for segment in self.segments:
            rows.append(
                (
                    segment.label(),
                    _timecode(segment.start),
                    _timecode(segment.end),
                    f"{segment.duration:.2f}s",
                )
            )
        return rows


def _timecode(seconds: float) -> str:
    total = max(0.0, seconds)
    minutes, secs = divmod(total, 60)
    return f"{int(minutes):02d}:{secs:05.2f}"


def _page_cuts(
    pages: list[AyahPage], timing, clip_duration: float
) -> list[tuple[float, float]]:
    """Where each page's audio begins and ends inside the ayah's recording.

    The cuts are taken at the START of each page's first word, and one page ends
    exactly where the next begins, so the pages tile the whole recording: no
    syllable is played twice and none is dropped. The first page keeps whatever
    lead-in the file has, and the last runs to the end of it - the reciter's
    final word is often held far past where its timing says it ends.
    """
    starts = [0.0]
    for page in pages[1:]:
        spans = timing.words if timing is not None else []
        if page.first_word < len(spans):
            starts.append(max(starts[-1], spans[page.first_word].start))
        else:
            # No timing for that word: share what is left evenly rather than
            # dropping the page. `validate` already reports estimated ayahs.
            share = clip_duration * page.index / max(1, len(pages))
            starts.append(max(starts[-1], share))

    bounds = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else clip_duration
        bounds.append((start, max(start, end)))
    return bounds


def _arabic_segments(
    ayah,
    recitation: AudioClip,
    pages: Optional[list[AyahPage]],
    timing,
    pad_before: float,
    pad_after: float,
    min_arabic: float,
    trailing_gap: float,
    speed: float,
) -> list[Segment]:
    """One segment for an ayah that fits; one per page for an ayah that does not.

    The recitation is never paused at a page turn: only the first page carries
    the lead-in silence and only the last carries the trailing silence, so the
    words run straight on while the card changes underneath them.
    """
    if not pages or len(pages) == 1:
        page = pages[0] if pages else None
        duration = max(
            min_arabic, pad_before + recitation.played_duration(speed) + pad_after
        ) + trailing_gap
        return [
            Segment(
                index=0,
                kind="arabic",
                start=0.0,
                duration=duration,
                card_id=f"ayah_{ayah.number:03d}_arabic",
                ayah_number=ayah.number,
                audio=recitation,
                audio_offset=pad_before,
                audio_speed=speed,
                subtitle_text=ayah.arabic,
                subtitle_language="ar",
                page=page,
            )
        ]

    segments: list[Segment] = []
    for page, (start, end) in zip(pages, _page_cuts(pages, timing, recitation.duration)):
        first, last = page.index == 0, page.index == len(pages) - 1
        clip = replace(
            recitation,
            duration=max(0.01, end - start),
            trim_start=recitation.trim_start + start,
            label=f"{recitation.label} ({page.index + 1}/{page.count})",
        )
        lead = pad_before if first else 0.0
        tail = pad_after if last else 0.0
        duration = lead + clip.played_duration(speed) + tail
        if last:
            duration = max(min_arabic, duration) + trailing_gap
        segments.append(
            Segment(
                index=0,
                kind="arabic",
                start=0.0,
                duration=duration,
                card_id=f"ayah_{ayah.number:03d}_p{page.index + 1}_arabic",
                ayah_number=ayah.number,
                audio=clip,
                audio_offset=lead,
                audio_speed=speed,
                subtitle_text=page.text,
                subtitle_language="ar",
                page=page,
                audio_source_offset=start,
            )
        )
    return segments


def build_timeline(
    config: Config,
    surah: Surah,
    plan: AudioPlan,
    max_ayahs: int = 0,
    pages: Optional[dict[int, list[AyahPage]]] = None,
    timings: Optional[dict] = None,
) -> Timeline:
    """Lay every segment end to end, following the audio durations.

    *pages* splits an ayah that will not fit one screen; *timings* says when
    each of its words is recited, which is where the splits are cut. Without
    either, every ayah is one card carrying its whole recitation - which is
    what every ayah short enough to fit gets anyway.
    """
    animation = config.animation
    audio_cfg = config.audio

    # playback_speed scales the entire timeline, so the finished file really is
    # 1.25x (say) rather than something the viewer has to speed up themselves.
    # The clips carry the same rate and are re-timed with atempo, which changes
    # tempo without transposing the reciter's voice.
    speed = max(0.01, config.video.playback_speed)
    pad_before = audio_cfg.padding_before / speed
    pad_after = audio_cfg.padding_after / speed
    gap = audio_cfg.gap_between_ayahs / speed
    min_arabic = audio_cfg.min_arabic_seconds / speed
    min_urdu = audio_cfg.min_urdu_seconds / speed

    ayahs = surah.ayahs
    if max_ayahs and max_ayahs > 0:
        ayahs = ayahs[:max_ayahs]

    timeline = Timeline(mode=plan.mode)
    cursor = 0.0
    index = 0

    intro_duration = animation.scaled(animation.intro_duration) / speed
    if intro_duration > 0:
        timeline.segments.append(
            Segment(
                index=index,
                kind="intro",
                start=cursor,
                duration=intro_duration,
                card_id="intro",
            )
        )
        cursor += intro_duration
        index += 1

    for position, ayah in enumerate(ayahs):
        recitation = plan.recitation[ayah.number]
        narration = plan.urdu.get(ayah.number)

        # The audio drives the length. The minimum only ever extends a section
        # whose clip is so short that the text would flash past unread - the
        # extra time becomes trailing silence, which reads as a pause.
        is_last = position == len(ayahs) - 1
        show_urdu = config.content.urdu_translation

        # With no Urdu card to follow it, the breath between ayahs has to sit at
        # the end of the Arabic section instead.
        trailing_gap = gap if (not show_urdu and not is_last) else 0.0

        for segment in _arabic_segments(
            ayah=ayah,
            recitation=recitation,
            pages=(pages or {}).get(ayah.number),
            timing=(timings or {}).get(ayah.number),
            pad_before=pad_before,
            pad_after=pad_after,
            min_arabic=min_arabic,
            trailing_gap=trailing_gap,
            speed=speed,
        ):
            segment.index = index
            segment.start = cursor
            timeline.segments.append(segment)
            cursor += segment.duration
            index += 1

        if not show_urdu:
            continue

        if narration is not None:
            spoken = pad_before + narration.played_duration(speed) + pad_after
        else:
            # Nothing is spoken, so the card has to be held for as long as the
            # translation takes to READ. A flat minimum would leave the long
            # ayahs unreadable and the short ones sitting there.
            words = max(1, len(ayah.urdu_translation.split()))
            spoken = (
                pad_before
                + words * audio_cfg.urdu_seconds_per_word / speed
                + pad_after
            )
        urdu_body = max(min_urdu, spoken)
        urdu_duration = urdu_body + (0.0 if is_last else gap)
        timeline.segments.append(
            Segment(
                index=index,
                kind="urdu",
                start=cursor,
                duration=urdu_duration,
                card_id=f"ayah_{ayah.number:03d}_urdu",
                ayah_number=ayah.number,
                audio=narration,
                audio_offset=pad_before,
                audio_speed=speed,
                subtitle_text=ayah.urdu_translation,
                subtitle_language="ur",
            )
        )
        cursor += urdu_duration
        index += 1

    outro_duration = animation.scaled(animation.outro_duration) / speed
    if outro_duration > 0:
        timeline.segments.append(
            Segment(
                index=index,
                kind="outro",
                start=cursor,
                duration=outro_duration,
                card_id="outro",
            )
        )
        cursor += outro_duration

    timeline.total_duration = cursor
    return timeline


@dataclass(frozen=True)
class CardWindow:
    """A card clip: exactly as long as its segment, with its own alpha ramps.

    Each card fades fully out before the next fades in, leaving a short beat
    where only the moving background is visible. That beat lands inside the
    silent padding after the audio, so it reads as a breath between ayahs rather
    than a gap - and two different ayahs are never legible on screen at once.
    """

    segment: Segment
    start: float
    duration: float
    fade_in: float
    fade_out: float
    hold_after: float = 0.0

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def fade_out_start(self) -> float:
        """When the fade-out begins, in the clip's own timebase."""
        return max(self.fade_in, self.duration - self.hold_after - self.fade_out)


@dataclass(frozen=True)
class WordWindow:
    """When one word of one ayah is lit, in timeline seconds."""

    segment_index: int
    ayah_number: int
    word_index: int
    start: float
    end: float
    estimated: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def word_windows(
    config: Config,
    timeline: Timeline,
    timings: dict,
    windows: Optional[list["CardWindow"]] = None,
) -> list[WordWindow]:
    """Place each word's highlight on the timeline.

    Three conversions happen here, and all three matter:

      * the timings are measured in the source recording, so they are divided
        by playback_speed and offset by where that clip starts on the timeline;
      * a highlight is held into the silence after its word (`carry_over`) and
        given a floor (`min_seconds`), because a 90 ms flash reads as a glitch
        rather than as a cue; and
      * every window is clipped to the stretch where its card is at full
        opacity, so a word can never light up over a card that is still fading
        in - which would look like the word arriving before the ayah does.
    """
    if not config.content.word_highlight:
        return []

    settings = config.word_timings
    speed = max(0.01, config.video.playback_speed)
    windows = windows if windows is not None else card_windows(config, timeline)
    opaque = {w.segment.index: w for w in windows}

    placed: list[WordWindow] = []
    dropped: list[tuple[int, int]] = []
    for segment in timeline.segments:
        if segment.kind != "arabic" or segment.ayah_number is None:
            continue
        entry = timings.get(segment.ayah_number)
        if entry is None or not entry.words:
            continue

        card = opaque.get(segment.index)
        if card is not None:
            first_moment = card.start + card.fade_in
            last_moment = card.start + card.fade_out_start
        else:
            first_moment, last_moment = segment.start, segment.end

        # A paged ayah's clip begins part-way into the recording, but its word
        # timings are still measured from the start of the file, so the cut has
        # to come off every one of them.
        origin = segment.audio_start - segment.audio_source_offset / speed
        spans = entry.words
        for position, span in enumerate(spans):
            if not segment.holds_word(span.index):
                continue
            start = origin + span.start / speed
            end = origin + span.end / speed

            # Hold into the gap before the next word rather than going dark -
            # but only as far as this card's own last word, or the colour would
            # run on into a page that is no longer on screen.
            on_this_card = (
                position + 1 < len(spans)
                and segment.holds_word(spans[position + 1].index)
            )
            next_start = (
                origin + spans[position + 1].start / speed
                if on_this_card
                else segment.audio_end
            )
            gap = max(0.0, next_start - end)
            end += gap * max(0.0, min(1.0, settings.carry_over))

            floor = settings.min_seconds / speed
            if end - start < floor:
                end = min(start + floor, max(next_start, start + floor))

            start = max(start, first_moment)
            end = min(end, last_moment)
            if end <= start:
                # The card is not yet at full opacity - or is already leaving -
                # for the whole of this word, so there is nothing to light.
                dropped.append((segment.ayah_number, span.index + 1))
                continue

            placed.append(
                WordWindow(
                    segment_index=segment.index,
                    ayah_number=segment.ayah_number,
                    word_index=span.index,
                    start=start,
                    end=end,
                    estimated=entry.is_estimated,
                )
            )

    if dropped:
        # Worth saying out loud: it is nearly always animation.text_fade_in
        # being longer than audio.padding_before, which swallows the opening
        # word of every ayah, and it is invisible in the finished file.
        get_logger().warning(
            "%d word(s) are spoken before their card is fully visible and will "
            "not be highlighted (first: ayah %d word %d). Lower "
            "animation.text_fade_in to at most audio.padding_before (%.2f).",
            len(dropped), dropped[0][0], dropped[0][1],
            config.audio.padding_before,
        )

    placed.sort(key=lambda w: (w.start, w.word_index))
    return placed


def card_windows(config: Config, timeline: Timeline) -> list[CardWindow]:
    """Work out the alpha schedule for every card."""
    animation = config.animation
    speed = max(0.01, config.video.playback_speed)
    handover = animation.scaled(animation.transition_duration) / speed
    fade_in = animation.scaled(animation.text_fade_in) / speed
    fade_out = animation.scaled(animation.text_fade_out) / speed

    windows: list[CardWindow] = []
    last_index = len(timeline.segments) - 1

    for position, segment in enumerate(timeline.segments):
        is_first = position == 0
        is_last = position == last_index
        duration = segment.duration

        this_fade_in = (
            animation.scaled(animation.intro_fade_in) / speed if is_first else fade_in
        )
        this_fade_out = (
            animation.scaled(animation.outro_fade_out) / speed if is_last else fade_out
        )
        this_hold = 0.0 if is_last else handover

        # Ramps and the trailing beat must all fit inside the segment, with at
        # least a moment of the card at full opacity in between.
        budget = duration * 0.85
        total = this_fade_in + this_fade_out + this_hold
        if total > budget and total > 0:
            shrink = budget / total
            this_fade_in *= shrink
            this_fade_out *= shrink
            this_hold *= shrink

        # The card should stay fully opaque while its clip is playing, so the
        # fade-out and the beat are fitted into the trailing silence - otherwise
        # the text dissolves under the reciter's last syllable.
        #
        # That has to give way when the padding is deliberately tight. Squeezing
        # a dissolve into a tenth of a second turns it into a hard cut, which
        # looks worse than letting it start a moment early, so it is never
        # shrunk below min_text_fade_out. Below that floor the beat is dropped
        # first, and only then does the ramp reach back over the audio.
        if segment.audio is not None:
            tail = max(0.0, duration - (segment.audio_end - segment.start))
            occupied = this_fade_out + this_hold
            if occupied > tail and occupied > 0:
                fit = tail / occupied
                this_fade_out *= fit
                this_hold *= fit

            floor = min(fade_out, animation.min_text_fade_out / speed)
            if this_fade_out < floor:
                this_fade_out = floor
                this_hold = 0.0

        # Whatever the clamps did, the ramps still have to fit the card.
        total = this_fade_in + this_fade_out + this_hold
        if total > duration and total > 0:
            shrink = duration / total
            this_fade_in *= shrink
            this_fade_out *= shrink
            this_hold *= shrink

        windows.append(
            CardWindow(
                segment=segment,
                start=segment.start,
                duration=duration,
                fade_in=this_fade_in,
                fade_out=this_fade_out,
                hold_after=this_hold,
            )
        )
    return windows
