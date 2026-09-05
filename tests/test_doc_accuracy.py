"""The documentation must not restate claims the live API disproved.

Auvik's own OpenAPI document is wrong about `filter[thruTime]` (D-4) and about
the type of `/v1/stat/*` `data` (D-1), and its dismissal endpoint is eventually
consistent (D-7). The code is already correct on all three. The risk this gate
exists to stop is the *opposite* direction: a future reader trusting the vendor
document (or stale prose in this repo) and "correcting" working code toward it.

So these tests assert on the three files a reader actually reads — README.md,
docs/API-DEVIATIONS.md and docs/HOSTING.md — and fail if any of them starts
agreeing with the vendor document instead of with the API.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README_PATH = ROOT / "README.md"
DEVIATIONS_PATH = ROOT / "docs" / "API-DEVIATIONS.md"
HOSTING_PATH = ROOT / "docs" / "HOSTING.md"

README = README_PATH.read_text(encoding="utf-8")
DEVIATIONS = DEVIATIONS_PATH.read_text(encoding="utf-8")
HOSTING = HOSTING_PATH.read_text(encoding="utf-8")


def _lines_with(text: str, needle: str) -> list[str]:
    return [ln for ln in text.splitlines() if needle in ln]


def _flat(text: str) -> str:
    """Markdown wraps prose at arbitrary columns and sprinkles emphasis marks, so
    a phrase can be split across a newline or broken by `**`. Collapse both, or
    the gate is dodged by a reflow rather than by a real correction."""
    return " ".join(text.replace("**", "").replace("`", "").split()).lower()


def _section(text: str, heading: str) -> str:
    """The body of one `## ` section, up to the next heading or horizontal rule."""
    assert heading in text, f"heading {heading!r} is gone"
    body = text.split(heading, 1)[1]
    for stop in ("\n## ", "\n---"):
        body = body.split(stop, 1)[0]
    return body


# --- D-4: filter[thruTime] is REQUIRED, not optional -------------------------

ATTRIBUTION = ("document", "openapi", "vendor", "auvik-openapi-v1.json", "d-4", "| d-4 |")


def test_docs_never_call_thrutime_optional_in_their_own_voice():
    """Omitting filter[thruTime] 400s every /v1/stat/* call. See D-4.

    A deviations file has to QUOTE the vendor's wrong claim, so a blanket ban on
    the word "optional" near thruTime would be unmaintainable. What must never
    appear is the claim in this repo's own voice — unattributed. So every line
    that calls thruTime optional has to carry an attribution marker on the same
    line, and the line that says it is required has to exist.
    """
    for name, text in (("README", README), ("API-DEVIATIONS", DEVIATIONS)):
        lines = _lines_with(text, "thruTime")
        assert lines, (
            f"{name} no longer mentions thruTime at all, so this gate just asserted nothing. "
            "D-4 is the deviation that killed three tools; it must stay documented."
        )
        for ln in lines:
            low = _flat(ln)
            assert "may be omitted" not in low, f"{name} says thruTime may be omitted: {ln!r}"
            assert "defaults to now" not in low, f"{name} repeats the vendor default claim: {ln!r}"
            if "optional" in low:
                assert any(a in low for a in ATTRIBUTION), (
                    f"{name} calls filter[thruTime] optional without attributing it to the vendor "
                    f"document — in this repo's own voice that claim is false: {ln!r}"
                )


def test_deviations_marks_thrutime_required_and_shows_the_error():
    d4 = _section(DEVIATIONS, "## D-4")
    flat = _flat(d4)
    assert "filter[thrutime]" in flat, "D-4 no longer names the parameter"
    assert "required" in flat, "D-4 does not say filter[thruTime] is required"
    assert "400" in d4, "D-4 does not record that omitting it is an HTTP 400"
    assert 'of required type "DateTime!" was not provided' in d4, (
        "D-4 no longer carries the literal error the live API returns"
    )


def test_readme_warns_that_thrutime_is_required():
    flat = _flat(README)
    assert "d-4" in flat, "README does not cross-reference D-4"
    assert "filter[thrutime] is required" in flat, (
        "README does not tell a reader that filter[thruTime] is required on every /v1/stat/* call"
    )


# --- D-1: /v1/stat/* `data` is a list, the vendor spec says object -----------

def test_deviations_says_stat_data_is_a_list_not_an_object():
    d1 = _section(DEVIATIONS, "## D-1")
    flat = _flat(d1)
    assert "object" in flat, "D-1 does not say the OpenAPI file types `data` as an object"
    assert "list" in flat, "D-1 does not say the live API sends a list"
    assert "keys" in flat, (
        "D-1 no longer explains why the object shape is dangerous: list() on a dict yields its keys"
    )


def test_readme_says_stat_data_is_a_list():
    flat = _flat(README)
    assert "d-1" in flat, "README does not cross-reference D-1"
    assert "sends a list" in flat, "README does not say the live statistics API sends a list of series"


# --- the units trap ----------------------------------------------------------

def test_readme_warns_that_stat_timestamps_are_unix_mins():
    """The single most likely thing for a consumer to get wrong: column 0 is
    MINUTES since the epoch. Read as seconds it lands in 1970; as milliseconds,
    in the 1970s. Neither looks obviously wrong on a chart."""
    flat = _flat(README)
    assert "unix_mins" in flat, "README never mentions unix_mins"
    assert "minutes since the epoch" in flat, (
        "README does not spell out that unix_mins means MINUTES since the epoch"
    )
    assert "units" in flat, "README does not tell the reader to read the units array"


# --- D-6: read the description, not the name ---------------------------------

def test_readme_says_to_read_description_not_name_on_alerts():
    flat = _flat(README)
    assert "d-6" in flat, "README does not cross-reference D-6"
    assert "description" in flat, "README does not point at the description field"
    assert "$deviceinterfaces.name" in flat, (
        "README no longer shows what an un-interpolated alert name actually looks like"
    )


def test_deviations_d6_refuses_to_synthesise_vendor_text():
    d6 = _flat(_section(DEVIATIONS, "## D-6"))
    assert "description" in d6, "D-6 does not name the field that is interpolated"
    assert "inventing" in d6 or "synthesise" in d6, (
        "D-6 no longer records the decision NOT to fabricate a name Auvik did not send"
    )


# --- D-7: the dismissal re-read is eventually consistent ---------------------

def test_deviations_d7_states_eventual_consistency_and_the_ordering():
    d7 = _section(DEVIATIONS, "## D-7")
    flat = _flat(d7)
    assert "eventually consistent" in flat, "D-7 does not state that dismissal is eventually consistent"
    assert "fails open" in flat, (
        "D-7 no longer explains that the re-read guard fails OPEN inside the propagation window"
    )
    assert "before the re-read" in flat, (
        "D-7 does not say the in-process dismissed-id check must come BEFORE the re-read"
    )


def test_readme_documents_the_dismiss_guardrails_in_order():
    """The README must state the ordering, and state it accurately.

    What the in-process set buys is the duplicate-write guard itself; what the
    ORDER buys is a retry being refused locally even when Auvik is unreachable.
    The behaviour is pinned by
    test_dismiss_guardrails.py::test_a_retry_is_refused_locally_even_when_auvik_is_unreachable;
    this only checks that the prose has not drifted away from it."""
    flat = _flat(README)
    assert "d-7" in flat, "README does not cross-reference D-7"
    assert "before the re-read" in flat, (
        "README does not state that the duplicate-dismiss check precedes the re-read"
    )
    assert "refused" in flat, "README does not say a retry inside the window is refused"


# --- the audit trail's durability contract -----------------------------------

def test_readme_documents_the_audit_trail():
    flat = _flat(README)
    assert "audit/dismissals.jsonl" in flat, "README does not mention the audit trail"
    assert "gitignored" in flat, "README does not say the audit trail is gitignored"
    assert "dismissal_audit" in flat, "README does not mention the durable log copy"


def test_readme_states_the_audit_file_append_is_best_effort():
    flat = _flat(README)
    assert "best effort" in flat or "best-effort" in flat, (
        "README does not say the audit file append is best-effort"
    )
    assert "still returns ok" in flat, (
        "README does not state that a completed write is never reported unavailable"
    )


# --- envelope contract -------------------------------------------------------

def test_readme_distinguishes_all_three_statuses():
    flat = _flat(README)
    for status in ("ok", "unavailable", "refused"):
        assert f'"{status}"' in flat, f"README does not describe data_status {status!r}"
    assert "no data keys" in flat, (
        "README does not state that an unavailable response carries no data keys — "
        "the whole point of the envelope"
    )


# --- D-9 / D-10 --------------------------------------------------------------

def test_deviations_d9_records_the_fabricated_zero():
    d9 = _flat(_section(DEVIATIONS, "## D-9"))
    assert "title-case" in d9, "D-9 does not say the live enums are Title-Case"
    assert "camelcase" in d9, "D-9 does not say the OpenAPI file documents camelCase"
    assert "problems_only=true" in d9, "D-9 does not tie the casing to the tool's DEFAULT view"
    assert "folds case" in d9, "D-9 does not record that the comparison folds case"


def test_deviations_d10_warns_against_parsing_the_warranty_date():
    d10 = _flat(_section(DEVIATIONS, "## D-10"))
    assert "fromisoformat" in d10, "D-10 no longer warns about datetime.fromisoformat"
    assert "aware" in d10, "D-10 does not explain the naive/aware comparison trap"


def _deviation_numbers() -> list:
    """The D-numbers that actually have sections, read from the headings rather
    than hard-coded - a hard-coded range(1, 11) silently ignores a D-11."""
    import re  # noqa: PLC0415

    return sorted(int(m) for m in re.findall(r"^## D-(\d+) ", DEVIATIONS, re.M))


def test_every_deviation_has_a_section_and_the_numbering_has_no_gaps():
    numbers = _deviation_numbers()
    assert numbers, "no deviation sections at all"
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"deviation numbering has a gap or a duplicate: {numbers}"
    )


def test_both_documents_state_the_number_of_deviations_they_contain():
    """The prose says "Ten things" and "Five of these ten". Adding a D-11 without
    touching either left the whole suite green."""
    n = len(_deviation_numbers())
    words = {9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}
    word = words.get(n)
    assert word, f"{n} deviations - extend the word map in this test"
    assert f"{word} things" in _flat(DEVIATIONS), (
        f"docs/API-DEVIATIONS.md contains {n} deviations but does not say so"
    )
    assert f"{word} things" in _flat(README), (
        f"README.md does not state the deviation count of {n}"
    )
    assert f"of these {word}" in _flat(DEVIATIONS), (
        f"the closing summary does not say 'of these {word}'"
    )


NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _contradiction_counts() -> dict:
    """Every place either document states how many deviations are outright
    contradictions of the vendor file, as {where: number}."""
    import re  # noqa: PLC0415

    found = {}
    m = re.search(r"\*\*(\w+) are flat contradictions\*\*", DEVIATIONS)
    if m:
        found["API-DEVIATIONS intro"] = NUMBER_WORDS[m.group(1).lower()]
    m = re.search(r"(\w+) of these ten \(([^)]*)\) are cases where a published", DEVIATIONS)
    if m:
        found["API-DEVIATIONS closing"] = NUMBER_WORDS[m.group(1).lower()]
        found["API-DEVIATIONS closing list"] = len(re.findall(r"D-\d+", m.group(2)))
    m = re.search(r"(\w+) of them places\s+where (?:it|the live API)\s+flatly contradicts", README)
    if m:
        found["README"] = NUMBER_WORDS[m.group(1).lower()]
    return found


def test_the_contradiction_count_agrees_across_every_place_that_states_it():
    """This file's opening classification is load-bearing — "the document is
    wrong" and "the document is silent" call for different amounts of caution —
    and the count is stated in three places across two documents. They drifted
    once (README said six while API-DEVIATIONS said four), which is exactly the
    kind of thing a reader checks and then stops trusting the rest."""
    counts = _contradiction_counts()
    assert len(counts) == 4, (
        f"expected all four count statements to be findable, got {sorted(counts)} — "
        "if the wording changed, update _contradiction_counts() rather than dropping the guard"
    )
    assert len(set(counts.values())) == 1, f"contradiction counts disagree: {counts}"


def test_the_named_contradictions_are_classified_consistently():
    """The intro names them individually and the closing lists them; both must
    name the same deviations, or one of the two is quietly wrong."""
    import re  # noqa: PLC0415

    intro = DEVIATIONS.split("are flat contradictions", 1)[1].split("\n* ", 1)[0]
    intro_ids = set(re.findall(r"D-\d+", intro))
    closing = re.search(r"of these ten \(([^)]*)\)", DEVIATIONS).group(1)
    closing_ids = set(re.findall(r"D-\d+", closing))
    assert intro_ids == closing_ids, (
        f"the intro calls {sorted(intro_ids)} contradictions, the closing calls {sorted(closing_ids)}"
    )


# --- hosting: the single-replica rule ----------------------------------------

def test_hosting_note_states_the_load_bearing_constraints():
    for needle in ("maxReplicas: 1", "minReplicas: 1", "D-7", "AUVIK_USERNAME", "AUVIK_API_KEY",
                   "AUVIK_REGION", "MCP_INBOUND_API_KEY", "/health", "dismissal_audit",
                   "ContainerAppConsoleLogs_CL", "Alerts", "Edit", "500 KB", "summary_only"):
        assert needle in HOSTING, f"HOSTING.md does not mention {needle!r}"


def test_hosting_explains_why_one_replica_rather_than_just_asserting_it():
    flat = _flat(HOSTING)
    assert "process-scoped" in flat or "process state" in flat, (
        "HOSTING.md does not say the dismiss guardrails are process state — the reason for one replica"
    )
    assert "second post" in flat, (
        "HOSTING.md does not spell out the failure: a second replica reintroduces the duplicate write"
    )


def test_readme_points_at_hosting_before_deploying():
    assert "docs/HOSTING.md" in README, "README does not link the hosting note"
    assert "one replica" in _flat(README), "README does not surface the single-replica constraint"


# --- region ------------------------------------------------------------------

def test_docs_state_the_region_default_and_how_to_change_it():
    flat = _flat(README)
    assert "us1" in flat, "README does not say which region this server defaults to"
    assert "auvik_region" in flat, "README does not name the environment variable that overrides it"
    assert "subdomain of your auvik url" in flat, (
        "README does not explain what a region IS — the subdomain of your Auvik URL"
    )


def test_readme_does_not_present_a_region_as_universal():
    """A region default is only right for the tenant it was chosen for; the
    README must tell a reader to check theirs, not imply one region fits all."""
    flat = _flat(README)
    assert "set it explicitly if that is not yours" in flat, (
        "README does not tell the reader to set AUVIK_REGION when the default is not their region"
    )


# --- the tool surface --------------------------------------------------------

from conftest import ALL_TOOLS  # noqa: E402


def test_readme_names_every_registered_tool():
    for tool in ALL_TOOLS:
        assert tool in README, f"README does not mention {tool}"


def test_readme_tool_count_matches_the_registry():
    assert len(ALL_TOOLS) == 15, "ALL_TOOLS changed; the README's headline count must change with it"
    flat = _flat(README)
    assert flat.count("fifteen tools") == 2, (
        f"README states the tool count {flat.count('fifteen tools')} time(s); it is stated twice - "
        "the opening paragraph and the testing section - and changing only one leaves the other stale"
    )


def test_readme_names_the_only_write_and_its_single_call_site():
    flat = _flat(README)
    assert "auvik_dismiss_alert" in flat, "README does not name the write tool"
    assert "client.post(" in flat, "README does not state where the single write call site is"


# --- the docstrings an AGENT reads, not just the docs a human reads ----------
#
# Everything above guards README.md / API-DEVIATIONS.md / HOSTING.md. But the
# text a model actually consumes at call time is the tool DESCRIPTION, and that
# was where the worst instance of this whole class hid: auvik_device_lifecycle's
# docstring listed the OpenAPI file's lowercase enums as if they were the values
# the tool emits. They are not — D-9 — and a model filtering on them matches
# nothing and reports "no device is end-of-support". The docs were right and the
# tool description was wrong, which is the one combination a docs-only gate
# cannot see.


async def _tool_descriptions() -> dict:
    import server  # noqa: PLC0415
    from fastmcp import Client  # noqa: PLC0415

    async with Client(server.mcp) as client:
        return {t.name: t.description or "" for t in await client.list_tools()}


async def test_lifecycle_tool_docstring_warns_about_the_casing_it_actually_emits():
    """D-9's trap, restated to the caller.

    The tool passes Auvik's Title-Case values through verbatim. The description
    may still MENTION the documented lowercase spellings — it has to, to explain
    the trap — but it must warn, in the same breath, which casing actually comes
    out and how to compare it. This checks that the warning is present, not that
    the lowercase spellings are absent."""
    doc = (await _tool_descriptions())["auvik_device_lifecycle"]
    flat = " ".join(doc.split())
    assert "Title-Case" in doc, (
        "auvik_device_lifecycle's description does not warn that Auvik sends Title-Case statuses"
    )
    assert '"Expired"' in doc, (
        "the description does not show, in quotes, the exact casing the tool emits"
    )
    assert "case-sensitively" in flat or "case-insensitively" in flat, (
        "the description does not tell the caller how to compare these values"
    )


async def test_stat_tool_docstrings_do_not_claim_behaviour_they_cannot_produce():
    """`auvik_interface_stats` returns one series per INTERFACE, so the 17-port
    example belongs to it. The device and availability tools return one series
    per device and cannot produce it — that paragraph was copy-pasted into all
    three and is false in two of them."""
    d = await _tool_descriptions()
    assert "17" in d["auvik_interface_stats"], (
        "auvik_interface_stats lost the empty-series example, which is true there"
    )
    for tool in ("auvik_device_stats", "auvik_device_availability"):
        assert "17 of 17" not in d[tool] and "13 of 17" not in d[tool], (
            f"{tool} claims a 17-series response; it returns one series per device"
        )


async def test_every_tool_description_names_its_own_tool_family():
    """Cheap guard against a copy-paste that leaves one tool describing another:
    each stat tool must name the stats it actually accepts."""
    d = await _tool_descriptions()
    for tool, must in (
        ("auvik_interface_stats", "packetDiscard"),
        ("auvik_device_stats", "memoryUtilization"),
        ("auvik_device_availability", "outage"),
        ("auvik_component_health", "powerSupply"),
        ("auvik_remote_access_audit", "remoteBrowser"),
    ):
        assert must in d[tool], f"{tool}'s description never mentions {must!r}"
