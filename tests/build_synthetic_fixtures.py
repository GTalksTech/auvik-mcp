"""Build entirely fictional fixtures under tests/fixtures/synthetic/.

Shapes follow Auvik's published OpenAPI document (JSON:API envelopes). Every value
is invented — no real tenant data is here, which is why these files are
committed. Where the live API is known to disagree with that OpenAPI file, the
fixtures follow the LIVE behaviour (see docs/API-DEVIATIONS.md), so the offline
tier can never quietly disagree with production.

Re-run whenever a projection needs a new field:
    py -3.12 tests/build_synthetic_fixtures.py
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fixtures" / "synthetic"
API = "https://auvikapi.us1.my.auvik.com"


def tenant_rel(tid="T1", prefix="acme-hq"):
    """The live API inlines domainPrefix on every record's tenant relationship,
    so a projection can name the site without a second call. Verified against a
    production tenant: every device carried a tenant relationship, and it was
    always the OWNING client tenant rather than the multiClient root."""
    return {"data": {"type": "tenant", "id": tid, "attributes": {"domainPrefix": prefix}}}


TENANT = tenant_rel()


def alert(aid, name, severity, status, entity_type, entity_id, detected, dismissed=False, description="desc"):
    """entity_type=None omits the `entity` relationship entirely. About one real
    alert in ten is collector-level ("Auvik Collector Approved/Reconnected") and
    carries no entity at all, despite the OpenAPI file listing one (D-5)."""
    rels = {"tenant": TENANT,
            "relatedAlert": {"data": {"type": "alert", "id": "AL-REL", "attributes": {"name": "Cleared"}}}}
    if entity_type is not None:
        rels["entity"] = {"data": {"type": entity_type, "id": entity_id,
                                   "links": {"dashboard": "https://acme-hq.my.auvik.com/#entity"}}}
    return {
        "type": "alert", "id": aid,
        "attributes": {"name": name, "severity": severity, "status": status,
                       "alertDefinitionId": "DEF-1", "specificationId": "-400", "detectedOn": detected,
                       "description": description, "dismissed": dismissed, "dispatched": True,
                       "externalTicket": [{"id": "56003", "system": "Microsoft_Teams"}]},
        "links": {"dashboard": f"https://acme-hq.my.auvik.com/#alert/{aid}", "self": f"{API}/v1/alert/history/info/{aid}"},
        "relationships": rels,
    }


def device(did, name, ip, dtype, vendor, model, status, serial, tenant=None):
    tenant = TENANT if tenant is None else tenant
    return {
        "type": "device", "id": did,
        "attributes": {"ipAddresses": [ip], "deviceName": name, "deviceType": dtype, "makeModel": model,
                       "vendorName": vendor, "softwareVersion": "1.2.3", "serialNumber": serial,
                       "description": f"{vendor} {model}", "firmwareVersion": "fw-9", "lastModified": "2026-08-01T00:00:00.000Z",
                       "lastSeenTime": "2026-08-29T06:00:00.000Z", "onlineStatus": status},
        "relationships": {"tenant": tenant, "networks": {"data": []},
                          "deviceDetail": {"data": {"type": "deviceDetail", "id": did}}},
        "links": {"dashboard": "https://acme-hq.my.auvik.com/#entity/device/1", "self": f"{API}/v1/inventory/device/info/{did}"},
    }


def detail(did):
    """Relationships POPULATED. They were empty here while the live API filled
    connectedDevices and interfaces on the very first device recorded, so the
    topology projection had no offline case to project against."""
    return {"type": "deviceDetail", "id": did,
            "attributes": {"discoveryStatus": {"snmp": "authorized", "login": "privileged", "wmi": "disabled", "vmware": "disabled"},
                           "manageStatus": True, "trafficInsightsStatus": "notDetected"},
            "relationships": {
                "tenant": TENANT,
                "connectedDevices": {"data": [
                    {"type": "device", "id": "DEV-2", "attributes": {"deviceName": "edge-fw-01"}},
                    {"type": "device", "id": "DEV-9", "attributes": {"deviceName": "ap-lobby-01"}}]},
                "interfaces": {"data": [{"type": "interface", "id": f"IF-{n}", "attributes": {"interfaceName": f"Gi1/0/{n}", "macAddress": "1A:2B:3C:4D:5E:6F"}} for n in (1, 2, 3)]},
                "configurations": {"data": [
                    {"type": "configuration", "id": "CFG-2", "attributes": {"isRunning": False, "backupTime": "2026-08-28T03:00:00.000Z"}},
                    {"type": "configuration", "id": "CFG-3", "attributes": {"isRunning": True, "backupTime": "2026-08-29T03:00:00.000Z"}}]},
                "components": {"data": [
                    {"type": "component", "id": "CMP-1", "attributes": {"componentName": "CPU 0", "componentType": "cpu"}},
                    {"type": "component", "id": "CMP-2", "attributes": {"componentName": "Fan 1", "componentType": "fan"}}]},
            }}


def interface(iid, name, parent, itype, oper, admin, speed="1.0 Gbit/s"):
    return {"type": "interface", "id": iid,
            "attributes": {"interfaceName": name, "interfaceType": itype, "macAddress": "1A:2B:3C:4D:5E:6F",
                           "negotiatedSpeed": speed, "duplex": "full", "customConnections": False,
                           "ipAddresses": ["10.10.0.1"] if iid == "IF-1" else [], "operationalStatus": oper,
                           "adminStatus": admin, "lastModified": "2026-08-01T00:00:00.000Z"},
            "relationships": {"tenant": TENANT, "parentDevice": {"data": {"id": parent, "type": "device"}}}}


def network(nid, name, ntype, scan, device_ids_names, tenant=None):
    tenant = TENANT if tenant is None else tenant
    return {"type": "network", "id": nid,
            "attributes": {"networkType": ntype, "networkName": name, "description": name,
                           "scanStatus": scan, "lastModified": "2026-08-20T00:00:00.000Z"},
            "relationships": {"tenant": tenant,
                              "devices": {"data": [{"type": "device", "id": d, "attributes": {"deviceName": n}}
                                                   for d, n in device_ids_names]}},
            "links": {"self": f"{API}/v1/inventory/network/info/{nid}"}}


def component(cid, name, ctype, status, parent_id, parent_name):
    return {"type": "component", "id": cid,
            "attributes": {"componentName": name, "componentType": ctype, "currentStatus": status,
                           "lastModified": "2026-08-28T00:00:00.000Z"},
            "relationships": {"tenant": TENANT,
                              "parentDevice": {"data": {"type": "device", "id": parent_id,
                                                        "attributes": {"deviceName": parent_name}}}}}


def configuration(cfid, device_id, device_name, backup_time, is_running=False):
    return {"type": "configuration", "id": cfid,
            "attributes": {"backupTime": backup_time, "isRunning": is_running},
            "relationships": {"tenant": TENANT,
                              "device": {"data": {"type": "device", "id": device_id,
                                                  "attributes": {"deviceName": device_name}}}}}


def audit(aid, user, category, status, device_id, device_name, started, last_active, action="create",
          direction="outbound", cause="", data="session opened"):
    return {"type": "entityAudit", "id": aid,
            "attributes": {"user": user, "category": category, "action": action, "direction": direction,
                           "status": status, "cause": cause, "data": data,
                           "dateStarted": started, "lastActive": last_active},
            "relationships": {"tenant": TENANT,
                              "device": {"data": {"type": "device", "id": device_id,
                                                  "attributes": {"deviceName": device_name}}}}}


def lifecycle(lid, device_id, device_name, sales, software, security, last_support):
    """Statuses are Title-Case because the LIVE API is (deviation D-9), even
    though Auvik's OpenAPI document declares camelCase. Lowercase
    fixtures would have let `has_problem` pass offline while matching NOTHING on
    real data - a fabricated zero on the tool's default view. Same remedy as
    D-8: the offline tier carries production's spelling so the two cannot
    disagree. tools/lifecycle.py folds case, so both forms still flag, and
    test_lifecycle.py pins the documented camelCase separately."""
    return {"type": "deviceLifecycle", "id": lid,
            "attributes": {"deviceName": device_name, "salesAvailability": sales,
                           "softwareMaintenanceStatus": software,
                           "securitySoftwareMaintenanceStatus": security, "lastSupportStatus": last_support},
            "relationships": {"tenant": TENANT,
                              "device": {"data": {"type": "device", "id": device_id,
                                                  "attributes": {"deviceName": device_name}}}}}


def warranty(device_id, device_name, coverage, expiration, service="Covered", recommended="1.2.4"):
    """`id` IS the device id - verified live on every record returned, which is
    what makes the lifecycle merge sound. `warrantyExpirationDate` uses live
    Auvik's non-ISO 'YYYY-MM-DD HH:MM:SS.sss+0000' (deviation D-10), not the
    'T...Z' every other Auvik timestamp uses; it is passed through unparsed."""
    return {"type": "deviceWarranty", "id": device_id,
            "attributes": {"deviceName": device_name, "serviceCoverageStatus": service,
                           "serviceAttachmentStatus": "Available", "contractRenewalAvailability": "Available",
                           "warrantyCoverageStatus": coverage, "warrantyExpirationDate": expiration,
                           "recommendedSoftwareVersion": recommended},
            "relationships": {"tenant": TENANT}}


def series(entity_id, stat_type, legend, unit, rows, rel_key, extra_rel):
    return {"type": f"{rel_key}Statistics", "id": f"ST-{entity_id}",
            "attributes": {"reportPeriod": {"fromTime": "2026-08-28T12:00:00.000Z", "thruTime": "2026-08-29T12:00:00.000Z"},
                           "interval": "hour", "statType": stat_type,
                           # The live API sends stats[].name and stats[].index as
                           # null. The projection must absorb that without
                           # leaking None anywhere downstream.
                           "stats": [{"name": None, "index": None, "legend": legend,
                                      "unit": unit, "data": rows}]},
            "relationships": {rel_key: {"data": {"id": entity_id, "type": rel_key, **extra_rel}}, "tenant": TENANT}}


def envelope(items, next_url=None):
    body = {"data": items, "links": {"first": f"{API}/v1/x"}, "meta": {"totalPages": 2 if next_url else 1}}
    if next_url:
        body["links"]["next"] = next_url
    return body


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # unix_mins, not epoch_ms: live Auvik labels column 0 "Recorded At" with
    # unit "unix_mins" — MINUTES since the epoch. Hourly interval = +60.
    rows = [[29799780 + i * 60, 10.0 + i, 20.0 + i] for i in range(24)]
    files = {
        "tenants.json": {"data": [{"type": "tenant", "id": "T1", "attributes": {"domainPrefix": "acme-hq", "tenantType": "corporateIt"}}]},
        # A multi-tenant /v1/tenants, shaped like the live one: a multiClient
        # ROOT that returns everything, two ordinary clients, one client whose
        # prefix is a superstring of another (acme-bos / acme-bos-dr - substring
        # matching must send both), and one client that has alerts but NO
        # devices. That last case is real: a tenant can hold alerts and no
        # devices, so a site list built from the device estate would omit it.
        "tenants_multi.json": {"data": [
            {"type": "tenant", "id": "T0", "attributes": {"domainPrefix": "acme-root", "tenantType": "multiClient"}},
            {"type": "tenant", "id": "T1", "attributes": {"domainPrefix": "acme-hq", "tenantType": "client"}},
            {"type": "tenant", "id": "T2", "attributes": {"domainPrefix": "acme-bos", "tenantType": "client"}},
            {"type": "tenant", "id": "T3", "attributes": {"domainPrefix": "acme-bos-dr", "tenantType": "client"}},
            {"type": "tenant", "id": "T4", "attributes": {"domainPrefix": "acme-misc", "tenantType": "client"}},
        ]},
        "alerts_page1.json": envelope([
            alert("AL-1", "Device Offline", "warning", "created", "device", "DEV-1", "2026-08-29T05:00:00.000Z", description="core-sw-01 stopped responding"),
            alert("AL-2", "Interface Errors High", "critical", "created", "interface", "IF-1", "2026-08-29T04:30:00.000Z"),
        ], next_url=f"{API}/v1/alert/history/info?page[after]=cursor1"),
        "alerts_page2.json": envelope([
            alert("AL-3", "Device Back Online", "info", "resolved", "device", "DEV-2", "2026-08-28T22:00:00.000Z"),
            # entity_type=None: a collector-level alert, no entity relationship.
            # About one real alert in ten looks like this; without one here the
            # entity-less projection path is never exercised on the committed tier.
            alert("AL-4", "Auvik Collector Reconnected", "warning", "resolved", None, "", "2026-08-28T21:00:00.000Z",
                  description="The Auvik collector at 10.10.0.9 has reconnected to the cloud"),
        ]),
        "alert_single.json": {"data": alert("AL-1", "Device Offline", "warning", "created", "device", "DEV-1", "2026-08-29T05:00:00.000Z")},
        "alert_single_dismissed.json": {"data": alert("AL-DISMISSED", "Old Alert", "info", "resolved", "device", "DEV-2", "2026-08-01T00:00:00.000Z", dismissed=True)},
        "devices.json": envelope([
            device("DEV-1", "core-sw-01", "10.10.0.1", "switch", "Cisco", "Catalyst 9300", "online", "SN-CORE1"),
            device("DEV-2", "edge-fw-01", "10.10.0.254", "firewall", "Fortinet", "FortiGate 100F", "offline", "SN-EDGE1"),
        ]),
        # A second tenant. The default estate above is single-tenant, which is
        # exactly how a missing `site` survived review for so long: every
        # projection test passed while the projection carried no tenant at all.
        # A real Auvik is many tenants, and a question like "what is down at the
        # Boston office?" is a per-site question.
        "devices_two_tenants.json": envelope([
            device("DEV-1", "core-sw-01", "10.10.0.1", "switch", "Cisco", "Catalyst 9300", "online", "SN-CORE1"),
            device("DEV-2", "edge-fw-01", "10.10.0.254", "firewall", "Fortinet", "FortiGate 100F", "offline", "SN-EDGE1"),
            device("DEV-3", "bos-access-01", "10.20.0.1", "switch", "Aruba", "6200", "online", "SN-BOS1",
                   tenant=tenant_rel("T2", "acme-bos")),
            device("DEV-4", "bos-edge-01", "10.20.0.254", "router", "Cisco", "ISR 4331", "online", "SN-BOS2",
                   tenant=tenant_rel("T2", "acme-bos")),
        ]),
        "device_single.json": {"data": device("DEV-1", "core-sw-01", "10.10.0.1", "switch", "Cisco", "Catalyst 9300", "online", "SN-CORE1")},
        "device_detail_single.json": {"data": detail("DEV-1")},
        "interfaces.json": envelope([
            interface("IF-1", "GigabitEthernet1/0/1", "DEV-1", "ethernet", "online", True),
            interface("IF-2", "GigabitEthernet1/0/2", "DEV-1", "ethernet", "offline", False),
            interface("IF-3", "Vlan10", "DEV-1", "vlan", "online", True, speed=""),
        ]),
        # statType is NOT the stat you asked for. The live API returns the family
        # prefix plus the stat capitalised (D-8), and these fixtures previously
        # carried the bare name - which made the projection look like it echoed
        # the request when it was only passing through a coincidence.
        "stat_interface.json": envelope([series("IF-1", "interfaceUtilization", ["Recorded At", "Transmit", "Receive"], ["unix_mins", "percent", "percent"], rows,
                                               "interface", {"interfaceName": "GigabitEthernet1/0/1", "parentDevice": "DEV-1"})]),
        # The live empty ratio. A real 17-port access switch returns 17 series of
        # which 13 are EMPTY, at both 6h and 24h. Those zeros are real - the
        # ports have no utilisation data - and the single fully-populated
        # fixture above could never exercise the majority case that dominates a
        # real response.
        "stat_interface_mixed.json": envelope([
            series(f"IF-M{n}", "interfaceUtilization", ["Recorded At", "Transmit", "Receive"],
                   ["unix_mins", "percent", "percent"], rows[:count], "interface",
                   {"interfaceName": f"Port {n}", "parentDevice": "DEV-1"})
            for n, count in enumerate([0, 0, 0, 6, 6, 6, 0, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0], start=1)
        ]),
        "stat_device.json": envelope([series("DEV-1", "deviceCpuUtilization", ["Recorded At", "Percent"], ["unix_mins", "percent"], [r[:2] for r in rows], "device", {})]),
        "stat_availability.json": envelope([series("DEV-1", "deviceAvailabilityUptime", ["Recorded At", "Uptime"], ["unix_mins", "percent"], [r[:2] for r in rows], "device", {})]),
        "networks.json": envelope([
            network("NET-1", "10.10.0.0/24", "routed", "true", [("DEV-1", "core-sw-01"), ("DEV-2", "edge-fw-01")]),
            network("NET-2", "VLAN 20 - Voice", "vlan", "false", [("DEV-1", "core-sw-01")]),
            network("NET-3", "Guest-WiFi", "wifi", "notAllowed", []),
        ]),
        "components.json": envelope([
            component("CMP-1", "CPU 0", "cpu", "ok", "DEV-1", "core-sw-01"),
            component("CMP-2", "Fan 1", "fan", "degraded", "DEV-1", "core-sw-01"),
            component("CMP-3", "Power Supply 2", "powerSupply", "failed", "DEV-2", "edge-fw-01"),
            component("CMP-4", "Disk 0", "disk", "ok", "DEV-2", "edge-fw-01"),
        ]),
        "configurations.json": envelope([
            configuration("CFG-1", "DEV-1", "core-sw-01", "2026-08-27T03:00:00.000Z"),
            configuration("CFG-2", "DEV-1", "core-sw-01", "2026-08-28T03:00:00.000Z"),
            configuration("CFG-3", "DEV-1", "core-sw-01", "2026-08-29T03:00:00.000Z", is_running=True),
            configuration("CFG-4", "DEV-2", "edge-fw-01", "2026-08-01T03:00:00.000Z"),
        ]),
        "audits.json": envelope([
            audit("AUD-1", "Alex Netadmin", "terminal", "closed", "DEV-1", "core-sw-01",
                  "2026-08-28T14:00:00.000Z", "2026-08-28T14:25:00.000Z"),
            audit("AUD-2", "Jane Tech", "tunnel", "created", "DEV-2", "edge-fw-01",
                  "2026-08-29T09:00:00.000Z", "2026-08-29T09:30:00.000Z"),
            audit("AUD-3", "Alex Netadmin", "remoteBrowser", "failed", "DEV-1", "core-sw-01",
                  "2026-08-27T08:00:00.000Z", "2026-08-27T08:00:10.000Z", cause="connection refused"),
        ]),
        "lifecycle.json": envelope([
            lifecycle("LC-1", "DEV-1", "core-sw-01", "Available", "Available", "Available", "Available"),
            lifecycle("LC-2", "DEV-2", "edge-fw-01", "Expired", "SecurityOnly", "SecurityOnly", "Expired"),
        ]),
        "lifecycle_single.json": {"data": lifecycle("LC-2", "DEV-2", "edge-fw-01", "Expired", "SecurityOnly", "SecurityOnly", "Expired")},
        "warranty.json": envelope([
            warranty("DEV-1", "core-sw-01", "Available", "2027-01-01 00:00:00.000+0000"),
            warranty("DEV-2", "edge-fw-01", "Expired", "2024-06-30 00:00:00.000+0000"),
        ]),
        "warranty_single.json": {"data": warranty("DEV-2", "edge-fw-01", "Expired", "2024-06-30 00:00:00.000+0000")},
        "interface_single.json": {"data": interface("IF-1", "GigabitEthernet1/0/1", "DEV-1", "ethernet", "online", True)},
    }
    for name, body in files.items():
        # newline="\n" explicitly: the default on Windows translates to CRLF and
        # would rewrite all 24 committed fixtures on every regeneration.
        (OUT / name).write_text(json.dumps(body, indent=1), encoding="utf-8", newline="\n")
        print("wrote", name)


if __name__ == "__main__":
    main()
