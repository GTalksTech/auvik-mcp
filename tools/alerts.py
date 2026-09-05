"""Alert tools. Projections are flat and Copilot-safe. Every read records the
alert IDs it returned in SEEN_ALERT_IDS — the dismiss guardrail's memory."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable

from envelope import iso_ago, log_audit, now_iso, ok, refused
from tools.common import scope_sites
from tools.devices import tenant_of

SEVERITIES = ("emergency", "critical", "warning", "info", "unknown")
STATUSES = ("created", "resolved", "paused", "unpaused")
MAX_LIMIT = 200
SEEN_ALERT_IDS: set[str] = set()
# Alerts this process has successfully dismissed. Auvik's dismissal is
# EVENTUALLY CONSISTENT — observed live, a re-read half a second after a
# successful POST still reported dismissed=false, flipping to true only about
# two minutes later (deviation D-7). The rule-4 re-read below therefore reads
# STALE inside that window: on its own it would let a retry through, issue a
# second POST, and write a second audit line for one logical action. This set is
# the authority the API cannot yet be.
#
# It is checked BEFORE the re-read, and that ordering earns its keep even though
# the check would still catch a retry afterwards: consulted first, a retry is
# refused deterministically and locally - including when Auvik is unreachable,
# where the re-read raises and the caller would otherwise be told "unavailable",
# which is precisely the status that invites another retry. It also saves a
# request nobody needs. Pinned by
# test_a_retry_is_refused_locally_even_when_auvik_is_unreachable.
# Process-scoped, the same documented limit as SEEN_ALERT_IDS.
DISMISSED_ALERT_IDS: set[str] = set()
AUDIT_PATH = Path(__file__).resolve().parent.parent / "audit" / "dismissals.jsonl"


def _append_audit(record: dict, audit_path: Path) -> str:
    """Append one line to the JSONL audit trail. Returns "" on success, or a
    warning to put in `detail`.

    **Best effort by design.** By the time this runs the POST has already
    happened and the alert really is dismissed. Raising here would make a
    completed write report `data_status: "unavailable"` — this server's
    founding rule inverted, a finished action masquerading as an outage — and
    the agent would retry a dismissal that succeeded. That retry is the D-7
    duplicate write, reached through the filesystem instead of through eventual
    consistency. The structured log line emitted alongside is the copy that does
    not depend on the filesystem at all.
    """
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        return ""
    except OSError as exc:
        return (f"WARNING: the dismissal succeeded but the audit file could not be written "
                f"({type(exc).__name__}: {exc}). The record was emitted as a dismissal_audit "
                f"log line instead — capture it from the process log.")


def _check_enum(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value and value not in allowed:
        raise ValueError(f"invalid {name} {value!r}; valid: {', '.join(allowed)}")


def _check_limit(limit: int) -> int:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return min(limit, MAX_LIMIT)


def project_alert(item: dict, resolve_device_name: Callable[[str], str],
                  resolve_interface: Callable[[str], dict] | None = None) -> dict:
    a = item.get("attributes") or {}
    rel = item.get("relationships") or {}
    tenant_id, site = tenant_of(item)
    entity = (rel.get("entity") or {}).get("data") or {}
    related = (rel.get("relatedAlert") or {}).get("data") or {}
    entity_type = entity.get("type") or ""
    entity_id = entity.get("id") or ""
    device_id = entity_id if entity_type == "device" else ""
    device_name = resolve_device_name(device_id) if device_id else ""
    interface_name = ""
    # An "Interface Down" alert names an interface GUID and nothing else. One
    # cached lookup turns it into a device the on-call can actually go to.
    if entity_type == "interface" and resolve_interface is not None and entity_id:
        parent = resolve_interface(entity_id)
        device_id, device_name, interface_name = parent["device_id"], parent["device_name"], parent["interface_name"]
    return {
        "alert_id": item.get("id") or "",
        "tenant_id": tenant_id,
        "site": site,
        "name": a.get("name") or "",
        "severity": a.get("severity") or "",
        "status": a.get("status") or "",
        "detected_on": a.get("detectedOn") or "",
        "dismissed": bool(a.get("dismissed")),
        "dispatched": bool(a.get("dispatched")),
        "description": a.get("description") or "",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "device_id": device_id,
        "device_name": device_name,
        "interface_name": interface_name,
        "related_alert_id": related.get("id") or "",
        "external_tickets": [f"{t.get('system', '')}:{t.get('id', '')}" for t in (a.get("externalTicket") or [])],
    }


def unresolved_interface_note(rows: list[dict], resolve_interface) -> str:
    """Empty string when every interface alert named its device; otherwise a
    count to put in `detail`.

    An enrichment failure must never turn a successful alert list into
    `unavailable` - the alerts are real and complete. It must never go unreported
    either, or blank device_name reads as "Auvik has no device for this" rather
    than "the lookup failed".
    """
    if resolve_interface is None:
        return ""
    n = sum(1 for r in rows if r["entity_type"] == "interface" and r["entity_id"] and not r["device_id"])
    if not n:
        return ""
    return (f"{n} interface alert{'s' if n != 1 else ''} could not be resolved to a device "
            f"(interface lookup failed); device_name and interface_name are blank for those")


def list_alerts(client, tenant_ids: list[str], resolve_device_name: Callable[[str], str],
                severity: str = "", status: str = "", include_dismissed: bool = False,
                device_id: str = "", hours: int = 24, limit: int = 50, site: str = "", name: str = "",
                tenant_sites: dict[str, str] | None = None, now: datetime | None = None,
                resolve_interface: Callable[[str], dict] | None = None) -> dict:
    _check_enum("severity", severity, SEVERITIES)
    _check_enum("status", status, STATUSES)
    if hours < 0:
        raise ValueError("hours must be >= 0 (0 = no time window)")
    limit = _check_limit(limit)

    # The site list comes from /v1/tenants, never the device estate, and `sites`
    # is omitted rather than [] when no map was given. Both rules and the reasons
    # for them live in tools/common.py::scope_sites, shared by every
    # tenant-scoped tool.
    sc = scope_sites(tenant_ids, tenant_sites, site)
    if sc.unmatched:
        # A real zero, shaped like any other answer, and no request made.
        return ok(alerts=[], total_count=0, returned_count=0, truncated=False,
                  counts_by_severity={}, window_hours=hours, **sc.site_key(),
                  detail=sc.unmatched_detail(site))

    params: dict = {"tenants": ",".join(sc.tenant_ids)}
    if severity:
        params["filter[severity]"] = severity
    if status:
        params["filter[status]"] = status
    if not include_dismissed:
        params["filter[dismissed]"] = "false"
    if device_id:
        params["filter[entityId]"] = device_id
    if hours:
        params["filter[detectedTimeAfter]"] = iso_ago(hours, now)

    items, upstream_truncated = client.get_all("/v1/alert/history/info", params, max_items=1000)
    rows = [project_alert(i, resolve_device_name, resolve_interface) for i in items]
    if name:
        # Matched against the description as well as the name, because a large
        # fraction of real alert names still hold Auvik's raw, un-interpolated
        # template (D-6) - the readable text only ever lands in the description.
        n = name.lower()
        rows = [r for r in rows if n in r["name"].lower() or n in r["description"].lower()]
    out = rows[:limit]
    # Only the rows actually RETURNED. This used to record every row that matched
    # the filter, before truncation - so a limit=1 read made every match in the
    # window dismissible while showing the caller one of them. Harmless in
    # practice (the caller cannot cite an id it never saw) but it made the
    # implemented rule "matched a read's filter" while every document says
    # "returned by a read". The narrower rule is the documented one, and a write
    # guardrail should be exactly as wide as it claims.
    SEEN_ALERT_IDS.update(r["alert_id"] for r in out if r["alert_id"])
    return ok(
        alerts=out,
        total_count=len(rows),
        returned_count=len(out),
        truncated=upstream_truncated or len(rows) > len(out),
        counts_by_severity=dict(Counter(r["severity"] for r in rows)),
        window_hours=hours,
        **sc.site_key(),
        detail=unresolved_interface_note(rows, resolve_interface),
    )


def get_alert(client, resolve_device_name: Callable[[str], str], alert_id: str,
              resolve_interface: Callable[[str], dict] | None = None) -> dict:
    if not alert_id.strip():
        raise ValueError("alert_id is required")
    body = client.get(f"/v1/alert/history/info/{alert_id}")
    row = project_alert(body.get("data") or {}, resolve_device_name, resolve_interface)
    if row["alert_id"]:
        SEEN_ALERT_IDS.add(row["alert_id"])
    return ok(alert=row)


def dismiss_alert(client, resolve_device_name: Callable[[str], str], alert_id: str, reason: str,
                  audit_path: Path | None = None, caller: str = "local",
                  now_iso_fn: Callable[[], str] = now_iso,
                  resolve_interface: Callable[[str], dict] | None = None) -> dict:
    """The only write in this server. Rules in order; first failure → refused, NO POST.

    1 reason required · 2 alert must have been read this session · 3 not already
    dismissed BY THIS PROCESS (checked before the re-read, because the re-read is
    eventually consistent — D-7) · 4 re-read; already dismissed → refused (a
    re-read failure → AuvikUnavailable propagates) · 5 POST · 6 audit line.

    `alert_id` must also be non-empty; that is an argument check rather than a
    guardrail, so it is deliberately unnumbered — the numbered rules are the ones
    other files refer to by number.
    """
    # Resolved here, not as a def-time default: a default binds AUDIT_PATH at
    # import and nothing can redirect it, so the offline test suite would append
    # fabricated dismissals to the real audit trail — and that JSONL line is the
    # only durable record of why a write happened.
    audit_path = AUDIT_PATH if audit_path is None else audit_path
    alert_id = alert_id.strip()
    if not reason or not reason.strip():
        return refused("reason is required — say why this alert is being dismissed")
    if not alert_id:
        return refused("alert_id is required")
    if alert_id not in SEEN_ALERT_IDS:
        return refused(f"alert {alert_id} has not been read in this session; call auvik_get_alert first")
    if alert_id in DISMISSED_ALERT_IDS:
        # Checked BEFORE the re-read. Rule 4 alone would let this through inside
        # the propagation window, and consulting this set first also means a retry
        # is refused without needing Auvik to answer at all.
        return refused(f"alert {alert_id} is already dismissed — this process dismissed it earlier; "
                       "Auvik can take a couple of minutes to reflect that on a read")

    # resolve_interface too: an "Interface Down" alert names an interface GUID,
    # and without it the audit record's device_name would be blank for exactly
    # the alerts an on-call person most needs to identify afterwards.
    current = project_alert((client.get(f"/v1/alert/history/info/{alert_id}").get("data") or {}),
                            resolve_device_name, resolve_interface)
    if current["dismissed"]:
        return refused(f"alert {alert_id} is already dismissed")

    client.post(f"/v1/alert/dismiss/{alert_id}")  # the single write call site — a test enforces that

    # Armed IMMEDIATELY, before any I/O that could fail. The write has happened;
    # this set records that fact, and nothing below may prevent it being
    # recorded. Arming it *after* the audit append would mean a read-only
    # filesystem left the duplicate-write guard disarmed on a dismissal that had
    # really gone through — the D-7 failure, reached through the filesystem
    # instead of through eventual consistency.
    DISMISSED_ALERT_IDS.add(alert_id)

    ts = now_iso_fn()
    record = {
        "ts": ts, "alert_id": alert_id, "name": current["name"], "severity": current["severity"],
        "entity_id": current["entity_id"], "device_name": current["device_name"], "reason": reason.strip(),
        "caller_key_label": caller, "result": "dismissed",
    }
    log_audit(record)                              # durable copy — survives the container
    audit_note = _append_audit(record, audit_path)  # best effort — must not fail the call

    detail = ("Auvik accepted the dismissal. It is eventually consistent: re-reading this "
              "alert may still show dismissed=false for a minute or two. That is propagation "
              "delay, not a failure — do not retry the dismissal.")
    return ok(dismissed=True, alert_id=alert_id, name=current["name"], severity=current["severity"],
              device_name=current["device_name"], reason=reason.strip(), audited_at=ts,
              detail=f"{audit_note} {detail}" if audit_note else detail)


SEVERITY_RANK = {"emergency": 4, "critical": 3, "warning": 2, "info": 1, "unknown": 0, "": 0}
FLAP_THRESHOLD = 3
RESOLVED_PREFIX = "Resolved: "


def _base_name(name: str) -> str:
    return name[len(RESOLVED_PREFIX):] if name.startswith(RESOLVED_PREFIX) else name


def summarize_alerts(client, tenant_ids: list[str], resolve_device_name: Callable[[str], str],
                     severity: str = "", include_dismissed: bool = False, hours: int = 24, limit: int = 50,
                     site: str = "", tenant_sites: dict[str, str] | None = None,
                     resolve_interface: Callable[[str], dict] | None = None,
                     now: datetime | None = None) -> dict:
    """Roll alerts up by (base name, entity, site). Same fetch as list_alerts.

    Does NOT touch SEEN_ALERT_IDS: the model has seen a group, not an alert.
    `latest_alert_id` lets it call get_alert and then dismiss under the usual rules.
    """
    _check_enum("severity", severity, SEVERITIES)
    if hours < 0:
        raise ValueError("hours must be >= 0 (0 = no time window)")
    limit = _check_limit(limit)
    sc = scope_sites(tenant_ids, tenant_sites, site)
    if sc.unmatched:
        return ok(groups=[], group_count=0, returned_count=0, truncated=False, alert_count=0,
                  active_alert_count=0, counts_by_severity={}, window_hours=hours, **sc.site_key(),
                  detail=sc.unmatched_detail(site))

    params: dict = {"tenants": ",".join(sc.tenant_ids)}
    if severity:
        params["filter[severity]"] = severity
    if not include_dismissed:
        params["filter[dismissed]"] = "false"
    if hours:
        params["filter[detectedTimeAfter]"] = iso_ago(hours, now)
    items, upstream_truncated = client.get_all("/v1/alert/history/info", params, max_items=1000)
    rows = [project_alert(i, resolve_device_name, resolve_interface) for i in items]

    groups: dict[tuple, dict] = {}
    for r in sorted(rows, key=lambda r: r["detected_on"]):  # chronological, so "latest" is last
        # Auvik prefixes the name with "Resolved: " when an alert clears, so the
        # raise and the clear would otherwise sit in two different groups and
        # neither would show the flap.
        key = (_base_name(r["name"]), r["entity_id"], r["site"])
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "alert_name": key[0], "site": r["site"], "entity_type": r["entity_type"], "entity_id": r["entity_id"],
                "device_id": r["device_id"], "device_name": r["device_name"], "interface_name": r["interface_name"],
                "severity": r["severity"], "count": 0, "active_count": 0, "resolved_count": 0,
                "first_seen": r["detected_on"], "last_seen": r["detected_on"],
                "latest_alert_id": r["alert_id"], "latest_status": r["status"],
                "latest_description": r["description"], "flapping": False,
            }
        g["count"] += 1
        if r["status"] == "resolved":
            g["resolved_count"] += 1
        else:
            g["active_count"] += 1
        if SEVERITY_RANK.get(r["severity"], 0) > SEVERITY_RANK.get(g["severity"], 0):
            g["severity"] = r["severity"]
        g["last_seen"], g["latest_alert_id"] = r["detected_on"], r["alert_id"]
        g["latest_status"], g["latest_description"] = r["status"], r["description"]
        if not g["device_name"] and r["device_name"]:
            g["device_id"], g["device_name"] = r["device_id"], r["device_name"]
    for g in groups.values():
        g["flapping"] = g["count"] >= FLAP_THRESHOLD

    ordered = sorted(groups.values(), key=lambda g: g["last_seen"], reverse=True)  # stable tiebreak
    ordered.sort(key=lambda g: (-g["active_count"], -SEVERITY_RANK.get(g["severity"], 0), -g["count"]))
    out = ordered[:limit]
    return ok(groups=out, group_count=len(ordered), returned_count=len(out),
              truncated=upstream_truncated or len(ordered) > len(out),
              alert_count=len(rows), active_alert_count=sum(1 for r in rows if r["status"] != "resolved"),
              counts_by_severity=dict(Counter(r["severity"] for r in rows)),
              window_hours=hours, **sc.site_key(),
              detail=unresolved_interface_note(rows, resolve_interface))
