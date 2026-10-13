"""Ontology and runtime regression counterexamples; all engine calls are local."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from framex.framex import Client, FrameXError
from ontology import builder, emit, mapper, registry, service
from ontology.ids import Scope
from runtime.query import QueryError
from runtime.session import Session
from runtime.source import _chunks, _fact_of_annotation, _program_chunks
from ontology.spec import Child, Observation, Relation

DAY = "2026-09-17"


def stop(trip, station, time, **extra):
    time = f"{DAY}T{time}:00" if time and len(time) == 5 else time
    return dict(fahrt_bezeichner=trip, bpuic=station, betriebstag=DAY,
                ankunftszeit=time, abfahrtszeit=time, an_prognose=time, ab_prognose=time,
                an_prognose_status="REAL", ab_prognose_status="REAL",
                durchfahrt_tf=False, faellt_aus_tf=False, verkehrsmittel_text="IC", **extra)


@pytest.fixture
def client():
    if not shutil.which("framex"):
        pytest.skip("framex not installed")
    with Client() as c:
        yield c


def load(client, rows=(), scope=None, extra=""):
    scope = scope or Scope(set(range(1, 7)))
    lines, _ = service.map_service(rows, scope, DAY)
    spine = "\n".join(f"sp_{n}:StopPoint. sp_{n}[bpuic -> {n}]." for n in scope.ids)
    client.load("\n".join(p.read_text() for p in builder.ontology_files()) + "\n" + spine + "\n" + "\n".join(lines) + "\n" + extra)
    return lines


def request(client, origin=1, name="journey", day=DAY, buffer=300):
    # Same adapter boundary as Session.prepare_journey: arithmetic is external,
    # while FrameX must establish every route and transfer.
    arrivals = client.query("?- ?E[arrivalEpoch -> ?A].").get("bindings", [])
    lines = [f'{r["E"]}[transferReady({name}) -> {int(r["A"])+buffer}].' for r in arrivals]
    lines += [f'{name}:JourneyRequest. {name}[origin -> sp_{origin}].',
              f'{name}[onDay -> "{day}"]. {name}[notBefore -> 0].',
              f'{name}[transferSeconds -> {buffer}].']
    client.add(source="\n".join(lines))


def truth(client, goal):
    return client.query("?- " + goal + ".")["status"]


def test_same_train_all_intermediate_stops_cost_zero_changes(client):
    load(client, [stop("t", n, f"{8+n:02}:00") for n in range(1, 6)])
    request(client)
    assert truth(client, "journey[reachDirect -> sp_5]") == "true"
    assert truth(client, "journey[reach2 -> sp_5]") == "true"
    assert truth(client, "sp_1[nonStopTo -> sp_5]") == "unknown"
    assert truth(client, "journey[changeAt(sp_2) -> sp_5]") == "unknown"
    assert truth(client, "sp_5[nonStopTo -> sp_4]") == "unknown"


@pytest.mark.parametrize("time,expected", [("08:00", "unknown"), ("13:04", "unknown"), ("13:05", "true"), ("13:10", "true")])
def test_transfers_require_different_trains_and_enough_time(client, time, expected):
    load(client, [stop("one", 1, "12:00"), stop("one", 2, "13:00"),
                  stop("two", 2, time), stop("two", 3, "14:00")])
    request(client)
    assert truth(client, "journey[changeAt(sp_2) -> sp_3]") == expected
    assert truth(client, "journey[reach1 -> sp_3]") == expected


def test_two_changes_and_request_isolation(client):
    load(client, [stop("one", 1, "09:00"), stop("one", 2, "10:00"),
                  stop("two", 2, "10:10"), stop("two", 3, "11:00"),
                  stop("three", 3, "11:10"), stop("three", 4, "12:00")])
    request(client)
    request(client, origin=4, name="reverse")
    assert truth(client, "journey[reach2 -> sp_4]") == "true"
    assert truth(client, "journey[reach1 -> sp_4]") == "unknown"
    assert truth(client, "reverse[reach2 -> sp_1]") == "unknown"


@pytest.mark.parametrize("cancelled", [True, "True", "1"])
def test_cancelled_train_cannot_supply_service_or_connections(client, cancelled):
    rows = [stop("t", 1, "09:00"), stop("t", 2, "10:00")]
    for r in rows:
        r["faellt_aus_tf"] = cancelled
    load(client, rows)
    request(client)
    assert truth(client, "sp_1[nonStopTo -> sp_2]") == "unknown"
    assert truth(client, "sp_1:LongDistanceStation") == "unknown"
    assert truth(client, "journey[reachDirect -> sp_2]") == "unknown"


def test_partial_cancellation_and_unknown_station_break_continuity(client):
    for middle in (dict(bpuic=2, faellt_aus_tf=True), dict(bpuic=None)):
        rows = [stop("t", 1, "09:00"), stop("t", 2, "10:00"), stop("t", 3, "11:00")]
        rows[1].update(middle)
        load(client, rows)
        request(client)
        assert truth(client, "journey[reachDirect -> sp_3]") == "unknown"
        assert truth(client, "sp_1[nonStopTo -> sp_3]") == "unknown"


def test_foreign_stop_is_direct_but_not_nonstop(client):
    load(client, [stop("t", 1, "09:00"), stop("t", 99, "10:00"), stop("t", 3, "11:00")])
    request(client)
    assert truth(client, "journey[reachDirect -> sp_3]") == "true"
    assert truth(client, "sp_1[nonStopTo -> sp_3]") == "unknown"


def test_single_visible_stop_keeps_service_and_does_not_become_facility(client):
    load(client, [stop("t", 1, "09:00")], extra="lp:LinePoint. lp[atStopPoint -> sp_1].")
    assert truth(client, "sp_1:LongDistanceStation") == "true"
    assert truth(client, "sp_1[hasFacility -> ?F]") == "unknown"


@pytest.mark.parametrize("other_time", [None, "09:00", "not-a-time"])
def test_uncertain_order_does_not_invent_connections(client, other_time):
    load(client, [stop("t", 1, "09:00"), stop("t", 2, other_time)])
    request(client)
    assert truth(client, "journey[reachDirect -> sp_2]") == "unknown"
    assert truth(client, "sp_1[nonStopTo -> sp_2]") == "unknown"
    assert truth(client, "sp_2[nonStopTo -> sp_1]") == "unknown"


def test_trip_ids_include_day_and_survive_reordering():
    rows = [stop("t", 1, "09:00"), stop("t", 2, "10:00")]
    next_day = [dict(r, betriebstag="2026-09-18") for r in rows]
    a, _ = service.map_service(rows + next_day, Scope({1, 2}), DAY)
    b, report = service.map_service(list(reversed(rows + next_day)) + rows, Scope({1, 2}), DAY)
    assert a == b
    assert len([x for x in a if x.endswith(":Trip.")]) == 2
    assert report.total == 6


def test_scheduled_times_are_not_presented_as_observed_times(client):
    rows = [stop("t", 1, "09:00"), stop("t", 2, "10:00")]
    for r in rows:
        r["ab_prognose_status"] = "PROGNOSE"
    load(client, rows)
    request(client)
    assert truth(client, "journey[reachDirect -> sp_2]") == "unknown"
    assert truth(client, "sp_1[nonStopTo -> sp_2]") == "true"


def test_cross_trip_edges_cannot_prove_service(client):
    extra = '''a:StopEvent. a[cancelled -> false]. a[passThrough -> false].
    a[ofTrip -> t1]. a[atStopPoint -> sp_1]. a[nextRealStop -> b].
    b:StopEvent. b[cancelled -> false]. b[passThrough -> false].
    b[ofTrip -> t2]. b[atStopPoint -> sp_2].'''
    load(client, extra=extra)
    assert truth(client, "t1[offersNonStop(sp_1) -> sp_2]") == "unknown"


@pytest.mark.parametrize("status,expected", [("BESTEHEND", "true"), ("PROJEKTIERT ABBRUCH", "true"), ("PROJEKTIERT NEU", "unknown"), (None, "unknown")])
def test_waiting_hall_availability_requires_lifecycle_evidence(client, status, expected):
    lines, _ = mapper.map_child(registry.LAYERS['haltestelle-wartehallen'],
                               [{"bpuic": 1, "status": status}], Scope({1}), DAY)
    load(client, extra="\n".join(lines))
    assert truth(client, "sp_1[hasWaitingHall -> true]") == expected


def test_retracting_last_platform_removes_rollup(client):
    lines, _ = mapper.map_child(registry.LAYERS['perron'], [{"bpuic": 1, "p_nr": 1}], Scope({1}), DAY)
    load(client, extra="\n".join(lines))
    assert truth(client, "sp_1[hasPlatform -> true]") == "true"
    fact = next(x[:-1] for x in lines if x.endswith(":Platform."))
    client.retract(fact=fact)
    assert truth(client, "sp_1[hasPlatform -> true]") == "unknown"


def test_raw_comment_data_cannot_inject_facts(client, tmp_path):
    rows = [{"standort": "Unknown\nintruder:Admin.\r\n//"}]
    builder.build_layer(tmp_path, 'wifistation', lambda *a, **kw: rows, Scope({1}), DAY)
    load(client, extra=builder.layer_path(tmp_path, 'wifistation').read_text())
    assert truth(client, "intruder:Admin") == "unknown"


def test_annotation_separator_in_literal_is_not_syntax(client):
    value = emit.literal('a source b " source c \\ source d')
    fact = emit.fact('s', 'label', value)
    annotation = emit.annotation('s', 'label', value, 'source x', DAY)
    assert _fact_of_annotation(annotation) == fact[:-1]
    for chunk in _chunks([fact, annotation], limit=500):
        client.load('world open.\n' + '\n'.join(chunk))
    assert truth(client, fact[:-1]) == 'true'


def test_chunk_budget_counts_json_escaping_and_rejects_oversized_units():
    lines = [emit.fact('s', f'v{i}', emit.literal('\\"'*10000)) for i in range(25)]
    for chunk in _chunks(lines):
        envelope = json.dumps(dict(command='add', source='\n'.join(chunk)), ensure_ascii=False)
        assert len(envelope.encode()) < 1048576
    with pytest.raises(QueryError, match='exceeds'):
        list(_chunks([emit.fact('s', 'v', emit.literal('x'*1_100_000))]))


def test_bulk_chunks_preserve_multiline_rules_and_escaped_request_budget(client):
    lines = ['world open.', 'item:Thing.', 'item[active -> true].',
             '?X:Eligible <-', '    ?X:Thing', '    AND ?X[active -> true].']
    chunks = list(_program_chunks(lines, limit=100))
    assert len(chunks) > 1
    client.load_begin()
    for chunk in chunks:
        client.load_chunk('\n'.join(chunk))
    client.load_commit()
    assert truth(client, 'item:Eligible') == 'true'

    escaped = [emit.fact('s', f'v{i}', emit.literal('\\"'*10000)) for i in range(25)]
    for chunk in _program_chunks(escaped):
        envelope = json.dumps(dict(command='load_chunk', file='60-data.fx',
                                   source='\n'.join(chunk)), ensure_ascii=False)
        assert len(envelope.encode()) < 1048576
    with pytest.raises(QueryError, match='exceeds'):
        list(_program_chunks([emit.fact('s', 'v', emit.literal('x'*1_100_000))]))


def test_session_bulk_load_resolves_annotations_across_chunks_and_keeps_provenance(
    tmp_path, monkeypatch,
):
    from runtime import session as module
    if not shutil.which('framex'):
        pytest.skip('framex not installed')
    monkeypatch.setattr(module, 'CHUNK_BYTES', 150)
    (tmp_path / '00-core.fx').write_text('world open.\n?X:Named <- ?X[label -> ?L].')
    value = emit.literal('a source b " source c \\ source d')
    fact = emit.fact('item', 'label', value)
    annotation = emit.annotation('item', 'label', value, 'original-dataset', DAY)
    lines = [fact, *[f'other_{i}:Thing.' for i in range(20)], annotation]
    (tmp_path / '60-data.fx').write_text('\n'.join(lines))
    chunks = list(_program_chunks(lines, limit=150))
    assert fact in chunks[0] and annotation in chunks[-1]
    assert fact not in chunks[-1]
    with Session(tmp_path) as session:
        assert session.client.transcript == []
        assert truth(session.client, 'item:Named') == 'true'
        assert 'original-dataset' in session.explain(fact[:-1])
        session.client.retract(fact=fact[:-1])
        assert truth(session.client, 'item:Named') == 'unknown'


@pytest.mark.parametrize('world', ['open', 'closed'])
def test_session_bulk_load_matches_one_shot_inference_and_alternate_support(client, tmp_path, world):
    source = (f'world {world}.\na:Evidence.\nb:Evidence.\n'
              'result[known -> true] <- a:Evidence.\n'
              'result[known -> true] <- b:Evidence.\n')
    (tmp_path / '00-core.fx').write_text(source)
    client.load(source)
    with Session(tmp_path) as session:
        for query in ('?- ?X:Evidence.', '?- result[known -> true].',
                      '?- absent[known -> true].'):
            assert session.ask(query)[0] == client.query(query)
        for fact in ('a:Evidence', 'b:Evidence'):
            session.client.retract(fact=fact)
            client.retract(fact=fact)
            assert session.ask('?- result[known -> true].')[0] == client.query(
                '?- result[known -> true].'
            )


@pytest.mark.parametrize('spec', [Child(dataset='d', klass='Clock', identity=('id',), slots={'v':'label'}),
                                 Observation(dataset='d', klass='StationUsage', identity=('id',), slots={'v':'label'}),
                                 Relation(dataset='d', klass='LinePoint', target=('line','onLine','Line','line'), identity=('id',), slots={'v':'label'})])
def test_record_identity_never_merges_slug_collisions_and_is_order_independent(spec):
    rows = [{'bpuic':1,'id':key,'v':str(i),'line':1} for i,key in enumerate(('x_3','x','x'))]
    a,_=mapper.map_layer(spec, rows, Scope({1}), DAY)
    b,_=mapper.map_layer(spec, list(reversed(rows))+rows, Scope({1}), DAY)
    assert set(a) == set(b)
    assert len([x for x in a if x.endswith(':'+spec.klass+'.')]) == 3


def test_generated_schema_accepts_whole_decimal_measurements(client):
    spine, _, _ = mapper.map_spine(registry.SPINE, [{'number':1,'height':500.0}], DAY)
    obs,_=mapper.map_layer(registry.LAYERS['passagierfrequenz'], [{'uic':1,'jahr_annee_anno':2025,'dtv_tjm_tgm':37900}], Scope({1}),DAY)
    rel,_=mapper.map_layer(registry.LAYERS['linie-mit-betriebspunkten'], [{'bpuic':1,'linie':900,'km':0}], Scope({1}),DAY)
    load(client, extra='\n'.join(spine+obs+rel))
    validation=client.validate()
    assert not validation['violations']
    assert all(x['status']=='satisfied' for x in validation['schema_checks']), validation


def test_failed_rebuild_preserves_old_generation(tmp_path):
    workspace=tmp_path/'fx'; workspace.mkdir()
    (workspace/'60-old.fx').write_text('old:Data.')
    (workspace/'manifest.json').write_text('{"old":true}')
    def unavailable(*args, **kwargs):
        raise RuntimeError('source unavailable')
    with pytest.raises(RuntimeError, match='unavailable'):
        builder.init(workspace, unavailable, datasets=[], progress=lambda _:None)
    assert (workspace/'60-old.fx').read_text() == 'old:Data.'
    assert (workspace/'manifest.json').read_text() == '{"old":true}'


def test_spine_fetch_orders_pages_by_unique_station_id(tmp_path):
    def overlapping_pages(name, **kwargs):
        assert name == registry.SPINE.dataset
        assert kwargs["where"] == registry.SPINE.where
        rows = [{'number': 8509000}, {'number': 8503006}]
        # Model an unordered API page overlapping its predecessor.
        return rows if kwargs.get("order_by") == "number" else [rows[0], rows[0]]

    scope = builder.build_spine(tmp_path, overlapping_pages, DAY, [])
    assert scope.ids == {8509000, 8503006}
    assert 'sp_8503006:StopPoint.' in (tmp_path / builder.SPINE_FILE).read_text()


@pytest.mark.parametrize("duplicate_id", [8509000, "8509000", 8509000.0])
def test_overlapping_spine_pages_fail_without_replacing_workspace(tmp_path, duplicate_id):
    workspace = tmp_path / "fx"
    workspace.mkdir()
    (workspace / "old.fx").write_text("old:Data.")
    (workspace / "manifest.json").write_text('{"old":true}')
    rows = [{'number': 8509000}, {'number': duplicate_id}, {'number': 8503006}]
    with pytest.raises(ValueError, match="duplicate stop-point ID 8509000"):
        builder.init(workspace, lambda *args, **kwargs: rows,
                     datasets=[], progress=lambda _: None)
    assert (workspace / "old.fx").read_text() == "old:Data."
    assert (workspace / "manifest.json").read_text() == '{"old":true}'
    assert sorted(path.name for path in workspace.iterdir()) == ["manifest.json", "old.fx"]


def test_session_journey_api_and_typed_validation(tmp_path):
    builder.copy_meta_model(tmp_path)
    lines,_=service.map_service([stop('t',1,'09:00'),stop('t',2,'10:00')],Scope({1,2}),DAY)
    (tmp_path/'60-data.fx').write_text('sp_1:StopPoint. sp_1[bpuic -> 1].\nsp_2:StopPoint. sp_2[bpuic -> 2].\n'+'\n'.join(lines))
    with Session(tmp_path) as session:
        name=session.prepare_journey(1,DAY,not_before=DAY+'T08:00:00',transfer_seconds=300)
        assert session.ask(f'?- {name}[reachDirect -> sp_2].')[0]['status']=='true'
        assert all(x['status']=='satisfied' for x in session.validate()['schema_checks'])


@pytest.fixture
def transfer_workspace(tmp_path):
    if not shutil.which("framex"):
        pytest.skip("framex not installed")
    builder.copy_meta_model(tmp_path)
    rows = [stop("one", 1, "09:00"), stop("one", 2, "10:00"),
            stop("two", 2, "10:05"), stop("two", 3, "11:00")]
    lines, _ = service.map_service(rows, Scope({1, 2, 3}), DAY)
    spine = [f"sp_{n}:StopPoint. sp_{n}[bpuic -> {n}]." for n in (1, 2, 3)]
    (tmp_path / "60-data.fx").write_text("\n".join(spine + lines))
    return tmp_path


def test_journey_rejects_delayed_arrival_mutation_and_fresh_session_recomputes(transfer_workspace):
    parameters = dict(origin=1, day=DAY, not_before=DAY + "T08:00:00", transfer_seconds=300)
    event_query = ('?- ?E[atStopPoint -> sp_2] AND ?E[ofTrip -> ?T] '
                   'AND ?T[tripRef -> "one"] AND ?E[arrivalEpoch -> ?A] '
                   'AND ?E[departureEpoch -> ?D].')
    with Session(transfer_workspace) as session:
        event = session.ask(event_query)[0]["bindings"][0]
        request_id = session.prepare_journey(**parameters)
        assert session.ask(f"?- {request_id}[reach1 -> sp_3].")[0]["status"] == "true"
        count = len(session.client.transcript)
        with pytest.raises(QueryError, match="open a new Session"):
            session.client.retract(fact=f"{event['E']}[arrivalEpoch -> {event['A']}]")
        assert len(session.client.transcript) == count
        assert session.ask(f"?- {event['E']}[arrivalEpoch -> {event['A']}].")[0]["status"] == "true"

    # The same update is supported before preparing journeys in a fresh session.
    with Session(transfer_workspace) as session:
        event = session.ask(event_query)[0]["bindings"][0]
        for slot, value in (("arrivalEpoch", event["A"]), ("departureEpoch", event["D"])):
            session.client.retract(fact=f"{event['E']}[{slot} -> {value}]")
            session.client.add(source=f"{event['E']}[{slot} -> {int(value) + 120}].")
        request_id = session.prepare_journey(**parameters)
        assert session.ask(f"?- {request_id}[reach1 -> sp_3].")[0]["status"] == "unknown"


@pytest.mark.parametrize("method,args,kwargs", [
    ("add", (), {"source": "new:Thing."}),
    ("retract", (), {"fact": "sp_1:StopPoint"}),
    ("retract_source", ("source",), {}),
    ("world", ("closed",), {}),
    ("load", ("world open. new:Thing.",), {}),
    ("load_program", ("world open. new:Thing.",), {}),
    ("load_begin", (), {}),
    ("load_chunk", ("new:Thing.",), {}),
    ("load_commit", (), {}),
    ("load_abort", (), {}),
    ("run", (), {}),
    ("rdf_import", (), {"source": "rdf", "format": "turtle", "document": ""}),
    ("request", ("add",), {"source": "new:Thing."}),
    ("request", ("unknown_command",), {}),
], ids=["add", "retract", "retract-source", "world", "load", "load-program",
        "load-begin", "load-chunk", "load-commit", "load-abort", "run", "rdf-import",
        "raw-add", "unknown-command"])
def test_prepared_journey_blocks_public_mutation_paths(transfer_workspace, method, args, kwargs):
    with Session(transfer_workspace) as session:
        request_id = session.prepare_journey(
            1, DAY, not_before=DAY + "T08:00:00", transfer_seconds=300)
        count = len(session.client.transcript)
        with pytest.raises(QueryError, match="after journey preparation starts"):
            getattr(session.client, method)(*args, **kwargs)
        assert len(session.client.transcript) == count
        assert session.ask(f"?- {request_id}[reach1 -> sp_3].")[0]["status"] == "true"


def test_prepared_journey_keeps_reads_and_additional_requests_available(transfer_workspace):
    with Session(transfer_workspace) as session:
        first = session.prepare_journey(1, DAY, not_before=DAY + "T08:00:00", transfer_seconds=300)
        count = len(session.client.transcript)
        assert session.prepare_journey(1, DAY, not_before=DAY + "T08:00:00", transfer_seconds=300) == first
        assert len(session.client.transcript) == count
        second = session.prepare_journey(1, DAY, not_before=DAY + "T08:00:00", transfer_seconds=360)
        assert second != first
        assert session.ask(f"?- {first}[reach1 -> sp_3].")[0]["status"] == "true"
        assert session.ask(f"?- {second}[reach1 -> sp_3].")[0]["status"] == "unknown"
        assert "transferReady" in session.explain(f"{first}[reach1 -> sp_3]")
        assert session.client.stats()
        assert all(check["status"] == "satisfied" for check in session.validate()["schema_checks"])


def test_failed_journey_preparation_still_blocks_mutations(transfer_workspace, monkeypatch):
    with Session(transfer_workspace) as session:
        original_query = session.client.query
        def fail_query(query):
            if "[reached0 ->" in query:
                raise QueryError("preparation failed")
            return original_query(query)
        monkeypatch.setattr(session.client, "query", fail_query)
        with pytest.raises(QueryError, match="preparation failed"):
            session.prepare_journey(1, DAY, not_before=DAY + "T08:00:00", transfer_seconds=300)
        assert not session._journey_requests
        with pytest.raises(QueryError, match="after journey preparation starts"):
            session.client.add(source="new:Thing.")


@pytest.mark.parametrize("origin,day,message", [
    (999, DAY, "unknown journey origin 999"),
    (1, "2026-09-18", "available operating days: 2026-09-17"),
])
def test_invalid_journey_inputs_do_not_write_or_lock(transfer_workspace, origin, day, message):
    with Session(transfer_workspace) as session:
        with pytest.raises(QueryError, match=message):
            session.prepare_journey(origin, day, not_before=day + "T08:00:00", transfer_seconds=300)
        assert session.ask("?- ?R:JourneyRequest.")[0]["status"] == "unknown"
        session.client.add(source='sp_4:StopPoint. sp_4[bpuic -> 4].')
        request_id = session.prepare_journey(1, DAY, not_before=DAY + "T08:00:00", transfer_seconds=300)
        assert session.ask(f"?- {request_id}[reach1 -> sp_3].")[0]["status"] == "true"


def test_journey_without_service_data_reports_no_available_days(transfer_workspace):
    (transfer_workspace / "60-data.fx").write_text('sp_1:StopPoint. sp_1[bpuic -> 1].')
    with Session(transfer_workspace) as session:
        with pytest.raises(QueryError, match=r"available operating days: none \(no dated trips loaded\)"):
            session.prepare_journey(1, DAY, not_before=DAY + "T08:00:00", transfer_seconds=300)


def test_journey_checks_loaded_data_and_lists_all_days(transfer_workspace):
    with Session(transfer_workspace) as session:
        # Data may change before preparation; diagnostics must use the engine,
        # rather than a manifest or a cache populated when the session loaded.
        session.client.add(source='sp_4:StopPoint. sp_4[bpuic -> 4]. '
                                 'extra:Trip. extra[tripRef -> "extra"]. '
                                 'extra[observedOn -> "2026-09-18"].')
        with pytest.raises(QueryError, match="available operating days: 2026-09-17, 2026-09-18"):
            session.prepare_journey(4, "2026-09-19", not_before="2026-09-19T08:00:00", transfer_seconds=300)
        # A known station and day with no matching rides is a valid empty result.
        request_id = session.prepare_journey(4, "2026-09-18", not_before="2026-09-18T08:00:00", transfer_seconds=300)
        assert session.ask(f"?- {request_id}[reach2 -> ?S].")[0]["status"] == "unknown"


@pytest.mark.parametrize("origin,day,message", [
    (999, DAY, "unknown journey origin 999"),
    (1, "2026-09-18", "no journey service data for operating day 2026-09-18"),
])
def test_cli_reports_journey_input_errors(transfer_workspace, capsys, origin, day, message):
    from cli import app
    assert app.main(["--journey", str(origin), "--day", day, "--not-before", day + "T08:00:00",
                     "--transfer-seconds", "300", "--workspace", str(transfer_workspace),
                     "--quiet"]) == 1
    output = capsys.readouterr()
    assert f"error: {message}" in output.err
    assert "Request:" not in output.out
    assert "Traceback" not in output.err


def test_failed_layer_keeps_previous_base_and_manifest(tmp_path):
    workspace = tmp_path / 'fx'
    workspace.mkdir()
    (workspace / 'old.fx').write_text('old:Data.')
    (workspace / 'manifest.json').write_text('{"old": true}')
    def loader(name, **kwargs):
        if name == registry.SPINE.dataset:
            return [{'number': 1}]
        raise RuntimeError('layer unavailable')
    with pytest.raises(builder.BuildError, match='previous generation retained'):
        builder.init(workspace, loader, datasets=['wifistation'], progress=lambda _: None)
    assert sorted(p.name for p in workspace.iterdir()) == ['manifest.json', 'old.fx']
    assert json.loads((workspace / 'manifest.json').read_text()) == {'old': True}


def test_failed_publication_restores_previous_generation(tmp_path, monkeypatch):
    workspace = tmp_path / 'fx'
    workspace.mkdir()
    (workspace / 'old.fx').write_text('old:Data.')
    real_replace = builder.os.replace
    def fail_stage(source, target):
        if Path(source).name == 'stage':
            raise OSError('publication failed')
        return real_replace(source, target)
    monkeypatch.setattr(builder.os, 'replace', fail_stage)
    with pytest.raises(OSError, match='publication failed'):
        builder.init(workspace, lambda *a, **k: [{'number': 1}], datasets=[], progress=lambda _: None)
    assert (workspace / 'old.fx').read_text() == 'old:Data.'


def test_dataset_row_count_counts_rows_not_statements(tmp_path):
    rows = [{'bpuic': 1}, {'bpuic': 1}]
    count, _ = builder.build_layer(tmp_path, 'wifistation', lambda *a, **k: rows, Scope({1}), DAY)
    text = builder.layer_path(tmp_path, 'wifistation').read_text()
    assert '[rowCount -> 2].' in text
    assert count > 2
    assert '[statementCount -> ' in text
    assert 'closed slot hasWifi' not in text


def test_validation_detects_schema_failures_without_violation_facts():
    from runtime.validation import validation_issues
    assert validation_issues({'violations': [], 'schema_checks': [{'status': 'violated'}]})
    assert validation_issues({'violations': [], 'schema_checks': [{'status': 'unknown'}]})
    assert not validation_issues({'violations': [], 'schema_checks': [{'status': 'satisfied'}],
                                  'constraint_checks': [{'status': 'unknown'}]})


def test_session_closes_client_when_loading_fails(tmp_path, monkeypatch):
    from runtime import session as module
    (tmp_path / '60-data.fx').write_text('bad')
    closed = []
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def close(self): closed.append(True)
    def fail(self): raise QueryError('bad source')
    monkeypatch.setattr(module, '_SessionClient', FakeClient)
    monkeypatch.setattr(Session, '_load', fail)
    with pytest.raises(QueryError):
        Session(tmp_path)
    assert closed == [True]


def test_session_uses_load_timeout_and_releases_startup_buffers(tmp_path, monkeypatch):
    from runtime import session as module

    (tmp_path / '00-core.fx').write_text('world open.\nThing::Entity.')
    (tmp_path / '60-data.fx').write_text('item:Thing.')
    clients = []
    starts = []
    progressed = []
    requests = []

    class FakeProgress:
        def message(self, text): pass
        def start(self, label, total=None, estimate=None, unit=None):
            starts.append((label, total))
        def step(self, count=1):
            progressed.append(count)
        def finish(self, note=''): return 1.0
        def close(self): pass

    class FakeClient:
        def __init__(self, *args, request_timeout, retain_transcript, **kwargs):
            self.request_timeout = request_timeout
            self.retain_transcript = retain_transcript
            self._transcript = []
            self._transcript_bytes = 0
            self.last_response = None
            self.closed = False
            clients.append(self)

        def request(self, command, **fields):
            requests.append((command, fields, self.request_timeout))
            if self.retain_transcript:
                self._transcript.append(command)
                self._transcript_bytes += len(command)
            self.last_response = {
                'warnings': [],
                'stats': {'derived_facts': 1, 'rule_firings': 0, 'rounds': 1},
            }
            return {}

        def load_begin(self, **limits): return self.request('load_begin', **limits)
        def load_chunk(self, source, **fields): return self.request('load_chunk', source=source, **fields)
        def load_commit(self): return self.request('load_commit')
        def close(self):
            self.closed = True

    monkeypatch.setattr(module, '_SessionClient', FakeClient)
    with Session(tmp_path, timeout=7, load_timeout=11, max_proofs=1234,
                 progress=FakeProgress()) as session:
        assert session.client.request_timeout == 7
        assert session.client._transcript == []
        assert session.client._transcript_bytes == 0
        total_bytes = sum(p.stat().st_size for p in tmp_path.glob('*.fx'))
        assert starts[0] == ('staging program', total_bytes)
        assert sum(progressed) == total_bytes
        assert [command for command, _, _ in requests] == [
            'load_begin', 'load_chunk', 'load_chunk', 'load_commit',
        ]
        assert requests[0][1]['max_proofs'] == 1234
        assert all(timeout == 11 for _, _, timeout in requests)
        assert all(fields['record'] is False for command, fields, _ in requests
                   if command == 'load_chunk')
        session.client.request('query', query='?- item:Thing.')
        assert session.client._transcript == ['query']
    assert clients[0].closed


@pytest.mark.parametrize('failure', ['load_chunk', 'load_commit'])
def test_session_aborts_and_closes_failed_bulk_load(tmp_path, monkeypatch, failure):
    from runtime import session as module
    (tmp_path / '00-core.fx').write_text('world open.')
    calls = []
    class FakeClient:
        last_response = None
        def __init__(self, *args, **kwargs): pass
        def load_begin(self, **kwargs): calls.append('load_begin')
        def load_chunk(self, *args, **kwargs):
            calls.append('load_chunk')
            if failure == 'load_chunk':
                raise FrameXError({'kind': 'SyntaxError', 'message': 'invalid source'})
        def load_commit(self):
            calls.append('load_commit')
            raise FrameXError({'kind': 'InferenceError', 'message': 'proof limit reached'})
        def load_abort(self): calls.append('load_abort')
        def close(self): calls.append('close')
    monkeypatch.setattr(module, '_SessionClient', FakeClient)
    with pytest.raises(QueryError, match='engine rejected'):
        Session(tmp_path)
    assert calls[-2:] == ['load_abort', 'close']


def test_session_reports_engine_without_bulk_load_support(tmp_path, monkeypatch):
    from runtime import session as module
    (tmp_path / '00-core.fx').write_text('world open.')
    closed = []
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def load_begin(self, **kwargs):
            raise FrameXError({'kind': 'ProtocolError', 'message': 'unknown command'})
        def close(self): closed.append(True)
    monkeypatch.setattr(module, '_SessionClient', FakeClient)
    with pytest.raises(QueryError, match='FrameX 0.4.3'):
        Session(tmp_path)
    assert closed == [True]


def test_load_warnings_are_not_overwritten_by_query_responses():
    session = object.__new__(Session)
    session._load_warnings = [{'kind': 'load', 'message': 'warning'}]
    session.client = type('Client', (), {'last_response': {'warnings': []}})()
    assert session.warnings() == [{'kind': 'load', 'message': 'warning'}]


def test_cli_forwards_limits_to_persistent_session(monkeypatch, tmp_path, capsys):
    from cli import app as main
    seen = {}
    class FakeSession:
        def __init__(self, workspace, **kwargs): seen.update(kwargs)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ask(self, query): return {'status': 'unknown'}, 0.0
    monkeypatch.setattr(main, 'Session', FakeSession)
    args = main.build_parser().parse_args([
        '--query', '?- x:C.', '--timeout', '7', '--load-timeout', '11',
        '--max-proofs', '123'
    ])
    assert main.do_query(args) == 0
    assert seen['timeout'] == 7 and seen['load_timeout'] == 11
    assert seen['max_proofs'] == 123


def test_dst_ambiguous_times_require_explicit_offset():
    assert service.timestamp('2026-10-25T02:30:00') is None
    assert service.timestamp('2026-03-29T02:30:00') is None
    assert service.timestamp('2026-10-25T02:30:00+02:00') is not None


def test_different_operating_days_do_not_form_a_transfer(client):
    later = [stop('two', 2, '13:10'), stop('two', 3, '14:00')]
    for row in later:
        row['betriebstag'] = '2026-09-18'
    load(client, [stop('one', 1, '12:00'), stop('one', 2, '13:00')] + later)
    request(client)
    assert truth(client, 'journey[reach1 -> sp_3]') == 'unknown'


def test_old_generated_workspaces_require_rebuild(tmp_path):
    from runtime.query import logic_files
    (tmp_path / '00-core.fx').write_text('world open.')
    (tmp_path / 'manifest.json').write_text('{"created":"2026-09-18"}')
    with pytest.raises(QueryError, match='old ontology'):
        logic_files(tmp_path)


def test_period_specific_busy_classification_does_not_mix_years(client):
    spec = registry.LAYERS['passagierfrequenz']
    rows = [{'uic':1,'jahr_annee_anno':'2018','dtv_tjm_tgm':30000},
            {'uic':1,'jahr_annee_anno':'2025','dtv_tjm_tgm':100}]
    lines, _ = mapper.map_layer(spec, rows, Scope({1}), DAY)
    load(client, extra='\n'.join(lines))
    assert truth(client, 'sp_1[busyIn("2018") -> true]') == 'true'
    assert truth(client, 'sp_1[busyIn("2025") -> true]') == 'unknown'


def test_session_expands_only_reachable_transfer_windows(tmp_path):
    builder.copy_meta_model(tmp_path)
    rows = [stop('one',1,'09:00'),stop('one',2,'10:00'),
            stop('two',2,'10:10'),stop('two',3,'11:00'),
            stop('three',3,'11:10'),stop('three',4,'12:00'),
            stop('unreachable',5,'09:00'),stop('unreachable',6,'10:00')]
    lines,_ = service.map_service(rows, Scope(set(range(1,7))), DAY)
    spine = '\n'.join(f'sp_{n}:StopPoint. sp_{n}[bpuic -> {n}].' for n in range(1,7))
    (tmp_path/'60-data.fx').write_text(spine+'\n'+'\n'.join(lines))
    with Session(tmp_path) as session:
        name=session.prepare_journey(1,DAY,not_before=DAY+'T08:00:00',transfer_seconds=300)
        assert session.ask(f'{name}[reach2 -> sp_4]')[0]['status']=='true'
        assert session.ask(f'?E[atStopPoint -> sp_6] AND ?E[transferReady({name}) -> ?T]')[0]['status']=='unknown'
        size=len(session.client.transcript)
        assert session.prepare_journey(1,DAY,not_before=DAY+'T08:00:00',transfer_seconds=300)==name
        assert len(session.client.transcript)==size


def test_backward_sequence_edge_cannot_create_reverse_trip(client):
    lines = load(client, [stop('t',1,'09:00'),stop('t',2,'10:00')])
    events = [line.split('[')[0] for line in lines if '[atStopPoint ->' in line]
    client.add(source=f'{events[1]}[nextRealStop -> {events[0]}]. {events[1]}[nextRideStop -> {events[0]}].')
    request(client, origin=2)
    assert truth(client, 'sp_2[nonStopTo -> sp_1]') == 'unknown'
    assert truth(client, 'journey[reachDirect -> sp_1]') == 'unknown'


def test_fractional_seconds_do_not_shorten_transfer_allowance(client):
    rows = [stop('one',1,'12:00'), stop('one',2,DAY+'T13:00:00.1'),
            stop('two',2,'13:05'), stop('two',3,'14:00')]
    load(client, rows)
    request(client, buffer=300)
    assert truth(client, 'journey[reach1 -> sp_3]') == 'unknown'


def test_earliest_departure_rounds_up_conservatively():
    assert service.timestamp(DAY+'T09:00:00.1', round_up=True) > service.timestamp(DAY+'T09:00:00')


@pytest.mark.parametrize('invalid_dwell', [False, True])
def test_inconsistent_actual_times_cannot_create_backwards_journeys(client, invalid_dwell):
    rows = [stop('one', 1, '09:00'), stop('one', 2, '10:00'),
            stop('two', 2, '08:40'), stop('two', 3, '08:50')]
    rows[1]['an_prognose'] = DAY+'T08:30:00'
    if invalid_dwell:
        rows[1]['an_prognose'] = DAY+'T10:01:00'  # arrival after its own departure
    _, report = service.map_service(rows, Scope({1, 2, 3}), DAY)
    assert report.excluded['inconsistent_actual_time'] == 1
    load(client, rows)
    request(client)
    assert truth(client, 'journey[reachDirect -> sp_2]') == 'unknown'
    assert truth(client, 'journey[reach1 -> sp_3]') == 'unknown'


def test_departure_poster_is_not_evidence_of_a_live_display(client):
    lines,_=mapper.map_layer(registry.LAYERS['abfahrtsplakate0'],[{'bpuic':1}],Scope({1}),DAY)
    load(client,extra='\n'.join(lines)+'\nsp_1[hasWaitingHall -> true].')
    assert truth(client,'sp_1[hasDeparturePoster -> true]')=='true'
    assert truth(client,'sp_1[hasDepartureBoard -> true]')=='unknown'
    assert truth(client,'sp_1:EquippedStation')=='unknown'


def test_journey_joins_filter_unrelated_events_before_membership(client):
    # A bounded match budget reproduces the full snapshot's Cartesian join
    # failure cheaply. Unrelated reported stops must not multiply every hop.
    rows = [stop('one', n, f'09:{n:02}') for n in range(1, 31)]
    scope = Scope(set(range(1, 31)))
    lines, _ = service.map_service(rows, scope, DAY)
    readiness = []
    for line in lines:
        match = re.fullmatch(r'(\w+)\[arrivalEpoch -> (\d+)\]\.', line)
        if match:
            readiness.append(f'{match[1]}[transferReady(journey) -> {int(match[2])+300}].')
    source = '\n'.join([
        *(p.read_text() for p in builder.ontology_files()),
        *(f'sp_{n}:StopPoint.' for n in scope.ids), *lines, *readiness,
        *(f'unrelated_{n}:ReportedStop.' for n in range(1000)),
        f'journey:JourneyRequest. journey[origin -> sp_1]. journey[onDay -> "{DAY}"].',
        'journey[notBefore -> 0]. journey[transferSeconds -> 300].',
    ])
    client.request('load', source=source, max_matches=10000)
    assert truth(client, 'journey[reachDirect -> sp_30]') == 'true'
