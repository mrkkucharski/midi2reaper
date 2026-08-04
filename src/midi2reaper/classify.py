"""Identify vocal tracks and decide the rhythm/lead role of a part.

DATA_CONTRACT.md requires role to be inferred from musical content, with source
track names treated as corroborating evidence rather than authority: names like
"Rhythm Guitar" are frequently absent, and vocal tracks routinely carry
unrelated programs, so vocals are detected by name and role by content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import gm
from .midiscan import Note, Segment

VOCAL_PATTERN = re.compile(r"\b(vocals?|voc|vox|voice|singer|singing)\b", re.IGNORECASE)

_RHYTHM_WORDS = re.compile(r"\b(rhythm|rhythmic|comp|chords?|backing|riff|pad)\b", re.IGNORECASE)
_LEAD_WORDS = re.compile(r"\b(lead|solo|melody|hook|fill)\b", re.IGNORECASE)


@dataclass
class Role:
    rhythm: bool
    confidence: float
    evidence: list[str]

    @property
    def note(self) -> str | None:
        """Populates the manifest's role_annotation_note when the call was close."""
        if self.confidence >= 0.4:
            return None
        return "low-confidence role: " + "; ".join(self.evidence)


def is_vocal(name: str) -> bool:
    return bool(VOCAL_PATTERN.search(name))


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


def mean_polyphony(notes: list[Note]) -> float:
    """Average number of simultaneously sounding notes over the part's span."""
    if not notes:
        return 0.0
    span = max(n.end for n in notes) - min(n.start for n in notes)
    if span <= 0:
        return float(len(notes))
    return sum(n.end - n.start for n in notes) / span


def coverage(notes: list[Note], ppq: int, song_ticks: int) -> float:
    """Fraction of the song's beats in which this part sounds.

    This is the primary discriminator. Chords imply accompaniment, but their
    absence implies nothing: bass lines, riffs and arpeggiated backing are all
    monophonic accompaniment. What separates accompaniment from a lead is that
    accompaniment runs through the piece while a lead appears in bursts.
    """
    if not notes or song_ticks <= 0:
        return 0.0
    beats = set()
    for note in notes:
        for beat in range(note.start // ppq, note.end // ppq + 1):
            beats.add(beat)
    return min(1.0, len(beats) / max(1, song_ticks // ppq + 1))


def classify_role(
    segment: Segment, ppq: int, song_ticks: int, source_name: str, vocal: bool
) -> Role:
    """Score a part as rhythm (positive) or lead (negative)."""
    notes = segment.notes
    if not notes:
        return Role(rhythm=True, confidence=0.0, evidence=["empty part"])

    # Percussion is accompaniment by construction; keeping it on one side also
    # stops sparse auxiliary kit tracks splitting off into a phantom lead part.
    if segment.is_drum:
        return Role(rhythm=True, confidence=1.0, evidence=["drum part"])

    window = max(1, ppq // 16)
    ratio = chord_ratio(notes, window)
    polyphony = mean_polyphony(notes)
    span = coverage(notes, ppq, song_ticks)

    score = 0.0
    evidence: list[str] = []

    score += max(-0.8, min(0.8, (span - 0.45) * 1.8))
    evidence.append(f"coverage {span:.2f}")

    # Chords and polyphony only ever argue *for* accompaniment.
    score += max(0.0, min(0.7, (ratio - 0.15) * 1.8))
    evidence.append(f"chord ratio {ratio:.2f}")

    score += max(0.0, min(0.5, (polyphony - 1.2) * 0.7))
    evidence.append(f"mean polyphony {polyphony:.2f}")

    if vocal:
        score -= 0.6
        evidence.append("vocal part")
    elif gm.is_bass(segment.program):
        score += 0.5
        evidence.append("bass program")
    elif 48 <= segment.program <= 51 or 88 <= segment.program <= 95:
        score += 0.25
        evidence.append("sustained ensemble/pad program")
    elif 80 <= segment.program <= 87:
        score -= 0.3
        evidence.append("lead synth program")

    if _RHYTHM_WORDS.search(source_name):
        score += 0.5
        evidence.append("name suggests rhythm")
    if _LEAD_WORDS.search(source_name):
        score -= 0.5
        evidence.append("name suggests lead")

    return Role(rhythm=score > 0, confidence=min(1.0, abs(score)), evidence=evidence)
