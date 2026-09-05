"""Push the site filter for alerts down to Auvik.

Alert projections carry a `site`, so an agent can group the rows it got back.
What it could not do was ask Auvik for one site's alerts: the endpoint takes
`tenants=<comma list>`, and the server used to send every tenant every time.

Verified live before building any of this (GET-only): filtering server-side
returns exactly the same rows as taking the matching subset of an all-tenant
page. Exact, so the rows are trusted rather than re-filtered client-side -
re-filtering would mask a future API change. The win is payload rather than
speed: a scoped query is a fraction of the tokens for a chat-facing agent, while
the latency difference is noise.

**The trap this file exists to pin:** the site list for alerts must come from
`/v1/tenants`, NOT from the device estate. A real tenant can hold alerts and
ZERO devices - so `auvik_find_device`'s `sites` list would not contain it.
Reusing that list here would refuse "what fired at that site?" as an unknown
site while its alerts sat there: a fabricated zero, produced by answering with
a list built for a different question.
"""
from __future__ import annotations

import pytest

from conftest import FakeClient, default_routes, load
from tools import alerts

resolve = lambda did: ""  # noqa: E731


def _sites() -> dict[str, str]:
    """id -> domainPrefix, exactly as /v1/tenants gives it."""
    return {t["id"]: (t.get("attributes") or {}).get("domainPrefix")
            for t in load("tenants_multi.json")["data"]}


def _client():
    routes = default_routes()
    routes["/v1/tenants"] = load("tenants_multi.json")
    return FakeClient(routes)


def _list(fake, **kw):
    return alerts.list_alerts(fake, list(_sites()), resolve, tenant_sites=_sites(), hours=0, **kw)


def _tenants_param(fake) -> str:
    call = next(c for c in fake.calls if c[1] == "/v1/alert/history/info")
    return call[2]["tenants"]


# ------------------------------------------------------------------ the filter

def test_a_site_narrows_the_tenants_param_to_that_tenant():
    """`acme-hq` is unique among the fixture's prefixes — `acme-bos` is not, and
    the substring case is pinned separately below."""
    fake = _client()
    _list(fake, site="acme-hq")
    sent = _tenants_param(fake).split(",")
    assert sent == [tid for tid, pre in _sites().items() if pre == "acme-hq"], (
        f"tenants param was {sent}; the point of the site filter is not sending all of them"
    )
    assert len(sent) == 1 and len(_sites()) == 5, "the filter did not narrow anything"


def test_no_site_still_asks_for_every_tenant():
    fake = _client()
    _list(fake)
    assert set(_tenants_param(fake).split(",")) == set(_sites())


def test_site_is_a_case_insensitive_substring_like_find_device():
    fake = _client()
    _list(fake, site="BOS-DR")
    assert _tenants_param(fake) == next(t for t, p in _sites().items() if p == "acme-bos-dr")


def test_a_substring_matching_two_tenants_sends_both():
    """`acme-bos` is a prefix of `acme-bos-dr`. Substring semantics match
    find_device, so both are asked for rather than one being silently dropped."""
    fake = _client()
    _list(fake, site="acme-bos")
    sent = set(_tenants_param(fake).split(","))
    expected = {tid for tid, pre in _sites().items() if "acme-bos" in pre}
    assert sent == expected and len(expected) == 2


# ------------------------------------------------------- discoverability / zeros

def test_every_response_lists_the_sites_that_exist():
    out = _list(_client())
    assert out["sites"] == sorted(_sites().values())


def test_the_site_list_includes_a_tenant_that_has_alerts_but_no_devices():
    """The acme-misc trap: it has alerts and no devices, so find_device's
    `sites` list - which is device-derived - would omit it. Alerts must not
    borrow that list."""
    out = _list(_client())
    assert "acme-misc" in out["sites"]

    from tools import devices  # noqa: PLC0415
    estate = devices.find_device(devices.DeviceCache(_client(), list(_sites())))
    assert "acme-misc" not in estate["sites"], (
        "fixture no longer reproduces the trap: the device estate now covers every tenant"
    )


