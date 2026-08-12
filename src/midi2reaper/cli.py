"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .chains import DEFAULT_LIBRARY as DEFAULT_CHAINS, ChainLibrary, extract_from_project
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
    build_cmd.add_argument("--chains", type=Path, default=DEFAULT_CHAINS,
                           help="FX chain library to splice instruments from")
    build_cmd.add_argument("--no-chains", action="store_true", help="always use SFLT")
    build_cmd.add_argument("-f", "--force", action="store_true",
                           help="overwrite projects that already exist")

    job_cmd = sub.add_parser("render-job", help="build one deterministic, pinned renderer job")
    job_cmd.add_argument("--job", required=True, type=Path, help="versioned render-job JSON")
    job_cmd.add_argument("-o", "--out", required=True, type=Path, help="output RPP path")
    job_cmd.add_argument("--result", required=True, type=Path, help="versioned build-result JSON")
    job_cmd.add_argument("-f", "--force", action="store_true", help="overwrite an existing project")

    index_cmd = sub.add_parser("index", help="index the soundfont library and report coverage")
    index_cmd.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    index_cmd.add_argument("--refresh-index", action="store_true")

    check_cmd = sub.add_parser("validate", help="check generated projects load a real preset")
    check_cmd.add_argument("projects", nargs="+", type=Path, help="RPP files or directories")

    chain_cmd = sub.add_parser("chains", help="manage the FX chain library")
    chain_sub = chain_cmd.add_subparsers(dest="chain_command", required=True)

    extract_cmd = chain_sub.add_parser("extract", help="harvest chains from tuned projects")
    extract_cmd.add_argument("projects", nargs="+", type=Path)
    extract_cmd.add_argument("--chains", type=Path, default=DEFAULT_CHAINS)
    extract_cmd.add_argument("--keep-existing", action="store_true",
                             help="do not replace chains already in the library")
    extract_cmd.add_argument("--keep-sflt", action="store_true",
                             help="retain SFLT instances instead of dropping them")

    list_cmd = chain_sub.add_parser("list", help="show the chain library")
    list_cmd.add_argument("--chains", type=Path, default=DEFAULT_CHAINS)

    alias_cmd = chain_sub.add_parser(
        "alias", help="reuse a chain under another key, e.g. @bass or @guitar")
    alias_cmd.add_argument("key", help="new key, such as @bass")
    alias_cmd.add_argument("source", help="existing key to point at")
    alias_cmd.add_argument("--chains", type=Path, default=DEFAULT_CHAINS)

    args = parser.parse_args(argv)
    if args.command == "index":
        return _index(args)
    if args.command == "validate":
        return _validate(args)
    if args.command == "chains":
        return _chains(args)
    if args.command == "render-job":
        from .render_job import JobError, run
        try:
            return run(args.job, args.out, args.result, force=args.force)
        except JobError as error:
            print(f"render-job rejected: {error}", file=sys.stderr)
            return 2
    return _build(args)


def _chains(args: argparse.Namespace) -> int:
    library = ChainLibrary(args.chains)
    if args.chain_command == "list":
        if not library:
            print(f"no chains in {args.chains}")
            return 0
        print(f"{len(library)} key(s) in {args.chains}\n")
        for key in library.keys():
            entries = library.index[key]
            suffix = "" if len(entries) == 1 else f"  ({len(entries)} variants)"
            print(f"  {key}{suffix}")
            for entry in entries:
                print(f"    {' -> '.join(entry['plugins'])}")
                origin = (f"alias of {entry['aliased_from']}" if "aliased_from" in entry
                          else f"from {entry['source_project']} ({entry['extracted_at']})")
                print(f"    {origin}")
        return 0

    if args.chain_command == "alias":
        if not library.alias(args.key, args.source):
            print(f"no chain named {args.source}", file=sys.stderr)
            return 2
        library.save()
        print(f"{args.key} -> {args.source}")
        return 0

    projects: list[Path] = []
    for item in args.projects:
        projects += sorted(item.rglob("*.RPP")) if item.is_dir() else [item]

    total = 0
    for path in projects:
        for chain in extract_from_project(path, drop_sflt=not args.keep_sflt):
            action = library.add(chain, replace=not args.keep_existing)
            total += action != "kept"
            print(f"{action:<8} {chain.key:<32} {' -> '.join(chain.plugins)}  [{path.name}]")
    library.save()
    print(f"\n{total} chain(s) written; library now holds {len(library)} at {args.chains}")
    return 0


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

    chains = None if args.no_chains else ChainLibrary(args.chains)
    if chains and len(chains):
        print(f"using {len(chains)} chain(s) from {args.chains}\n")

    report = []
    accepted = skipped_existing = 0
    for path in _collect(args.inputs):
        out_path = args.out / f"{path.stem}.RPP"
        # Generated projects get hand-tuned in REAPER, so overwriting one is
        # destructive in a way rebuilding a fresh project is not.
        if out_path.exists() and not args.force:
            print(f"EXISTS  {out_path.name} — not overwritten (pass --force)")
            skipped_existing += 1
            continue

        result = build(path, library, args.min_score, chains)
        if not result.accepted:
            print(f"REJECT  {path.name}: {result.rejection}")
            report.append({"source": str(path), "rejected": result.rejection})
            continue

        from .rpp import write_project

        write_project(result.song, result.parts, out_path)
        accepted += 1
        print(f"OK      {path.name} -> {out_path.name} ({len(result.parts)} parts, "
              f"{len(result.skipped)} skipped)")
        for part, meta in zip(result.parts, result.manifest_parts):
            source = (f"chain:{meta['chain']}" if meta["chain"]
                      else f"{meta['soundfont']} (bank {meta['bank']} patch {meta['patch']})")
            print(f"          {part.track_name:<34} {source}")
            if meta["chain_variant"]:
                print(f"          {'':<34} variant: {meta['chain_variant']}")
            if part.range_warning:
                print(f"          WARN  {part.range_warning.detail}")
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

    print(f"\n{accepted} project(s) written to {args.out}"
          + (f", {skipped_existing} left untouched" if skipped_existing else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
