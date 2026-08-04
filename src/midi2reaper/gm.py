"""General MIDI program tables and the mapping from programs to library folders."""

from __future__ import annotations

import re

PROGRAM_NAMES = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
    "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2", "Harpsichord",
    "Clavi", "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba",
    "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ",
    "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ",
    "Accordion", "Harmonica", "Tango Accordion", "Acoustic Guitar (nylon)",
    "Acoustic Guitar (steel)", "Electric Guitar (jazz)",
    "Electric Guitar (clean)", "Electric Guitar (muted)", "Overdriven Guitar",
    "Distortion Guitar", "Guitar Harmonics", "Acoustic Bass",
    "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2", "Violin",
    "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings",
    "Orchestral Harp", "Timpani", "String Ensemble 1", "String Ensemble 2",
    "Synth Strings 1", "Synth Strings 2", "Choir Aahs", "Voice Oohs",
    "Synth Voice", "Orchestra Hit", "Trumpet", "Trombone", "Tuba",
    "Muted Trumpet", "French Horn", "Brass Section", "Synth Brass 1",
    "Synth Brass 2", "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet", "Piccolo", "Flute",
    "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle",
    "Ocarina", "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)",
    "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)",
    "Lead 8 (bass + lead)", "Pad 1 (new age)", "Pad 2 (warm)",
    "Pad 3 (polysynth)", "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)",
    "Pad 7 (halo)", "Pad 8 (sweep)", "FX 1 (rain)", "FX 2 (soundtrack)",
    "FX 3 (crystal)", "FX 4 (atmosphere)", "FX 5 (brightness)",
    "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)", "Sitar", "Banjo",
    "Shamisen", "Koto", "Kalimba", "Bag pipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum",
    "Melodic Tom", "Synth Drum", "Reverse Cymbal", "Guitar Fret Noise",
    "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter",
    "Applause", "Gunshot",
]

DRUM_SLUG = "drums"

# Guitar programs anchor corpus membership: an example without one is rejected.
GUITAR_PROGRAMS = range(24, 32)
BASS_PROGRAMS = range(32, 40)

# Ordered category preferences per program range. The first category that
# yields a candidate wins the category bonus; later ones score lower.
_CATEGORY_RANGES: list[tuple[range, tuple[str, ...]]] = [
    (range(0, 8), ("pianos",)),
    (range(8, 16), ("pianos", "orchestral", "other")),
    (range(16, 24), ("organs",)),
    (range(24, 32), ("guitars",)),
    # The library keeps every bass soundfont in guitars/.
    (range(32, 40), ("guitars",)),
    (range(40, 48), ("strings", "orchestral", "st-james-orchestra")),
    (range(48, 52), ("strings", "orchestral", "st-james-orchestra")),
    (range(52, 55), ("choirs",)),
    (range(55, 56), ("orchestral", "brass")),
    (range(56, 64), ("brass", "orchestral", "st-james-orchestra")),
    (range(64, 68), ("brass",)),
    (range(68, 72), ("flutes", "orchestral", "brass")),
    (range(72, 80), ("flutes", "orchestral")),
    (range(80, 96), ("synths", "thor")),
    (range(96, 104), ("fx", "synths")),
    (range(104, 112), ("other", "orchestral", "strings")),
    (range(112, 120), ("drums", "other")),
    (range(120, 128), ("fx", "other")),
]


def program_name(program: int) -> str:
    return PROGRAM_NAMES[program]


def slug(text: str) -> str:
    """Canonical kebab-case slug used in part track names."""
    text = text.lower().replace("+", " plus ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def program_slug(program: int) -> str:
    return slug(PROGRAM_NAMES[program])


def preferred_categories(program: int) -> tuple[str, ...]:
    for rng, cats in _CATEGORY_RANGES:
        if program in rng:
            return cats
    return ()


def is_guitar(program: int) -> bool:
    return program in GUITAR_PROGRAMS


def is_bass(program: int) -> bool:
    return program in BASS_PROGRAMS


def track_name(program: int | None, is_drum: bool, rhythm: bool | None) -> str:
    """Canonical part name required by DATA_CONTRACT.md.

    `rhythm` is the only annotation there is. A guitar playing chordal
    accompaniment throughout is named `<slug>:rhythm`; every other part —
    guitars that lead, guitars that mix roles, and everything that is not a
    guitar — is named `<slug>` alone.

    There is deliberately no `lead`. Marking it would need a third label state
    for a minority of parts, while the guitars between the two roles have no
    natural boundary to cut at; the absence of `:rhythm` says only that the part
    is not chordal accompaniment, which is exactly as much as can be claimed.

    This name is the interface to `reaper2mt3`, which parses it back out of the
    REAPER project as the training label. Changing the grammar here without
    changing it there mis-labels a corpus silently.
    """
    part = DRUM_SLUG if is_drum else program_slug(program)
    return f"{part}:rhythm" if rhythm else part
