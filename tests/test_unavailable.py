"""With upstream failing every tool says 'unavailable', carries a
detail, and leaks NO data-shaped keys. With upstream returning nothing, tools
return successful zeros. The two worlds are distinguishable in the payload."""
from __future__ import annotations

import pytest

from conftest import ALL_TOOLS, REQUIRED_ARGS, FakeClient

DATA_KEYS = {
    "alerts", "alert", "devices", "device", "interfaces", "series_for", "total_count", "returned_count",
    "truncated", "counts_by_severity", "counts_by_oper_status", "entity_count", "dismissed", "window_hours",
    "groups", "group_count", "alert_count", "active_alert_count",
    "networks", "counts_by_type", "counts_by_scan_status",
    "components", "counts_by_status",
    "devices_changed", "device_count", "backup_count",
    "sessions", "counts_by_user", "counts_by_category",
    "counts_by_last_support",
}


@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_upstream_failure_yields_explicit_unavailable(tool, srv_down, calltool):
    from tools import alerts
    alerts.SEEN_ALERT_IDS.add("AL-1")  # so dismiss reaches the re-read, which fails
    data = await calltool(tool, REQUIRED_ARGS.get(tool))
    assert data["data_status"] == "unavailable", f"{tool}: {data}"
    assert "503" in data["detail"]
    assert not (DATA_KEYS & set(data)), f"{tool} leaked data keys in an unavailable response"


async def test_empty_upstream_is_ok_zero(srv, calltool):
    server, fake = srv
    empty = {"data": [], "links": {}}
    fake.routes["/v1/alert/history/info"] = empty
    fake.routes["/v1/inventory/device/info"] = empty
    fake.routes["/v1/inventory/interface/info"] = empty
    server.get_device_cache().clear()
    for tool, args in (("auvik_list_alerts", {}), ("auvik_find_device", {"name": "x"}), ("auvik_list_interfaces", {"device_id": "DEV-1"})):
        data = await calltool(tool, args)
        assert data["data_status"] == "ok" and data["total_count"] == 0, f"{tool}: {data}"


async def test_startup_failure_is_unavailable_with_startup_error(monkeypatch, calltool):
    import server
    from credentials import CredentialsMissing

    server.reset_state_for_tests(None, [])
    monkeypatch.setattr(server, "load_credentials", lambda: (_ for _ in ()).throw(CredentialsMissing("no creds here")))
    data = await calltool("auvik_list_alerts", {})
    assert data["data_status"] == "unavailable" and "no creds here" in data["detail"]
    server.reset_state_for_tests(None, [])


async def test_bad_enum_is_refused(srv, calltool):
    data = await calltool("auvik_list_alerts", {"severity": "bogus"})
    assert data["data_status"] == "refused" and "valid:" in data["detail"]
