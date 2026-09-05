"""Interface inventory for one device: name, type, MAC, negotiated speed, duplex,
IPs, operational status and admin state.

Scoped to a single `device_id` by contract - Auvik will happily return every
interface in the tenant, and on a large estate that is neither a useful answer
nor one that fits the response-size cap.
"""
from __future__ import annotations

from collections import Counter

from envelope import ok

INTERFACE_TYPES = (
    "ethernet", "wifi", "bluetooth", "cdma", "coax", "cpu", "distributedVirtualSwitch", "firewire", "gsm",
    "ieee8023AdLag", "inferredWired", "inferredWireless", "interface", "linkAggregation", "loopback", "modem",
    "wimax", "optical", "other", "parallel", "ppp", "radiomac", "rs232", "tunnel", "unknown", "usb",
    "virtualBridge", "virtualNic", "virtualSwitch", "vlan",
)
OPER_STATUSES = ("online", "offline", "unreachable", "testing", "unknown", "dormant", "notPresent", "lowerLayerDown")
MAX_LIMIT = 200


def _check_enum(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value and value not in allowed:
        raise ValueError(f"invalid {name} {value!r}; valid: {', '.join(allowed)}")


def project_interface(item: dict) -> dict:
    a = item.get("attributes") or {}
    parent = ((item.get("relationships") or {}).get("parentDevice") or {}).get("data") or {}
    return {
        "interface_id": item.get("id") or "",
        "name": a.get("interfaceName") or "",
        "type": a.get("interfaceType") or "",
        "mac": a.get("macAddress") or "",
        "speed": a.get("negotiatedSpeed") or "",
        "duplex": a.get("duplex") or "",
        "ips": list(a.get("ipAddresses") or []),
        "oper_status": a.get("operationalStatus") or "",
        "admin_up": bool(a.get("adminStatus")),
        "last_modified": a.get("lastModified") or "",
        "device_id": parent.get("id") or "",
    }


def list_interfaces(client, tenant_ids: list[str], device_id: str, interface_type: str = "",
                    operational_status: str = "", limit: int = 100) -> dict:
    if not device_id.strip():
        raise ValueError("device_id is required")
    _check_enum("interface_type", interface_type, INTERFACE_TYPES)
    _check_enum("operational_status", operational_status, OPER_STATUSES)
    if limit < 1:
        raise ValueError("limit must be >= 1")
    limit = min(limit, MAX_LIMIT)

    params = {"tenants": ",".join(tenant_ids), "filter[parentDevice]": device_id}
    if interface_type:
        params["filter[interfaceType]"] = interface_type
    if operational_status:
        params["filter[operationalStatus]"] = operational_status
    items, upstream_truncated = client.get_all("/v1/inventory/interface/info", params, max_items=2000)
    rows = [project_interface(i) for i in items]
    out = rows[:limit]
    return ok(interfaces=out, total_count=len(rows), returned_count=len(out),
              truncated=upstream_truncated or len(rows) > len(out),
              counts_by_oper_status=dict(Counter(r["oper_status"] for r in rows)))
