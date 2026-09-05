"""Every tool's response stays under 500 KB even when upstream is
padded to the largest plausible size; truncated/total_count stay honest."""
from __future__ import annotations

import copy
import json

import pytest

from conftest import ALL_TOOLS, REQUIRED_ARGS, load

CAP = 500 * 1024


def _pad(body: dict, n: int) -> dict:
    body = copy.deepcopy(body)
    base = body["data"][0]
    body["data"] = [{**copy.deepcopy(base), "id": f"{base['id']}-{i}"} for i in range(n)]
    body.pop("_next", None)
    return body


def _pad_series(body: dict, points: int, entities: int) -> dict:
    body = copy.deepcopy(body)
    base = body["data"][0]
    # unix_mins (MINUTES since the epoch), matching the wire shape the rest of
    # this repo documents - not epoch_ms. Hourly interval = +60.
    base["attributes"]["stats"][0]["data"] = [[29799780 + i * 60, 1.5, 2.5] for i in range(points)]
    body["data"] = [{**copy.deepcopy(base), "id": f"ST-{i}"} for i in range(entities)]
    return body


@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_response_under_cap(tool, srv, calltool):
    server, fake = srv
    fake.routes["/v1/alert/history/info"] = _pad(load("alerts_page1.json"), 1000)
    fake.routes["/v1/inventory/device/info"] = _pad(load("devices.json"), 5000)
    fake.routes["/v1/inventory/interface/info"] = _pad(load("interfaces.json"), 2000)
    fake.routes["/v1/inventory/network/info"] = _pad(load("networks.json"), 2000)
    # A static body: the two status calls will EACH return 2000, which is the worst case.
    fake.routes["/v1/inventory/component/info"] = _pad(load("components.json"), 2000)
    fake.routes["/v1/inventory/configuration"] = _pad(load("configurations.json"), 5000)
    fake.routes["/v1/inventory/entity/audit"] = _pad(load("audits.json"), 2000)
    fake.routes["/v1/inventory/device/lifecycle"] = _pad(load("lifecycle.json"), 5000)
    fake.routes["/v1/inventory/device/warranty"] = _pad(load("warranty.json"), 5000)
    fake.routes["/v1/stat/interface/"] = _pad_series(load("stat_interface.json"), 10000, 48)
    fake.routes["/v1/stat/device/"] = _pad_series(load("stat_device.json"), 10000, 1)
    fake.routes["/v1/stat/deviceAvailability/"] = _pad_series(load("stat_availability.json"), 10000, 1)
    fake.post_response = {"data": {}}
    server.get_device_cache().clear()
    from tools import alerts
    alerts.SEEN_ALERT_IDS.add("AL-1")

    args = dict(REQUIRED_ARGS.get(tool, {}))
    if tool in ("auvik_list_alerts", "auvik_find_device", "auvik_list_interfaces", "auvik_alert_summary",
                "auvik_list_networks", "auvik_component_health",
                "auvik_config_changes", "auvik_remote_access_audit",
                "auvik_device_lifecycle"):
        args["limit"] = 200
    data = await calltool(tool, args)
    size = len(json.dumps(data).encode())
    assert size < CAP, f"{tool}: {size} bytes"
    if "total_count" in data:
        assert data["total_count"] >= data["returned_count"]
        if data["total_count"] > data["returned_count"]:
            assert data["truncated"] is True


async def test_stats_series_row_truncation_reports_full_count(srv, calltool):
    server, fake = srv
    fake.routes["/v1/stat/device/"] = _pad_series(load("stat_device.json"), 10000, 1)
    data = await calltool("auvik_device_stats", {"stat": "cpuUtilization", "device_id": "DEV-1", "limit": 50})
    s = data["series_for"][0]["series"][0]
    assert s["row_count"] == 10000 and len(s["rows"]) == 50 and s["truncated"] is True


async def test_interface_stats_on_a_large_stack_stays_under_cap(srv, calltool):
    """A 400-port stacked switch is an ordinary thing to find in a datacentre.
    At only 48 interfaces this tool already uses over half the cap, so the
    per-entity row budget must scale down with the number of entities returned,
    and must still report the true row_count for each series."""
    server, fake = srv
    fake.routes["/v1/stat/interface/"] = _pad_series(load("stat_interface.json"), 10000, 400)
    data = await calltool("auvik_interface_stats", {"stat": "utilization", "device_id": "DEV-1", "limit": 200})
    assert data["data_status"] == "ok" and data["entity_count"] == 400
    size = len(json.dumps(data).encode())
    assert size < CAP, f"400-interface stack: {size} bytes"
    s = data["series_for"][0]["series"][0]
    assert s["row_count"] == 10000 and s["truncated"] is True and len(s["rows"]) >= 1
