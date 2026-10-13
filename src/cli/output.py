"""Format engine answers for terminal output."""

def render(result: dict) -> str:
    """Format a query result the way the CLI prints answers.

    Ground questions answer with a truth value; open questions answer with
    rows. Both are answers -- an empty binding list is not a failure.
    """
    status = result.get("status")
    if status != "bindings":
        return str(status)
    rows = result.get("bindings") or []
    if not rows:
        return "(no bindings)"
    out = []
    for row in rows:
        out.append(", ".join(f"{k} = {v}" for k, v in sorted(row.items())))
    return "\n".join(out)
