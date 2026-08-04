"""A library of REAPER FX chains, harvested from projects you have tuned by ear.

SFLT gets a part audible; it does not get it sounding right. Once a part has
been given a real instrument in REAPER — Shreddage through an amp sim, Ample
Bass behind a MIDI transpose — that chain is worth keeping and reusing.

A chain is copied verbatim rather than reconstructed. Everything that makes it
work lives inside the `<FXCHAIN>` block: plugin order, opaque plugin state,
bypass flags and any JS effects. Nothing outside it matters for these tracks —
they are `NCHAN 2` with no routing — so the block transplants cleanly.

The library accumulates. Extracting from a project adds the parts that project
happens to contain and leaves the rest alone, so overdriven guitar can be tuned
in one song and piano in another.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import gm

DEFAULT_LIBRARY = Path.home() / ".config" / "midi2reaper" / "chains"
INDEX_NAME = "index.json"

# SFLT is midi2reaper's own fallback instrument. A chain is harvested precisely
# because something better replaced it, so it is never carried along.
_SFLT = re.compile(r'<VST\s+"VST3i: SFLT', re.IGNORECASE)
# The name may be a quoted string containing spaces ("VST3i: Kontakt 8 (64 out)")
# or a bare token (midi/midi_transpose), so the two forms are matched separately.
_PLUGIN_LINE = re.compile(r'^<(VST|JS|AU|CLAP)\s+(?:"([^"]*)"|(\S+))')
_FORMAT_PREFIX = re.compile(r"^(VST3i?|VSTi?|AUi?|CLAPi?):\s*")
_FXID = re.compile(r"^(\s*FXID\s+)\{[0-9A-Fa-f-]+\}")


@dataclass
class Chain:
    key: str
    lines: list[str]
    plugins: list[str] = field(default_factory=list)
    source_project: str = ""
    source_track: str = ""
    extracted_at: str = ""

    @property
    def filename(self) -> str:
        # ':' is legal in HFS+ filenames but displays as '/' in Finder.
        return self.key.replace(":", "__") + ".rfxchain"


def _read_block(lines: list[str], start: int) -> tuple[list[str], int]:
    """Return the inner lines of the block opening at `start`, and its end index.

    Plugin entries nest their own `<...>` blocks, so depth is tracked rather
    than stopping at the first closing angle bracket.
    """
    depth = 1
    inner: list[str] = []
    i = start + 1
    while i < len(lines) and depth:
        stripped = lines[i].strip()
        if stripped.startswith("<"):
            depth += 1
        elif stripped == ">":
            depth -= 1
            if not depth:
                break
        inner.append(lines[i])
        i += 1
    return inner, i


def split_entries(chain: list[str]) -> tuple[list[str], list[list[str]]]:
    """Split an FX chain into its header and one group of lines per plugin.

    Each plugin entry begins at its `BYPASS` line and runs to the next one, so
    the entry carries its own bypass state, `FXID` and `WAK` with it.
    """
    first = next((i for i, l in enumerate(chain) if l.strip().startswith("BYPASS ")), len(chain))
    header, entries, current = chain[:first], [], []

    i = first
    while i < len(chain):
        stripped = chain[i].strip()
        if stripped.startswith("BYPASS ") and current:
            entries.append(current)
            current = []
        current.append(chain[i])
        if stripped.startswith("<"):
            inner, end = _read_block(chain, i)
            current += chain[i + 1 : end + 1]
            i = end
        i += 1
    if current:
        entries.append(current)
    return header, entries


def plugin_name(entry: list[str]) -> str:
    """Readable plugin name, without REAPER's format prefix."""
    for line in entry:
        match = _PLUGIN_LINE.match(line.strip())
        if match:
            name = match.group(2) if match.group(2) is not None else match.group(3)
            return _FORMAT_PREFIX.sub("", name.strip())
    return "?"


