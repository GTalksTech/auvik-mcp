"""Structural invariants that must hold on EITHER fixture tier.

The value-level projection tests hard-code synthetic IDs (AL-1, DEV-1, IF-1) and
so cannot run against recorded fixtures. These tests assert only what is true of
both tiers, so `AUVIK_FIXTURES=recorded pytest` exercises them against real
recorded data:

  * every projection returns its full documented key set — no key silently
    disappears because an attribute was absent upstream;
  * no value is ever None, at any depth — this server's rule is ""/[]/False,
    because None round-trips to JSON `null` and a Copilot Studio connector
    cannot type it;
  * no projection raises on real data.

This is what caught nothing and proved much: run against a real recording,
every failure elsewhere in the suite was a value mismatch (real serials versus
'SN-CORE1'), and every structural invariant below held.
"""
from __future__ import annotations

import pytest

from conftest import load
from tools import alerts, devices, interfaces, stats

ALERT_KEYS = {"alert_id", "tenant_id", "site", "name", "severity", "status", "detected_on",
              "dismissed", "dispatched", "description", "entity_type", "entity_id",
              "device_id", "device_name", "interface_name", "related_alert_id", "external_tickets"}
DEVICE_KEYS = {"device_id", "tenant_id", "site", "name", "type", "ips", "make_model", "vendor",
               "software_version", "firmware_version", "serial", "description", "online_status",
               "last_seen", "last_modified"}
DETAIL_KEYS = {"managed", "snmp_discovery", "wmi_discovery", "login_discovery",
               "vmware_discovery", "traffic_insights", "connected_devices", "interface_count",
               "component_count", "config_backup_count", "last_config_backup"}
IFACE_KEYS = {"interface_id", "name", "type", "mac", "speed", "duplex", "ips",
              "oper_status", "admin_up", "last_modified", "device_id"}
SERIES_KEYS = {"entity_id", "entity_name", "stat", "requested_stat", "interval",
               "from_time", "thru_time", "series"}

_resolve = lambda _did: ""  # noqa: E731 — name resolution is covered elsewhere


