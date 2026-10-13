"""Building the FrameX logic base on disk.

The workspace is a directory of .fx files plus a manifest:

    fx/
      00-core.fx ... 50-observations.fx     the meta model, copied verbatim
      60-spine.fx                           stop-point registry
      70-<dataset>.fx                       one file per attached dataset
      manifest.json                         what was pulled, when, and the scope

Files are numbered because the engine loads them together and exactly one file
may declare the world -- 00-core.fx does, and the generated files never do.

The manifest records the in-scope BPUIC set, so `--add` can join a new dataset
onto the spine without re-pulling 1,710 rows from the API.
"""

from __future__ import annotations

import json
import shutil
import os
import subprocess
import tempfile
from datetime import date
from importlib import resources
from pathlib import Path

from . import emit, mapper, registry, service
from .ids import Resolution, Scope
from runtime.query import binary

MANIFEST = "manifest.json"
SPINE_FILE = "60-spine.fx"
META_PREFIXES = ("00-", "10-", "20-", "30-", "40-", "50-")


class BuildError(RuntimeError):
    """A build could not proceed; the message is meant for the user."""


def ontology_files() -> list[Path]:
    """The hand-written meta-model layers, in load order."""
    root = resources.files(__package__) / "ontology"
    return sorted(Path(str(p)) for p in root.iterdir() if p.name.endswith(".fx"))


def layer_path(workspace: Path, dataset: str) -> Path:
    return workspace / f"70-{dataset}.fx"


def existing_logic(workspace: Path) -> list[Path]:
    if not workspace.exists():
        return []
    return sorted(workspace.glob("*.fx"))


