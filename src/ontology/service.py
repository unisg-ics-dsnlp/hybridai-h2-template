"""Normalize dated service reports into events; FrameX derives their meaning.

Scheduled timestamps establish route order, not actual arrival. REAL forecast
columns supply observed timestamps for transfer checks. Cancelled/unknown rows
break ride continuity; out-of-scope real stops break non-stop adjacency only.
Ambiguous ordering is retained as evidence but supplies no connection edges.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import math
from zoneinfo import ZoneInfo

from . import emit
from .ids import Resolution, Scope, normalise_bpuic, stop_point_id
from .mapper import rows_of

TRIP = "fahrt_bezeichner"
ARRIVAL, DEPARTURE = "ankunftszeit", "abfahrtszeit"
PASS_THROUGH = "durchfahrt_tf"
CANCELLED = "faellt_aus_tf"


def _flag(value) -> bool | None:
    # The source contract defines null as false. Unknown spellings are not false.
    if value is None or str(value).strip().lower() in {"", "false", "0"}:
        return False
    if str(value).strip().lower() in {"true", "1"}:
        return True
    return None


def _is_pass_through(value) -> bool:
    return _flag(value) is True


def timestamp(value, *, round_up: bool = False) -> int | None:
    """UTC seconds; naive SBB timestamps are Swiss local time.

    Refuse ambiguous/nonexistent local times instead of silently choosing a DST
    fold. Explicit UTC offsets remove that ambiguity.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        zone = ZoneInfo("Europe/Zurich")
        first, second = dt.replace(tzinfo=zone, fold=0), dt.replace(tzinfo=zone, fold=1)
        if first.utcoffset() != second.utcoffset():
            return None
        dt = first
    seconds = dt.timestamp()
    return math.ceil(seconds) if round_up else math.floor(seconds)


def _order_key(row: dict) -> int | None:
    return timestamp(row.get(DEPARTURE) or row.get(ARRIVAL))


def map_service(data, scope: Scope, stamp: str) -> tuple[list[str], Resolution]:
    report = Resolution("ist-daten-sbb")
    report.provenance = "layer"
    lines: list[str] = []
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows_of(data):
        trip = str(row.get(TRIP) or "").strip()
        day = str(row.get("betriebstag") or "").strip()
        try:
            day = datetime.strptime(day, "%Y-%m-%d").date().isoformat()
        except ValueError:
            report.no_id("missing/invalid operating day")
            continue
        if not trip:
            report.no_id()
            continue
        grouped.setdefault((day, trip), []).append(row)

    for (day, trip), raw_rows in sorted(grouped.items()):
        trip_id = emit.stable_identifier("trip", day, trip)
        # Duplicates are one event. A content key avoids input-order identities.
        unique = {}
        for row in raw_rows:
            event = emit.stable_identifier("ev", day, trip,
                {k: emit.literal(row.get(k)) for k in
                 ("bpuic", ARRIVAL, DEPARTURE, "an_prognose", "ab_prognose",
                  "an_prognose_status", "ab_prognose_status", PASS_THROUGH, CANCELLED)})
            bpuic = normalise_bpuic(row.get("bpuic"))
            if bpuic is None:
                report.no_id(scope.label(row))
            elif bpuic not in scope:
                report.miss(bpuic, str(row.get("haltestellen_name") or ""))
            else:
                report.hit()
            unique[event] = row
        times = Counter(_order_key(row) for row in unique.values())
        ordered = None not in times and all(n == 1 for n in times.values())
        if not ordered:
            report.excluded["ambiguous_trip_order"] += len(unique)
        rows = sorted(unique.items(), key=lambda pair: (_order_key(pair[1]) or 0, pair[0]))
        if not any(normalise_bpuic(r.get("bpuic")) in scope for _, r in rows):
            continue
        lines.extend([emit.membership(trip_id, "Trip"),
                      emit.fact(trip_id, "tripRef", emit.typed_literal(trip, "String")),
                      emit.fact(trip_id, "observedOn", emit.typed_literal(day, "String"))])
        # Conflicting trip metadata is not resolved by picking an arbitrary row.
        for column, slot in (("verkehrsmittel_text", "serviceCategory"),
                             ("linien_text", "lineText"), ("betreiber_abk", "operator")):
            values = {emit.typed_literal(r.get(column), "String") for _, r in rows}
            values.discard(None)
            if len(values) == 1:
                lines.append(emit.fact(trip_id, slot, values.pop()))
        previous_real = previous_ride = segment = None
        previous_departure = None
        for index, (event, row) in enumerate(rows, 1):
            bpuic = normalise_bpuic(row.get("bpuic"))
            cancelled, through = _flag(row.get(CANCELLED)), _flag(row.get(PASS_THROUGH))
            actual = {}
            for column, status_column, slot in (("an_prognose", "an_prognose_status", "arrivalEpoch"),
                                              ("ab_prognose", "ab_prognose_status", "departureEpoch")):
                value = timestamp(row.get(column), round_up=slot == "arrivalEpoch")
                if value is not None and str(row.get(status_column) or "").upper() == "REAL":
                    actual[slot] = value
            arrival, departure = actual.get("arrivalEpoch"), actual.get("departureEpoch")
            # Scheduled order alone cannot justify travelling backwards in real
            # time. Retain contradictory reports as evidence, but split rides.
            invalid_dwell = arrival is not None and departure is not None and arrival > departure
            backwards = previous_departure is not None and any(
                value < previous_departure for value in actual.values())
            active = cancelled is False and through is False and not invalid_dwell
            if invalid_dwell or backwards:
                report.excluded["inconsistent_actual_time"] += 1
            if (cancelled is not False or through is None or bpuic is None
                    or invalid_dwell or backwards):
                previous_real = previous_ride = segment = None
                previous_departure = None
            if cancelled is False and not invalid_dwell and departure is not None:
                previous_departure = departure
            if through is True:
                report.excluded["pass_through"] += 1
                continue
            if bpuic not in scope:
                previous_real = None
                continue
            lines.extend([emit.membership(event, "StopEvent"),
                          emit.fact(event, "ofTrip", trip_id),
                          emit.fact(event, "atStopPoint", stop_point_id(bpuic)),
                          emit.fact(event, "stopSequence", str(index))])
            if cancelled is not None:
                lines.append(emit.fact(event, "cancelled", emit.literal(cancelled)))
            if through is not None:
                lines.append(emit.fact(event, "passThrough", emit.literal(through)))
            for column, slot in ((ARRIVAL, "scheduledArrival"), (DEPARTURE, "scheduledDeparture")):
                value = emit.typed_literal(row.get(column), "String")
                if value is not None:
                    lines.append(emit.fact(event, slot, value))
            for slot, value in actual.items():
                lines.append(emit.fact(event, slot, str(value)))
            if active and ordered:
                if segment is None:
                    segment = emit.stable_identifier("segment", trip_id, event)
                    lines.append(emit.membership(segment, "RideSegment"))
                    lines.append(emit.fact(segment, "ofTrip", trip_id))
                lines.append(emit.fact(event, "rideSegment", segment))
                if previous_real:
                    lines.append(emit.fact(previous_real, "nextRealStop", event))
                if previous_ride:
                    lines.append(emit.fact(previous_ride, "nextRideStop", event))
                previous_real = previous_ride = event
            else:
                previous_real = previous_ride = segment = None
    return lines, report
