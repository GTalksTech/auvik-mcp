# What Auvik's API actually does

Ten things worth knowing before you build against Auvik's API. Eight are
behaviours of the live API, learned by pointing this server at a real tenant;
**D-2** was learned by reading the vendor's document carefully, and **D-3** is
not about Auvik at all. They sort into four kinds, and the distinction matters —
"the document is wrong" and "the document is silent" call for different amounts
of caution:

* **Five are flat contradictions** of Auvik's own published OpenAPI document:
  **D-1** (declared an object, sends a list), **D-4** (declared optional,
  actually required), **D-6** (example shows readable text, often sends a raw
  template), **D-9** (declared camelCase, sends Title-Case), and **D-10**
  (the document's own `info.description` promises every date is
  `YYYY-MM-DDTHH:MM:SS.sssZ`; this one is not).
* **Three are behaviours the document is simply silent about**: **D-7**
  (dismissal is eventually consistent), **D-8** (the reply renames the statistic
  you asked for), and **D-5** (the `entity` relationship has no `required`
  marker, so nothing promises it is always there — the document does not
  mislead, it just never warns).
* **D-2** is a trap in how the document has to be *read*, not an error in it.
* **D-3** is not an API issue at all — it is a defect in this server, kept in
  the sequence for its lesson.

**D-10 is the interesting one, because the document contradicts *itself*.** Its
`info.description` promises *"All date formats are formatted in the format of
YYYY-MM-DDTHH:MM:SS.sssZ"*, and `warrantyExpirationDate`'s example obeys that —
while that same field's own description warns the value *"is not guaranteed to
be a date"*. The live API sides with the warning. So it counts as a
contradiction above, and it is also the one place the document is **ahead** of
anyone who reads only types and examples.

They are numbered `D-1`…`D-10` and the code cross-references those numbers, so
the numbering is stable.

This is probably the most useful file in the repo if you are integrating with
Auvik yourself, whether or not you use this server. Every entry says the same
three things: **what the document says**, **what the live API does**, and **how
this server handles it**.

Two of them — **D-4** and **D-9** — are not cosmetic. Each one, taken on the
vendor document's word, produces a confident, successful-looking wrong answer:
D-4 makes every statistics call fail outright, and D-9 makes an end-of-support
report say "nothing is expired" while sitting on records where Auvik is
reporting `Expired`. No offline test could have caught either, because the
fixtures were built from the same document that was wrong.

The authoritative copy of the vendor document is checked in at
`reference/auvik-openapi-v1.json` so you can see the disagreement for yourself.
That file is **Auvik's**, reproduced unmodified, and is not covered by this
repo's MIT licence — see the note at the end of the README.

| # | Subject | Vendor document says | Live API does | Severity |
|---|---|---|---|---|
| **D-1** | `/v1/stat/*` response `data` | an object | a **list** of series | Silent wrong answer if you index it |
| **D-2** | `deviceAvailabilityStatistics.attributes` | *(appears empty)* | carries `stats` via `allOf` | Not a deviation — a reading trap |
| **D-3** | *(not an API issue)* | — | — | A defect in this server, kept in the numbering |
| **D-4** | `filter[thruTime]` on `/v1/stat/*` | optional, "defaults to current time" | **required**; omitting it is HTTP 400 | Three tools dead on arrival |
| **D-5** | alert `entity` relationship | *(listed, with no `required` marker and no warning)* | **absent** on collector-level alerts | Blank device fields are legitimate |
| **D-6** | alert `name` | interpolated text | often a raw, un-interpolated template | Unreadable alert names |
| **D-7** | `POST /v1/alert/dismiss/{id}` | *(silent)* | **eventually consistent**, ~2 min | Duplicate writes |
| **D-8** | `attributes.statType` in the reply | *(silent)* | renamed: `<family> + <Stat>` | Looks like a failed request |
| **D-9** | lifecycle status enums | camelCase (`expired`) | **Title-Case** (`Expired`) | Fabricated zero |
| **D-10** | `warrantyExpirationDate` | ISO example, but warns it *"is not guaranteed to be a date"* | space instead of `T`, `+0000` instead of `Z` | Breaks the next person's date maths |

---

## D-1 — `/v1/stat/*` `data` is a list, not an object