def read_manifest(workspace: Path) -> dict:
    path = workspace / MANIFEST
    if not path.exists():
        raise BuildError(
            f"no manifest in {workspace}/ -- run --init first to create the logic base"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(workspace: Path, manifest: dict) -> None:
    (workspace / MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def _dataset_facts(dataset: str, stamp: str, statements: int,
                   provenance: str, complete_for=(), row_count: int = 0) -> list[str]:
    """The layer's own Dataset object.

    Every layer gets one, so `?D:Dataset` can report what is loaded and when it
    was retrieved. For layers whose provenance is recorded at layer level
    rather than per fact, this object *is* the provenance record.
    """
    node = emit.identifier("ds", dataset)
    facts = [
        emit.membership(node, "Dataset"),
        emit.fact(node, "datasetId", emit.literal(dataset)),
        emit.fact(node, "retrievedAt", emit.literal(stamp)),
        emit.fact(node, "rowCount", str(row_count)),
        emit.fact(node, "statementCount", str(statements)),
        emit.fact(node, "provenanceMode", emit.literal(provenance)),
    ]
    for slot in complete_for:
        facts.append(emit.fact(node, "completeFor", emit.literal(slot)))
    return facts


def _write_layer(path: Path, title: str, notes: list[str], lines: list[str]) -> None:
    body = emit.header(title, notes) + [""] + lines + [""]
    path.write_text("\n".join(body), encoding="utf-8")


def copy_meta_model(workspace: Path) -> list[str]:
    """Copy the meta model into the workspace so it is self-contained."""
    workspace.mkdir(parents=True, exist_ok=True)
    names = []
    for source in ontology_files():
        shutil.copyfile(source, workspace / source.name)
        names.append(source.name)
    return names


def build_spine(workspace: Path, load_dataset, stamp: str,
                report: list[str]) -> Scope:
    """Pull the stop-point registry and write 60-spine.fx."""
    spec = registry.SPINE
    # The filtered spine fits the paging endpoint. Its unique national ID
    # supplies a total order: unsorted pages can overlap and omit stations.
    frame = load_dataset(spec.dataset, where=spec.where, order_by=spec.key)
    lines, scope, resolution = mapper.map_spine(spec, frame, stamp)
    _write_layer(
        workspace / SPINE_FILE,
        f"Spine: {spec.dataset}",
        [
            "The stop-point registry. Identity is the national BPUIC.",
            f"ODSQL scope filter: {spec.where}",
            f"Retrieved {stamp}: {len(scope)} stop points, {len(lines)} statements.",
        ],
        lines,
    )
    report.append(f"  {SPINE_FILE:<44} {len(scope)} stop points, {len(lines)} statements")
    return scope


def build_layer(workspace: Path, dataset: str, load_dataset, scope: Scope,
                stamp: str) -> tuple[int, Resolution]:
    """Pull one dataset and write its layer file. Returns (statements, report)."""
    spec = registry.LAYERS.get(dataset)
    if dataset == registry.SERVICE_DATASET:
        # 70,314 rows: past the /records offset limit. load_dataset handles
        # that itself by switching to the exports endpoint.
        frame = mapper.rows_of(load_dataset(dataset))
        lines, resolution = service.map_service(frame, scope, stamp)
        notes = [
            "Observed service: trips and stop events from actual running data.",
            "Scheduled order; cancelled/uncertain events break continuity; REAL times retained.",
            "Dated service reports -- not a live timetable or a guarantee of operation.",
        ]
    elif spec is None:
        raise BuildError(
            f"unknown dataset {dataset!r}\n"
            f"known datasets: {', '.join(registry.available())}"
        )
    else:
        kwargs = {}
        if spec.where:
            kwargs["where"] = spec.where
        if getattr(spec, "max_records", None):
            kwargs["max_records"] = spec.max_records
        frame = mapper.rows_of(load_dataset(dataset, **kwargs))
        lines, resolution = mapper.map_layer(spec, frame, scope, stamp)
        notes = [
            f"Shape: {type(spec).__name__}.",
            f"Retrieved {stamp}. {resolution.summary()}.",
        ]

    # Only advertise a completeness contract the ingest actually honoured; the
    # mapper withholds it when rows could not be placed.
    claimed = () if resolution.withheld else getattr(spec, "complete_for", ())
    lines = lines + [""] + _dataset_facts(
        dataset, stamp, len(lines), resolution.provenance, claimed, row_count=len(frame)
    )
    _write_layer(layer_path(workspace, dataset), f"Layer: {dataset}", notes, lines)
    return len(lines), resolution


def _init_in_place(workspace: Path, load_dataset, datasets=None, stamp=None,
         progress=print) -> dict:
    """Create the whole logic base from scratch.

    Callers are expected to have already resolved the overwrite question; this
    function removes any .fx files it is about to replace.
    """
    stamp = stamp or date.today().isoformat()
    datasets = list(registry.INIT_DATASETS if datasets is None else datasets)

    for stale in existing_logic(workspace):
        stale.unlink()

    report: list[str] = []
    progress(f"Building FrameX logic base in {workspace}/")
    names = copy_meta_model(workspace)
    progress(f"  meta model: {len(names)} layers ({', '.join(names)})")

    progress(f"  pulling {registry.SPINE.dataset} ...")
    scope = build_spine(workspace, load_dataset, stamp, report)
    progress(report[-1])

    manifest = {
        "created": stamp,
        "format_version": 2,
        "spine": {
            "dataset": registry.SPINE.dataset,
            "where": registry.SPINE.where,
            "stop_points": sorted(scope.ids),
            "names": {k: v for k, v in scope.names.items() if v is not None},
        },
        "layers": {},
        "meta_model": names,
    }

    for dataset in datasets:
        progress(f"  pulling {dataset} ...")
        try:
            count, resolution = build_layer(
                workspace, dataset, load_dataset, scope, stamp
            )
        except Exception as exc:  # one bad dataset must not lose the others
            progress(f"    FAILED: {type(exc).__name__}: {exc}")
            manifest["layers"][dataset] = {"status": "failed", "error": str(exc)}
            continue
        manifest["layers"][dataset] = {
            "status": "ok",
            "retrieved": stamp,
            "statements": count,
            "resolved": resolution.resolved,
            "outside_scope": len(resolution.out_of_scope),
            "outside_scope_rows": resolution.out_of_scope_rows,
            "excluded": dict(resolution.excluded),
            "without_id": resolution.unidentified,
            "by_name": resolution.by_name,
            "completeness_withheld": list(resolution.withheld),
        }
        progress(f"    {count} statements; {resolution.summary()}")

    write_manifest(workspace, manifest)
    return manifest


def _add_in_place(workspace: Path, dataset: str, load_dataset, stamp=None,
        progress=print) -> dict:
    """Attach one dataset to an existing logic base."""
    stamp = stamp or date.today().isoformat()
    manifest = read_manifest(workspace)
    scope = Scope(
        set(manifest["spine"]["stop_points"]),
        {k: int(v) for k, v in manifest["spine"].get("names", {}).items()},
    )

    if not registry.known(dataset):
        raise BuildError(
            f"unknown dataset {dataset!r}\n"
            f"known datasets: {', '.join(registry.available())}"
        )
    if dataset == registry.SPINE.dataset:
        raise BuildError(
            "the spine is rebuilt with --init, not --add: it defines which stop "
            "points exist, so replacing it would orphan every attached layer"
        )

    target = layer_path(workspace, dataset)
    replacing = target.exists()
    progress(f"{'Replacing' if replacing else 'Adding'} {dataset} "
             f"({len(scope)} stop points in scope) ...")

    count, resolution = build_layer(workspace, dataset, load_dataset, scope, stamp)
    manifest["layers"][dataset] = {
        "status": "ok",
        "retrieved": stamp,
        "statements": count,
        "resolved": resolution.resolved,
        "outside_scope": len(resolution.out_of_scope),
        "outside_scope_rows": resolution.out_of_scope_rows,
        "excluded": dict(resolution.excluded),
        "without_id": resolution.unidentified,
        "by_name": resolution.by_name,
        "completeness_withheld": list(resolution.withheld),
    }
    write_manifest(workspace, manifest)
    progress(f"  wrote {target.name}: {count} statements; {resolution.summary()}")
    return manifest


def _check_syntax(workspace: Path) -> None:
    """Bound syntax checks so the engine never parses the entire base at once."""
    from runtime.source import _chunks, _statements

    engine = binary()
    def check(paths):
        try:
            result = subprocess.run([engine, "check", *map(str, paths)],
                                    capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise BuildError("generated logic syntax check timed out") from exc
        if result.returncode:
            raise BuildError("generated logic failed syntax validation: " + result.stderr + result.stdout)

    files = existing_logic(workspace)
    meta = [p for p in files if p.name.startswith(META_PREFIXES)]
    check(meta)
    data = [line for p in files if p not in meta for line in _statements(p)]
    with tempfile.TemporaryDirectory(prefix=".sbb-check-", dir=workspace.parent) as temp:
        chunk_path = Path(temp) / "chunk.fx"
        for chunk in _chunks(data):
            chunk_path.write_text("world open.\n" + "\n".join(chunk), encoding="utf-8")
            check([chunk_path])


def _publish(workspace: Path, build) -> dict:
    """Stage a generation beside the workspace; roll back failed publication.

    Directory replacement is recoverable on ordinary exceptions, not a durable
    multi-writer transaction. Builds for one workspace must be serialized.
    """
    workspace = Path(workspace).absolute()
    workspace.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".sbb-build-", dir=workspace.parent) as temp:
        stage, backup = Path(temp) / "stage", Path(temp) / "previous"
        if workspace.exists():
            shutil.copytree(workspace, stage)
        else:
            stage.mkdir()
        manifest = build(stage)
        failed = [name for name, info in manifest["layers"].items() if info["status"] != "ok"]
        if failed:
            raise BuildError("build failed; previous generation retained: " + ", ".join(failed))
        _check_syntax(stage)
        had_previous = workspace.exists()
        if had_previous:
            os.replace(workspace, backup)
        try:
            os.replace(stage, workspace)
        except BaseException:
            if had_previous:
                os.replace(backup, workspace)
            raise
        return manifest


def init(workspace: Path, load_dataset, datasets=None, stamp=None, progress=print) -> dict:
    return _publish(workspace, lambda stage: _init_in_place(
        stage, load_dataset, datasets=datasets, stamp=stamp, progress=progress))


def add(workspace: Path, dataset: str, load_dataset, stamp=None, progress=print) -> dict:
    manifest = read_manifest(workspace)
    if manifest.get("format_version") != 2:
        raise BuildError("this workspace uses the old ontology; rebuild with --init before --add")
    return _publish(workspace, lambda stage: _add_in_place(
        stage, dataset, load_dataset, stamp=stamp, progress=progress))
