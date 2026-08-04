# midi2reaper

Turns multi-track MIDI arrangements into REAPER projects where every part is
loaded into its own [SFLT](https://estrobiologist.gumroad.com/) SoundFont player
instance, ready to audition and render.

It exists to build training data for the MT3 fine-tune in `../DATA_CONTRACT.md`,
which this tool implements: guitar anchors corpus membership, vocals are
rendered as instruments rather than skipped, unresolvable tracks are dropped,
and tracks that change program mid-song are split into one part per program.

## Usage

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e .

.venv/bin/midi2reaper index                            # library coverage
.venv/bin/midi2reaper build ../reaper/midi-src -o out --report out/report.json
.venv/bin/midi2reaper validate out                     # every preset resolvable
```

`build` writes one `.RPP` per accepted source file, plus a JSON report
recording every match, its score and the evidence behind each rhythm/lead call.
Open a project in REAPER and press play — no further setup is needed.

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
- Rendering to audio is not implemented. `build` stops at a project you can
  open, verify and play.
