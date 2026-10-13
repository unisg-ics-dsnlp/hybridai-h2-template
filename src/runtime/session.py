"""A persistent FrameX session over a logic base.

`framex query FILE...` is stateless: it parses the whole workspace, computes the
deductive closure, answers one question, and throws all of it away. Loading the
program into one long-lived `framex serve` process pays that initial cost once.
Reuse a Session for a batch of queries or dataset tests. Query and mutation costs
still depend on the rules and bindings; broad joins can remain expensive.

FrameX 0.4.3 bulk loading parses bounded chunks without rebuilding the growing
knowledge base after each request. One `load_commit` builds the indexes and
derives the closure. Rules and facts may be staged in either order, and an
annotation may refer to a fact in another chunk of the same transaction.

Source files are streamed rather than retained in Python. Startup requests
are not recorded in the client transcript: the files on disk already provide
the replay source. Query and mutation requests are recorded after startup.

The separate `_chunks` helper keeps facts and annotations together for one-shot
syntax checks and live `add` mutations, where annotations must resolve within
one request. Bulk loading uses `_program_chunks` instead.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

from framex.framex import Client, FrameXError

from .progress import NullProgress, ProgressReporter
from .query import DEFAULT_MAX_PROOFS, QueryError, logic_files
from .source import CHUNK_BYTES, _chunks, _program_chunks, _statements

#: Broad queries, validation and live mutations can exceed the client's
#: default response budget even though bulk loading returns only a fact count.
MAX_RESPONSE_BYTES = 512 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 256 * 1024 * 1024
DEFAULT_LOAD_TIMEOUT = 1800

#: Headroom for the fixpoint. The defaults are tuned for teaching-sized
#: programs; see DEFAULT_MAX_PROOFS for why the proof limit in particular has
#: to be raised here.
LIMITS = {
    "max_facts": 5_000_000,
    "max_rounds": 2000,
    "max_proofs": DEFAULT_MAX_PROOFS,
}


class _SessionClient(Client):
    """Prevent source mutations from invalidating prepared journey facts."""

    _READ_COMMANDS = frozenset({
        "query", "explain", "why_not", "dependents", "schema", "modules",
        "validate", "stats", "diff", "compare", "provenance", "rdf_export",
    })
    _journey_data_locked = False

    def request(self, command, **fields):
        if self._journey_data_locked and command not in self._READ_COMMANDS:
            raise QueryError(
                "cannot change session data after journey preparation starts; "
                "open a new Session and apply changes before preparing journeys"
            )
        return super().request(command, **fields)

    def _add_journey(self, source, **limits):
        """Only Session's preparation code may extend a locked knowledge base."""
        return super().request("add", source=source, **limits)


