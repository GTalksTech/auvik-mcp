"""Interface stats return a majority of empty series.

Measured live against a 17-port access switch:

    hours= 6 interval=hour  17 series, row_count per series:
        [0, 0, 0, 6, 6, 6, 0, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    hours=24 interval=hour  17 series, row_count per series:
        [0, 0, 0, 24, 24, 24, 0, 24, 0, 0, 0, 0, 0, 0, 0, 0, 0]

**13 of 17 empty** - and the ratio held across window sizes, so it tracks port
count rather than the size of the question being asked.

Those zeros are REAL: those interfaces genuinely have no utilisation data, and
reporting that honestly is the whole point of this server. So the default must
never drop them. What the agent gets instead is the split stated up front
(`series_total` / `series_with_data` / `series_empty`) and an opt-in
`include_empty=False` that says in `detail` exactly how many it left out.

The rule this file exists to enforce: **an omitted-empty response must still
report the full total.** A response that quietly shrinks is the failure mode
this whole server was built to prevent.
"""
from __future__ import annotations

import pytest

from conftest import FakeClient, default_routes, load
from tools import stats as S

# the live-observed pattern, as committed in stat_interface_mixed.json
LIVE_PATTERN = [0, 0, 0, 6, 6, 6, 0, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0]
N_TOTAL = len(LIVE_PATTERN)
N_WITH_DATA = sum(1 for r in LIVE_PATTERN if r)
N_EMPTY = N_TOTAL - N_WITH_DATA


def _client():
    routes = default_routes()
    routes["/v1/stat/interface/"] = load("stat_interface_mixed.json")
    return FakeClient(routes)


def _run(**kw):
    return S.interface_stats(_client(), ["T1"], "utilization", device_id="DEV-1", **kw)


# ------------------------------------------------------------- always reported

def test_the_split_is_reported_without_being_asked_for():
    out = _run()
    assert out["series_total"] == N_TOTAL
    assert out["series_with_data"] == N_WITH_DATA
    assert out["series_empty"] == N_EMPTY


def test_a_majority_of_empty_series_is_still_data_status_ok():
    out = _run()
    assert out["data_status"] == "ok", "a real zero is not an outage"


def test_empty_series_are_kept_by_default():
    """Dropping them silently would hide a real zero - the one thing this
    server exists not to do. Opting out has to be the caller's choice."""
    out = _run()
    kept = [s for e in out["series_for"] for s in e["series"]]
    assert len(kept) == N_TOTAL
    assert sum(1 for s in kept if s["row_count"] == 0) == N_EMPTY


# ----------------------------------------------------------------- opting out

def test_include_empty_false_omits_them():
    out = _run(include_empty=False)
    kept = [s for e in out["series_for"] for s in e["series"]]
    assert len(kept) == N_WITH_DATA
    assert all(s["row_count"] > 0 for s in kept)


def test_include_empty_false_still_reports_the_full_total():
    out = _run(include_empty=False)
    assert out["series_total"] == N_TOTAL, "the total must survive the filtering, or the response lies"
    assert out["series_with_data"] == N_WITH_DATA
    assert out["series_empty"] == N_EMPTY


def test_include_empty_false_says_how_many_it_left_out():
    out = _run(include_empty=False)
    assert str(N_EMPTY) in out["detail"], f"detail does not state the omission: {out['detail']!r}"
    assert "include_empty" in out["detail"], "detail does not tell the caller how to see them"


def test_include_empty_false_drops_entities_that_have_nothing_left():
    out = _run(include_empty=False)
    assert out["entity_count_total"] == N_TOTAL
    assert out["entity_count"] == N_WITH_DATA
    assert all(e["series"] for e in out["series_for"]), "an entity with an empty series list is noise too"


def test_nothing_is_said_when_there_was_nothing_to_omit():
    """The committed single-series fixture is fully populated."""
    out = S.interface_stats(FakeClient(default_routes()), ["T1"], "utilization",
                            device_id="DEV-1", include_empty=False)
    assert out["series_empty"] == 0
    assert "omitted" not in out["detail"]


def test_the_clamp_note_is_not_lost_when_series_are_omitted():
    out = _run(hours=999, interval="hour", include_empty=False)
    assert "clamped" in out["detail"] and str(N_EMPTY) in out["detail"], (
        "the omission note overwrote the window-clamp note; both are the caller's business"
    )


# ------------------------------------------------------------------ MCP surface

async def test_include_empty_through_the_mcp_layer(srv, calltool):
    server, fake = srv
    fake.routes["/v1/stat/interface/"] = load("stat_interface_mixed.json")
    out = await calltool("auvik_interface_stats",
                         {"stat": "utilization", "device_id": "DEV-1", "include_empty": False})
    assert out["data_status"] == "ok"
    assert out["series_total"] == N_TOTAL and out["entity_count"] == N_WITH_DATA


# --------------------------------------------------------------- the fixture

def test_the_committed_fixture_reproduces_the_live_empty_ratio():
    body = load("stat_interface_mixed.json")
    counts = [len((s.get("data") or [])) for i in body["data"] for s in (i["attributes"]["stats"])]
    assert counts == LIVE_PATTERN, (
        f"fixture pattern {counts} no longer matches the live 17-interface observation"
    )
