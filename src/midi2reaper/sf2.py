"""Read SoundFont preset tables and index a soundfont library.

Only the `phdr` chunk is parsed: it lists every preset with its name, bank and
patch number, which is all the matcher and SFLT need. Sample data is never read,
so indexing 500+ files stays fast.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

PHDR_RECORD_SIZE = 38


@dataclass(frozen=True)
class Preset:
    bank: int
    patch: int
    name: str


@dataclass
class SoundFont:
    path: Path
    category: str
    presets: list[Preset]

    @property
    def rel_path(self) -> str:
        return f"{self.category}/{self.path.name}"

    @property
    def stem(self) -> str:
        return self.path.stem


@dataclass
class Library:
    root: Path
    soundfonts: list[SoundFont] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    def by_category(self, category: str) -> list[SoundFont]:
        return [s for s in self.soundfonts if s.category == category]

    @property
    def preset_count(self) -> int:
        return sum(len(s.presets) for s in self.soundfonts)


def read_presets(path: Path) -> list[Preset] | None:
    """Return the file's presets, or None if it is not a readable SoundFont.

    The terminal EOP record that closes every phdr chunk is dropped.
    """
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"RIFF":
                return None
            f.seek(12)  # skip RIFF size + 'sfbk'
            while True:
                header = f.read(8)
                if len(header) < 8:
                    return None
                chunk_id, size = struct.unpack("<4sI", header)
                if chunk_id != b"LIST":
                    f.seek(size + (size & 1), 1)
                    continue
                if f.read(4) != b"pdta":
                    f.seek(size - 4 + (size & 1), 1)
                    continue
                end = f.tell() + size - 4
                while f.tell() < end:
                    sub = f.read(8)
                    if len(sub) < 8:
                        return None
                    sub_id, sub_size = struct.unpack("<4sI", sub)
                    if sub_id == b"phdr":
                        return _parse_phdr(f.read(sub_size))
                    f.seek(sub_size + (sub_size & 1), 1)
                return None
    except (OSError, struct.error):
        return None


def _parse_phdr(raw: bytes) -> list[Preset]:
    presets = []
    for offset in range(0, len(raw), PHDR_RECORD_SIZE):
        record = raw[offset : offset + PHDR_RECORD_SIZE]
        if len(record) < PHDR_RECORD_SIZE:
            break
        name = record[:20].split(b"\0")[0].decode("latin-1").strip()
        patch, bank = struct.unpack("<HH", record[20:24])
        presets.append(Preset(bank=bank, patch=patch, name=name))
    return presets[:-1]


def index_library(root: Path, cache: Path | None = None, refresh: bool = False) -> Library:
    """Index every SoundFont under `root`, one category per immediate subfolder."""
    if cache and cache.exists() and not refresh:
        return _load_cache(root, cache)

    library = Library(root=root)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        presets = read_presets(path)
        rel = path.relative_to(root)
        category = rel.parts[0] if len(rel.parts) > 1 else ""
        if presets is None:
            library.unreadable.append(str(rel))
            continue
        library.soundfonts.append(SoundFont(path=path, category=category, presets=presets))

    if cache:
        _save_cache(library, cache)
    return library


def _save_cache(library: Library, cache: Path) -> None:
    cache.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "root": str(library.root),
        "unreadable": library.unreadable,
        "soundfonts": [
            {
                "path": str(s.path),
                "category": s.category,
                "presets": [[p.bank, p.patch, p.name] for p in s.presets],
            }
            for s in library.soundfonts
        ],
    }
    cache.write_text(json.dumps(payload))


def _load_cache(root: Path, cache: Path) -> Library:
    payload = json.loads(cache.read_text())
    return Library(
        root=root,
        unreadable=payload["unreadable"],
        soundfonts=[
            SoundFont(
                path=Path(s["path"]),
                category=s["category"],
                presets=[Preset(bank=b, patch=p, name=n) for b, p, n in s["presets"]],
            )
            for s in payload["soundfonts"]
        ],
    )
