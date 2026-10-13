# Development

[Project overview](../README.md) · [Usage](usage.md) · [Ontology extension](ontology.md)

## Package responsibilities

| Package | Responsibility |
|---|---|
| `cli` | Arguments, command dispatch, REPL, progress, answer formatting |
| `data` | Dataset discovery, pagination, exports |
| `ontology` | Mapping specs, IDs, fact emission, normalization, construction |
| `runtime` | Engine discovery, source loading, sessions, queries, validation |
| `framex` | Vendored JSON Lines transport to the engine |

[src/main.py](../src/main.py) delegates to `cli.app.main()`. In `cli`,
`app.py` dispatches commands, `repl.py` manages interaction, `output.py`
formats answers, `progress.py` reports loading, and `stateless.py` prints
one-shot results. The other packages do not import the CLI.

`ontology.builder` coordinates fact construction and uses runtime helpers
for bounded syntax checks. `runtime.session` manages engine lifetime and
uses ontology helpers to create journey requests. `runtime.progress` defines
a reporter interface and silent default; the CLI supplies terminal reporting.

Import `Session` from `runtime.session`, `QueryError` from
`runtime.query`, and the raw client from `framex.framex`.

Journey preparation asserts transfer-readiness timestamps and caches requests.
The session's client therefore rejects source mutations once preparation starts,
including after a partial failure. All public client commands pass through this
guard; only internal journey preparation may add request and readiness facts.
Reads and further journey requests remain available. The standalone FrameX
client remains mutable; use a fresh `Session` to work with changed source data.

## Generated workspaces and publication

```text
fx/
├── 00-core.fx ... 50-observations.fx
├── 60-spine.fx
├── 70-<dataset>.fx
└── manifest.json
```

Rules are copied from `src/ontology/ontology/`. The registry defines station
scope; attached layers join onto it. The manifest records format version,
registry identity, retrieval metadata, and layer statistics.

Builds stage a generation beside the target and check generated syntax in
bounded chunks before publication. Fetch, mapping, syntax, or ordinary
publication failures preserve the previous generation. Use one writer;
publication is not a durable transaction for multiple writers. Syntax checks
alone do not certify schemas or semantics.

Registry downloads use stable ordering and reject duplicate IDs. Rebuild to
apply updated mappings or rules; modifying Python does not repair generated
facts. Use a separate `--workspace` to inspect a rebuild.

## Engine loading

Persistent sessions require FrameX 0.4.3+. Files stream in bounded chunks
through `load_begin` and `load_chunk`; one `load_commit` builds indexes
and derives the closure. Loading failures abort staging and close the process.

The bulk chunker preserves multi-line statements and accounts for JSON-escaped
request size. Annotations can refer across staged chunks. Syntax checks and
live additions use another chunker that keeps each annotation's fact in the
same request. Oversized statements fail explicitly.

Startup requests are omitted from the transcript because source files already
provide replay input. Later queries and mutations are recorded subject to
transcript limits. Load warnings are retained separately. `--load-timeout`
applies per loading request, including closure derivation at commit.

Small fixture tests do not measure full-workspace startup. Use the REPL's
`ready in` value for load time; command total timing includes interaction.

## Testing

```bash
uv run pytest -q
```

The default suite uses local fixtures and mocked HTTP requests, without live
downloads. Install `framex` on `PATH` to run engine integration cases;
they skip otherwise. Snapshot cases skip unless explicitly enabled.

| Test module | Coverage |
|---|---|
| `tests/test_loader.py` | API options, paging, exports, catalog, and errors |
| `tests/test_ontology.py` | Mapping, IDs, schemas, rules, and output helpers |
| `tests/test_regression.py` | Counterexamples, rollback, chunking, sessions |
| `tests/test_full_dataset.py` | Opt-in full-snapshot checks |

New mappings and rules need positive, missing-evidence, and counterexample
cases. Check the path from source rows to facts and conclusions. Validation
must inspect schema and constraint channels as well as `violations`;
`runtime.validation.validation_issues()` implements that interpretation.

### Full-snapshot checks

Build a complete workspace, then enable the checks:

```bash
uv run python src/main.py --init
SBB_TEST_WORKSPACE="$PWD/fx" uv run pytest -q -s tests/test_full_dataset.py
```

Use an existing full workspace if already built; use `--yes` when intentionally
refreshing it. Run cases in one pytest process without parallel workers; they
share one loaded engine and prepared journey.

`SBB_TEST_TIMEOUT` sets the request timeout, default 900 seconds.
The day is detected from `observedOn` in `70-ist-daten-sbb.fx`;
`SBB_TEST_DAY` overrides it. Journey checks depart at 18:00 Swiss local time
with a 300-second allowance.

The checks assume Chur WiFi, cancellation evidence, and available journeys in
the supplied snapshot. They validate targeted invariants and expected evidence,
not every answer. Investigate failures against the source snapshot before
attributing them to data changes or logic.

## Packaging

`pyproject.toml` lists packages under `src/` and the vendored client.
The six `.fx` files ship inside `ontology`. Guides live in `docs/`;
the root README supplies the package description.

```bash
uv build --wheel
```

After changing package paths or resources, verify the editable installation,
wheel imports, and included `.fx` files.
