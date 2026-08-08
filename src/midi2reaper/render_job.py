"""Versioned, deterministic renderer-job adapter.

This path deliberately bypasses the interactive soundfont matcher and local
chain library.  A job names every canonical part and a manifest pins the
asset used for its profile, so the same JSON inputs always produce the same
project bytes.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import gm
from .midiscan import Note, scan
from .rpp import RenderPart, write_project

RENDER_JOB_SCHEMA = "midi2reaper.render-job/v1"
LIBRARY_MANIFEST_SCHEMA = "midi2reaper.library-manifest/v1"
BUILD_RESULT_SCHEMA = "midi2reaper.build-result/v1"
BUILD_RESULT_VERSION = 1
SUPPORTED_TEMPLATE = "midi2reaper/sflt-v1"


def run(job_path: Path, out_path: Path, result_path: Path) -> dict[str, Any]:
    """Build one deterministic RPP from a versioned renderer job.

    ``renderer_tracks`` is optional and additive to the v1 contract.  When it
    is absent every authoritative symbolic part receives its historical single
    renderer track.  When entries are supplied for a part, they replace that
    default with one or more non-symbolic aliases that share the same notes.
    """
    job = _load_object(job_path)
    _validate_job(job)
    manifest_path = _relative_path(job_path.parent, _string(job, "library_manifest"))
    manifest = _load_object(manifest_path)
    _validate_manifest(manifest)
    profiles = _object(job, "part_profiles")
    assets = _object(manifest, "assets")
    resolved_assets = {
        name: _asset(manifest_path.parent, profile_id, assets)
        for name, profile_id in sorted(profiles.items())
    }

    song = scan(_relative_path(job_path.parent, _string(job, "renderer_midi")))
    authoritative = _authoritative_parts(song, profiles)
    routes = _routes(job, profiles)
    render_parts: list[RenderPart] = []
    built: list[dict[str, Any]] = []
    for name in sorted(profiles):
        notes, sources = authoritative[name]
        profile_id = profiles[name]
        asset = resolved_assets[name]
        for route in routes[name]:
            render_parts.append(
                RenderPart(
                    track_name=name,
                    source_name=" + ".join(sources),
                    notes=notes,
                    soundfont_path=asset["path"],
                    bank=asset["bank"],
                    patch=asset["patch"],
                    is_drum=name == gm.DRUM_SLUG,
                    renderer_track_id=route["renderer_track_id"],
                    authoritative_part=name,
                    pan=route["pan"],
                )
            )
            built.append(
                {
                    "renderer_track_id": route["renderer_track_id"],
                    "renderer_label": route["renderer_track_id"],
                    "authoritative_symbolic_part": name,
                    "profile_id": profile_id,
                    "pan": route["pan"],
                    "note_count": len(notes),
                }
            )

    canonical_job = json.dumps(job, sort_keys=True, separators=(",", ":")).encode()
    write_project(song, render_parts, out_path, deterministic_seed=canonical_job)
    result = {
        "schema_version": BUILD_RESULT_SCHEMA,
        "version": BUILD_RESULT_VERSION,
        "job_id": job["job_id"],
        "template_id": job["template_id"],
        "procgen_commit": job["procgen_commit"],
        "midi2reaper_commit": job["midi2reaper_commit"],
        "project": str(out_path),
        "renderer_midi_sha256": _sha256(_relative_path(job_path.parent, job["renderer_midi"])),
        "built": built,
        "skipped": [],
        "rejected": [],
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def rejection_result(reason: str) -> dict[str, Any]:
    """Machine-readable result used when a job cannot be validated."""
    return {
        "schema_version": BUILD_RESULT_SCHEMA,
        "version": BUILD_RESULT_VERSION,
        "built": [],
        "skipped": [],
        "rejected": [{"reason": reason}],
    }


def _authoritative_parts(song: Any, profiles: dict[str, Any]) -> dict[str, tuple[list[Note], list[str]]]:
    notes: dict[str, list[Note]] = defaultdict(list)
    sources: dict[str, list[str]] = defaultdict(list)
    unknown: list[str] = []
    for track in song.tracks:
        if not track.segments:
            continue
        name = track.name
        if name not in profiles:
            unknown.append(name or f"track {track.index}")
            continue
        for segment in track.segments:
            candidates = {
                gm.track_name(segment.program, segment.is_drum, False),
                gm.track_name(segment.program, segment.is_drum, True),
            }
            if name not in candidates:
                raise ValueError(f"renderer MIDI track {name!r} does not match its GM program")
            notes[name].extend(segment.notes)
        sources[name].append(name)
    if unknown:
        raise ValueError("renderer MIDI contains unassigned part(s): " + ", ".join(sorted(unknown)))
    missing = sorted(set(profiles) - set(notes))
    if missing:
        raise ValueError("renderer MIDI is missing assigned part(s): " + ", ".join(missing))
    return {
        name: (sorted(notes[name], key=lambda note: (note.start, note.end, note.pitch)), sources[name])
        for name in profiles
    }


def _routes(job: dict[str, Any], profiles: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw = job.get("renderer_tracks", [])
    if not isinstance(raw, list):
        raise TypeError("renderer_tracks must be a list")
    routes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ids: set[str] = set()
    for item in raw:
        entry = _as_object(item)
        part = _string(entry, "authoritative_part")
        if part not in profiles:
            raise ValueError(f"renderer track names unknown authoritative part {part!r}")
        track_id = _string(entry, "renderer_track_id")
        if track_id in ids:
            raise ValueError(f"duplicate renderer_track_id {track_id!r}")
        ids.add(track_id)
        pan = entry.get("pan", 0.0)
        if not isinstance(pan, (int, float)) or isinstance(pan, bool) or not -1 <= pan <= 1:
            raise ValueError("renderer track pan must be a number in [-1, 1]")
        routes[part].append({"renderer_track_id": track_id, "pan": float(pan)})
    for part in profiles:
        if not routes[part]:
            routes[part].append({"renderer_track_id": part, "pan": 0.0})
    return routes


def _asset(root: Path, profile_id: Any, assets: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile_id, str) or not profile_id:
        raise TypeError("part_profiles values must be non-empty strings")
    entry = _as_object(assets.get(profile_id))
    if entry.get("kind") != "sflt":
        raise ValueError(f"profile {profile_id!r} is not an SFLT asset")
    path = _relative_path(root, _string(entry, "path"))
    expected = _string(entry, "sha256")
    actual = _sha256(path)
    if actual != expected:
        raise ValueError(f"profile {profile_id!r} asset SHA-256 does not match its manifest")
    bank, patch = entry.get("bank"), entry.get("patch")
    if not isinstance(bank, int) or not isinstance(patch, int):
        raise TypeError(f"profile {profile_id!r} bank and patch must be integers")
    return {"path": path, "bank": bank, "patch": patch}


def _validate_job(job: dict[str, Any]) -> None:
    if job.get("schema_version") != RENDER_JOB_SCHEMA:
        raise ValueError(f"unsupported renderer job schema {job.get('schema_version')!r}")
    for key in ("job_id", "renderer_midi", "template_id", "procgen_commit", "midi2reaper_commit", "library_manifest"):
        _string(job, key)
    if job["template_id"] != SUPPORTED_TEMPLATE:
        raise ValueError(f"unsupported template {job['template_id']!r}")
    profiles = _object(job, "part_profiles")
    if not profiles:
        raise ValueError("part_profiles must not be empty")
    for name, profile in profiles.items():
        if not isinstance(name, str) or not name or not isinstance(profile, str) or not profile:
            raise TypeError("part_profiles must map non-empty names to non-empty profile IDs")


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != LIBRARY_MANIFEST_SCHEMA:
        raise ValueError("unsupported library manifest schema")
    _object(manifest, "assets")


def _load_object(path: Path) -> dict[str, Any]:
    return _as_object(json.loads(path.read_text(encoding="utf-8")))


def _relative_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _object(raw: dict[str, Any], key: str) -> dict[str, Any]:
    return _as_object(raw.get(key))


def _as_object(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TypeError("expected an object")
    return raw


def _string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise TypeError(f"{key} must be a non-empty string")
    return value
