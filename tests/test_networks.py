"""What Auvik OBSERVES at a site, with scan_status on every row so "scanned
here" is separable from "seen over L3 from here". Not an IPAM and not a source
of truth for addressing."""
from __future__ import annotations

import pytest

from conftest import FakeClient, default_routes, load
from tools import networks as N

SITES = {"T1": "acme-hq", "T2": "acme-bos"}


def test_project_network_maps_all_fields_and_counts_devices():
    item = load("networks.json")["data"][0]
    p = N.project_network(item)
    assert p == {"network_id": "NET-1", "tenant_id": "T1", "site": "acme-hq", "name": "10.10.0.0/24",
                 "description": "10.10.0.0/24", "type": "routed", "scan_status": "true",
                 "last_modified": "2026-08-20T00:00:00.000Z", "device_count": 2, "devices": [], "devices_truncated": False}
    p2 = N.project_network(item, include_devices=True, max_devices=1)
    assert p2["devices"] == [{"device_id": "DEV-1", "name": "core-sw-01"}] and p2["devices_truncated"] is True


def test_project_network_normalises_boolean_scan_status_and_never_none():
    p = N.project_network({"id": "X", "attributes": {"scanStatus": False}, "relationships": {}})
    assert p["scan_status"] == "false" and None not in p.values() and p["device_count"] == 0


def test_list_networks_filters_reach_the_api_and_counts():
    fake = FakeClient(default_routes())
    out = N.list_networks(fake, ["T1"], network_type="vlan", scan_status="false", device_id="DEV-1", tenant_sites=SITES)
    _, path, params = fake.calls[0]
    assert path == "/v1/inventory/network/info"
    assert params == {"tenants": "T1", "filter[networkType]": "vlan", "filter[scanStatus]": "false", "filter[devices]": "DEV-1"}
    assert out["data_status"] == "ok" and out["total_count"] == 3  # FakeClient ignores filters; counts come from what returned
    assert out["counts_by_type"] == {"routed": 1, "vlan": 1, "wifi": 1}
    assert out["counts_by_scan_status"] == {"true": 1, "false": 1, "notAllowed": 1}
    assert out["sites"] == ["acme-bos", "acme-hq"]


def test_list_networks_name_filter_and_limit():
    fake = FakeClient(default_routes())
    out = N.list_networks(fake, ["T1"], name="voice", tenant_sites=SITES)
    assert [n["network_id"] for n in out["networks"]] == ["NET-2"] and out["total_count"] == 1
    out = N.list_networks(fake, ["T1"], limit=1, tenant_sites=SITES)
    assert out["returned_count"] == 1 and out["total_count"] == 3 and out["truncated"] is True


def test_list_networks_bad_enum_refused_and_unmatched_site_zero():
    fake = FakeClient(default_routes())
    with pytest.raises(ValueError, match="valid:"):
        N.list_networks(fake, ["T1"], network_type="bogus")
    out = N.list_networks(fake, ["T1", "T2"], site="nowhere", tenant_sites=SITES)
    assert out["data_status"] == "ok" and out["networks"] == [] and out["total_count"] == 0
    assert "no site matches 'nowhere'" in out["detail"] and fake.calls == []


def test_the_documented_per_network_device_cap_is_the_one_actually_applied():
    """`MAX_DEVICES_PER_NETWORK` is stated as 50 in two published documents, and
    was pinned by nothing — changing it to 5 left the whole suite green. The cap
    is a response-size control, so a silent change to it is a silent change to
    whether this tool can blow the 500 KB budget."""
    from pathlib import Path  # noqa: PLC0415

    assert N.MAX_DEVICES_PER_NETWORK == 50

    # The doc cross-check deliberately lives in tests/test_documented_constants.py,
    # which flattens markdown before matching. The version that used to be here
    # matched raw text, so HOSTING.md's wrapped "per-network device / list at 50"
    # never entered the guard and it asserted nothing - the exact defect that
    # file exists to prevent, reproduced inside the test written to prevent it.

    # The default really applies, without being passed explicitly:
    many = {"id": "NET-BIG", "attributes": {"networkName": "big"},
            "relationships": {"devices": {"data": [
                {"type": "device", "id": f"DEV-{i}", "attributes": {"deviceName": f"d{i}"}}
                for i in range(N.MAX_DEVICES_PER_NETWORK + 25)]}}}
    p = N.project_network(many, include_devices=True)
    assert p["device_count"] == N.MAX_DEVICES_PER_NETWORK + 25, "device_count must report the true total"
    assert len(p["devices"]) == N.MAX_DEVICES_PER_NETWORK, "the default cap was not applied"
    assert p["devices_truncated"] is True, "a capped list must say it was capped"
