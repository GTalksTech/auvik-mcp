"""Time-series tools. Auvik requires fromTime + interval; we compute fromTime
from `hours` and clamp the window per interval so a careless request cannot
blow the 500 KB response cap — and SAY so in `detail` rather than truncating
silently.

Row shape is NOT fixed: each row is [timestamp, v1, v2, ...] and the parallel
`unit` array names the scale of every column. Against the live API the first
column is labelled 'Recorded At' with unit 'unix_mins' — MINUTES since the
epoch, not seconds and not milliseconds. Never assume; pass units through and
let the caller read them."""
from __future__ import annotations

from datetime import datetime

from envelope import iso_ago, ok

INTERFACE_STATS = ("bandwidth", "utilization", "packetLoss", "packetDiscard")
DEVICE_STATS = ("cpuUtilization", "memoryUtilization", "storageUtilization", "bandwidth")
AVAIL_STATS = ("uptime", "outage")
INTERVALS = ("minute", "hour", "day")
# Clamp per interval, in HOURS - including for the "day" interval, where 365
# means 365 HOURS (about 15 days), not 365 days. The point is the response-size
# cap, not calendar coverage, and window() reports every clamp in `detail` so a
# shortened window is never silent. If you need a full year of daily points,
# this is the number to revisit - and the size budget with it.
CLAMP_HOURS = {"minute": 6, "hour": 168, "day": 365}
MAX_LIMIT = 200


