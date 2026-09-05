"""The audit trail must survive the container it runs in — and a failure to
write it must never be reported as a failure to dismiss.

`audit/dismissals.jsonl` is container-local disk. In a container that is
ephemeral: a restart or a new revision and the *only* record of why a write
happened is gone. Worse, a read-only root filesystem is a common container
hardening default, and without the fix below that turns every dismissal into
this sequence:

    client.post(...)            <- the alert IS dismissed in Auvik
    audit_path.open("a")        <- OSError: Read-only file system
    -> exception escapes dismiss_alert
    -> @envelope reports data_status: "unavailable"
    -> DISMISSED_ALERT_IDS.add() never runs, so rule 3 is never armed
    -> the agent believes the write failed and retries
    -> second POST, second dismissal, and no record of either

A successful write reported as `unavailable` is this server's founding rule
inverted: instead of an outage masquerading as an empty answer, it is a
completed action masquerading as an outage. The retry it provokes is exactly
the D-7 duplicate-write failure, reached through the filesystem instead of
through eventual consistency.

Two changes, both pinned here:

* the record is emitted as a structured log line on the same stream as every
  other log line, so the host's stdout capture holds a durable copy that
  does not depend on the filesystem at all;
* the file append becomes best-effort and strictly after the guard is armed.
  A failed append is reported in `detail` of an `ok` response, because the
  dismissal did happen and saying otherwise is a lie.
"""
from __future__ import annotations

import io
import json

import pytest

import envelope as env
from conftest import FakeClient, default_routes
from tools import alerts

resolve = lambda did: {"DEV-1": "core-sw-01"}.get(did, "")  # noqa: E731
TS = "2026-08-31T12:00:00.000Z"


@pytest.fixture()
def logged():
    """Capture the log stream the server writes to (stderr in stdio mode)."""
    buf = io.StringIO()
    env.set_log_stream(buf)
    yield buf
    env.set_log_stream(__import__("sys").stdout)


def _fake():
    return FakeClient(default_routes(), post_response={"data": {}})


def _dismiss(fake, audit_path, reason="planned reboot"):
    alerts.SEEN_ALERT_IDS.update({"AL-1"})
    return alerts.dismiss_alert(fake, resolve, "AL-1", reason, audit_path=audit_path,
                                caller="app-key", now_iso_fn=lambda: TS)


def _audit_lines(buf) -> list[dict]:
    out = []
    for line in buf.getvalue().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("event") == "dismissal_audit":
            out.append(rec)
    return out


def _unwritable(tmp_path):
    """A path that cannot be appended to: it is a directory. Stands in for the
    read-only root filesystem a hardened container gives you."""
    p = tmp_path / "dismissals.jsonl"
    p.mkdir(exist_ok=True)
    return p


# --------------------------------------------------- the log copy always exists

def test_a_dismissal_is_emitted_as_a_structured_log_line(logged, tmp_path):
    out = _dismiss(_fake(), tmp_path / "a.jsonl")
    assert out["data_status"] == "ok"
    recs = _audit_lines(logged)
    assert len(recs) == 1, "no dismissal_audit line on the log stream"
    assert recs[0]["alert_id"] == "AL-1" and recs[0]["reason"] == "planned reboot"


def test_the_log_line_and_the_file_line_carry_the_same_record(logged, tmp_path):
    audit = tmp_path / "a.jsonl"
    _dismiss(_fake(), audit)
    from_file = json.loads(audit.read_text(encoding="utf-8").strip())
    from_log = _audit_lines(logged)[0]
    assert {k: v for k, v in from_log.items() if k in from_file} == from_file, (
        "the durable copy disagrees with the file copy"
    )


def test_the_log_line_never_carries_the_api_key(logged, tmp_path):
    """The audit record carries the caller LABEL and never the credential.

    The credential is reachable from inside dismiss_alert — it hangs off the
    client as `client.creds` — so this test puts a real AuvikCredentials there
    with a distinctive key and asserts that key never reaches the log. That is
    what makes the assertion falsifiable: adding `client.creds.api_key` (or the
    whole creds object, whose repr would be interpolated) to the audit record
    fails this test. Asserting against a credential that is not in scope at all
    would prove nothing.

    Deliberately NOT read from the real keyring: that would break the suite's
    own offline rule (conftest._offline_guard), and on any machine without a
    stored credential the assertion would silently evaporate."""
    from credentials import AuvikCredentials  # noqa: PLC0415

    secret = "unmistakable-secret-value-that-must-never-be-logged"
    fake = _fake()
    fake.creds = AuvikCredentials("svc@example.com", secret, "us1")

    _dismiss(fake, tmp_path / "a.jsonl")
    body = logged.getvalue()

    assert body.strip(), "no audit line was logged at all"
    assert secret not in body, "the API key reached a log line"
    assert fake.creds.fingerprint not in body, (
        "even the key fingerprint does not belong in the audit record"
    )
    assert "password" not in body.lower()
    assert "api_key" not in body, "the audit record must not even name a key field"
    assert '"caller_key_label"' in body, "the audit record must carry the caller LABEL"


# ------------------------------------- a file failure never becomes an outage

def test_a_write_that_succeeded_is_never_reported_unavailable(logged, tmp_path):
    fake = _fake()
    out = _dismiss(fake, _unwritable(tmp_path))
    assert out["data_status"] == "ok", (
        "the POST succeeded and the alert IS dismissed; reporting unavailable invites "
        "the retry that duplicates the write"
    )
    assert [c for c in fake.calls if c[0] == "POST"] == [("POST", "/v1/alert/dismiss/AL-1", {})]


def test_the_duplicate_guard_is_armed_even_when_the_file_cannot_be_written(tmp_path):
    fake = _fake()
    _dismiss(fake, _unwritable(tmp_path))
    again = _dismiss(fake, _unwritable(tmp_path))
    assert again["data_status"] == "refused", "rule 3 was not armed; a retry would POST again"
    assert len([c for c in fake.calls if c[0] == "POST"]) == 1


def test_the_record_still_reaches_the_log_when_the_file_cannot_be_written(logged, tmp_path):
    _dismiss(_fake(), _unwritable(tmp_path))
    assert len(_audit_lines(logged)) == 1, "no durable copy of a dismissal whose file write failed"


def test_the_response_says_the_audit_file_failed(logged, tmp_path):
    out = _dismiss(_fake(), _unwritable(tmp_path))
    assert "audit" in out["detail"].lower(), f"detail hides the audit failure: {out['detail']!r}"
    assert "propagation delay" in out["detail"], "the eventual-consistency warning was dropped"


def test_a_missing_parent_directory_is_not_a_failure(logged, tmp_path):
    """The normal case: audit/ does not exist yet and gets created."""
    audit = tmp_path / "audit" / "dismissals.jsonl"
    out = _dismiss(_fake(), audit)
    assert out["data_status"] == "ok" and audit.exists()
    assert "audit" not in out["detail"].lower().replace("audited", "")


def test_a_parent_that_is_a_file_degrades_to_the_log_copy(logged, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    out = _dismiss(_fake(), blocker / "dismissals.jsonl")
    assert out["data_status"] == "ok"
    assert len(_audit_lines(logged)) == 1