def test_an_unmatched_site_is_a_real_zero_that_names_the_sites():
    fake = _client()
    out = _list(fake, site="boston")
    assert out["data_status"] == "ok", "no match is a real zero, not an outage"
    assert out["total_count"] == 0 and out["alerts"] == []
    assert "acme-bos" in out["detail"], f"detail does not name the sites that exist: {out['detail']!r}"


def test_an_unmatched_site_makes_no_api_call_at_all():
    """Nothing can match, so asking Auvik is pure waste."""
    fake = _client()
    _list(fake, site="boston")
    assert not [c for c in fake.calls if c[1] == "/v1/alert/history/info"]


def test_an_unmatched_site_still_reports_the_window_and_counts():
    out = _list(_client(), site="boston")
    for key in ("total_count", "returned_count", "truncated", "counts_by_severity", "window_hours", "sites"):
        assert key in out, f"the zero response dropped {key}; it must be shaped like any other"


# ------------------------------------------------------------------ MCP surface

async def test_site_filter_through_the_mcp_layer(srv, calltool):
    server, fake = srv
    fake.routes["/v1/tenants"] = load("tenants_multi.json")
    server.reset_state_for_tests(fake, list(_sites()), tenant_sites=_sites())
    out = await calltool("auvik_list_alerts", {"site": "acme-bos", "hours": 0})
    assert out["data_status"] == "ok"
    assert "acme-misc" in out["sites"]


async def test_unmatched_site_through_the_mcp_layer_is_ok_not_refused(srv, calltool):
    server, fake = srv
    fake.routes["/v1/tenants"] = load("tenants_multi.json")
    server.reset_state_for_tests(fake, list(_sites()), tenant_sites=_sites())
    out = await calltool("auvik_list_alerts", {"site": "nowhere", "hours": 0})
    assert out["data_status"] == "ok" and out["total_count"] == 0
    assert "acme-bos" in out["detail"]


# --------------------------------------------------------------- startup wiring

def test_the_tenant_map_carries_domain_prefixes_from_one_call():
    """`/v1/tenants` is called once at startup, and its inlined `domainPrefix`
    is what `site` means everywhere - so site filtering costs no extra request.

    This asserts the shape that makes that true: one call in, the full
    id -> prefix map out."""
    fake = _client()
    before = len(fake.calls)
    m = fake.tenant_map()
    assert m == _sites()
    assert len(fake.calls) - before <= 1


def test_the_committed_tenant_fixture_has_a_root_and_a_deviceless_tenant():
    data = load("tenants_multi.json")["data"]
    types = {(t.get("attributes") or {}).get("tenantType") for t in data}
    assert "multiClient" in types, "no root tenant; the live estate has one and it returns everything"
    assert len(data) >= 4


# ------------------------------------------------- not-computed vs really-empty

def test_sites_is_omitted_rather_than_empty_when_the_map_was_not_supplied():
    """`sites: []` claims this Auvik has no sites. The truth is that the caller
    did not supply the map — the same fabricated-empty defect that `.get(key, {})`
    produced in live_read_all's summary. An absent key says "not computed"; an
    empty list says "computed, and there are none". They are different answers."""
    out = alerts.list_alerts(FakeClient(default_routes()), ["T1"], resolve, hours=0)
    assert out["data_status"] == "ok"
    assert "sites" not in out, (
        f"sites reported as {out.get('sites')!r} without a tenant map to derive it from"
    )


def test_sites_is_present_and_empty_only_when_no_tenant_reports_one():
    """A tenant map that genuinely carries no prefixes IS a real empty."""
    out = alerts.list_alerts(FakeClient(default_routes()), ["T1"], resolve, hours=0,
                             tenant_sites={"T1": ""})
    assert out["sites"] == []


def test_the_live_read_all_probe_supplies_the_tenant_map():
    """The re-verification script must exercise the path the MCP tool uses, not
    a degraded one."""
    import sys as _sys  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "scripts"))
    import live_read_all as R  # noqa: PLC0415

    results = R.run_all(_client(), list(_sites()), out=lambda *_: None)
    assert all(r["data_status"] == "ok" for r in results)
    src = (_Path(__file__).resolve().parent.parent / "scripts" / "live_read_all.py").read_text(encoding="utf-8")
    assert "tenant_sites" in src, "the probe calls list_alerts without the tenant map"
