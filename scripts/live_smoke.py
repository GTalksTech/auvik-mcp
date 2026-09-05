"""Live read-only smoke test: verify → tenants → list_alerts(24h).

Prints literal output so it can be pasted into a build record. Never writes.
Needs a working credential — see the README."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auvik_client import AuvikClient, AuvikUnavailable  # noqa: E402
from credentials import CredentialsMissing, load_credentials  # noqa: E402
from tools import alerts, devices  # noqa: E402


def main() -> int:
    try:
        creds = load_credentials()
    except CredentialsMissing as exc:
        print(f"BLOCKED (no credential): {exc}")
        return 2
    print(f"credential: {creds!r}")
    client = AuvikClient(creds)
    try:
        print("verify:", json.dumps(client.verify())[:200])
        tenants = client.tenants()
        print("tenants:", tenants)
        cache = devices.DeviceCache(client, tenants)
        out = alerts.list_alerts(client, tenants, cache.name_of, hours=24, limit=10)
        print("list_alerts(24h): data_status =", out["data_status"], "| total_count =", out.get("total_count"),
              "| counts_by_severity =", out.get("counts_by_severity"))
        for a in out.get("alerts", []):
            print(f"  {a['severity']:9} {a['detected_on']}  {a['name']}  [{a['device_name'] or a['entity_type']}]")
        print("config endpoint probe (backup metadata only — no config text or diff exists):",
              json.dumps(client.get("/v1/inventory/configuration", {"tenants": ",".join(tenants), "page[first]": 1}))[:400])
        return 0
    except AuvikUnavailable as exc:
        print("UNAVAILABLE:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
