from __future__ import annotations

from datetime import datetime, timezone

import pytest

from conftest import FakeClient, default_routes, load
from tools import stats

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_window_clamps_and_notes():
    assert stats.window(24, "hour", NOW) == ("2026-08-28T12:00:00.000Z", 24, "")
    frm, hrs, note = stats.window(48, "minute", NOW)
    assert (frm, hrs) == ("2026-08-29T06:00:00.000Z", 6) and "clamped" in note and "minute" in note
    assert stats.window(1000, "day", NOW)[1] == 365


def test_window_rejects_bad_input():
    with pytest.raises(ValueError):
        stats.window(0, "hour", NOW)
    with pytest.raises(ValueError):
        stats.window(1, "fortnight", NOW)


def test_project_series_flattens_and_limits_to_most_recent_rows():
    item = load("stat_interface.json")["data"][0]
    p = stats.project_series(item, limit=5)
    assert p["entity_id"] == "IF-1" and p["entity_name"] == "GigabitEthernet1/0/1"
    # Auvik echoes the family-prefixed name, not the requested one (D-8,
    # verified live across all ten stats of all three families). Both are asserted so the
    # rename cannot silently become an echo or vice versa.
    assert p["stat"] == "interfaceUtilization" and p["interval"] == "hour"
    assert (p["from_time"], p["thru_time"]) == ("2026-08-28T12:00:00.000Z", "2026-08-29T12:00:00.000Z")
    s = p["series"][0]
    # Real column labels and units, taken from the live API: the timestamp
    # column is "Recorded At" in unix_mins (MINUTES since epoch), not epoch_ms.
    assert s["columns"] == ["Recorded At", "Transmit", "Receive"]
    assert s["units"] == ["unix_mins", "percent", "percent"]
    assert s["row_count"] == 24 and len(s["rows"]) == 5 and s["truncated"] is True
    assert s["rows"][-1][1] == 33.0  # most recent kept
    # Auvik sends stats[].name and stats[].index as null; neither may leak None.
    assert s["name"] == "" and s["index"] == ""


def test_project_series_missing_never_none():
    p = stats.project_series({"id": "X", "attributes": {}, "relationships": {}}, limit=10)
    assert p["series"] == [] and None not in p.values()


def test_as_list_accepts_object_or_array():
    assert stats._as_list({"id": "1"}) == [{"id": "1"}]
    assert stats._as_list([{"id": "1"}]) == [{"id": "1"}]
    assert stats._as_list(None) == []


def test_interface_stats_params_and_output():
    fake = FakeClient(default_routes())
    out = stats.interface_stats(fake, ["T1"], "utilization", device_id="DEV-1", hours=24, interval="hour", limit=10, now=NOW)
    _, path, params = fake.calls[0]
    assert path == "/v1/stat/interface/utilization"
    # filter[thruTime] was added after the first live run: the API requires it
    # even though the OpenAPI file calls it optional (D-4). This
    # assertion is exact, so it now demands one MORE key than before, not fewer.
    assert params == {"tenants": "T1", "filter[fromTime]": "2026-08-28T12:00:00.000Z",
                      "filter[thruTime]": "2026-08-29T12:00:00.000Z", "filter[interval]": "hour",
                      "filter[parentDevice]": "DEV-1"}
    assert out["data_status"] == "ok" and out["entity_count"] == 1 and out["series_for"][0]["entity_id"] == "IF-1"
    assert out["window_hours"] == 24 and out["clamp_note"] == ""


def test_interface_stats_requires_device_or_interface_and_valid_stat():
    fake = FakeClient(default_routes())
    with pytest.raises(ValueError):
        stats.interface_stats(fake, ["T1"], "utilization", now=NOW)
    with pytest.raises(ValueError):
        stats.interface_stats(fake, ["T1"], "cpuUtilization", device_id="DEV-1", now=NOW)


def test_interface_stats_clamp_is_reported_in_detail():
    fake = FakeClient(default_routes())
    out = stats.interface_stats(fake, ["T1"], "bandwidth", interface_id="IF-1", hours=48, interval="minute", now=NOW)
    assert out["window_hours"] == 6 and "clamped" in out["clamp_note"] and out["detail"] == out["clamp_note"]
    assert fake.calls[0][2]["filter[interfaceId]"] == "IF-1"


def test_device_stats_and_availability_paths():
    fake = FakeClient(default_routes())
    out = stats.device_stats(fake, ["T1"], "cpuUtilization", "DEV-1", now=NOW)
    assert fake.calls[0][1] == "/v1/stat/device/cpuUtilization" and fake.calls[0][2]["filter[deviceId]"] == "DEV-1"
    assert out["data_status"] == "ok"
    fake2 = FakeClient(default_routes())
    out2 = stats.device_availability(fake2, ["T1"], "uptime", "DEV-1", now=NOW)
    assert fake2.calls[0][1] == "/v1/stat/deviceAvailability/uptime"
    assert fake2.calls[0][2]["filter[interval]"] == "day" and out2["window_hours"] == 168


@pytest.mark.parametrize("fn,stat", [(stats.device_stats, "uptime"), (stats.device_availability, "cpuUtilization")])
def test_device_tools_reject_wrong_stat_family(fn, stat):
    with pytest.raises(ValueError):
        fn(FakeClient(default_routes()), ["T1"], stat, "DEV-1", now=NOW)


def test_device_tools_require_device_id():
    with pytest.raises(ValueError):
        stats.device_stats(FakeClient(default_routes()), ["T1"], "cpuUtilization", "", now=NOW)


def test_thru_time_is_always_sent_because_the_live_api_requires_it():
    """Auvik's OpenAPI document marks filter[thruTime] OPTIONAL
    ("defaults to current time"). The live API disagrees. Every /v1/stat/* call
    without it returns HTTP 400, identically across probes varying the tenant,
    deviceId, interval and timestamp format:

        Field "thruTime" of required type "DateTime!" was not provided.

    All three stat tools were dead on arrival until it was sent. Demonstrated
    live behaviour outranks a published document that is provably wrong (D-4)."""
    for fn, args in (
        (stats.interface_stats, dict(stat="utilization", device_id="DEV-1")),
        (stats.device_stats, dict(stat="cpuUtilization", device_id="DEV-1")),
        (stats.device_availability, dict(stat="uptime", device_id="DEV-1")),
    ):
        fake = FakeClient(default_routes())
        fn(fake, ["T1"], now=NOW, **args)
        p = fake.calls[0][2]
        assert p["filter[thruTime]"] == "2026-08-29T12:00:00.000Z", f"{fn.__name__} sent no thruTime: {p}"
        assert p["filter[fromTime]"] < p["filter[thruTime]"], f"{fn.__name__} window is inverted: {p}"


def test_thru_time_tracks_the_clamped_window_not_the_requested_one():
    fake = FakeClient(default_routes())
    stats.interface_stats(fake, ["T1"], "bandwidth", interface_id="IF-1", hours=48, interval="minute", now=NOW)
    p = fake.calls[0][2]
    assert (p["filter[fromTime]"], p["filter[thruTime]"]) == ("2026-08-29T06:00:00.000Z", "2026-08-29T12:00:00.000Z")
