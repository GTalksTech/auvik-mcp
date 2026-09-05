"""Rules this repo states in prose and, until review, enforced nowhere.

Each test here exists because a mutation survived: the behaviour was changed so
the documented claim became false, and the whole suite stayed green. They are
grouped by the claim they defend rather than by module, because that is how they
were found and how they would be lost again.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from auvik_client import AuvikUnavailable
from conftest import FakeClient, default_routes, load
from tools import alerts, lifecycle, networks

ROOT = Path(__file__).resolve().parent.parent
SITES = {"T1": "acme-hq", "T2": "acme-bos"}
_resolve = lambda _d: ""  # noqa: E731


# --- "zero is never confused with unavailable", at startup -------------------

def test_a_credential_that_sees_no_tenants_is_an_error_not_an_empty_estate():
    """`server.py` refuses to start against a credential Auvik reports zero
    tenants for. Without that check every tool would answer `ok` with an empty
    list — a confident, successful-looking "your network is fine" produced by a
    misscoped API key. That is this server's founding failure mode, and removing
    the check left the suite green."""
    import server  # noqa: PLC0415

    class _NoTenants:
        creds = None

        def tenant_map(self):
            return {}

    server.reset_state_for_tests(None, [])
    try:
        import credentials  # noqa: PLC0415

        server.load_credentials = lambda: credentials.AuvikCredentials("u@example.com", "k", "us1")
        server.AuvikClient = lambda _c: _NoTenants()
        with pytest.raises(AuvikUnavailable) as ei:
            server._ensure_started()
        assert "zero tenants" in str(ei.value), (
            f"startup failed, but not with the reason that helps: {ei.value}"
        )
    finally:
        server.reset_state_for_tests(None, [])


# --- guardrail 2 is exactly as wide as it claims -----------------------------

def test_only_alerts_actually_returned_become_dismissible():
    """Every document says an alert must have been "returned by" a read. The
    seen-set is therefore filled from the TRUNCATED rows, not from everything
    that matched the filter — otherwise a limit=1 read would quietly make every
    alert in the window dismissible while showing the caller one of them."""
    fake = FakeClient(default_routes())
    out = alerts.list_alerts(fake, ["T1"], _resolve, hours=0, limit=1)

    assert len(out["alerts"]) == 1, "fixture must return more than it shows for this to mean anything"
    assert out["total_count"] > 1, "the filter must match more rows than the limit returns"
    returned = {a["alert_id"] for a in out["alerts"]}
    assert alerts.SEEN_ALERT_IDS == returned, (
        f"seen-set is {sorted(alerts.SEEN_ALERT_IDS)} but only {sorted(returned)} were returned; "
        "the guardrail is wider than every document describing it"
    )


# --- warranty is informational and must never drive has_problem --------------

def test_warranty_status_never_makes_a_device_a_problem():
    """`tools/lifecycle.py` says warranty "never drives has_problem: many vendors
    report 'Not Covered' simply for lack of data", and the tool description says
    has_problem is about lifecycle statuses. Folding warranty into the
    disjunction left the suite green, and would flag every device whose vendor
    simply reports nothing."""
    item = {"type": "deviceLifecycle", "id": "LC-9",
            "attributes": {"deviceName": "core-sw-01", "salesAvailability": "Available",
                           "softwareMaintenanceStatus": "Available",
                           "securitySoftwareMaintenanceStatus": "Available",
                           "lastSupportStatus": "Available"},
            "relationships": {"device": {"data": {"type": "device", "id": "DEV-9"}}}}
    warranties = {"DEV-9": {"id": "DEV-9", "attributes": {
        "warrantyCoverageStatus": "Expired", "serviceCoverageStatus": "Not Covered",
        "warrantyExpirationDate": "2019-01-01 00:00:00.000+0000"}}}

    row = lifecycle.project_lifecycle(item, warranties)
    assert row["warranty_coverage"] == "Expired", "the warranty value must still be reported"
    assert row["has_problem"] is False, (
        "an expired WARRANTY made has_problem true; only lifecycle statuses may do that"
    )


# --- alert_summary groups by site, as its description promises ---------------

def test_alert_groups_do_not_merge_across_sites():
    """The description promises "one row per (alert name, device/interface,
    site)". Dropping `site` from the group key left the suite green — and D-5
    collector alerts carry no entity, so identical alerts from different sites
    would silently collapse into one row."""
    base = load("alert_single.json")["data"]

    def _collector(aid, prefix, tid):
        a = {**base, "id": aid,
             "attributes": {**base["attributes"], "name": "Auvik Collector Reconnected",
                            "detectedOn": "2026-08-29T05:00:00.000Z"}}
        a["relationships"] = {"tenant": {"data": {"type": "tenant", "id": tid,
                                                  "attributes": {"domainPrefix": prefix}}}}
        return a

    body = {"data": [_collector("AL-A", "acme-hq", "T1"), _collector("AL-B", "acme-bos", "T2")], "links": {}}
    fake = FakeClient({"/v1/alert/history/info": body})
    out = alerts.summarize_alerts(fake, ["T1", "T2"], _resolve, hours=0, tenant_sites=SITES)

    assert out["group_count"] == 2, (
        f"two sites' collector alerts collapsed into {out['group_count']} group(s); "
        "the group key must include site"
    )
    assert {g["site"] for g in out["groups"]} == {"acme-hq", "acme-bos"}


# --- the networks name filter matches the description too --------------------

def test_the_network_name_filter_also_matches_the_description():
    """`auvik_list_networks`'s `name=` is documented as a substring of "the
    network name/description". The alerts tool has the same rule and a test;
    this one did not, and dropping the description half left the suite green."""
    body = {"data": [{"type": "network", "id": "NET-D",
                      "attributes": {"networkName": "vlan-42", "description": "Voice VLAN",
                                     "networkType": "vlan", "scanStatus": "true"},
                      "relationships": {}}], "links": {}}
    fake = FakeClient({"/v1/inventory/network/info": body})

    by_desc = networks.list_networks(fake, ["T1"], name="voice", tenant_sites={"T1": "acme-hq"})
    assert [n["network_id"] for n in by_desc["networks"]] == ["NET-D"], (
        "name= did not match the network's description"
    )
    by_name = networks.list_networks(fake, ["T1"], name="vlan-42", tenant_sites={"T1": "acme-hq"})
    assert [n["network_id"] for n in by_name["networks"]] == ["NET-D"]


# --- claims about the HTTP layer, pinned at the source -----------------------

SERVER = (ROOT / "server.py").read_text(encoding="utf-8")


def test_inbound_key_comparison_is_constant_time():
    """README and HOSTING both promise a constant-time comparison. Replacing it
    with `!=` left the suite green — the behaviour is identical, which is exactly
    why only the source can pin it."""
    assert "hmac.compare_digest(" in SERVER, (
        "the inbound API key is no longer compared with hmac.compare_digest; "
        "both published documents promise a constant-time comparison"
    )
    assert not re.search(r"provided\s*!=\s*expected", SERVER)


def test_http_app_is_stateless():
    """`docs/HOSTING.md` builds its single-replica argument on stateless HTTP:
    no session affinity is needed, so the ONLY thing tying a caller to a replica
    is the dismissal state. Dropping the flag left the suite green."""
    assert "stateless_http=True" in SERVER, (
        "stateless HTTP was disabled; docs/HOSTING.md's replica reasoning assumes it"
    )


def test_http_requests_are_labelled_with_a_non_default_caller_label():
    """The audit record's `caller_key_label` is the control HOSTING.md describes
    for telling apart who dismissed what. In HTTP mode it must not silently stay
    at the stdio default."""
    import envelope as env  # noqa: PLC0415

    assert env.caller_label.get() == "local", "the stdio default changed"
    m = re.search(r'caller_label\.set\((["\'])(.+?)\1\)', SERVER)
    assert m, "nothing sets a caller label in HTTP mode"
    assert m.group(2) != "local", (
        "HTTP requests are labelled 'local', so the audit trail cannot distinguish them from stdio"
    )


def test_the_startup_retry_backoff_survives():
    """`warm_start`'s docstring makes the 60 s timer part of its contract: a
    caller already told "unavailable" must not re-hammer a broken credential."""
    import server  # noqa: PLC0415

    assert server._State().retry_seconds == 60.0
    assert "retry_seconds" in SERVER and "last_attempt" in SERVER


# --- the audit trail's filename is the one the documents name ----------------

def test_the_audit_file_is_the_one_the_documents_name():
    assert alerts.AUDIT_PATH.name == "dismissals.jsonl"
    assert alerts.AUDIT_PATH.parent.name == "audit"
    for doc in ("README.md", "docs/HOSTING.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert "audit/dismissals.jsonl" in text, f"{doc} names a different audit file"
