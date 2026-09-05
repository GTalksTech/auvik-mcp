"""Configuration-change detection from backup metadata.

Auvik's API exposes backupTime and isRunning per configuration version and
nothing else - no config text and no diff. What it does give is the change
SIGNAL: a device with a new backup version in the window had its configuration
change (or was first backed up) in that window, whether or not Auvik's own
config-change alert fired.

Two things were measured live against a production tenant, and both are why
this reads backup metadata directly rather than filtering alerts:

* Backups are CHANGE-DRIVEN, not scheduled. The gaps between one device's
  versions were wildly uneven - an hour, then days, then an hour again - and
  across a fleet, some devices accumulated many versions over a month while
  others accumulated barely any. A nightly schedule would instead give every
  device the same evenly spaced count. So a new version really is evidence of
  a change, not of the clock.
* Auvik's own config-change ALERTS under-report. More devices had a new backup
  in the window than there were alerts mentioning "configuration" in the same
  window.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from envelope import iso_ago, ok
from tools.common import check_limit, scope_sites
from tools.devices import tenant_of

MAX_BACKUPS_PER_DEVICE = 20


def config_changes(client, tenant_ids: list[str], resolve_device_name: Callable[[str], str],
                   hours: int = 168, site: str = "", device_id: str = "", limit: int = 100,
                   tenant_sites: dict[str, str] | None = None, now: datetime | None = None,
                   max_backups_per_device: int = MAX_BACKUPS_PER_DEVICE) -> dict:
    if hours < 0:
        raise ValueError("hours must be >= 0 (0 = all backups ever, which needs device_id)")
    device_id = device_id.strip()
    if hours == 0 and not device_id:
        raise ValueError("hours=0 lists every backup Auvik holds; give device_id for one device's full history")
    limit = check_limit(limit)
    sc = scope_sites(tenant_ids, tenant_sites, site)
    if sc.unmatched:
        return ok(devices_changed=[], device_count=0, total_count=0, returned_count=0, truncated=False,
                  backup_count=0, window_hours=hours, **sc.site_key(), detail=sc.unmatched_detail(site))

    params: dict = {"tenants": ",".join(sc.tenant_ids)}
    if device_id:
        params["filter[deviceId]"] = device_id
    if hours:
        params["filter[backupTimeAfter]"] = iso_ago(hours, now)
    items, upstream_truncated = client.get_all("/v1/inventory/configuration", params, max_items=5000)

    by_dev: dict[str, dict] = {}
    for item in items:
        a = item.get("attributes") or {}
        dev = (((item.get("relationships") or {}).get("device") or {}).get("data")) or {}
        did = dev.get("id") or ""
        tenant_id, site_name = tenant_of(item)
        g = by_dev.get(did)
        if g is None:
            # The inline relationship name costs nothing; the device cache is the
            # fallback when Auvik omits it.
            name = (dev.get("attributes") or {}).get("deviceName") or resolve_device_name(did)
            g = by_dev[did] = {"device_id": did, "device_name": name or "", "tenant_id": tenant_id, "site": site_name,
                               "backup_count": 0, "running_backup_count": 0, "first_backup": "", "last_backup": "",
                               "backups": [], "backups_truncated": False}
        g["backup_count"] += 1
        g["running_backup_count"] += 1 if a.get("isRunning") else 0
        g["backups"].append({"backup_time": a.get("backupTime") or "", "is_running": bool(a.get("isRunning"))})
    for g in by_dev.values():
        g["backups"].sort(key=lambda b: b["backup_time"], reverse=True)
        g["first_backup"], g["last_backup"] = g["backups"][-1]["backup_time"], g["backups"][0]["backup_time"]
        # backup_count keeps the true total; only the listing is capped.
        g["backups_truncated"] = len(g["backups"]) > max_backups_per_device
        g["backups"] = g["backups"][:max_backups_per_device]

    ordered = sorted(by_dev.values(), key=lambda g: g["last_backup"], reverse=True)
    out = ordered[:limit]
    return ok(devices_changed=out, device_count=len(ordered), total_count=len(ordered), returned_count=len(out),
              truncated=upstream_truncated or len(ordered) > len(out), backup_count=len(items),
              window_hours=hours, **sc.site_key())
