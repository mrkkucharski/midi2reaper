"""Turn a source MIDI arrangement into REAPER-ready parts.

Implements the source-track selection rules in DATA_CONTRACT.md: guitar anchors
membership, vocals are rendered rather than skipped, unresolvable tracks are
dropped, and tracks that change program mid-song split into one part per
program before the `(program, rhythm)` merge is applied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import classify, gm
from .match import Match, match_program
from .midiscan import Note, Song, scan
from .rpp import RenderPart
from .sf2 import Library

TENOR_SAX = 66


@dataclass
class SkippedTrack:
    name: str
    reason: str


@dataclass
class BuildResult:
    song: Song
    parts: list[RenderPart] = field(default_factory=list)
    manifest_parts: list[dict] = field(default_factory=list)
    skipped: list[SkippedTrack] = field(default_factory=list)
    rejection: str | None = None

    @property
    def accepted(self) -> bool:
        return self.rejection is None


@dataclass
class _Candidate:
    program: int
    is_drum: bool
    rhythm: bool
    notes: list[Note]
    match: Match
    source_name: str
    vocal: bool
    role: classify.Role


def build(path: Path, library: Library, min_score: float) -> BuildResult:
    song = scan(path)
    result = BuildResult(song=song)
    candidates: list[_Candidate] = []
    song_ticks = max(
        (n.end for t in song.tracks for s in t.segments for n in s.notes),
        default=0,
    )

    for track in song.tracks:
        label = track.name or f"track {track.index}"
        vocal = classify.is_vocal(track.name)

        for segment in track.segments:
            if not segment.notes:
                continue

            program = segment.program
            substituted = False
            if vocal and not track.has_program_change and not segment.is_drum:
                program, substituted = TENOR_SAX, True

            found = match_program(program, segment.is_drum, track.name, library, min_score)
            if found is None:
                result.skipped.append(
                    SkippedTrack(label, f"no soundfont match above {min_score} for program {program}")
                )
                continue

            role = classify.classify_role(segment, song.ppq, song_ticks, track.name, vocal)
            candidates.append(
                _Candidate(
                    program=program,
                    is_drum=segment.is_drum,
                    rhythm=role.rhythm,
                    notes=segment.notes,
                    match=found,
                    source_name=label,
                    vocal=vocal or substituted,
                    role=role,
                )
            )

    if not any(gm.is_guitar(c.program) for c in candidates if not c.is_drum):
        result.rejection = "no guitar part: DATA_CONTRACT.md requires a guitar to anchor the example"
        return result

    _merge_into(result, candidates, song)
    return result


def _merge_into(result: BuildResult, candidates: list[_Candidate], song: Song) -> None:
    """Collapse candidates sharing a `(program, rhythm)` pair into one part."""
    groups: dict[tuple[int | None, bool, bool], list[_Candidate]] = {}
    for candidate in candidates:
        key = (None if candidate.is_drum else candidate.program, candidate.is_drum, candidate.rhythm)
        groups.setdefault(key, []).append(candidate)

    for (program, is_drum, rhythm), members in sorted(
        groups.items(), key=lambda kv: (kv[0][1], kv[0][0] if kv[0][0] is not None else -1, kv[0][2])
    ):
        notes = sorted((n for m in members for n in m.notes), key=lambda n: n.start)
        best = max(members, key=lambda m: m.match.score)
        track_name = gm.track_name(program, is_drum, rhythm)
        vocal = any(m.vocal for m in members)
        sources = [m.source_name for m in members]

        result.parts.append(
            RenderPart(
                track_name=track_name,
                source_name=" + ".join(sources),
                notes=notes,
                soundfont_path=best.match.soundfont.path,
                bank=best.match.bank,
                patch=best.match.patch,
                is_drum=is_drum,
                vocal_substitution=vocal,
            )
        )
        result.manifest_parts.append(
            {
                "track_name": track_name,
                "program": None if is_drum else program,
                "is_drum": is_drum,
                "rhythm": rhythm,
                "note_count": len(notes),
                "source_track_names": sources,
                "soundfont": best.match.rel_path,
                "bank": best.match.bank,
                "patch": best.match.patch,
                "preset_name": best.match.preset_name,
                "match_confidence": round(best.match.score, 3),
                "match_reasons": best.match.reasons,
                "role_confidence": round(best.role.confidence, 3),
                "role_evidence": best.role.evidence,
                "role_annotation_note": best.role.note,
                "vocal_substitution": vocal,
            }
        )
