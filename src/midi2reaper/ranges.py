"""Typical playable register per General MIDI program.

This is not `DATA_CONTRACT.md`'s hard MIDI 21-108 boundary -- `reaper2mt3`
enforces that downstream, and it is a rejection. This is narrower and softer:
where a program's *acoustic* instrument ordinarily sits, so `build` can warn
when a part strays outside it. Real performances legitimately do this -- a
down-tuned or extended-range guitar, an octave pedal turning a guitar into a
"bass" (see `PROJECT_LOG.md`, "Seven Nation Army"'s `"Bass"` track), a solo
pushed above written range for effect -- so this exists to surface the case
worth listening to, not to reject anything.

Guitar's 38-91 (D2-G6) matches the range `DATA_CONTRACT.md` itself used before
being widened to today's blanket 21-108 (see `PROJECT_LOG.md`, "Widened corpus
from guitar-only to multi-instrument"). Every other family below is sourced
from standard orchestration range references and is deliberately generous
rather than precise -- a warning that fires too rarely is better than one that
nags on every legitimate low or high note in a family this table only
approximates.
"""

from __future__ import annotations

from dataclasses import dataclass

from .midiscan import Note

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(pitch: int) -> str:
    """Scientific pitch notation, matching DATA_CONTRACT.md's own convention
    (MIDI 21 = A0, MIDI 108 = C8)."""
    return f"{_NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


# No meaningful acoustic constraint beyond DATA_CONTRACT's own blanket bound:
# synths, FX, and unclassified programs.
_FULL = (21, 108)

_RANGES: dict[int, tuple[int, int]] = {}


def _set(programs, low: int, high: int) -> None:
    for p in programs:
        _RANGES[p] = (low, high)


# Piano family (0-7): full keyboard, no tighter constraint is meaningful.
_set(range(0, 8), *_FULL)

# Chromatic percussion (8-15).
_set([8], 60, 108)  # Celesta
_set([9], 77, 108)  # Glockenspiel
_set([10], 60, 96)  # Music Box
_set([11], 53, 89)  # Vibraphone
_set([12], 36, 96)  # Marimba
_set([13], 65, 108)  # Xylophone
_set([14], 60, 77)  # Tubular Bells
_set([15], 48, 96)  # Dulcimer

# Organ (16-23): manuals + pedal, generously bounded.
_set(range(16, 24), 24, 96)

# Guitar (24-31): D2-G6, matching this project's own pre-widening
# DATA_CONTRACT.md bound -- see module docstring.
_set(range(24, 32), 38, 91)

# Bass (32-39): standard 4-string range. A 5-string low B or a tapped
# harmonic will warn -- that is real and worth a look, not a bug.
_set(range(32, 40), 28, 67)

# Strings, solo and ensemble (40-51).
_set([40], 55, 100)  # Violin
_set([41], 48, 88)  # Viola
_set([42], 36, 84)  # Cello
_set([43], 28, 72)  # Contrabass
_set([44], 28, 96)  # Tremolo Strings
_set([45], 28, 96)  # Pizzicato Strings
_set([46], 24, 103)  # Orchestral Harp
_set([47], 38, 60)  # Timpani
_set([48, 49], 28, 96)  # String Ensemble 1/2
_set([50, 51], *_FULL)  # Synth Strings 1/2

# Choir/voice/hit (52-55).
_set([52, 53], 40, 84)  # Choir Aahs, Voice Oohs
_set([54], *_FULL)  # Synth Voice
_set([55], *_FULL)  # Orchestra Hit

# Brass (56-63).
_set([56], 52, 84)  # Trumpet
_set([57], 40, 77)  # Trombone
_set([58], 26, 65)  # Tuba
_set([59], 52, 84)  # Muted Trumpet
_set([60], 35, 77)  # French Horn
_set([61], 36, 96)  # Brass Section
_set([62, 63], *_FULL)  # Synth Brass 1/2

# Reed (64-71).
_set([64], 56, 88)  # Soprano Sax
_set([65], 49, 81)  # Alto Sax
_set([66], 44, 76)  # Tenor Sax
_set([67], 37, 69)  # Baritone Sax
_set([68], 58, 93)  # Oboe
_set([69], 47, 76)  # English Horn
_set([70], 34, 75)  # Bassoon
_set([71], 50, 94)  # Clarinet

# Pipe (72-79).
_set([72], 62, 96)  # Piccolo
_set([73], 60, 98)  # Flute
_set([74], 65, 91)  # Recorder
_set([75], 60, 96)  # Pan Flute
_set([76], 48, 96)  # Blown Bottle
_set([77], 55, 91)  # Shakuhachi
_set([78], 60, 96)  # Whistle
_set([79], 60, 91)  # Ocarina

# Synth lead/pad/FX (80-103): electronic, no acoustic constraint.
_set(range(80, 104), *_FULL)

# Ethnic (104-111).
_set([104], 40, 96)  # Sitar
_set([105], 50, 81)  # Banjo
_set([106], 48, 84)  # Shamisen
_set([107], 43, 88)  # Koto
_set([108], 48, 84)  # Kalimba
_set([109], 55, 79)  # Bag pipe
_set([110], 55, 100)  # Fiddle
_set([111], 55, 84)  # Shanai

# Percussive/sound effects (112-127): pitched percussion and FX programs, no
# meaningful acoustic constraint.
_set(range(112, 128), *_FULL)


def typical_range(program: int) -> tuple[int, int]:
    return _RANGES.get(program, _FULL)


@dataclass
class RangeWarning:
    track_name: str
    low: int
    high: int
    below: int
    above: int
    lowest: int
    highest: int

    @property
    def detail(self) -> str:
        bits = []
        if self.below:
            bits.append(
                f"{self.below} note(s) below {note_name(self.low)} "
                f"(lowest {note_name(self.lowest)})"
            )
        if self.above:
            bits.append(
                f"{self.above} note(s) above {note_name(self.high)} "
                f"(highest {note_name(self.highest)})"
            )
        return "; ".join(bits)

    def to_dict(self) -> dict:
        return {
            "low": self.low,
            "high": self.high,
            "below": self.below,
            "above": self.above,
            "lowest": self.lowest,
            "highest": self.highest,
        }


def check_range(track_name: str, notes: list[Note], program: int) -> RangeWarning | None:
    """Warn when a part's notes stray outside its program's typical register.

    Not a rejection: DATA_CONTRACT.md's own MIDI 21-108 boundary is the hard
    limit, enforced downstream by `reaper2mt3`. This surfaces the case worth
    listening to -- an octave pedal, a down-tuned guitar, a solo pushed above
    written range -- while leaving the judgment to you.
    """
    if not notes:
        return None
    low, high = typical_range(program)
    pitches = [n.pitch for n in notes]
    below = sum(1 for p in pitches if p < low)
    above = sum(1 for p in pitches if p > high)
    if not below and not above:
        return None
    return RangeWarning(
        track_name=track_name,
        low=low,
        high=high,
        below=below,
        above=above,
        lowest=min(pitches),
        highest=max(pitches),
    )