class Session:
    """A loaded logic base; source mutations lock when journey preparation starts."""

    def __init__(self, workspace: Path, *, binary: str = "framex",
                 timeout: float = 900, load_timeout: float = DEFAULT_LOAD_TIMEOUT,
                 progress: ProgressReporter | None = None,
                 max_proofs: int = DEFAULT_MAX_PROOFS):
        if type(max_proofs) is not int or max_proofs <= 0:
            raise ValueError("max_proofs must be a positive integer")
        for name, value in (("timeout", timeout), ("load_timeout", load_timeout)):
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be finite and positive")
        self.limits = dict(LIMITS, max_proofs=max_proofs)
        self.query_timeout = timeout
        self._load_warnings: list = []
        self._journey_requests: set[str] = set()
        self.workspace = Path(workspace)
        #: Disabled by default so importing code stays quiet; the CLI passes a
        #: live reporter in.
        self.progress = progress if progress is not None else NullProgress()
        self.files = logic_files(self.workspace)
        self.client = _SessionClient(
            binary,
            # Loading and closure derivation are much slower than later
            # queries. Switch back to the user-facing query timeout below.
            request_timeout=load_timeout,
            retain_transcript=False,
            max_response_bytes=MAX_RESPONSE_BYTES,
            max_transcript_bytes=MAX_TRANSCRIPT_BYTES,
        )
        self.stats: dict = {}
        self.load_seconds = 0.0
        try:
            self._load()
            self.client.retain_transcript = True
            self.client.request_timeout = self.query_timeout
        except BaseException:
            self.progress.close()
            self.client.close()
            raise

    # ----------------------------------------------------------------
    # Loading
    # ----------------------------------------------------------------

    def _capture_load_response(self, *, stats: bool = False) -> None:
        response = self.client.last_response or {}
        self._load_warnings.extend(response.get("warnings") or [])
        if stats:
            self.stats = dict(response.get("stats") or {})
        self.client.last_response = None

    def _load(self) -> None:
        started = time.monotonic()
        try:
            self.client.load_begin(**self.limits)
        except FrameXError as exc:
            raise QueryError(
                f"cannot start bulk loading: {exc}\n"
                "Session loading requires FrameX 0.4.3 or newer on PATH."
            ) from None
        self._capture_load_response()

        self.progress.message(
            f"Staging {len(self.files)} logic files "
            "(indexes and inference built once at commit)"
        )
        total_bytes = sum(path.stat().st_size for path in self.files)
        self.progress.start("staging program", total=total_bytes, unit="bytes")
        statement_count = 0
        chunks = 0
        bytes_read = 0
        bytes_reported = 0

        def count_bytes(count: int) -> None:
            nonlocal bytes_read
            bytes_read += count

        try:
            for path in self.files:
                statements = _statements(path, on_bytes=count_bytes)
                for chunk in _program_chunks(statements, limit=CHUNK_BYTES):
                    chunks += 1
                    statement_count += len(chunk)
                    try:
                        self.client.load_chunk("\n".join(chunk), file=str(path), record=False)
                    except FrameXError as exc:
                        raise QueryError(
                            f"the engine rejected {path.name}, chunk {chunks}: {exc}\n"
                            "Check the workspace .fx source, not your query."
                        ) from None
                    self._capture_load_response()
                    self.progress.step(bytes_read - bytes_reported)
                    bytes_reported = bytes_read
                # A file may end with comments or contain no statements.
                self.progress.step(bytes_read - bytes_reported)
                bytes_reported = bytes_read
            self.progress.finish(f"{statement_count:,} source lines in {chunks} chunks")
            self.progress.start("building indexes and deriving closure")
            try:
                self.client.load_commit()
            except FrameXError as exc:
                raise QueryError(f"the engine rejected the staged logic base: {exc}") from None
            self._capture_load_response(stats=True)
            self.progress.finish(
                f"{self.stats.get('derived_facts', 0):,} facts derived, "
                f"{self.stats.get('rule_firings', 0):,} rule firings, "
                f"{self.stats.get('rounds', 0)} rounds"
            )
        except BaseException:
            # Preserve the original error even if a timeout already killed the engine.
            try:
                self.client.load_abort()
            except Exception:
                pass
            raise
        self.load_seconds = time.monotonic() - started
        self.progress.message(
            f"Ready in {self.load_seconds:.1f}s — reusing this loaded session."
        )

    # ----------------------------------------------------------------
    # Asking
    # ----------------------------------------------------------------

    def ask(self, query: str) -> tuple[dict, float]:
        """Answer one query. Returns (result, seconds)."""
        started = time.time()
        result = self.client.query(query)
        return result, time.time() - started

    def prepare_journey(self, origin: int, day: str, *, not_before: str,
                        transfer_seconds: int) -> str:
        """Create a dated journey request; return its queryable FrameX id.

        The caller supplies the transfer allowance explicitly. Results use only
        REAL timestamps for boarding and changing, and are not live timetables.
        Source data becomes immutable when preparation starts, including if a
        later preparation step fails. Further journey requests remain supported.
        Unknown origins and days absent from the loaded trips raise QueryError
        before any journey facts are written or source mutations are locked.
        """
        from datetime import date
        from ontology import emit
        from ontology.ids import normalise_bpuic, stop_point_id
        from ontology.service import timestamp

        number = normalise_bpuic(origin)
        if number is None:
            raise ValueError("origin must be a station BPUIC")
        day = date.fromisoformat(day).isoformat()
        earliest = timestamp(not_before, round_up=True)
        if earliest is None:
            raise ValueError("not_before must be an unambiguous ISO timestamp")
        if type(transfer_seconds) is not int or not 0 <= transfer_seconds <= 86400:
            raise ValueError("transfer_seconds must be an integer between 0 and 86400")
        request = emit.stable_identifier("journey", number, day, earliest, transfer_seconds)
        if request in self._journey_requests:
            return request
        station = stop_point_id(number)
        if self.client.query(f"?- {station}:StopPoint.")["status"] != "true":
            raise QueryError(
                f"unknown journey origin {number} ({station}): "
                f"not a StopPoint in the loaded workspace {self.workspace}"
            )
        day_literal = emit.typed_literal(day, "String")
        trips = self.client.query(f"?- ?T[observedOn -> {day_literal}] AND ?T:Trip.")
        if not trips.get("bindings"):
            observed = self.client.query("?- ?T[observedOn -> ?Day] AND ?T:Trip.")
            days = sorted({json.loads(row["Day"]) for row in observed.get("bindings", [])})
            available = ", ".join(days) if days else "none (no dated trips loaded)"
            raise QueryError(
                f"no journey service data for operating day {day} in {self.workspace}; "
                f"available operating days: {available}. "
                "Choose an available --day or load ist-daten-sbb for the requested day."
            )
        # Readiness is asserted from arrival times rather than derived by the
        # engine. Freeze source mutations before the first write so it cannot
        # become stale, even if preparation subsequently fails.
        self.client._journey_data_locked = True
        self.progress.message("Preparing journey: finding direct rides in the loaded session...")
        self.client._add_journey("\n".join([
            emit.membership(request, "JourneyRequest"),
            emit.fact(request, "origin", station),
            emit.fact(request, "onDay", day_literal),
            emit.fact(request, "notBefore", str(earliest)),
            emit.fact(request, "transferSeconds", str(transfer_seconds)),
        ]), **self.limits)
        # Materialize readiness only for events already reached by this request.
        # Two expansions suffice for at most two changes. Computing readiness
        # for every event in the country multiplies both load and closure costs.
        prepared: set[str] = set()
        for level in (0, 1):
            self.progress.message(f"Preparing journey: expanding transfer {level + 1} of 2...")
            arrivals = self.client.query(
                f"?- {request}[reached{level} -> ?E] AND ?E[arrivalEpoch -> ?A]."
            ).get("bindings", [])
            ready = []
            for row in arrivals:
                fact = emit.fact(row["E"], f"transferReady({request})",
                                 str(int(row["A"]) + transfer_seconds))
                if fact not in prepared:
                    prepared.add(fact)
                    ready.append(fact)
            for chunk in _chunks(ready):
                self.client._add_journey("\n".join(chunk), **self.limits)
        self._journey_requests.add(request)
        self.progress.message("Journey request ready; subsequent queries reuse these results.")
        return request

    def validate(self) -> dict:
        """Run actual engine validation, separately from answering queries."""
        return self.client.validate()

    def explain(self, fact: str) -> str:
        return self.client.explain(fact)

    def warnings(self) -> list:
        return list(self._load_warnings)

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