def _check_enum(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ValueError(f"invalid {name} {value!r}; valid: {', '.join(allowed)}")


def window(hours: int, interval: str, now: datetime | None = None) -> tuple[str, int, str]:
    _check_enum("interval", interval, INTERVALS)
    if hours < 1:
        raise ValueError("hours must be >= 1")
    cap = CLAMP_HOURS[interval]
    note = ""
    if hours > cap:
        note = f"window clamped from {hours}h to {cap}h for interval '{interval}' (response-size cap)"
        hours = cap
    return iso_ago(hours, now), hours, note


def _as_list(data) -> list:
    """Auvik's OpenAPI declares /v1/stat/* `data` as an OBJECT, yet the same
    endpoints accept cursor-pagination params that only make sense for a
    collection (deviation D-1 in docs/API-DEVIATIONS.md). Tolerate either shape."""
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def column_summary(columns: list, units: list, rows: list) -> list[dict]:
    """Per value column (column 0 is the timestamp): samples, min, max, avg, last.
    Computed over the FULL row set, so a capped `rows` never distorts it. A
    column with no numeric samples reports samples=0 and zeros - the zeros are
    flagged by the count, never presented as measurements."""
    out = []
    for ci in range(1, len(columns)):
        vals = [r[ci] for r in rows if ci < len(r) and _is_number(r[ci])]
        unit = units[ci] if ci < len(units) else ""
        if not vals:
            out.append({"column": columns[ci], "unit": unit, "samples": 0, "min": 0.0, "max": 0.0, "avg": 0.0, "last": 0.0})
            continue
        out.append({"column": columns[ci], "unit": unit, "samples": len(vals), "min": float(min(vals)),
                    "max": float(max(vals)), "avg": round(sum(vals) / len(vals), 3), "last": float(vals[-1])})
    return out


def project_series(item: dict, limit: int, requested_stat: str = "", summary_only: bool = False) -> dict:
    a = item.get("attributes") or {}
    period = a.get("reportPeriod") or {}
    rel = item.get("relationships") or {}
    ent = (rel.get("interface") or rel.get("device") or {}).get("data") or {}
    series = []
    for s in a.get("stats") or []:
        rows = list(s.get("data") or [])
        columns, units = list(s.get("legend") or []), list(s.get("unit") or [])
        kept = [] if summary_only else (rows[-limit:] if limit else rows)
        series.append({
            "name": s.get("name") or "", "index": str(s.get("index") or ""),
            "columns": columns, "units": units,
            "rows": kept, "row_count": len(rows),
            # summary_only cut nothing - the rows were omitted on request, and
            # `detail` says so. Reporting truncated=true there would claim the
            # answer is short of the data when it is not.
            "truncated": False if summary_only else len(kept) < len(rows),
            "summary": column_summary(columns, units, rows),
        })
    return {
        "entity_id": ent.get("id") or "",
        "entity_name": ent.get("interfaceName") or ent.get("deviceName") or "",
        # Auvik does not echo the stat you asked for. It returns the family
        # prefix plus the stat capitalised, verified live across all ten stats
        # of all three families: cpuUtilization -> deviceCpuUtilization,
        # utilization -> interfaceUtilization, uptime -> deviceAvailabilityUptime
        # (deviation D-8). Both are reported: `stat` is whatever Auvik said,
        # passed through unaltered, and `requested_stat` is the caller's own
        # string, so an agent can compare the two without knowing the rule and
        # without mistaking the rename for a failed request.
        "stat": a.get("statType") or "",
        "requested_stat": requested_stat,
        "interval": a.get("interval") or "",
        "from_time": period.get("fromTime") or "",
        "thru_time": period.get("thruTime") or "",
        "series": series,
    }


def _run(client, path: str, params: dict, hours: int, interval: str, limit: int, now,
         requested_stat: str = "", include_empty: bool = True, summary_only: bool = False) -> dict:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    limit = min(limit, MAX_LIMIT)
    frm, eff_hours, note = window(hours, interval, now)
    # filter[thruTime] is REQUIRED by the live API, despite the OpenAPI spec
    # marking it optional with "defaults to current time". Omitting it 400s
    # every /v1/stat/* call: 'Field "thruTime" of required type "DateTime!" was
    # not provided.' Verified against the live API (deviation D-4).
    # iso_ago(0, now) is "now" on the same clock that produced fromTime, so the
    # window can never come out inverted.
    params = {**params, "filter[fromTime]": frm, "filter[thruTime]": iso_ago(0, now),
              "filter[interval]": interval}
    body = client.get(path, params)
    items = _as_list(body.get("data"))

    # A 400-port stacked switch returns 400 series. At the full row limit that
    # serialises to ~2.3 MB — 4.5x the 500 KB cap — so the per-entity row budget
    # scales down with the entity count. row_count still reports the true total,
    # so a shrunken window is visible, never silent.
    per_entity = max(1, limit // max(1, len(items) // 10))
    if per_entity < limit:
        scaled = (f"{len(items)} entities returned; rows per series reduced from {limit} to "
                  f"{per_entity} to stay under the response-size cap (row_count reports the full total)")
        note = f"{note}; {scaled}" if note else scaled

    projected = [project_series(i, per_entity, requested_stat, summary_only) for i in items]

    # A real 17-port access switch answers with 13 EMPTY series, observed live:
    # ports that genuinely have no utilisation data. Those zeros are real and are
    # never dropped by default — hiding a real zero is the failure mode this
    # server exists to prevent. What the agent gets for free is the split, so it
    # can see at a glance how much of the payload is signal; and if it would
    # rather not read past the noise, include_empty=False omits them and says how
    # many. The totals below always describe the FULL response, filtered or not.
    series_total = sum(len(p["series"]) for p in projected)
    series_with_data = sum(1 for p in projected for s in p["series"] if s["row_count"])
    series_empty = series_total - series_with_data
    entity_count_total = len(projected)

    if not include_empty and series_empty:
        for p in projected:
            p["series"] = [s for s in p["series"] if s["row_count"]]
        # An entity left holding no series is noise of a different shape.
        projected = [p for p in projected if p["series"]]
        omitted = (f"{series_empty} of {series_total} series omitted because they contain no data "
                   f"(series_total still reports all {series_total}); pass include_empty=true to see them")
        note = f"{note}; {omitted}" if note else omitted

    if summary_only:
        omitted = "rows omitted (summary_only=true); each series still reports row_count and a per-column summary"
        note = f"{note}; {omitted}" if note else omitted

    out = ok(series_for=projected, entity_count=len(projected), entity_count_total=entity_count_total,
             series_total=series_total, series_with_data=series_with_data, series_empty=series_empty,
             window_hours=eff_hours, interval=interval, clamp_note=note)
    out["detail"] = note
    return out


def interface_stats(client, tenant_ids: list[str], stat: str, device_id: str = "", interface_id: str = "",
                    hours: int = 24, interval: str = "hour", limit: int = 200, include_empty: bool = True,
                    summary_only: bool = False, now: datetime | None = None) -> dict:
    _check_enum("stat", stat, INTERFACE_STATS)
    if not device_id.strip() and not interface_id.strip():
        raise ValueError("one of device_id or interface_id is required")
    params = {"tenants": ",".join(tenant_ids)}
    if interface_id.strip():
        params["filter[interfaceId]"] = interface_id.strip()
    if device_id.strip():
        params["filter[parentDevice]"] = device_id.strip()
    return _run(client, f"/v1/stat/interface/{stat}", params, hours, interval, limit, now, stat, include_empty, summary_only)


def device_stats(client, tenant_ids: list[str], stat: str, device_id: str, hours: int = 24,
                 interval: str = "hour", limit: int = 200, include_empty: bool = True,
                 summary_only: bool = False, now: datetime | None = None) -> dict:
    _check_enum("stat", stat, DEVICE_STATS)
    if not device_id.strip():
        raise ValueError("device_id is required")
    params = {"tenants": ",".join(tenant_ids), "filter[deviceId]": device_id.strip()}
    return _run(client, f"/v1/stat/device/{stat}", params, hours, interval, limit, now, stat, include_empty, summary_only)


def device_availability(client, tenant_ids: list[str], stat: str, device_id: str, hours: int = 168,
                        interval: str = "day", limit: int = 200, include_empty: bool = True,
                        summary_only: bool = False, now: datetime | None = None) -> dict:
    _check_enum("stat", stat, AVAIL_STATS)
    if not device_id.strip():
        raise ValueError("device_id is required")
    params = {"tenants": ",".join(tenant_ids), "filter[deviceId]": device_id.strip()}
    return _run(client, f"/v1/stat/deviceAvailability/{stat}", params, hours, interval, limit, now, stat, include_empty, summary_only)
