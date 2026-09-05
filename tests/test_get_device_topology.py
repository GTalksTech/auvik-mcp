"""get_device projects the detail relationships it already fetched: who the
device is connected to, how many interfaces and components it has, and when its
configuration was last backed up. No new request is made."""
from __future__ import annotations

from conftest import FakeClient, default_routes, load
from tools import devices


def test_detail_projects_topology_and_backups():
    d = devices.project_detail(load("device_detail_single.json")["data"])
    assert d["connected_devices"] == [{"device_id": "DEV-2", "name": "edge-fw-01"}, {"device_id": "DEV-9", "name": "ap-lobby-01"}]
    assert d["interface_count"] == 3 and d["component_count"] == 2
    assert d["config_backup_count"] == 2 and d["last_config_backup"] == "2026-08-29T03:00:00.000Z"


def test_detail_empty_relationships_are_zero_not_none():
    d = devices.project_detail({"id": "X", "attributes": {}, "relationships": {}})
    assert d["connected_devices"] == [] and d["interface_count"] == 0 and d["component_count"] == 0
    assert d["config_backup_count"] == 0 and d["last_config_backup"] == ""
    assert None not in d.values()


def test_get_device_makes_exactly_two_requests():
    fake = FakeClient(default_routes())
    out = devices.get_device(fake, "DEV-1")
    assert out["data_status"] == "ok" and out["device"]["connected_devices"][0]["name"] == "edge-fw-01"
    assert [c[1] for c in fake.calls] == ["/v1/inventory/device/info/DEV-1", "/v1/inventory/device/detail/DEV-1"]
