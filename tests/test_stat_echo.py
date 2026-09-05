"""Auvik echoes back a different statType than the caller asked for (D-8).

Ask for `cpuUtilization`, get `attributes.statType = "deviceCpuUtilization"`.
Harmless in itself - it is passed straight through - but an agent comparing what
it asked for against what it got sees a mismatch and may retry or apologise for
a call that worked perfectly.

The prefixing is mechanical. Measured live, every stat of every family:

    interface     bandwidth          -> interfaceBandwidth
    interface     utilization        -> interfaceUtilization
    interface     packetLoss         -> interfacePacketLoss
    interface     packetDiscard      -> interfacePacketDiscard
    device        cpuUtilization     -> deviceCpuUtilization
    device        memoryUtilization  -> deviceMemoryUtilization
    device        storageUtilization -> deviceStorageUtilization
    device        bandwidth          -> deviceBandwidth
    availability  uptime             -> deviceAvailabilityUptime
    availability  outage             -> deviceAvailabilityOutage

i.e. `<family prefix> + <stat capitalised>`, with the availability family
prefixed `deviceAvailability`, not `deviceavailability`.

Fixed by echoing the caller's own string back as `requested_stat` beside Auvik's
`stat`, so the two are comparable without the agent having to know the rule.
"""
from __future__ import annotations

import pytest

from conftest import FakeClient, default_routes, load
from tools import stats as S

LIVE_STAT_TYPES = {
    ("interface", "bandwidth"): "interfaceBandwidth",
    ("interface", "utilization"): "interfaceUtilization",
    ("interface", "packetLoss"): "interfacePacketLoss",
    ("interface", "packetDiscard"): "interfacePacketDiscard",
    ("device", "cpuUtilization"): "deviceCpuUtilization",
    ("device", "memoryUtilization"): "deviceMemoryUtilization",
    ("device", "storageUtilization"): "deviceStorageUtilization",
    ("device", "bandwidth"): "deviceBandwidth",
    ("availability", "uptime"): "deviceAvailabilityUptime",
    ("availability", "outage"): "deviceAvailabilityOutage",
}


def test_device_stats_echo_the_stat_the_caller_asked_for():
    out = S.device_stats(FakeClient(default_routes()), ["T1"], "cpuUtilization", "DEV-1")
    s = out["series_for"][0]
    assert s["requested_stat"] == "cpuUtilization", "the caller's own string is not echoed back"
    assert s["stat"] == "deviceCpuUtilization", "Auvik's prefixed value must still be reported unchanged"


def test_interface_stats_echo_the_stat_the_caller_asked_for():
    out = S.interface_stats(FakeClient(default_routes()), ["T1"], "utilization", device_id="DEV-1")
    s = out["series_for"][0]
    assert s["requested_stat"] == "utilization"
    assert s["stat"] == "interfaceUtilization"


def test_availability_echoes_the_stat_the_caller_asked_for():
    out = S.device_availability(FakeClient(default_routes()), ["T1"], "uptime", "DEV-1")
    s = out["series_for"][0]
    assert s["requested_stat"] == "uptime"
    assert s["stat"] == "deviceAvailabilityUptime"


def test_requested_stat_survives_a_response_with_no_statType_at_all():
    """Auvik's value is passed through, so an absent one must not blank the echo."""
    p = S.project_series({"id": "X", "attributes": {}}, limit=10, requested_stat="uptime")
    assert p["requested_stat"] == "uptime" and p["stat"] == ""


@pytest.mark.parametrize("fixture,family,stat", [
    ("stat_interface.json", "interface", "utilization"),
    ("stat_device.json", "device", "cpuUtilization"),
    ("stat_availability.json", "availability", "uptime"),
])
def test_committed_fixtures_carry_the_prefixed_statType_the_live_api_returns(fixture, family, stat):
    """The synthetic fixtures carried the bare stat name, which is why D-8 was
    invisible offline: the projection looked like it echoed the request when it
    was really passing through a value that happened to match."""
    body = load(fixture)
    got = {(i.get("attributes") or {}).get("statType") for i in (body.get("data") or [])}
    assert got == {LIVE_STAT_TYPES[(family, stat)]}, (
        f"{fixture} has statType {got}; the live API returns "
        f"{LIVE_STAT_TYPES[(family, stat)]!r} for a {family} {stat!r} request"
    )
