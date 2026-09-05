"""Shared validators and the one site-scoping rule.

`site` everywhere in this server means a case-insensitive substring of an Auvik
tenant's domainPrefix - so the Boston office might be `acme-bos`, and the place
name on its own will not match. The site list for ANY tenant-scoped endpoint
comes from /v1/tenants, never from the device estate: a real tenant can hold
alerts and zero devices, and a device-derived list would then call a real site
unknown. `sites` is omitted, not reported as [], when the caller supplied no
tenant map - an empty list would claim "computed, and there are none" when the
truth is "not given".
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SiteScope:
    tenant_ids: list[str]
    sites: list[str]
    known: bool
    unmatched: bool

    def site_key(self) -> dict:
        return {"sites": self.sites} if self.known else {}

    def unmatched_detail(self, site: str) -> str:
        if self.sites:
            return f"no site matches {site!r}; sites in this Auvik: {', '.join(self.sites)}"
        return f"no site matches {site!r}; no tenant reports a site"


def scope_sites(tenant_ids: list[str], tenant_sites: dict[str, str] | None, site: str) -> SiteScope:
    known = tenant_sites is not None
    tenant_sites = tenant_sites or {}
    sites = sorted({p for p in tenant_sites.values() if p})
    if not site:
        return SiteScope(list(tenant_ids), sites, known, unmatched=False)
    s = site.lower()
    scoped = [tid for tid, prefix in tenant_sites.items() if prefix and s in prefix.lower()]
    return SiteScope(scoped, sites, known, unmatched=not scoped)


def check_enum(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value and value not in allowed:
        raise ValueError(f"invalid {name} {value!r}; valid: {', '.join(allowed)}")


def check_limit(limit: int, max_limit: int = 200) -> int:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return min(limit, max_limit)
