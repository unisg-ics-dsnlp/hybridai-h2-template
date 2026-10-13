"""Tests for the mapping layer and the meta model.

The mapping tests use hand-written rows rather than live API calls, so they are
fast and deterministic. The values they use are the real awkward ones observed
on data.sbb.ch: float-shaped ids, boolean-as-string, pipe-delimited modes, and
station names carrying umlauts, commas and apostrophes.

The meta-model test runs the engine over the .fx layers on their own -- the
ontology has to be sound before any data is attached to it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ontology import emit, ids, mapper, registry, service
from ontology.spec import Attribute, Child, Observation, Relation, Spine

STAMP = "2026-09-18"


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        (8503424, 8503424),
        (8503424.0, 8503424),            # wifistation arrives as float64
        ("8503424", 8503424),
        ("8502113.0", 8502113),          # passagierfrequenz arrives as a string
        ("  8502113  ", 8502113),
        (None, None),
        ("", None),
        ("nan", None),
        (float("nan"), None),
        (float("inf"), None),
        (8503424.5, None),               # not a whole number: not an id
        (-5, None),
        (0, None),
        (True, None),                    # a bool is not a station
        ("Chur", None),
    ],
)
def test_normalise_bpuic(value, expected):
    assert ids.normalise_bpuic(value) == expected


def test_didok_expands_to_full_bpuic():
    # linie-mit-betriebspunkten carries the 5-digit short number.
    assert ids.bpuic_from_didok("2113") == 8502113
    assert ids.bpuic_from_didok(2113) == 8502113
    # An already-full BPUIC must pass through unchanged.
    assert ids.bpuic_from_didok(8502113) == 8502113
    assert ids.bpuic_from_didok(None) is None


def test_stop_point_id_is_the_number_not_the_name():
    assert ids.stop_point_id(8509000) == "sp_8509000"


def test_scope_matches_names_exactly_and_refuses_ambiguity():
    scope = ids.Scope.build([
        (8506302, "St. Gallen"),
        (8507000, "Bern"),
        (1, "Wil SG"),
        (2, "Wil ZH"),
        (10, "Sankt Peter"),
        (11, "Sankt Peter"),   # two distinct stations, identical name
    ])
    # Exact fold-equal match resolves; punctuation and case are ignored.
    assert scope.by_name({"standort": "St. Gallen"}) == 8506302
    assert scope.by_name({"standort": "st.gallen"}) == 8506302
    # A qualified name is its own key and resolves exactly.
    assert scope.by_name({"standort": "Wil SG"}) == 1
    # A name that is merely a prefix does not match: wifistation's bare "Wil"
    # could be Wil SG or Wil ZH, so it must stay unresolved rather than guessed.
    assert scope.by_name({"standort": "Wil"}) is None
    # A name genuinely shared by two stations is never guessed either.
    assert scope.by_name({"standort": "Sankt Peter"}) is None
    assert scope.by_name({"standort": "Nowhere"}) is None


# --------------------------------------------------------------------------
# Emitting syntax
# --------------------------------------------------------------------------

def test_literals_render_for_the_engine():
    assert emit.literal(4) == "4"
    assert emit.literal(4.0) == "4"
    assert emit.literal(4.5) == "4.5"
    assert emit.literal(True) == "true"
    # The API delivers booleans as strings; they must not stay strings, or
    # `?S[isStopPoint -> true]` would silently fail to match.
    assert emit.literal("true") == "true"
    assert emit.literal("false") == "false"
    assert emit.literal("Chur") == '"Chur"'


def test_missing_values_emit_nothing_rather_than_a_placeholder():
    # Under an open world, silence leaves a statement `unknown`, which is the
    # honest encoding of an empty cell.
    for empty in (None, "", "   ", "nan", "NaN", "None", float("nan")):
        assert emit.literal(empty) is None


def test_string_escaping_survives_real_station_names():
    assert emit.literal("Genève-Aéroport") == '"Genève-Aéroport"'
    assert emit.literal('quote " inside') == '"quote \\" inside"'
    assert emit.literal("back \\ slash") == '"back \\\\ slash"'
    assert emit.literal("Chamonix-Mont-Blanc,Argentière").startswith('"Chamonix')


def test_identifiers_are_safe_stable_and_deterministic():
    assert emit.identifier("canton", "GR") == "canton_gr"
    assert emit.identifier("mode", "CABLE_CAR") == "mode_cable_car"
    # Umlauts fold rather than vanish, so the id stays readable.
    assert emit.identifier("mun", "Zürich") == "mun_zuerich"
    assert emit.identifier("line", "Biel/Bienne") == "line_biel_bienne"
    # Same input, same output: re-running an ingest overwrites, not duplicates.
    assert emit.identifier("x", "Genève, Cornavin") == emit.identifier("x", "Genève, Cornavin")
    # Must never begin with a digit.
    assert not emit.identifier("7", prefix="p")[0].isdigit()


# --------------------------------------------------------------------------
# The spine
# --------------------------------------------------------------------------

SPINE_SPEC = Spine(
    dataset="dienststellen",
    where="",
    key="number",
    slots={"designationofficial": "designation", "isocountrycode": "country",
           "stoppoint": "isStopPoint"},
    references={"cantonabbreviation": ("inCanton", "Canton", "canton")},
    multi={"meansoftransport": ("servesMode", "mode")},
)

SPINE_ROWS = [
    {"number": 8509000, "designationofficial": "Chur", "isocountrycode": "CH",
     "stoppoint": "true", "cantonabbreviation": "GR", "meansoftransport": "TRAIN"},
    {"number": 8506302, "designationofficial": "St. Gallen", "isocountrycode": "CH",
     "stoppoint": "true", "cantonabbreviation": "SG", "meansoftransport": "BUS|TRAIN"},
]


def test_spine_defines_scope_and_splits_multi_valued_modes():
    lines, scope, report = mapper.map_spine(SPINE_SPEC, SPINE_ROWS, STAMP)
    text = "\n".join(lines)

    assert scope.ids == {8509000, 8506302}
    assert report.resolved == 2
    assert "sp_8509000:StopPoint." in lines
    assert 'sp_8509000[designation -> "Chur"].' in lines
    assert "sp_8509000[inCanton -> canton_gr]." in lines
    # "BUS|TRAIN" is two modes, not one string.
    assert "sp_8506302[servesMode -> mode_bus]." in lines
    assert "sp_8506302[servesMode -> mode_train]." in lines
    # Shared objects are declared once, not once per station.
    assert text.count("canton_gr:Canton.") == 1
    # Booleans must not arrive as quoted strings.
    assert "sp_8509000[isStopPoint -> true]." in lines


def test_spine_skips_rows_without_an_id():
    rows = SPINE_ROWS + [{"number": None, "designationofficial": "Nowhere"}]
    _, scope, report = mapper.map_spine(SPINE_SPEC, rows, STAMP)
    assert len(scope) == 2
    assert report.unidentified == 1


# --------------------------------------------------------------------------
# The four shapes
# --------------------------------------------------------------------------

@pytest.fixture
def scope():
    return ids.Scope.build([(8509000, "Chur"), (8506302, "St. Gallen")])


def test_attribute_turns_a_rows_existence_into_a_fact(scope):
    # wifistation has no WiFi column at all: the row *is* the fact.
    spec = Attribute(dataset="wifistation", presence=("hasWifi", True),
                     complete_for=("hasWifi",))
    rows = [{"bpuic": 8509000.0, "standort": "Chur"}]
    lines, report = mapper.map_attribute(spec, rows, scope, STAMP)

    assert "sp_8509000[hasWifi -> true]." in lines
    assert report.complete
    # Every row landed, so the completeness claim is honoured.
    assert emit.closed_slot("hasWifi") in lines


def test_completeness_is_withheld_when_rows_could_not_be_placed(scope):
    """The central safety property.

    Six wifistation rows carry a NaN id. Closing the slot regardless would
    make "St. Gallen has no WiFi" answer `false`, which is untrue. A spec
    *claims* completeness; the ingest decides whether the data earned it.
    """
    spec = Attribute(dataset="wifistation", presence=("hasWifi", True),
                     complete_for=("hasWifi",))
    rows = [
        {"bpuic": 8509000.0, "standort": "Chur"},
        {"bpuic": None, "standort": "Wil"},          # unresolvable
    ]
    lines, report = mapper.map_attribute(spec, rows, scope, STAMP)

    assert not report.complete
    assert report.withheld == ("hasWifi",)
    assert emit.closed_slot("hasWifi") not in lines
    assert any("Completeness NOT declared" in line for line in lines)


def test_attribute_resolves_a_missing_id_by_exact_name(scope):
    spec = Attribute(dataset="wifistation", presence=("hasWifi", True))
    rows = [{"bpuic": None, "standort": "St. Gallen"}]
    lines, report = mapper.map_attribute(spec, rows, scope, STAMP)

    assert "sp_8506302[hasWifi -> true]." in lines
    assert report.by_name == 1


def test_attribute_does_not_duplicate_a_repeated_row(scope):
    spec = Attribute(dataset="wifistation", presence=("hasWifi", True))
    rows = [{"bpuic": 8509000}, {"bpuic": 8509000.0}]
    lines, _ = mapper.map_attribute(spec, rows, scope, STAMP)
    assert lines.count("sp_8509000[hasWifi -> true].") == 1


def test_out_of_scope_rows_are_reported_not_dropped_silently(scope):
    spec = Attribute(dataset="wifistation", presence=("hasWifi", True))
    # A German station: real data, correctly outside a Swiss-rail scope.
    rows = [{"bpuic": 8015458, "standort": "Köln Hbf"}]
    lines, report = mapper.map_attribute(spec, rows, scope, STAMP)

    assert lines == [] or all("hasWifi" not in line for line in lines)
    assert 8015458 in report.out_of_scope
    assert "Köln Hbf" in report.summary()


def test_child_creates_one_object_per_row_with_a_back_reference(scope):
    spec = Child(dataset="perron", klass="Platform", identity=("linie", "p_nr"),
                 slots={"p_lange": "platformLength"})
    rows = [
        {"bpuic": 8509000, "linie": "930", "p_nr": "1", "p_lange": 420.0},
        {"bpuic": 8509000, "linie": "930", "p_nr": "2", "p_lange": 300.0},
    ]
    lines, report = mapper.map_child(spec, rows, scope, STAMP)
    text = "\n".join(lines)

    assert report.resolved == 2
    children = [line.split(":")[0] for line in lines if line.endswith(":Platform.")]
    assert len(set(children)) == 2
    assert all(f"{child}[atStopPoint -> sp_8509000]." in lines for child in children)
    assert any("[platformLength -> 420]." in line for line in lines)
    # Rollups are owned by FrameX so retracting facilities removes amenities.
    assert "sp_8509000[hasPlatform -> true]." not in lines


def test_child_deduplicates_identical_source_records(scope):
    spec = Child(dataset="perron", klass="Platform", identity=("p_nr",))
    rows = [{"bpuic": 8509000, "p_nr": "1"}, {"bpuic": 8509000, "p_nr": "1"}]
    lines, _ = mapper.map_child(spec, rows, scope, STAMP)
    subjects = [line for line in lines if line.endswith(":Platform.")]
    assert len(subjects) == len(set(subjects)) == 1


def test_relation_is_reified_so_the_edge_can_carry_a_kilometre(scope):
    spec = Relation(dataset="linie", klass="LinePoint",
                    target=("linie", "onLine", "Line", "line"),
                    identity=("km",), slots={"km": "kilometre"})
    rows = [{"bpuic": 8509000, "linie": "930", "km": 13.611}]
    lines, _ = mapper.map_relation(spec, rows, scope, STAMP)

    assert "line_930:Line." in lines
    edge = next(line.split(":")[0] for line in lines if line.endswith(":LinePoint."))
    assert f"{edge}:LinePoint." in lines
    assert f"{edge}[atStopPoint -> sp_8509000]." in lines
    assert f"{edge}[onLine -> line_930]." in lines
    assert f"{edge}[kilometre -> 13.611]." in lines


def test_observation_keeps_the_period_in_its_identity(scope):
    """Two years of one station's traffic must not collide on one slot."""
    spec = Observation(dataset="passagierfrequenz", klass="PassengerFrequency",
                       key="uic", identity=("jahr_annee_anno",),
                       slots={"jahr_annee_anno": "period", "dtv_tjm_tgm": "value"})
    rows = [
        {"uic": "8509000.0", "jahr_annee_anno": "2018", "dtv_tjm_tgm": 37900.0},
        {"uic": "8509000.0", "jahr_annee_anno": "2024", "dtv_tjm_tgm": 39000.0},
    ]
    lines, report = mapper.map_observation(spec, rows, scope, STAMP)

    assert report.resolved == 2
    nodes = [line.split(":")[0] for line in lines if line.endswith(":PassengerFrequency.")]
    assert len(set(nodes)) == 2
    for node, year, value in zip(nodes, ("2018", "2024"), (37900, 39000)):
        assert f'{node}[period -> "{year}"].' in lines
        assert f"{node}[value -> {value}]." in lines


