import base64
import json
import struct
from pathlib import Path

import pytest

from midi2reaper import gm
from midi2reaper.classify import chord_ratio, classify_role, coverage, is_vocal, onset_polyphony
from midi2reaper.midiscan import Note, Segment
from midi2reaper.midiscan import Song, TempoMap
from midi2reaper.rpp import (
    B64_LINE_WIDTH,
    MASTER_VOLUME_GAIN,
    RENDER_CFG_B64,
    RENDER_FMT_LINE,
    midi_events,
    sflt_chunk,
    write_project,
)


def split_blocks(lines: list[str]) -> list[bytes]:
    """Reaper delimits base64 blocks with a line shorter than 128 characters."""
    blocks, current = [], []
    for line in lines:
        current.append(line)
        if len(line) < B64_LINE_WIDTH:
            blocks.append(base64.b64decode("".join(current)))
            current = []
    assert not current, "trailing data with no short line to close the final block"
    return blocks


def decode_state(lines: list[str]) -> dict:
    blocks = split_blocks(lines)
    assert len(blocks) == 3
    declared = struct.unpack("<I", blocks[0][-12:-8])[0]
    assert declared == len(blocks[1]), "block0 must declare block1's length"
    json_length = struct.unpack("<I", blocks[1][:4])[0]
    return json.loads(blocks[1][8 : 8 + json_length].decode())


def test_chunk_round_trips_path_bank_and_patch():
    path = Path("/Users/Shared/Soundfonts/guitars/Clean Strat.sf2")
    state = decode_state(sflt_chunk(path, bank=0, patch=27))

    assert json.loads(state["fields"]["file"]) == str(path)
    assert state["fields"]["patch"] == "27"
    assert state["params"]["patch"]["f32"] == 27.0


@pytest.mark.parametrize("length", range(1, 160))
def test_no_block_ends_on_a_line_boundary(length):
    """A block whose base64 length is a multiple of 128 would silently merge
    into the next block, so the JSON is padded until that cannot happen."""
    path = Path("/Users/Shared/Soundfonts/guitars/" + "x" * length + ".sf2")
    for block in split_blocks(sflt_chunk(path, 0, 0)):
        assert len(base64.b64encode(block)) % B64_LINE_WIDTH != 0


def _empty_song(tmp_path):
    ppq = 480
    return Song(
        path=tmp_path / "song.mid",
        ppq=ppq,
        tempo_map=TempoMap(ppq=ppq, changes=[]),
        time_signature=(4, 4),
        tracks=[],
        length_seconds=1.0,
    )


def test_project_defaults_match_tuned_render_settings(tmp_path):
    """Render was tuned by ear to mono/16-bit and master volume to -10 dB, and
    that setting applied to every project by hand; new projects must not need
    the same manual edit again."""
    out = tmp_path / "out.RPP"
    write_project(_empty_song(tmp_path), [], out)
    text = out.read_text(encoding="utf-8")

    assert f"  MASTER_VOLUME {MASTER_VOLUME_GAIN} 0 -1 -1 1" in text
    assert RENDER_FMT_LINE in text
    assert f"    {RENDER_CFG_B64}" in text


def test_render_cfg_default_does_not_touch_record_cfg(tmp_path):
    """RECORD_CFG governs live recording, not rendering, and was never part of
    what was tuned; only the RENDER_CFG blob should change."""
    out = tmp_path / "out.RPP"
    write_project(_empty_song(tmp_path), [], out)
    text = out.read_text(encoding="utf-8")

    record_line = next(l for l in text.split("\n") if l.strip().startswith("<RECORD_CFG"))
    record_index = text.split("\n").index(record_line)
    record_blob = text.split("\n")[record_index + 1].strip()
    assert record_blob == "ZXZhdxgAAQ=="
    assert record_blob != RENDER_CFG_B64


def test_midi_events_use_deltas_and_close_notes():
    notes = [Note(start=0, end=480, pitch=60, velocity=100, channel=0),
             Note(start=480, end=960, pitch=62, velocity=90, channel=0)]
    lines = midi_events(notes, is_drum=False)

    assert lines[0] == "E 0 90 3c 64"
    assert lines[1] == "E 480 80 3c 00"
    assert lines[2] == "E 0 90 3e 5a"
    assert lines[-1].endswith("b0 7b 00")


def test_midi_events_omit_program_changes():
    notes = [Note(start=0, end=10, pitch=60, velocity=100, channel=0)]
    assert not any(" c0 " in line for line in midi_events(notes, is_drum=False))


def test_drums_use_channel_ten():
    notes = [Note(start=0, end=10, pitch=36, velocity=100, channel=9)]
    assert midi_events(notes, is_drum=True)[0] == "E 0 99 24 64"


def test_track_name_matches_contract():
    assert gm.track_name(30, False, True) == "distortion-guitar:rhythm"
    assert gm.track_name(66, False, None) == "tenor-sax"
    assert gm.track_name(None, True, None) == "drums"


def test_only_rhythm_is_ever_annotated():
    """There is no `lead`: absence of `:rhythm` says the part is not chordal
    accompaniment, which is all that can be claimed."""
    assert gm.track_name(30, False, False) == "distortion-guitar"
    assert gm.track_name(30, False, None) == "distortion-guitar"
    assert ":lead" not in gm.track_name(27, False, False)


@pytest.mark.parametrize("name", ["Lead Vocals", "Voc", "Kurt Cobain | Vocals", "backing vox"])
def test_vocal_names_detected(name):
    assert is_vocal(name)


@pytest.mark.parametrize("name", ["Lead Guitar", "Bass", "Hammond C-3 | Organ"])
def test_non_vocal_names_rejected(name):
    assert not is_vocal(name)


