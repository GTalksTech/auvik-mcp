"""The whole-surface re-verification script must itself be verified.

`scripts/live_smoke.py` covers only a few tools. The exercise that actually
caught D-4 (three tools dead on arrival because the live API requires a
parameter the OpenAPI file marks optional) lived in a scratch file and was lost,
so nothing in the repo could re-check the whole surface after an Auvik change.

`scripts/live_read_all.py` is that script. Two properties matter and both are
tested here rather than trusted:

* **it cannot write.** No `.post(`, no call into `dismiss_alert`. The same grep
  discipline as the single-write-site test, applied to a script that will be run
  against production by someone who is not reading it first.
* **it actually covers everything.** A re-verification script that silently
  skips a tool is worse than none: it certifies a surface it did not test. The
  coverage assertion is derived from the server's own registered tool list, so
  adding a tool without adding it here fails the suite.

The runner is written to take an injected client, so this file drives the whole
script offline against the committed fixtures — the script is exercised, not
merely linted.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from auvik_client import AuvikUnavailable
from conftest import ALL_TOOLS, FakeClient, default_routes, load

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "live_read_all.py"
sys.path.insert(0, str(ROOT / "scripts"))


def _routes():
    routes = default_routes()
    routes["/v1/stat/interface/"] = load("stat_interface_mixed.json")
    return routes


# ------------------------------------------------------------------ it cannot write

def test_the_script_has_no_write_call_site():
    body = SCRIPT.read_text(encoding="utf-8")
    code = [ln for ln in body.splitlines() if not ln.strip().startswith("#")]
    joined = "\n".join(code)
    assert not re.search(r"\.post\(", joined), "live_read_all.py can issue a POST"
    assert not re.search(r"dismiss_alert\s*\(", joined), "live_read_all.py calls the write tool"


def test_the_script_is_covered_by_the_single_write_site_gate():
    """The single-write-site test scans server.py and tools/. Scripts run against
    production too."""
    from test_single_write_site import SCAN  # noqa: PLC0415

    assert SCRIPT in SCAN, "scripts/ is outside the single-write-site scan"


# ------------------------------------------------------------------ it covers everything

def test_the_runner_covers_every_read_tool_the_server_registers():
    import live_read_all as R  # noqa: PLC0415

    expected = {t for t in ALL_TOOLS if t != "auvik_dismiss_alert"}
    assert set(R.READ_TOOLS) == expected, (
        f"coverage drift: {set(R.READ_TOOLS) ^ expected}. A re-verification script that skips a "
        "tool certifies a surface it did not test."
    )


def test_the_runner_never_lists_the_write_tool():
    import live_read_all as R  # noqa: PLC0415

    assert "auvik_dismiss_alert" not in R.READ_TOOLS


# ------------------------------------------------------------------ it actually runs

def test_run_all_exercises_every_read_tool_against_fixtures():
    import live_read_all as R  # noqa: PLC0415

    results = R.run_all(FakeClient(_routes()), ["T1"], out=lambda *_: None)
    assert [r["tool"] for r in results] == list(R.READ_TOOLS)
    bad = [r for r in results if r["data_status"] != "ok"]
    assert not bad, f"tools did not return ok against committed fixtures: {bad}"


def test_run_all_reports_an_upstream_failure_as_unavailable_not_as_success():
    """The whole point of this server: a failure must never read as an empty
    but successful answer."""
    import live_read_all as R  # noqa: PLC0415

    down = FakeClient({}, fail=AuvikUnavailable(503, "simulated outage"))
    results = R.run_all(down, ["T1"], out=lambda *_: None)
    assert results, "the runner swallowed the outage and reported nothing"
    assert all(r["data_status"] == "unavailable" for r in results), (
        f"an outage was reported as something other than unavailable: "
        f"{ {r['tool']: r['data_status'] for r in results} }"
    )
    assert all("503" in r["detail"] for r in results), "the HTTP status is not surfaced in detail"


def test_main_exit_code_is_nonzero_when_any_tool_fails():
    import live_read_all as R  # noqa: PLC0415

    down = FakeClient({}, fail=AuvikUnavailable(503, "simulated outage"))
    assert R.report(R.run_all(down, ["T1"], out=lambda *_: None), out=lambda *_: None) != 0
    assert R.report(R.run_all(FakeClient(_routes()), ["T1"], out=lambda *_: None), out=lambda *_: None) == 0


def test_the_summary_never_prints_an_empty_where_the_tool_returned_data():
    """Caught on the first live run: the summary printed `by_status={}` for a
    device that had plenty of interfaces, because it read `counts_by_status` while the tool
    returns `counts_by_oper_status` — and `.get(key, {})` turned the typo into a
    confident zero. That is the exact failure this whole server exists to
    prevent, committed in the script meant to verify it."""
    import live_read_all as R  # noqa: PLC0415
    from tools import interfaces  # noqa: PLC0415

    res = interfaces.list_interfaces(FakeClient(_routes()), ["T1"], "DEV-1")
    assert res["counts_by_oper_status"], "fixture no longer exercises the counts"
    line = R._summary("auvik_list_interfaces", res)
    for status, n in res["counts_by_oper_status"].items():
        assert status in line and str(n) in line, (
            f"summary {line!r} does not report {status}={n}"
        )


def test_the_summary_raises_rather_than_defaulting_on_a_missing_key():
    """A probe that cannot describe a response must fail loudly, not print a
    plausible blank. No `.get(key, default)` on contract keys."""
    import live_read_all as R  # noqa: PLC0415

    with pytest.raises(KeyError):
        R._summary("auvik_list_interfaces", {"data_status": "ok"})
