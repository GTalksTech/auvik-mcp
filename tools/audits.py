"""Remote-access audit: who opened a terminal, tunnel or remote-browser session to
a device through Auvik, and when. The endpoint has no entity filter, so
device_id is applied client-side after the fetch.

Shape note: the endpoint answers HTTP 200 and accepts every documented filter
name, but we have not yet seen a populated response from it, so this projection
is built on the OpenAPI file's declared shape alone. That file has been wrong
before (see docs/API-DEVIATIONS.md D-1, D-4, D-9), so treat the first
odd-looking real response as a shape problem rather than a data problem.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from envelope import iso_ago, ok
from tools.common import check_enum, check_limit, scope_sites
from tools.devices import tenant_of

AUDIT_CATEGORIES = ("tunnel", "terminal", "remoteBrowser", "unknown")
AUDIT_STATUSES = ("initiated", "created", "closed", "failed", "unknown")
MAX_DATA_CHARS = 300


def project_audit(item: dict) -> dict:
    a = item.get("attributes") or {}
    tenant_id, site = tenant_of(item)
    dev = (((item.get("relationships") or {}).get("device") or {}).get("data")) or {}
    return {
        "audit_id": item.get("id") or "",
        "tenant_id": tenant_id,
        "site": site,
        "user": a.get("user") or "",
        "category": a.get("category") or "",
        "action": a.get("action") or "",
        "direction": a.get("direction") or "",
        "status": a.get("status") or "",
        "cause": a.get("cause") or "",
        # `data` is free-form and undocumented in length; capped so one session
        # record cannot dominate the 500 KB response budget.
        "data": (a.get("data") or "")[:MAX_DATA_CHARS],
        "started": a.get("dateStarted") or "",
        "last_active": a.get("lastActive") or "",
        "device_id": dev.get("id") or "",
        "device_name": (dev.get("attributes") or {}).get("deviceName") or "",
    }


def remote_access_audit(client, tenant_ids: list[str], hours: int = 168, site: str = "", user: str = "",
                        category: str = "", status: str = "", device_id: str = "", limit: int = 100,
                        tenant_sites: dict[str, str] | None = None, now: datetime | None = None) -> dict:
    check_enum("category", category, AUDIT_CATEGORIES)
    check_enum("status", status, AUDIT_STATUSES)
    if hours < 0:
        raise ValueError("hours must be >= 0 (0 = no time window)")
    limit = check_limit(limit)
    sc = scope_sites(tenant_ids, tenant_sites, site)
    if sc.unmatched:
        return ok(sessions=[], total_count=0, returned_count=0, truncated=False, counts_by_user={},
                  counts_by_category={}, window_hours=hours, **sc.site_key(), detail=sc.unmatched_detail(site))

    params: dict = {"tenants": ",".join(sc.tenant_ids)}
    if user.strip():
        params["filter[user]"] = user.strip()
    if category:
        params["filter[category]"] = category
    if status:
        params["filter[status]"] = status
    if hours:
        params["filter[modifiedAfter]"] = iso_ago(hours, now)
    items, upstream_truncated = client.get_all("/v1/inventory/entity/audit", params, max_items=2000)
    rows = [project_audit(i) for i in items]
    if device_id.strip():
        # No filter[entityId] exists on this endpoint, so this one is ours.
        rows = [r for r in rows if r["device_id"] == device_id.strip()]
    rows.sort(key=lambda r: r["started"], reverse=True)
    out = rows[:limit]
    return ok(sessions=out, total_count=len(rows), returned_count=len(out),
              truncated=upstream_truncated or len(rows) > len(out),
              counts_by_user=dict(Counter(r["user"] for r in rows)),
              counts_by_category=dict(Counter(r["category"] for r in rows)),
              window_hours=hours, **sc.site_key())
