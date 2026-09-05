"""The only HTTP layer. Basic auth, JSON:API, cursor pagination, local rate budget.

Contract: every failure raises AuvikUnavailable. This module never returns an
empty structure to disguise a failure — that is this server's founding rule.
Only one caller in the codebase may use .post(): tools.alerts.dismiss_alert.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable

import requests

from credentials import AuvikCredentials

ACCEPT = "application/vnd.api+json"


class AuvikUnavailable(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


class RateBudget:
    """Sliding-window request budget, deliberately under the published API limit.

    Auvik documents a per-IP rate limit in its API documentation (not in the
    OpenAPI file checked in here, which carries no rate-limit information at
    all). The defaults below sit well under the figure that applied when this
    was written; check Auvik's current documentation before raising them, and
    note the budget refuses locally BEFORE issuing a request rather than
    discovering the limit as an HTTP 429."""

    def __init__(self, max_requests: int = 2000, window_seconds: int = 300, clock: Callable[[], float] = time.monotonic):
        self.max, self.window, self.clock = max_requests, window_seconds, clock
        self._hits: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._hits and now - self._hits[0] >= self.window:
            self._hits.popleft()

    def remaining(self) -> int:
        self._prune(self.clock())
        return self.max - len(self._hits)

    def acquire(self) -> None:
        now = self.clock()
        self._prune(now)
        if len(self._hits) >= self.max:
            wait = int(self.window - (now - self._hits[0])) + 1
            raise AuvikUnavailable(429, f"local rate budget exhausted ({self.max}/{self.window}s); retry after {wait}s")
        self._hits.append(now)


def _data_items(body: dict) -> list[dict]:
    """JSON:API `data` as a list, whatever shape Auvik sends.

    Auvik's OpenAPI declares `data` on the paginated /v1/stat/* endpoints as an
    OBJECT while those same endpoints accept cursor-pagination params (deviation
    D-1 in docs/API-DEVIATIONS.md; live, it is a list). A bare
    list(body["data"]) on an object yields the dict's KEYS — an
    empty-but-successful answer hiding real data. Wrap instead.
    """
    data = body.get("data")
    if data is None:
        return []
    return list(data) if isinstance(data, list) else [data]


def _error_detail(resp) -> str:
    try:
        body = resp.json()
        errs = body.get("errors") or []
        if errs:
            return "; ".join(str(e.get("detail") or e.get("title") or e) for e in errs)[:300]
        return (resp.text or "")[:300]
    except Exception:  # noqa: BLE001
        return (getattr(resp, "text", "") or "")[:300]


class AuvikClient:
    def __init__(self, creds: AuvikCredentials, session=None, budget: RateBudget | None = None,
                 timeout: int = 30, sleep: Callable[[float], None] = time.sleep):
        self.creds = creds
        self.session = session or requests.Session()
        self.budget = budget or RateBudget()
        self.timeout = timeout
        self._sleep = sleep
        self.base_url = f"https://auvikapi.{creds.region}.my.auvik.com"

    # ------------------------------------------------------------ transport
    def _request(self, method: str, url: str, params: dict | None = None, _retried: bool = False) -> dict:
        self.budget.acquire()
        try:
            resp = self.session.request(
                method, url, params=params or None,
                auth=(self.creds.username, self.creds.api_key),
                headers={"Accept": ACCEPT}, timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AuvikUnavailable(0, f"{type(exc).__name__}: {exc}") from exc

        if resp.status_code == 429 and not _retried:
            try:
                wait = min(float(resp.headers.get("Retry-After", "1")), 10.0)
            except ValueError:
                wait = 1.0
            self._sleep(wait)
            return self._request(method, url, params, _retried=True)
        if resp.status_code >= 400:
            raise AuvikUnavailable(resp.status_code, _error_detail(resp))
        try:
            return resp.json()
        except ValueError as exc:
            body = (resp.text or "").strip()
            if not body:
                # Documented no-content success. GET /v1/authentication/verify
                # declares its 200 as `content: {}`, and POST /v1/alert/dismiss/{id}
                # declares a 200 with no `content` key at all — neither promises a
                # body, so an empty one IS the success signal, not a parse failure.
                # A non-empty body that will not parse (an HTML login page served
                # at 200) still raises.
                return {}
            raise AuvikUnavailable(resp.status_code, f"non-JSON body: {body[:120]!r}") from exc

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", self.base_url + path, params)

    def post(self, path: str) -> dict:
        return self._request("POST", self.base_url + path)

    # ------------------------------------------------------------ pagination
    def get_all(self, path: str, params: dict | None = None, page_size: int = 100, max_items: int = 1000) -> tuple[list[dict], bool]:
        """Follow JSON:API links.next. Returns (items, truncated)."""
        q = dict(params or {})
        q["page[first]"] = page_size
        body = self.get(path, q)
        items: list[dict] = _data_items(body)
        nxt = (body.get("links") or {}).get("next")
        while nxt and len(items) < max_items:
            body = self._request("GET", nxt)
            items.extend(_data_items(body))
            nxt = (body.get("links") or {}).get("next")
        truncated = bool(nxt) or len(items) > max_items
        return items[:max_items], truncated

    # ------------------------------------------------------------ helpers
    def verify(self) -> dict:
        return self.get("/v1/authentication/verify")

    def tenants(self) -> list[str]:
        return list(self.tenant_map())

    def tenant_map(self) -> dict[str, str]:
        """tenant id -> domainPrefix, from the one /v1/tenants call.

        The prefix is the only site name Auvik reliably offers: the richer
        `/v1/tenants/detail` answered HTTP 500 on the tenant this was built
        against. It is what `site` means on every projection and filter in this
        server.
        """
        return {t["id"]: (t.get("attributes") or {}).get("domainPrefix") or ""
                for t in self.get("/v1/tenants").get("data") or [] if t.get("id")}