def test_chord_ratio_counts_simultaneous_onsets():
    chord = [Note(0, 100, p, 100, 0) for p in (60, 64, 67)]
    assert chord_ratio(chord, window=30) == 1.0
    melody = [Note(i * 200, i * 200 + 100, 60, 100, 0) for i in range(4)]
    assert chord_ratio(melody, window=30) == 0.0


def test_sustain_does_not_count_as_polyphony():
    """A fingerpicked line whose notes ring into each other is played one note
    at a time. Measuring sustain overlap instead of onsets read a purely
    sequential guitar part as 5.7-voice polyphony and pushed it to 'rhythm'."""
    ppq = 480
    ringing = [Note(i * ppq, i * ppq + ppq * 6, 60 + i, 100, 0) for i in range(8)]
    assert onset_polyphony(ringing, ppq // 16) == 1.0
    assert chord_ratio(ringing, ppq // 16) == 0.0


def test_onset_polyphony_counts_struck_chords():
    ppq = 480
    strummed = []
    for bar in range(4):
        for offset, pitch in enumerate((52, 55, 59)):
            # a strum rolls across the strings a few milliseconds apart
            strummed.append(Note(bar * ppq + offset * 4, bar * ppq + ppq * 4, pitch, 100, 0))
    assert onset_polyphony(strummed, ppq // 16) == pytest.approx(3.0)


def test_sustained_arpeggio_is_lead_not_rhythm():
    """Regression: sustained single-note lines were classified as accompaniment."""
    ppq = 480
    notes = [Note(i * ppq // 2, i * ppq // 2 + ppq * 8, 60 + (i % 5), 100, 0) for i in range(60)]
    segment = Segment(program=27, is_drum=False, notes=notes)

    role = classify_role(segment, ppq, song_ticks=200 * ppq, source_name="Guitar", vocal=False)
    assert not role.rhythm


@pytest.mark.parametrize(
    "program,is_drum",
    [(33, False), (0, False), (18, False), (66, False), (48, False), (0, True)],
)
def test_only_guitars_take_a_role(program, is_drum):
    """Role is a guitar-only annotation: bass, drums, keys and the rest carry a
    program and nothing else."""
    ppq = 480
    notes = [Note(i * ppq, i * ppq + ppq, 40, 100, 0) for i in range(200)]
    segment = Segment(program=program, is_drum=is_drum, notes=notes)

    assert classify_role(segment, ppq, 200 * ppq, "Bass", vocal=False) is None


@pytest.mark.parametrize("program", range(24, 32))
def test_guitars_do_take_a_role(program):
    ppq = 480
    notes = [Note(i * ppq, i * ppq + ppq, 40, 100, 0) for i in range(200)]
    segment = Segment(program=program, is_drum=False, notes=notes)

    assert classify_role(segment, ppq, 200 * ppq, "Guitar", vocal=False) is not None


def _mixed_guitar(ppq):
    """Alternating two-bar blocks of strummed chords and single-note line."""
    notes = []
    for block in range(8):
        base = block * ppq * 8
        if block % 2:
            for beat in range(8):  # chords
                for pitch in (52, 55, 59):
                    notes.append(Note(base + beat * ppq, base + beat * ppq + ppq, pitch, 100, 0))
        else:
            for beat in range(8):  # single line
                notes.append(Note(base + beat * ppq, base + beat * ppq + ppq, 64 + beat, 100, 0))
    return Segment(program=27, is_drum=False, notes=notes)


def test_mixed_guitar_is_not_marked_rhythm():
    """A guitar that moves between riff, chords and solo is not accompaniment;
    averaging it into one label produces a part that is neither."""
    ppq = 480
    role = classify_role(_mixed_guitar(ppq), ppq, 64 * ppq, "Guitar", vocal=False)
    assert role.mixed
    assert not role.rhythm


def test_mixed_guitar_named_rhythm_stays_rhythm():
    """The source MIDI naming it rhythm outranks the mixed-usage finding."""
    ppq = 480
    role = classify_role(_mixed_guitar(ppq), ppq, 64 * ppq, "Rhythm Guitar", vocal=False)
    assert role.rhythm


def test_uniformly_chordal_guitar_is_rhythm():
    ppq = 480
    notes = []
    for beat in range(200):
        for pitch in (52, 55, 59):
            notes.append(Note(beat * ppq, beat * ppq + ppq, pitch, 100, 0))
    segment = Segment(program=27, is_drum=False, notes=notes)

    role = classify_role(segment, ppq, 200 * ppq, "Guitar", vocal=False)
    assert role.rhythm
    assert not role.mixed


def test_sparse_solo_is_lead():
    ppq = 480
    notes = [Note(i * ppq, i * ppq + ppq // 2, 76, 100, 0) for i in range(12)]
    segment = Segment(program=30, is_drum=False, notes=notes)

    role = classify_role(segment, ppq, song_ticks=400 * ppq, source_name="Guitar", vocal=False)
    assert not role.rhythm


def test_track_name_omits_role_for_non_guitars():
    """No colon is how a name says the part carries no role."""
    assert gm.track_name(33, False, None) == "electric-bass-finger"
    assert gm.track_name(None, True, None) == "drums"
    assert gm.track_name(0, False, None) == "acoustic-grand-piano"


def test_coverage_separates_continuous_from_bursty():
    ppq = 480
    continuous = [Note(i * ppq, i * ppq + ppq, 40, 100, 0) for i in range(100)]
    bursty = [Note(i * ppq, i * ppq + ppq, 40, 100, 0) for i in range(10)]

    assert coverage(continuous, ppq, 100 * ppq) > 0.9
    assert coverage(bursty, ppq, 100 * ppq) < 0.2
