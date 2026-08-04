"""Identify vocal tracks and decide the rhythm/lead role of a guitar part.

Role is a guitar-only annotation. Bass, drums, keys and everything else carry a
program and no role at all: the rhythm/lead distinction is being learned for
guitar, and asserting it for a piano or a bass line would be inventing labels
that nothing downstream asked for.

For guitars, role is inferred from musical content, with the source track name
as corroborating evidence. Names like "Rhythm Guitar" are frequently absent or
wrong, and vocal tracks routinely carry unrelated programs, so vocals are
detected by name and role by content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import gm
from .midiscan import Note, Segment

VOCAL_PATTERN = re.compile(r"\b(vocals?|voc|vox|voice|singer|singing)\b", re.IGNORECASE)

_RHYTHM_WORDS = re.compile(r"\b(rhythm|rhythmic|comp|chords?|backing|riff|pad)\b", re.IGNORECASE)
_LEAD_WORDS = re.compile(r"\b(lead|solo|melody|hook|fill)\b", re.IGNORECASE)

# Geometry for the mixed-usage scan, in beats. Two bars of 4/4 gives a window
# enough notes for its texture statistics to mean anything.
WINDOW_BEATS = 8
MIN_NOTES_PER_WINDOW = 4
MIN_WINDOWS_TO_SCAN = 4
# A guitar counts as mixed once the minority texture holds this share of its
# windows — below that it is a fill or a transition, not a second role.
MIXED_SHARE = 0.2


@dataclass
class Role:
    rhythm: bool
    confidence: float
    evidence: list[str]
    mixed: bool = False

    @property
    def note(self) -> str | None:
        """Populates the manifest's role_annotation_note when the call was close."""
        if self.mixed:
            return "mixed usage: " + "; ".join(self.evidence)
        if self.confidence >= 0.4:
            return None
        return "low-confidence role: " + "; ".join(self.evidence)


def is_vocal(name: str) -> bool:
    return bool(VOCAL_PATTERN.search(name))


def takes_role(segment: Segment) -> bool:
    """Only guitars carry a rhythm/lead label."""
    return not segment.is_drum and gm.is_guitar(segment.program)


def chord_ratio(notes: list[Note], window: int) -> float:
    """Fraction of onsets sounding together with at least one other note."""
    if not notes:
        return 0.0
    starts = sorted(n.start for n in notes)
    grouped = 0
    i = 0
    while i < len(starts):
        j = i
        while j + 1 < len(starts) and starts[j + 1] - starts[i] <= window:
            j += 1
        size = j - i + 1
        if size > 1:
            grouped += size
        i = j + 1
    return grouped / len(starts)


def onset_polyphony(notes: list[Note], window: int) -> float:
    """Average notes per onset group — 1.0 for a purely sequential line.

    Deliberately measures notes *struck* together rather than notes *sounding*
    together. Sustain overlap cannot tell a strummed chord from a fingerpicked
    arpeggio whose notes ring into one another: a monophonic line with long
    release reaches an overlap of 5.7 while striking one note at a time.
    """
    if not notes:
        return 0.0
    starts = sorted(n.start for n in notes)
    groups = 0
    i = 0
    while i < len(starts):
        j = i
        while j + 1 < len(starts) and starts[j + 1] - starts[i] <= window:
            j += 1
        groups += 1
        i = j + 1
    return len(starts) / groups


