"""The four shapes a data.sbb.ch dataset can take, as declarative specs.

Measuring rows-per-station across every dataset that carries a station id
showed only four recurring shapes, which is why one mapper plus a short spec
per dataset replaces bespoke code per dataset:

    1:1   attribute    wifistation, abfahrtsplakate0, lorastation
    1:N   child        perron, haltestelle-wartehallen, sektortafel
    N:M   relation     linie-mit-betriebspunkten
    n/a   observation  passagierfrequenz, ist-daten-sbb

What a spec supplies is exactly the part that cannot be inferred from a
DataFrame. wifistation is the clearest case: its columns are
`standort, bpuic, geopos` and there is no WiFi column at all -- the existence
of a row *is* the WiFi fact. Only a human can say that, so `presence` says it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Spine:
    """The stop-point registry every other dataset joins onto.

    Modelled separately from the layers because it defines identity: it is the
    only dataset allowed to bring a stop point into existence.
    """

    dataset: str
    #: ODSQL evaluated server-side. Filtering here costs 18 API pages instead
    #: of 601 for the full 60,111-row table.
    where: str
    key: str = "number"
    #: Scalar columns copied onto the stop point as slots: column -> slot.
    slots: dict[str, str] = field(default_factory=dict)
    slot_types: dict[str, str] = field(default_factory=dict)
    #: Columns whose value names a shared object (canton, municipality...):
    #: column -> (slot, class, id prefix).
    references: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    #: Pipe-delimited multi-value columns ("BUS|TRAIN"): column -> (slot, prefix).
    multi: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Attribute:
    """1:1 -- at most one row per stop point; columns become slots on it."""

    dataset: str
    #: Column carrying the national id. `None` means the dataset has no id
    #: column at all and can only be joined by exact station name -- a weaker
    #: claim, so it is declared explicitly rather than reached by accident.
    key: str | None = "bpuic"
    #: The fact asserted merely because a row exists: (slot, value).
    presence: tuple[str, object] | None = None
    slots: dict[str, str] = field(default_factory=dict)
    slot_types: dict[str, str] = field(default_factory=dict)
    #: Slots whose inventory this dataset completes, emitted as `closed slot`.
    #: Only correct when the dataset really is the full list.
    complete_for: tuple[str, ...] = ()
    where: str | None = None
    #: Provenance granularity. "fact" annotates every statement, so
    #: `framex explain` names the source of each one. "layer" records the
    #: source once as a Dataset object instead: measured, per-fact annotation
    #: becomes superlinear past roughly 70,000 annotations (the spine's 17,000
    #: cost 1.7s; ist-daten's 72,000 pushed one query past 120s), so the
    #: high-volume observation layers declare "layer" and stay queryable.
    provenance: str = "fact"


@dataclass(frozen=True)
class Child:
    """1:N -- each row is its own object, pointing back at its stop point."""

    dataset: str
    klass: str
    #: Slot on the child that references the stop point.
    parent_slot: str = "atStopPoint"
    #: `None` means name-only joining; see Attribute.key.
    key: str | None = "bpuic"
    #: Columns forming the child's identity, appended to the stop point id.
    identity: tuple[str, ...] = ()
    slots: dict[str, str] = field(default_factory=dict)
    slot_types: dict[str, str] = field(default_factory=dict)
    where: str | None = None
    #: Provenance granularity. "fact" annotates every statement, so
    #: `framex explain` names the source of each one. "layer" records the
    #: source once as a Dataset object instead: measured, per-fact annotation
    #: becomes superlinear past roughly 70,000 annotations (the spine's 17,000
    #: cost 1.7s; ist-daten's 72,000 pushed one query past 120s), so the
    #: high-volume observation layers declare "layer" and stay queryable.
    provenance: str = "fact"


@dataclass(frozen=True)
class Relation:
    """N:M -- reified when the edge carries its own attributes.

    linie-mit-betriebspunkten is reified: the station/line pair carries a
    kilometre mark, and a bare edge cannot hold it.
    """

    dataset: str
    klass: str
    key: str = "bpuic"
    #: Column naming the other end: column -> (slot, class, id prefix).
    target: tuple[str, str, str, str] = ()
    subject_slot: str = "atStopPoint"
    identity: tuple[str, ...] = ()
    slots: dict[str, str] = field(default_factory=dict)
    slot_types: dict[str, str] = field(default_factory=dict)
    where: str | None = None
    #: Provenance granularity. "fact" annotates every statement, so
    #: `framex explain` names the source of each one. "layer" records the
    #: source once as a Dataset object instead: measured, per-fact annotation
    #: becomes superlinear past roughly 70,000 annotations (the spine's 17,000
    #: cost 1.7s; ist-daten's 72,000 pushed one query past 120s), so the
    #: high-volume observation layers declare "layer" and stay queryable.
    provenance: str = "fact"


@dataclass(frozen=True)
class Observation:
    """A measurement or event, always a named object so period and provenance
    can attach to it.

    Never a bare slot on the stop point: passagierfrequenz holds one figure per
    station *per year*, so `station[dailyTraffic -> 37900]` would silently mix
    2018 and 2024 into two competing values of one slot.
    """

    dataset: str
    klass: str
    key: str = "bpuic"
    subject_slot: str = "aboutStopPoint"
    identity: tuple[str, ...] = ()
    slots: dict[str, str] = field(default_factory=dict)
    slot_types: dict[str, str] = field(default_factory=dict)
    where: str | None = None
    #: Rows the API caps at; observation tables can be very large.
    max_records: int | None = None
    #: Provenance granularity. "fact" annotates every statement, so
    #: `framex explain` names the source of each one. "layer" records the
    #: source once as a Dataset object instead: measured, per-fact annotation
    #: becomes superlinear past roughly 70,000 annotations (the spine's 17,000
    #: cost 1.7s; ist-daten's 72,000 pushed one query past 120s), so the
    #: high-volume observation layers declare "layer" and stay queryable.
    provenance: str = "fact"


#: Every spec kind carries `dataset`; this is the union used for dispatch.
Layer = Attribute | Child | Relation | Observation
