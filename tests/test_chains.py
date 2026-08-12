import json
import re

import pytest

from midi2reaper import gm
from midi2reaper.chains import (
    ChainLibrary,
    Chain,
    extract_from_project,
    family_key,
    freshen_ids,
    plugin_name,
    split_entries,
)

CHAIN = """      WNDRECT 0 702 724 190
      SHOW 0
      LASTSEL 1
      DOCKED 0
      BYPASS 1 0 0
      <VST "VST3i: SFLT (ash taylor) (34 out)" SFLT.vst3 0 "" 955354538{4} ""
        AAAA
      >
      FLOATPOS 0 0 0 0
      FXID {AAAAAAAA-0000-0000-0000-000000000001}
      WAK 0 0
      BYPASS 0 0 0
      <JS midi/midi_transpose ""
        12 1 0 127 - -
      >
      FLOATPOS 0 0 0 0
      FXID {AAAAAAAA-0000-0000-0000-000000000002}
      WAK 0 0
      BYPASS 0 0 0
      <VST "VSTi: Ample Bass P Lite (Ample Sound)" ABPL.vst 0 "" 1096970348<5> ""
        BBBB
      >
      FLOATPOS 0 0 0 0
      FXID {AAAAAAAA-0000-0000-0000-000000000003}
      WAK 0 0""".split("\n")


def test_split_entries_separates_header_and_plugins():
    header, entries = split_entries(CHAIN)
    assert [l.strip() for l in header] == ["WNDRECT 0 702 724 190", "SHOW 0", "LASTSEL 1", "DOCKED 0"]
    assert len(entries) == 3
    # each entry keeps its own bypass state and trailing FXID/WAK
    assert entries[0][0].strip() == "BYPASS 1 0 0"
    assert any("WAK" in l for l in entries[0])


def test_plugin_name_reads_quoted_and_bare_forms():
    _, entries = split_entries(CHAIN)
    assert [plugin_name(e) for e in entries] == [
        "SFLT (ash taylor) (34 out)",
        "midi/midi_transpose",
        "Ample Bass P Lite (Ample Sound)",
    ]


def _project(tmp_path, chain_lines, track="overdriven-guitar:rhythm | Ritchie"):
    text = "\n".join(
        ['<REAPER_PROJECT 0.1 "7.74" 0 0', "  <TRACK {G}", f'    NAME "{track}"',
         "    <FXCHAIN"] + chain_lines + ["    >", "  >", ">"]
    )
    path = tmp_path / "donor.RPP"
    path.write_text(text)
    return path


def test_extract_drops_sflt_by_default(tmp_path):
    """SFLT is the fallback a chain exists to replace, so it is never carried."""
    chains = extract_from_project(_project(tmp_path, CHAIN))
    assert len(chains) == 1
    assert "SFLT" not in " ".join(chains[0].plugins)
    assert chains[0].plugins == ["midi/midi_transpose", "Ample Bass P Lite (Ample Sound)"]


def test_extract_can_retain_sflt(tmp_path):
    chains = extract_from_project(_project(tmp_path, CHAIN), drop_sflt=False)
    assert len(chains[0].plugins) == 3


def test_extract_keys_by_canonical_part_name(tmp_path):
    assert extract_from_project(_project(tmp_path, CHAIN))[0].key == "overdriven-guitar:rhythm"
    path = _project(tmp_path, CHAIN, track="electric-bass-finger | Roger Glover")
    assert extract_from_project(path)[0].key == "electric-bass-finger"


def test_extract_ignores_tracks_without_canonical_names(tmp_path):
    assert extract_from_project(_project(tmp_path, CHAIN, track="Some Random Track")) == []


def test_freshen_ids_makes_every_instance_unique():
    once = freshen_ids(CHAIN)
    twice = freshen_ids(CHAIN)
    ids = re.findall(r"FXID \{([^}]+)\}", "\n".join(once + twice))
    assert len(ids) == 6
    assert len(set(ids)) == 6


def test_freshen_ids_leaves_everything_else_untouched():
    assert [l for l in freshen_ids(CHAIN) if "FXID" not in l] == [
        l for l in CHAIN if "FXID" not in l
    ]


@pytest.mark.parametrize(
    "track,expected",
    [
        ("overdriven-guitar:rhythm", ["overdriven-guitar:rhythm", "overdriven-guitar", "@guitar"]),
        ("electric-bass-pick", ["electric-bass-pick", "@bass"]),
        ("drums", ["drums", "@drums"]),
        ("tenor-sax", ["tenor-sax", "@reed"]),
    ],
)
def test_resolution_ladder(track, expected):
    assert ChainLibrary.candidates(track) == expected


def test_family_covers_sibling_programs():
    """GM splits bass finely -- finger and pick are different programs but the
    same plugin -- so a family alias has to reach across them."""
    assert gm.family_of(33) == gm.family_of(34) == "bass"
    assert family_key("bass") in ChainLibrary.candidates("electric-bass-pick")


def test_library_resolves_most_specific_first(tmp_path):
    library = ChainLibrary(tmp_path)
    library.add(Chain(key="overdriven-guitar", lines=["a"], plugins=["X"]))
    library.add(Chain(key="overdriven-guitar:rhythm", lines=["b"], plugins=["Y"]))

    assert library.resolve("overdriven-guitar:rhythm")[0] == "overdriven-guitar:rhythm"
    assert library.resolve("overdriven-guitar")[0] == "overdriven-guitar"


