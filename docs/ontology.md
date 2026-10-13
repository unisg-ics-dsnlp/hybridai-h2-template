# Ontology and mappings

[Project overview](../README.md) · [Usage](usage.md) · [Data](data.md)

The model lives in [src/ontology/ontology/](../src/ontology/ontology/).
Python normalizes source records and emits facts; `.fx` rules establish
domain conclusions.

| Rule file | Responsibility |
|---|---|
| `00-core.fx` | World assumption, identity, station scope, and provenance |
| `10-geography.fx` | Cantons, municipalities, and transport modes |
| `20-facilities.fx` | Facilities, amenity rollups, and waiting-hall lifecycle |
| `30-network.fx` | Railway lines, line points, and junctions |
| `40-service.fx` | Dated reports, non-stop connections, and bounded journeys |
| `50-observations.fx` | Passenger-frequency observations and classifications |

## Identity and missing evidence

A station is identified by its BPUIC: `sp_8509000` identifies Chur. Names are
attributes. Name fallback accepts only an unambiguous normalized exact match.
The registry defines station scope; attached datasets cannot introduce stations.

The model declares `world open.`. Missing WiFi, LoRa, or departure-poster
evidence remains unknown. Resolving supplied rows does not establish nationwide
completeness. A `complete_for` contract may close a slot only when its source
covers the full target population and period; unresolved rows cause the mapper
to withhold the contract. Current inventories leave it unset.

Facility, relation, and observation identifiers use deterministic source-record
content hashes. Exact duplicates deduplicate; changed contents can change IDs.
Trip identity includes its operating day. Persist national or source keys when
you need stable references across rebuilds.

Each layer contributes a `Dataset` object. `rowCount` counts source rows;
`statementCount` counts emitted statements. `provenanceMode` distinguishes
per-fact annotations from a layer-level source record.

## Facilities and observations

Waiting-hall rollups require lifecycle evidence: `BESTEHEND` and
`PROJEKTIERT ABBRUCH` establish an existing hall; `PROJEKTIERT NEU` does not.
Planned-hall queries can still return those source objects.
`abfahrtsplakate0` establishes `hasDeparturePoster`, which does not establish
a live departure display. Facility rollups are derived in FrameX.

Measurements retain their period through observation objects. Use
`observedFrequency(?Year)`, `busyIn("2025")`, or `majorHubIn("2025")` to
select a period. `BusyStation` and `MajorHub` can qualify through any recorded
passenger-frequency year. Even `majorHubIn(year)` combines that year's
frequency with separately loaded infrastructure and service snapshots.
It does not establish every condition for that historical year.
Line labels are strings, such as `"900"`.

## Service and journeys

Categories and connections describe the loaded service reports and can change
when the feed is refreshed.

| Query | Meaning |
|---|---|
| `sp_A[nonStopTo -> sp_B]` | Consecutive served stops on a non-cancelled dated trip |
| `request[reachDirect -> ?B]` | One train, including intermediate stops |
| `request[reach1 -> ?B]` | At most one change |
| `request[reach2 -> ?B]` | At most two changes |
| `request[changeAt(?Via) -> ?B]` | A change to a different train at Via |

Direction matters. Known foreign stops preserve same-train continuity while
breaking non-stop adjacency between retained Swiss endpoints. Ambiguous order,
unresolved stations, cancellations, and contradictory actual times can break
continuity. A single visible stop can still establish service there.

Journey results belong to a `JourneyRequest`, created with
`Session.prepare_journey()`. It supplies the day, earliest departure, and
transfer allowance. Boarding and transfer checks use timestamps marked `REAL`;
scheduled times help order the route. Forecast-only timestamps cannot establish
boarding or transfer feasibility. Requests stay within one operating day.
See [journey examples](usage.md#journeys-on-the-snapshots-operating-day).

## Extend the model

[registry.py](../src/ontology/registry.py) declares mappings using
[spec.py](../src/ontology/spec.py):

| Specification | Use |
|---|---|
| `Attribute` | Station attributes or presence flags |
| `Child` | Facility records pointing back to a station |
| `Relation` | Connections with their own attributes |
| `Observation` | Measurements that retain their period |
| `Spine` | The registry establishing station identity |

The service adapter reconstructs trip order and disruption boundaries separately.

```python
from ontology.spec import Observation

spec = Observation(
    dataset="measurements", klass="PassengerFrequency", key="uic",
    identity=("year",), slots={"year": "period", "count": "value"},
    slot_types={"period": "String", "value": "Float"},
)
```

`slots` maps source columns to destination slots; `slot_types` is keyed by
destination slot. Scalar types used here are `Integer`, `Float`, `Boolean`,
and `String`. Decimal measurements use `Float`. Malformed nonempty typed
values are rejected.

Add rollups as typed `.fx` rules. Use typed inverse rules for slots shared by
different object types. Extend the registry and, when appropriate, its default
dataset list. Rebuild after changing rules or mappings. Add positive examples,
missing-evidence cases, and counterexamples covering emitted facts and derived
answers; see [testing](development.md#testing).
