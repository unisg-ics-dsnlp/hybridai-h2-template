# Usage

[Project overview](../README.md) · [Ontology semantics](ontology.md) · [Development](development.md)

Run commands from the repository root after `uv sync`.
`uv run python -m cli` accepts the same arguments as
`uv run python src/main.py`. Use `--help` for all options.

## Build and refresh a workspace

```bash
uv run python src/main.py --datasets
uv run python src/main.py --init
```

`--datasets` lists sources supported by the ontology. Initialization builds
the stop-point registry and configured layers in `fx/`. For a small build:

```bash
uv run python src/main.py --init --only wifistation --workspace fx-demo
uv run python src/main.py --add perron --workspace fx-demo
```

Refresh an existing workspace with `--init --yes`. To inspect a rebuild
before selecting it, use a separate workspace:

```bash
uv run python src/main.py --init --workspace fx-next
uv run python src/main.py --repl --workspace fx-next
```

Workspaces contain copies of rules and downloaded facts. Updating Python or
source rules does not update existing workspaces; rebuild to apply mapping
or rule changes. Queries and `--add` reject incompatible manifests.
Use one writer per workspace.

## Query and validate

```bash
uv run python src/main.py --query '?- sp_8509000[hasWifi -> ?W].'
uv run python src/main.py --query \
  '?- sp_8507000[servedByCategory -> ?C].' \
  '?- sp_8507000[nonStopTo -> ?B] AND ?B[designation -> ?N].'
uv run python src/main.py --validate --timeout 900
```

`--query` answers queries; `--validate` runs schema and constraint validation.
`--check` remains an alias for `--validate`. Older commands using
`--validate QUERY` must be changed to `--query QUERY`.
Variable queries return bindings; ground queries return `true`, `false`,
or `unknown`. See [ontology semantics](ontology.md).

Multiple queries share a loaded engine. `--stateless` loads and derives the
workspace separately for each query through the engine CLI. Schema validation
can cost substantially more than an ordinary query; a timeout is a failed check.

| Option | Meaning |
|---|---|
| `--workspace DIR` | Generated knowledge base; default `fx/` |
| `--timeout SECONDS` | Query/request timeout; CLI default 300 |
| `--load-timeout SECONDS` | Timeout per initial loading request; default 1800 |
| `--max-proofs N` | Engine proof limit per fact; default 100000 |
| `--quiet` | Suppress the loading bar; status messages may still be printed |

## REPL

```bash
uv run python src/main.py --repl
```

Queries can omit the leading `?-` and trailing period:

```text
sp_8509000[hasWifi -> ?W]
sp_8506302[nonStopTo -> ?B]
:explain sp_8509000[hasWifi -> true]
:stats
:warnings
:help
:quit
```

`:explain` expects a ground fact. `:warnings` shows load warnings.
Ctrl-D also exits. Initial derivation is shared across questions; later costs
depend on joins and result size.

## Journeys on the snapshot's operating day

Requests need an origin, an operating day present in the workspace, an earliest
departure, and a transfer allowance. Use the snapshot's day rather than today's
date: the service feed covers the previous operating day when retrieved.
An unknown origin or a day with no loaded trip evidence is an error (exit code
1), rather than an empty journey answer. Missing-day errors list the available
operating days, or say that no dated trips are loaded. With a known origin and
day, a result can still be `unknown` when no journey meets the supplied time
and transfer allowance.

This command reads the most frequent `observedOn` date from the service layer
in the default `fx/` workspace, then runs the journey query:

```bash
uv run python - <<'PY'
from collections import Counter
from pathlib import Path
import re
import subprocess
import sys

workspace = Path("fx")
source = (workspace / "70-ist-daten-sbb.fx").read_text()
days = Counter(re.findall(r'\[observedOn -> "([\d-]+)"\]', source))
if not days:
    raise SystemExit("The workspace contains no service operating day.")
day = days.most_common(1)[0][0]
raise SystemExit(subprocess.call([
    sys.executable, "src/main.py", "--workspace", str(workspace),
    "--journey", "8509000", "--day", day,
    "--not-before", f"{day}T18:00:00", "--transfer-seconds", "300",
]))
PY
```

The 300-second allowance is a caller-supplied example. Answers depend on the
services and actual timestamps in the snapshot. These reports do not provide
a live journey planner; see [journey semantics](ontology.md#service-and-journeys).

## Python sessions

This example reads the operating day from trip facts and reuses one session:

```python
from pathlib import Path
import json

from runtime.session import Session

with Session(Path("fx")) as session:
    answer, seconds = session.ask('?- sp_8509000[hasWifi -> ?W].')
    print(answer, seconds)

    reports, _ = session.ask('?- ?T:Trip AND ?T[observedOn -> ?D].')
    days = sorted({json.loads(row["D"]) for row in reports.get("bindings", [])})
    if not days:
        raise ValueError("The workspace contains no reported service day.")
    day = days[-1]
    request = session.prepare_journey(
        8509000, day, not_before=f"{day}T18:00:00", transfer_seconds=300,
    )
    result, seconds = session.ask(f'?- {request}[reachDirect -> ?Destination].')
    print(result, seconds)
```

`Session.ask()` returns an engine result dictionary and elapsed seconds.
Bindings retain FrameX literal syntax, including quotes around string values.
Sessions stay silent unless given a progress reporter. The context manager
closes the engine process. See the [raw client reference](../framex/README.md)
for lower-level calls.

Source mutations through `session.client` are supported before journey
preparation. Once `prepare_journey()` validates the inputs and starts writing
journey facts, data-changing commands such as
`add`, `retract`, `retract_source`, `world`, and loading raise `QueryError`.
Queries, explanations, validation, and additional journey requests remain
available. To change the source data, open a fresh `Session`, apply the changes,
then prepare journeys. The restriction also holds after preparation fails
following a write. Input errors raised before writing leave the data mutable.
