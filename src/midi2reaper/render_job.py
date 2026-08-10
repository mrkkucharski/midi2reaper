"""Versioned, reproducible renderer-job adapter.

This module deliberately delegates MIDI scanning and RPP serialization to the
normal midi2reaper pipeline.  It only supplies a hermetic job boundary around
those established operations.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from . import gm
from .pipeline import build
from .sf2 import Library, Preset, SoundFont

JOB_SCHEMA = "midi2reaper.render-job/v1"
LIBRARY_SCHEMA = "midi2reaper.library-manifest/v1"
RESULT_SCHEMA = "midi2reaper.build-result/v1"
SFLT_TEMPLATE = "midi2reaper/sflt-v1"


class JobError(ValueError):
    """A job or its pinned asset manifest is invalid."""


@dataclass(frozen=True)
class Profile:
    kind: str
    soundfont: Path | None = None
    bank: int | None = None
    patch: int | None = None
    chain: Path | None = None


def _renderer_tracks(job: dict, assignments: dict[str, str], path: Path) -> dict[str, list[dict]]:
    """Validate optional v1 fan-out entries, grouped by symbolic part."""
    raw = job.get("renderer_tracks")
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise JobError(f"{path}: renderer_tracks must be a list")
    grouped: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise JobError(f"{path}: renderer track entries must be objects")
        part = _required_string(entry, "authoritative_part", path)
        track_id = _required_string(entry, "renderer_track_id", path)
        pan = entry.get("pan", 0.0)
        if part not in assignments:
            raise JobError(f"{path}: renderer track references unknown part {part!r}")
        if track_id in assignments or track_id in seen:
            raise JobError(f"{path}: renderer_track_id must be a unique non-symbolic alias")
        if not isinstance(pan, (int, float)) or isinstance(pan, bool) or not -1 <= pan <= 1:
            raise JobError(f"{path}: renderer track pan must be a number in [-1, 1]")
        seen.add(track_id)
        grouped.setdefault(part, []).append({"renderer_track_id": track_id, "pan": float(pan)})
    return grouped


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise JobError(f"cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise JobError(f"{path} must contain a JSON object")
    return value


def _required_string(value: dict, key: str, source: Path) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise JobError(f"{source}: {key} must be a non-empty string")
    return item


def _verified_path(entry: dict, manifest: Path, label: str) -> Path:
    raw = _required_string(entry, "path", manifest)
    expected = _required_string(entry, "sha256", manifest).lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise JobError(f"{manifest}: {label}.sha256 must be a SHA-256 digest")
    path = (manifest.parent / raw).resolve()
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise JobError(f"{manifest}: cannot read pinned {label} {path}: {error}") from error
    if actual != expected:
        raise JobError(f"{manifest}: checksum mismatch for {label} {path}")
    return path


def _profiles(manifest: dict, path: Path) -> dict[str, Profile]:
    if manifest.get("schema_version") != LIBRARY_SCHEMA:
        raise JobError(f"{path}: schema_version must be {LIBRARY_SCHEMA!r}")
    raw_profiles = manifest.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise JobError(f"{path}: profiles must be a non-empty object")
    profiles: dict[str, Profile] = {}
    for profile_id, entry in raw_profiles.items():
        if not isinstance(profile_id, str) or not isinstance(entry, dict):
            raise JobError(f"{path}: profile ids and values must be strings and objects")
        kind = entry.get("kind")
        if kind == "sflt":
            soundfont = _verified_path(entry.get("soundfont", {}), path, f"profiles.{profile_id}.soundfont")
            bank, patch = entry.get("bank"), entry.get("patch")
            if not isinstance(bank, int) or not isinstance(patch, int):
                raise JobError(f"{path}: SFLT profile {profile_id} needs integer bank and patch")
            profiles[profile_id] = Profile(kind, soundfont=soundfont, bank=bank, patch=patch)
        elif kind == "chain":
            chain = _verified_path(entry.get("chain", {}), path, f"profiles.{profile_id}.chain")
            profiles[profile_id] = Profile(kind, chain=chain)
        else:
            raise JobError(f"{path}: profile {profile_id} kind must be 'sflt' or 'chain'")
    return profiles


def _library_from_profiles(profiles: dict[str, Profile]) -> Library:
    # The normal pipeline still classifies/merges the MIDI.  Profiles are then
    # applied exactly below, so this in-memory index cannot introduce a local
    # soundfont-library dependency.
    soundfonts: dict[Path, tuple[int, int]] = {}
    for profile in profiles.values():
        if profile.soundfont and profile.soundfont not in soundfonts:
            soundfonts[profile.soundfont] = (profile.bank or 0, profile.patch or 0)
    if not soundfonts:
        # Chain-only jobs need a harmless synthetic match to reach normal
        # classification; no synthetic path ever reaches the emitted RPP.
        placeholder = Path("/pinned/chain-only.sf2")
        soundfonts[placeholder] = (0, 0)
    # Supply each profile to all matcher categories. Selection is replaced by
    # the explicit part profile after classification, so this is only a bridge
    # through the existing, well-tested MIDI-to-part pipeline.
    categories = {"drums"}
    for program in range(128):
        categories.update(gm.preferred_categories(program))
    indexed = [
        SoundFont(path, category, [Preset(bank, patch, "pinned")])
        for path, (bank, patch) in soundfonts.items()
        for category in sorted(categories)
    ]
    return Library(root=Path("/pinned"), soundfonts=indexed)


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parents[2], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run(job_path: Path, out: Path, result_path: Path, *, force: bool = False) -> int:
    """Build one hermetic job and write a versioned result. Returns a CLI status."""
    job = _read_json(job_path)
    if job.get("schema_version") != JOB_SCHEMA:
        raise JobError(f"{job_path}: schema_version must be {JOB_SCHEMA!r}")
    job_id = _required_string(job, "job_id", job_path)
    template_id = _required_string(job, "template_id", job_path)
    if template_id != SFLT_TEMPLATE:
        raise JobError(f"{job_path}: unsupported template_id {template_id!r}; expected {SFLT_TEMPLATE!r}")
    procgen_commit = _required_string(job, "procgen_commit", job_path)
    midi = (job_path.parent / _required_string(job, "renderer_midi", job_path)).resolve()
    label_midi = (job_path.parent / _required_string(job, "label_midi", job_path)).resolve()
    if not label_midi.is_file():
        # Unlike renderer_midi (whose absence surfaces from inside build()),
        # nothing else in this function ever opens label_midi, so a missing
        # file would otherwise ship silently instead of failing the job.
        raise JobError(f"{job_path}: label_midi does not exist: {label_midi}")
    manifest_path = (job_path.parent / _required_string(job, "library_manifest", job_path)).resolve()
    assignments = job.get("part_profiles")
    if not isinstance(assignments, dict) or not assignments or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in assignments.items()
    ):
        raise JobError(f"{job_path}: part_profiles must map canonical part names to profile ids")
    routes = _renderer_tracks(job, assignments, job_path)
    profiles = _profiles(_read_json(manifest_path), manifest_path)
    unknown = sorted(set(assignments.values()) - set(profiles))
    if unknown:
        raise JobError(f"{job_path}: unknown profile ids: {', '.join(unknown)}")

    result = {
        "schema_version": RESULT_SCHEMA,
        "version": 1,
        "job_id": job_id,
        "template_id": template_id,
        "procgen_commit": procgen_commit,
        "renderer_midi": str(midi),
        "label_midi": str(label_midi),
        "midi2reaper_commit": _commit(),
        "status": "rejected",
        "built": [],
        "skipped": [],
        "rejected": [],
    }
    if out.exists() and not force:
        result["rejected"].append({"reason": f"output exists: {out}"})
        _write_result(result_path, result)
        return 1

    # Renderer MIDI is procgen's canonical symbolic export.  Its exact track
    # names, including whether a guitar has the terminal ``:rhythm`` suffix,
    # are the authority for this versioned job; generic MIDI role inference is
    # intentionally reserved for the ordinary import path.
    built = build(
        midi,
        _library_from_profiles(profiles),
        min_score=0.0,
        canonical_track_names=True,
    )
    result["skipped"] = [{"name": item.name, "reason": item.reason} for item in built.skipped]
    if not built.accepted:
        result["rejected"].append({"reason": built.rejection})
        _write_result(result_path, result)
        return 1

    actual_names = {part.track_name for part in built.parts}
    if actual_names != set(assignments):
        missing, extra = sorted(set(assignments) - actual_names), sorted(actual_names - set(assignments))
        result["rejected"].append({"reason": "part_profiles must exactly cover built parts", "missing": missing, "extra": extra})
        _write_result(result_path, result)
        return 1
    if built.skipped:
        result["rejected"].append({"reason": "pinned profiles failed to resolve one or more MIDI tracks"})
        _write_result(result_path, result)
        return 1

    for part, metadata in zip(built.parts, built.manifest_parts):
        profile_id = assignments[part.track_name]
        profile = profiles[profile_id]
        metadata["profile"] = profile_id
        if profile.kind == "sflt":
            part.soundfont_path, part.bank, part.patch, part.chain, part.chain_key = (
                profile.soundfont, profile.bank, profile.patch, None, None
            )
        else:
            part.chain = profile.chain.read_text(encoding="utf-8").splitlines()
            part.chain_key = profile_id
            metadata["instrument"] = "chain:" + profile_id
            metadata["chain"] = profile_id

    render_parts = []
    rendered_metadata = []
    aliases = []
    for part, metadata in zip(built.parts, built.manifest_parts):
        entries = routes.get(part.track_name)
        if not entries:
            render_parts.append(part)
            rendered_metadata.append(metadata)
            continue
        for entry in entries:
            render_parts.append(replace(part, renderer_track_id=entry["renderer_track_id"], pan=entry["pan"]))
            rendered_metadata.append(dict(metadata, renderer_track_id=entry["renderer_track_id"], pan=entry["pan"]))
            aliases.append({
                "renderer_track_id": entry["renderer_track_id"],
                "authoritative_symbolic_part": part.track_name,
                "pan": entry["pan"],
            })

    from .rpp import write_project

    seed = hashlib.sha256(json.dumps(job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    write_project(built.song, render_parts, out, deterministic_seed=seed)
    result["status"] = "built"
    built_result = {
        "project": str(out),
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "label_midi": str(label_midi),
        "label_midi_sha256": hashlib.sha256(label_midi.read_bytes()).hexdigest(),
        "parts": rendered_metadata,
    }
    if routes:
        built_result["renderer_track_aliases"] = aliases
    result["built"].append(built_result)
    _write_result(result_path, result)
    return 0


def _write_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
