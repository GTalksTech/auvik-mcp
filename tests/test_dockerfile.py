"""The container image must carry every local module the server imports and none
of the dev tooling, and must start the HTTP entrypoint rather than --stdio.
Parsed out of the Dockerfile, so a new tools/ module or a new top-level import
cannot ship as an ImportError at container start."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
LOCAL_TOP_LEVEL = {p.stem for p in ROOT.glob("*.py")} | {"tools"}


def _dockerfile_env_names() -> set[str]:
    """Every ENV the image bakes in, so HOSTING.md cannot document fewer."""
    names = set()
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("ENV "):
            names.add(line.split()[1].split("=")[0])
    return names


def _copied() -> set[str]:
    copied: set[str] = set()
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("COPY "):
            parts = line.split()[1:-1]  # sources; last token is the destination
            copied.update(p.rstrip("/") for p in parts)
    return copied


def _local_imports(py: Path) -> set[str]:
    names = set()
    for m in re.finditer(r"^(?:from|import)\s+([A-Za-z_][\w]*)", py.read_text(encoding="utf-8"), re.M):
        if m.group(1) in LOCAL_TOP_LEVEL:
            names.add(m.group(1))
    return names


def test_dockerfile_exists_and_copies_every_local_import():
    assert DOCKERFILE.exists(), "Dockerfile missing at the repo root"
    copied = _copied()
    sources = [ROOT / "server.py", ROOT / "envelope.py", ROOT / "auvik_client.py", ROOT / "credentials.py",
               *sorted((ROOT / "tools").glob("*.py"))]
    for src in sources:
        for name in _local_imports(src):
            target = "tools" if name == "tools" else f"{name}.py"
            assert target in copied, f"{src.name} imports {name} but the Dockerfile does not COPY {target}"
    assert "server.py" in copied and "tools" in copied


def test_dockerfile_installs_pinned_prod_deps_only_and_runs_http_mode():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "requirements.txt" in text and "constraints.txt" in text and "-c constraints.txt" in text
    assert "requirements-dev.txt" not in text
    assert 'CMD ["python", "server.py"]' in text
    assert "--stdio" not in text
    assert "EXPOSE 8080" in text
    prod = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "pytest" not in prod, "requirements.txt must hold only runtime deps; the image installs it"


def test_hosting_note_covers_what_the_dockerfile_leaves_to_the_operator():
    """The Dockerfile sets PORT, AUVIK_REGION and the entrypoint; everything else
    an operator must decide lives in HOSTING.md. The full needle list is owned by
    tests/test_doc_accuracy.py — reusing it here keeps the two from drifting, as
    they had (this copy was silently missing "minReplicas: 1")."""
    from test_doc_accuracy import test_hosting_note_states_the_load_bearing_constraints  # noqa: PLC0415

    test_hosting_note_states_the_load_bearing_constraints()

    note = (ROOT / "docs" / "HOSTING.md").read_text(encoding="utf-8")
    for env in _dockerfile_env_names():
        assert env in note, (
            f"the Dockerfile sets {env} but HOSTING.md never explains it"
        )
