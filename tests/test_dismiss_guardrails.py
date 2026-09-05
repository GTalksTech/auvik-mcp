"""The one write. Each refusal makes NO POST; the happy path makes
exactly one POST and one audit line with every required field."""
from __future__ import annotations

import json
import pathlib
from pathlib import Path

from auvik_client import AuvikUnavailable
from conftest import FakeClient, default_routes
from tools import alerts

resolve = lambda did: {"DEV-1": "core-sw-01"}.get(did, "")  # noqa: E731
REPO_AUDIT = Path(__file__).resolve().parent.parent / "audit" / "dismissals.jsonl"


def _fake(post_response={"data": {}}):
    return FakeClient(default_routes(), post_response=post_response)


def _fake_audit_path():
    import tempfile  # noqa: PLC0415
    return pathlib.Path(tempfile.mkdtemp()) / "dismissals.jsonl"


def _posts(fake):
    return [c for c in fake.calls if c[0] == "POST"]


def test_empty_reason_is_refused_without_post(tmp_path):
    fake = _fake()
    alerts.SEEN_ALERT_IDS.update({"AL-1"})
    out = alerts.dismiss_alert(fake, resolve, "AL-1", "   ", audit_path=tmp_path / "a.jsonl")
    assert out["data_status"] == "refused" and "reason is required" in out["detail"]
    assert _posts(fake) == [] and not (tmp_path / "a.jsonl").exists()


def test_unseen_alert_is_refused_without_post(tmp_path):
    fake = _fake()
    alerts.SEEN_ALERT_IDS.clear()
    out = alerts.dismiss_alert(fake, resolve, "AL-1", "false positive", audit_path=tmp_path / "a.jsonl")
    assert out["data_status"] == "refused" and "has not been read in this session" in out["detail"]
    assert "auvik_get_alert" in out["detail"]
    assert _posts(fake) == []


def test_already_dismissed_is_refused_without_post(tmp_path):
    fake = _fake()
    alerts.SEEN_ALERT_IDS.update({"AL-DISMISSED"})
    out = alerts.dismiss_alert(fake, resolve, "AL-DISMISSED", "cleanup", audit_path=tmp_path / "a.jsonl")
    assert out["data_status"] == "refused" and "already dismissed" in out["detail"]
    assert _posts(fake) == []


def test_reread_failure_is_unavailable_not_refused(tmp_path):
    from auvik_client import AuvikUnavailable
    fake = FakeClient(default_routes(), post_response={"data": {}}, fail=AuvikUnavailable(503, "down"))
    alerts.SEEN_ALERT_IDS.update({"AL-1"})
    try:
        alerts.dismiss_alert(fake, resolve, "AL-1", "x", audit_path=tmp_path / "a.jsonl")
        raised = False
    except AuvikUnavailable:
        raised = True
    assert raised, "re-read failures must propagate as AuvikUnavailable so the envelope reports 'unavailable'"
    assert _posts(fake) == []


def test_happy_path_posts_once_and_audits(tmp_path):
    fake = _fake()
    alerts.SEEN_ALERT_IDS.update({"AL-1"})
    audit = tmp_path / "a.jsonl"
    out = alerts.dismiss_alert(fake, resolve, "AL-1", "confirmed maintenance window", audit_path=audit,
                               caller="app-key", now_iso_fn=lambda: "2026-08-29T12:00:00.000Z")
    # `detail` carries the eventual-consistency warning (D-7);
    # every other key is still asserted exactly.
    detail = out.pop("detail")
    assert "propagation delay" in detail and "do not retry" in detail.lower()
    assert out == {"data_status": "ok", "dismissed": True, "alert_id": "AL-1", "name": "Device Offline",
                   "severity": "warning", "device_name": "core-sw-01", "reason": "confirmed maintenance window",
                   "audited_at": "2026-08-29T12:00:00.000Z"}
    assert _posts(fake) == [("POST", "/v1/alert/dismiss/AL-1", {})]
    lines = audit.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec == {"ts": "2026-08-29T12:00:00.000Z", "alert_id": "AL-1", "name": "Device Offline", "severity": "warning",
                   "entity_id": "DEV-1", "device_name": "core-sw-01", "reason": "confirmed maintenance window",
                   "caller_key_label": "app-key", "result": "dismissed"}