def extract_from_project(path: Path, drop_sflt: bool = True) -> list[Chain]:
    """Harvest one chain per canonically named track in a REAPER project."""
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    chains: list[Chain] = []
    track_name: str | None = None

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("<TRACK "):
            track_name = None
        elif stripped.startswith("NAME ") and track_name is None:
            track_name = _unquote(stripped[5:].strip())
        elif stripped.startswith("<FXCHAIN"):
            inner, end = _read_block(lines, i)
            key = _key_for(track_name or "")
            if key:
                header, entries = split_entries(inner)
                if drop_sflt:
                    entries = [e for e in entries if not any(_SFLT.search(l) for l in e)]
                if entries:
                    chains.append(
                        Chain(
                            key=key,
                            lines=header + [l for e in entries for l in e],
                            plugins=[plugin_name(e) for e in entries],
                            source_project=path.name,
                            source_track=track_name or "",
                            extracted_at=stamp,
                        )
                    )
            i = end
        i += 1
    return chains


def _key_for(track_name: str) -> str | None:
    """The canonical part name a track is titled with, ignoring any suffix."""
    head = track_name.split("|", 1)[0].strip()
    if not head:
        return None
    slug, _, annotation = head.partition(":")
    slug = slug.strip()
    if slug == gm.DRUM_SLUG:
        return slug
    if slug not in {gm.program_slug(p) for p in range(128)}:
        return None
    return f"{slug}:rhythm" if annotation.strip() == "rhythm" else slug


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        return text[1:-1]
    return text


def family_key(family: str) -> str:
    """`@guitar` — distinct from any program slug, which never starts with '@'."""
    return f"@{family}"


class ChainLibrary:
    """Chains on disk, keyed by canonical part name."""

    def __init__(self, root: Path):
        self.root = root
        self.index: dict[str, dict] = {}
        path = root / INDEX_NAME
        if path.exists():
            self.index = json.loads(path.read_text())

    def __len__(self) -> int:
        return len(self.index)

    def keys(self) -> list[str]:
        return sorted(self.index)

    def get(self, key: str) -> list[str] | None:
        entry = self.index.get(key)
        if entry is None:
            return None
        path = self.root / entry["file"]
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8").split("\n")

    def resolve(self, track_name: str) -> tuple[str, list[str]] | None:
        """Most specific chain for a part, falling back by degrees.

        1. the exact part name, `overdriven-guitar:rhythm`
        2. its bare program, `overdriven-guitar` — so a chain captured for an
           unannotated guitar also serves the rhythm one until a specific chain
           replaces it
        3. its family alias, `@guitar` — one amp rig can cover every guitar
           program, and one bass instrument every bass program, which matters
           because GM splits them finely: finger bass and pick bass are
           different programs but the same plugin.
        """
        for candidate in self.candidates(track_name):
            lines = self.get(candidate)
            if lines is not None:
                return candidate, lines
        return None

    @staticmethod
    def candidates(track_name: str) -> list[str]:
        slug = track_name.split(":", 1)[0]
        keys = [track_name, slug]
        if slug == gm.DRUM_SLUG:
            keys.append(family_key(gm.DRUM_SLUG))
        else:
            program = next((p for p in range(128) if gm.program_slug(p) == slug), None)
            if program is not None:
                keys.append(family_key(gm.family_of(program)))
        # dict.fromkeys keeps order while dropping the duplicate when a part
        # carries no annotation and name == slug.
        return list(dict.fromkeys(keys))

    def alias(self, key: str, source: str) -> bool:
        """Point `key` at the chain already stored under `source`."""
        entry = self.index.get(source)
        if entry is None:
            return False
        self.index[key] = dict(entry, aliased_from=source)
        return True


    def add(self, chain: Chain, replace: bool = True) -> str:
        """Store a chain. Returns 'added', 'updated' or 'kept'."""
        existing = self.index.get(chain.key)
        if existing and not replace:
            return "kept"
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / chain.filename).write_text("\n".join(chain.lines), encoding="utf-8")
        self.index[chain.key] = {
            "file": chain.filename,
            "plugins": chain.plugins,
            "source_project": chain.source_project,
            "source_track": chain.source_track,
            "extracted_at": chain.extracted_at,
        }
        return "updated" if existing else "added"

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / INDEX_NAME).write_text(json.dumps(self.index, indent=2, sort_keys=True))


def freshen_ids(lines: list[str]) -> list[str]:
    """Give every plugin in a spliced chain its own FXID.

    Copying a chain verbatim would otherwise repeat one plugin instance id
    across every track that uses it.
    """
    return [_FXID.sub(lambda m: m.group(1) + "{" + str(uuid.uuid4()).upper() + "}", line)
            for line in lines]