def _no_none(obj, path="root"):
    """Recursively assert nothing is None. None becomes JSON null, which the
    Copilot Studio connector cannot type and the agent cannot reason about."""
    if obj is None:
        pytest.fail(f"None leaked into the projection at {path}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _no_none(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _no_none(v, f"{path}[{i}]")


def _items(fixture, key="data"):
    body = load(fixture)
    d = body.get(key)
    if d is None:
        return []
    return d if isinstance(d, list) else [d]


# Both alert pages, always. The entity-less collector alert lives on page 2, and
# a test that reads only page 1 silently skips the one record most likely to drop
# a key or leak a None - which is exactly how the D-5 defect survived the first
# time. See ALERT_PAGES's use in the two tests below.
ALERT_PAGES = ("alerts_page1.json", "alerts_page2.json")


def test_every_alert_projects_completely():
    rows = [i for page in ALERT_PAGES for i in _items(page)]
    rows += _items("alert_single.json") + _items("alert_single_dismissed.json")
    assert rows, "no alerts in this fixture tier"
    for item in rows:
        p = alerts.project_alert(item, _resolve)
        assert set(p) == ALERT_KEYS, f"alert key drift: {set(p) ^ ALERT_KEYS}"
        _no_none(p, "alert")
        assert isinstance(p["dismissed"], bool) and isinstance(p["external_tickets"], list)


def test_alerts_without_an_entity_degrade_to_empty_strings():
    """About one real alert in ten (collector-level: 'Auvik Collector Approved',
    'Auvik Collector Reconnected') carries no `entity` relationship at all, even
    though the OpenAPI file lists one — deviation D-5. They must project as ""
    — not None, not a KeyError.

    This test previously read only `alerts_page1.json`, where every alert HAS an
    entity, so its loop body `continue`d on every item and it asserted nothing:
    the second incarnation of the very defect D-5 records. It now reads both
    pages AND fails if neither contains an entity-less alert, so it cannot go
    vacuous again by a fixture moving."""
    checked = 0
    for page in ALERT_PAGES:
        for item in _items(page):
            rel = item.get("relationships") or {}
            if (rel.get("entity") or {}).get("data"):
                continue
            p = alerts.project_alert(item, _resolve)
            assert p["entity_type"] == "" and p["entity_id"] == "" and p["device_id"] == "" and p["device_name"] == ""
            checked += 1
    assert checked, (
        "no entity-less alert in any committed alert page, so this test asserted nothing. "
        "A test that cannot fail is not a test - add a collector-level alert back to the fixtures."
    )


def test_every_device_and_detail_projects_completely():
    rows = _items("devices.json") + _items("device_single.json")
    assert rows, "no devices in this fixture tier"
    for item in rows:
        p = devices.project_device(item)
        assert set(p) == DEVICE_KEYS, f"device key drift: {set(p) ^ DEVICE_KEYS}"
        _no_none(p, "device")
        assert isinstance(p["ips"], list)
    for item in _items("device_detail_single.json"):
        d = devices.project_detail(item)
        assert set(d) == DETAIL_KEYS, f"detail key drift: {set(d) ^ DETAIL_KEYS}"
        _no_none(d, "detail")
        assert isinstance(d["managed"], bool)


def test_every_interface_projects_completely():
    rows = _items("interfaces.json")
    assert rows, "no interfaces in this fixture tier"
    for item in rows:
        p = interfaces.project_interface(item)
        assert set(p) == IFACE_KEYS, f"interface key drift: {set(p) ^ IFACE_KEYS}"
        _no_none(p, "interface")
        assert isinstance(p["admin_up"], bool) and isinstance(p["ips"], list)


@pytest.mark.parametrize("fixture", ["stat_interface.json", "stat_device.json", "stat_availability.json"])
def test_every_series_projects_completely(fixture):
    """Real Auvik sends `stats[].name` and `stats[].index` as null and labels
    the first column 'Recorded At' in unix_mins. The projection must absorb
    both without leaking None."""
    rows = _items(fixture)
    assert rows, f"no series in {fixture} for this fixture tier"
    for item in rows:
        p = stats.project_series(item, limit=50)
        assert set(p) == SERIES_KEYS, f"series key drift: {set(p) ^ SERIES_KEYS}"
        _no_none(p, "series")
        for s in p["series"]:
            assert set(s) == {"name", "index", "columns", "units", "rows", "row_count", "truncated", "summary"}
            assert len(s["summary"]) == max(0, len(s["columns"]) - 1), "one summary per value column"
            assert isinstance(s["name"], str) and isinstance(s["index"], str)
            assert len(s["columns"]) == len(s["units"]), "legend/unit arrays must stay parallel"
            assert s["row_count"] >= len(s["rows"])


def test_stat_data_is_a_list_in_practice():
    """Deviation D-1 settled: Auvik's OpenAPI declares /v1/stat/* `data` as an
    object, but the live API sends an ARRAY - one series for a device stat, one
    per interface for an interface stat. The recorded tier proves it; _as_list
    keeps the object path safe anyway, since the published document may yet
    describe some deployment."""
    for f in ("stat_interface.json", "stat_device.json", "stat_availability.json"):
        assert isinstance(load(f).get("data"), list), f"{f}: data is not a list"


NETWORK_KEYS = {"network_id", "tenant_id", "site", "name", "description", "type", "scan_status",
                "last_modified", "device_count", "devices", "devices_truncated"}


def test_network_projection_invariants():
    """Guarded like its siblings: a tier with no networks must SKIP, not report a
    silent green over a loop that never ran."""
    from tools import networks
    items = load("networks.json")["data"]
    if not items:
        pytest.skip("this fixture tier has no networks to project")
    for item in items:
        p = networks.project_network(item, include_devices=True)
        assert set(p) == NETWORK_KEYS
        _no_none(p)


COMPONENT_KEYS = {"component_id", "tenant_id", "site", "name", "type", "status", "last_modified", "device_id", "device_name"}


def test_component_projection_invariants():
    """Skips rather than passing vacuously: a recorded tier can legitimately hold
    no components at all, because an estate with nothing degraded or failed
    records nothing here. A silent green would then claim coverage it does not
    have (the D-5 lesson)."""
    from tools import components
    items = load("components.json")["data"]
    if not items:
        pytest.skip("this fixture tier has no components to project")
    for item in items:
        p = components.project_component(item)
        assert set(p) == COMPONENT_KEYS
        _no_none(p)


AUDIT_KEYS = {"audit_id", "tenant_id", "site", "user", "category", "action", "direction", "status", "cause",
              "data", "started", "last_active", "device_id", "device_name"}


def test_audit_projection_invariants():
    """Skips rather than passing vacuously: a recorded tier can legitimately hold
    no sessions, because none may have been opened in the window (the D-5
    lesson)."""
    from tools import audits
    items = load("audits.json")["data"]
    if not items:
        pytest.skip("this fixture tier has no audits to project")
    for item in items:
        p = audits.project_audit(item)
        assert set(p) == AUDIT_KEYS
        _no_none(p)


LIFECYCLE_KEYS = {"device_id", "device_name", "tenant_id", "site", "sales_availability", "software_maintenance",
                  "security_maintenance", "last_support", "warranty_coverage", "warranty_expiration",
                  "service_coverage", "recommended_software", "has_problem"}


def test_lifecycle_projection_invariants():
    from tools import lifecycle
    wmap = {w["id"]: w for w in load("warranty.json")["data"]}
    items = load("lifecycle.json")["data"]
    assert items, "no lifecycle records in this fixture tier"
    for item in items:
        p = lifecycle.project_lifecycle(item, wmap)
        assert set(p) == LIFECYCLE_KEYS
        _no_none(p)


def test_lifecycle_flags_a_problem_on_whichever_tier_is_active():
    """Deviation D-9's regression guard. The live API spells the statuses
    Title-Case; the OpenAPI file documents camelCase. Matching exactly scores
    has_problem on NOTHING, even on records Auvik is reporting as `Expired` - a
    fabricated zero on this tool's default view. Both tiers carry at least one
    problem device, so a reintroduced exact match fails here, not in
    production."""
    from tools import lifecycle
    rows = [lifecycle.project_lifecycle(i, {}) for i in load("lifecycle.json")["data"]]
    assert any(r["has_problem"] for r in rows), (
        "no lifecycle record flagged has_problem - case-sensitivity regression? (D-9)"
    )
