"""Hardware fault sweep. The API filters on ONE status, so the default problems
view is two server-side calls (degraded, failed) merged; a fleet-wide list of
healthy components is refused."""
from __future__ import annotations

import pytest

from conftest import FakeClient, default_routes, load
from tools import components as C

SITES = {"T1": "acme-hq"}


def test_project_component_maps_all_fields():
    p = C.project_component(load("components.json")["data"][1])
    assert p == {"component_id": "CMP-2", "tenant_id": "T1", "site": "acme-hq", "name": "Fan 1", "type": "fan",
                 "status": "degraded", "last_modified": "2026-08-28T00:00:00.000Z",
                 "device_id": "DEV-1", "device_name": "core-sw-01"}
    assert None not in C.project_component({"id": "X", "attributes": {}, "relationships": {}}).values()


def test_default_problems_only_makes_two_calls_and_merges():
    fake = FakeClient(default_routes())
    out = C.component_health(fake, ["T1"], tenant_sites=SITES)
    statuses = [c[2].get("filter[currentStatus]") for c in fake.calls]
    assert statuses == ["degraded", "failed"] and all(c[1] == "/v1/inventory/component/info" for c in fake.calls)
    assert [c["component_id"] for c in out["components"]] == ["CMP-2", "CMP-3"]
    assert out["total_count"] == 2 and out["counts_by_status"] == {"degraded": 1, "failed": 1}
    assert out["counts_by_type"] == {"fan": 1, "powerSupply": 1} and out["sites"] == ["acme-hq"]


def test_explicit_status_is_one_call_and_device_id_reaches_the_api():
    fake = FakeClient(default_routes())
    out = C.component_health(fake, ["T1"], status="ok", device_id="DEV-1", tenant_sites=SITES)
    assert len(fake.calls) == 1 and fake.calls[0][2] == {"tenants": "T1", "filter[currentStatus]": "ok", "filter[deviceId]": "DEV-1"}
    assert [c["component_id"] for c in out["components"]] == ["CMP-1", "CMP-4"]  # FakeClient does not filter on device


def test_all_components_of_one_device_is_allowed_fleet_wide_is_refused():
    fake = FakeClient(default_routes())
    out = C.component_health(fake, ["T1"], device_id="DEV-1", problems_only=False, tenant_sites=SITES)
    assert out["total_count"] == 4 and fake.calls[0][2] == {"tenants": "T1", "filter[deviceId]": "DEV-1"}
    with pytest.raises(ValueError, match="fleet-wide"):
        C.component_health(fake, ["T1"], problems_only=False, tenant_sites=SITES)


def test_component_type_filter_limit_and_unmatched_site():
    fake = FakeClient(default_routes())
    out = C.component_health(fake, ["T1"], device_id="DEV-1", problems_only=False, component_type="disk", tenant_sites=SITES)
    assert [c["component_id"] for c in out["components"]] == ["CMP-4"] and out["total_count"] == 1
    out = C.component_health(fake, ["T1"], device_id="DEV-1", problems_only=False, limit=1, tenant_sites=SITES)
    assert out["returned_count"] == 1 and out["total_count"] == 4 and out["truncated"] is True
    fake2 = FakeClient(default_routes())
    out = C.component_health(fake2, ["T1"], site="zzz", tenant_sites=SITES)
    assert out["data_status"] == "ok" and out["components"] == [] and fake2.calls == []
