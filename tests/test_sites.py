"""One site-scoping rule for every tenant-scoped tool.

Case-insensitive substring on domainPrefix; the site list comes from
/v1/tenants, never the device estate; `sites` is OMITTED (not []) when no tenant
map was supplied; and an unmatched site is a real zero on which the caller must
make no request at all."""
from __future__ import annotations

import pytest

from tools import common

MAP = {"T0": "acme-root", "T1": "acme-hq", "T2": "acme-bos", "T3": "acme-bos-dr", "T4": ""}
IDS = list(MAP)


def test_no_site_scopes_to_every_tenant_and_lists_sites():
    sc = common.scope_sites(IDS, MAP, "")
    assert sc.tenant_ids == IDS and sc.known and not sc.unmatched
    assert sc.sites == ["acme-bos", "acme-bos-dr", "acme-hq", "acme-root"]  # sorted, blanks dropped
    assert sc.site_key() == {"sites": sc.sites}


def test_substring_match_is_case_insensitive_and_includes_superstrings():
    sc = common.scope_sites(IDS, MAP, "BOS")
    assert sc.tenant_ids == ["T2", "T3"] and not sc.unmatched


def test_unmatched_site_is_flagged_with_a_detail_naming_the_sites():
    sc = common.scope_sites(IDS, MAP, "boston")
    assert sc.unmatched and sc.tenant_ids == []
    assert sc.unmatched_detail("boston") == "no site matches 'boston'; sites in this Auvik: acme-bos, acme-bos-dr, acme-hq, acme-root"


def test_unknown_map_omits_sites_and_cannot_match():
    sc = common.scope_sites(["T1"], None, "")
    assert sc.tenant_ids == ["T1"] and not sc.known and sc.site_key() == {}
    sc2 = common.scope_sites(["T1"], None, "acme")
    assert sc2.unmatched and sc2.unmatched_detail("acme") == "no site matches 'acme'; no tenant reports a site"


def test_check_enum_and_limit():
    common.check_enum("x", "", ("a", "b"))
    common.check_enum("x", "a", ("a", "b"))
    with pytest.raises(ValueError, match="valid: a, b"):
        common.check_enum("x", "zzz", ("a", "b"))
    assert common.check_limit(500) == 200 and common.check_limit(5) == 5
    with pytest.raises(ValueError):
        common.check_limit(0)
