"""Which datasets are in the knowledge base, and what each one means.

This is the only file that grows as datasets are added. Everything a dataset
needs that cannot be read off its columns lives here: what its rows *are*,
which column carries identity, and whether its inventory is complete.

Ordering matters. SPINE runs first because it is the only dataset allowed to
create stop points; every LAYER joins onto the set of ids it produced.
"""

from __future__ import annotations

from .spec import Attribute, Child, Observation, Relation, Spine

# --------------------------------------------------------------------------
# The spine
#
# ODSQL filters the registry server-side. Text fields need quoted values:
# stoppoint is text, so its "true" value is not a boolean literal.
# --------------------------------------------------------------------------

SPINE = Spine(
    dataset="dienststellen-gemass-opentransportdataswiss",
    where='isocountrycode:"CH" and stoppoint:"true" and search(meansoftransport,"TRAIN")',
    key="number",
    slots={
        "designationofficial": "designation",
        "abbreviation": "abbreviation",
        "isocountrycode": "country",
        "stoppoint": "isStopPoint",
        "operatingpointwithtimetable": "hasTimetable",
        "freightservicepoint": "isFreightPoint",
        "validfrom": "validFrom",
        "validto": "validTo",
        "height": "height",
    },
    slot_types={"height": "Float", "designation": "String", "country": "String",
                "isStopPoint": "Boolean", "hasTimetable": "Boolean",
                "isFreightPoint": "Boolean"},
    references={
        "cantonabbreviation": ("inCanton", "Canton", "canton"),
        "municipalityname": ("inMunicipality", "Municipality", "mun"),
        "districtname": ("inDistrict", "District", "district"),
    },
    multi={"meansoftransport": ("servesMode", "mode")},
)


# --------------------------------------------------------------------------
# Layers, in ingestion order
# --------------------------------------------------------------------------

