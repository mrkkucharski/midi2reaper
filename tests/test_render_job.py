import hashlib
import json

import mido

from midi2reaper.render_job import BUILD_RESULT_SCHEMA, RENDER_JOB_SCHEMA, run


def _write_midi(path):
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="distortion-guitar:rhythm", time=0))
    track.append(mido.Message("program_change", program=30, channel=0, time=0))
    track.append(mido.Message("note_on", note=52, velocity=100, channel=0, time=0))
    track.append(mido.Message("note_off", note=52, velocity=0, channel=0, time=480))
    midi.save(path)


def _job(tmp_path, routes=None):
    asset = tmp_path / "guitar.sf2"
    asset.write_bytes(b"pinned test asset")
    manifest = {
        "schema_version": "midi2reaper.library-manifest/v1",
        "assets": {
            "test/guitar/v1": {
                "kind": "sflt",
                "path": asset.name,
                "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                "bank": 0,
                "patch": 30,
            }
        },
    }
    (tmp_path / "library.json").write_text(json.dumps(manifest), encoding="utf-8")
    _write_midi(tmp_path / "renderer.mid")
    job = {
        "schema_version": RENDER_JOB_SCHEMA,
        "job_id": "fixture-001",
        "renderer_midi": "renderer.mid",
        "template_id": "midi2reaper/sflt-v1",
        "procgen_commit": "procgen-fixture",
        "midi2reaper_commit": "midi2reaper-fixture",
        "library_manifest": "library.json",
        "part_profiles": {"distortion-guitar:rhythm": "test/guitar/v1"},
    }
    if routes is not None:
        job["renderer_tracks"] = routes
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return path


def test_renderer_job_double_tracks_are_deterministic_and_have_a_sidecar(tmp_path):
    job = _job(
        tmp_path,
        [
            {
                "renderer_track_id": "renderer-alias/rhythm-guitar-left",
                "authoritative_part": "distortion-guitar:rhythm",
                "pan": -0.7,
            },
            {
                "renderer_track_id": "renderer-alias/rhythm-guitar-right",
                "authoritative_part": "distortion-guitar:rhythm",
                "pan": 0.7,
            },
        ],
    )
    first, second = tmp_path / "one.RPP", tmp_path / "two.RPP"
    result = run(job, first, tmp_path / "one-result.json")
    run(job, second, tmp_path / "two-result.json")

    assert first.read_bytes() == second.read_bytes()
    assert result["schema_version"] == BUILD_RESULT_SCHEMA
    assert result["version"] == 1
    assert [entry["authoritative_symbolic_part"] for entry in result["built"]] == [
        "distortion-guitar:rhythm",
        "distortion-guitar:rhythm",
    ]
    text = first.read_text(encoding="utf-8")
    assert "renderer-alias/rhythm-guitar-left" in text
    assert "renderer-alias/rhythm-guitar-right" in text
    assert text.count("<TRACK ") == 2


def test_renderer_job_without_routes_keeps_the_single_track_v1_behavior(tmp_path):
    job = _job(tmp_path)
    result = run(job, tmp_path / "out.RPP", tmp_path / "result.json")

    assert result["built"] == [
        {
            "renderer_track_id": "distortion-guitar:rhythm",
            "renderer_label": "distortion-guitar:rhythm",
            "authoritative_symbolic_part": "distortion-guitar:rhythm",
            "profile_id": "test/guitar/v1",
            "pan": 0.0,
            "note_count": 1,
        }
    ]
