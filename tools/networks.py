"""Networks Auvik OBSERVES. This is not a source of truth for addressing - it is
what the collectors can see. A collector reached over L3 routing sees networks
that belong to other sites, and those are typically set to "do not scan", so
`scan_status` is on every row and is the filter that means "the networks this
site actually scans" rather than "the networks visible from here"."""
from __future__ import annotations

from collections import Counter

from envelope import ok
from tools.common import check_enum, check_limit, scope_sites
from tools.devices import tenant_of

NETWORK_TYPES = ("routed", "vlan", "wifi", "loopback", "network", "layer2", "internet")
SCAN_STATUSES = ("true", "false", "notAllowed", "unknown")
MAX_DEVICES_PER_NETWORK = 50


def _scan_status(value) -> str:
    """Normalise `scanStatus` to a string, whatever arrives.

    The OpenAPI file and the live API agree here: the field is a STRING with the
    enum "true" | "false" | "notAllowed" | "unknown" (SCAN_STATUSES below matches
    it exactly). That is unusual enough - a field named like a boolean, carrying
    the WORD "true" - that a JSON boolean arriving one day would be an easy
    change for Auvik to make, and would break this silently. Normalising costs
    nothing and means the caller never has to handle two types."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value) if value is not None else ""


def project_network(item: dict, include_devices: bool = False, max_devices: int = MAX_DEVICES_PER_NETWORK) -> dict:
    a = item.get("attributes") or {}
    tenant_id, site = tenant_of(item)
    devs = list((((item.get("relationships") or {}).get("devices") or {}).get("data")) or [])
    return {
        "network_id": item.get("id") or "",
        "tenant_id": tenant_id,
        "site": site,
        "name": a.get("networkName") or "",
        "description": a.get("description") or "",
        "type": a.get("networkType") or "",
        "scan_status": _scan_status(a.get("scanStatus")),
        "last_modified": a.get("lastModified") or "",
        "device_count": len(devs),
        # device_count is always present; the list is opt-in and capped, because
        # a /24 with 200 hosts across 100 networks would approach the 500 KB cap.
        "devices": [{"device_id": d.get("id") or "", "name": (d.get("attributes") or {}).get("deviceName") or ""}
                    for d in devs[:max_devices]] if include_devices else [],
        "devices_truncated": bool(include_devices and len(devs) > max_devices),
    }


def list_networks(client, tenant_ids: list[str], site: str = "", network_type: str = "", scan_status: str = "",
                  device_id: str = "", name: str = "", include_devices: bool = False, limit: int = 100,
                  tenant_sites: dict[str, str] | None = None) -> dict:
    check_enum("network_type", network_type, NETWORK_TYPES)
    check_enum("scan_status", scan_status, SCAN_STATUSES)
    limit = check_limit(limit)
    sc = scope_sites(tenant_ids, tenant_sites, site)
    if sc.unmatched:
        return ok(networks=[], total_count=0, returned_count=0, truncated=False, counts_by_type={},
                  counts_by_scan_status={}, **sc.site_key(), detail=sc.unmatched_detail(site))

    params: dict = {"tenants": ",".join(sc.tenant_ids)}
    if network_type:
        params["filter[networkType]"] = network_type
    if scan_status:
        params["filter[scanStatus]"] = scan_status
    if device_id.strip():
        params["filter[devices]"] = device_id.strip()
    items, upstream_truncated = client.get_all("/v1/inventory/network/info", params, max_items=2000)
    rows = [project_network(i, include_devices) for i in items]
    if name:
        n = name.lower()
        rows = [r for r in rows if n in r["name"].lower() or n in r["description"].lower()]
    out = rows[:limit]
    return ok(networks=out, total_count=len(rows), returned_count=len(out),
              truncated=upstream_truncated or len(rows) > len(out),
              counts_by_type=dict(Counter(r["type"] for r in rows)),
              counts_by_scan_status=dict(Counter(r["scan_status"] for r in rows)),
              **sc.site_key())
