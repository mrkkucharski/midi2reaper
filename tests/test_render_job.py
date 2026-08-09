import hashlib
import json

import mido

from midi2reaper.render_job import RESULT_SCHEMA, run


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _midi(path):
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Rhythm Guitar", time=0))
    track.append(mido.Message("program_change", program=27, time=0))
    for beat in range(8):
        for index, pitch in enumerate((52, 55, 59)):
            track.append(mido.Message("note_on", note=pitch, velocity=96, time=0 if index else 480))
        for index, pitch in enumerate((52, 55, 59)):
            track.append(mido.Message("note_off", note=pitch, velocity=0, time=0 if index else 240))
    midi.save(path)


def _canonical_lead_midi(path, name="overdriven-guitar"):
    """Chordal notes must not override a canonical non-rhythm lead label."""
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name=name, time=0))
    track.append(mido.Message("program_change", program=29, time=0))
    for beat in range(8):
        for index, pitch in enumerate((52, 55, 59)):
            track.append(mido.Message("note_on", note=pitch, velocity=96, time=0 if index else 480))
        for index, pitch in enumerate((52, 55, 59)):
            track.append(mido.Message("note_off", note=pitch, velocity=0, time=0 if index else 240))
    midi.save(path)


def test_render_job_is_byte_deterministic_and_reports_schema(tmp_path):
    midi = tmp_path / "renderer.mid"
    font = tmp_path / "clean.sf2"
    _midi(midi)
    font.write_bytes(b"pinned test soundfont")
    library = tmp_path / "library.json"
    library.write_text(json.dumps({
        "schema_version": "midi2reaper.library-manifest/v1",
        "profiles": {"guitar": {"kind": "sflt", "soundfont": {"path": "clean.sf2", "sha256": _sha(font)}, "bank": 0, "patch": 27}},
    }))
    job = tmp_path / "render_job.json"
    job.write_text(json.dumps({
        "schema_version": "midi2reaper.render-job/v1",
        "job_id": "test-job",
        "procgen_commit": "0123456789abcdef",
        "renderer_midi": "renderer.mid",
        "template_id": "midi2reaper/sflt-v1",
        "library_manifest": "library.json",
        "part_profiles": {"electric-guitar-clean:rhythm": "guitar"},
        "renderer_tracks": [
            {"renderer_track_id": "renderer-alias/guitar-left", "authoritative_part": "electric-guitar-clean:rhythm", "pan": -0.7},
            {"renderer_track_id": "renderer-alias/guitar-right", "authoritative_part": "electric-guitar-clean:rhythm", "pan": 0.7},
        ],
    }))

    first, second = tmp_path / "one.RPP", tmp_path / "two.RPP"
    assert run(job, first, tmp_path / "one-result.json") == 0
    assert run(job, second, tmp_path / "two-result.json") == 0
    assert first.read_bytes() == second.read_bytes()
    result = json.loads((tmp_path / "one-result.json").read_text())
    assert result["schema_version"] == RESULT_SCHEMA
    assert result["version"] == 1
    assert result["status"] == "built"
    assert result["built"][0]["sha256"] == _sha(first)
    assert result["built"][0]["renderer_track_aliases"] == [
        {"renderer_track_id": "renderer-alias/guitar-left", "authoritative_symbolic_part": "electric-guitar-clean:rhythm", "pan": -0.7},
        {"renderer_track_id": "renderer-alias/guitar-right", "authoritative_symbolic_part": "electric-guitar-clean:rhythm", "pan": 0.7},
    ]
    assert first.read_text(encoding="utf-8").count("<TRACK ") == 2

    legacy = json.loads(job.read_text())
    legacy.pop("renderer_tracks")
    legacy_job = tmp_path / "legacy-render-job.json"
    legacy_job.write_text(json.dumps(legacy))
    legacy_project = tmp_path / "legacy.RPP"
    assert run(legacy_job, legacy_project, tmp_path / "legacy-result.json") == 0
    assert legacy_project.read_text(encoding="utf-8").count("<TRACK ") == 1


def test_render_job_rejects_asset_checksum_mismatch(tmp_path):
    font = tmp_path / "font.sf2"
    font.write_bytes(b"changed")
    library = tmp_path / "library.json"
    library.write_text(json.dumps({
        "schema_version": "midi2reaper.library-manifest/v1",
        "profiles": {"p": {"kind": "sflt", "soundfont": {"path": "font.sf2", "sha256": "0" * 64}, "bank": 0, "patch": 0}},
    }))
    job = tmp_path / "job.json"
    job.write_text(json.dumps({
        "schema_version": "midi2reaper.render-job/v1", "job_id": "x", "procgen_commit": "test", "renderer_midi": "missing.mid",
        "template_id": "midi2reaper/sflt-v1", "library_manifest": "library.json", "part_profiles": {"x": "p"},
    }))
    try:
        run(job, tmp_path / "x.RPP", tmp_path / "result.json")
    except ValueError as error:
        assert "checksum mismatch" in str(error)
    else:
        raise AssertionError("expected checksum rejection")


def test_render_job_preserves_canonical_non_rhythm_guitar_identity(tmp_path):
    midi = tmp_path / "renderer.mid"
    font = tmp_path / "lead.sf2"
    _canonical_lead_midi(midi)
    font.write_bytes(b"pinned test soundfont")
    library = tmp_path / "library.json"
    library.write_text(json.dumps({
        "schema_version": "midi2reaper.library-manifest/v1",
        "profiles": {"lead": {"kind": "sflt", "soundfont": {"path": "lead.sf2", "sha256": _sha(font)}, "bank": 0, "patch": 29}},
    }))
    job = tmp_path / "render-job.json"
    job.write_text(json.dumps({
        "schema_version": "midi2reaper.render-job/v1",
        "job_id": "canonical-lead",
        "procgen_commit": "test",
        "renderer_midi": "renderer.mid",
        "template_id": "midi2reaper/sflt-v1",
        "library_manifest": "library.json",
        "part_profiles": {"overdriven-guitar": "lead"},
    }))

    project = tmp_path / "lead.RPP"
    assert run(job, project, tmp_path / "result.json") == 0
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["built"][0]["parts"][0]["track_name"] == "overdriven-guitar"
    assert "overdriven-guitar:rhythm" not in project.read_text(encoding="utf-8")


def test_render_job_rejects_canonical_name_that_conflicts_with_midi_identity(tmp_path):
    midi = tmp_path / "renderer.mid"
    font = tmp_path / "lead.sf2"
    _canonical_lead_midi(midi, name="distortion-guitar:rhythm")
    font.write_bytes(b"pinned test soundfont")
    library = tmp_path / "library.json"
    library.write_text(json.dumps({
        "schema_version": "midi2reaper.library-manifest/v1",
        "profiles": {"lead": {"kind": "sflt", "soundfont": {"path": "lead.sf2", "sha256": _sha(font)}, "bank": 0, "patch": 29}},
    }))
    job = tmp_path / "render-job.json"
    job.write_text(json.dumps({
        "schema_version": "midi2reaper.render-job/v1",
        "job_id": "canonical-conflict",
        "procgen_commit": "test",
        "renderer_midi": "renderer.mid",
        "template_id": "midi2reaper/sflt-v1",
        "library_manifest": "library.json",
        "part_profiles": {"distortion-guitar:rhythm": "lead"},
    }))

    assert run(job, tmp_path / "conflict.RPP", tmp_path / "result.json") == 1
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["rejected"] == [{
        "reason": "source track 'distortion-guitar:rhythm' identity does not match its MIDI program/channel"
    }]
