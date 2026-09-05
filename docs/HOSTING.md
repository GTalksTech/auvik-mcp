# Hosting the Auvik MCP server

The server has two transports. `python server.py --stdio` is what a desktop MCP
client launches directly. `python server.py` (no flag) serves **streamable HTTP**
on `$PORT`, and that is the mode this document is about: one container, an
inbound API key, and an unauthenticated health probe.

The reference target here is **Azure Container Apps**, because that is what the
Dockerfile and the log guidance below were written against — but nothing in the
server is Azure-specific. Any container host works if it can give you the four
environment variables, a stable single replica, and somewhere that keeps stdout.

## Scale: `minReplicas: 1`, `maxReplicas: 1` — not negotiable without a code change

This is the one constraint you cannot relax by configuration.

`auvik_dismiss_alert` — the only write — is guarded by two **process-scoped**
sets in `tools/alerts.py`:

- `SEEN_ALERT_IDS`: the read-before-dismiss rule. An alert may only be dismissed
  if this process returned it from a read first.
- `DISMISSED_ALERT_IDS`: the duplicate-write guard, and the reason the replica
  count matters. Auvik's dismissal is **eventually consistent** (deviation
  **D-7**, `docs/API-DEVIATIONS.md`): for a couple of minutes after a successful
  POST, re-reading the alert still reports `dismissed: false`. Inside that
  window the "re-read and refuse if already dismissed" rule is reading stale
  data, so on its own it **fails open** — this in-process set is the only
  authority that is not stale, and it is consulted before the re-read so a retry
  is refused even when Auvik cannot be reached at all.

A second replica has neither set. HTTP requests are stateless and can land on
either replica, so with two replicas:

- a read on replica A followed by a dismiss on replica B is **refused**
  ("not read in this session") — an annoying but safe failure; and
- a dismiss on A followed by a retry on B issues a **second POST** and writes a
  second audit line for one logical action — the D-7 duplicate write,
  reintroduced by scaling. That is the unsafe one.

`minReplicas: 1` (rather than scale-to-zero) is also worth having for a
different reason: the first request on a cold process pays the device-inventory
fetch, which can exceed a chat connector's default timeout.

Multi-replica-safe state would mean moving both sets to shared storage. That is
a code change, not a config change. Until someone makes it, run one replica.

## Credentials: environment variables, not the OS keyring

`credentials.py` resolves in this order:

1. environment: `AUVIK_USERNAME` + `AUVIK_API_KEY` (both, or neither), with
   `AUVIK_REGION` optional;
2. the OS keyring (`keyring`, service `auvik-api-key`).

A container has no keyring, so the environment path is the only one that works
there. Supply `AUVIK_API_KEY` as a secret (on Azure Container Apps, a Key Vault
secret reference); `AUVIK_USERNAME` is an email address and is ordinary config.

`AUVIK_REGION` is the subdomain of your Auvik URL — if you sign in at
`https://yourcompany.us2.my.auvik.com`, it is `us2`, and the API host the client
builds is `https://auvikapi.us2.my.auvik.com`. The Dockerfile bakes in `us1` as
a default because Auvik's own documentation uses it; **set it explicitly** if
that is not your region. A wrong region with a perfectly good key looks like an
authentication failure, not a hostname failure, and that is an expensive hour.

**Scope the API key deliberately.** Reads need Device, Interface, Network,
Component, Configuration, Statistics, Lifecycle and Tenants access.
`auvik_dismiss_alert` additionally needs **Alerts: Edit** — a role with Alerts
read but not Edit returns HTTP 403 on the POST, and the tool reports
`data_status: "unavailable"` with that status in `detail`. If you never want the
write to be possible at all, issue a key without Alerts Edit: the guardrails in
this server are then belt-and-braces on top of a vendor-side refusal.

`MCP_INBOUND_API_KEY` is the inbound key the ASGI middleware compares in
constant time against the client's `X-API-Key` header. **Unset means fail
closed**: every request except `/health` is answered 401. Rotating it is a
secret update plus a revision restart.

## Probes

`GET /health` answers `200 {"status":"ok","app":"auvik-mcp"}` with no API key and
without Auvik being reachable. Use it for both liveness and readiness.

Both properties are deliberate. Requiring the inbound key on a health probe
means a key rotation looks like an outage; requiring Auvik to be reachable means
a vendor incident restart-loops your container instead of letting the tools
report `data_status: "unavailable"` honestly. Note also that the MCP endpoint
itself is POST-only, so a default TCP or `GET /` probe will fail — point the
probe at `/health` explicitly.

## The audit trail, and where it actually survives

Every successful dismissal is recorded **twice**:

1. a line appended to `audit/dismissals.jsonl`, and
2. a `dismissal_audit` JSON line on the process log stream — stdout in HTTP
   mode, stderr in stdio mode.

In a container, **only the second one is durable.** The file lives on
container-local disk: a restart or a new revision loses it, and a read-only root
filesystem (a common hardening default) stops the append succeeding at all. The
file append is therefore **best effort by design** — if it fails, the dismissal
still returns `ok`, because the write really did happen and reporting
`unavailable` would invite a retry that dismisses twice. The response's `detail`
carries a warning telling you to capture the record from the process log.

So: ship stdout somewhere that is kept, and make sure somebody owns reading it.
On Azure Container Apps, console output lands in Log Analytics:

```kusto
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "<your container app name>"
| where Log_s has "dismissal_audit"
| project TimeGenerated, Log_s
| order by TimeGenerated desc
```

Each record carries the timestamp, alert id, name, severity, entity, device
name, the caller-supplied reason, and the caller **key label** — never the key
itself, and never the credential.

## Response size and chat-client constraints

Chat platforms that consume MCP over HTTP (Microsoft Copilot Studio is the one
these limits were sized against) impose two constraints the tools already
respect: a **500 KB** response cap and a default connector timeout around 30
seconds.

Every list tool takes `limit` (max 200) and always reports `returned_count`,
`truncated` and a full-match count — `total_count` on most, and
`group_count` / `alert_count` on `auvik_alert_summary`, which is counting groups
and alerts rather than rows — so a cut is visible rather than silent. The three statistics tools
take `summary_only=true`, which returns per-column `{samples, min, max, avg,
last}` with `rows: []` — the compact answer to "is this link hot?" — and
`include_empty=false` to drop series that returned no data. `site=` scopes a
query to one tenant at the API rather than fetching everything and filtering.
Individually capped payloads: `auvik_list_networks` caps its per-network device
list at 50 and only includes it when asked, `auvik_config_changes` caps each
device's backup list at 20, `auvik_remote_access_audit` caps its free-form
`data` field at 300 characters, and `auvik_component_health` outright **refuses**
a fleet-wide list of healthy components.

The tool schemas also obey the primitive-types-only rule those connectors need
(no `Optional`/unions, no `$ref`, no `exclusiveMinimum`); `tests/test_schema_rules.py`
enforces it, because a violation makes a client drop the tool silently at import.

Stateless HTTP is enabled on the MCP app, so no session affinity is required —
which is exactly why the single-replica rule above is about *dismissal state*
and nothing else.