LAYERS: dict[str, object] = {
    # ---- 1:1 attributes -------------------------------------------------
    # wifistation has no WiFi column: `standort, bpuic, geopos`. The row's
    # existence is positive evidence. Coverage of SBB stations does not
    # license closing the slot across every Swiss rail operator.
    "wifistation": Attribute(
        dataset="wifistation",
        key="bpuic",
        presence=("hasWifi", True),
        # SBB inventory does not establish nationwide absence.
        complete_for=(),
    ),
    "lorastation": Attribute(
        dataset="lorastation",
        key="bpuic",
        presence=("hasLoraGateway", True),
        complete_for=(),
    ),
    # Departure boards are a facility elsewhere, but this dataset is 1:1 and
    # carries the board's own descriptors, so it maps as attributes.
    "abfahrtsplakate0": Attribute(
        dataset="abfahrtsplakate0",
        key="bpuic",
        presence=("hasDeparturePoster", True),
        slots={"bezeichnung_offiziell": "sourceLabel"},
        complete_for=(),
    ),
    "haltestelle-visuell-taktile-sicherheitslinie": Attribute(
        dataset="haltestelle-visuell-taktile-sicherheitslinie",
        key="bpuic",
        presence=("hasTactileSafetyLine", True),
        slots={"kanton": "sourceCanton"},
    ),
    "regionaler-linienplan": Attribute(
        dataset="regionaler-linienplan",
        key="bpuic",
        presence=("hasRegionalLineMap", True),
        slots={"url": "lineMapUrl"},
    ),

    # ---- 1:N children ---------------------------------------------------
    # Several platforms per station, each with its own length and type, so a
    # platform has to be its own object.
    "perron": Child(
        dataset="perron",
        klass="Platform",
        key="bpuic",
        identity=("fid", "linie", "p_nr"),
        slot_types={"platformLength": "Float"},
        slots={
            "p_nr": "platformNumber",
            "p_lange": "platformLength",
            "perrontyp": "platformType",
            "linie": "sourceLine",
            "km": "sourceKilometre",
        },
    ),
    "haltestelle-wartehallen": Child(
        dataset="haltestelle-wartehallen",
        klass="WaitingHall",
        key="bpuic",
        identity=("gebaudename", "linie", "km"),
        slots={
            "gebaudename": "buildingName",
            "gebaudetyp": "buildingType",
            "status": "status",
            "ort": "locality",
            "kanton": "sourceCanton",
        },
    ),
    # These two carry no national id on the Explore API -- only `bps_name`,
    # the station's name. They can therefore only be joined by exact name,
    # which is a weaker claim than an id match, so `key=None` says so out loud
    # and the resolution report counts them separately. Around a fifth of their
    # rows do not resolve (regional operators' stops outside the SBB rail
    # scope) and are reported rather than dropped quietly.
    "haltestelle-uhr": Child(
        dataset="haltestelle-uhr",
        klass="Clock",
        key=None,
        identity=("linie", "km"),
        slots={"linie": "sourceLine", "km": "sourceKilometre",
               "art_auswahl": "clockType"},
    ),
    "billetentwerter": Child(
        dataset="billetentwerter",
        klass="TicketValidator",
        key=None,
        identity=("linie", "km"),
        slots={"linie": "sourceLine", "km": "sourceKilometre",
               "gleisebene": "trackLevel"},
    ),
    "sektortafel": Child(
        dataset="sektortafel",
        klass="SectorBoard",
        key="bpuic",
        identity=("linie", "km", "kundengleisnummer"),
        slots={
            "sektor_vorderseite": "sectorFront",
            "kundengleisnummer": "trackNumber",
        },
        provenance="layer",
    ),

    # ---- N:M relation ---------------------------------------------------
    # Reified: the station/line pair carries a kilometre mark, which a bare
    # edge could not hold.
    "linie-mit-betriebspunkten": Relation(
        dataset="linie-mit-betriebspunkten",
        klass="LinePoint",
        key="bpuic",
        target=("linie", "onLine", "Line", "line"),
        subject_slot="atStopPoint",
        identity=("km",),
        slot_types={"kilometre": "Float"},
        slots={
            "km": "kilometre",
            "abkurzung_bpk": "pointAbbreviation",
            "linienname": "sourceLineName",
        },
    ),

    # ---- observations ---------------------------------------------------
    # One figure per station per year, so the period is part of the object's
    # identity rather than a competing value on one slot.
    "passagierfrequenz": Observation(
        dataset="passagierfrequenz",
        klass="PassengerFrequency",
        key="uic",
        identity=("jahr_annee_anno",),
        slot_types={"period": "String", "value": "Float", "workdayValue": "Float"},
        slots={
            "jahr_annee_anno": "period",
            "dtv_tjm_tgm": "value",
            "dwv_tmjo_tfm": "workdayValue",
            "isb_gi": "infrastructureOperator",
        },
        # 5,622 observations x 4 slots is enough annotation to hurt; the
        # Dataset object carries the source instead.
        provenance="layer",
    ),
}


#: Datasets pulled by `--init`, in order. The spine first, then the layers
#: that answer the questions this knowledge base exists for. ist-daten-sbb is
#: handled by service.py rather than a generic spec, because a trip is a
#: sequence and the sequence is in no column.
INIT_DATASETS: tuple[str, ...] = (
    "wifistation",
    "lorastation",
    "abfahrtsplakate0",
    "haltestelle-visuell-taktile-sicherheitslinie",
    "regionaler-linienplan",
    "perron",
    "haltestelle-wartehallen",
    "haltestelle-uhr",
    "billetentwerter",
    "sektortafel",
    "linie-mit-betriebspunkten",
    "passagierfrequenz",
    "ist-daten-sbb",
)

#: Handled by service.py, not by a spec in LAYERS.
SERVICE_DATASET = "ist-daten-sbb"


def known(name: str) -> bool:
    return name in LAYERS or name == SERVICE_DATASET or name == SPINE.dataset


def available() -> list[str]:
    return sorted({*LAYERS, SERVICE_DATASET, SPINE.dataset})
