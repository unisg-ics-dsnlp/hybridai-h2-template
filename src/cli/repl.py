"""Interactive querying against a loaded session.

The initial load and closure are shared across questions. Query cost still
depends on its bindings and joins; broad queries can remain expensive.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .progress import Progress
from runtime.session import DEFAULT_LOAD_TIMEOUT, Session
from .output import render
from runtime.query import DEFAULT_MAX_PROOFS

BANNER = """\
FrameX session over {files} logic files in {workspace}/
{stats}

Type a FrameX query and press enter. Queries may omit the `?- ` and the `.`:

  sp_8509000[hasWifi -> ?W]          does Chur have WiFi?
  sp_8506302[nonStopTo -> ?B]        non-stop from St. Gallen?
  sp_8509000:TrainStopPoint          is it in scope?

Commands:  :explain <fact>   why is this true?
           :warnings         engine warnings from the load
           :stats            fixpoint statistics
           :help             this text
           :quit             exit (or Ctrl-D)
"""

HELP = BANNER.split("Type a FrameX query", 1)[1]


def _handle_command(session: Session, line: str) -> bool:
    """Run a `:` command. Returns False when the session should end."""
    name, _, argument = line[1:].partition(" ")
    name, argument = name.strip().lower(), argument.strip()

    if name in {"quit", "exit", "q"}:
        return False
    if name in {"help", "h", "?"}:
        print("Type a FrameX query" + HELP)
    elif name == "explain":
        if not argument:
            print("usage: :explain <ground fact>, e.g. "
                  ":explain sp_8509000[hasWifi -> true]")
        else:
            try:
                print(session.explain(argument))
            except Exception as exc:  # the engine's own diagnostic is the answer
                print(f"error: {exc}")
    elif name == "warnings":
        warnings = session.warnings()
        if not warnings:
            print("(none)")
        for warning in warnings:
            print(f"{warning.get('kind')}: {warning.get('message')}")
    elif name == "stats":
        for key, value in sorted(session.stats.items()):
            print(f"  {key}: {value}")
        print(f"  load_seconds: {session.load_seconds:.1f}")
    else:
        print(f"unknown command :{name} -- try :help")
    return True


def run(workspace: Path, *, binary: str = "framex", timeout: float = 900,
        load_timeout: float = DEFAULT_LOAD_TIMEOUT,
        max_proofs: int = DEFAULT_MAX_PROOFS, quiet: bool = False) -> int:
    """Load the workspace once, then answer questions until the user stops."""
    reporter = Progress(enabled=False if quiet else None)
    try:
        session = Session(workspace, binary=binary, progress=reporter, timeout=timeout,
                          load_timeout=load_timeout, max_proofs=max_proofs)
    except Exception as exc:
        reporter.close()
        print(f"error: {exc}", file=sys.stderr)
        return 1
    reporter.close()

    with session:
        stats = (
            f"{session.stats.get('derived_facts', 0):,} derived facts, "
            f"ready in {session.load_seconds:.1f}s"
        )
        print()
        print(BANNER.format(files=len(session.files), workspace=workspace, stats=stats))

        while True:
            try:
                line = input("framex> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line:
                continue
            if line.startswith(":"):
                if not _handle_command(session, line):
                    return 0
                continue
            try:
                result, seconds = session.ask(line)
            except Exception as exc:
                print(f"error: {exc}")
                continue
            print(render(result))
            print(f"[{seconds * 1000:.1f} ms]")
