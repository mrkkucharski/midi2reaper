import pytest

from midi2reaper.midiscan import Note
from midi2reaper.ranges import check_range, note_name, typical_range

GUITAR = 28  # Electric Guitar (muted)
TENOR_SAX = 66
SYNTH_LEAD = 80


def _note(pitch: int) -> Note:
    return Note(start=0, end=1, pitch=pitch, velocity=100, channel=0)


@pytest.mark.parametrize(
    "pitch, expected",
    [(0, "C-1"), (21, "A0"), (60, "C4"), (69, "A4"), (108, "C8")],
)
def test_note_name_matches_scientific_pitch_notation(pitch, expected):
    assert note_name(pitch) == expected


def test_guitar_range_matches_pre_widening_data_contract_bound():
    """See PROJECT_LOG.md: DATA_CONTRACT.md used MIDI 38-91 for guitar before
    being widened to the current blanket 21-108."""
    assert typical_range(GUITAR) == (38, 91)


def test_no_warning_within_range():
    notes = [_note(p) for p in (40, 55, 80, 91)]
    assert check_range("distortion-guitar", notes, GUITAR) is None


def test_warns_below_range():
    notes = [_note(p) for p in (31, 33, 35, 36, 40)]  # 4 below 38, 1 at floor
    warning = check_range("electric-guitar-muted:rhythm", notes, GUITAR)
    assert warning is not None
    assert warning.below == 4
    assert warning.above == 0
    assert warning.lowest == 31
    assert "below D2" in warning.detail
    assert "lowest G1" in warning.detail


def test_warns_above_range():
    notes = [_note(p) for p in (60, 91, 93, 96)]  # 2 above 91
    warning = check_range("overdriven-guitar", notes, GUITAR)
    assert warning is not None
    assert warning.below == 0
    assert warning.above == 2
    assert warning.highest == 96
    assert "above G6" in warning.detail
    assert "highest C7" in warning.detail


def test_warns_both_directions_at_once():
    notes = [_note(p) for p in (30, 60, 100)]
    warning = check_range("distortion-guitar", notes, GUITAR)
    assert warning.below == 1
    assert warning.above == 1
    assert "below" in warning.detail and "above" in warning.detail


def test_empty_notes_never_warn():
    assert check_range("drums", [], GUITAR) is None


def test_synth_programs_have_no_meaningful_ceiling():
    """Electronic programs aren't acoustically constrained, so the full
    DATA_CONTRACT 21-108 band applies and nothing realistic ever warns."""
    assert typical_range(SYNTH_LEAD) == (21, 108)
    notes = [_note(p) for p in (21, 60, 108)]
    assert check_range("lead-1-square", notes, SYNTH_LEAD) is None


def test_unknown_program_falls_back_to_full_range():
    assert typical_range(999) == (21, 108)


def test_seven_nation_army_bass_track_is_the_motivating_case():
    """The real regression this check exists for: Jack White's guitar through
    an octave pedal, sounding like a bass, tagged as a guitar program."""
    pitches = [31] * 40 + [33] * 41 + [35] * 52 + [36] * 61 + [40] * 159
    notes = [_note(p) for p in pitches]
    warning = check_range('electric-guitar-muted:rhythm | Jack White | "Bass"', notes, GUITAR)
    assert warning is not None
    assert warning.below == 40 + 41 + 52 + 61  # everything under D2 (38)
    assert warning.above == 0
