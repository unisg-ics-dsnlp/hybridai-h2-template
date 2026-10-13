"""The generic row -> FrameX facts engine.

One function per shape, all sharing identity resolution, literal rendering and
provenance. Adding a dataset means writing a spec, not writing code.

Rows are data, never instructions: every value goes through emit.literal, so a
cell containing FrameX syntax is rendered as a quoted string and cannot inject
rules into the knowledge base.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from . import emit
from .ids import (
    Resolution,
    Scope,
    bpuic_from_didok,
    normalise_bpuic,
    stop_point_id,
)
from .spec import Attribute, Child, Observation, Relation, Spine


def rows_of(data) -> list[dict]:
    """Accept a DataFrame or any iterable of row dicts.

    The loader returns row dictionaries; DataFrames are also accepted.
    """
    records = getattr(data, "to_dict", None)
    if callable(records):
        return data.to_dict("records")
    if isinstance(data, Iterable):
        return list(data)
    raise TypeError("expected a DataFrame or an iterable of row dicts")


def _key_of(row: dict, key: str | None) -> int | None:
    """Resolve a row's stop-point id, expanding DIDOK short numbers."""
    if key is None:
        return None  # dataset has no id column; the caller falls back to names
    raw = row.get(key)
    if key in {"didok", "didok_nr", "abk_didok"}:
        return bpuic_from_didok(raw)
    return normalise_bpuic(raw)


def _locate(row: dict, key: str | None, scope: Scope,
            report: Resolution) -> tuple[int | None, str]:
    """Place one row on the spine, or record why it could not be placed.

    Order matters: the numeric id is authoritative, and a name is consulted
    only when the id column is empty. Six of wifistation's 79 rows arrive with
    a NaN bpuic, and one of them ("St. Gallen") matches the spine exactly --
    the remaining five ("Wil", "Rapperswil", ...) are genuinely ambiguous
    against Swiss station names and stay unresolved rather than guessed.
    """
    bpuic = _key_of(row, key)
    how = "id"
    if bpuic is None:
        bpuic = scope.by_name(row)
        how = "name"
    if bpuic is None:
        report.no_id(scope.label(row))
        return None, ""
    if bpuic not in scope:
        report.miss(bpuic, scope.label(row))
        return None, ""
    report.hit(how)
    return bpuic, how


def _slot_facts(subject: str, row: dict, slots: dict[str, str],
                source: str, stamp: str, annotate: bool = True,
                slot_types: dict[str, str] | None = None) -> Iterator[str]:
    for column, slot in slots.items():
        value = emit.typed_literal(row.get(column), (slot_types or {}).get(slot))
        if value is None:
            continue  # An empty cell stays `unknown`; see emit.literal.
        yield emit.fact(subject, slot, value)
        if annotate:
            yield emit.annotation(subject, slot, value, source, stamp)


# --------------------------------------------------------------------------
# The spine: the only dataset that may create stop points.
# --------------------------------------------------------------------------

