"""Write REAPER project files with one SFLT instance per part.

SFLT is a nih-plug plugin, so REAPER stores its state as JSON inside the RPP's
base64 VST chunk:

    block0 = [288-byte VST3 pin config][u32 len(block1)][u32 1][ffff][0000]
    block1 = [u32 len(json)][u32 1][ json ][8 zero bytes]
    block2 = 6 zero bytes

Only `fields.file`, `fields.bank` and `fields.patch` differ between instances,
so the template is lifted verbatim from a project REAPER saved and the three
fields are substituted. Blocks are delimited by a line shorter than 128
characters, so the JSON is padded when its base64 length lands on a multiple
of 128.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import time
import uuid
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .chains import freshen_ids
from .midiscan import Note, Song
from .ranges import RangeWarning

VST_LINE = (
    '<VST "VST3i: SFLT (ash taylor) (34 out)" SFLT.vst3 0 "" '
    '955354538{496D53464C54696E674F757442616279} ""'
)
B64_LINE_WIDTH = 128
DRUM_CHANNEL = 9

# Project-wide defaults matching the tuned render settings (mono 16-bit render
# by ear in Californication; master fader set to -10 dB) applied to every
# generated project. Baked in here so a fresh `build` already has them instead
# of every project needing the same manual edit.
#
# Bit depth and channel count are stored in two places that must agree: the
# plain-text RENDER_FMT line and an opaque base64 blob in RENDER_CFG. Decoding
# the blob confirms what it encodes rather than assuming: it starts with the
# output format's ASCII fourcc reversed ("evaw" for "wave", "calf" for "flac"),
# the same convention in both. WAV's blob is 7 bytes -- fourcc + a 2-byte
# little-endian bit-depth field (0x18/24 in REAPER's own default, 0x10/16
# here) + a 1-byte flag. FLAC's is a different shape, not just a longer
# bit-depth field: fourcc + a 4-byte bit-depth field (0x10/16) + a 4-byte
# compression-level field (5, REAPER's own FLAC default, unchanged here).
# Confirmed against a real project (`The Police-Every Breath You Take-REV.RPP`)
# resaved with the render format switched to FLAC in REAPER's own UI, not
# guessed at.
MASTER_VOLUME_GAIN = 0.31622776601684  # linear; -10 dB
RENDER_FMT_LINE = "  RENDER_FMT 0 1 44100"  # channel field: 1 = mono
RENDER_CFG_B64 = "Y2FsZhAAAAAFAAAA"  # 'calf' + 16-bit + compression level 5


@dataclass
class RenderPart:
    """A part ready to be written as one REAPER track."""

    track_name: str
    source_name: str
    notes: list[Note]
    soundfont_path: Path
    bank: int
    patch: int
    is_drum: bool
    vocal_substitution: bool = False
    # A harvested FX chain, spliced in place of the SFLT fallback.
    chain: list[str] | None = None
    chain_key: str | None = None
    range_warning: RangeWarning | None = None

    @property
    def display_name(self) -> str:
        """Track label shown in REAPER: canonical name, source, and any substitution."""
        label = f"{self.track_name} | {self.source_name}" if self.source_name else self.track_name
        if self.vocal_substitution:
            label += " [vocal→instrument]"
        return label


def _guid() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


class DeterministicIds:
    """Stable UUID-shaped ids derived from a renderer-job content hash."""

    def __init__(self, seed: str):
        self.seed = seed
        self.counter = 0

    def guid(self) -> str:
        value = self.raw()
        return "{" + value + "}"

    def raw(self) -> str:
        digest = hashlib.sha256(f"{self.seed}:{self.counter}".encode()).hexdigest()
        self.counter += 1
        return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}".upper()


def _b64_lines(payload: bytes) -> list[str]:
    text = base64.b64encode(payload).decode()
    return [text[i : i + B64_LINE_WIDTH] for i in range(0, len(text), B64_LINE_WIDTH)]


def _load_template() -> tuple[dict, bytes]:
    data = resources.files("midi2reaper.data")
    state = json.loads(data.joinpath("sflt_state_template.json").read_text())
    pin = base64.b64decode(data.joinpath("sflt_pin_config.b64").read_text())
    return state, pin


def sflt_chunk(soundfont: Path, bank: int, patch: int) -> list[str]:
    """Base64 lines encoding one SFLT instance loaded with `soundfont`."""
    state, pin = _load_template()
    # SFLT stores the path as a JSON string *inside* the field's string value.
    state["fields"]["file"] = json.dumps(str(soundfont))
    state["fields"]["bank"] = str(bank)
    state["fields"]["patch"] = str(patch)
    state["params"]["bank"]["f32"] = float(bank)
    state["params"]["patch"]["f32"] = float(patch)

    payload = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    while True:
        block1 = struct.pack("<II", len(payload), 1) + payload + b"\x00" * 8
        if len(base64.b64encode(block1)) % B64_LINE_WIDTH:
            break
        payload += b" "  # trailing whitespace keeps the JSON valid

    block0 = pin + struct.pack("<IIHH", len(block1), 1, 0xFFFF, 0)
    return _b64_lines(block0) + _b64_lines(block1) + _b64_lines(b"\x00" * 6)


def midi_events(notes: list[Note], is_drum: bool) -> list[str]:
    """`E <delta_ticks> <hex>` lines. Program and bank-select events are omitted
    deliberately: SFLT reacts to them and, with auto-find-preset enabled, would
    silently snap a multi-preset bank to the wrong instrument."""
    channel = DRUM_CHANNEL if is_drum else 0
    events: list[tuple[int, int, str]] = []
    for note in notes:
        events.append((note.start, 1, f"9{channel:x} {note.pitch:02x} {note.velocity:02x}"))
        events.append((note.end, 0, f"8{channel:x} {note.pitch:02x} 00"))
    events.sort(key=lambda e: (e[0], e[1]))  # note-offs before note-ons at a shared tick

    lines = []
    previous = 0
    for tick, _, payload in events:
        lines.append(f"E {tick - previous} {payload}")
        previous = tick
    lines.append(f"E {max(1, 0)} b{channel:x} 7b 00")  # all notes off
    return lines


def _track_block(part: RenderPart, song: Song, length: float, ids: DeterministicIds | None = None) -> list[str]:
    guid_fn = ids.guid if ids else _guid
    guid, track_id, fx_id, item_guid, iguid = (guid_fn() for _ in range(5))
    out = [
        f"  <TRACK {guid}",
        f"    NAME {_quote(part.display_name)}",
        "    PEAKCOL 16576",
        "    BEAT -1",
        "    AUTOMODE 0",
        "    VOLPAN 1 0 -1 -1 1",
        "    MUTESOLO 0 0 0",
        "    IPHASE 0",
        "    PLAYOFFS 0 1",
        "    ISBUS 0 0",
        "    BUSCOMP 0 0 0 0 0",
        "    SHOWINMIX 1 0.6667 0.5 1 0.5 0 0 0 0",
        "    SEL 0",
        "    REC 0 0 1 0 0 0 0 0",
        "    VU 64",
        "    TRACKHEIGHT 0 0 0 0 0 0 0",
        "    INQ 0 0 0 0.5 100 0 0 100",
        "    NCHAN 2",
        "    FX 1",
        f"    TRACKID {track_id}",
        "    PERF 0",
        "    MIDIOUT -1",
        "    MAINSEND 1 0",
        "    <FXCHAIN",
    ]
    if part.chain is not None:
        out += freshen_ids(part.chain, ids.raw if ids else None)
    else:
        out += [
            "      SHOW 0",
            "      LASTSEL 0",
            "      DOCKED 0",
            "      BYPASS 0 0 0",
            f"      {VST_LINE}",
        ]
        out += [f"        {line}" for line in sflt_chunk(part.soundfont_path, part.bank, part.patch)]
        out += [
            "      >",
            "      FLOATPOS 0 0 0 0",
            f"      FXID {fx_id}",
            "      WAK 0 0",
        ]
    out += [
        "    >",
        "    <ITEM",
        "      POSITION 0",
        "      SNAPOFFS 0",
        f"      LENGTH {length:.6f}",
        "      LOOP 0",
        "      ALLTAKES 0",
        "      FADEIN 1 0 0 1 0 0 0",
        "      FADEOUT 1 0 0 1 0 0 0",
        "      MUTE 0 0",
        "      SEL 0",
        f"      IGUID {iguid}",
        "      IID 1",
        f"      NAME {_quote(part.display_name)}",
        "      VOLPAN 1 0 1 -1",
        "      SOFFS 0 0",
        "      PLAYRATE 1 1 0 -1 0 0.0025",
        "      CHANMODE 0",
        f"      GUID {item_guid}",
        "      <SOURCE MIDI",
        f"        HASDATA 1 {song.ppq} QN",
        "        CCINTERP 32",
    ]
    out += [f"        {line}" for line in midi_events(part.notes, part.is_drum)]
    out += ["      >", "    >", "  >"]
    return out


def _quote(text: str) -> str:
    """REAPER strings pick a quote character the value does not contain."""
    for quote in ('"', "'", "`"):
        if quote not in text:
            return f"{quote}{text}{quote}"
    return '"' + text.replace('"', "'") + '"'


def write_project(
    song: Song, parts: list[RenderPart], out_path: Path, *, deterministic_seed: str | None = None,
) -> None:
    """Write a project.  A renderer job supplies ``deterministic_seed``.

    The legacy CLI intentionally retains its historical random project ids.
    """
    ids = DeterministicIds(deterministic_seed) if deterministic_seed else None
    points = song.tempo_map.points or [(0.0, 120.0)]
    numerator, denominator = song.time_signature
    length = max(song.length_seconds, _last_note_seconds(song, parts)) + 1.0

    lines = [
        f'<REAPER_PROJECT 0.1 "7.74/macOS-arm64" {0 if ids else int(time.time())} 0',
        "  RIPPLE 0 0",
        "  GROUPOVERRIDE 0 0 0 0",
        "  AUTOXFADE 129",
        "  ENVATTACH 3",
        "  POOLEDENVATTACH 0",
        "  TCPUIFLAGS 0",
        "  MIXERUIFLAGS 11 48",
        "  PEAKGAIN 1",
        "  FEEDBACK 0",
        "  PANLAW 1",
        "  PROJOFFS 0 0 0",
        "  MAXPROJLEN 0 0",
        "  GRID 3455 8 1 8 1 0 0 0",
        "  TIMEMODE 1 5 -1 30 0 0 -1 0",
        "  VIDEO_CONFIG 0 0 256",
        "  PANMODE 3",
        "  CURSOR 0",
        "  ZOOM 4 0 0",
        "  VZOOMEX 6 0",
        "  USE_REC_CFG 0",
        "  RECMODE 1",
        "  SMPTESYNC 0 30 100 40 1000 300 0 0 1 0 0",
        "  LOOP 0",
        "  LOOPGRAN 0 4",
        '  RECORD_PATH "Media/Recordings" ""',
        "  <RECORD_CFG",
        "    ZXZhdxgAAQ==",
        "  >",
        "  <APPLYFX_CFG",
        "  >",
        '  RENDER_FILE ""',
        '  RENDER_PATTERN ""',
        RENDER_FMT_LINE,
        "  RENDER_1X 0",
        "  RENDER_RANGE 1 0 0 18 1000",
        "  RENDER_RESAMPLE 3 0 1",
        "  RENDER_ADDTOPROJ 0",
        "  RENDER_STEMS 0",
        "  RENDER_DITHER 0",
        "  RENDER_TRIM 0.000001 0.000001 0 0",
        "  TIMELOCKMODE 1",
        "  TEMPOENVLOCKMODE 1",
        "  ITEMMIX 1",
        "  DEFPITCHMODE 589824 0",
        "  TAKELANE 1",
        "  SAMPLERATE 44100 0 0",
        "  <RENDER_CFG",
        f"    {RENDER_CFG_B64}",
        "  >",
        "  LOCK 1",
        "  GLOBAL_AUTO -1",
        f"  TEMPO {points[0][1]:.6f} {numerator} {denominator} 0",
        "  PLAYRATE 1 1 0.25 4",
        "  SELECTION 0 0",
        "  SELECTION2 0 0",
        "  MASTERAUTOMODE 0",
        "  MASTERTRACKHEIGHT 0 0",
        "  MASTERPEAKCOL 16576",
        "  MASTERMUTESOLO 0",
        "  MASTERTRACKVIEW 0 0.6667 0.5 0.5 0 0 0 0 0 0 0 0 0 0 1",
        "  MASTERHWOUT 0 0 1 0 0 0 0 -1",
        "  MASTER_NCH 2 2",
        f"  MASTER_VOLUME {MASTER_VOLUME_GAIN} 0 -1 -1 1",
        "  MASTER_PANMODE 3",
        "  MASTER_FX 1",
        "  MASTER_SEL 0",
    ]

    if len(points) > 1:
        lines.append("  <TEMPOENVEX")
        lines += [f"    EGUID {(ids.guid() if ids else _guid())}", "    ACT 1 -1", "    VIS 1 0 1",
                  "    LANEHEIGHT 0 0", "    ARM 0", "    DEFSHAPE 1 -1 -1"]
        lines += [f"    PT {seconds:.12f} {bpm:.10f} 1" for seconds, bpm in points]
        lines.append("  >")

    lines += ["  RULERHEIGHT 86 86", "  <PROJBAY", "  >"]
    for part in parts:
        lines += _track_block(part, song, length, ids)
    lines.append(">")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _last_note_seconds(song: Song, parts: list[RenderPart]) -> float:
    last = 0
    for part in parts:
        for note in part.notes:
            last = max(last, note.end)
    return song.tempo_map.seconds_at(last)
