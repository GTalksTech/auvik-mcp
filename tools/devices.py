"""Device tools. Auvik has no server-side name filter, so find_device works over
a TTL-cached, projected copy of the tenant's device list; the same cache
resolves device_id → name for alert projections."""
from __future__ import annotations

import threading
import time
from typing import Callable

from auvik_client import AuvikUnavailable
from envelope import TTLCache, ok

DEVICE_TYPES = (
    "unknown", "switch", "l3Switch", "router", "accessPoint", "firewall", "workstation", "server", "storage",
    "printer", "copier", "hypervisor", "multimedia", "phone", "tablet", "handheld", "virtualAppliance", "bridge",
    "controller", "hub", "modem", "ups", "module", "loadBalancer", "camera", "telecommunications", "packetProcessor",
    "chassis", "airConditioner", "virtualMachine", "pdu", "ipPhone", "backhaul", "internetOfThings", "voipSwitch",
    "stack", "backupDevice", "timeClock", "lightingDevice", "audioVisual", "securityAppliance", "utm", "alarm",
    "buildingManagement", "ipmi", "thinAccessPoint", "thinClient", "subnet",
)
ONLINE_STATUSES = ("online", "offline", "unreachable", "testing", "unknown", "dormant", "notPresent", "lowerLayerDown")
MAX_LIMIT = 200

# Cold-start economics of the estate fetch, measured live against a production
# tenant. Walking the device inventory at the vendor's default page[first]=100
# is round-trip bound; raising the page size collapses most of that cost, and
# 1000 is the KNEE of the curve — the ID set comes back identical at every page
# size tried, the OpenAPI file documents no maximum for page[first] (only the
# default of 100), and past 1000 the remaining cost is server-side work and
# transfer rather than round trips, so a larger number buys almost nothing.
DEVICE_PAGE_SIZE = 1000
# The estate changes on the order of days, not minutes. At the old 5-minute TTL a
# working session re-paid the fetch 12 times an hour; 30 minutes is still far
# shorter than the rate at which devices are added.
DEVICE_CACHE_TTL = 1800


