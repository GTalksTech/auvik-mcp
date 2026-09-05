"""Live, READ-ONLY re-verification of this server's whole tool surface.

`live_smoke.py` covers only a few tools. The exercise that actually caught D-4 -
three tools dead on arrival because the live API requires a parameter the
OpenAPI file marks optional - lived in a scratch file and was lost. This is that
exercise, committed, so anyone can re-check every read tool against the real
Auvik in one command after a vendor change:

    .venv\\Scripts\\python.exe scripts\\live_read_all.py

Exit code 0 only if every read tool returned `ok`.

**It cannot write.** It issues no POST, calls no write tool, and takes no
argument that would enable one; the single-write-site test scans this file, and
`tests/test_live_read_all.py` drives the whole runner offline against the
committed fixtures so the coverage claim is tested rather than trusted.

IDs are discovered from the estate rather than hard-coded, so this keeps working
as the network changes. When discovery itself fails, the tools that needed an
ID report **that** upstream failure - not a fabricated argument error - because
the distinction between "Auvik is down" and "you called it wrong" is the whole
point of the envelope.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auvik_client import AuvikClient, AuvikUnavailable  # noqa: E402
from credentials import CredentialsMissing, load_credentials  # noqa: E402
from envelope import unavailable  # noqa: E402
from tools import alerts, audits, components, configs, devices, interfaces, lifecycle, networks, stats  # noqa: E402

READ_TOOLS = (
    "auvik_list_alerts",
    "auvik_get_alert",
    "auvik_find_device",
    "auvik_get_device",
    "auvik_list_interfaces",
    "auvik_interface_stats",
    "auvik_device_stats",
    "auvik_device_availability",
    "auvik_alert_summary",
    "auvik_list_networks",
    "auvik_component_health",
    "auvik_config_changes",
    "auvik_remote_access_audit",
    "auvik_device_lifecycle",
)


def _summary(tool: str, res: dict) -> str:
    """One line per tool: enough to see that real data came back, short enough to paste."""
    if res["data_status"] != "ok":
        return res.get("detail", "")[:160]
    if tool == "auvik_list_alerts":
        return (f"total_count={res['total_count']} returned={res['returned_count']} "
                f"by_severity={json.dumps(res['counts_by_severity'])} sites={len(res['sites'])}")
    if tool == "auvik_get_alert":
        a = res["alert"]
        return f"{a['severity']} {a['name'][:40]!r} site={a['site']!r} device={a['device_name']!r}"
    if tool == "auvik_find_device":
        return f"total_count={res['total_count']} sites={res['sites']}"
    if tool == "auvik_get_device":
        d = res["device"]
        return f"{d['name']!r} {d['type']} {d['online_status']} site={d['site']!r} managed={d['managed']}"
    if tool == "auvik_list_interfaces":
        # Indexed, not .get(...): the first live run printed `by_status={}` for
        # a device with plenty of interfaces, because this read `counts_by_status` while
        # the tool returns `counts_by_oper_status`, and the default turned a typo
        # into a confident zero. A probe that cannot describe a response must
        # fail loudly rather than print a plausible blank.
        return f"total_count={res['total_count']} by_oper_status={json.dumps(res['counts_by_oper_status'])}"
    if tool == "auvik_device_lifecycle":
        return f"problem_devices={res['total_count']} by_last_support={json.dumps(res['counts_by_last_support'])}"
    if tool == "auvik_remote_access_audit":
        return (f"sessions={res['total_count']} by_category={json.dumps(res['counts_by_category'])} "
                f"users={len(res['counts_by_user'])}")
    if tool == "auvik_config_changes":
        return f"devices={res['device_count']} backups={res['backup_count']} window={res['window_hours']}h"
    if tool == "auvik_component_health":
        return (f"total_count={res['total_count']} by_status={json.dumps(res['counts_by_status'])} "
                f"by_type={json.dumps(res['counts_by_type'])}")
    if tool == "auvik_list_networks":
        return (f"total_count={res['total_count']} by_type={json.dumps(res['counts_by_type'])} "
                f"by_scan={json.dumps(res['counts_by_scan_status'])}")
    if tool == "auvik_alert_summary":
        top = res["groups"][0] if res["groups"] else {}
        return (f"alerts={res['alert_count']} active={res['active_alert_count']} groups={res['group_count']} "
                f"top={top.get('alert_name', '')[:40]!r} flapping={sum(1 for g in res['groups'] if g['flapping'])}")
    return (f"entity_count={res['entity_count']}/{res['entity_count_total']} "
            f"series_total={res['series_total']} with_data={res['series_with_data']} "
            f"empty={res['series_empty']} requested_stat="
            f"{(res['series_for'][0]['requested_stat'] if res['series_for'] else '')!r} "
            f"stat={(res['series_for'][0]['stat'] if res['series_for'] else '')!r}")


def run_all(client, tenant_ids: list[str], out=print) -> list[dict]:
    """Drive every read tool. Returns one record per tool, in READ_TOOLS order."""
    cache = devices.DeviceCache(client, tenant_ids)
    # The same tenant map the MCP tool passes, so this probe exercises the real
    # path rather than a degraded one that reports no sites.
    try:
        tenant_sites = client.tenant_map()
    except Exception:  # noqa: BLE001 — a probe must not abort on discovery
        tenant_sites = {}
    ctx: dict[str, str] = {"alert_id": "", "device_id": "", "interface_id": "", "blocker": ""}
    results: list[dict] = []

    def call(tool: str, fn, needs: tuple[str, ...] = ()) -> dict:
        missing = [k for k in needs if not ctx[k]]
        t0 = time.perf_counter()
        if missing:
            # Never turn an upstream outage into an argument error: report the
            # failure that actually prevented discovery.
            res = unavailable(ctx["blocker"] or f"no {'/'.join(missing)} discovered in this estate")
        else:
            try:
                res = fn()
            except AuvikUnavailable as exc:
                res = unavailable(str(exc))
                ctx["blocker"] = ctx["blocker"] or str(exc)
            except Exception as exc:  # noqa: BLE001 - a probe must not abort on one tool
                res = unavailable(f"{type(exc).__name__}: {exc}")
        rec = {"tool": tool, "data_status": res["data_status"], "detail": res.get("detail", ""),
               "ms": round((time.perf_counter() - t0) * 1000)}
        results.append(rec)
        flag = "ok " if rec["data_status"] == "ok" else "!! "
        out(f"  {flag}{tool:<28} {rec['ms']:>6} ms  {_summary(tool, res)}")
        return res

    res = call("auvik_list_alerts", lambda: alerts.list_alerts(client, tenant_ids, cache.name_of,
                                                               tenant_sites=tenant_sites, hours=168, limit=50))
    for a in res.get("alerts", []):
        ctx["alert_id"] = ctx["alert_id"] or a["alert_id"]
        if a["device_id"]:
            ctx["device_id"] = ctx["device_id"] or a["device_id"]

    call("auvik_get_alert", lambda: alerts.get_alert(client, cache.name_of, ctx["alert_id"]), needs=("alert_id",))

    res = call("auvik_find_device", lambda: devices.find_device(cache, limit=5))
    for d in res.get("devices", []):
        ctx["device_id"] = ctx["device_id"] or d["device_id"]

    call("auvik_get_device", lambda: devices.get_device(client, ctx["device_id"]), needs=("device_id",))

    res = call("auvik_list_interfaces",
               lambda: interfaces.list_interfaces(client, tenant_ids, ctx["device_id"], limit=50),
               needs=("device_id",))
    for i in res.get("interfaces", []):
        ctx["interface_id"] = ctx["interface_id"] or i["interface_id"]

    call("auvik_interface_stats",
         lambda: stats.interface_stats(client, tenant_ids, "utilization", device_id=ctx["device_id"],
                                       hours=6, interval="hour"),
         needs=("device_id",))
    call("auvik_device_stats",
         lambda: stats.device_stats(client, tenant_ids, "cpuUtilization", ctx["device_id"],
                                    hours=6, interval="hour"),
         needs=("device_id",))
    call("auvik_device_availability",
         lambda: stats.device_availability(client, tenant_ids, "uptime", ctx["device_id"],
                                           hours=168, interval="day"),
         needs=("device_id",))

    call("auvik_alert_summary", lambda: alerts.summarize_alerts(client, tenant_ids, cache.name_of, hours=168,
                                                                tenant_sites=tenant_sites,
                                                                resolve_interface=cache.interface_parent))
    call("auvik_list_networks", lambda: networks.list_networks(client, tenant_ids, limit=20, tenant_sites=tenant_sites))
    call("auvik_component_health", lambda: components.component_health(client, tenant_ids, tenant_sites=tenant_sites))
    call("auvik_config_changes", lambda: configs.config_changes(client, tenant_ids, cache.name_of, hours=168,
                                                                 tenant_sites=tenant_sites))
    call("auvik_remote_access_audit", lambda: audits.remote_access_audit(client, tenant_ids, hours=720,
                                                                          tenant_sites=tenant_sites))
    call("auvik_device_lifecycle", lambda: lifecycle.device_lifecycle(client, tenant_ids, tenant_sites=tenant_sites))
    return results


def report(results: list[dict], out=print) -> int:
    bad = [r for r in results if r["data_status"] != "ok"]
    out(f"\n{len(results) - len(bad)}/{len(results)} read tools returned ok"
        f" ({sum(r['ms'] for r in results)} ms total)")
    for r in bad:
        out(f"  FAILED {r['tool']}: {r['data_status']} — {r['detail'][:200]}")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print(f"live_read_all.py takes no arguments (got {argv}); it is read-only by construction "
              "and has no dismissal mode.")
        return 2
    try:
        creds = load_credentials()
    except CredentialsMissing as exc:
        print(f"BLOCKED: {exc}")
        return 2
    client = AuvikClient(creds)
    try:
        tenants = client.tenants()
    except AuvikUnavailable as exc:
        print(f"UNAVAILABLE fetching tenants: {exc}")
        return 1
    print(f"region={creds.region} key={creds.fingerprint} tenants={len(tenants)}\n")
    return report(run_all(client, tenants))


if __name__ == "__main__":
    raise SystemExit(main())