**What the document says.** The 200 response schemas
`interfaceStatisticsRead`, `deviceStatisticsRead` and
`deviceAvailabilityStatisticsRead` — returned by the operations
`readInterfaceStatistics`, `readDeviceStatistics` and
`readDeviceAvailabilityStatistics` — each declare `properties.data` as a `$ref`
to their `…StatisticsResourceObject`, and each of those is `"type": "object"`:
a single resource object, not an array. (Follow the `$ref`; the `type` is on the
target, not on `data` itself.)

**Where the document contradicts itself.** Those same three operations accept
`page[first]`, `page[after]`, `page[last]` and `page[before]`, plus
`filter[interfaceType]` on the interface one and `filter[deviceType]` on the
other two. You cannot cursor-paginate or
type-filter a single object. One half of the vendor's own document had to be
wrong.

**What the live API does.** It sends a **list**: one series for
`/v1/stat/device/*`, one for `/v1/stat/deviceAvailability/*`, and one **per
interface** for `/v1/stat/interface/*` — so a single device routinely answers
with many series. The pagination parameters were the honest half of the
document.

**How this server handles it.** It tolerates both shapes and assumes neither.
`tools/stats.py::_as_list` and `auvik_client.py::_data_items` normalise `data`
to a list whichever way it arrives. That second one matters more than it looks:
a bare `list(body["data"])` on a dict yields the dict's **keys**, so an object
response would have produced a successful-looking answer containing field names
instead of data — the exact class of silent failure this server is built to
prevent. The defence stays even though live behaviour is settled, because it
costs nothing and some deployment may yet match the published document.

## D-2 — `deviceAvailabilityStatistics.attributes` is not empty (recorded to stop a re-investigation)

**What the document appears to say.** A first read of
`deviceAvailabilityStatisticsAttributes` suggests it has no properties — no
`stats` array, so no data.

**What it actually says.** The schema is
`allOf: [statisticsAttributes, {firstDiscoveredDateTime}]`. It carries
`reportPeriod`, `interval`, `statType` and `stats` exactly like its device and
interface siblings, plus one extra field.

**How this server handles it.** Normally. This entry exists because any tool or
generator that dereferences `$ref` **without following `allOf`** will conclude
the availability endpoint returns nothing useful and quietly skip it. If you are
generating a client from this document, check your generator's `allOf` support
before you believe an empty schema.

## D-3 — not an API deviation: the test suite was polluting the real audit trail

Kept in the sequence because the code cross-references these numbers by name
(`D-1`, `D-4`, `D-7`, `D-9`…), so renumbering to close a gap would silently
change what an existing reference points at — and mainly because the lesson
generalises well beyond this repo.

**What happened.** A full test run left fabricated lines in the repo's real
`audit/dismissals.jsonl` — plausible-looking records for alerts that were never
dismissed. No live dismissal ever occurred: the writes came from a test driving
the real tool through the MCP layer against a fake client, so no network call
was made. It still mattered, because that JSONL line is the entire compensating
control for a write API that has no reason field of its own, and an audit log
seeded with realistic fabrications is worse than no audit log — because it is
trusted.

**The cause, which is the reusable part.**

```python
def dismiss_alert(..., audit_path: Path = AUDIT_PATH):   # WRONG
```

A mutable-looking module constant used as a **default argument** binds at import
time. Patching `tools.alerts.AUDIT_PATH` afterwards has no effect, and the MCP
wrapper (which correctly exposes no `audit_path` parameter) always wrote to the
production path.

**The fix.** `audit_path` defaults to `None` and resolves to `AUDIT_PATH` **at
call time**, so the test fixtures can redirect it. Two tests were added, one of
which asserts the repo's audit file is byte-identical before and after a
dismissal driven through the full MCP layer.

## D-4 — `filter[thruTime]` is REQUIRED, and the document says optional

**What the document says.** `filter[thruTime]` is optional, with the description
*"Timestamp to which you want to query (defaults to current time)"*

**What the live API does.** It rejects the call. Every request to the three
statistics endpoints this server uses — `/v1/stat/device/{statId}`,
`/v1/stat/deviceAvailability/{statId}` and `/v1/stat/interface/{statId}` —
returns **HTTP 400** without it, identically across tenants, device ids,
intervals and timestamp formats:

```
Variable "$filter" got invalid value { deviceId: "...", fromTime: "...",
interval: "hour", statId: "cpuUtilization" };
Field "thruTime" of required type "DateTime!" was not provided.
```

