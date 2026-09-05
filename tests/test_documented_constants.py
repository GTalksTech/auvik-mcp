"""Every number this repo states in prose must be the number the code uses.

Two separate failure modes, both found by review rather than by the suite:

* a **constant** changes and no test notices, because the only test that
  exercises it passes an explicit override (so the default is never on a tested
  path). `MAX_BACKUPS_PER_DEVICE`, three copies of `MAX_LIMIT`, the hour clamp
  and `MAX_DEVICES_PER_NETWORK` were all in that state at once.
* a **document** changes and no test notices, because the doc cross-check
  matched a phrase that markdown had wrapped across a newline — so the check
  quietly stopped entering its own loop body.

The second is why every document is flattened here before matching: markdown
wraps at whatever column it likes, and a gate that a reflow can silence is not
a gate. See `_flat`.

These are response-size and blast-radius controls. A silent change to one is a
silent change to whether a tool can breach the 500 KB cap, or to how many rows
a caller can ask for.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools import alerts, audits, configs, devices, interfaces, networks, stats
from tools import common

ROOT = Path(__file__).resolve().parent.parent
DOCS = {name: (ROOT / name).read_text(encoding="utf-8")
        for name in ("README.md", "docs/HOSTING.md", "server.py")}


def _flat(text: str) -> str:
    """Collapse whitespace and markdown emphasis, so a line wrap cannot hide a
    phrase from a substring match. This is the bug that made the first version
    of the per-network check inert."""
    return " ".join(text.replace("**", "").replace("`", "").split())


FLAT = {name: _flat(text) for name, text in DOCS.items()}


# --- the constants themselves ------------------------------------------------

@pytest.mark.parametrize("where, value, expected", [
    ("tools/alerts.py::MAX_LIMIT", alerts.MAX_LIMIT, 200),
    ("tools/devices.py::MAX_LIMIT", devices.MAX_LIMIT, 200),
    ("tools/interfaces.py::MAX_LIMIT", interfaces.MAX_LIMIT, 200),
    ("tools/stats.py::MAX_LIMIT", stats.MAX_LIMIT, 200),
    ("tools/networks.py::MAX_DEVICES_PER_NETWORK", networks.MAX_DEVICES_PER_NETWORK, 50),
    ("tools/configs.py::MAX_BACKUPS_PER_DEVICE", configs.MAX_BACKUPS_PER_DEVICE, 20),
    ("tools/audits.py::MAX_DATA_CHARS", audits.MAX_DATA_CHARS, 300),
    ("tools/alerts.py::FLAP_THRESHOLD", alerts.FLAP_THRESHOLD, 3),
    ("tools/stats.py::CLAMP_HOURS[minute]", stats.CLAMP_HOURS["minute"], 6),
    ("tools/stats.py::CLAMP_HOURS[hour]", stats.CLAMP_HOURS["hour"], 168),
    ("tools/stats.py::CLAMP_HOURS[day]", stats.CLAMP_HOURS["day"], 365),
    ("tools/devices.py::DEVICE_PAGE_SIZE", devices.DEVICE_PAGE_SIZE, 1000),
    ("tools/devices.py::DEVICE_CACHE_TTL", devices.DEVICE_CACHE_TTL, 1800),
])
def test_constant_matches_its_documented_value(where, value, expected):
    assert value == expected, f"{where} is {value}, but the documentation says {expected}"


def test_the_shipped_rate_budget_defaults_are_the_ones_that_apply():
    """Every rate-budget test builds its own RateBudget, so the SHIPPED defaults
    were never exercised - 2000 could become 200000 silently. Auvik publishes a
    per-IP limit and this budget is meant to sit under it."""
    from auvik_client import RateBudget  # noqa: PLC0415

    b = RateBudget()
    assert (b.max, b.window) == (2000, 300)
    assert b.remaining() == 2000
    b.acquire()
    assert b.remaining() == 1999


def test_check_limit_default_ceiling_is_200():
    """tools/common.py::check_limit is the shared ceiling for the tools that use
    it; its default is the "max 200" both published documents promise."""
    assert common.check_limit(10_000) == 200
    assert common.check_limit(5) == 5
    with pytest.raises(ValueError):
        common.check_limit(0)


# --- and the documents that state them ---------------------------------------

@pytest.mark.parametrize("doc, phrase", [
    ("README.md", "limit (max 200)"),
    ("docs/HOSTING.md", "limit (max 200)"),
    ("docs/HOSTING.md", "caps its per-network device list at 50"),
    ("docs/HOSTING.md", "caps each device's backup list at 20"),
    ("docs/HOSTING.md", "caps its free-form data field at 300 characters"),
    ("docs/HOSTING.md", "500 KB"),
    ("README.md", "500 KB"),
    ("server.py", "add up to 50 {device_id, name} per network"),
    ("server.py", "the newest 20 backup timestamps"),
    ("server.py", "flapping flag (3+ occurrences in the window)"),
])
def test_document_states_the_constant_it_is_supposed_to(doc, phrase):
    """Flattened match: the phrase must survive markdown rewrapping. If you
    reword a document, reword the expectation with it - do not delete it.

    Matched with a trailing non-digit boundary, because a phrase that ENDS in
    its number ("... list at 50") is also a substring of a bigger one
    ("... list at 500"). Shrinking a cap was caught; growing one was not."""
    assert re.search(re.escape(phrase) + r"(?!\d)", FLAT[doc]), (
        f"{doc} no longer states: {phrase!r}"
    )


def test_the_stat_clamps_are_documented_in_hours_not_days():
    """The clamp is in HOURS for every interval, including "day" — 365 there
    means about 15 days, not a year. Three tool docstrings say so, and that
    wording is the only thing standing between a caller and a very wrong
    assumption about how much history they just asked for."""
    flat = FLAT["server.py"]
    assert flat.count("365h at day") == 3, (
        "all three statistics tools must state the day clamp in HOURS"
    )
    assert "tops out around 15 days" in flat


# --- the caps actually apply on the default path -----------------------------

def test_the_backup_cap_applies_without_being_passed_explicitly():
    """The only other test of this cap passes max_backups_per_device=2, so the
    DEFAULT was never on a tested path — it could be changed silently."""
    from datetime import datetime, timezone  # noqa: PLC0415

    from conftest import FakeClient  # noqa: PLC0415

    n = configs.MAX_BACKUPS_PER_DEVICE + 15
    body = {"data": [
        {"type": "configuration", "id": f"CFG-{i}",
         "attributes": {"backupTime": f"2026-08-{(i % 28) + 1:02d}T03:00:00.000Z", "isRunning": False},
         "relationships": {"tenant": {"data": {"type": "tenant", "id": "T1",
                                               "attributes": {"domainPrefix": "acme-hq"}}},
                           "device": {"data": {"type": "device", "id": "DEV-1",
                                               "attributes": {"deviceName": "core-sw-01"}}}}}
        for i in range(n)], "links": {}}
    fake = FakeClient({"/v1/inventory/configuration": body})
    out = configs.config_changes(fake, ["T1"], lambda _d: "", hours=0, device_id="DEV-1",
                                 tenant_sites={"T1": "acme-hq"},
                                 now=datetime(2026, 8, 29, 12, tzinfo=timezone.utc))
    dev = out["devices_changed"][0]
    assert dev["backup_count"] == n, "backup_count must report the true total"
    assert len(dev["backups"]) == configs.MAX_BACKUPS_PER_DEVICE, "the default cap was not applied"
    assert dev["backups_truncated"] is True, "a capped list must say it was capped"


def test_the_audit_data_cap_applies_on_the_default_path():
    long_item = {"id": "AUD-X", "attributes": {"data": "x" * 5000}, "relationships": {}}
    assert len(audits.project_audit(long_item)["data"]) == audits.MAX_DATA_CHARS
