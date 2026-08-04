# midi2reaper

Turns multi-track MIDI arrangements into REAPER projects where every part is
loaded into its own [SFLT](https://estrobiologist.gumroad.com/p/sflt-beta-v0-10-released)
SoundFont player instance, ready to audition.

It exists to build training data for the MT3 fine-tune in `../DATA_CONTRACT.md`,
which this tool implements: guitar anchors corpus membership, vocals are
rendered as instruments rather than skipped, unresolvable tracks are dropped,
and tracks that change program mid-song are split into one part per program.

This tool stops at a project you can open and verify by ear. Turning verified
projects into training examples is [`reaper2mt3`](../reaper2mt3)'s job.

The `.RPP` is the handover between the two, and REAPER track names are the
interface: each part is titled `<slug>:<role>` — `distortion-guitar:rhythm` —
which `reaper2mt3` parses back as the training label. Because the project is
read rather than the source MIDI, corrections made while auditioning (a swapped
soundfont, a renamed part, an edited note) carry through.

## Requirements

macOS only. Paths below are the defaults the tool and SFLT both assume; the
versions in brackets are the ones this has been verified against.

### 1. REAPER  [7.74]

<https://www.reaper.fm/download.php>

Installed at `/Applications/REAPER.app`. Needed only to open, audition and
render the generated projects — `build` and `validate` do not require it.

After installing SFLT below, let REAPER rescan plugins
(*Settings → Plug-ins → VST → Re-scan*) so it can resolve the plugin reference
inside generated projects.

### 2. SFLT SoundFont player  [beta v0.10.0]

<https://estrobiologist.gumroad.com/p/sflt-beta-v0-10-released> — free.

**The VST3 build is required.** Generated projects reference the VST3 class id
`955354538{496D53464C54696E674F757442616279}`; the AU and CLAP builds will not
satisfy them. From the downloaded archive:

```sh
sudo cp -R SFLT.vst3 /Library/Audio/Plug-Ins/VST3/
```

On Apple Silicon use `SFLT.vst3`, not the `SFLT_x86.vst3` variant shipped
alongside it.

The state template in `src/midi2reaper/data/` was captured from **0.10.0**. A
different SFLT release may change the persisted field set; if a future version
misreads generated projects, re-capture the template from a project REAPER saved
with that version.

### 3. SoundFont library  [537 files, 1.7 GB]

<https://www.zanderjaz.com/downloads/soundfonts/>

Must live at `/Users/Shared/Soundfonts` — SFLT's hardcoded default library root
on macOS — laid out with **one subfolder per category**:

```text
/Users/Shared/Soundfonts/
  brass/  choirs/  drums/  flutes/  fx/  guitars/  orchestral/  organs/
  other/  packs/  pianos/  planet-phatt-hip-hop/  st-james-orchestra/
  strings/  synths/  thor/
```

Those folder names are load-bearing: `gm.py` maps each GM program to the
categories it should be matched from, so a library organised differently will
match poorly. A different root can be passed with `--library`, but the paths
written into projects are absolute, so the library must then stay put.

Two files in this collection are not readable SoundFonts and are skipped —
`pianos/FOXPIAN2.SBK` (older SBK format) and
`planet-phatt-hip-hop/sbs Sub 4 Gold.sf2`. That is expected, not an error.

### 4. Python  [3.11]

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). The only runtime
dependency is `mido`; `pytest` is used for the tests.

## Usage

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e .

.venv/bin/midi2reaper index                            # library coverage
.venv/bin/midi2reaper build ../reaper/midi-src -o ../reaper/generated \
    --report ../reaper/generated/report.json
.venv/bin/midi2reaper validate ../reaper/generated     # every preset resolvable
```

`build` writes one `.RPP` per accepted source file, plus a JSON report
recording every match, its score and the evidence behind each rhythm/lead call.
Open a project in REAPER and press play — no further setup is needed.

Once the projects sound right, hand them to
[`reaper2mt3`](../reaper2mt3) to render the corpus.

## How it works

**Soundfont matching.** The strongest signal is that many single-instrument
soundfonts declare their GM patch number: `Clean Strat.sf2` is bank 0 patch 27,
exactly "Electric Guitar (clean)". An exact patch hit outweighs any name
evidence; library category and keyword overlap resolve the rest. Parts with no
candidate above `--min-score` are skipped rather than guessed at.

**Rhythm vs lead.** Inferred from content, with track names as corroboration
only. The primary axis is *coverage* — what fraction of the piece the part
sounds in — because accompaniment runs throughout while a lead appears in
bursts. Chords and polyphony argue only *for* accompaniment: their absence
means nothing, since bass lines, riffs and arpeggiated backing are all
monophonic accompaniment. Percussion is always rhythm.

**Writing SFLT state.** SFLT is a nih-plug plugin, so REAPER stores its state as
JSON inside the RPP's base64 VST chunk:

```
block0 = [288-byte VST3 pin config][u32 len(block1)][u32 1][ffff][0000]
block1 = [u32 len(json)][u32 1][ json ][8 zero bytes]
block2 = 6 zero bytes
```

Only `fields.file`, `fields.bank` and `fields.patch` vary between instances, so
the template in `data/` is lifted verbatim from a REAPER-saved project and those
three fields are substituted. Blocks are delimited by a line shorter than 128
characters, so the JSON is padded whenever its base64 length would land on a
multiple of 128 and silently merge into the next block.

**Program changes are stripped** from generated items. SFLT reacts to them, and
with auto-find-preset enabled (its default) a stray program change does not go
silent — it snaps a multi-preset bank to the *wrong* instrument.

## Known limits

- Soundfont paths are absolute and point at `/Users/Shared/Soundfonts`, SFLT's
  default macOS library root. Moving the library invalidates generated projects;
  `validate` detects this.
- Matching is heuristic. Review `report.json` before trusting a batch: every
  part carries its score, the reasons behind it, and the role evidence.
- Rendering to audio is out of scope. `build` stops at a project you can
  open, verify and play; `reaper2mt3` takes it from there.
