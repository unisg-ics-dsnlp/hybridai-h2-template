"""Print answers from the one-shot FrameX command."""
from pathlib import Path

from runtime.query import DEFAULT_MAX_PROOFS, DEFAULT_TIMEOUT, logic_files, run

def ask(workspace: Path, query: str, timeout: int = DEFAULT_TIMEOUT,
        max_proofs: int = DEFAULT_MAX_PROOFS) -> int:
    """Answer one query, printing the engine's output. Returns an exit code."""
    files = logic_files(workspace)
    print(f"Querying {len(files)} logic files in {workspace}/")
    print(f"  {query}\n")
    result = run(workspace, query, timeout=timeout, max_proofs=max_proofs)
    out = result.stdout.strip()
    err = result.stderr.strip()
    if out:
        print(out)
    if err:
        print(err)
    if not out and not err and result.returncode == 0:
        # A query with no rows is an answer, not a malfunction.
        print("(no bindings)")
    return result.returncode
