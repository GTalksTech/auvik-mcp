"""The API key must never reach a log line, a print, a file or a response.

This is a source-level gate, deliberately, because the behavioural version of it
kept missing sites. A test that captures one log stream proves one call site is
clean; it says nothing about the next one somebody adds. Review found exactly
that: a gate was written for `server.py`'s startup line, its docstring claimed
that line was "the only place in the codebase that reaches for the credential",
and two other sites were printing credential-derived values at the time —
`scripts/live_read_all.py` and `scripts/live_smoke.py`, both of which are run BY
HAND against production and whose output is meant to be pasted into a record.

So the rule enforced here is structural and has no exceptions to remember:

    `.api_key` may be read in exactly two modules — the one that defines it and
    the one that puts it in an Authorization header. Anywhere else, in any
    shipped file, is a finding.

Everything else that wants to identify a credential uses `.fingerprint`, an
8-character SHA-256 digest, or the `caller_key_label`, which is a label and not
a secret.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# The two modules allowed to touch the raw key, and why.
ALLOWED = {
    "credentials.py": "defines the credential and derives .fingerprint from it",
    "auvik_client.py": "passes it to requests' HTTP Basic auth, and nowhere else",
}

SHIPPED = [
    ROOT / "server.py",
    ROOT / "envelope.py",
    ROOT / "auvik_client.py",
    ROOT / "credentials.py",
    *sorted((ROOT / "tools").glob("*.py")),
    # scripts/ is in scope precisely because these run against production by
    # hand and their output gets pasted into documents.
    *sorted((ROOT / "scripts").glob("*.py")),
]


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def test_only_two_modules_read_the_raw_api_key():
    offenders = []
    for path in SHIPPED:
        name = path.name if path.parent == ROOT else _rel(path)
        if path.name in ALLOWED and path.parent == ROOT:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if re.search(r"\.api_key\b", line):
                offenders.append(f"{name}:{n}: {line.strip()}")
    assert not offenders, (
        "these files read the raw API key; only "
        + ", ".join(sorted(ALLOWED))
        + " may:\n  " + "\n  ".join(offenders)
    )


def test_the_allowed_modules_still_do_not_print_it():
    """`credentials.py` and `auvik_client.py` may READ the key. Neither may write
    it anywhere: no print, no logging call, no f-string interpolation into one."""
    offenders = []
    for name in ALLOWED:
        path = ROOT / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            called = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if called not in {"print", "debug", "info", "warning", "error", "exception", "write"}:
                continue
            for arg in ast.walk(node):
                if isinstance(arg, ast.Attribute) and arg.attr == "api_key":
                    offenders.append(f"{name}:{node.lineno}: {called}(... .api_key ...)")
    assert not offenders, "the raw API key is passed to an output call:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("path", SHIPPED, ids=_rel)
def test_no_shipped_file_interpolates_a_bare_credentials_object(path):
    """`AuvikCredentials.__repr__` is overridden to redact, so `{creds!r}` is
    safe — but only because of that override. This pins the pairing: if the
    override is ever removed, `test_fingerprint_and_repr_never_leak_key` fails;
    while it exists, interpolating the object is allowed and interpolating the
    key is not."""
    from credentials import AuvikCredentials  # noqa: PLC0415

    c = AuvikCredentials("svc@example.com", "SENTINEL-KEY-VALUE", "us1")
    assert "SENTINEL-KEY-VALUE" not in repr(c)
    assert "SENTINEL-KEY-VALUE" not in str(c)
    assert "SENTINEL-KEY-VALUE" not in f"{c}" and "SENTINEL-KEY-VALUE" not in f"{c!r}"
    assert c.fingerprint in repr(c), "the redacted repr should still identify WHICH key"
