# midi2reaper

Turns multi-track MIDI arrangements into REAPER projects with an instrument
loaded on every part, ready to audition. Parts use FX chains harvested from
projects you have tuned by ear, falling back to the
[SFLT](https://estrobiologist.gumroad.com/p/sflt-beta-v0-10-released) SoundFont
player where no chain exists yet.

It exists to build training data for the MT3 fine-tune in `../DATA_CONTRACT.md`,
which this tool implements: guitar anchors corpus membership, vocals are
rendered as instruments rather than skipped, unresolvable tracks are dropped,
and tracks that change program mid-song are split into one part per program.

This tool stops at a project you can open and verify by ear. Turning verified
projects into training examples is [`reaper2mt3`](../reaper2mt3)'s job.

The `.RPP` is the handover between the two, and REAPER track names are the
interface: a guitar playing chordal accompaniment is titled
`distortion-guitar:rhythm`, every other part just `tenor-sax`, and `reaper2mt3`
parses those back as the training labels. Because the project is read rather
than the source MIDI, corrections made while auditioning (a swapped soundfont, a
renamed part, an edited note) carry through.

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

Required only for the fallback instrument. Parts covered by a harvested chain do
not use it, and the plugins those chains reference (Kontakt, Guitar Rig, Ample
Bass and so on) are your own — this tool never installs or configures them.

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

**Existing projects are never overwritten** without `-f/--force`, because
generated projects get hand-tuned and clobbering one loses that work.

## Instrument chains

SFLT gets a part audible; it does not get it sounding right. Once a part has a
real instrument in REAPER, harvest that chain and every future project reuses
it:

```sh
# tune a project in REAPER, then:
.venv/bin/midi2reaper chains extract ../reaper/generated/*.RPP
.venv/bin/midi2reaper chains list
```

The library **accumulates**, so overdriven guitar can be tuned in one song and
piano in another and both land in your defaults. It lives in
`~/.config/midi2reaper/chains` (override with `--chains`).

Chains are copied verbatim: plugin order, opaque plugin state, bypass flags and
any JS effects all live inside the `<FXCHAIN>` block, and these tracks are
`NCHAN 2` with no routing, so the block transplants cleanly. Only `FXID`s are
regenerated, so each spliced instance stays unique. SFLT is dropped on
extraction — it is the fallback a chain exists to replace (`--keep-sflt` to
retain it).

### Which chain a part gets

Most specific wins:

1. the exact part name — `overdriven-guitar:rhythm`
2. its bare program — `overdriven-guitar`
3. its GM family alias — `@guitar`

Family aliases matter because GM splits instruments finely: finger bass and pick
bass are different programs but the same plugin. Create one with:

```sh
.venv/bin/midi2reaper chains alias @bass electric-bass-finger
.venv/bin/midi2reaper chains alias @guitar overdriven-guitar
```

Note that `@guitar` spans programs 24–31, so it covers acoustic guitars too. If
that is wrong for your rig, capture a chain under `acoustic-guitar-steel` — being
more specific, it wins.

Parts with no matching chain fall back to SFLT and soundfont matching, so the
library can stay partial indefinitely. `--no-chains` ignores it entirely.

Once the projects sound right, hand them to
[`reaper2mt3`](../reaper2mt3) to render the corpus.

## Current chain library

The library itself lives outside this repo, at `~/.config/midi2reaper/chains`,
and accumulates as more songs get tuned — this is a snapshot taken
2026-08-04, not a tracked file. Regenerate it anytime with
`midi2reaper chains list`.

| Part | Chain |
| --- | --- |
| `acoustic-grand-piano` | Kontakt 8 |
| `acoustic-guitar-nylon`, `:rhythm` | Kontakt 8 |
| `acoustic-guitar-steel`, `:rhythm` | Ample Guitar M II Lite |
| `distortion-guitar` | Kontakt 8 → Guitar Rig 7 |
| `drums` | Kontakt 8 |
| `electric-bass-finger` | midi/midi_transpose → Ample Bass P Lite |
| `electric-bass-pick` | midi/midi_transpose → Ample Bass P Lite |
| `electric-grand-piano` | Kontakt 8 |
| `electric-guitar-clean`, `:rhythm` | Kontakt 8 → Guitar Rig 7 |
| `electric-guitar-jazz`, `:rhythm` | Kontakt 8 → Guitar Rig 7 |
| `fretless-bass` | midi/midi_transpose → Ample Bass P Lite |
| `overdriven-guitar`, `:rhythm` | Kontakt 8 → Guitar Rig 7 |
| `tenor-sax` | Kontakt 8 |

Family aliases: `@bass` → `electric-bass-finger`, `@guitar` → `overdriven-guitar`.

**External plugin dependencies** — installed and configured by you, this tool
only references them: Kontakt 8, Guitar Rig 7 (Native Instruments), Ample Bass
P Lite, Ample Guitar M II Lite (Ample Sound), and REAPER's bundled
`midi/midi_transpose` JS effect.

**Still on SFLT/soundfont fallback**, no chain harvested yet — mostly
orchestral and one-off parts:

| Part | Songs | Soundfont |
| --- | --- | --- |
| `clarinet` | 5 | `flutes/DCs_Mellotron_Flute.SF2` |
| `string-ensemble-1`, `violin`, `viola`, `cello`, `orchestral-harp` | 6 | `strings/String sect.sf2` |
| `rock-organ`, `drawbar-organ` | 2 | `organs/Open_Diapason_Pipe_Organ.sf2.sf2` |
| `electric-piano-1` | 1 | `pianos/9MB Piano.sf2` |
| `recorder` | 1 | `flutes/DCs_Mellotron_Flute.SF2` |
| `trombone` | 1 | `brass/ZSF_Brass_Trombone.sf2` |
| `french-horn` | 1 | `st-james-orchestra/SJO - French Horn.sf2` |
| `baritone-sax` | 1 | `brass/1276_Soft_Tenor_Sax.sf2` |
| `lead-8-bass-plus-lead` | 1 | `synths/198_d10_TEK_bass.sf2` |
| `choir-aahs`, `voice-oohs`, `synth-voice` | 1 each | `choirs/*.sf2` |
| `lead-3-calliope` | 1 | `synths/Pro53 Lead.sf2` |
| `pad-8-sweep` | 1 | `synths/HS Synth Collection I.sf2` |

`strings/String sect.sf2` covers the most parts of any single fallback — the
best next candidate for a harvested chain.

## Project-wide render defaults

Every generated project ships with master volume at **-10 dB**, and render
format **mono / 16-bit** -- tuned by ear once in Californication, then applied
to every other project by hand. New projects need neither edit again.

Bit depth and channel count are stored in two places that must agree: the
plain-text `RENDER_FMT` line and an opaque base64 blob in `RENDER_CFG`
(`src/midi2reaper/rpp.py`). The blob was decoded rather than guessed at: it
starts with the ASCII fourcc `wave` reversed, followed by a little-endian
bit-depth field. `RECORD_CFG`, which governs live recording rather than
rendering, is untouched.

To change the defaults, edit `MASTER_VOLUME_GAIN`, `RENDER_FMT_LINE` and
`RENDER_CFG_B64` at the top of `rpp.py`.

## How it works

**Soundfont matching.** The strongest signal is that many single-instrument
soundfonts declare their GM patch number: `Clean Strat.sf2` is bank 0 patch 27,
exactly "Electric Guitar (clean)". An exact patch hit outweighs any name
evidence; library category and keyword overlap resolve the rest. Parts with no
candidate above `--min-score` are skipped rather than guessed at.

**`rhythm` — guitars only, and only when unambiguous.** A guitar playing chordal
accompaniment throughout is named `<slug>:rhythm`. Everything else — guitars that
lead, guitars that mix roles, and every non-guitar — is named `<slug>` alone.

There is deliberately no `lead`. Guitars between the two roles form a continuum
with no natural cut (8 of 34 surveyed parts sit at a chord ratio of 0.30–0.60),
and only about a sixth are unambiguous leads, so a third label would add a bucket
beside the ambiguity rather than resolve it — at the cost of a three-valued
target in the MT3 codec. Absence of `:rhythm` claims only that the part is not
chordal accompaniment.

Role is inferred from content, with track names as corroboration only. Two things
it deliberately does *not* do:

- **Sustain is not polyphony.** Simultaneity is measured at note onsets. A
  fingerpicked line whose notes ring into one another is played one note at a
  time; measuring sustained overlap instead scored a purely sequential guitar
  line at 5.7 voices and pushed it to "rhythm".
- **A guitar that changes role is not rhythm.** One that plays melody in one
  section, chords in another and a solo in a third is left unannotated, because
  averaging it produces a label describing neither section. Stairway's acoustic
  guitar (45 of 58 windows chordal) and Californication's Gretsch (32 of 63) are
  both this case. The exception is a source track named `Rhythm Guitar` with
  nothing contradicting it — a human who labelled it knew what it was for.

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
