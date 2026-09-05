"""The cold-start cost of the device cache.

Measured live: the first `auvik_list_alerts` in a fresh process blocked for a
long, plainly user-visible time while it walked the whole device inventory a page
at a time. Every alert projection resolves `device_id -> device_name` through
`DeviceCache`, and the cache filled lazily, on the critical path, on the single
most likely opening question.

Three changes fix it and each is pinned below:

* fetch the estate in pages of 1000 rather than the vendor default of 100 -
  verified live to return an identical result set in a small fraction of the
  requests and roughly half the wall time (`page[first]` has no documented
  maximum);
* fill once under concurrency, not once per waiting caller;
* prime the cache off the critical path at process start - without changing how
  a process with no credential behaves.

**What is deliberately NOT done, and must not be reintroduced:** resolving names
per alert via `GET /v1/inventory/device/info/{id}`. That call averages around
200 ms live, so a 50-alert page would spend ten seconds - worse than fetching
the whole estate. Per-tenant parallelism is out too: the root tenant alone
returns every device, so the slowest shard is the whole job.
"""
from __future__ import annotations

import json
import threading
import time

import auvik_client as ac
from credentials import AuvikCredentials
from tools import alerts as A
from tools import devices as D

CREDS = AuvikCredentials("svc@example.com", "k-1", "us1")


# --------------------------------------------------------------- HTTP recorder