def test_provenance_accompanies_every_ingested_fact(scope):
    spec = Attribute(dataset="wifistation", presence=("hasWifi", True))
    lines, _ = mapper.map_attribute(spec, [{"bpuic": 8509000}], scope, STAMP)
    assert any(
        line.startswith("annotate sp_8509000[hasWifi -> true]")
        and '"wifistation"' in line
        for line in lines
    )


def test_cell_contents_cannot_inject_rules(scope):
    """Rows are data, never instructions."""
    spec = Attribute(dataset="wifistation", slots={"standort": "sourceLabel"})
    hostile = "x]. evil:Admin. y[z -> true"
    lines, _ = mapper.map_attribute(spec, [{"bpuic": 8509000, "standort": hostile}],
                                    scope, STAMP)
    text = "\n".join(lines)
    assert "evil:Admin." not in lines
    # The whole payload is inside one quoted literal.
    assert 'sp_8509000[sourceLabel -> "x]. evil:Admin. y[z -> true"].' in text


# --------------------------------------------------------------------------
# Service: sequence reconstruction
# --------------------------------------------------------------------------

def _stop(trip, bpuic, dep, through="False", name=""):
    return {
        "fahrt_bezeichner": trip, "bpuic": bpuic, "abfahrtszeit": dep,
        "ankunftszeit": dep, "durchfahrt_tf": through, "betriebstag": "2026-09-17",
        "verkehrsmittel_text": "IC", "haltestellen_name": name,
    }


