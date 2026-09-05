"""Record real API responses into tests/fixtures/recorded/ (GITIGNORED — real
names/IPs). Then run the same suite with AUVIK_FIXTURES=recorded. GETs only."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auvik_client import AuvikClient  # noqa: E402
from credentials import load_credentials  # noqa: E402
from envelope import iso_ago  # noqa: E402

OUT = Path(__file__).resolve().parent / "fixtures" / "recorded"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    c = AuvikClient(load_credentials())
    tenants = c.tenants()
    t = ",".join(tenants)
    (OUT / "tenants.json").write_text(json.dumps(c.get("/v1/tenants"), indent=1), encoding="utf-8")
    alerts_body = c.get("/v1/alert/history/info", {"tenants": t, "page[first]": 50, "filter[dismissed]": "false"})
    (OUT / "alerts_page1.json").write_text(json.dumps(alerts_body, indent=1), encoding="utf-8")
    (OUT / "alerts_page2.json").write_text(json.dumps({"data": [], "links": {}}, indent=1), encoding="utf-8")
    first_alert = (alerts_body.get("data") or [{}])[0].get("id")
    if first_alert:
        (OUT / "alert_single.json").write_text(json.dumps(c.get(f"/v1/alert/history/info/{first_alert}"), indent=1), encoding="utf-8")
    dismissed = c.get("/v1/alert/history/info", {"tenants": t, "page[first]": 1, "filter[dismissed]": "true"})
    (OUT / "alert_single_dismissed.json").write_text(json.dumps({"data": (dismissed.get("data") or [{}])[0]}, indent=1), encoding="utf-8")
    devs = c.get("/v1/inventory/device/info", {"tenants": t, "page[first]": 100})
    (OUT / "devices.json").write_text(json.dumps(devs, indent=1), encoding="utf-8")
    dev_id = (devs.get("data") or [{}])[0].get("id")
    if dev_id:
        (OUT / "device_single.json").write_text(json.dumps(c.get(f"/v1/inventory/device/info/{dev_id}"), indent=1), encoding="utf-8")
        (OUT / "device_detail_single.json").write_text(json.dumps(c.get(f"/v1/inventory/device/detail/{dev_id}"), indent=1), encoding="utf-8")
        (OUT / "interfaces.json").write_text(json.dumps(c.get("/v1/inventory/interface/info", {"tenants": t, "filter[parentDevice]": dev_id, "page[first]": 100}), indent=1), encoding="utf-8")
        # filter[thruTime] is REQUIRED by the live API even though the OpenAPI
        # file calls it optional — see docs/API-DEVIATIONS.md D-4. Omitting it 400s.
        common = {"tenants": t, "filter[fromTime]": iso_ago(24), "filter[thruTime]": iso_ago(0),
                  "filter[interval]": "hour"}
        (OUT / "stat_interface.json").write_text(json.dumps(c.get("/v1/stat/interface/utilization", {**common, "filter[parentDevice]": dev_id}), indent=1), encoding="utf-8")
        (OUT / "stat_device.json").write_text(json.dumps(c.get("/v1/stat/device/cpuUtilization", {**common, "filter[deviceId]": dev_id}), indent=1), encoding="utf-8")
        (OUT / "stat_availability.json").write_text(json.dumps(c.get("/v1/stat/deviceAvailability/uptime", {**common, "filter[deviceId]": dev_id, "filter[interval]": "day"}), indent=1), encoding="utf-8")
    record_shape_probes(c, t, tenants)
    print("recorded to", OUT, "— NOTE: recorded IDs differ from synthetic ones; tests that hard-code AL-1/DEV-1 are synthetic-only by design.")


def _write(name: str, body) -> None:
    (OUT / name).write_text(json.dumps(body, indent=1), encoding="utf-8")


def _count(body) -> int:
    d = body.get("data")
    return len(d) if isinstance(d, list) else (1 if d else 0)


def _try(label: str, fn):
    """Record a body or the failure. A 500 is evidence too - print it, never hide it."""
    from auvik_client import AuvikUnavailable  # noqa: PLC0415
    try:
        body = fn()
        print(f"  {label:<26} items={_count(body)}")
        return body
    except AuvikUnavailable as exc:
        print(f"  {label:<26} UNAVAILABLE HTTP {exc.status}: {exc.detail[:160]}")
        return None


def record_shape_probes(c, t: str, tenants: list[str]) -> None:
    """Probe the endpoints whose live shape is not yet known. GETs only."""
    print("\n== endpoint probe ==")
    nets = _try("network/info", lambda: c.get("/v1/inventory/network/info", {"tenants": t, "page[first]": 50}))
    if nets is not None:
        _write("networks.json", nets)
    merged: list = []
    probes_ok = 0
    for status in ("degraded", "failed"):
        body = _try(f"component/info {status}", lambda s=status: c.get(
            "/v1/inventory/component/info", {"tenants": t, "filter[currentStatus]": s, "page[first]": 50}))
        if body is not None:
            probes_ok += 1
            _write(f"components_{status}.json", body)
            # Merged HERE, inside the loop, where `body` is the page that was
            # just fetched. This used to sit two blocks below and read the
            # LEAKED loop variable, so only the `failed` page was ever merged -
            # and then only if two unrelated probes further down happened to
            # succeed. An empty components.json produced that way is
            # indistinguishable from one produced by an estate with no faults:
            # a fabricated zero in the recorder itself, which is the exact
            # defect the rest of this repo exists to prevent.
            merged.extend(body.get("data") or [])
    # When degraded and failed both return 0, the component record shape AND the
    # live casing of currentStatus are unverified - and D-9 showed the vendor
    # file's camelCase enums come back Title-Case on lifecycle.
    # If live components read "Ok"/"Degraded", the server-side filter values the
    # tool sends ("degraded", "failed") may match nothing forever: a fabricated
    # zero. One small page of healthy components settles the shape and casing.
    ok_body = _try("component/info (status=ok, shape+casing probe)", lambda: c.get(
        "/v1/inventory/component/info", {"tenants": t, "filter[currentStatus]": "ok", "page[first]": 25}))
    if ok_body is not None:
        _write("components_ok.json", ok_body)
        from collections import Counter  # noqa: PLC0415
        items = ok_body.get("data") or []
        print("  component currentStatus values:", dict(Counter((i.get("attributes") or {}).get("currentStatus") for i in items)))
        print("  component componentType values:", dict(Counter((i.get("attributes") or {}).get("componentType") for i in items)))
        if items:
            first = items[0]
            print("  component attrs:", sorted((first.get("attributes") or {}).keys()),
                  "rels:", sorted((first.get("relationships") or {}).keys()))
        unfiltered = _try("component/info (no filter)", lambda: c.get(
            "/v1/inventory/component/info", {"tenants": t, "page[first]": 25}))
        if unfiltered is not None:
            print("  unfiltered currentStatus values:", dict(Counter(
                (i.get("attributes") or {}).get("currentStatus") for i in unfiltered.get("data") or [])))
    # Alias under the synthetic tier's filename so `AUVIK_FIXTURES=recorded`
    # loads the same names. This file CAN legitimately be empty - an estate with
    # nothing degraded and nothing failed records nothing here, and the
    # tier-invariant test skips on that rather than passing vacuously (the D-5
    # lesson). But it is only written when at least one probe actually answered:
    # an empty file written after both probes FAILED would claim a real zero the
    # run never established.
    if probes_ok:
        _write("components.json", {"data": merged, "links": {}})
    else:
        print("  !! both component probes failed - components.json NOT written. An empty file "
              "here would be indistinguishable from an estate with no faults.")
    cfg = _try("configuration 7d", lambda: c.get(
        "/v1/inventory/configuration", {"tenants": t, "filter[backupTimeAfter]": iso_ago(168), "page[first]": 100}))
    if cfg is not None:
        _write("configurations_7d.json", cfg)
        _write("configurations.json", cfg)  # synthetic-tier filename alias
    aud = _try("entity/audit 30d", lambda: c.get(
        "/v1/inventory/entity/audit", {"tenants": t, "filter[modifiedAfter]": iso_ago(720), "page[first]": 50}))
    if aud is not None:
        _write("audits_30d.json", aud)
        _write("audits.json", aud)          # synthetic-tier filename alias
    lc = _try("device/lifecycle", lambda: c.get("/v1/inventory/device/lifecycle", {"tenants": t, "page[first]": 50}))
    if lc is not None:
        _write("lifecycle.json", lc)
    wr = _try("device/warranty", lambda: c.get("/v1/inventory/device/warranty", {"tenants": t, "page[first]": 50}))
    if wr is not None:
        _write("warranty.json", wr)
    ifs = c.get("/v1/inventory/interface/info", {"tenants": t, "page[first]": 1})
    iid = ((ifs.get("data") or [{}])[0]).get("id")
    if iid:
        one = _try("interface/info/{id}", lambda: c.get(f"/v1/inventory/interface/info/{iid}"))
        if one is not None:
            _write("interface_single.json", one)

    # --- is the lifecycle endpoint worth building a tool on? ----------------
    if lc is not None:
        informative = 0
        for item in lc.get("data") or []:
            a = item.get("attributes") or {}
            vals = {a.get(k) or "" for k in ("salesAvailability", "softwareMaintenanceStatus",
                                             "securitySoftwareMaintenanceStatus", "lastSupportStatus")}
            if vals - {"empty", "unpublished", ""}:
                informative += 1
        print(f"  lifecycle records with informative status: {informative} of {_count(lc)} "
              f"-> {'informative, worth a tool' if informative else 'no usable vendor data on this tenant'}")

    # --- config backup cadence (the evidence behind tools/configs.py) -------
    if cfg is not None:
        by_dev: dict[str, list[str]] = {}
        for item in cfg.get("data") or []:
            did = (((item.get("relationships") or {}).get("device") or {}).get("data") or {}).get("id") or "?"
            by_dev.setdefault(did, []).append((item.get("attributes") or {}).get("backupTime") or "")
        print(f"  devices with a backup in 7d: {len(by_dev)}  backups: {_count(cfg)}")
        for did in list(by_dev)[:3]:
            month = c.get("/v1/inventory/configuration", {"tenants": t, "filter[deviceId]": did,
                                                          "filter[backupTimeAfter]": iso_ago(720), "page[first]": 300})
            times = sorted((i.get("attributes") or {}).get("backupTime") or "" for i in month.get("data") or [])
            print(f"    device {did[:12]}... backups in 30d: {len(times)}  first={times[0] if times else ''} last={times[-1] if times else ''}")
            if len(times) >= 2:
                gaps = [times[i + 1][:16] + " <- " + times[i][:16] for i in range(min(4, len(times) - 1))]
                print("      gaps:", " | ".join(gaps))
        # Alerts that mention configuration in the same 7-day window, for the consistency comparison.
        al = c.get("/v1/alert/history/info", {"tenants": t, "filter[detectedTimeAfter]": iso_ago(168), "page[first]": 300})
        cfg_alerts = [i for i in al.get("data") or []
                      if "onfiguration" in ((i.get("attributes") or {}).get("name") or "")
                      or "onfiguration" in ((i.get("attributes") or {}).get("description") or "")]
        print(f"  alerts mentioning 'configuration' in 7d: {len(cfg_alerts)} (of {_count(al)} on the first page) "
              f"vs devices with a new backup: {len(by_dev)}")


if __name__ == "__main__":
    main()
