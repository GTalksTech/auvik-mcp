""""Did any network device's configuration change this week?" answered from
backup metadata: a new backup version in the window is the change signal,
independent of whether Auvik's own config-change alert fired. Auvik returns
backupTime and isRunning only - no config text and no diff are available from
the API at all."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from conftest import FakeClient, default_routes
from tools import configs as CF

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
SITES = {"T1": "acme-hq"}
resolve = lambda did: ""  # noqa: E731 - inline relationship names are preferred


def test_groups_backups_per_device_newest_first():
    fake = FakeClient(default_routes())
    out = CF.config_changes(fake, ["T1"], resolve, hours=168, tenant_sites=SITES, now=NOW)
    _, path, params = fake.calls[0]
    assert path == "/v1/inventory/configuration"
    assert params == {"tenants": "T1", "filter[backupTimeAfter]": "2026-08-22T12:00:00.000Z"}
    assert out["data_status"] == "ok" and out["backup_count"] == 4 and out["device_count"] == 2
    d1, d2 = out["devices_changed"]  # sorted by last_backup desc: DEV-1 (08-29) before DEV-2 (08-01)
    assert (d1["device_id"], d1["device_name"], d1["site"]) == ("DEV-1", "core-sw-01", "acme-hq")
    assert (d1["backup_count"], d1["running_backup_count"]) == (3, 1)
    assert (d1["first_backup"], d1["last_backup"]) == ("2026-08-27T03:00:00.000Z", "2026-08-29T03:00:00.000Z")
    assert d1["backups"][0] == {"backup_time": "2026-08-29T03:00:00.000Z", "is_running": True}
    assert d1["backups_truncated"] is False and d2["device_id"] == "DEV-2"
    assert out["total_count"] == 2 and out["window_hours"] == 168 and out["sites"] == ["acme-hq"]


def test_device_id_reaches_the_api_and_hours_zero_needs_device_id():
    fake = FakeClient(default_routes())
    out = CF.config_changes(fake, ["T1"], resolve, hours=0, device_id="DEV-1", tenant_sites=SITES, now=NOW)
    assert fake.calls[0][2] == {"tenants": "T1", "filter[deviceId]": "DEV-1"}
    assert out["device_count"] == 1 and out["devices_changed"][0]["backup_count"] == 3
    with pytest.raises(ValueError, match="device_id"):
        CF.config_changes(fake, ["T1"], resolve, hours=0, tenant_sites=SITES, now=NOW)
    with pytest.raises(ValueError):
        CF.config_changes(fake, ["T1"], resolve, hours=-1, tenant_sites=SITES, now=NOW)


def test_per_device_backup_list_is_capped_with_true_count():
    fake = FakeClient(default_routes())
    out = CF.config_changes(fake, ["T1"], resolve, device_id="DEV-1", tenant_sites=SITES, now=NOW, max_backups_per_device=2)
    d = out["devices_changed"][0]
    assert d["backup_count"] == 3 and len(d["backups"]) == 2 and d["backups_truncated"] is True
    assert [b["backup_time"] for b in d["backups"]] == ["2026-08-29T03:00:00.000Z", "2026-08-28T03:00:00.000Z"]


def test_limit_truncates_devices_and_unmatched_site_is_zero():
    fake = FakeClient(default_routes())
    out = CF.config_changes(fake, ["T1"], resolve, limit=1, tenant_sites=SITES, now=NOW)
    assert out["returned_count"] == 1 and out["device_count"] == 2 and out["truncated"] is True
    fake2 = FakeClient(default_routes())
    out = CF.config_changes(fake2, ["T1"], resolve, site="nowhere", tenant_sites=SITES, now=NOW)
    assert out["data_status"] == "ok" and out["devices_changed"] == [] and out["device_count"] == 0 and fake2.calls == []


def test_missing_inline_name_falls_back_to_the_cache_and_never_none():
    fake = FakeClient(default_routes())
    body = fake.routes["/v1/inventory/configuration"]({})
    for i in body["data"]:
        i["relationships"]["device"]["data"].pop("attributes", None)
    fake.routes["/v1/inventory/configuration"] = body
    out = CF.config_changes(fake, ["T1"], lambda did: {"DEV-1": "from-cache"}.get(did, ""), tenant_sites=SITES, now=NOW)
    names = {d["device_id"]: d["device_name"] for d in out["devices_changed"]}
    assert names == {"DEV-1": "from-cache", "DEV-2": ""}