def _event_at(lines, station):
    return next(line.split("[")[0] for line in lines
                if line.endswith(f"[atStopPoint -> sp_{station}]."))


def test_service_chains_consecutive_real_stops_in_time_order():
    scope = ids.Scope({1, 2, 3})
    rows = [_stop("t1", n, f"2026-09-17T{8+n:02}:00:00") for n in (3, 1, 2)]
    lines, report = service.map_service(rows, scope, STAMP)
    assert report.resolved == 3
    a, b, c = [_event_at(lines, n) for n in (1, 2, 3)]
    assert f"{a}[nextRealStop -> {b}]." in lines
    assert f"{b}[nextRealStop -> {c}]." in lines
    assert any('[observedOn -> "2026-09-17"].' in line for line in lines)


def test_a_pass_through_does_not_break_a_non_stop_claim():
    rows = [_stop("t", 1, "2026-09-17T09:00:00"),
            _stop("t", 2, "2026-09-17T09:30:00", through="True"),
            _stop("t", 3, "2026-09-17T10:00:00")]
    lines, _ = service.map_service(rows, ids.Scope({1, 2, 3}), STAMP)
    a, c = [_event_at(lines, n) for n in (1, 3)]
    assert f"{a}[nextRealStop -> {c}]." in lines
    assert not any("sp_2" in line for line in lines)


