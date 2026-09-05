"""Hardware component health - fans, PSUs, disks, memory, CPUs.

The API filters on a single status, so "anything wrong anywhere?" is two calls
(degraded + failed) merged. A fleet-wide list of HEALTHY components is refused:
every CPU core on every server is unbounded and answers nothing.

Shape note: the endpoint answers HTTP 200 and accepts every documented filter
name, but we have not yet seen a populated response from it, so this projection
is built on the OpenAPI file's declared shape alone. That file has been wrong
before (see docs/API-DEVIATIONS.md D-1, D-4, D-9), so treat the first
odd-looking real response as a shape problem rather than a data problem.
"""
from __future__ import annotations

from collections import Counter

from envelope import ok
from tools.common import check_enum, check_limit, scope_sites
from tools.devices import tenant_of

COMPONENT_STATUSES = ("ok", "degraded", "failed")
COMPONENT_TYPES = ("cpu", "disk", "fan", "memory", "powerSupply", "storageController", "systemBoard")
PROBLEM_STATUSES = ("degraded", "failed")


def project_component(item: dict) -> dict:
    a = item.get("attributes") or {}
    tenant_id, site = tenant_of(item)
    parent = (((item.get("relationships") or {}).get("parentDevice") or {}).get("data")) or {}
    return {
        "component_id": item.get("id") or "",
        "tenant_id": tenant_id,
        "site": site,
        "name": a.get("componentName") or "",
        "type": a.get("componentType") or "",
        "status": a.get("currentStatus") or "",
        "last_modified": a.get("lastModified") or "",
        "device_id": parent.get("id") or "",
        "device_name": (parent.get("attributes") or {}).get("deviceName") or "",
    }


def component_health(client, tenant_ids: list[str], site: str = "", device_id: str = "", status: str = "",
                     component_type: str = "", problems_only: bool = True, limit: int = 100,
                     tenant_sites: dict[str, str] | None = None) -> dict:
    check_enum("status", status, COMPONENT_STATUSES)
    check_enum("component_type", component_type, COMPONENT_TYPES)
    limit = check_limit(limit)
    device_id = device_id.strip()
    if status:
        statuses: list[str] = [status]
    elif problems_only:
        statuses = list(PROBLEM_STATUSES)
    elif device_id:
        statuses = [""]
    else:
        raise ValueError("a fleet-wide list of healthy components is unbounded; give device_id, or status, "
                         "or leave problems_only=true")
    sc = scope_sites(tenant_ids, tenant_sites, site)
    if sc.unmatched:
        return ok(components=[], total_count=0, returned_count=0, truncated=False, counts_by_status={},
                  counts_by_type={}, **sc.site_key(), detail=sc.unmatched_detail(site))

    base: dict = {"tenants": ",".join(sc.tenant_ids)}
    if device_id:
        base["filter[deviceId]"] = device_id
    items: list[dict] = []
    upstream_truncated = False
    for s in statuses:
        params = dict(base)
        if s:
            params["filter[currentStatus]"] = s
        got, trunc = client.get_all("/v1/inventory/component/info", params, max_items=2000)
        items.extend(got)
        upstream_truncated = upstream_truncated or trunc
    rows = [project_component(i) for i in items]
    if component_type:
        # No server-side filter exists for componentType, so this one is ours.
        rows = [r for r in rows if r["type"] == component_type]
    out = rows[:limit]
    return ok(components=out, total_count=len(rows), returned_count=len(out),
              truncated=upstream_truncated or len(rows) > len(out),
              counts_by_status=dict(Counter(r["status"] for r in rows)),
              counts_by_type=dict(Counter(r["type"] for r in rows)),
              **sc.site_key())
