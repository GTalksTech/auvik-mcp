"""Tenant/site scoping.

A real Auvik account is several tenants, but `project_device()` carried no
tenant at all and `find_device()` had no way to scope to one. A question like
"what's down at the Boston office?" could then only be answered by luck of
whatever the device naming convention happened to be.

Verified live, and the reason this is plumbing rather than new API surface:

* every device record carries `relationships.tenant.data.id` **and** an inline
  `attributes.domainPrefix` - no second call needed;
* devices report their **owning client** tenant, never the `multiClient` root,
  and every device carried a tenant relationship;
* where devices are named after the place they live in, they sit in that
  place's tenant too - so an explicit site filter answers the question a naming
  convention was only ever standing in for, and keeps working where the
  convention was not followed;
* `domainPrefix` is the only site name Auvik reliably offers - the richer
  `/v1/tenants/detail` answered HTTP 500 on the tenant this was built against.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import alerts as A
from tools import devices as D

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "synthetic"


def two_tenant_estate() -> dict:
    return json.loads((FIXTURES / "devices_two_tenants.json").read_text(encoding="utf-8"))


class _Estate:
    """Minimal client that serves one device page, so find_device sees two sites."""

    def __init__(self, body):
        self.body = body

    def get_all(self, path, params=None, page_size=100, max_items=1000):
        return list(self.body.get("data") or []), False


def _cache():
    return D.DeviceCache(_Estate(two_tenant_estate()), ["T1", "T2"])


# ------------------------------------------------------------------ projections

def test_device_projection_carries_tenant_id_and_site():
    row = D.project_device(two_tenant_estate()["data"][0])
    assert row["tenant_id"], "device projection has no tenant_id"
    assert row["site"], "device projection has no human-readable site"


def test_alert_projection_carries_tenant_id_and_site():
    from conftest import load  # noqa: PLC0415

    row = A.project_alert(load("alert_single.json")["data"], lambda _: "")
    assert row["tenant_id"] == "T1"
    assert row["site"] == "acme-hq"


def test_a_record_with_no_tenant_relationship_degrades_to_empty_strings():
    """Never None, never a fabricated name - the same rule that D-5 checks for entity."""
    bare = {"id": "DEV-X", "type": "device", "attributes": {"deviceName": "orphan"}}
    row = D.project_device(bare)
    assert row["tenant_id"] == "" and row["site"] == ""
    alert_bare = {"id": "AL-X", "type": "alert", "attributes": {"name": "n"}}
    arow = A.project_alert(alert_bare, lambda _: "")
    assert arow["tenant_id"] == "" and arow["site"] == ""


# ---------------------------------------------------------------------- filter

def test_find_device_filters_by_site():
    res = D.find_device(_cache(), site="acme-bos")
    assert res["data_status"] == "ok"
    assert res["total_count"] >= 1
    assert {d["site"] for d in res["devices"]} == {"acme-bos"}
    everything = D.find_device(_cache())
    assert res["total_count"] < everything["total_count"], "the site filter did not narrow anything"


def test_find_device_site_match_is_a_case_insensitive_substring():
    assert D.find_device(_cache(), site="BOS")["total_count"] == D.find_device(_cache(), site="acme-bos")["total_count"]


def test_find_device_site_combines_with_the_other_filters():
    res = D.find_device(_cache(), site="acme-bos", device_type="switch")
    assert all(d["site"] == "acme-bos" and d["type"] == "switch" for d in res["devices"])


# -------------------------------------------------------- discoverability of sites

def test_find_device_reports_the_sites_present_in_the_estate():
    """An agent cannot filter by a site whose name it has never seen. domainPrefix
    is not guessable from a human place name (Boston -> acme-bos)."""
    res = D.find_device(_cache())
    assert res["sites"] == sorted({d["site"] for d in _cache().all() if d["site"]})
    assert len(res["sites"]) == 2


def test_an_unmatched_site_is_a_real_zero_that_names_the_sites_that_exist():
    res = D.find_device(_cache(), site="boston")
    assert res["data_status"] == "ok", "no match is a real zero, not unavailable"
    assert res["total_count"] == 0
    assert "acme-bos" in res["detail"], (
        "a zero from an unmatched site must say which sites exist, or the agent "
        "cannot tell a wrong site name from an empty site"
    )


# ------------------------------------------------------------------- MCP surface

async def test_site_filter_works_through_the_mcp_layer(srv, calltool, monkeypatch):
    server, fake = srv
    fake.routes["/v1/inventory/device/info"] = two_tenant_estate()
    server.get_device_cache().clear()
    out = await calltool("auvik_find_device", {"site": "acme-bos"})
    assert out["data_status"] == "ok"
    assert out["total_count"] >= 1
    assert {d["site"] for d in out["devices"]} == {"acme-bos"}


async def test_alerts_through_the_mcp_layer_carry_the_site(srv, calltool):
    out = await calltool("auvik_list_alerts", {"hours": 0})
    assert out["data_status"] == "ok"
    assert all("site" in a and "tenant_id" in a for a in out["alerts"])


# ------------------------------------------------------------------- the fixture

def test_the_committed_estate_fixture_actually_has_two_tenants():
    """A single-tenant fixture is why this gap survived to production: every
    projection test passed while carrying no tenant at all."""
    sites = {(((d.get("relationships") or {}).get("tenant") or {}).get("data") or {})
             .get("attributes", {}).get("domainPrefix") for d in two_tenant_estate()["data"]}
    assert len(sites) == 2, f"fixture covers {sites}; the filter cannot be proven to filter"