def coverage(notes: list[Note], ppq: int, song_ticks: int) -> float:
    """Fraction of the song's beats in which this part sounds.

    Accompaniment runs through the piece while a lead appears in bursts. This
    is meaningful only against the whole song: measured inside a window a
    sounding part covers essentially all of it, which is why the mixed-usage
    scan below judges texture alone.
    """
    if not notes or song_ticks <= 0:
        return 0.0
    beats = set()
    for note in notes:
        for beat in range(note.start // ppq, note.end // ppq + 1):
            beats.add(beat)
    return min(1.0, len(beats) / max(1, song_ticks // ppq + 1))


def _texture_score(notes: list[Note], ppq: int) -> tuple[float, list[str]]:
    """How chordal the playing is, independent of how long it runs."""
    window = max(1, ppq // 16)
    ratio = chord_ratio(notes, window)
    polyphony = onset_polyphony(notes, window)

    # Chordal texture is the strongest evidence of accompaniment, and a line
    # that is genuinely sequential is evidence the other way, so this term
    # argues in both directions.
    score = max(-0.6, min(0.8, (ratio - 0.30) * 1.7))
    score += max(0.0, min(0.4, (polyphony - 1.5) * 0.4))
    return score, [f"chord ratio {ratio:.2f}", f"onset polyphony {polyphony:.2f}"]


def _name_score(source_name: str) -> tuple[float, list[str]]:
    score = 0.0
    evidence: list[str] = []
    if _RHYTHM_WORDS.search(source_name):
        score += 0.5
        evidence.append("name suggests rhythm")
    if _LEAD_WORDS.search(source_name):
        score -= 0.5
        evidence.append("name suggests lead")
    return score, evidence


def named_rhythm(source_name: str) -> bool:
    """True when the source track calls itself rhythm and nothing contradicts it."""
    return bool(_RHYTHM_WORDS.search(source_name)) and not _LEAD_WORDS.search(source_name)


def texture_windows(segment: Segment, ppq: int, window_beats: int = WINDOW_BEATS) -> list[bool]:
    """Per-window chordal/sequential calls, skipping windows too sparse to judge."""
    notes = segment.notes
    if not notes:
        return []
    window_ticks = ppq * window_beats
    origin = min(n.start for n in notes)
    finish = max(n.end for n in notes)
    labels: list[bool] = []

    for index in range(max(1, -(-(finish - origin) // window_ticks))):
        start = origin + index * window_ticks
        chunk = [n for n in notes if start <= n.start < start + window_ticks]
        if len(chunk) < MIN_NOTES_PER_WINDOW:
            continue
        score, _ = _texture_score(chunk, ppq)
        labels.append(score > 0)
    return labels


def is_mixed(segment: Segment, ppq: int, window_beats: int = WINDOW_BEATS) -> tuple[bool, str]:
    """Does this guitar alternate between chordal and single-line playing?

    Stairway's acoustic guitar and Californication's Gretsch both move between
    riff, chords and solo. Averaging that into one label produces a part that
    is neither, so a mixed guitar is not claimed as rhythm.
    """
    labels = texture_windows(segment, ppq, window_beats)
    if len(labels) < MIN_WINDOWS_TO_SCAN:
        return False, ""
    chordal = sum(labels)
    minority = min(chordal, len(labels) - chordal)
    share = minority / len(labels)
    return share >= MIXED_SHARE, (
        f"{chordal}/{len(labels)} windows chordal (minority share {share:.0%})"
    )


def classify_role(
    segment: Segment, ppq: int, song_ticks: int, source_name: str, vocal: bool
) -> Role | None:
    """Role for a guitar part, or None for anything that carries no role."""
    if not takes_role(segment):
        return None

    notes = segment.notes
    if not notes:
        return Role(rhythm=False, confidence=0.0, evidence=["empty part"])

    mixed, detail = is_mixed(segment, ppq)
    if mixed and not named_rhythm(source_name):
        return Role(
            rhythm=False,
            confidence=1.0,
            evidence=[f"mixed roles: {detail}"],
            mixed=True,
        )

    texture, evidence = _texture_score(notes, ppq)
    span = coverage(notes, ppq, song_ticks)
    name, name_evidence = _name_score(source_name)

    score = texture + name
    score += max(-0.6, min(0.6, (span - 0.45) * 1.4))
    evidence = evidence + [f"coverage {span:.2f}"] + name_evidence
    if mixed:
        evidence.append(f"mixed roles overridden by name: {detail}")

    return Role(rhythm=score > 0, confidence=min(1.0, abs(score)), evidence=evidence)