def map_spine(spec: Spine, data, stamp: str) -> tuple[list[str], Scope, Resolution]:
    """Emit the stop-point registry and return the scope it defines.

    That scope is the contract every later layer joins against, so a dataset
    can never quietly introduce a station the spine does not know.
    """
    report = Resolution(spec.dataset)
    lines: list[str] = []
    pairs: list[tuple[int, str]] = []
    shared: set[str] = set()
    seen_ids: set[int] = set()

    for row in rows_of(data):
        bpuic = _key_of(row, spec.key)
        if bpuic is None:
            report.no_id()
            continue
        if bpuic in seen_ids:
            raise ValueError(
                f"duplicate stop-point ID {bpuic} in {spec.dataset}; "
                "the registry may contain overlapping API pages and omit stations. "
                "Fetch it with a stable order by its unique ID or use a complete export."
            )
        seen_ids.add(bpuic)
        subject = stop_point_id(bpuic)
        pairs.append((bpuic, row.get("designationofficial") or ""))
        report.hit()

        lines.append(emit.membership(subject, "StopPoint"))
        lines.append(emit.fact(subject, "bpuic", str(bpuic)))
        # The annotation must sit directly behind its fact: the engine rejects
        # `annotate` whose fact is not in the same loaded program, and a
        # persistent session loads the program in chunks, so a gap here can be
        # split across two chunks.
        lines.append(emit.annotation(subject, "bpuic", str(bpuic), spec.dataset, stamp))
        lines.extend(_slot_facts(subject, row, spec.slots, spec.dataset, stamp, slot_types=spec.slot_types))

        for column, (slot, klass, prefix) in spec.references.items():
            raw = row.get(column)
            value = emit.typed_literal(raw, "String")
            if value is None:
                continue
            target = emit.identifier(prefix, str(raw).strip())
            declaration = emit.membership(target, klass)
            if declaration not in shared:
                shared.add(declaration)
                lines.append(declaration)
                lines.append(emit.fact(target, "label", value))
            lines.append(emit.fact(subject, slot, target))

        # "BUS|TRAIN" is two modes, not one string.
        for column, (slot, prefix) in spec.multi.items():
            raw = row.get(column)
            if raw is None or not str(raw).strip():
                continue
            for piece in str(raw).split("|"):
                piece = piece.strip()
                if not piece:
                    continue
                target = emit.identifier(prefix, piece)
                declaration = emit.membership(target, "TransportMode")
                if declaration not in shared:
                    shared.add(declaration)
                    lines.append(declaration)
                    lines.append(emit.fact(target, "label", emit.literal(piece)))
                lines.append(emit.fact(subject, slot, target))

    return lines, Scope.build(pairs), report


# --------------------------------------------------------------------------
# The four layer shapes.
# --------------------------------------------------------------------------

def map_attribute(spec: Attribute, data, scope: Scope,
                  stamp: str) -> tuple[list[str], Resolution]:
    report = Resolution(spec.dataset)
    report.provenance = spec.provenance
    annotate = spec.provenance == "fact"
    lines: list[str] = []
    seen: set[str] = set()

    for row in rows_of(data):
        bpuic, _ = _locate(row, spec.key, scope, report)
        if bpuic is None:
            continue
        subject = stop_point_id(bpuic)

        if spec.presence is not None:
            slot, raw = spec.presence
            value = emit.literal(raw)
            statement = emit.fact(subject, slot, value)
            if statement not in seen:  # a duplicated row is not a second fact
                seen.add(statement)
                lines.append(statement)
                if annotate:
                    lines.append(
                        emit.annotation(subject, slot, value, spec.dataset, stamp)
                    )
        lines.extend(
            _slot_facts(subject, row, spec.slots, spec.dataset, stamp, annotate, spec.slot_types)
        )

    # Completeness is declared after the facts, so absence means `false` only
    # once every row of the inventory has been asserted -- and only if every
    # row actually landed. A spec *claims* completeness; the ingest decides
    # whether the data earned it. Closing a slot on a partially-resolved
    # inventory would turn "we could not place this row" into "it is false".
    if spec.complete_for:
        if report.complete:
            for slot in spec.complete_for:
                lines.append(emit.closed_slot(slot))
        else:
            report.withheld = spec.complete_for
            lines.append(emit.comment(
                f"Completeness NOT declared for {', '.join(spec.complete_for)}: "
                f"{report.unidentified} unresolvable row(s), "
                f"{len(report.out_of_scope)} outside scope."
            ))
            lines.append(emit.comment(
                "Absence therefore stays `unknown` rather than becoming `false`."
            ))
    return lines, report


def _record_id(spec, bpuic: int, row: dict, parts: list) -> str:
    """A source record identity, stable under row reordering and duplication.

    Facilities without unique source keys are snapshot records, not an assertion
    that equal human-readable names identify one persistent physical object.
    Typed mapped values normalize measurements; identity-component source types
    are preserved.
    """
    values = {slot: emit.typed_literal(row.get(column), spec.slot_types.get(slot))
              for column, slot in spec.slots.items()}
    target = getattr(spec, "target", ())
    return emit.stable_identifier(spec.klass, spec.dataset, bpuic,
                                  [emit.literal(p) for p in parts], values,
                                  emit.literal(row.get(target[0])) if target else None)


