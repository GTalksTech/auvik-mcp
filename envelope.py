"""Uniform tool envelope, structured logging, TTL cache, time helpers.

Envelope: {"data_status": "ok"|"unavailable"|"refused", "detail": str, ...}.
- ok          real data (zero rows is a real zero).
- unavailable upstream/auth/rate/parse failure; NO data keys.
- refused     the server declined to call Auvik (guardrail or invalid input).
One log line per tool call, on stdout in HTTP mode and stderr in stdio mode
(stdout is the MCP channel there).
"""
from __future__ import annotations

import contextvars
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable

from auvik_client import AuvikUnavailable

APP_NAME = "auvik-mcp"
caller_label: contextvars.ContextVar[str] = contextvars.ContextVar("caller_label", default="local")
_log_stream = sys.stdout


def set_log_stream(stream) -> None:
    global _log_stream
    _log_stream = stream


def log_call(tool: str, args_summary: str, t0: float, ok: bool) -> None:
    print(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "app": APP_NAME,
        "tool": tool,
        "args_summary": args_summary,
        "caller_key_label": caller_label.get(),
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
        "ok": ok,
    }), file=_log_stream, flush=True)


def log_audit(record: dict) -> None:
    """Emit a dismissal audit record on the log stream, beside the JSONL file.

    `audit/dismissals.jsonl` is container-local disk. In a container that is
    ephemeral — a restart or a new revision and the only record of why a write
    happened is gone — and a read-only root filesystem, a common hardening
    default, stops the append from ever succeeding. stdout/stderr is
    captured by the host and cannot fail that way, so the record goes to both.
    Two copies, one of which survives the container.

    The record carries no secret: `caller_key_label` is a label, never the key.
    """
    print(json.dumps({"app": APP_NAME, "event": "dismissal_audit", **record}),
          file=_log_stream, flush=True)


def ok(**payload: Any) -> dict:
    return {"data_status": "ok", "detail": "", **payload}


def unavailable(detail: str) -> dict:
    return {"data_status": "unavailable", "detail": detail}


def refused(detail: str) -> dict:
    return {"data_status": "refused", "detail": detail}


def _summarize(kwargs: dict) -> str:
    parts = [f"{k}={str(v)[:80]}" for k, v in kwargs.items() if v not in ("", None, False, 0)]
    return (",".join(parts) or "-")[:100]


def envelope(fn: Callable[..., dict]) -> Callable[..., dict]:
    """One log line per call; failures become explicit envelopes, never raises."""

    @wraps(fn)
    def wrapper(**kwargs):
        t0 = time.perf_counter()
        summary = _summarize(kwargs)
        try:
            result = fn(**kwargs)
            log_call(fn.__name__, summary, t0, ok=result.get("data_status") == "ok")
            return result
        except AuvikUnavailable as exc:
            log_call(fn.__name__, summary, t0, ok=False)
            return unavailable(str(exc))
        except ValueError as exc:  # caller input the tool could not accept
            log_call(fn.__name__, summary, t0, ok=False)
            return refused(str(exc))
        except Exception as exc:  # noqa: BLE001 — the envelope IS the handler
            log_call(fn.__name__, summary, t0, ok=False)
            return unavailable(f"{type(exc).__name__}: {exc}")

    return wrapper


class TTLCache:
    def __init__(self, ttl: float = 300, clock: Callable[[], float] = time.monotonic):
        self.ttl, self.clock, self._store = ttl, clock, {}

    def get(self, key: str, fetch: Callable[[], Any]) -> Any:
        hit = self._store.get(key)
        now = self.clock()
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        value = fetch()
        self._store[key] = (now, value)
        return value

    def clear(self) -> None:
        self._store.clear()


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def now_iso() -> str:
    return _fmt(datetime.now(timezone.utc))


def iso_ago(hours: float, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return _fmt(now - timedelta(hours=hours))