class _Resp:
    def __init__(self, body):
        self.status_code, self._body, self.headers = 200, body, {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


class RecordingSession:
    """A real AuvikClient talks to this, so `calls` is a true HTTP request count."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, **kw):
        params = kw.get("params") or {}
        self.calls.append((method, url, dict(params)))
        return _Resp(self.handler(url, params))

    def paths(self) -> list[str]:
        return [u.split(".com", 1)[-1].split("?", 1)[0] for _, u, _ in self.calls]


def _estate(n: int) -> list[dict]:
    return [{"id": f"D{i}", "type": "device", "attributes": {"deviceName": f"sw-{i}"}} for i in range(n)]


def _alert_page(n_alerts: int, n_devices: int) -> dict:
    return {"data": [
        {"id": f"AL{i}", "type": "alert",
         "attributes": {"name": "Network Element Offline", "severity": "critical",
                        "status": "created", "detectedOn": "2026-08-29T00:00:00.000Z"},
         "relationships": {"entity": {"data": {"type": "device", "id": f"D{i % n_devices}"}}}}
        for i in range(n_alerts)
    ], "links": {}}


def _make(n_alerts=50, n_devices=40, estate=1200):
    """Serve one alert page and a paginated device estate; count every request."""

    def handler(url, params):
        if "/v1/alert/history/info" in url:
            return _alert_page(n_alerts, n_devices)
        if "/v1/inventory/device/info" in url:
            # Real Auvik links.next carries page[first] forward - verified live,
            # because the observed request count is only possible if page 2 kept
            # the size. A fake that reverts to the default on the next link
            # invents an extra request.
            q = dict(kv.split("=", 1) for kv in url.split("?", 1)[1].split("&")) if "?" in url else {}
            start = int(q.get("page[after]", 0))
            size = int(q.get("page[first]") or params.get("page[first]") or 100)
            chunk = _estate(estate)[start:start + size]
            nxt = start + size
            links = {"next": "https://auvikapi.us1.my.auvik.com/v1/inventory/device/info"
                             f"?page[first]={size}&page[after]={nxt}"} if nxt < estate else {}
            return {"data": chunk, "links": links}
        raise AssertionError(f"unexpected request {url}")

    s = RecordingSession(handler)
    return ac.AuvikClient(CREDS, session=s, sleep=lambda _: None), s


# ------------------------------------------------------------ page size / count

def test_device_cache_fetches_the_estate_in_pages_of_1000():
    client, s = _make()
    D.DeviceCache(client, ["T1"]).all()
    sizes = [p.get("page[first]") for _, u, p in s.calls if "/v1/inventory/device/info" in u and p]
    assert sizes and sizes[0] == 1000, (
        f"estate fetch still pages at {sizes[0]!r}; live measurement showed the vendor default of "
        "100 costs many times the requests and roughly twice the wall time for an identical result set"
    )


def test_cold_alert_page_spends_pages_not_one_request_per_alert():
    """The acceptance test: pin the request count."""
    client, s = _make(n_alerts=50, n_devices=40, estate=1200)
    cache = D.DeviceCache(client, ["T1"])
    res = A.list_alerts(client, ["T1"], cache.name_of, hours=24, limit=50)

    per_device = [p for p in s.paths() if p.startswith("/v1/inventory/device/info/")]
    assert per_device == [], (
        f"{len(per_device)} single-device GETs - at roughly 200 ms each live, that is the "
        "ten-second path this file exists to reject; names must come from the estate fetch"
    )
    assert len(s.calls) == 3, (
        f"expected 3 requests (1 alert page + 2 device pages of 1000 over a 1200-device "
        f"estate), spent {len(s.calls)}: {s.paths()}"
    )
    assert res["data_status"] == "ok"
    assert all(a["device_name"] for a in res["alerts"]), "device names must still resolve"


def test_request_count_does_not_grow_with_the_alert_page_size():
    small, s_small = _make(n_alerts=5, n_devices=5, estate=1200)
    D.DeviceCache(small, ["T1"]).name_of("D0")
    big, s_big = _make(n_alerts=200, n_devices=150, estate=1200)
    cache = D.DeviceCache(big, ["T1"])
    A.list_alerts(big, ["T1"], cache.name_of, hours=24, limit=200)
    assert len(s_big.calls) == len(s_small.calls) + 1, (
        "a 200-alert page must cost the same estate fetch as a 5-alert one, plus its own page"
    )


# ------------------------------------------------------------------- concurrency

def test_concurrent_first_calls_fill_the_cache_exactly_once():
    """Eight tools asking for a name at once must not launch eight estate fetches."""
    fetches = []

    class SlowClient:
        def get_all(self, path, params=None, page_size=100, max_items=1000):
            fetches.append(path)
            time.sleep(0.05)
            return _estate(3), False

    cache = D.DeviceCache(SlowClient(), ["T1"])
    threads = [threading.Thread(target=cache.name_of, args=("D1",)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(fetches) == 1, f"{len(fetches)} concurrent estate fetches; expected single-flight"


# --------------------------------------------------------------------------- TTL

def test_device_cache_ttl_is_thirty_minutes():
    assert D.DEVICE_CACHE_TTL == 1800, "the device list is near-static; a 5-minute TTL re-pays the fetch 12x/hour"
    client, _ = _make()
    assert D.DeviceCache(client, ["T1"])._cache.ttl == 1800


# ------------------------------------------------------------------ warm at start

def test_warm_start_primes_the_cache_off_the_critical_path(srv):
    server, fake = srv
    server.warm_start()
    before = len([c for c in fake.calls if c[1] == "/v1/inventory/device/info"])
    assert before == 1, "warm_start did not fetch the estate"
    server.get_device_cache().name_of("DEV-1")
    after = len([c for c in fake.calls if c[1] == "/v1/inventory/device/info"])
    assert after == 1, "the first real lookup re-fetched; the warm did not land in the shared cache"


def test_warm_start_without_a_credential_leaves_the_process_retryable(monkeypatch):
    """A cold stdio process starts without a credential. The warm must
    not turn that into a cached 'startup failed' that suppresses the retry the
    first real tool call would otherwise make."""
    import server

    def _boom():
        raise RuntimeError("no credential")

    server.reset_state_for_tests(None, [])
    monkeypatch.setattr(server, "load_credentials", _boom)
    server.warm_start()  # must not raise
    assert server._state.error == "", "a failed warm poisoned the lazy-start retry clock"
    assert server._state.last_attempt == 0.0
    server.reset_state_for_tests(None, [])


def test_background_warm_thread_is_a_daemon_and_never_blocks_shutdown(srv):
    server, _ = srv
    t = server.start_background_warm()
    t.join(timeout=5)
    assert t.daemon, "a non-daemon warm thread would hold the process open at exit"
    assert not t.is_alive()


def test_main_starts_the_background_warm_in_stdio_mode(monkeypatch):
    import server

    started = []
    monkeypatch.setattr(server, "start_background_warm", lambda: started.append(True))
    monkeypatch.setattr(server.mcp, "run", lambda **kw: None)
    server.main(["--stdio"])
    assert started == [True], "stdio entrypoint does not prime the cache; the first question pays for it"