def map_child(spec: Child, data, scope: Scope,
              stamp: str) -> tuple[list[str], Resolution]:
    report = Resolution(spec.dataset)
    report.provenance = spec.provenance
    annotate = spec.provenance == "fact"
    lines: list[str] = []
    used: set[str] = set()

    for row in rows_of(data):
        bpuic, _ = _locate(row, spec.key, scope, report)
        if bpuic is None:
            continue
        parent = stop_point_id(bpuic)

        parts = [row.get(column) for column in spec.identity]
        child = _record_id(spec, bpuic, row, parts)
        if child in used:
            continue
        used.add(child)

        lines.append(emit.membership(child, spec.klass))
        lines.append(emit.fact(child, spec.parent_slot, parent))
        if annotate:
            lines.append(
                emit.annotation(child, spec.parent_slot, parent, spec.dataset, stamp)
            )
        lines.extend(
            _slot_facts(child, row, spec.slots, spec.dataset, stamp, annotate, spec.slot_types)
        )

    return lines, report


def map_relation(spec: Relation, data, scope: Scope,
                 stamp: str) -> tuple[list[str], Resolution]:
    report = Resolution(spec.dataset)
    report.provenance = spec.provenance
    annotate = spec.provenance == "fact"
    lines: list[str] = []
    declared: set[str] = set()
    used: set[str] = set()
    column, slot, klass, prefix = spec.target

    for row in rows_of(data):
        bpuic, _ = _locate(row, spec.key, scope, report)
        if bpuic is None:
            continue
        raw = row.get(column)
        target_value = emit.typed_literal(raw, "String")
        if target_value is None:
            # The station resolved but the other end of the edge is missing,
            # so there is no relation to state.
            continue

        subject = stop_point_id(bpuic)
        target = emit.identifier(prefix, str(raw).strip())
        declaration = emit.membership(target, klass)
        if declaration not in declared:
            declared.add(declaration)
            lines.append(declaration)
            lines.append(emit.fact(target, "label", target_value))

        parts = [row.get(c) for c in spec.identity]
        edge = _record_id(spec, bpuic, row, parts)
        if edge in used:
            continue
        used.add(edge)

        lines.append(emit.membership(edge, spec.klass))
        lines.append(emit.fact(edge, spec.subject_slot, subject))
        lines.append(emit.fact(edge, slot, target))
        if annotate:
            lines.append(
                emit.annotation(edge, spec.subject_slot, subject, spec.dataset, stamp)
            )
        lines.extend(
            _slot_facts(edge, row, spec.slots, spec.dataset, stamp, annotate, spec.slot_types)
        )
    return lines, report


def map_observation(spec: Observation, data, scope: Scope,
                    stamp: str) -> tuple[list[str], Resolution]:
    report = Resolution(spec.dataset)
    report.provenance = spec.provenance
    annotate = spec.provenance == "fact"
    lines: list[str] = []
    used: set[str] = set()

    for row in rows_of(data):
        bpuic, _ = _locate(row, spec.key, scope, report)
        if bpuic is None:
            continue
        subject = stop_point_id(bpuic)

        parts = [row.get(column) for column in spec.identity]
        node = _record_id(spec, bpuic, row, parts)
        if node in used:
            continue
        used.add(node)

        lines.append(emit.membership(node, spec.klass))
        lines.append(emit.fact(node, spec.subject_slot, subject))
        if annotate:
            lines.append(
                emit.annotation(node, spec.subject_slot, subject, spec.dataset, stamp)
            )
        lines.extend(
            _slot_facts(node, row, spec.slots, spec.dataset, stamp, annotate, spec.slot_types)
        )
    return lines, report


MAPPERS = {
    Attribute: map_attribute,
    Child: map_child,
    Relation: map_relation,
    Observation: map_observation,
}


def map_layer(spec, data, scope: Scope, stamp: str) -> tuple[list[str], Resolution]:
    """Dispatch a layer spec to its shape's mapper."""
    mapper = MAPPERS.get(type(spec))
    if mapper is None:
        raise TypeError(f"no mapper for {type(spec).__name__}")
    return mapper(spec, data, scope, stamp)
