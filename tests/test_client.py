"""Client contract: Basic auth + JSON:API headers, every failure is
AuvikUnavailable (never an empty dict), cursor pagination follows links.next and
reports truncation, the local rate budget refuses BEFORE the network."""
from __future__ import annotations

import json

import pytest
import requests

import auvik_client as ac
from credentials import AuvikCredentials

CREDS = AuvikCredentials("svc@example.com", "k-1", "us3")


class FakeResp:
    def __init__(self, status=200, body=None, headers=None, raw=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = raw if raw is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Records requests; serves queued responses (or raises queued exceptions)."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _client(*responses, budget=None):
    s = FakeSession(*responses)
    c = ac.AuvikClient(CREDS, session=s, budget=budget, sleep=lambda s_: None)
    return c, s


def test_base_url_uses_region():
    c, _ = _client()
    assert c.base_url == "https://auvikapi.us3.my.auvik.com"


def test_get_sends_basic_auth_and_jsonapi_accept():
    c, s = _client(FakeResp(200, {"data": []}))
    c.get("/v1/tenants")
    method, url, kw = s.calls[0]
    assert (method, url) == ("GET", "https://auvikapi.us3.my.auvik.com/v1/tenants")
    assert kw["auth"] == ("svc@example.com", "k-1")
    assert kw["headers"]["Accept"] == "application/vnd.api+json"
    assert kw["timeout"] == 30


@pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
def test_http_errors_raise_unavailable_with_status(status):
    c, _ = _client(FakeResp(status, {"errors": [{"detail": "nope"}]}))
    with pytest.raises(ac.AuvikUnavailable) as ei:
        c.get("/v1/tenants")
    assert ei.value.status == status
    assert "nope" in ei.value.detail


def test_transport_error_raises_unavailable_status_zero():
    c, _ = _client(requests.ConnectionError("dns failed"))
    with pytest.raises(ac.AuvikUnavailable) as ei:
        c.get("/v1/tenants")
    assert ei.value.status == 0
    assert "dns failed" in ei.value.detail


def test_non_json_200_raises_unavailable():
    c, _ = _client(FakeResp(200, None, raw="<html>login</html>"))
    with pytest.raises(ac.AuvikUnavailable) as ei:
        c.get("/v1/tenants")
    assert "non-JSON" in ei.value.detail


def test_429_retries_once_honoring_retry_after_then_succeeds():
    c, s = _client(FakeResp(429, {"errors": []}, headers={"Retry-After": "2"}), FakeResp(200, {"data": [1]}))
    assert c.get("/v1/tenants") == {"data": [1]}
    assert len(s.calls) == 2


def test_429_twice_raises():
    c, _ = _client(FakeResp(429, {"errors": []}), FakeResp(429, {"errors": []}))
    with pytest.raises(ac.AuvikUnavailable) as ei:
        c.get("/v1/tenants")
    assert ei.value.status == 429


def test_get_all_follows_next_and_stops_at_max_items():
    page1 = {"data": [{"id": "1"}, {"id": "2"}], "links": {"next": "https://auvikapi.us3.my.auvik.com/v1/x?page[after]=c1"}}
    page2 = {"data": [{"id": "3"}, {"id": "4"}], "links": {"next": "https://auvikapi.us3.my.auvik.com/v1/x?page[after]=c2"}}
    c, s = _client(FakeResp(200, page1), FakeResp(200, page2))
    items, truncated = c.get_all("/v1/x", {"tenants": "t1"}, page_size=2, max_items=3)
    assert [i["id"] for i in items] == ["1", "2", "3"]
    assert truncated is True
    # first call carries params + page[first]; second call hits links.next verbatim with no params
    assert s.calls[0][2]["params"] == {"tenants": "t1", "page[first]": 2}
    assert s.calls[1][1].endswith("page[after]=c1") and s.calls[1][2].get("params") in (None, {})


def test_get_all_complete_when_no_next():
    c, _ = _client(FakeResp(200, {"data": [{"id": "1"}], "links": {}}))
    items, truncated = c.get_all("/v1/x")
    assert [i["id"] for i in items] == ["1"] and truncated is False


def test_rate_budget_refuses_before_network():
    clock = {"t": 1000.0}
    b = ac.RateBudget(max_requests=2, window_seconds=300, clock=lambda: clock["t"])
    c, s = _client(FakeResp(200, {"data": []}), FakeResp(200, {"data": []}), FakeResp(200, {"data": []}), budget=b)
    c.get("/v1/a"); c.get("/v1/b")
    with pytest.raises(ac.AuvikUnavailable) as ei:
        c.get("/v1/c")
    assert ei.value.status == 429 and "local rate budget" in ei.value.detail
    assert len(s.calls) == 2  # third never hit the network
    clock["t"] += 301
    c.get("/v1/c")  # window slid; allowed again
    assert len(s.calls) == 3


def test_post_uses_post_verb():
    c, s = _client(FakeResp(200, {}))
    c.post("/v1/alert/dismiss/abc")
    assert s.calls[0][0] == "POST"


def test_tenants_returns_ids():
    c, _ = _client(FakeResp(200, {"data": [{"id": "111", "type": "tenant"}, {"id": "222", "type": "tenant"}]}))
    assert c.tenants() == ["111", "222"]


def test_get_all_wraps_a_single_object_data_in_a_list():
    """Deviation D-1: Auvik's OpenAPI declares some paginated endpoints' `data`
    as an OBJECT. `list(dict)` would silently yield the dict's KEYS — an
    empty-but-successful answer masking real data, the exact failure mode this
    server exists to prevent. Wrap the object instead."""
    c, _ = _client(FakeResp(200, {"data": {"id": "1", "type": "deviceStatistics"}, "links": {}}))
    items, truncated = c.get_all("/v1/x")
    assert items == [{"id": "1", "type": "deviceStatistics"}] and truncated is False


def test_get_all_wraps_object_data_on_followed_pages_too():
    page1 = {"data": {"id": "1"}, "links": {"next": "https://auvikapi.us3.my.auvik.com/v1/x?page[after]=c1"}}
    page2 = {"data": {"id": "2"}, "links": {}}
    c, _ = _client(FakeResp(200, page1), FakeResp(200, page2))
    items, truncated = c.get_all("/v1/x")
    assert [i["id"] for i in items] == ["1", "2"] and truncated is False


def test_empty_body_at_2xx_is_success_not_a_parse_failure():
    """Auvik's OpenAPI document declares /v1/authentication/verify's 200 as
    `content: {}`, and POST /v1/alert/dismiss/{id}'s 200 with no `content` key
    at all — neither promises a body.
    Treating that as a parse failure made verify() report 'unavailable' against
    a credential Auvik had just confirmed valid (found on the first live run),
    and would have made a SUCCESSFUL dismissal report unavailable
    with no audit line written: the write happens, the record does not."""
    c, _ = _client(FakeResp(200, None, raw=""))
    assert c.get("/v1/authentication/verify") == {}


def test_empty_body_post_is_success():
    c, _ = _client(FakeResp(200, None, raw=""))
    assert c.post("/v1/alert/dismiss/abc") == {}


def test_whitespace_only_body_counts_as_empty():
    c, _ = _client(FakeResp(204, None, raw="  \n "))
    assert c.get("/v1/x") == {}


def test_non_empty_non_json_2xx_still_raises():
    """The guard that catches an HTML login/error page served at 200 must
    survive the empty-body allowance — that is a real failure, not no-content."""
    c, _ = _client(FakeResp(200, None, raw="<html>login</html>"))
    with pytest.raises(ac.AuvikUnavailable) as ei:
        c.get("/v1/tenants")
    assert "non-JSON" in ei.value.detail
