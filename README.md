# Auvik MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server for
**[Auvik](https://www.auvik.com/)**, the cloud network monitoring platform. It
gives any MCP-capable AI agent — Claude Code, Claude Desktop, Copilot Studio, or
your own — fifteen tools for answering real network-operations questions about
an Auvik tenant: alerts (raw and rolled up for triage), device and interface
inventory, the subnets and VLANs the collectors actually observe, hardware
component health, which device configurations changed this week, who opened a
remote session to a switch, vendor end-of-support status, and interface/device
time-series. Fourteen of the fifteen are reads. The fifteenth — **dismissing an
alert** — is the only write this server can perform, and it is gated behind a
required reason, a read-before-dismiss rule, a duplicate-write guard, and an
audit record written in two places.

> *"What fired overnight, grouped so the collector flapping doesn't bury the
> real fault?" · "Is core-sw-01 online, and when was it last seen?" · "Which
> ports on that switch are down?" · "Did any device configuration change this
> week?" · "Who was on that router before it broke?" · "What are we running that
> is past end-of-support?"*

Two ideas run through the whole thing.

**"We found nothing" and "we couldn't reach Auvik" are never the same answer.**
Every tool returns `data_status: "ok" | "unavailable" | "refused"`. `ok` means
the answer is real, and a zero is a real zero. `unavailable` means the upstream
call failed — and the response then carries **no data keys at all**, so there is
no empty list for a model to read as "all clear". `refused` means the server
declined to make the call: a guardrail, or input it cannot accept.

**It is built from what the API does, not from what its documentation says.**
Ten things worth knowing before you build against Auvik — five of them places
where the live API flatly contradicts Auvik's own published OpenAPI file — are
documented in
**[docs/API-DEVIATIONS.md](docs/API-DEVIATIONS.md)**, each with what the
document claims, what the API actually returns, and what this server does about
it. Several of them silently produce a wrong answer if you take the
document at its word.

## Tools

### Triage

| Tool | What it answers |
| --- | --- |
| `auvik_alert_summary` | **Start here.** One row per (alert, entity, site) with active/resolved counts, worst severity seen, and a `flapping` flag — a raw list is often dominated by repeated collector reconnects |
| `auvik_list_alerts` | The alerts themselves, filtered by severity, status, device, site, name/description substring, and time window |
| `auvik_get_alert` | One alert in full — and the read that unlocks dismissing it |
| **`auvik_dismiss_alert`** | **The only write.** Dismiss one alert, with a required reason, recorded to an audit trail |

### Inventory

| Tool | What it answers |
| --- | --- |
| `auvik_find_device` | Find devices by name, IP, type, vendor, online status or site |
| `auvik_get_device` | One device in full: management/discovery state, topology neighbours, interface and component counts, newest config backup |
| `auvik_list_interfaces` | A device's interfaces: type, MAC, negotiated speed, duplex, IPs, operational and admin state |
| `auvik_list_networks` | Subnets, VLANs and wifi networks the collectors **observe** — with `scan_status`, which separates "networks here" from "networks visible from here" |

### Health

| Tool | What it answers |
| --- | --- |
| `auvik_component_health` | Fans, PSUs, disks, memory, CPUs — defaults to "is anything degraded or failed?" across the estate |
| `auvik_config_changes` | Which devices had a configuration change in the window, from backup metadata |
| `auvik_remote_access_audit` | Who opened terminal, tunnel or remote-browser sessions to which device, newest first |
| `auvik_device_lifecycle` | End-of-sale / end-of-support / warranty per device, problems only by default |

### Statistics

| Tool | What it answers |
| --- | --- |
| `auvik_interface_stats` | Interface time-series: bandwidth, utilization, packet loss, packet discard |
| `auvik_device_stats` | Device time-series: CPU, memory, storage, bandwidth |
| `auvik_device_availability` | Uptime and outage series per device |

All three statistics tools take `summary_only=true` for a compact
`{samples, min, max, avg, last}` per column instead of every row, and report
`series_total` / `series_with_data` / `series_empty` so an empty series is
visibly a real zero rather than a missing one.

## Design principles

These are enforced across every tool, because they are what make an agent's
answers trustworthy.

- **A zero is never confused with an outage.** Covered above: `ok` /
  `unavailable` / `refused`, and `unavailable` responses carry no data keys.
  The HTTP client's contract is that *every* failure raises; it never returns an
  empty structure that could be mistaken for an answer.
- **`refused` is its own status, and it is not a failure.** A refusal is the
  server declining on purpose: dismissing an alert nobody read, retrying a
  dismissal inside the propagation window, or asking for a fleet-wide list of
  every healthy CPU core. Collapsing that into `unavailable` would tell an agent
  to retry something it should not.
- **One write, one call site.** `client.post(` appears exactly once in the
  shipped server — in `tools/alerts.py` — and a test enforces that, scanning
  `server.py`, `tools/` and `scripts/` (the scripts too, because those get run
  by hand against production). There is no passthrough or "raw request" tool, so
  no new write can appear by accident.
- **Responses stay under 500 KB.** Every list tool has `limit` (max 200) and
  reports `returned_count` and `truncated`, plus a full-match count —
  `total_count` on most, and `group_count` / `alert_count` on
  `auvik_alert_summary`, which counts groups and alerts rather than rows. The
  statistics tools scale their per-series row budget down as the number of
  series goes up, and say so in `detail`, so a truncation is always visible.
- **Chat-client-safe schemas.** Primitive parameter types only, a default on
  every optional parameter, no `Optional`/unions, no `$ref`, no
  `exclusiveMinimum` — the rules Copilot Studio's connector needs. A violation
  makes some clients drop the tool *silently* at import, so
  `tests/test_schema_rules.py` checks the generated schemas rather than trusting
  review.
- **Vendor text is passed through, never invented.** Where Auvik returns
  something ugly — a raw alert template, a renamed statistic, a Title-Case enum
  — this server reports it exactly as sent and gives you the context to
  interpret it. It never synthesises prettier text and presents it as vendor
  data.

## Install

Requires **Python 3.12** and an Auvik account with an API key
(*Admin → Integrations → Auvik API*).

```powershell
git clone https://github.com/GTalksTech/auvik-mcp.git
cd auvik-mcp
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints.txt
```

On macOS/Linux, `python3.12 -m venv .venv` and `.venv/bin/python`. Versions are
pinned and resolved together with `constraints.txt` — install with `-c` or you
will get a different, untested set.

For development (adds pytest):

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c constraints.txt
```

## Credentials

Two ways, checked in this order:

```powershell
# A: environment variables (the only option inside a container)
$env:AUVIK_USERNAME = "you@example.com"     # your Auvik login email
$env:AUVIK_API_KEY  = "<your Auvik API key>"
$env:AUVIK_REGION   = "us1"                 # optional; see below

# B: the OS keyring (Windows Credential Manager / macOS Keychain / libsecret)
py -3.12 -c "import keyring,getpass; keyring.set_password('auvik-api-key', input('auvik login email: '), getpass.getpass('api key: '))"
```

**`AUVIK_REGION` is the subdomain of your Auvik URL.** If you sign in at
`https://yourcompany.us2.my.auvik.com`, it is `us2`, and the API host becomes
`https://auvikapi.us2.my.auvik.com`. The default is `us1`, which is the region
Auvik's own documentation uses — set it explicitly if that is not yours. This is
worth getting right first: a wrong region with a perfectly good key fails in a
way that looks like a bad key, not a bad hostname. A stdio launch passes no
environment at all, so set it in the keyring path's environment or accept the
default deliberately.

**Scope the key deliberately too.** Reads need Device, Interface, Network,
Component, Configuration, Statistics, Lifecycle and Tenants access.
`auvik_dismiss_alert` additionally needs **Alerts: Edit** — issue a key without
it if you never want the write to be possible, and the vendor will refuse the
POST regardless of what any code asks for.

The key is **never logged**. Startup emits an 8-character SHA-256 fingerprint of
it and nothing else; the audit records carry a caller *label*, never a
credential.

## Run it

### Claude Code

```powershell
claude mcp add auvik -- "$PWD\.venv\Scripts\python.exe" "$PWD\server.py" --stdio
claude mcp list      # auvik should appear and report connected
```

Then ask, in a session: **"What Auvik alerts fired in the last 24 hours?"**

**Tool discovery and `/health` never depend on Auvik being reachable.** With no
credential configured — or with Auvik down — the server still starts, still
lists its fifteen tools, and every call answers `data_status: "unavailable"`
with the reason in `detail`. That is correct behaviour, not a bug.

What the process *does* do at startup is kick off a **background warm thread**
that pre-fetches the device inventory, because alert projections resolve device
IDs to names through it and paying that on the first question is slow enough to
be noticeable. The warm is best-effort by contract: it runs off the request
path, and if it fails — no credential, Auvik unreachable — it leaves the server
exactly as cold as it was, so the first real tool call still tries afresh.

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "auvik": {
      "command": "/absolute/path/to/auvik-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/auvik-mcp/server.py", "--stdio"],
      "env": {
        "AUVIK_USERNAME": "you@example.com",
        "AUVIK_API_KEY": "<your Auvik API key>",
        "AUVIK_REGION": "us1"
      }
    }
  }
}
```

On Windows, use `\\`-escaped paths and `.venv\\Scripts\\python.exe`.

### HTTP mode

```powershell
$env:MCP_INBOUND_API_KEY = "<a long random string>"
.venv\Scripts\python.exe server.py          # streamable HTTP on $PORT (default 8080)
```

Clients send that key in an `X-API-Key` header; `GET /health` is exempt and
needs neither the key nor Auvik. **Unset `MCP_INBOUND_API_KEY` means every
request except `/health` is rejected** — fail-closed, deliberately.

A `Dockerfile` is included. Read
**[docs/HOSTING.md](docs/HOSTING.md)** before deploying it: there is one
load-bearing constraint, **run exactly one replica**, and it is explained there.

## What Auvik's API actually does

Full catalogue in **[docs/API-DEVIATIONS.md](docs/API-DEVIATIONS.md)**. The
three you are most likely to hit:

**1 · `filter[thruTime]` is required on the `/v1/stat/*` calls** — the OpenAPI
file marks it optional and says it "defaults to current time". It does not.
Omitting it is an HTTP 400 on every statistics endpoint this server uses
(deviation **D-4**, which also says exactly which ones were tested).
This server always sends it, so you never have to; but if you are writing your
own client, this is the one that takes out all your statistics calls on day one.

**2 · Statistics `data` is a list, and timestamps are `unix_mins`.** The OpenAPI
file types `data` as an object; the live API sends a **list** of series (**D-1**).
And column 0 of every statistics row is labelled `Recorded At` with unit
`unix_mins` — **MINUTES since the epoch**. Read it as seconds and every point
lands in January 1970; as milliseconds, in the 1970s. Neither looks obviously
wrong on a chart. **Read the `units` array**: it names the scale of every
column, and the row shape is not fixed across statistics.

**3 · Read an alert's `description`, not its `name`.** A large fraction of real
alert names come back as Auvik's raw, un-interpolated template — literally
`$deviceInterfaces.name Down on $system.name` (**D-6**). That is Auvik's own
stored data. The `description` field is fully interpolated and is the one to put
in front of a human. Both are in every projection, and `auvik_list_alerts`
matches its `name=` filter against the description as well.

Two more worth knowing. Auvik **renames the statistic you asked for** in its
reply (`utilization` → `interfaceUtilization`), so each series reports both
`stat` (Auvik's) and `requested_stat` (yours), and the mismatch is normal
(**D-8**). And a `site` is the Auvik **tenant's `domainPrefix`**, not a place
name — so the Boston office might be `acme-bos`, and searching for "boston"
finds nothing. Every site-aware tool lists the valid values under `sites`, and
an unmatched site comes back as a real zero whose `detail` names the ones that
exist.

Two of those `sites` lists differ, deliberately. `auvik_find_device` reports the
sites that actually **have devices**, because that is what it searches.
Everything else reports every tenant from `/v1/tenants`. A tenant can hold
alerts and no devices, so asking the alert tools to reuse the device-derived
list would make them call a real site unknown — a fabricated zero produced by
answering with a list built for a different question.

## The write path

`auvik_dismiss_alert` is the only non-GET this server can make. In order, first
failure refuses and **no POST is issued**:

1. a `reason` is required;
2. the alert must have been returned by a read **in this process** — a summary
   does not count, since the model has seen a group, not an alert;
3. it must not already have been dismissed by this process. This is the rule
   that actually prevents a duplicate write, because the re-read below is
   eventually consistent (**D-7**) and would say "not dismissed" for a couple of
   minutes. It is checked **before** the re-read so that a retry is refused
   locally and deterministically — including while Auvik is unreachable, where
   the re-read would instead fail and report `unavailable`, the one status that
   invites another retry;
4. re-read from Auvik; already dismissed → refused;
5. POST;
6. audit.

Every successful dismissal is recorded **twice**: a line in
`audit/dismissals.jsonl` and a `dismissal_audit` line on the process log stream
(stderr in stdio mode, stdout in HTTP mode). Both carry the same record —
timestamp, alert id, name, severity, entity, device name, the caller's reason,
and the caller key label; never the key itself.

`audit/dismissals.jsonl` is **gitignored** — it holds real operational evidence —
and it is append-only; nothing here rewrites or prunes it. The log copy exists
because the file cannot be trusted to survive: on container-local disk it is
ephemeral, and a read-only root filesystem stops the append entirely. **The file
append is best effort.** If it fails, the dismissal still returns `ok` — the
write really happened, and reporting `unavailable` would invite a retry that
dismisses twice — with a warning in `detail` telling you to capture the record
from the process log.

Auvik's dismissal is eventually consistent: a re-read within a couple of minutes
can still report `dismissed: false`. That is propagation delay, not failure. The
tool says so in `detail`, and a retry inside that window is **refused** rather
than issuing a second write.

## Testing

```powershell
.venv\Scripts\python.exe -m pytest -q
```

**372 tests, entirely offline.** The HTTP client is replaced by a fake serving
JSON:API fixtures, and an autouse fixture makes both a real credential load and a
real socket raise — so the suite cannot reach the network even by accident, and
cannot be made flaky by a workstation that happens to have a credential in its
keyring.

The committed fixtures under `tests/fixtures/synthetic/` are **entirely
invented** and are regenerated by `tests/build_synthetic_fixtures.py`. They
deliberately carry production's *spelling* — Title-Case lifecycle enums, prefixed
`statType` values, an alert with no entity, an interface stats response where 13
of 17 series are empty — so the offline tier cannot quietly disagree with the
live API on the things D-5, D-8 and D-9 are about.

There is an optional second tier: `tests/record_fixtures.py` records real
responses to `tests/fixtures/recorded/` (gitignored), and
`AUVIK_FIXTURES=recorded pytest` runs the same suite against them. A set of
tier-invariant tests runs on both and **skips rather than passes vacuously**
when a tier has no records for an endpoint — because a test that cannot fail is
not a test.

`scripts/live_smoke.py` and `scripts/live_read_all.py` exercise the read tools
against a real tenant. Both are **GET-only** and issue no writes of any kind.

## Security notes

- **One write, and it is scoped.** `client.post(` exists at a single call site,
  enforced by a test. Issue an API key without *Alerts: Edit* and the write
  becomes impossible at the vendor as well.
- **Read before dismiss.** Nothing can be dismissed that this process has not
  read, so an agent cannot act on an alert id it invented or carried over from
  somewhere else.
- **Auditable by construction.** Two copies of every dismissal record, one of
  which survives the container, both including the caller-supplied reason.
- **The credential never lands in a log, a fixture or a response.** Startup logs
  an 8-character fingerprint. `AuvikCredentials.__repr__` is overridden so the
  key cannot leak through an exception traceback or a debugger.
- **Inbound auth fails closed** in HTTP mode, with a constant-time comparison,
  and `/health` is the only exempt path.

## See also

A sibling server built on the same envelope contract and skeleton: [GTalksTech/sonicwall-mcp](https://github.com/GTalksTech/sonicwall-mcp).

## License

MIT — see [LICENSE](LICENSE). Everything in this repository is my own work.

Auvik's OpenAPI document is deliberately **not** vendored here: it is Auvik's
copyright, and shipping it inside an MIT-licensed repo would claim a grant over
it that is not mine to give. `docs/API-DEVIATIONS.md` links to Auvik's
documentation and records the SHA-256 of the revision its claims were checked
against, so you can verify them against your own copy.
