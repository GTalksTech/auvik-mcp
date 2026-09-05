from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import envelope as env
from auvik_client import AuvikUnavailable


def _capture():
    buf = io.StringIO()
    env.set_log_stream(buf)
    return buf


def test_ok_unavailable_refused_shapes():
    assert env.ok(alerts=[]) == {"data_status": "ok", "detail": "", "alerts": []}
    assert env.unavailable("HTTP 503: x") == {"data_status": "unavailable", "detail": "HTTP 503: x"}
    assert env.refused("reason is required") == {"data_status": "refused", "detail": "reason is required"}


def test_envelope_maps_auvik_unavailable_and_logs():
    buf = _capture()

    @env.envelope
    def boom(a: str = ""):
        raise AuvikUnavailable(503, "upstream down")

    out = boom(a="x")
    assert out == {"data_status": "unavailable", "detail": "HTTP 503: upstream down"}
    line = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert set(line) == {"ts", "app", "tool", "args_summary", "caller_key_label", "duration_ms", "ok"}
    assert line["app"] == "auvik-mcp" and line["tool"] == "boom" and line["ok"] is False
    assert line["args_summary"] == "a=x"


def test_envelope_maps_value_error_to_refused():
    _capture()

    @env.envelope
    def bad(stat: str = ""):
        raise ValueError("invalid stat 'x'; valid: a, b")

    assert bad(stat="x") == {"data_status": "refused", "detail": "invalid stat 'x'; valid: a, b"}


def test_envelope_maps_unknown_exception_to_unavailable():
    _capture()

    @env.envelope
    def crash():
        raise KeyError("attributes")

    out = crash()
    assert out["data_status"] == "unavailable" and "KeyError" in out["detail"]


def test_envelope_passes_ok_through_and_truncates_long_args_in_log():
    buf = _capture()

    @env.envelope
    def fine(reason: str = ""):
        return env.ok(n=1)

    assert fine(reason="r" * 200)["n"] == 1
    line = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert len(line["args_summary"]) <= 100


def test_ttl_cache_hits_within_ttl_and_refetches_after():
    clock = {"t": 0.0}
    c = env.TTLCache(ttl=300, clock=lambda: clock["t"])
    calls = []
    fetch = lambda: calls.append(1) or "v"  # noqa: E731
    assert c.get("k", fetch) == "v" and c.get("k", fetch) == "v" and len(calls) == 1
    clock["t"] = 301
    c.get("k", fetch)
    assert len(calls) == 2
    c.clear(); c.get("k", fetch)
    assert len(calls) == 3


def test_iso_ago_format():
    now = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    assert env.iso_ago(24, now) == "2026-08-28T12:00:00.000Z"
