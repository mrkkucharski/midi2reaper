from pathlib import Path

from midi2reaper.match import match_program
from midi2reaper.sf2 import Library, Preset, SoundFont


def _library(*soundfonts: SoundFont) -> Library:
    return Library(root=Path("/Users/Shared/Soundfonts"), soundfonts=list(soundfonts))


def test_pinned_organ_wins_over_better_scoring_candidate():
    hammond = SoundFont(
        path=Path("/Users/Shared/Soundfonts/organs/137_Hammond_B3_88006000_Slow_Leslie.sf2"),
        category="organs",
        presets=[Preset(bank=0, patch=0, name="B3 Slow Leslie")],
    )
    # A file that would otherwise win on keyword + declared-GM-patch scoring.
    generic = SoundFont(
        path=Path("/Users/Shared/Soundfonts/organs/Drawbar_Organ_Perfect.sf2"),
        category="organs",
        presets=[Preset(bank=0, patch=16, name="Drawbar Organ")],
    )

    library = _library(hammond, generic)
    match = match_program(16, is_drum=False, source_name="drawbar-organ", library=library)

    assert match is not None
    assert match.soundfont is hammond
    assert (match.bank, match.patch) == (0, 0)


def test_pinned_cello_wins_over_better_scoring_candidate():
    pinned = SoundFont(
        path=Path("/Users/Shared/Soundfonts/strings/cello1.SF2"),
        category="strings",
        presets=[Preset(bank=0, patch=1, name="cello")],
    )
    generic = SoundFont(
        path=Path("/Users/Shared/Soundfonts/strings/String sect.sf2"),
        category="strings",
        presets=[Preset(bank=0, patch=42, name="Cello")],
    )

    library = _library(pinned, generic)
    match = match_program(42, is_drum=False, source_name="cello", library=library)

    assert match is not None
    assert match.soundfont is pinned
    assert (match.bank, match.patch) == (0, 1)


def test_unpinned_program_falls_back_to_scoring():
    only = SoundFont(
        path=Path("/Users/Shared/Soundfonts/strings/Violin.sf2"),
        category="strings",
        presets=[Preset(bank=0, patch=40, name="Violin")],
    )
    library = _library(only)
    match = match_program(40, is_drum=False, source_name="violin", library=library)

    assert match is not None
    assert match.soundfont is only