def test_library_falls_back_to_family(tmp_path):
    library = ChainLibrary(tmp_path)
    library.add(Chain(key="electric-bass-finger", lines=["a"], plugins=["Ample"]))
    assert library.resolve("electric-bass-pick") is None

    library.alias(family_key("bass"), "electric-bass-finger")
    key, lines, entry = library.resolve("electric-bass-pick")
    assert key == "@bass" and lines == ["a"]
    assert entry["aliased_from"] == "electric-bass-finger"


def test_alias_of_missing_chain_is_refused(tmp_path):
    assert ChainLibrary(tmp_path).alias("@bass", "nothing-here") is False


def test_library_round_trips_through_disk(tmp_path):
    library = ChainLibrary(tmp_path)
    library.add(Chain(key="drums", lines=["x", "y"], plugins=["BeatBuddy"]))
    library.save()

    assert ChainLibrary(tmp_path).get("drums") == ["x", "y"]


def test_keep_existing_does_not_replace(tmp_path):
    library = ChainLibrary(tmp_path)
    library.add(Chain(key="drums", lines=["old"], plugins=["A"]))
    assert library.add(Chain(key="drums", lines=["new"], plugins=["B"]), replace=False) == "kept"
    assert library.get("drums") == ["old"]
    assert library.add(Chain(key="drums", lines=["new"], plugins=["B"])) == "updated"
    assert library.get("drums") == ["new"]


def test_add_new_source_track_appends_a_variant(tmp_path):
    """A different `source_track` under the same key is a new variant, not a replace."""
    library = ChainLibrary(tmp_path)
    library.add(Chain(key="overdriven-guitar", lines=["a"], plugins=["X"], source_track="overdriven-guitar|v1"))
    action = library.add(
        Chain(key="overdriven-guitar", lines=["b"], plugins=["Y"], source_track="overdriven-guitar|v2")
    )
    assert action == "added"
    assert len(library.variants("overdriven-guitar")) == 2
    # each variant keeps its own file, so neither clobbers the other on disk
    files = {v["file"] for v in library.variants("overdriven-guitar")}
    assert len(files) == 2
    assert library.get("overdriven-guitar", choose=lambda vs: vs[0])[0] == "a"
    assert library.get("overdriven-guitar", choose=lambda vs: vs[1])[0] == "b"


def test_resolve_uses_choose_to_pick_a_variant(tmp_path):
    library = ChainLibrary(tmp_path)
    library.add(Chain(key="drums", lines=["a"], plugins=["A"], source_track="drums|v1"))
    library.add(Chain(key="drums", lines=["b"], plugins=["B"], source_track="drums|v2"))

    assert library.resolve("drums", choose=lambda vs: vs[0])[1] in (["a"], ["b"])
    first = library.resolve("drums", choose=min_by_file)[1]
    second = library.resolve("drums", choose=max_by_file)[1]
    assert {tuple(first), tuple(second)} == {("a",), ("b",)}


def test_resolve_reports_which_variant_it_picked(tmp_path):
    """The picked variant's `source_track` is per-build provenance: which
    tuning of the instrument a part actually got."""
    library = ChainLibrary(tmp_path)
    library.add(Chain(key="drums", lines=["a"], plugins=["A"], source_track="drums|v1"))
    library.add(Chain(key="drums", lines=["b"], plugins=["B"], source_track="drums|v2"))

    key, lines, entry = library.resolve("drums", choose=min_by_file)
    assert key == "drums"
    assert entry["source_track"] == "drums|v1"
    assert lines == ["a"]

    key, lines, entry = library.resolve("drums", choose=max_by_file)
    assert entry["source_track"] == "drums|v2"
    assert lines == ["b"]


def min_by_file(variants):
    return min(variants, key=lambda v: v["file"])


def max_by_file(variants):
    return max(variants, key=lambda v: v["file"])


def test_alias_carries_every_variant(tmp_path):
    library = ChainLibrary(tmp_path)
    library.add(Chain(key="electric-bass-finger", lines=["a"], plugins=["A"], source_track="v1"))
    library.add(Chain(key="electric-bass-finger", lines=["b"], plugins=["B"], source_track="v2"))

    assert library.alias(family_key("bass"), "electric-bass-finger")
    assert len(library.variants(family_key("bass"))) == 2
    assert all(v.get("aliased_from") == "electric-bass-finger" for v in library.variants(family_key("bass")))


def test_old_single_entry_index_migrates_to_list_on_load(tmp_path):
    (tmp_path / "index.json").write_text(json.dumps({
        "drums": {"file": "drums.rfxchain", "plugins": ["A"], "source_project": "", "source_track": "", "extracted_at": ""}
    }))
    (tmp_path / "drums.rfxchain").write_text("x")

    library = ChainLibrary(tmp_path)
    assert library.variants("drums") == [
        {"file": "drums.rfxchain", "plugins": ["A"], "source_project": "", "source_track": "", "extracted_at": ""}
    ]
    assert library.get("drums") == ["x"]
