"""Running FrameX queries against the logic base on disk.

These stateless helpers use the `framex` CLI, loading the base for each query.
The default application path uses Session instead, sending data in bounded
chunks and reusing one loaded engine for a batch of queries.

Queries are passed to the engine as written, in F-Script form. The engine is
the authority on syntax, so a malformed query gets the engine's own diagnostic
rather than a guess from here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

#: The engine also accepts a bare goal, but `?- goal.` is the documented query
#: form and what the tutorial teaches, so both are allowed through unchanged.
DEFAULT_TIMEOUT = 300

#: The engine's default proof limit is tuned for small programs and is too low
#: here: `servedByTrip` alone is proved once per trip serving a station, and
#: hundreds of trips serve a large station, so a station fact legitimately has
#: hundreds of proofs. Without raising this, a non-stop query fails with
#: "proof limit reached; simplify alternatives or raise --max-proofs" rather
#: than answering. Raising it costs nothing when proofs are few.
DEFAULT_MAX_PROOFS = 100_000


class QueryError(RuntimeError):
    pass


def binary() -> str:
    found = shutil.which("framex")
    if not found:
        raise QueryError(
            "the `framex` engine is not on PATH.\n"
            "Build it with `cargo build --release` or `cargo install framex`, "
            "or put the binary somewhere on PATH."
        )
    return found


def logic_files(workspace: Path) -> list[Path]:
    manifest_path = workspace / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format_version") != 2:
            raise QueryError(
                "this generated base uses the old ontology; rebuild with --init "
                "(prefer a new --workspace) before querying it with this version"
            )
    files = sorted(workspace.glob("*.fx"))
    if not files:
        raise QueryError(
            f"no .fx files in {workspace}/ -- run --init to create the logic base"
        )
    return files


def run(workspace: Path, query: str, command: str = "query",
        timeout: int = DEFAULT_TIMEOUT,
        max_proofs: int = DEFAULT_MAX_PROOFS) -> subprocess.CompletedProcess:
    """Run one engine command over every .fx file in the workspace."""
    files = [str(p) for p in logic_files(workspace)]
    argv = [binary(), command]
    if max_proofs:
        argv += ["--max-proofs", str(max_proofs)]
    argv += files
    if query:
        argv.append(query)
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        raise QueryError(
            f"the engine did not answer within {timeout}s.\n"
            "Unbounded recursion is the usual cause -- reachability in this model "
            "is bounded (reachDirect / reach1 / reach2) for exactly that reason."
        ) from None

