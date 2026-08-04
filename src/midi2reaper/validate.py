"""Re-read generated projects and check every SFLT instance is loadable.

Catches the failures that would otherwise only show up as silence in REAPER: a
soundfont that moved, or a bank/patch the referenced file does not contain.
"""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from .rpp import B64_LINE_WIDTH
from .sf2 import read_presets

SFLT_MARKER = '<VST "VST3i: SFLT'


@dataclass
class Instance:
    track: str
    soundfont: Path
    bank: int
    patch: int


def read_instances(project: Path) -> list[Instance]:
    lines = project.read_text(encoding="utf-8", errors="replace").split("\n")
    instances: list[Instance] = []
    track = ""

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("<TRACK "):
            track = ""
        elif stripped.startswith("NAME ") and not track:
            track = stripped[5:].strip().strip("\"'`")
        elif stripped.startswith(SFLT_MARKER):
            payload = []
            j = i + 1
            while not lines[j].strip().startswith(">"):
                payload.append(lines[j].strip())
                j += 1
            state = _decode_state(payload)
            fields = state["fields"]
            instances.append(
                Instance(
                    track=track,
                    soundfont=Path(json.loads(fields["file"])),
                    bank=int(float(fields["bank"])),
                    patch=int(float(fields["patch"])),
                )
            )
    return instances


def _decode_state(payload: list[str]) -> dict:
    blocks, current = [], []
    for line in payload:
        current.append(line)
        if len(line) < B64_LINE_WIDTH:
            blocks.append(base64.b64decode("".join(current)))
            current = []
    if current:
        raise ValueError("unterminated base64 block")
    length = struct.unpack("<I", blocks[1][:4])[0]
    return json.loads(blocks[1][8 : 8 + length].decode())


def validate_project(project: Path) -> list[str]:
    problems: list[str] = []
    try:
        instances = read_instances(project)
    except Exception as error:  # noqa: BLE001 - report, do not crash the sweep
        return [f"{project.name}: unreadable ({error})"]

    if not instances:
        problems.append(f"{project.name}: no SFLT instances")

    for instance in instances:
        if not instance.soundfont.exists():
            problems.append(f"{project.name}: missing soundfont {instance.soundfont}")
            continue
        presets = read_presets(instance.soundfont)
        if presets is None:
            problems.append(f"{project.name}: unreadable soundfont {instance.soundfont.name}")
            continue
        if not any(p.bank == instance.bank and p.patch == instance.patch for p in presets):
            problems.append(
                f"{project.name}: {instance.soundfont.name} has no bank "
                f"{instance.bank} patch {instance.patch} (track {instance.track})"
            )
    return problems
