"""Auvik MCP server — fifteen tools (fourteen reads, one guarded write) over
stdio (Claude Code, Claude Desktop) or streamable HTTP (any MCP client, and
chat platforms such as Copilot Studio, behind a container).

One JSON log line per call, an explicit data_status envelope on every answer,
X-API-Key inbound auth in HTTP mode, a custom unauthenticated GET /health, and
stateless HTTP so the process can be replaced at any time. README.md explains
the design; docs/API-DEVIATIONS.md explains where the live Auvik API differs
from its own published OpenAPI file.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field

from fastmcp import FastMCP

import envelope as env
from auvik_client import AuvikClient, AuvikUnavailable
from credentials import load_credentials
from envelope import envelope
from tools import alerts, audits, components, configs, devices, interfaces, lifecycle, networks, stats

mcp = FastMCP(env.APP_NAME)

# ------------------------------------------------------------------ startup state


@dataclass
class _State:
    client: AuvikClient | None = None
    tenant_ids: list[str] = field(default_factory=list)
    tenant_sites: dict[str, str] = field(default_factory=dict)
    device_cache: devices.DeviceCache | None = None
    error: str = ""
    last_attempt: float = 0.0
    retry_seconds: float = 60.0


_state = _State()


def _ensure_started() -> None:
    if _state.client is not None:
        return
    now = time.monotonic()
    if _state.error and now - _state.last_attempt < _state.retry_seconds:
        raise AuvikUnavailable(0, f"startup failed: {_state.error}")
    _state.last_attempt = now
    try:
        creds = load_credentials()
        client = AuvikClient(creds)
        # One /v1/tenants call gives both the ids and the domainPrefixes; the
        # prefix is what `site` means everywhere in this server.
        tenant_sites = client.tenant_map()
        tenant_ids = list(tenant_sites)
        if not tenant_ids:
            raise AuvikUnavailable(0, "Auvik returned zero tenants for this credential")
        _state.client, _state.tenant_ids, _state.error = client, tenant_ids, ""
        _state.tenant_sites = tenant_sites
        _state.device_cache = devices.DeviceCache(client, tenant_ids)
        print(json.dumps({"ts": env.now_iso(), "app": env.APP_NAME, "event": "startup", "tenants": len(tenant_ids),
                          "key_fingerprint": creds.fingerprint, "region": creds.region}), file=sys.stderr, flush=True)
    except Exception as exc:  # noqa: BLE001 — recorded, retried later
        _state.error = f"{type(exc).__name__}: {exc}"
        raise AuvikUnavailable(0, f"startup failed: {_state.error}") from exc


def get_client() -> AuvikClient:
    _ensure_started()
    return _state.client  # type: ignore[return-value]


def get_tenants() -> list[str]:
    _ensure_started()
    return _state.tenant_ids


def get_tenant_sites() -> dict[str, str]:
    _ensure_started()
    return _state.tenant_sites


def get_device_cache() -> devices.DeviceCache:
    _ensure_started()
    return _state.device_cache  # type: ignore[return-value]


def reset_state_for_tests(client, tenant_ids: list[str], tenant_sites: dict[str, str] | None = None) -> None:
    """Tests inject a FakeClient; passing None resets to cold start."""
    global _state
    _state = _State()
    if client is not None:
        _state.client, _state.tenant_ids = client, list(tenant_ids)
        if tenant_sites is None:
            try:
                tenant_sites = client.tenant_map()
            except Exception:  # noqa: BLE001 — a fake without the method is fine
                tenant_sites = {}
        _state.tenant_sites = dict(tenant_sites)
        _state.device_cache = devices.DeviceCache(client, list(tenant_ids))


def _resolve_name(device_id: str) -> str:
    return get_device_cache().name_of(device_id)


def _resolve_interface(interface_id: str) -> dict:
    return get_device_cache().interface_parent(interface_id)


def warm_start() -> None:
    """Pay the device-estate fetch before anyone asks a question.

    Alert projections resolve device_id -> device_name through DeviceCache, and
    on a large estate that fetch is slow enough to be plainly visible on a cold
    process — landing on "what alerts fired today?", the likeliest first question.
    Nothing here is on a request path, so the cost becomes invisible instead of
    merely smaller.

    Best-effort by contract. A stdio process must still start with no credential
    at all, and a warm that fails must leave the server exactly as cold as it
    was: _ensure_started() records the failure and starts a 60s
    retry timer so repeated tool calls do not hammer a broken credential, but
    that timer is for callers who have already been told "unavailable". Letting
    a background thread arm it would make the user's FIRST call return a cached
    failure it never triggered — and suppress the retry that would have worked
    if, say, the keyring unlocked in between.
    """
    try:
        _ensure_started()
        get_device_cache().all()
    except Exception:  # noqa: BLE001 — a warm failure is never the caller's problem
        _state.error = ""
        _state.last_attempt = 0.0


def start_background_warm() -> threading.Thread:
    """Daemon so a process that exits before the warm finishes is not held open."""
    t = threading.Thread(target=warm_start, name="auvik-warm", daemon=True)
    t.start()
    return t


# ------------------------------------------------------------------------- tools
# Param rules (Copilot Studio): primitives only, defaults on every optional
# param, no Optional/unions, enum values spelled out in the docstring.


@mcp.tool
@envelope
def auvik_list_alerts(severity: str = "", status: str = "", include_dismissed: bool = False,
                      device_id: str = "", hours: int = 24, limit: int = 50, site: str = "",
                      name: str = "") -> dict:
    """List Auvik alerts (default: not dismissed, detected in the last 24 hours).
    severity: emergency | critical | warning | info | unknown (empty = all).
    status: created | resolved | paused | unpaused (empty = all).
    include_dismissed: true to include already-dismissed alerts.
    device_id: Auvik device ID to restrict to that device's alerts.
    hours: window ending now (0 = no window). limit: max rows returned (1-200);
    total_count always reports the full match count and truncated flags a cut.
    name: case-insensitive substring matched against the alert name AND description (the name
    often still holds Auvik's raw template, so the description is the reliable field).
    site: case-insensitive substring of the Auvik tenant domain prefix, which is how this Auvik
    names a client site - NOT the place name, so the Boston office might be "acme-bos". Filtering happens at the
    API, so a site query costs less than fetching everything. Every response lists the sites under
    `sites`; read that rather than guessing. Note the parent (multiClient) tenant returns every
    site's alerts, which is correct - it is the whole organisation."""
    return alerts.list_alerts(get_client(), get_tenants(), _resolve_name, severity=severity, status=status,
                              include_dismissed=include_dismissed, device_id=device_id, hours=hours,
                              limit=limit, site=site, name=name, tenant_sites=get_tenant_sites(),
                              resolve_interface=_resolve_interface)


@mcp.tool
@envelope
def auvik_get_alert(alert_id: str) -> dict:
    """Get one Auvik alert by ID: name, severity, status, detected time, description,
    the device/interface/network it relates to (with device name), related alert,
    and any external tickets. Reading an alert - with this tool or with
    auvik_list_alerts - is what makes it eligible for dismissal. auvik_alert_summary
    does NOT count: it returns groups, and a group is not an alert. To dismiss from a
    summary, call this tool on the group's latest_alert_id first."""
    return alerts.get_alert(get_client(), _resolve_name, alert_id, resolve_interface=_resolve_interface)


@mcp.tool
@envelope
def auvik_alert_summary(hours: int = 24, site: str = "", severity: str = "", include_dismissed: bool = False,
                        limit: int = 50) -> dict:
    """Roll-up of Auvik alerts for triage: one row per (alert name, device/interface, site) with count,
    active_count, resolved_count, first_seen, last_seen, highest severity, latest_alert_id and a
    flapping flag (3+ occurrences in the window). Use this BEFORE auvik_list_alerts when the question
    is "what is going on?" - a raw list is often dominated by repeated collector reconnect events.
    Groups sort by active_count, then severity, then count. hours: window ending now (0 = no window).
    severity: emergency | critical | warning | info | unknown (empty = all). include_dismissed: include
    dismissed alerts. site: substring of the Auvik tenant domain prefix (the Boston office might be "acme-bos");
    `sites` lists the valid ones. To dismiss, call auvik_get_alert on latest_alert_id first."""
    return alerts.summarize_alerts(get_client(), get_tenants(), _resolve_name, severity=severity,
                                   include_dismissed=include_dismissed, hours=hours, limit=limit, site=site,
                                   tenant_sites=get_tenant_sites(), resolve_interface=_resolve_interface)


@mcp.tool
@envelope
def auvik_dismiss_alert(alert_id: str, reason: str) -> dict:
    """Dismiss (close) one Auvik alert. THE ONLY WRITE this server performs.
    reason: required — why the alert is being dismissed; recorded in the local audit log.
    The alert must have been returned by auvik_list_alerts or auvik_get_alert in this
    session, and must not already be dismissed; otherwise the call is refused and no
    change is made. Returns data_status refused | unavailable | ok."""
    return alerts.dismiss_alert(get_client(), _resolve_name, alert_id, reason, caller=env.caller_label.get(),
                                resolve_interface=_resolve_interface)


@mcp.tool
@envelope
def auvik_find_device(name: str = "", ip: str = "", device_type: str = "", vendor: str = "",
                      online_status: str = "", site: str = "", limit: int = 50) -> dict:
    """Find Auvik devices. name: case-insensitive substring. ip: exact IPv4/IPv6 match.
    device_type: e.g. switch | l3Switch | router | firewall | accessPoint | server | hypervisor |
    printer | ups | workstation | unknown (full list in Auvik docs). vendor: substring.
    online_status: online | offline | unreachable | testing | unknown | dormant | notPresent | lowerLayerDown.
    site: case-insensitive substring of the Auvik tenant domain prefix, which is how this Auvik
    names a client site - it is NOT the place name, so the Boston office might be site "acme-bos". Every response
    lists the sites present in the estate under `sites`; read that rather than guessing.
    Returns id, name, type, IPs, make/model, vendor, versions, serial, status, last seen,
    plus tenant_id and site."""
    return devices.find_device(get_device_cache(), name=name, ip=ip, device_type=device_type,
                               vendor=vendor, online_status=online_status, site=site, limit=limit)


@mcp.tool
@envelope
def auvik_get_device(device_id: str) -> dict:
    """Full profile of one Auvik device by ID: inventory fields plus management state
    (managed flag, SNMP/WMI/login/VMware discovery status, TrafficInsights status).
    Also: connected_devices (topology neighbours Auvik has mapped), interface_count,
    component_count, config_backup_count and last_config_backup (ISO time of the newest
    configuration backup, "" if Auvik holds none)."""
    return devices.get_device(get_client(), device_id)


@mcp.tool
@envelope
def auvik_list_networks(site: str = "", network_type: str = "", scan_status: str = "", device_id: str = "",
                        name: str = "", include_devices: bool = False, limit: int = 100) -> dict:
    """Networks (subnets, VLANs, wifi) that Auvik OBSERVES. This is what the collectors see, not the
    intended design - it is not an IPAM and not a source of truth for addressing. Collectors also see
    remote networks over L3 routing, so read scan_status: "true" = this site scans it (local), "false" = seen
    but set to do-not-scan (usually remote), "notAllowed" | "unknown". network_type: routed | vlan |
    wifi | loopback | network | layer2 | internet. device_id: networks a given device is on.
    name: substring of the network name/description (e.g. "10.20."). include_devices: add up to 50
    {device_id, name} per network (device_count is always present). site: substring of the Auvik
    tenant domain prefix (the Boston office might be "acme-bos"); `sites` lists the valid ones."""
    return networks.list_networks(get_client(), get_tenants(), site=site, network_type=network_type,
                                  scan_status=scan_status, device_id=device_id, name=name,
                                  include_devices=include_devices, limit=limit, tenant_sites=get_tenant_sites())


@mcp.tool
@envelope
def auvik_list_interfaces(device_id: str, interface_type: str = "", operational_status: str = "", limit: int = 100) -> dict:
    """List a device's interfaces from Auvik with name, type, MAC, negotiated speed, duplex,
    IPs, operational status and admin state. interface_type: ethernet | vlan | loopback |
    linkAggregation | wifi | tunnel | virtualNic | other (more in Auvik docs).
    operational_status: online | offline | unreachable | testing | unknown | dormant | notPresent | lowerLayerDown."""
    return interfaces.list_interfaces(get_client(), get_tenants(), device_id, interface_type=interface_type,
                                      operational_status=operational_status, limit=limit)


@mcp.tool
@envelope
def auvik_component_health(site: str = "", device_id: str = "", status: str = "", component_type: str = "",
                           problems_only: bool = True, limit: int = 100) -> dict:
    """Hardware component status from Auvik: fans, power supplies, disks, memory, CPUs, storage
    controllers, system boards. Default answers "is any hardware degraded or failed?" across the
    estate (or one site / device). status: ok | degraded | failed (empty + problems_only=true = degraded
    and failed). component_type: cpu | disk | fan | memory | powerSupply | storageController | systemBoard.
    problems_only=false lists healthy components too, but only for one device_id or one status - a
    fleet-wide list of every healthy CPU core is refused. site: substring of the Auvik tenant domain
    prefix (the Boston office might be "acme-bos"); `sites` lists the valid ones. Zero rows with data_status ok means
    Auvik reports no faults; an Auvik failure is data_status unavailable, never an empty list."""
    return components.component_health(get_client(), get_tenants(), site=site, device_id=device_id, status=status,
                                       component_type=component_type, problems_only=problems_only, limit=limit,
                                       tenant_sites=get_tenant_sites())


@mcp.tool
@envelope
def auvik_config_changes(hours: int = 168, site: str = "", device_id: str = "", limit: int = 100) -> dict:
    """Which network devices had a configuration change, from Auvik's configuration backups.
    Auvik stores a new backup version when it detects the configuration differs, so a device listed
    here had a config change (or its first backup) in the window - independent of whether Auvik's
    own config-change alert fired. Returns per device: backup_count, running_backup_count,
    first_backup, last_backup and the newest 20 backup timestamps. No config text or diff is
    available from the Auvik API. hours: window ending now (default 168 = 7 days; 0 = full history,
    device_id required). device_id: one device's backup history. site: substring of the Auvik tenant
    domain prefix (the Boston office might be "acme-bos"); `sites` lists the valid ones. Zero devices with data_status
    ok means no backups changed in the window."""
    return configs.config_changes(get_client(), get_tenants(), _resolve_name, hours=hours, site=site,
                                  device_id=device_id, limit=limit, tenant_sites=get_tenant_sites())


@mcp.tool
@envelope
def auvik_remote_access_audit(hours: int = 168, site: str = "", user: str = "", category: str = "",
                              status: str = "", device_id: str = "", limit: int = 100) -> dict:
    """Who opened remote sessions to devices through Auvik, and when: terminal (SSH/telnet), tunnel
    (port forward), or remote browser. Answers "who was on that switch before it broke?". hours:
    window ending now (default 168 = 7 days; 0 = no window). user: exact Auvik user name as Auvik
    records it. category: tunnel | terminal | remoteBrowser | unknown. status: initiated | created |
    closed | failed | unknown. device_id: sessions to one device (filtered here, after the fetch).
    site: substring of the Auvik tenant domain prefix (the Boston office might be "acme-bos"); `sites` lists the valid
    ones. Sessions sort newest first; counts_by_user and counts_by_category cover the full match."""
    return audits.remote_access_audit(get_client(), get_tenants(), hours=hours, site=site, user=user,
                                      category=category, status=status, device_id=device_id, limit=limit,
                                      tenant_sites=get_tenant_sites())


@mcp.tool
@envelope
def auvik_device_lifecycle(site: str = "", device_id: str = "", problems_only: bool = True,
                           include_warranty: bool = True, limit: int = 100) -> dict:
    """Vendor lifecycle status per device from Auvik: sales_availability, software_maintenance,
    security_maintenance, last_support, plus warranty_coverage / warranty_expiration /
    service_coverage / recommended_software.
    IMPORTANT - the status VALUES are passed through exactly as Auvik sends them, and Auvik sends
    Title-Case: "Expired", "Available", "Unpublished", "Covered" have all been seen live
    ("SecurityOnly" is inferred from the same rule, not yet observed). Auvik's own
    OpenAPI file documents them lowercase (expired, available, ...) and is wrong. Do NOT filter
    these strings case-sensitively against the documented spelling - you will match nothing and
    read it as "no device is end-of-support". Compare case-insensitively, or use has_problem.
    has_problem is computed here with case folded, and is true when any lifecycle status is
    expired or securityOnly in any casing; problems_only (default true) returns only those.
    Auvik only has this data for some vendors - a device absent here is unknown, not healthy.
    include_warranty=false skips the warranty call.
    site: substring of the Auvik tenant domain prefix (the Boston office might be "acme-bos"); `sites` lists the valid ones."""
    return lifecycle.device_lifecycle(get_client(), get_tenants(), site=site, device_id=device_id,
                                      problems_only=problems_only, include_warranty=include_warranty, limit=limit,
                                      tenant_sites=get_tenant_sites())


@mcp.tool
@envelope
def auvik_interface_stats(stat: str, device_id: str = "", interface_id: str = "", hours: int = 24,
                          interval: str = "hour", limit: int = 200, include_empty: bool = True,
                          summary_only: bool = False) -> dict:
    """Interface time-series from Auvik. stat: bandwidth | utilization | packetLoss | packetDiscard.
    Give device_id (all its interfaces) or interface_id (one). hours: window ending now.
    interval: minute | hour | day. The window is clamped to keep the response under the size cap -
    6h at minute interval, 168h at hour, 365h at day (that is HOURS in every case, so a day-interval
    request tops out around 15 days) - and any clamp is reported in detail. limit: most-recent rows kept per series — when many
    interfaces answer at once this budget is scaled down per series to stay under the
    response-size cap, and the reduction is reported in detail; each series still
    reports its true row_count. Each row is [timestamp, value, ...]; read the units
    array for what the timestamp actually is (Auvik sends 'unix_mins' — MINUTES
    since the epoch, not seconds and not milliseconds) and for each value's unit.
    Auvik renames the stat in its reply - a `utilization` request comes back as `interfaceUtilization` - so each
    series reports both: `stat` is Auvik's value and `requested_stat` is yours. A mismatch
    between them is normal and is not a failed request.
    include_empty: keep series that returned no rows (default true). A real 17-port switch
    answers with 13 of 17 series empty - those are REAL zeros, so they are kept unless you
    opt out. Every response reports series_total / series_with_data / series_empty whichever
    you choose, and omitting them says how many in detail.
    Every series also carries summary: per value column {column, unit, samples, min, max, avg,
    last} over the full window. summary_only: true returns the summaries with rows: [] - the
    compact form for "is this link hot?" questions; row_count stays true.
    """
    return stats.interface_stats(get_client(), get_tenants(), stat, device_id=device_id, interface_id=interface_id,
                                 hours=hours, interval=interval, limit=limit, include_empty=include_empty,
                                 summary_only=summary_only)


@mcp.tool
@envelope
def auvik_device_stats(stat: str, device_id: str, hours: int = 24, interval: str = "hour", limit: int = 200,
                       include_empty: bool = True, summary_only: bool = False) -> dict:
    """Device time-series from Auvik. stat: cpuUtilization | memoryUtilization | storageUtilization | bandwidth.
    hours: window ending now. interval: minute | hour | day. The window is clamped in HOURS -
    6h at minute, 168h at hour, 365h at day (so a day-interval request tops out around 15 days) -
    and any clamp is reported in detail. limit: most-recent rows kept per series;
    row_count always reports the true total.
    Each row is [timestamp, value, ...]; the units array names the timestamp scale
    (Auvik sends 'unix_mins' — MINUTES since the epoch) and each value's unit.
    Auvik renames the stat in its reply - a `cpuUtilization` request comes back as `deviceCpuUtilization` - so each
    series reports both: `stat` is Auvik's value and `requested_stat` is yours. A mismatch
    between them is normal and is not a failed request.
    include_empty: keep series that returned no rows (default true). A device Auvik holds no
    samples for answers with an EMPTY series - that is a real zero, not a missing answer, so it
    is kept unless you opt out. Every response reports series_total / series_with_data /
    series_empty whichever you choose, and omitting them says how many in detail.
    Every series also carries summary: per value column {column, unit, samples, min, max, avg,
    last} over the full window. summary_only: true returns the summaries with rows: [] - the
    compact form for "how hard is this device working?" questions; row_count stays true.
    """
    return stats.device_stats(get_client(), get_tenants(), stat, device_id, hours=hours, interval=interval,
                              limit=limit, include_empty=include_empty, summary_only=summary_only)


@mcp.tool
@envelope
def auvik_device_availability(stat: str, device_id: str, hours: int = 168, interval: str = "day", limit: int = 200,
                              include_empty: bool = True, summary_only: bool = False) -> dict:
    """Device availability from Auvik. stat: uptime | outage. Default window 168h (7 days) at
    day interval. interval: minute | hour | day. The window is clamped in HOURS - 6h at minute,
    168h at hour, 365h at day (so a day-interval request tops out around 15 days) - and any
    clamp is reported in detail. limit: most-recent rows kept per series;
    row_count always reports the true total.
    Each row is [timestamp, value, ...]; the units array names the timestamp scale
    (Auvik sends 'unix_mins' — MINUTES since the epoch) and each value's unit.
    Auvik renames the stat in its reply - a `uptime` request comes back as `deviceAvailabilityUptime` - so each
    series reports both: `stat` is Auvik's value and `requested_stat` is yours. A mismatch
    between them is normal and is not a failed request.
    include_empty: keep series that returned no rows (default true). A device Auvik holds no
    samples for answers with an EMPTY series - that is a real zero, not a missing answer, so it
    is kept unless you opt out. Every response reports series_total / series_with_data /
    series_empty whichever you choose, and omitting them says how many in detail.
    Every series also carries summary: per value column {column, unit, samples, min, max, avg,
    last} over the full window. summary_only: true returns the summaries with rows: [] - the
    compact form for "how much of the window was this device up?" questions; row_count stays true.
    """
    return stats.device_availability(get_client(), get_tenants(), stat, device_id, hours=hours, interval=interval,
                                     limit=limit, include_empty=include_empty, summary_only=summary_only)


# -------------------------------------------------------------------- ASGI app (HTTP mode)


class _AuthWrapper:
    """ASGI middleware: /health always answers (liveness must not depend on
    Auvik or on auth); everything else requires X-API-Key equal to
    MCP_INBOUND_API_KEY (constant-time compare, fail-closed when unset)."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        path = scope.get("path", "")
        if path in ("/health", "/health/"):
            return await self._json(send, 200, {"status": "ok", "app": env.APP_NAME})
        expected = os.environ.get("MCP_INBOUND_API_KEY", "")
        provided = ""
        for k, v in scope.get("headers") or []:
            if k.decode("latin-1").lower() == "x-api-key":
                provided = v.decode("latin-1")
                break
        if not expected or not hmac.compare_digest(provided.encode(), expected.encode()):
            return await self._json(send, 401, {"error": "unauthorized"})
        token = env.caller_label.set("app-key")
        try:
            await self.inner(scope, receive, send)
        finally:
            env.caller_label.reset(token)

    @staticmethod
    async def _json(send, status: int, body: dict):
        payload = json.dumps(body).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]})
        await send({"type": "http.response.body", "body": payload})


def build_app():
    try:
        inner = mcp.http_app(stateless_http=True)
    except TypeError:  # fastmcp version without the kwarg on http_app()
        inner = mcp.http_app()
    return _AuthWrapper(inner)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    stdio = "--stdio" in argv or os.environ.get("MCP_TRANSPORT", "").lower() == "stdio"
    start_background_warm()
    if stdio:
        env.set_log_stream(sys.stderr)  # stdout is the MCP channel
        mcp.run(transport="stdio")
        return
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(build_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
