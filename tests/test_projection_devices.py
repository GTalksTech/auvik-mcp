from __future__ import annotations

import pytest

from conftest import FakeClient, default_routes, load
from tools import devices


def test_project_device_maps_all_fields():
    p = devices.project_device(load("device_single.json")["data"])
    assert p == {
        "device_id": "DEV-1", "tenant_id": "T1", "site": "acme-hq",
        "name": "core-sw-01", "type": "switch", "ips": ["10.10.0.1"],
        "make_model": "Catalyst 9300", "vendor": "Cisco", "software_version": "1.2.3",
        "firmware_version": "fw-9", "serial": "SN-CORE1", "description": "Cisco Catalyst 9300",
        "online_status": "online", "last_seen": "2026-08-29T06:00:00.000Z", "last_modified": "2026-08-01T00:00:00.000Z",
    }


def test_project_device_missing_never_none():
    p = devices.project_device({"id": "X", "attributes": {}})
    assert None not in p.values() and p["ips"] == []


def test_project_detail():
    d = devices.project_detail(load("device_detail_single.json")["data"])
    assert d == {"managed": True, "snmp_discovery": "authorized", "wmi_discovery": "disabled",
                 "login_discovery": "privileged", "vmware_discovery": "disabled", "traffic_insights": "notDetected",
                 "connected_devices": [{"device_id": "DEV-2", "name": "edge-fw-01"},
                                       {"device_id": "DEV-9", "name": "ap-lobby-01"}],
                 "interface_count": 3, "component_count": 2,
                 "config_backup_count": 2, "last_config_backup": "2026-08-29T03:00:00.000Z"}


def test_cache_fetches_once_and_resolves_names():
    fake = FakeClient(default_routes())
    c = devices.DeviceCache(fake, ["T1"], clock=lambda: 0.0)
    assert c.name_of("DEV-1") == "core-sw-01" and c.name_of("DEV-2") == "edge-fw-01" and c.name_of("nope") == ""
    assert len(c.all()) == 2
    assert len([x for x in fake.calls if x[1] == "/v1/inventory/device/info"]) == 1
    assert fake.calls[0][2] == {"tenants": "T1"}


def test_find_device_by_name_substring_case_insensitive():
    c = devices.DeviceCache(FakeClient(default_routes()), ["T1"])
    out = devices.find_device(c, name="CORE")
    assert out["data_status"] == "ok" and [d["device_id"] for d in out["devices"]] == ["DEV-1"]
    assert out["total_count"] == 1 and out["truncated"] is False


def test_find_device_by_ip_exact_and_by_filters():
    c = devices.DeviceCache(FakeClient(default_routes()), ["T1"])
    assert [d["device_id"] for d in devices.find_device(c, ip="10.10.0.254")["devices"]] == ["DEV-2"]
    assert [d["device_id"] for d in devices.find_device(c, ip="10.10.0.25")["devices"]] == []
    assert [d["device_id"] for d in devices.find_device(c, device_type="firewall")["devices"]] == ["DEV-2"]
    assert [d["device_id"] for d in devices.find_device(c, vendor="cisco")["devices"]] == ["DEV-1"]
    assert [d["device_id"] for d in devices.find_device(c, online_status="offline")["devices"]] == ["DEV-2"]


def test_find_device_zero_matches_is_ok_zero():
    c = devices.DeviceCache(FakeClient(default_routes()), ["T1"])
    out = devices.find_device(c, name="doesnotexist")
    assert out["data_status"] == "ok" and out["devices"] == [] and out["total_count"] == 0


def test_find_device_limit_truncates_with_true_total():
    c = devices.DeviceCache(FakeClient(default_routes()), ["T1"])
    out = devices.find_device(c, limit=1)
    assert len(out["devices"]) == 1 and out["total_count"] == 2 and out["truncated"] is True


@pytest.mark.parametrize("kw", [{"device_type": "toaster"}, {"online_status": "meh"}, {"limit": 0}])
def test_find_device_rejects_bad_input(kw):
    c = devices.DeviceCache(FakeClient(default_routes()), ["T1"])
    with pytest.raises(ValueError):
        devices.find_device(c, **kw)


def test_get_device_merges_info_and_detail():
    fake = FakeClient(default_routes())
    out = devices.get_device(fake, "DEV-1")
    assert out["data_status"] == "ok"
    assert out["device"]["name"] == "core-sw-01" and out["device"]["managed"] is True
    paths = [p for _, p, _ in fake.calls]
    assert paths == ["/v1/inventory/device/info/DEV-1", "/v1/inventory/device/detail/DEV-1"]


def test_get_device_requires_id():
    with pytest.raises(ValueError):
        devices.get_device(FakeClient(default_routes()), " ")