def test_post_failure_is_unavailable_and_not_audited(tmp_path):
    from auvik_client import AuvikUnavailable

    class PostFails(FakeClient):
        def post(self, path):
            self.calls.append(("POST", path, {}))
            raise AuvikUnavailable(403, "Forbidden")

    fake = PostFails(default_routes())
    alerts.SEEN_ALERT_IDS.update({"AL-1"})
    audit = tmp_path / "a.jsonl"
    try:
        alerts.dismiss_alert(fake, resolve, "AL-1", "x", audit_path=audit)
        raised = False
    except AuvikUnavailable as exc:
        raised = exc.status == 403
    assert raised and not audit.exists()


def test_audit_path_is_resolved_at_call_time_not_bound_at_import(tmp_path, monkeypatch):
    """Found during final verification: the offline suite had appended 19 fake
    'AL-1 dismissed' lines to the repo's real audit/dismissals.jsonl, because
    test_size.py drives the real tool through the MCP layer and the MCP wrapper
    passes no audit_path. `audit_path: Path = AUDIT_PATH` binds at def time, so
    nothing could redirect it. That JSONL line is the whole compensating
    control for a write API with no reason field of its own — seeding it with
    fabricated lines is exactly the corruption it exists to prevent."""
    redirected = tmp_path / "redirected.jsonl"
    monkeypatch.setattr(alerts, "AUDIT_PATH", redirected)
    fake = _fake()
    alerts.SEEN_ALERT_IDS.update({"AL-1"})
    out = alerts.dismiss_alert(fake, resolve, "AL-1", "checking the audit path")
    assert out["data_status"] == "ok"
    assert redirected.exists(), "dismiss ignored the patched AUDIT_PATH"
    assert json.loads(redirected.read_text(encoding="utf-8").strip())["reason"] == "checking the audit path"


async def test_suite_never_writes_to_the_repo_audit_trail(srv, calltool):
    """Belt and braces: driving the real tool through the MCP layer (as the
    response-size test does) must not touch the repo's audit trail."""
    from tools import alerts as al

    _server, fake = srv
    fake.post_response = {"data": {}}
    repo_audit = REPO_AUDIT
    before = repo_audit.read_bytes() if repo_audit.exists() else None
    al.SEEN_ALERT_IDS.add("AL-1")
    data = await calltool("auvik_dismiss_alert", {"alert_id": "AL-1", "reason": "suite hygiene check"})
    assert data["data_status"] == "ok"
    after = repo_audit.read_bytes() if repo_audit.exists() else None
    assert after == before, "the test suite appended to the repo's real audit/dismissals.jsonl"


def test_audit_line_written_when_auvik_returns_an_empty_body(tmp_path, monkeypatch):
    """Auvik documents the dismiss POST as 200 with no content. The audit line
    must still be written: a write that happened with no record of why is the
    one outcome the guardrails exist to prevent."""
    monkeypatch.setattr(alerts, "AUDIT_PATH", tmp_path / "a.jsonl")
    fake = FakeClient(default_routes(), post_response={})
    alerts.SEEN_ALERT_IDS.update({"AL-1"})
    out = alerts.dismiss_alert(fake, resolve, "AL-1", "empty body from Auvik")
    assert out["data_status"] == "ok" and out["dismissed"] is True
    rec = json.loads((tmp_path / "a.jsonl").read_text(encoding="utf-8").strip())
    assert rec["reason"] == "empty body from Auvik" and rec["result"] == "dismissed"


