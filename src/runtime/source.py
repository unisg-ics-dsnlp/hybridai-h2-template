"""Stream FrameX source within request budgets while preserving annotations."""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from .query import QueryError

# Below the engine's 1 MiB request cap, with room for the JSON envelope.
CHUNK_BYTES = 1_000_000


def _statements(path: Path, *,
                on_bytes: Callable[[int], None] | None = None) -> Iterator[str]:
    """Yield statement lines of one .fx file without retaining the whole file."""
    with path.open("rb") as source:
        for raw_line in source:
            if on_bytes is not None:
                on_bytes(len(raw_line))
            line = raw_line.decode("utf-8").strip()
            if line and not line.startswith("//"):
                yield line


def _fact_of_annotation(line: str) -> str | None:
    """The exact fact text an `annotate FACT source ...` line refers to.

    Returns None for a plain statement; rejects malformed annotations.
    """
    if not line.startswith("annotate "):
        return None
    body = line[len("annotate "):]
    quote = None
    escaped = False
    for i, char in enumerate(body):
        if escaped:
            escaped = False
        elif quote and char == "\\":
            escaped = True
        elif quote and char == quote:
            quote = None
        elif not quote and char in ('"', "`"):
            quote = char
        elif not quote and body.startswith(" source ", i):
            return body[:i]
    raise QueryError("malformed annotation: missing source separator")


def _chunks(lines: Iterable[str], limit: int = CHUNK_BYTES):
    """Split statements into request-sized chunks, each self-contained.

    Every `annotate` in a chunk must have its fact asserted in that same
    chunk (see the module docstring), so this tracks, per chunk, which facts
    have actually been asserted in it -- not merely assumed adjacent -- and
    re-states one right before its `annotate` whenever the fact is missing.
    A fact and the re-assertion that guards its annotation are always added
    to the same chunk together, so this never itself creates the problem it
    is fixing.
    """
    buf: list[str] = []
    asserted: set[str] = set()
    size = 0

    for line in lines:
        fact = _fact_of_annotation(line)
        # Whether a re-assertion is needed depends on `asserted`, but a
        # boundary flush below empties it -- so if that happens, this must
        # be recomputed against the fresh chunk, not decided once up front.
        while True:
            to_add = [line] if fact is None or fact in asserted else [fact + ".", line]
            cost = sum(len(json.dumps(part, ensure_ascii=False).encode()) for part in to_add)
            if size + cost > limit and buf:
                yield buf
                buf, asserted, size = [], set(), 0
                continue
            if cost > limit:
                raise QueryError("one fact/annotation exceeds the request chunk budget")
            break

        buf.extend(to_add)
        size += cost
        for part in to_add:
            if not part.startswith("annotate "):
                asserted.add(part[:-1] if part.endswith(".") else part)

    if buf:
        yield buf


def _program_chunks(lines: Iterable[str], limit: int = CHUNK_BYTES):
    """Stream bulk-load chunks, keeping multi-line statements intact.

    Count JSON-escaped bytes, with headroom for line separators and the request
    envelope. Unlike `_chunks`, annotations need no repeated facts: the engine
    resolves them against the complete staged program at commit.
    """
    buf: list[str] = []
    pending: list[str] = []
    size = pending_size = 0
    for line in lines:
        pending.append(line)
        pending_size += len(json.dumps(line, ensure_ascii=False).encode()) + 2
        if pending_size > limit:
            raise QueryError("one FrameX statement exceeds the request chunk budget")
        if not line.endswith("."):
            continue
        if buf and size + pending_size > limit:
            yield buf
            buf, size = [], 0
        buf.extend(pending)
        size += pending_size
        pending, pending_size = [], 0
    # Let the engine diagnose incomplete source rather than silently dropping it.
    if pending:
        if buf and size + pending_size > limit:
            yield buf
            buf = []
        buf.extend(pending)
    if buf:
        yield buf


