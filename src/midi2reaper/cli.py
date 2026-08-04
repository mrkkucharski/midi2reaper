"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .match import DEFAULT_MIN_SCORE
from .pipeline import build
from .sf2 import index_library

DEFAULT_LIBRARY = Path("/Users/Shared/Soundfonts")
DEFAULT_CACHE = Path.home() / ".cache" / "midi2reaper" / "library.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="midi2reaper", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build_cmd = sub.add_parser("build", help="write REAPER projects for source MIDI files")
    build_cmd.add_argument("inputs", nargs="+", type=Path, help="MIDI files or directories")
    build_cmd.add_argument("-o", "--out", type=Path, required=True, help="output directory")
    build_cmd.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    build_cmd.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    build_cmd.add_argument("--report", type=Path, help="write a JSON report of every decision")
    build_cmd.add_argument("--refresh-index", action="store_true", help="rebuild the library cache")

    index_cmd = sub.add_parser("index", help="index the soundfont library and report coverage")
    index_cmd.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    index_cmd.add_argument("--refresh-index", action="store_true")

    check_cmd = sub.add_parser("validate", help="check generated projects load a real preset")
    check_cmd.add_argument("projects", nargs="+", type=Path, help="RPP files or directories")

    data_cmd = sub.add_parser("dataset", help="render verified projects into MT3 training examples")
    data_cmd.add_argument("projects", nargs="+", type=Path, help="RPP files or directories")
    data_cmd.add_argument("-o", "--out", type=Path, required=True, help="dataset root")
    data_cmd.add_argument("--test-fraction", type=float, default=0.2)
    data_cmd.add_argument("--sample-rate", type=int, default=44100)
    data_cmd.add_argument("--tail-seconds", type=float, default=2.0)
    data_cmd.add_argument("--gain", type=float, default=0.6)
    data_cmd.add_argument("--keep-stems", action="store_true", help="retain per-part renders")

    args = parser.parse_args(argv)
    if args.command == "index":
        return _index(args)
    if args.command == "validate":
        return _validate(args)
    if args.command == "dataset":
        return _dataset(args)
    return _build(args)


def _collect_projects(items: list[Path]) -> list[Path]:
    projects: list[Path] = []
    for item in items:
        if item.is_dir():
            projects += sorted(item.rglob("*.RPP"))
        else:
            projects.append(item)
    return projects


def _dataset(args: argparse.Namespace) -> int:
    import shutil
    import tempfile

    from .dataset import assign_splits, build_example, write_manifest
    from .render import RenderError, RenderSettings, fluidsynth_version
    from .rppread import read_project

    try:
        renderer = fluidsynth_version()
    except RenderError as error:
        print(error, file=sys.stderr)
        return 2

    settings = RenderSettings(
        sample_rate=args.sample_rate, tail_seconds=args.tail_seconds, gain=args.gain
    )
    work_dir = Path(tempfile.mkdtemp(prefix="midi2reaper-"))
    examples, failures = [], 0

    paths = _collect_projects(args.projects)
    splits = assign_splits([p.stem for p in paths], args.test_fraction)

    try:
        for index, path in enumerate(paths, start=1):
            project = read_project(path)
            if not project.parts:
                print(f"SKIP    {path.name}: no SFLT parts found")
                failures += 1
                continue

            example_id = f"ex_{index:04d}"
            split = splits[project.name]
            try:
                example = build_example(project, args.out, example_id, split, settings,
                                        work_dir, renderer)
            except RenderError as error:
                print(f"FAIL    {path.name}: {error}")
                failures += 1
                continue

            examples.append(example)
            status = "OK " if not example.problems else "PROB"
            print(f"{status}    {example_id} [{split}] {path.stem[:40]:<40} "
                  f"{len(project.parts)} parts")
            for problem in example.problems:
                print(f"          {problem}")

        if examples:
            write_manifest(examples, args.out / "manifest.jsonl")
        if args.keep_stems:
            shutil.copytree(work_dir, args.out / "stems", dirs_exist_ok=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    clean = sum(1 for e in examples if not e.problems)
    print(f"\n{len(examples)} example(s) written to {args.out} "
          f"({clean} clean, {len(examples) - clean} with problems, {failures} failed)")
    return 1 if failures or clean != len(examples) else 0


def _validate(args: argparse.Namespace) -> int:
    from .validate import validate_project

    projects: list[Path] = []
    for item in args.projects:
        if item.is_dir():
            projects += sorted(item.rglob("*.RPP"))
        else:
            projects.append(item)

    problems = [p for project in projects for p in validate_project(project)]
    for problem in problems:
        print(problem)
    print(f"{len(projects)} project(s) checked, {len(problems)} problem(s)")
    return 1 if problems else 0


def _collect(inputs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        if item.is_dir():
            files += sorted(p for p in item.rglob("*") if p.suffix.lower() == ".mid")
        else:
            files.append(item)
    return files


def _index(args: argparse.Namespace) -> int:
    library = index_library(args.library, DEFAULT_CACHE, refresh=args.refresh_index)
    print(f"{len(library.soundfonts)} soundfonts, {library.preset_count} presets")
    categories: dict[str, int] = {}
    for soundfont in library.soundfonts:
        categories[soundfont.category] = categories.get(soundfont.category, 0) + 1
    for category, count in sorted(categories.items()):
        print(f"  {category:<24} {count}")
    if library.unreadable:
        print(f"\nunreadable ({len(library.unreadable)}):")
        for name in library.unreadable:
            print(f"  {name}")
    return 0


def _build(args: argparse.Namespace) -> int:
    library = index_library(args.library, DEFAULT_CACHE, refresh=args.refresh_index)
    if not library.soundfonts:
        print(f"no soundfonts under {args.library}", file=sys.stderr)
        return 2

    report = []
    accepted = 0
    for path in _collect(args.inputs):
        result = build(path, library, args.min_score)
        if not result.accepted:
            print(f"REJECT  {path.name}: {result.rejection}")
            report.append({"source": str(path), "rejected": result.rejection})
            continue

        out_path = args.out / f"{path.stem}.RPP"
        from .rpp import write_project

        write_project(result.song, result.parts, out_path)
        accepted += 1
        print(f"OK      {path.name} -> {out_path.name} ({len(result.parts)} parts, "
              f"{len(result.skipped)} skipped)")
        for part, meta in zip(result.parts, result.manifest_parts):
            print(f"          {part.track_name:<34} {meta['soundfont']} "
                  f"(bank {meta['bank']} patch {meta['patch']})")
        for skip in result.skipped:
            print(f"          skipped: {skip.name} — {skip.reason}")

        report.append(
            {
                "source": str(path),
                "project": str(out_path),
                "parts": result.manifest_parts,
                "skipped": [{"name": s.name, "reason": s.reason} for s in result.skipped],
            }
        )

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2))
        print(f"\nreport written to {args.report}")

    print(f"\n{accepted} project(s) written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
