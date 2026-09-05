"""An alert on an INTERFACE entity must name its device.

Before this, an "Interface Down" alert projected device_id "" / device_name "" -
the agent was told which interface GUID went down and nothing about where. One
cached GET of /v1/inventory/interface/info/{id} per unique interface fixes it.
A failed lookup is reported in `detail`, not cached, and never turns a
successful alert list into `unavailable`."""
from __future__ import annotations

from conftest import FakeClient, default_routes, load
from tools import alerts, devices


def _cache(fake):
    return devices.DeviceCache(fake, ["T1"], clock=lambda: 0.0)


def test_interface_parent_resolves_and_caches():
    fake = FakeClient(default_routes())
    c = _cache(fake)
    r = c.interface_parent("IF-1")
    assert r == {"device_id": "DEV-1", "device_name": "core-sw-01", "interface_name": "GigabitEthernet1/0/1", "resolved": True}
    c.interface_parent("IF-1")
    assert len([x for x in fake.calls if x[1] == "/v1/inventory/interface/info/IF-1"]) == 1


def test_interface_parent_failure_is_blank_not_cached_and_never_raises():
    fake = FakeClient(default_routes())
    c = _cache(fake)
    r = c.interface_parent("IF-MISSING")  # FakeClient raises AuvikUnavailable(404) for unknown routes
    assert r == {"device_id": "", "device_name": "", "interface_name": "", "resolved": False}
    c.interface_parent("IF-MISSING")
    assert len([x for x in fake.calls if x[1] == "/v1/inventory/interface/info/IF-MISSING"]) == 2
    assert c.interface_parent("") == {"device_id": "", "device_name": "", "interface_name": "", "resolved": False}


def test_project_alert_uses_the_interface_resolver():
    item = load("alerts_page1.json")["data"][1]  # AL-2 on interface IF-1
    c = _cache(FakeClient(default_routes()))
    p = alerts.project_alert(item, c.name_of, c.interface_parent)
    assert (p["entity_type"], p["device_id"], p["device_name"], p["interface_name"]) == \
        ("interface", "DEV-1", "core-sw-01", "GigabitEthernet1/0/1")


def test_list_alerts_reports_unresolved_interfaces_in_detail_and_stays_ok():
    routes = default_routes()
    del routes["/v1/inventory/interface/info/IF-1"]
    fake = FakeClient(routes)
    c = _cache(fake)
    out = alerts.list_alerts(fake, ["T1"], c.name_of, resolve_interface=c.interface_parent)
    assert out["data_status"] == "ok" and out["total_count"] == 4
    al2 = next(a for a in out["alerts"] if a["alert_id"] == "AL-2")
    assert al2["device_name"] == "" and al2["interface_name"] == ""
    assert "1 interface alert" in out["detail"] and "could not be resolved" in out["detail"]


def test_list_alerts_no_detail_when_everything_resolves():
    fake = FakeClient(default_routes())
    c = _cache(fake)
    out = alerts.list_alerts(fake, ["T1"], c.name_of, resolve_interface=c.interface_parent)
    assert out["detail"] == ""
    assert next(a for a in out["alerts"] if a["alert_id"] == "AL-2")["device_name"] == "core-sw-01"
