"""End-of-sale / end-of-support / warranty per device. Built only after a live
probe confirmed Auvik actually holds informative lifecycle data (it has it for
some vendors and not others). has_problem is driven by lifecycle statuses only;
warranty fields are informational.

Deviation D-9 is pinned here. Live Auvik returns Title-Case statuses
("Expired", "Available") while the OpenAPI file documents camelCase
("expired", "available"). Matching exactly, as the OpenAPI file implies, scores
has_problem on NOTHING, even on records Auvik is reporting as `Expired` - a
confident, successful-looking "nothing is end-of-support" that is false. The
comparison folds case; both spellings are asserted below so neither can regress.
"""
from __future__ import annotations

from conftest import FakeClient, default_routes, load
from tools import lifecycle as L

SITES = {"T1": "acme-hq"}


def test_project_lifecycle_merges_warranty_and_flags_problems():
    wmap = {w["id"]: w for w in load("warranty.json")["data"]}
    ok_row = L.project_lifecycle(load("lifecycle.json")["data"][0], wmap)
    bad_row = L.project_lifecycle(load("lifecycle.json")["data"][1], wmap)
    assert ok_row == {"device_id": "DEV-1", "device_name": "core-sw-01", "tenant_id": "T1", "site": "acme-hq",
                      "sales_availability": "Available", "software_maintenance": "Available",
                      "security_maintenance": "Available", "last_support": "Available",
                      "warranty_coverage": "Available", "warranty_expiration": "2027-01-01 00:00:00.000+0000",
                      "service_coverage": "Covered", "recommended_software": "1.2.4", "has_problem": False}
    assert bad_row["has_problem"] is True and bad_row["last_support"] == "Expired" and bad_row["warranty_coverage"] == "Expired"
    bare = L.project_lifecycle({"id": "X", "attributes": {}, "relationships": {}}, {})
    assert None not in bare.values() and bare["has_problem"] is False


def test_has_problem_is_case_insensitive_over_both_spellings():
    """D-9. The live API sends Title-Case; the OpenAPI file documents camelCase.
    Both must flag, and the projected VALUE is passed through verbatim either
    way - this server never presents text Auvik did not send (D-6, D-8)."""
    def _row(**attrs):
        return {"id": "X", "attributes": attrs, "relationships": {}}

    for spelling in ("expired", "Expired", "EXPIRED"):
        p = L.project_lifecycle(_row(lastSupportStatus=spelling), {})
        assert p["has_problem"] is True, spelling
        assert p["last_support"] == spelling, "the vendor's own string must be passed through unaltered"
    for spelling in ("securityOnly", "SecurityOnly"):
        assert L.project_lifecycle(_row(softwareMaintenanceStatus=spelling), {})["has_problem"] is True, spelling
    for benign in ("Available", "available", "Unpublished", "Covered", ""):
        assert L.project_lifecycle(_row(lastSupportStatus=benign), {})["has_problem"] is False, benign


def test_problems_only_default_and_counts():
    fake = FakeClient(default_routes())
    out = L.device_lifecycle(fake, ["T1"], tenant_sites=SITES)
    assert [c[1] for c in fake.calls] == ["/v1/inventory/device/lifecycle", "/v1/inventory/device/warranty"]
    assert [d["device_id"] for d in out["devices"]] == ["DEV-2"] and out["total_count"] == 1
    assert out["counts_by_last_support"] == {"Expired": 1} and out["sites"] == ["acme-hq"]
    out = L.device_lifecycle(fake, ["T1"], problems_only=False, tenant_sites=SITES)
    assert out["total_count"] == 2 and out["counts_by_last_support"] == {"Available": 1, "Expired": 1}


def test_include_warranty_false_skips_the_call():
    fake = FakeClient(default_routes())
    out = L.device_lifecycle(fake, ["T1"], problems_only=False, include_warranty=False, tenant_sites=SITES)
    assert [c[1] for c in fake.calls] == ["/v1/inventory/device/lifecycle"]
    assert out["devices"][0]["warranty_coverage"] == ""


def test_device_id_uses_the_single_endpoints():
    fake = FakeClient(default_routes())
    out = L.device_lifecycle(fake, ["T1"], device_id="DEV-2", tenant_sites=SITES)
    assert [c[1] for c in fake.calls] == ["/v1/inventory/device/lifecycle/DEV-2", "/v1/inventory/device/warranty/DEV-2"]
    assert out["total_count"] == 1 and out["devices"][0]["warranty_expiration"] == "2024-06-30 00:00:00.000+0000"


def test_unmatched_site_is_zero_and_limit_truncates():
    fake = FakeClient(default_routes())
    out = L.device_lifecycle(fake, ["T1"], site="nowhere", tenant_sites=SITES)
    assert out["data_status"] == "ok" and out["devices"] == [] and fake.calls == []
    out = L.device_lifecycle(fake, ["T1"], problems_only=False, limit=1, tenant_sites=SITES)
    assert out["returned_count"] == 1 and out["total_count"] == 2 and out["truncated"] is True
