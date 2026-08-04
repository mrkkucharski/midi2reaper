"""Choose a soundfont preset for a part.

Three signals are combined. The strongest is that many single-instrument
soundfonts declare their GM patch number (`Clean Strat.sf2` is bank 0 patch 27,
exactly "Electric Guitar (clean)"), so an exact patch hit is worth more than any
name evidence. Category placement and keyword overlap resolve the rest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import gm
from .sf2 import Library, SoundFont

GM_DRUM_BANK = 128
DEFAULT_MIN_SCORE = 1.0

# Extra vocabulary per program, beyond the tokens in the GM name itself.
_EXTRA_TOKENS: dict[int, set[str]] = {
    24: {"nylon", "classical", "spanish"},
    25: {"steel", "folk", "12", "twelve", "dreadnought", "martin"},
    26: {"jazz", "hollow", "archtop"},
    27: {"clean", "strat", "tele", "fender", "chorus"},
    28: {"mute", "muted", "palm"},
    29: {"overdrive", "drive", "crunch", "rock", "od"},
    30: {"dist", "distortion", "metal", "power", "heavy", "deth", "fuzz"},
    31: {"harmonic"},
    32: {"upright", "double", "acoustic"},
    33: {"finger", "precision", "jazz", "fender"},
    34: {"pick", "picked"},
    35: {"fretless"},
    36: {"slap", "funk"},
    37: {"slap", "funk"},
    38: {"synth", "sub"},
    39: {"synth", "sub"},
}

_FAMILY_NEGATIVES: list[tuple[range, set[str]]] = [
    (range(24, 32), {"bass"}),
    (range(32, 40), {"lead", "solo"}),
    (range(0, 8), {"bass"}),
]

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_STOPWORDS = {"the", "and", "of", "a", "sf2", "sf3", "kb", "mb", "v1", "v2", ""}


@dataclass
class Match:
    soundfont: SoundFont
    bank: int
    patch: int
    preset_name: str
    score: float
    reasons: list[str] = field(default_factory=list)

    @property
    def rel_path(self) -> str:
        return self.soundfont.rel_path


def tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(text.lower()) if t not in _STOPWORDS and len(t) > 1}


def _wanted_tokens(program: int) -> set[str]:
    return tokens(gm.program_name(program)) | _EXTRA_TOKENS.get(program, set())


def _negative_tokens(program: int) -> set[str]:
    negatives: set[str] = set()
    for rng, words in _FAMILY_NEGATIVES:
        if program in rng:
            negatives |= words
    # Anything strongly identifying a *different* guitar tone is a negative.
    if program in gm.GUITAR_PROGRAMS:
        for other, extra in _EXTRA_TOKENS.items():
            if other in gm.GUITAR_PROGRAMS and other != program:
                negatives |= extra
    return negatives - _wanted_tokens(program)


def match_program(
    program: int,
    is_drum: bool,
    source_name: str,
    library: Library,
    min_score: float = DEFAULT_MIN_SCORE,
) -> Match | None:
    """Best preset for the part, or None if nothing clears `min_score`."""
    if is_drum:
        return _match_drums(library, source_name)

    categories = gm.preferred_categories(program)
    wanted = _wanted_tokens(program)
    unwanted = _negative_tokens(program)
    name_tokens = tokens(source_name) - {"guitar", "bass", "lead", "rhythm"}

    best: Match | None = None
    for soundfont in library.soundfonts:
        if soundfont.category not in categories:
            continue
        category_score = 1.0 - 0.3 * categories.index(soundfont.category)
        file_tokens = tokens(soundfont.stem)

        for preset in soundfont.presets:
            score = category_score
            reasons = [f"category {soundfont.category}"]

            if preset.bank == 0 and preset.patch == program:
                score += 1.2
                reasons.append(f"declares GM patch {program}")
            elif preset.bank == 0 and len(soundfont.presets) == 1 and preset.patch == 0:
                score += 0.1  # single-preset bank that simply never set a patch

            candidate_tokens = file_tokens | tokens(preset.name)
            hits = wanted & candidate_tokens
            if hits:
                score += min(1.0, 0.4 * len(hits))
                reasons.append("matches " + "/".join(sorted(hits)))
            misses = unwanted & candidate_tokens
            if misses:
                score -= 0.7 * len(misses)
                reasons.append("conflicts with " + "/".join(sorted(misses)))

            shared = name_tokens & candidate_tokens
            if shared:
                score += min(0.6, 0.3 * len(shared))
                reasons.append("track name shares " + "/".join(sorted(shared)))

            if best is None or score > best.score:
                best = Match(soundfont, preset.bank, preset.patch, preset.name, score, reasons)

    if best is None or best.score < min_score:
        return None
    return best


# A general-purpose acoustic kit suits these arrangements; electronic and
# novelty kits are a poor default and would otherwise win ties on sort order.
_KIT_PREFERRED = {"rock", "standard", "acoustic", "kit", "studio", "live", "natural"}
_KIT_AVOIDED = {"techno", "electronic", "electro", "dance", "house", "tr808", "808", "909",
                "orchestral", "timpani", "latin", "world"}


def _match_drums(library: Library, source_name: str) -> Match | None:
    name_tokens = tokens(source_name) - {"drums", "drum"}
    best: Match | None = None
    for soundfont in library.by_category("drums"):
        file_tokens = tokens(soundfont.stem)
        for preset in soundfont.presets:
            score = 1.0
            reasons = ["category drums"]
            if preset.bank == GM_DRUM_BANK:
                score += 0.8
                reasons.append("GM percussion bank 128")

            candidate_tokens = file_tokens | tokens(preset.name)
            preferred = _KIT_PREFERRED & candidate_tokens
            if preferred:
                score += min(0.6, 0.3 * len(preferred))
                reasons.append("kit style " + "/".join(sorted(preferred)))
            avoided = _KIT_AVOIDED & candidate_tokens
            if avoided:
                score -= 0.8 * len(avoided)
                reasons.append("avoids " + "/".join(sorted(avoided)))

            shared = name_tokens & candidate_tokens
            if shared:
                score += 0.3
                reasons.append("track name shares " + "/".join(sorted(shared)))

            if best is None or score > best.score:
                best = Match(soundfont, preset.bank, preset.patch, preset.name, score, reasons)
    return best
