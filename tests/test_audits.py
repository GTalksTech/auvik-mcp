"""Who opened a remote terminal / tunnel / browser session to which device, and
when. user/category/status are server-side filters; there is no entity filter on
this endpoint, so device_id is applied client-side."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from conftest import FakeClient, default_routes, load
from tools import audits as AU

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
SITES = {"T1": "acme-hq"}


def test_project_audit_maps_all_fields_and_caps_data():
    p = AU.project_audit(load("audits.json")["data"][2])
    assert p == {"audit_id": "AUD-3", "tenant_id": "T1", "site": "acme-hq", "user": "Alex Netadmin",
                 "category": "remoteBrowser", "action": "create", "direction": "outbound", "status": "failed",
                 "cause": "connection refused", "data": "session opened",
                 "started": "2026-08-27T08:00:00.000Z", "last_active": "2026-08-27T08:00:10.000Z",
                 "device_id": "DEV-1", "device_name": "core-sw-01"}
    long = {"id": "X", "attributes": {"data": "x" * 1000}, "relationships": {}}
    assert len(AU.project_audit(long)["data"]) == 300 and None not in AU.project_audit(long).values()


def test_filters_reach_the_api_and_sessions_sort_newest_first():
    fake = FakeClient(default_routes())
    out = AU.remote_access_audit(fake, ["T1"], user="Alex Netadmin", category="terminal", status="closed",
                                 tenant_sites=SITES, now=NOW)
    _, path, params = fake.calls[0]
    assert path == "/v1/inventory/entity/audit"
    assert params == {"tenants": "T1", "filter[user]": "Alex Netadmin", "filter[category]": "terminal",
                      "filter[status]": "closed", "filter[modifiedAfter]": "2026-08-22T12:00:00.000Z"}
    assert [s["audit_id"] for s in out["sessions"]] == ["AUD-2", "AUD-1", "AUD-3"]  # FakeClient ignores filters
    assert out["counts_by_user"] == {"Alex Netadmin": 2, "Jane Tech": 1}
    assert out["counts_by_category"] == {"terminal": 1, "tunnel": 1, "remoteBrowser": 1}
    assert out["window_hours"] == 168 and out["sites"] == ["acme-hq"]


def test_device_id_is_client_side_and_hours_zero_sends_no_time_filter():
    fake = FakeClient(default_routes())
    out = AU.remote_access_audit(fake, ["T1"], device_id="DEV-2", hours=0, tenant_sites=SITES, now=NOW)
    assert "filter[modifiedAfter]" not in fake.calls[0][2]
    assert [s["audit_id"] for s in out["sessions"]] == ["AUD-2"] and out["total_count"] == 1


def test_bad_enum_refused_limit_truncates_unmatched_site_zero():
    fake = FakeClient(default_routes())
    with pytest.raises(ValueError, match="valid:"):
        AU.remote_access_audit(fake, ["T1"], category="ssh")
    out = AU.remote_access_audit(fake, ["T1"], limit=1, tenant_sites=SITES, now=NOW)
    assert out["returned_count"] == 1 and out["total_count"] == 3 and out["truncated"] is True
    fake2 = FakeClient(default_routes())
    out = AU.remote_access_audit(fake2, ["T1"], site="nowhere", tenant_sites=SITES, now=NOW)
    assert out["data_status"] == "ok" and out["sessions"] == [] and fake2.calls == []
