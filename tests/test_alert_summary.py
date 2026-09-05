"""The on-call roll-up.

A live page of alerts came back almost entirely "Auvik Collector Reconnected" /
"Resolved: Auvik Collector Disconnected". A list cannot show what matters in
that; a grouping can. Groups are (base name, entity, site); the
"Resolved: " prefix Auvik adds on resolution is stripped so raise and clear land
together; flapping = 3+ in the window; groups sort by what is still active."""
from __future__ import annotations

from datetime import datetime, timezone

from conftest import FakeClient, default_routes, load
from tools import alerts
from tools.alerts import summarize_alerts

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
resolve = lambda did: {"DEV-1": "core-sw-01", "DEV-2": "edge-fw-01"}.get(did, "")  # noqa: E731


def _alert(aid, name, severity, status, entity_id, detected, entity_type="device"):
    a = load("alert_single.json")["data"]
    a = {**a, "id": aid, "attributes": {**a["attributes"], "name": name, "severity": severity, "status": status,
                                        "detectedOn": detected, "description": f"{name} on {entity_id}"}}
    a["relationships"] = {**a["relationships"], "entity": {"data": {"type": entity_type, "id": entity_id}}}
    return a


def _fake(items):
    routes = default_routes()
    routes["/v1/alert/history/info"] = {"data": items, "links": {}}
    return FakeClient(routes)


def test_groups_raise_and_clear_together_and_flags_flapping():
    items = [
        _alert("A1", "Device Offline", "critical", "created", "DEV-1", "2026-08-29T01:00:00.000Z"),
        _alert("A2", "Resolved: Device Offline", "critical", "resolved", "DEV-1", "2026-08-29T02:00:00.000Z"),
        _alert("A3", "Device Offline", "critical", "created", "DEV-1", "2026-08-29T03:00:00.000Z"),
        _alert("A4", "High CPU", "warning", "created", "DEV-2", "2026-08-29T04:00:00.000Z"),
        _alert("A5", "High CPU", "emergency", "resolved", "DEV-2", "2026-08-28T04:00:00.000Z"),
    ]
    out = summarize_alerts(_fake(items), ["T1"], resolve, now=NOW)
    assert out["data_status"] == "ok" and out["alert_count"] == 5 and out["active_alert_count"] == 3
    assert out["group_count"] == 2 and out["counts_by_severity"] == {"critical": 3, "warning": 1, "emergency": 1}
    g1, g2 = out["groups"]
    # Active count 2 beats active count 1, regardless of severity rank.
    assert g1["alert_name"] == "Device Offline" and g1["device_name"] == "core-sw-01"
    assert (g1["count"], g1["active_count"], g1["resolved_count"], g1["flapping"]) == (3, 2, 1, True)
    assert (g1["first_seen"], g1["last_seen"], g1["latest_alert_id"], g1["latest_status"]) == \
        ("2026-08-29T01:00:00.000Z", "2026-08-29T03:00:00.000Z", "A3", "created")
    assert g2["alert_name"] == "High CPU" and g2["severity"] == "emergency"  # highest seen wins
    assert (g2["count"], g2["active_count"], g2["flapping"], g2["latest_alert_id"]) == (2, 1, False, "A4")


def test_sort_ties_break_on_severity_then_count():
    items = [
        _alert("B1", "Warn Thing", "warning", "created", "DEV-1", "2026-08-29T01:00:00.000Z"),
        _alert("B2", "Crit Thing", "critical", "created", "DEV-2", "2026-08-29T00:00:00.000Z"),
    ]
    out = summarize_alerts(_fake(items), ["T1"], resolve, now=NOW)
    assert [g["alert_name"] for g in out["groups"]] == ["Crit Thing", "Warn Thing"]


def test_summary_does_not_mark_alerts_seen_and_sends_the_same_filters_as_list():
    fake = _fake([_alert("C1", "X", "info", "created", "DEV-1", "2026-08-29T01:00:00.000Z")])
    alerts.SEEN_ALERT_IDS.clear()
    out = summarize_alerts(fake, ["T1"], resolve, severity="info", now=NOW)
    assert alerts.SEEN_ALERT_IDS == set() and out["groups"][0]["latest_alert_id"] == "C1"
    _, path, params = fake.calls[0]
    assert path == "/v1/alert/history/info" and params["filter[dismissed]"] == "false"
    assert params["filter[severity]"] == "info" and params["filter[detectedTimeAfter]"] == "2026-08-28T12:00:00.000Z"


def test_hours_zero_sends_no_time_filter_and_limit_truncates():
    items = [_alert(f"D{i}", f"Name {i}", "info", "created", f"DEV-{i}", "2026-08-29T01:00:00.000Z") for i in range(5)]
    fake = _fake(items)
    out = summarize_alerts(fake, ["T1"], resolve, hours=0, limit=2, now=NOW)
    assert "filter[detectedTimeAfter]" not in fake.calls[0][2]
    assert out["group_count"] == 5 and out["returned_count"] == 2 and out["truncated"] is True and out["window_hours"] == 0


def test_unmatched_site_is_a_real_zero_with_no_request():
    fake = _fake([])
    sites = {"T1": "acme-hq", "T2": "acme-bos"}
    out = summarize_alerts(fake, ["T1", "T2"], resolve, site="nowhere", tenant_sites=sites, now=NOW)
    assert out["data_status"] == "ok" and out["groups"] == [] and out["group_count"] == 0
    assert out["sites"] == ["acme-bos", "acme-hq"] and "no site matches 'nowhere'" in out["detail"]
    assert fake.calls == []


def test_site_scopes_the_tenants_param():
    fake = _fake([])
    out = summarize_alerts(fake, ["T1", "T2"], resolve, site="bos", tenant_sites={"T1": "acme-hq", "T2": "acme-bos"}, now=NOW)
    assert out["alert_count"] == 0 and fake.calls[0][2]["tenants"] == "T2"
