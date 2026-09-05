"""Device lifecycle (end of sale / support) merged with warranty.

Auvik holds this data for some vendors and not others, so a device that is
absent here is UNKNOWN, not healthy - the tool says so rather than implying
coverage it does not have.

Warranty records carry no device relationship; their `id` IS the device id.
That was verified live against a production tenant on every record returned -
warranty ids equalled the lifecycle records' relationships.device.data.id
exactly - so the merge is by id.

**Deviation D-9** (docs/API-DEVIATIONS.md). The live API returns Title-Case
statuses ("Expired", "Available", "Unpublished"); the vendor's own
`reference/auvik-openapi-v1.json` documents camelCase ("expired", "available",
"unpublished"). Comparing exactly, as the OpenAPI file implies, flags
has_problem on NOTHING - including on records where Auvik is reporting
`Expired` in the very same payload. That is a fabricated zero on this tool's
DEFAULT view (problems_only=True), which is exactly the failure mode this
server exists to prevent. So the comparison folds case, while the projected
values are passed through verbatim: this server does not present text Auvik did
not send (D-6, D-8), so counts_by_last_support reports Auvik's own spelling.
"""
from __future__ import annotations

from collections import Counter

from envelope import ok
from tools.common import check_limit, scope_sites
from tools.devices import tenant_of

# Deliberately NOT a validation tuple of the documented spellings. Auvik's
# OpenAPI file declares covered|available|expired|securityOnly|unpublished|empty,
# but the live API sends those Title-Case (D-9), so anything that checked an
# incoming value against the documented list would reject real data. Values are
# passed through verbatim and only COMPARED case-folded, here:
PROBLEM_STATUSES = {"expired", "securityonly"}  # compared case-folded - see D-9 above
_LIFECYCLE_FIELDS = ("salesAvailability", "softwareMaintenanceStatus", "securitySoftwareMaintenanceStatus", "lastSupportStatus")


def _is_problem(status: str) -> bool:
    return status.strip().lower() in PROBLEM_STATUSES


def project_lifecycle(item: dict, warranty_by_id: dict) -> dict:
    a = item.get("attributes") or {}
    tenant_id, site = tenant_of(item)
    dev = (((item.get("relationships") or {}).get("device") or {}).get("data")) or {}
    device_id = dev.get("id") or item.get("id") or ""
    w = ((warranty_by_id.get(device_id) or warranty_by_id.get(item.get("id") or "") or {}).get("attributes")) or {}
    statuses = [a.get(f) or "" for f in _LIFECYCLE_FIELDS]
    return {
        "device_id": device_id,
        "device_name": a.get("deviceName") or (dev.get("attributes") or {}).get("deviceName") or "",
        "tenant_id": tenant_id,
        "site": site,
        "sales_availability": statuses[0],
        "software_maintenance": statuses[1],
        "security_maintenance": statuses[2],
        "last_support": statuses[3],
        # Warranty is informational only and never drives has_problem: many
        # vendors report "Not Covered" simply for lack of data.
        "warranty_coverage": w.get("warrantyCoverageStatus") or "",
        "warranty_expiration": w.get("warrantyExpirationDate") or "",
        "service_coverage": w.get("serviceCoverageStatus") or "",
        "recommended_software": w.get("recommendedSoftwareVersion") or "",
        "has_problem": any(_is_problem(s) for s in statuses),
    }


def device_lifecycle(client, tenant_ids: list[str], site: str = "", device_id: str = "", problems_only: bool = True,
                     include_warranty: bool = True, limit: int = 100,
                     tenant_sites: dict[str, str] | None = None) -> dict:
    limit = check_limit(limit)
    device_id = device_id.strip()
    sc = scope_sites(tenant_ids, tenant_sites, site)
    if sc.unmatched:
        return ok(devices=[], total_count=0, returned_count=0, truncated=False, counts_by_last_support={},
                  **sc.site_key(), detail=sc.unmatched_detail(site))

    upstream_truncated = False
    if device_id:
        items = [client.get(f"/v1/inventory/device/lifecycle/{device_id}").get("data") or {}]
        warranties = [client.get(f"/v1/inventory/device/warranty/{device_id}").get("data") or {}] if include_warranty else []
    else:
        params = {"tenants": ",".join(sc.tenant_ids)}
        items, upstream_truncated = client.get_all("/v1/inventory/device/lifecycle", params, max_items=5000)
        warranties = []
        if include_warranty:
            warranties, trunc_w = client.get_all("/v1/inventory/device/warranty", params, max_items=5000)
            upstream_truncated = upstream_truncated or trunc_w
    warranty_by_id = {w.get("id") or "": w for w in warranties if w}
    rows = [project_lifecycle(i, warranty_by_id) for i in items if i]
    if problems_only:
        rows = [r for r in rows if r["has_problem"]]
    out = rows[:limit]
    return ok(devices=out, total_count=len(rows), returned_count=len(out),
              truncated=upstream_truncated or len(rows) > len(out),
              counts_by_last_support=dict(Counter(r["last_support"] for r in rows)),
              **sc.site_key())
