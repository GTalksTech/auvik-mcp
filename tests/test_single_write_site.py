"""Exactly one call site for client.post( outside auvik_client.py,
and it is tools/alerts.py. No passthrough tool may ever exist."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# scripts/ is in scope too: those files are run by hand against production, by
# someone who has not read them first. A write hiding in a probe script is the
# same defect as a write hiding in a tool.
SCAN = [ROOT / "server.py", ROOT / "envelope.py", ROOT / "credentials.py",
        *sorted((ROOT / "tools").glob("*.py")), *sorted((ROOT / "scripts").glob("*.py"))]


def test_client_post_has_exactly_one_call_site():
    hits = []
    for f in SCAN:
        if not f.exists():
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.post\(", line) and not line.strip().startswith("#"):
                hits.append((f.name, n))
    assert len(hits) == 1, f"expected exactly one .post( call site, found {hits}"
    assert hits[0][0] == "alerts.py"


def test_no_raw_request_tool():
    text = "\n".join(f.read_text(encoding="utf-8") for f in SCAN if f.exists())
    # No passthrough may ever appear: not a raw_request tool, and not a tool
    # that reaches under the client to AuvikClient._request (which does not
    # enforce the single-call-site rule the way .post() does).
    assert "raw_request" not in text, "a raw_request passthrough appeared"
    assert "._request(" not in text, "a caller reached under the client to _request()"