def test_foreign_stops_break_nonstop_but_preserve_same_train_travel():
    rows = [_stop("t", 1, "2026-09-17T09:00:00"),
            _stop("t", 8015458, "2026-09-17T09:30:00", name="Köln Hbf"),
            _stop("t", 3, "2026-09-17T10:00:00")]
    lines, report = service.map_service(rows, ids.Scope({1, 3}), STAMP)
    a, c = [_event_at(lines, n) for n in (1, 3)]
    assert report.out_of_scope[8015458] == "Köln Hbf"
    assert f"{a}[nextRealStop -> {c}]." not in lines
    assert f"{a}[nextRideStop -> {c}]." in lines


def test_a_single_stop_trip_keeps_service_evidence_without_connections():
    lines, report = service.map_service([_stop("t", 1, "2026-09-17T09:00:00")], ids.Scope({1}), STAMP)
    assert any(line.endswith(":Trip.") for line in lines)
    assert any(line.endswith(":StopEvent.") for line in lines)
    assert not any("nextRealStop" in line for line in lines)
    assert report.resolved == 1


# --------------------------------------------------------------------------
# The meta model, on its own
# --------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("framex") is None, reason="framex not on PATH")
def test_meta_model_is_sound_before_any_data_is_attached():
    from ontology import builder

    files = [str(p) for p in builder.ontology_files()]
    assert files, "the meta model should ship with the package"

    check = subprocess.run(["framex", "check", *files], capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr

    validate = subprocess.run(["framex", "validate", *files],
                              capture_output=True, text=True)
    assert validate.returncode == 0, validate.stdout + validate.stderr
    assert '"violations":[]' in validate.stdout.replace(" ", "")


@pytest.mark.skipif(shutil.which("framex") is None, reason="framex not on PATH")
def test_meta_model_derives_scope_and_bounded_reachability(tmp_path):
    """The reachability rules must answer the questions this model exists for.

    Reachability is deliberately bounded: an unbounded transitive closure over
    the real network exceeds the engine's join limit, so the model offers
    reachDirect / reach1 / reach2 instead.
    """
    from ontology import builder

    for source in builder.ontology_files():
        shutil.copyfile(source, tmp_path / source.name)

    # A minimal three-station line: SG -> WT -> ZH.
    (tmp_path / "60-data.fx").write_text(
        """
sp_sg:StopPoint. sp_sg[country -> "CH"]. sp_sg[isStopPoint -> true].
sp_sg[servesMode -> mode_train].
sp_wt:StopPoint. sp_wt[country -> "CH"]. sp_wt[isStopPoint -> true].
sp_wt[servesMode -> mode_train].
sp_zh:StopPoint. sp_zh[country -> "CH"]. sp_zh[isStopPoint -> true].
sp_zh[servesMode -> mode_train].
t1:Trip. t1[tripRef -> "t1"]. t1[serviceCategory -> "IC"].
e1:StopEvent. e1[stopSequence -> 1]. e1[cancelled -> false]. e1[passThrough -> false]. e1[ofTrip -> t1]. e1[atStopPoint -> sp_sg]. e1[nextRealStop -> e2].
e2:StopEvent. e2[stopSequence -> 2]. e2[cancelled -> false]. e2[passThrough -> false]. e2[ofTrip -> t1]. e2[atStopPoint -> sp_wt]. e2[nextRealStop -> e3].
e3:StopEvent. e3[stopSequence -> 3]. e3[cancelled -> false]. e3[passThrough -> false]. e3[ofTrip -> t1]. e3[atStopPoint -> sp_zh].

// Scope is derived, not filtered.
expect true: sp_sg:TrainStopPoint.
expect true: sp_sg:PassengerStopPoint.

// Consecutive real stops are a non-stop connection.
expect true: sp_sg[nonStopTo -> sp_wt].
expect true: sp_wt[nonStopTo -> sp_zh].

// Two stops apart is NOT non-stop, and under an open world that is
// `unknown` rather than `false`.
expect unknown: sp_sg[nonStopTo -> sp_zh].

// Journeys are request-scoped. A shared train is not an interchange.
expect unknown: sp_sg[changeAt(sp_wt) -> sp_zh].

// Direction matters: the chain runs the way the train runs.
expect unknown: sp_zh[nonStopTo -> sp_wt].

// Long-distance service is derived from the trip's category.
expect true: sp_sg:LongDistanceStation.
""",
        encoding="utf-8",
    )

    files = sorted(str(p) for p in tmp_path.glob("*.fx"))
    result = subprocess.run(["framex", "test", *files], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


# --------------------------------------------------------------------------
# Chunking for the persistent session
# --------------------------------------------------------------------------

def test_chunking_never_orphans_an_annotation():
    """The engine rejects `annotate` whose fact is in another loaded program.

    For one-shot syntax checks and live `add` calls, every chunk must be
    self-contained: each `annotate` in it must have its exact
    fact asserted somewhere in that same chunk. A fact line may legitimately
    be repeated to guarantee that (a repeated assertion is a no-op for the
    engine); an annotation itself must never be lost or duplicated.
    """
    from runtime.source import _chunks

    lines = []
    for n in range(4000):
        lines.append(f"sp_{n}[slot -> {n}].")
        lines.append(f'annotate sp_{n}[slot -> {n}] source "d" time "t".')

    chunks = list(_chunks(lines, limit=400))
    assert len(chunks) > 1, "test needs the input to actually be split"

    seen_annotations = []
    for chunk in chunks:
        asserted = {line[:-1] for line in chunk if not line.startswith("annotate ")}
        for line in chunk:
            if not line.startswith("annotate "):
                continue
            seen_annotations.append(line)
            fact, _, _ = line[len("annotate "):].partition(" source ")
            assert fact in asserted, f"chunk is missing the fact for {line!r}"

    original_annotations = [line for line in lines if line.startswith("annotate ")]
    assert seen_annotations == original_annotations


def test_chunking_reasserts_a_fact_split_from_a_non_adjacent_annotation():
    """A fact and its annotation are not guaranteed to be adjacent.

    The emitter usually places them next to each other, but not always --
    map_spine's own bpuic fact is a real counter-example (its annotation is
    emitted after the dataset's other slot facts, not right behind it) -- so
    the chunker must not rely on adjacency to keep a chunk self-contained.
    """
    from runtime.source import _chunks

    lines = [
        "sp_1[bpuic -> 1].",
        'sp_1[designation -> "A"].',
        'annotate sp_1[designation -> "A"] source "d" time "t".',
        'annotate sp_1[bpuic -> 1] source "d" time "t".',
    ]
    chunks = list(_chunks(lines, limit=100))
    assert len(chunks) > 1, "test needs the input to actually be split"
    for chunk in chunks:
        for line in chunk:
            if not line.startswith("annotate "):
                continue
            fact, _, _ = line[len("annotate "):].partition(" source ")
            assert fact + "." in chunk, f"chunk is missing the fact for {line!r}"


def test_spine_keeps_each_annotation_behind_its_fact(scope):
    """Emitter guarantee the chunker relies on."""
    lines, _, _ = mapper.map_spine(SPINE_SPEC, SPINE_ROWS, STAMP)
    for index, line in enumerate(lines):
        if not line.startswith("annotate "):
            continue
        # `annotate X[s -> v] source ...` must follow `X[s -> v].`
        fact = line[len("annotate "):].split(" source ")[0] + "."
        assert lines[index - 1] == fact, (
            f"annotation at {index} does not follow its fact: {line}"
        )


# --------------------------------------------------------------------------
# Progress reporting
# --------------------------------------------------------------------------

def test_human_durations_read_naturally():
    from cli.progress import human

    assert human(0.25) == "250ms"
    assert human(1.5) == "1.5s"
    assert human(20.5) == "20.5s"
    assert human(95) == "1m35s"


def test_progress_is_a_no_op_when_disabled():
    """Callers should never have to branch on whether progress is shown."""
    import io

    from cli.progress import Progress

    stream = io.StringIO()
    bar = Progress(enabled=False, stream=stream)
    bar.start("phase", total=3)
    bar.step()
    bar.finish("note")
    bar.close()
    text = stream.getvalue()
    # A disabled reporter still says what happened, but draws no bar.
    assert "phase" in text
    assert "█" not in text and "\r" not in text


def test_progress_writes_a_counted_bar_to_a_tty():
    import io

    from cli.progress import Progress

    stream = io.StringIO()
    bar = Progress(enabled=True, stream=stream)
    bar.start("sending data", total=4)
    for _ in range(4):
        bar.step()
    bar.finish("4 chunks")
    bar.close()
    text = stream.getvalue()
    assert "█" in text
    assert "4/4" in text
    assert "sending data: " in text and "4 chunks" in text


def test_estimated_bar_never_claims_to_be_finished_early():
    """An un-counted phase must not show a full bar while still working.

    The closure phase is one blocking engine call, so its bar is driven by an
    estimate. If the estimate is short, the bar must still read as in-progress.
    """
    import io

    from cli.progress import Progress

    stream = io.StringIO()
    bar = Progress(enabled=True, stream=stream)
    # An estimate already in the past: elapsed/estimate far exceeds 1.
    bar.start("deriving closure", estimate=0.000001)
    bar._paint()
    bar.close()
    line = stream.getvalue()
    assert "working" in line
    # Capped below full, so it cannot look complete before it is.
    assert "░" in line


def test_progress_does_not_report_a_negative_time_remaining():
    import io

    from cli.progress import Progress

    stream = io.StringIO()
    bar = Progress(enabled=True, stream=stream)
    bar.start("phase", estimate=0.000001)
    bar._paint()
    bar.close()
    assert "-" not in stream.getvalue().split("·")[-1]


# --------------------------------------------------------------------------
# Result rendering
# --------------------------------------------------------------------------

def test_render_distinguishes_truth_values_from_rows():
    from cli.output import render

    assert render({"status": "true"}) == "true"
    assert render({"status": "unknown"}) == "unknown"
    assert render({"status": "false"}) == "false"
    # An empty binding list is an answer, not a malfunction.
    assert render({"status": "bindings", "bindings": []}) == "(no bindings)"
    assert render({"status": "bindings", "bindings": [{"W": "true"}]}) == "W = true"
    two = render({"status": "bindings",
                  "bindings": [{"B": "sp_2", "Name": '"Bern"'}]})
    assert two == 'B = sp_2, Name = "Bern"'


# --------------------------------------------------------------------------
# Registry consistency
# --------------------------------------------------------------------------

def test_every_init_dataset_is_mapped():
    for dataset in registry.INIT_DATASETS:
        assert registry.known(dataset), f"{dataset} has no mapping"


def test_registry_specs_name_themselves_consistently():
    for name, spec in registry.LAYERS.items():
        assert spec.dataset == name, f"{name} declares dataset {spec.dataset!r}"


def test_the_spine_cannot_be_attached_as_a_layer():
    # It defines which stop points exist; replacing it would orphan the layers.
    assert registry.SPINE.dataset not in registry.LAYERS
