"""Opt-in full-snapshot checks, loaded exactly once.

Set SBB_TEST_WORKSPACE to a rebuilt workspace. Run in one pytest process (no
xdist): the session and prepared journey are shared by every case below.

The operating day is read out of the snapshot rather than written down here.
ist-daten-sbb is the *previous day* feed, so every rebuild carries a different
date and any literal in this file is wrong the day after it is committed.
SBB_TEST_DAY overrides the detected day.
"""
from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import re

import pytest

from runtime.session import Session

SERVICE_LAYER = "70-ist-daten-sbb.fx"


@pytest.fixture(scope="session")
def full_session():
    workspace = os.environ.get("SBB_TEST_WORKSPACE")
    if not workspace:
        pytest.skip("set SBB_TEST_WORKSPACE to run the full snapshot checks")
    with Session(Path(workspace), timeout=float(os.environ.get("SBB_TEST_TIMEOUT", "900"))) as session:
        yield session


@pytest.fixture(scope="session")
def service_layer(full_session):
    return (full_session.workspace / SERVICE_LAYER).read_text()


@pytest.fixture(scope="session")
def snapshot_events(service_layer):
    events = re.findall(r"^(\w+):StopEvent\.$", service_layer, re.MULTILINE)
    cancelled = re.findall(r"^(\w+)\[cancelled -> true\]\.$", service_layer, re.MULTILINE)
    assert events and cancelled, "expected the supplied snapshot's stop/cancellation evidence"
    return events[0], cancelled[0]


@pytest.fixture(scope="session")
def snapshot_day(service_layer):
    """The operating day this snapshot actually covers.

    `board0` matches the request's day against `observedOn` exactly, so a day
    the snapshot does not contain yields no rides and every journey answer is
    `unknown`. Reading the day from the evidence keeps the test honest whenever
    the workspace is rebuilt.
    """
    days = Counter(re.findall(r'\[observedOn -> "([\d-]+)"\]', service_layer))
    assert days, f"{SERVICE_LAYER} carries no operating day"
    return days.most_common(1)[0][0]


@pytest.mark.parametrize("goal", [
    "sp_8509000:StopPoint",
    "sp_8509000[hasWifi -> true]",
])
def test_snapshot_station_evidence(full_session, goal):
    assert full_session.ask(f"?- {goal}.")[0]["status"] == "true"


def test_snapshot_event_is_not_a_facility(full_session, snapshot_events):
    event, _ = snapshot_events
    # Bind the event first: an unbound negative join is expensive on this base.
    assert full_session.ask(f"?- {event}:Facility.")[0]["status"] == "unknown"


def test_snapshot_cancelled_event_has_no_connection(full_session, snapshot_events):
    _, event = snapshot_events
    for slot in ("nextRealStop", "nextRideStop", "rideSegment"):
        assert full_session.ask(f"?- {event}[{slot} -> ?X].")[0]["status"] == "unknown"


@pytest.fixture(scope="session")
def snapshot_journey_parameters(snapshot_day):
    day = os.environ.get("SBB_TEST_DAY", snapshot_day)
    return dict(origin=8509000, day=day, not_before=f"{day}T18:00:00", transfer_seconds=300)


def _journey_diagnosis(service_layer, parameters, slot):
    """Say why a journey found nothing, rather than only that it did.

    The overwhelmingly likely cause is a day the snapshot does not cover, so
    name the days it does. Getting this wrong is silent otherwise: every
    journey slot simply answers `unknown`.
    """
    days = sorted(set(re.findall(r'\[observedOn -> "([\d-]+)"\]', service_layer)))
    return (
        f"no {slot} evidence for {parameters}\n"
        f"  operating days present in {SERVICE_LAYER}: {days or 'none'}\n"
        f"  requested day: {parameters['day']}"
        + ("" if parameters["day"] in days else "  <-- not in the snapshot")
    )


@pytest.fixture(scope="session")
def snapshot_journey(full_session, snapshot_journey_parameters, service_layer):
    request = full_session.prepare_journey(**snapshot_journey_parameters)
    destinations = {}
    for slot in ("reachDirect", "reach1", "reach2"):
        result, _ = full_session.ask(f"?- {request}[{slot} -> ?S].")
        assert result["status"] == "bindings", _journey_diagnosis(
            service_layer, snapshot_journey_parameters, slot
        )
        destinations[slot] = {row["S"] for row in result["bindings"]}
    return request, destinations


def test_snapshot_journey_change_budget_is_monotonic(snapshot_journey):
    _, destinations = snapshot_journey
    assert destinations["reachDirect"]
    assert destinations["reachDirect"] <= destinations["reach1"] <= destinations["reach2"]
    assert "sp_8509000" not in destinations["reach2"]


def test_snapshot_repeated_journey_reuses_preparation(
    full_session, snapshot_journey, snapshot_journey_parameters,
):
    request, _ = snapshot_journey
    count = len(full_session.client.transcript)
    assert full_session.prepare_journey(**snapshot_journey_parameters) == request
    assert len(full_session.client.transcript) == count
