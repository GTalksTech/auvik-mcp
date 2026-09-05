"""Alert projection: every documented attribute maps; missing → ""/[]/False,
never None; device names resolve via the supplied resolver; filters reach the
API as filter[...] params; SEEN_ALERT_IDS fills on every read."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from conftest import FakeClient, default_routes, load
from tools import alerts

NAMES = {"DEV-1": "core-sw-01", "DEV-2": "edge-fw-01"}
resolve = lambda did: NAMES.get(did, "")  # noqa: E731
NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_project_alert_maps_all_fields():
    item = load("alert_single.json")["data"]
    p = alerts.project_alert(item, resolve)
    assert p == {
        "alert_id": "AL-1", "tenant_id": "T1", "site": "acme-hq",
        "name": "Device Offline", "severity": "warning", "status": "created",
        "detected_on": "2026-08-29T05:00:00.000Z", "dismissed": False, "dispatched": True,
        "description": "desc", "entity_type": "device", "entity_id": "DEV-1",
        "device_id": "DEV-1", "device_name": "core-sw-01", "interface_name": "",
        "related_alert_id": "AL-REL",
        "external_tickets": ["Microsoft_Teams:56003"],
    }


def test_project_alert_missing_attributes_never_none():
    p = alerts.project_alert({"id": "X", "attributes": {}, "relationships": {}}, resolve)
    assert None not in p.values()
    assert p["external_tickets"] == [] and p["dismissed"] is False and p["device_name"] == ""


def test_interface_entity_without_resolver_has_no_device_name():
    item = load("alerts_page1.json")["data"][1]  # AL-2 on interface IF-1
    p = alerts.project_alert(item, resolve)
    assert p["entity_type"] == "interface" and p["device_id"] == "" and p["device_name"] == ""


def test_list_alerts_defaults_paginates_counts_and_marks_seen():
    fake = FakeClient(default_routes())
    alerts.SEEN_ALERT_IDS.clear()
    out = alerts.list_alerts(fake, ["T1"], resolve, now=NOW)
    assert out["data_status"] == "ok"
    # AL-4 is a collector-level alert with NO entity relationship — about one
    # real alert in ten looks like that (D-5), so the fixtures carry one.
    assert [a["alert_id"] for a in out["alerts"]] == ["AL-1", "AL-2", "AL-3", "AL-4"]
    assert out["total_count"] == 4 and out["truncated"] is False
    assert out["counts_by_severity"] == {"warning": 2, "critical": 1, "info": 1}
    assert alerts.SEEN_ALERT_IDS == {"AL-1", "AL-2", "AL-3", "AL-4"}
    entity_less = [a for a in out["alerts"] if a["alert_id"] == "AL-4"][0]
    assert entity_less["entity_type"] == "" and entity_less["device_name"] == ""
    _, path, params = fake.calls[0]
    assert path == "/v1/alert/history/info"
    assert params["tenants"] == "T1"
    assert params["filter[dismissed]"] == "false"
    assert params["filter[detectedTimeAfter]"] == "2026-08-28T12:00:00.000Z"


def test_list_alerts_filters_and_limit():
    fake = FakeClient(default_routes())
    out = alerts.list_alerts(fake, ["T1"], resolve, severity="critical", status="created",
                             include_dismissed=True, device_id="DEV-1", hours=0, limit=1, now=NOW)
    params = fake.calls[0][2]
    assert params["filter[severity]"] == "critical" and params["filter[status]"] == "created"
    assert params["filter[entityId]"] == "DEV-1"
    assert "filter[dismissed]" not in params and "filter[detectedTimeAfter]" not in params
    assert len(out["alerts"]) == 1 and out["total_count"] == 4 and out["truncated"] is True


@pytest.mark.parametrize("kw", [{"severity": "bogus"}, {"status": "bogus"}, {"hours": -1}, {"limit": 0}])
def test_list_alerts_rejects_bad_input(kw):
    with pytest.raises(ValueError):
        alerts.list_alerts(FakeClient(default_routes()), ["T1"], resolve, now=NOW, **kw)


def test_get_alert_marks_seen_and_projects():
    fake = FakeClient(default_routes())
    alerts.SEEN_ALERT_IDS.clear()
    out = alerts.get_alert(fake, resolve, "AL-1")
    assert out["data_status"] == "ok" and out["alert"]["device_name"] == "core-sw-01"
    assert "AL-1" in alerts.SEEN_ALERT_IDS


def test_get_alert_requires_id():
    with pytest.raises(ValueError):
        alerts.get_alert(FakeClient(default_routes()), resolve, "")


def test_list_alerts_name_filter_matches_name_or_description_case_insensitive():
    fake = FakeClient(default_routes())
    out = alerts.list_alerts(fake, ["T1"], resolve, name="STOPPED RESPONDING", now=NOW)  # AL-1 description
    assert [a["alert_id"] for a in out["alerts"]] == ["AL-1"] and out["total_count"] == 1
    out = alerts.list_alerts(fake, ["T1"], resolve, name="collector", now=NOW)  # AL-4 name
    assert [a["alert_id"] for a in out["alerts"]] == ["AL-4"]
    out = alerts.list_alerts(fake, ["T1"], resolve, name="nothing-like-this", now=NOW)
    assert out["data_status"] == "ok" and out["total_count"] == 0 and out["alerts"] == []