The `!` is GraphQL for non-nullable. Auvik's statistics API is GraphQL-backed
and the JSON:API surface is a thin wrapper over it, which is why the error
arrives in GraphQL's vocabulary rather than JSON:API's.

**How this server handles it.** `tools/stats.py::_run` always sends
`filter[thruTime]`, computed as "now" **on the same clock that produced
`fromTime`**, so the window can never come out inverted and it tracks the
*clamped* window rather than the requested one. Callers never have to know.

**Scope, stated precisely.** The document declares `filter[thruTime]` on seven
of its eight `/v1/stat/*` operations — the eighth, `/v1/stat/oid/{statId}`,
declares neither it nor `filter[fromTime]`. This server exercises three of them,
so treat the 400 as confirmed for those three and *likely* for the four other
operations that declare the parameter; check before you rely on it.

**Why it is listed as the severe one.** It took out all three statistics tools
on their first live run, and it is unfindable offline: a fixture built from the
document reproduces the document's mistake.

## D-5 — about one alert in ten has no `entity` at all

**What the document says.** Alerts have an `entity` relationship. It is listed
without any note that it may be absent.

**What the live API does.** Roughly a tenth of real alerts carry no `entity`.
They are collector-level events — the Auvik collector approving, reconnecting,
disconnecting — which are about the monitoring system itself rather than about a
monitored device.

**How this server handles it.** The projection degrades to empty strings, never
`None` and never a `KeyError`: every accessor is written `(x or {}).get(...)`,
so `entity_type`, `entity_id`, `device_id` and `device_name` all come back `""`.
A blank `device_name` on an alert is therefore **legitimate data**, not a
lookup failure.

**What it exposed about testing.** Every synthetic fixture had an `entity`, so
the test named `test_alerts_without_an_entity_degrade_to_empty_strings` was
passing **vacuously** — its loop body `continue`d on every item. A collector
alert with the relationship omitted is now in the committed fixtures. A test
that cannot fail is not a test.

## D-6 — a large fraction of alert `name`s are raw templates

**What the document says.** `name` is *"This alert's name"*, with the example
`"Printer - Low Ink Level"` — ordinary readable text. Nothing hints that the
field may arrive as an un-substituted template.

**What the live API does.** A substantial minority of alerts — in the sample
measured, more than two in five over a 30-day window — come back with `name`
still holding Auvik's **un-interpolated template**:

```
$deviceInterfaces.name Down on $system.name
```

This is Auvik's own stored data, visible in the raw JSON:API record. It is not a
projection bug.

**What to use instead.** `description`, which was fully interpolated on **every**
one of those alerts:

> `Interface ge-0/0/1 (LAN), ge-0/0/5 (WAN), mgmt0 (MGMT) … on <device> is down.
> Check device and restore connectivity immediately.`

(Example invented end to end — names, role labels and all. The shape is the
point: every `$placeholder` in the template is resolved in `description`, and
none of them is in `name`.)

**How this server handles it.** Both fields are in every alert projection, and
`auvik_list_alerts` matches its `name=` filter against the **description as well
as the name** — otherwise searching for "interface down" misses the alerts whose
name is still a template.

**What it deliberately does not do:** synthesise a `name` from the description,
or substitute the resolved device name into `$system.name`. That would mean
inventing text the vendor did not send and presenting it as vendor data. If you
want prettier output, the honest options are to have your agent prefer
`description` when `name` contains `$` (a prompt-level choice), or to fix the
alert templates in Auvik, which is where the defect actually lives.

## D-7 — dismissal is eventually consistent, and a naive idempotency check fails open

**What the document says.** Nothing about timing. `POST /v1/alert/dismiss/{id}`
returns 200 with an empty body.

**What the live API does.** The write is applied, but **not immediately
visible**. A re-read half a second after a successful POST still reported
`dismissed: false`; the alert flipped to `true` only about two minutes later.

**Why that is dangerous rather than merely surprising.** The obvious way to make
a dismissal idempotent is to re-read the alert and refuse if it is already
dismissed. Inside the propagation window that read is **stale**, so the rule
fails **open**: a retry sails through, issues a second POST, and writes a second
audit line for one logical action. An audit trail that double-counts writes is
worse than none, because it is trusted.

