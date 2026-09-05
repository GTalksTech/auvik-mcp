from __future__ import annotations

import pytest

from conftest import FakeClient, default_routes, load
from tools import interfaces


def test_project_interface_maps_all_fields():
    p = interfaces.project_interface(load("interfaces.json")["data"][0])
    assert p == {"interface_id": "IF-1", "name": "GigabitEthernet1/0/1", "type": "ethernet", "mac": "1A:2B:3C:4D:5E:6F",
                 "speed": "1.0 Gbit/s", "duplex": "full", "ips": ["10.10.0.1"], "oper_status": "online",
                 "admin_up": True, "last_modified": "2026-08-01T00:00:00.000Z", "device_id": "DEV-1"}


def test_project_interface_missing_never_none():
    p = interfaces.project_interface({"id": "X", "attributes": {}, "relationships": {}})
    assert None not in p.values() and p["admin_up"] is False and p["ips"] == []


def test_list_interfaces_params_counts_and_limit():
    fake = FakeClient(default_routes())
    out = interfaces.list_interfaces(fake, ["T1"], "DEV-1", interface_type="ethernet", operational_status="online", limit=2)
    params = fake.calls[0][2]
    assert fake.calls[0][1] == "/v1/inventory/interface/info"
    assert params == {"tenants": "T1", "filter[parentDevice]": "DEV-1", "filter[interfaceType]": "ethernet",
                      "filter[operationalStatus]": "online"}
    assert out["data_status"] == "ok" and out["total_count"] == 3 and len(out["interfaces"]) == 2 and out["truncated"] is True
    assert out["counts_by_oper_status"] == {"online": 2, "offline": 1}


@pytest.mark.parametrize("kw", [{"interface_type": "carrierpigeon"}, {"operational_status": "meh"}, {"limit": 0}])
def test_list_interfaces_rejects_bad_input(kw):
    with pytest.raises(ValueError):
        interfaces.list_interfaces(FakeClient(default_routes()), ["T1"], "DEV-1", **kw)


def test_list_interfaces_requires_device_id():
    with pytest.raises(ValueError):
        interfaces.list_interfaces(FakeClient(default_routes()), ["T1"], "")
