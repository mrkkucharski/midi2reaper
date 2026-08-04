"""Parse a multi-track MIDI file into note segments split by program.

Source arrangements routinely broadcast program changes across several channels
and switch program mid-track, so the program in effect is tracked per channel
and every note is tagged with the program sounding when it started.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path

import mido

DRUM_CHANNEL = 9
DEFAULT_TEMPO = 500_000  # microseconds per beat, i.e. 120 BPM


@dataclass(frozen=True)
class Note:
    start: int
    end: int
    pitch: int
    velocity: int
    channel: int


@dataclass
class Segment:
    """One source track's notes under a single program."""

    program: int
    is_drum: bool
    notes: list[Note]

    @property
    def note_count(self) -> int:
        return len(self.notes)


@dataclass
class SourceTrack:
    index: int
    name: str
    segments: list[Segment] = field(default_factory=list)
    # Distinguishes "declared program 0" from "declared nothing", which decides
    # whether a vocal track falls back to Tenor Sax.
    has_program_change: bool = False

    @property
    def note_count(self) -> int:
        return sum(s.note_count for s in self.segments)

    @property
    def programs(self) -> list[int]:
        return sorted({s.program for s in self.segments if not s.is_drum})


@dataclass
class TempoMap:
    ppq: int
    changes: list[tuple[int, int]]  # (tick, microseconds per beat)

    def __post_init__(self) -> None:
        self._ticks: list[int] = []
        self._seconds: list[float] = []
        self._tempos: list[int] = []
        seconds = 0.0
        previous_tick = 0
        previous_tempo = DEFAULT_TEMPO
        for tick, tempo in self.changes:
            seconds += (tick - previous_tick) / self.ppq * previous_tempo / 1e6
            self._ticks.append(tick)
            self._seconds.append(seconds)
            self._tempos.append(tempo)
            previous_tick, previous_tempo = tick, tempo

    def seconds_at(self, tick: int) -> float:
        if not self._ticks:
            return tick / self.ppq * DEFAULT_TEMPO / 1e6
        i = bisect.bisect_right(self._ticks, tick) - 1
        if i < 0:
            return tick / self.ppq * DEFAULT_TEMPO / 1e6
        return self._seconds[i] + (tick - self._ticks[i]) / self.ppq * self._tempos[i] / 1e6

    @property
    def points(self) -> list[tuple[float, float]]:
        """(seconds, bpm) for the Reaper tempo envelope."""
        return [(s, 60e6 / t) for s, t in zip(self._seconds, self._tempos)]


@dataclass
class Song:
    path: Path
    ppq: int
    tempo_map: TempoMap
    time_signature: tuple[int, int]
    tracks: list[SourceTrack]
    length_seconds: float

    @property
    def name(self) -> str:
        return self.path.stem


def scan(path: Path) -> Song:
    midi = mido.MidiFile(str(path))
    ppq = midi.ticks_per_beat

    tempo_changes: list[tuple[int, int]] = []
    time_signature = (4, 4)
    tracks: list[SourceTrack] = []

    for index, raw in enumerate(midi.tracks):
        name = ""
        saw_program_change = False
        program_by_channel: dict[int, int] = {}
        # Queue per (channel, pitch): a source track may restrike a pitch before
        # releasing it, and a plain dict would keep only the last of the run.
        open_notes: dict[tuple[int, int], list[tuple[int, int]]] = {}
        by_key: dict[tuple[int, bool], list[Note]] = {}
        tick = 0

        for message in raw:
            tick += message.time
            kind = message.type

            if kind == "set_tempo":
                tempo_changes.append((tick, message.tempo))
            elif kind == "time_signature" and not tracks and tick == 0:
                time_signature = (message.numerator, message.denominator)
            elif kind == "track_name" and not name:
                name = message.name.strip()
            elif kind == "program_change":
                saw_program_change = True
                program_by_channel[message.channel] = message.program
            elif kind == "note_on" and message.velocity > 0:
                open_notes.setdefault((message.channel, message.note), []).append(
                    (tick, message.velocity)
                )
            elif kind in ("note_off", "note_on"):
                queue = open_notes.get((message.channel, message.note))
                if not queue:
                    continue
                start, velocity = queue.pop(0)
                is_drum = message.channel == DRUM_CHANNEL
                program = 0 if is_drum else program_by_channel.get(message.channel, 0)
                note = Note(start, max(tick, start + 1), message.note, velocity, message.channel)
                by_key.setdefault((program, is_drum), []).append(note)

        segments = [
            Segment(program=program, is_drum=is_drum, notes=sorted(notes, key=lambda n: n.start))
            for (program, is_drum), notes in sorted(by_key.items())
            if notes
        ]
        tracks.append(
            SourceTrack(
                index=index, name=name, segments=segments, has_program_change=saw_program_change
            )
        )

    tempo_changes.sort()
    return Song(
        path=path,
        ppq=ppq,
        tempo_map=TempoMap(ppq=ppq, changes=tempo_changes),
        time_signature=time_signature,
        tracks=tracks,
        length_seconds=midi.length,
    )