There is a second trap, aimed at the agent rather than the server: an LLM told
`ok`, which then re-reads, sees `dismissed: false` and concludes the write
failed, will retry — which is how one dismissal becomes three.

**How this server handles it.**

1. `DISMISSED_ALERT_IDS`, a process-scoped set of alerts this process has
   actually dismissed. Inside the propagation window it is the only authority
   that is not stale, so it — not the re-read — is what prevents the duplicate
   write. It is checked **before** the re-read so a retry is refused locally,
   without needing Auvik to answer (during an outage the re-read would raise and
   the caller would be told `unavailable`, which invites another retry). It is
   armed immediately after the POST returns, before any I/O that could fail.
2. The success payload's `detail` says so in words: *"It is eventually
   consistent … That is propagation delay, not a failure — do not retry."*
3. A retry inside the window is `refused`, not silently re-POSTed.

Because that set is process state, **the server must run as a single replica**.
See `docs/HOSTING.md`.

## D-8 — the reply renames the statistic you asked for

**What the document says.** `statId` is the path parameter. Nothing says the
response renames it.

**What the live API does.** `attributes.statType` in the reply is never the
string you sent. It is `<family prefix> + <stat capitalised>`:

```
interface     requested=bandwidth          -> interfaceBandwidth
interface     requested=utilization        -> interfaceUtilization
interface     requested=packetLoss         -> interfacePacketLoss
interface     requested=packetDiscard      -> interfacePacketDiscard
device        requested=cpuUtilization     -> deviceCpuUtilization
device        requested=memoryUtilization  -> deviceMemoryUtilization
device        requested=storageUtilization -> deviceStorageUtilization
device        requested=bandwidth          -> deviceBandwidth
availability  requested=uptime             -> deviceAvailabilityUptime
availability  requested=outage             -> deviceAvailabilityOutage
```

**Why it matters even though nothing is lost.** An agent that asked for
`cpuUtilization` and reads back `deviceCpuUtilization` sees a mismatch, and may
retry a call that worked or apologise for a result that is correct.

**How this server handles it.** Every series reports **both**: `stat` is Auvik's
value passed through unaltered, and `requested_stat` is the caller's own string.
The three statistics tool docstrings say the mismatch is normal.

**What it exposed about testing.** The synthetic fixtures originally carried the
*bare* stat name, which made the projection look like it echoed the request when
it was actually passing through a value that merely coincided. The fixtures now
emit the prefixed forms, so the offline tier cannot disagree with production on
this.

## D-9 — lifecycle enums are Title-Case, and the document says camelCase

**What the document says.** All four `deviceLifecycleAttributes` statuses carry
the enum `covered | available | expired | securityOnly | unpublished | empty`,
each with the example `available`.

(`deviceWarrantyAttributes` is a different story and is **not** part of this
deviation: it declares no enum at all, and its own examples are already
Title-Case — `Covered`, `Available`, `Not Covered`. On warranty the document
and the API agree. The warranty rows below are shown only because this server
merges the two and you will see both spellings side by side.)

**What the live API does.** Title-Case, on every field, on every record:

```
lifecycle.salesAvailability                  Expired / Unpublished / Available
lifecycle.softwareMaintenanceStatus          Expired / Unpublished / Available
lifecycle.securitySoftwareMaintenanceStatus  Available / Unpublished
lifecycle.lastSupportStatus                  Available / Unpublished
warranty.warrantyCoverageStatus              Available / Expired
warranty.serviceCoverageStatus               Covered
```

(`SecurityOnly` is **not** in that list — no probe has returned one. This server
and its fixtures use that spelling by applying the same Title-Case rule to the
documented `securityOnly`; it is an inference, and it is flagged here as one.)

**Why this is the other severe one.** The natural implementation is
`PROBLEM_STATUSES = {"expired", "securityOnly"}` matched exactly, straight from
the document. But `"Expired" not in {"expired", "securityOnly"}`. Run against
live data, that comparison flags **nothing** — including on devices Auvik is
reporting as `Expired` right there in the same payload.

`auvik_device_lifecycle` defaults to `problems_only=True`. So it would have
returned `data_status: "ok"`, `devices: []`, `total_count: 0` — a confident,
successful-looking *"no device is end-of-support"* — which is the single failure
mode this server exists to prevent, reached through an enum's capitalisation
rather than through an outage. And **no offline test could have caught it**,
because every synthetic fixture was generated from the same document.