def _check_enum(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value and value not in allowed:
        raise ValueError(f"invalid {name} {value!r}; valid: {', '.join(allowed)}")


def tenant_of(item: dict) -> tuple[str, str]:
    """(tenant_id, site) from a JSON:API record's tenant relationship.

    Auvik inlines `attributes.domainPrefix` on the relationship itself, so the
    site name costs no extra call. domainPrefix is the only site name reliably
    available - the richer /v1/tenants/detail answered HTTP 500 on the tenant
    this was built against - so the Boston office might be spelled `acme-bos`, and
    find_device reports the list rather than expecting a caller to guess it.
    A missing tenant degrades to ("", ""), never None.
    """
    data = ((item.get("relationships") or {}).get("tenant") or {}).get("data") or {}
    return data.get("id") or "", (data.get("attributes") or {}).get("domainPrefix") or ""


def project_device(item: dict) -> dict:
    a = item.get("attributes") or {}
    tenant_id, site = tenant_of(item)
    return {
        "device_id": item.get("id") or "",
        "tenant_id": tenant_id,
        "site": site,
        "name": a.get("deviceName") or "",
        "type": a.get("deviceType") or "",
        "ips": list(a.get("ipAddresses") or []),
        "make_model": a.get("makeModel") or "",
        "vendor": a.get("vendorName") or "",
        "software_version": a.get("softwareVersion") or "",
        "firmware_version": a.get("firmwareVersion") or "",
        "serial": a.get("serialNumber") or "",
        "description": a.get("description") or "",
        "online_status": a.get("onlineStatus") or "",
        "last_seen": a.get("lastSeenTime") or "",
        "last_modified": a.get("lastModified") or "",
    }


def project_detail(item: dict) -> dict:
    a = item.get("attributes") or {}
    disc = a.get("discoveryStatus") or {}
    rel = item.get("relationships") or {}

    def _data(key: str) -> list:
        return list((rel.get(key) or {}).get("data") or [])

    backups = [((c.get("attributes") or {}).get("backupTime") or "") for c in _data("configurations")]
    return {
        "managed": bool(a.get("manageStatus")),
        "snmp_discovery": disc.get("snmp") or "",
        "wmi_discovery": disc.get("wmi") or "",
        "login_discovery": disc.get("login") or "",
        "vmware_discovery": disc.get("vmware") or "",
        "traffic_insights": a.get("trafficInsightsStatus") or "",
        # Topology and backup metadata ride along on the same detail call that
        # was already being made. The live API populated connectedDevices on the
        # very first device recorded; this was being fetched and discarded.
        "connected_devices": [{"device_id": d.get("id") or "", "name": (d.get("attributes") or {}).get("deviceName") or ""}
                              for d in _data("connectedDevices")],
        "interface_count": len(_data("interfaces")),
        "component_count": len(_data("components")),
        "config_backup_count": len(backups),
        "last_config_backup": max(backups) if backups else "",
    }


class DeviceCache:
    def __init__(self, client, tenant_ids: list[str], ttl: float = DEVICE_CACHE_TTL,
                 clock: Callable[[], float] = time.monotonic):
        self.client, self.tenant_ids = client, tenant_ids
        self._cache = TTLCache(ttl=ttl, clock=clock)
        # Single-flight. The estate fetch takes seconds, not milliseconds, and
        # the background warm at startup runs concurrently with whatever tool the
        # agent calls first, so an unguarded TTLCache would let both miss and
        # issue two full estate fetches for one fill. Held across the fetch on
        # purpose: a waiter should block on the in-flight result rather than
        # starting a second one. A cache hit under
        # an uncontended lock costs orders of magnitude less than the linear
        # scan name_of() already does.
        self._lock = threading.Lock()

    def _fetch(self) -> list[dict]:
        items, _ = self.client.get_all("/v1/inventory/device/info", {"tenants": ",".join(self.tenant_ids)},
                                       page_size=DEVICE_PAGE_SIZE, max_items=5000)
        return [project_device(i) for i in items]

    def all(self) -> list[dict]:
        with self._lock:
            return self._cache.get("devices", self._fetch)

    _BLANK_PARENT = {"device_id": "", "device_name": "", "interface_name": "", "resolved": False}

    def interface_parent(self, interface_id: str) -> dict:
        """Resolve an interface to its device: one GET per unique interface, cached
        for the device-cache TTL. A failure is NOT cached (so a transient error is
        retried next call) and never raises - enrichment must not turn a
        successful alert list into `unavailable`. Callers count `resolved=False`
        and say so in `detail`."""
        if not interface_id:
            return dict(self._BLANK_PARENT)
        key = f"iface:{interface_id}"
        try:
            with self._lock:
                return self._cache.get(key, lambda: self._fetch_parent(interface_id))
        except AuvikUnavailable:
            return dict(self._BLANK_PARENT)

    def _fetch_parent(self, interface_id: str) -> dict:
        data = self.client.get(f"/v1/inventory/interface/info/{interface_id}").get("data") or {}
        parent = ((data.get("relationships") or {}).get("parentDevice") or {}).get("data") or {}
        did = parent.get("id") or ""
        name = next((d["name"] for d in self._cache.get("devices", self._fetch) if d["device_id"] == did), "")
        return {"device_id": did, "device_name": name,
                "interface_name": (data.get("attributes") or {}).get("interfaceName") or "", "resolved": True}

    def name_of(self, device_id: str) -> str:
        if not device_id:
            return ""
        return next((d["name"] for d in self.all() if d["device_id"] == device_id), "")

    def clear(self) -> None:
        self._cache.clear()


def find_device(cache: DeviceCache, name: str = "", ip: str = "", device_type: str = "",
                vendor: str = "", online_status: str = "", site: str = "", limit: int = 50) -> dict:
    _check_enum("device_type", device_type, DEVICE_TYPES)
    _check_enum("online_status", online_status, ONLINE_STATUSES)
    if limit < 1:
        raise ValueError("limit must be >= 1")
    limit = min(limit, MAX_LIMIT)
    n, v, s = name.lower(), vendor.lower(), site.lower()
    estate = cache.all()
    rows = [
        d for d in estate
        if (not n or n in d["name"].lower())
        and (not ip or ip in d["ips"])
        and (not device_type or d["type"] == device_type)
        and (not v or v in d["vendor"].lower())
        and (not online_status or d["online_status"] == online_status)
        and (not s or s in d["site"].lower())
    ]
    out = rows[:limit]
    sites = sorted({d["site"] for d in estate if d["site"]})
    # A zero from an unmatched site IS a real zero, but an agent cannot tell a
    # wrong site name from a genuinely empty site - and it cannot guess the name
    # either, since the prefix need not resemble the place name. Name the sites
    # that exist rather than leaving it to infer. Same reasoning as the rule
    # that a zero must never be confusable with an absence of data.
    detail = ""
    if site and not rows:
        detail = (f"no device matches site {site!r}; sites in this estate: {', '.join(sites)}"
                  if sites else f"no device matches site {site!r}; no device carries a site")
    return ok(detail=detail, devices=out, total_count=len(rows), returned_count=len(out),
              truncated=len(rows) > len(out), sites=sites)


def get_device(client, device_id: str) -> dict:
    if not device_id.strip():
        raise ValueError("device_id is required")
    info = project_device(client.get(f"/v1/inventory/device/info/{device_id}").get("data") or {})
    detail = project_detail(client.get(f"/v1/inventory/device/detail/{device_id}").get("data") or {})
    return ok(device={**info, **detail})
