"""Build and query a FrameX ontology of the Swiss rail passenger network.

    uv run python src/main.py --init
    uv run python src/main.py --add wifistation
    uv run python src/main.py --query '?- sp_8509000[hasWifi -> ?W].'
    uv run python src/main.py --help
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from data import load_dataset
from ontology import builder, registry
from runtime import query
from runtime.session import DEFAULT_LOAD_TIMEOUT, Session
from runtime.validation import validation_issues

from . import repl, stateless
from .output import render
from .progress import Progress

DEFAULT_WORKSPACE = Path("fx")

EPILOG = """
examples:
  --init                       pull every dataset and build the logic base
  --init --only wifistation    build the spine plus one dataset (quick)
  --add perron                 attach one dataset to an existing base
  --query '?- sp_8509000[hasWifi -> ?W].'      does Chur have WiFi?
  --query '?- sp_8506302[nonStopTo -> ?B].'    non-stop from St. Gallen?
  --query Q1 Q2 Q3             several queries, one loaded session
  --validate                   run schema and constraint validation
  --repl                       load once, then ask interactively (fastest)
                               for many questions
  --datasets                   list the datasets this tool knows

queries are FrameX F-Script, answered against every .fx file in the workspace.
station objects are named sp_<bpuic>, e.g. Chur is sp_8509000.
"""


def confirm_overwrite(workspace: Path, assume_yes: bool) -> bool:
    """Refuse to clobber an existing logic base without saying so."""
    existing = builder.existing_logic(workspace)
    if not existing:
        return True

    print(f"error: {workspace}/ already contains a FrameX logic base "
          f"({len(existing)} .fx files).", file=sys.stderr)
    if assume_yes:
        print("  --yes given: overwriting.", file=sys.stderr)
        return True
    if not sys.stdin.isatty():
        print("  Re-run with --yes to overwrite, or use --workspace to build "
              "somewhere else.", file=sys.stderr)
        return False
    answer = input("  Overwrite it? [y/N] ").strip().lower()
    if answer in {"y", "yes"}:
        return True
    print("  Left unchanged.", file=sys.stderr)
    return False


def do_init(args) -> int:
    workspace = args.workspace
    if not confirm_overwrite(workspace, args.yes):
        return 1
    datasets = args.only or None
    manifest = builder.init(workspace, load_dataset, datasets=datasets)

    failed = [name for name, info in manifest["layers"].items()
              if info.get("status") != "ok"]
    total = sum(info.get("statements", 0) for info in manifest["layers"].values())
    print(f"\nLogic base ready in {workspace}/")
    print(f"  {len(manifest['spine']['stop_points'])} stop points, "
          f"{len(manifest['layers']) - len(failed)} layers, {total} layer statements")
    if failed:
        print(f"  {len(failed)} dataset(s) failed: {', '.join(failed)}")
    print("\nTry:  uv run python src/main.py "
          "--query '?- sp_8509000[hasWifi -> ?W].'")
    return 1 if failed else 0


def do_add(args) -> int:
    builder.add(args.workspace, args.add, load_dataset)
    return 0


def do_query(args) -> int:
    """Answer one or more queries through a loaded session.

    Staged bulk loading builds the initial knowledge base once;
    subsequent queries reuse it. --stateless keeps the CLI path available for
    comparison, loading the base separately for each query.
    """
    queries = args.query
    if args.stateless:
        failed = 0
        for text in queries:
            failed |= stateless.ask(args.workspace, text, timeout=args.timeout,
                                max_proofs=args.max_proofs)
        return failed

    reporter = Progress(enabled=None if not args.quiet else False)
    session = Session(args.workspace, progress=reporter, timeout=args.timeout,
                      load_timeout=args.load_timeout, max_proofs=args.max_proofs)
    with session:
        reporter.close()
        print(file=sys.stderr)
        failed = 0
        for text in queries:
            # Echo the query as the user wrote it. Note lstrip("?- ") would
            # eat the leading ? of a variable like ?P.
            shown = text.strip()
            if shown.startswith("?-"):
                shown = shown[2:].strip()
            print(f"?- {shown}")
            try:
                result, seconds = session.ask(text)
            except Exception as exc:
                print(f"  error: {exc}")
                failed += 1
                continue
            for line in render(result).splitlines():
                print(f"  {line}")
            print(f"  [{seconds * 1000:.1f} ms]")
        return 1 if failed else 0


def do_repl(args) -> int:
    return repl.run(args.workspace, timeout=args.timeout, load_timeout=args.load_timeout,
                    max_proofs=args.max_proofs, quiet=args.quiet)


def do_validate(args) -> int:
    with Session(args.workspace, timeout=args.timeout, max_proofs=args.max_proofs,
                 load_timeout=args.load_timeout,
                 progress=Progress(enabled=False if args.quiet else None)) as session:
        result = session.validate()
    issues = validation_issues(result)
    print(f"{len(result.get('schema_checks', []))} schema checks, {len(issues)} issues")
    for issue in issues:
        print(issue)
    return int(bool(issues))


def do_journey(args) -> int:
    if not args.day or not args.not_before or args.transfer_seconds is None:
        raise query.QueryError("--journey requires --day, --not-before and --transfer-seconds")
    with Session(args.workspace, timeout=args.timeout, max_proofs=args.max_proofs,
                 load_timeout=args.load_timeout,
                 progress=Progress(enabled=False if args.quiet else None)) as session:
        request = session.prepare_journey(args.journey, args.day,
            not_before=args.not_before, transfer_seconds=args.transfer_seconds)
        print(f"Request: {request}")
        print("Reported journeys using actual times and the supplied transfer allowance:")
        for relation in ("reachDirect", "reach1", "reach2", "changeAt(?Via)"):
            result, _ = session.ask(f"?- {request}[{relation} -> ?Destination].")
            print(f"{relation}: {render(result)}")
    return 0


def do_datasets(args) -> int:
    print(f"spine (rebuilt by --init):\n  {registry.SPINE.dataset}")
    print("\nlayers (attach with --add):")
    for name in sorted(registry.LAYERS):
        spec = registry.LAYERS[name]
        print(f"  {name:<48} {type(spec).__name__}")
    print(f"  {registry.SERVICE_DATASET:<48} Service (trips and stop events)")
    print(f"\n--init pulls: {', '.join(registry.INIT_DATASETS)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Build and query a FrameX ontology of the Swiss rail "
                    "passenger-transport network.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--validate", "--check", action="store_true",
                        help="run engine schema and constraint validation (--check is an alias)")
    action.add_argument("--journey", type=int, metavar="BPUIC", help="query dated journeys from a station")
    action.add_argument(
        "--init", action="store_true",
        help="pull the datasets and create the FrameX logic files from scratch; "
             "errors if a logic base already exists",
    )
    action.add_argument(
        "--add", metavar="DATASET",
        help="pull one dataset and attach it to the existing logic base",
    )
    action.add_argument(
        "--query", metavar="QUERY", nargs="+",
        help="ask one or more FrameX F-Script queries against the logic base, "
             "e.g. '?- sp_8509000[hasWifi -> ?W].'. All queries share one "
             "loaded session, so the derivation cost is paid once",
    )
    action.add_argument(
        "--repl", action="store_true",
        help="load the logic base once, then answer queries interactively "
             "in milliseconds (the fast way to explore)",
    )
    action.add_argument(
        "--datasets", action="store_true",
        help="list the datasets this tool knows and how each is mapped",
    )

    parser.add_argument("--day", help="operating day for --journey (YYYY-MM-DD)")
    parser.add_argument("--not-before", help="earliest departure ISO timestamp for --journey")
    parser.add_argument("--transfer-seconds", type=int, help="explicit minimum transfer allowance")
    parser.add_argument(
        "--workspace", type=Path, default=DEFAULT_WORKSPACE, metavar="DIR",
        help=f"directory holding the .fx logic base (default: {DEFAULT_WORKSPACE}/)",
    )
    parser.add_argument(
        "--only", nargs="+", metavar="DATASET",
        help="with --init, build the spine plus only these datasets",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="with --init, overwrite an existing logic base without asking",
    )
    parser.add_argument(
        "--timeout", type=int, default=query.DEFAULT_TIMEOUT, metavar="SECONDS",
        help=f"query timeout (default: {query.DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--load-timeout", type=int, default=DEFAULT_LOAD_TIMEOUT, metavar="SECONDS",
        help=f"timeout for each request while loading the complete logic base "
             f"(default: {DEFAULT_LOAD_TIMEOUT})",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="suppress the loading progress bar (it already goes to stderr, so "
             "piping stdout is unaffected either way)",
    )
    parser.add_argument(
        "--stateless", action="store_true",
        help="answer each --query through the one-shot framex CLI "
             "instead of a loaded session. Reloads and derives the complete "
             "base for every query and shows no loading progress; kept for "
             "comparison",
    )
    parser.add_argument(
        "--max-proofs", type=int, default=query.DEFAULT_MAX_PROOFS, metavar="N",
        help="engine proof limit per fact. A station fact can legitimately have "
             "hundreds of proofs (one per trip serving it), so the engine's own "
             f"default is too low here (default: {query.DEFAULT_MAX_PROOFS})",
    )
    return parser


def args_label(args) -> str:
    """Which subcommand ran, for the total-timing log line."""
    for flag in ("init", "add", "query", "validate", "journey", "repl", "datasets"):
        if getattr(args, flag, None) is not None and getattr(args, flag) is not False:
            return f"--{flag}"
    return "main.py"


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    started = time.time()
    try:
        if args.validate:
            return do_validate(args)
        if args.journey is not None:
            return do_journey(args)
        if args.init:
            return do_init(args)
        if args.add:
            return do_add(args)
        if args.query:
            return do_query(args)
        if args.repl:
            return do_repl(args)
        if args.datasets:
            return do_datasets(args)
        return 0
    except (builder.BuildError, query.QueryError, ValueError, TimeoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    finally:
        # For --repl this spans the whole interactive session (load time plus
        # however long you spent typing), not just the load -- see the
        # session's own "Ready in Xs" line, printed once loading finishes,
        # for a clean load-only number to compare across machines.
        elapsed = time.time() - started
        print(f"[timing] {args_label(args)} total: {elapsed:.2f}s", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