**How this server handles it.**

1. Comparison folds case: `{"expired", "securityonly"}` matched against
   `value.strip().lower()`. Both the documented camelCase and the live
   Title-Case flag correctly, so this stays right if Auvik ever "fixes" it.
2. Projected **values pass through exactly as Auvik sent them**. Same call as
   D-6 and D-8: presenting text the vendor did not send, as if it were vendor
   data, is the wrong trade for tidiness. `counts_by_last_support` therefore
   reports Auvik's own spelling.
3. The synthetic fixtures emit the **live Title-Case forms**, so the offline
   tier cannot disagree with production — while a separate test pins the
   documented camelCase, so both spellings are covered and neither can regress.

**Also confirmed while checking the above, and not a deviation:** a warranty record's
`id` equals the corresponding lifecycle record's
`relationships.device.data.id`, on every record — which is what makes merging
the two by id sound, since warranty records carry no device relationship of
their own.

## Two endpoints whose shape is still unverified

Not a deviation — an honest gap, and the one place this file is running on the
vendor document's word rather than on observation.

`GET /v1/inventory/component/info` and `GET /v1/inventory/entity/audit` both
answer HTTP 200 and accept every documented filter name, but **we have not yet
seen a populated response from either** — every probe so far came back with an
empty collection. So `auvik_component_health` and `auvik_remote_access_audit`
are built on the OpenAPI file's declared shape alone, and that is the same
source that was wrong about `thruTime` (D-4), about `data` being an object
(D-1), and about enum casing (D-9).

If either tool returns something odd against your data, suspect the projection's
assumed shape before you suspect your data.

## D-10 — `warrantyExpirationDate` is not ISO 8601, unlike every other timestamp

**What the document says — and it is the one place the document is *ahead* of
you.** Its `example` is `2015-07-31T00:00:00.000Z`, ISO like every sibling, so a
generated client will type it as a timestamp. But its `description` carries a
warning no other timestamp field has:

> *"Warranty expiration date for this device. Value is what is returned by Cisco
> device, which is not guaranteed to be a date."*

So the vendor already told us this field is whatever the hardware said. Worth
reading descriptions, not just types.

**What the live API does.** Every timestamp this server actually reads —
`detectedOn`, `backupTime`, `lastSeenTime`, `lastModified`, `dateStarted`, the
statistics `reportPeriod` — is `YYYY-MM-DDTHH:MM:SS.sssZ`. This one is not:

```
warrantyExpirationDate: '2029-06-30 00:00:00.000+0000'
```

A **space** instead of `T`, and **`+0000`** instead of `Z`.

**How this server handles it.** It doesn't parse it. The field is projected
verbatim as `warranty_expiration` and never compared to `now` or used to compute
"expires in N days", so nothing is broken today.

**And it is not the only one.** Searching the OpenAPI file's own examples turns
up the same shape elsewhere — `asmClientAttributes.dateAdded` is documented as
`2022-11-24 04:22:08.585696+00`, and the `/v1/stat/snmppoller/*` time parameters
as `2022-03-10 01:00:00`. So this is a family, not a one-off: **do not assume any
Auvik date field is ISO 8601 because its neighbours are.** Check the field.

**Recorded because the next change is where it bites.** Anyone adding
"warranties expiring in the next 90 days" will reach for
`datetime.fromisoformat`, which accepts this string on Python 3.12 and returns a
**timezone-aware** datetime — which then raises `TypeError` the moment it is
compared with a naive `now`, and would have raised outright on Python 3.10.
Sorting these strings is safe, because the format is lexicographically ordered
within itself. Mixing one into a comparison with any other Auvik timestamp is
not.

---

## The pattern, if you take one thing away

Five of these ten (D-1, D-4, D-6, D-9, D-10) are cases where a published vendor
document and the running API flatly disagree, and in **every** case the running
API won. Three of those five (D-1, D-4, D-9) could not have been caught by any
test built from that document, because the fixtures inherit the document's
mistake. Three more (D-5, D-7, D-8) are behaviours the document never mentions —
which is its own lesson: an absent warning is not an assurance.

That is the argument for the two rules this repo follows: **observe an endpoint
live before writing code against it**, and **make the offline fixtures carry
production's spelling** — including the ugly parts — so the two tiers can never
quietly disagree.