def test_second_dismissal_is_refused_even_when_the_reread_is_stale(tmp_path, monkeypatch):
    """Auvik's dismissal is EVENTUALLY CONSISTENT (deviation D-7). The first
    live dismissal proved it: the POST succeeded, but a re-read half a second
    later still returned dismissed=false, and only about two minutes later did
    it flip to true.

    Rule 4 refuses a duplicate dismissal by re-reading the alert. Against
    a stale read that rule silently fails open: the second call issued a SECOND
    POST and wrote a SECOND audit line for one logical action. An audit trail
    that double-counts writes is worse than none.

    So the process must remember what it has already dismissed and refuse on
    that, not on what the API says it can currently see. Process-scoped, the
    same documented limit as SEEN_ALERT_IDS."""
    monkeypatch.setattr(alerts, "AUDIT_PATH", tmp_path / "a.jsonl")
    alerts.DISMISSED_ALERT_IDS.clear()
    fake = _fake()                       # route AL-1 -> alert_single.json, dismissed=false
    alerts.SEEN_ALERT_IDS.update({"AL-1"})

    first = alerts.dismiss_alert(fake, resolve, "AL-1", "the real dismissal")
    assert first["data_status"] == "ok" and first["dismissed"] is True
    assert len(_posts(fake)) == 1

    # The fixture still reports dismissed=false — exactly the stale read that
    # fooled the guardrail live.
    second = alerts.dismiss_alert(fake, resolve, "AL-1", "accidental retry")
    assert second["data_status"] == "refused", f"duplicate write not refused: {second}"
    assert "already dismissed" in second["detail"]
    assert len(_posts(fake)) == 1, "a SECOND POST was issued for one logical dismissal"
    assert len((tmp_path / "a.jsonl").read_text(encoding="utf-8").strip().splitlines()) == 1


def test_ok_payload_warns_that_the_dismissal_is_not_instantly_visible(tmp_path, monkeypatch):
    """The agent must not re-read, see dismissed=false, and conclude it failed —
    that is the trap the first live dismissal fell into."""
    monkeypatch.setattr(alerts, "AUDIT_PATH", tmp_path / "a.jsonl")
    alerts.DISMISSED_ALERT_IDS.clear()
    fake = _fake()
    alerts.SEEN_ALERT_IDS.update({"AL-1"})
    out = alerts.dismiss_alert(fake, resolve, "AL-1", "checking the detail text")
    assert out["data_status"] == "ok"
    assert "propagat" in out["detail"].lower() or "may still" in out["detail"].lower()


def test_a_retry_is_refused_locally_even_when_auvik_is_unreachable():
    """This is what the guardrail ORDER buys, and until now nothing tested it.

    `DISMISSED_ALERT_IDS` is checked BEFORE the re-read. Both orderings refuse a
    duplicate while Auvik is answering — so the ordering looks like a style
    choice until Auvik stops answering. Then it matters: consulted first, the
    retry is `refused` locally and makes no request at all. Consulted after the
    re-read, that re-read raises AuvikUnavailable and the caller is told
    `unavailable` — the one status that means "transient, try again", which is
    exactly the wrong advice for a write that already succeeded.

    Moving the DISMISSED_ALERT_IDS check below the re-read makes this fail.
    """
    audit = _fake_audit_path()
    alerts.SEEN_ALERT_IDS.add("AL-1")
    first = alerts.dismiss_alert(_fake(), resolve, "AL-1", "the real dismissal", audit_path=audit)
    assert first["data_status"] == "ok"

    # Auvik is now unreachable: every GET raises.
    down = FakeClient({}, fail=AuvikUnavailable(503, "Auvik upstream unreachable"),
                      post_response={"data": {}})
    alerts.SEEN_ALERT_IDS.add("AL-1")
    retry = alerts.dismiss_alert(down, resolve, "AL-1", "an anxious retry", audit_path=audit)

    assert retry["data_status"] == "refused", (
        f"a retry during an outage reported {retry['data_status']!r}; 'unavailable' invites "
        "another retry of a write that already succeeded"
    )
    assert "already dismissed" in retry["detail"]
    assert [c for c in down.calls if c[0] == "POST"] == [], "the retry issued a second write"
    assert down.calls == [], "the retry called Auvik at all; the in-process set should answer alone"
