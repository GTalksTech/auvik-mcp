"""Shared test plumbing. All tests run offline: the HTTP client is replaced by
FakeClient serving fixtures by path. Default fixture tier is synthetic
(committed); set AUVIK_FIXTURES=recorded to run the same suite against
gitignored real recordings (tests/record_fixtures.py)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auvik_client import AuvikUnavailable  # noqa: E402

FIXTURES = HERE / "fixtures" / os.environ.get("AUVIK_FIXTURES", "synthetic")


def load(name: str) -> dict:
    path = FIXTURES / name
    if not path.exists():
        raise RuntimeError(f"Missing fixture {path}. Synthetic: run tests/build_synthetic_fixtures.py. "
                           "Recorded: run tests/record_fixtures.py (needs a live credential).")
    return json.loads(path.read_text(encoding="utf-8"))


class FakeClient:
    """Serves fixture bodies by exact path (or path prefix ending in '/'). Records every call.
    POST raises unless post_response is set — so an unexpected write fails loudly."""

    def __init__(self, routes: dict, post_response=None, fail: AuvikUnavailable | None = None):
        self.routes, self.post_response, self.fail = routes, post_response, fail
        self.calls: list[tuple[str, str, dict]] = []

    def _route(self, path: str):
        if path in self.routes:
            return self.routes[path]
        for k, v in self.routes.items():
            if k.endswith("/") and path.startswith(k):
                return v
        raise AuvikUnavailable(404, f"FakeClient: no route for {path}")

    def get(self, path, params=None):
        self.calls.append(("GET", path, dict(params or {})))
        if self.fail:
            raise self.fail
        body = self._route(path)
        return body(params or {}) if callable(body) else body

    def get_all(self, path, params=None, page_size=100, max_items=1000):
        self.calls.append(("GET_ALL", path, dict(params or {})))
        if self.fail:
            raise self.fail
        body = self._route(path)
        body = body(params or {}) if callable(body) else body
        items = list(body.get("data") or [])
        # a fixture may carry "_next": "<fixture name>" to simulate a second page
        while body.get("_next"):
            body = load(body["_next"])
            items.extend(body.get("data") or [])
        return items[:max_items], len(items) > max_items

    def post(self, path):
        self.calls.append(("POST", path, {}))
        if self.fail:
            raise self.fail
        if self.post_response is None:
            raise AssertionError(f"unexpected POST {path} — tests must opt in via post_response")
        return self.post_response

    def tenants(self):
        return list(self.tenant_map())

    def tenant_map(self):
        body = self.routes.get("/v1/tenants") or load("tenants.json")
        self.calls.append(("GET", "/v1/tenants", {}))
        return {t["id"]: (t.get("attributes") or {}).get("domainPrefix") or "" for t in body["data"]}


def _filter_components(body: dict, params: dict) -> dict:
    status = params.get("filter[currentStatus]")
    if not status:
        return body
    return {**body, "data": [i for i in body["data"] if (i.get("attributes") or {}).get("currentStatus") == status]}


def _filter_by_device(body: dict, params: dict, key: str) -> dict:
    did = params.get(key)
    if not did:
        return body
    return {**body, "data": [i for i in body["data"]
                             if ((((i.get("relationships") or {}).get("device") or {}).get("data") or {}).get("id")) == did]}


def default_routes() -> dict:
    alerts = dict(load("alerts_page1.json"))
    alerts["_next"] = "alerts_page2.json"
    return {
        "/v1/tenants": load("tenants.json"),
        "/v1/authentication/verify": {"data": {"ok": True}},
        "/v1/alert/history/info": alerts,
        "/v1/alert/history/info/AL-1": load("alert_single.json"),
        "/v1/alert/history/info/AL-DISMISSED": load("alert_single_dismissed.json"),
        "/v1/inventory/device/info": load("devices.json"),
        "/v1/inventory/device/info/DEV-1": load("device_single.json"),
        "/v1/inventory/device/detail/DEV-1": load("device_detail_single.json"),
        "/v1/inventory/interface/info": load("interfaces.json"),
        "/v1/stat/interface/": load("stat_interface.json"),
        "/v1/stat/device/": load("stat_device.json"),
        "/v1/stat/deviceAvailability/": load("stat_availability.json"),
        "/v1/inventory/network/info": load("networks.json"),
        # Callable: the tool makes one call per status (degraded, failed), and a
        # static body would return the same four components twice.
        "/v1/inventory/component/info": lambda params: _filter_components(load("components.json"), params),
        "/v1/inventory/configuration": lambda params: _filter_by_device(load("configurations.json"), params, "filter[deviceId]"),
        "/v1/inventory/entity/audit": load("audits.json"),
        "/v1/inventory/device/lifecycle": load("lifecycle.json"),
        "/v1/inventory/device/lifecycle/DEV-2": load("lifecycle_single.json"),
        "/v1/inventory/device/warranty": load("warranty.json"),
        "/v1/inventory/device/warranty/DEV-2": load("warranty_single.json"),
        "/v1/inventory/interface/info/IF-1": load("interface_single.json"),
    }


def _wire(monkeypatch, fake, tmp_path):
    import server
    from tools import alerts

    server.reset_state_for_tests(fake, ["T1"])
    alerts.SEEN_ALERT_IDS.clear()
    # Never let a test append to the repo's real audit/dismissals.jsonl: tests
    # that drive auvik_dismiss_alert through the MCP layer pass no audit_path,
    # and a fabricated line there is indistinguishable from a real one.
    monkeypatch.setattr(alerts, "AUDIT_PATH", tmp_path / "dismissals.jsonl")
    return server


@pytest.fixture(autouse=True)
def _offline_guard(monkeypatch):
    """The offline suite must never load a real credential or open a socket.

    Found as a roughly 1-in-6 flake in test_unavailable::test_empty_upstream_is_ok_zero:
    two transport tests call server.main(), which starts the background warm
    thread and the mocked mcp.run returns at once, so the thread outlives its
    test. On a machine that HAS a credential in its OS keyring, that thread
    loaded it, built a REAL AuvikClient, fetched live tenants and devices, and
    assigned them into whatever `_state` object was current - sometimes the NEXT
    test's FakeClient state. That test then asked the live API and got real
    alerts where it expected a fixture zero.

    Two rules were being broken at once: the suite was nondeterministic, and it
    reached the live API, which no test may ever do. This fixture makes both
    impossible rather than unlikely: server.load_credentials
    raises, and requests.Session.request raises. Tests that need a failing
    credential path patch server.load_credentials themselves and still can.
    The keyring-backed credentials module itself is tested by test_credentials
    against an injected keyring module, never the real one.
    """
    import requests

    import server
    from credentials import CredentialsMissing

    def _no_creds():
        raise CredentialsMissing("offline suite: real credentials are never loaded (tests/conftest.py::_offline_guard)")

    def _no_network(self, method, url, *a, **kw):
        raise AssertionError(f"offline suite reached the network: {method} {url} (tests/conftest.py::_offline_guard)")

    monkeypatch.setattr(server, "load_credentials", _no_creds)
    monkeypatch.setattr(requests.Session, "request", _no_network)
    yield


@pytest.fixture(autouse=True)
def _isolate_process_scoped_alert_state():
    """tools.alerts keeps two module-level sets — SEEN_ALERT_IDS (guardrail 2)
    and DISMISSED_ALERT_IDS (the eventual-consistency guard). Module state
    survives between tests, so without this a test that dismisses AL-1 makes
    every later test see it as already dismissed. Clear both around every test
    rather than relying on each one to remember."""
    from tools import alerts

    alerts.SEEN_ALERT_IDS.clear()
    alerts.DISMISSED_ALERT_IDS.clear()
    yield
    alerts.SEEN_ALERT_IDS.clear()
    alerts.DISMISSED_ALERT_IDS.clear()


@pytest.fixture()
def fake():
    return FakeClient(default_routes())


@pytest.fixture()
def srv(monkeypatch, fake, tmp_path):
    server = _wire(monkeypatch, fake, tmp_path)
    yield server, fake
    server.reset_state_for_tests(None, [])


@pytest.fixture()
def srv_down(monkeypatch, tmp_path):
    fake = FakeClient({}, fail=AuvikUnavailable(503, "Auvik upstream unreachable (simulated)"))
    server = _wire(monkeypatch, fake, tmp_path)
    yield server
    server.reset_state_for_tests(None, [])


def payload_of(res):
    sc = getattr(res, "structured_content", None)
    if sc is None:
        return json.loads(res.content[0].text)
    if isinstance(sc, dict) and set(sc) == {"result"}:
        return sc["result"]
    return sc


@pytest.fixture()
def calltool():
    async def _call(tool: str, args: dict | None = None):
        import server
        from fastmcp import Client

        async with Client(server.mcp) as client:
            res = await client.call_tool(tool, args or {})
        return payload_of(res)

    return _call


ALL_TOOLS = [
    "auvik_list_alerts", "auvik_get_alert", "auvik_dismiss_alert", "auvik_alert_summary",
    "auvik_find_device", "auvik_get_device", "auvik_list_interfaces", "auvik_list_networks",
    "auvik_component_health", "auvik_config_changes", "auvik_remote_access_audit",
    "auvik_device_lifecycle",
    "auvik_interface_stats", "auvik_device_stats", "auvik_device_availability",
]

REQUIRED_ARGS = {
    "auvik_get_alert": {"alert_id": "AL-1"},
    "auvik_dismiss_alert": {"alert_id": "AL-1", "reason": "test"},
    "auvik_get_device": {"device_id": "DEV-1"},
    "auvik_list_interfaces": {"device_id": "DEV-1"},
    "auvik_interface_stats": {"stat": "utilization", "device_id": "DEV-1"},
    "auvik_device_stats": {"stat": "cpuUtilization", "device_id": "DEV-1"},
    "auvik_device_availability": {"stat": "uptime", "device_id": "DEV-1"},
}
