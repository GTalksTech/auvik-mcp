"""Credential resolution: env path, keyring path, missing → clear
fix text; the key never appears in repr/str."""
from __future__ import annotations

import hashlib
import types

import pytest

import credentials as creds


def test_env_path_takes_all_three_values():
    env = {"AUVIK_USERNAME": "svc@example.com", "AUVIK_API_KEY": "k-123", "AUVIK_REGION": "us3"}
    c = creds.load_credentials(env=env, keyring_module=None)
    assert (c.username, c.api_key, c.region) == ("svc@example.com", "k-123", "us3")


def test_env_path_partial_is_an_error_not_a_fallthrough():
    env = {"AUVIK_USERNAME": "svc@example.com"}  # key missing
    with pytest.raises(creds.CredentialsMissing) as ei:
        creds.load_credentials(env=env, keyring_module=None)
    assert "AUVIK_API_KEY" in str(ei.value)


def test_keyring_path_uses_service_and_default_region():
    fake_cred = types.SimpleNamespace(username="svc@example.com", password="k-456")
    fake_keyring = types.SimpleNamespace(get_credential=lambda service, user: fake_cred if service == "auvik-api-key" else None)
    c = creds.load_credentials(env={}, keyring_module=fake_keyring)
    # Asserted against the constant, not against a literal, so this test keeps
    # checking "the keyring path applies the default region" rather than
    # pinning a value it does not own.
    assert (c.username, c.api_key, c.region) == ("svc@example.com", "k-456", creds.DEFAULT_REGION)


def test_keyring_path_region_from_env():
    fake_cred = types.SimpleNamespace(username="svc@example.com", password="k-456")
    fake_keyring = types.SimpleNamespace(get_credential=lambda s, u: fake_cred)
    c = creds.load_credentials(env={"AUVIK_REGION": "us2"}, keyring_module=fake_keyring)
    assert c.region == "us2"


def test_missing_everywhere_gives_fix_text():
    fake_keyring = types.SimpleNamespace(get_credential=lambda s, u: None)
    with pytest.raises(creds.CredentialsMissing) as ei:
        creds.load_credentials(env={}, keyring_module=fake_keyring)
    msg = str(ei.value)
    assert "keyring.set_password('auvik-api-key'" in msg
    assert "AUVIK_USERNAME" in msg


def test_fingerprint_and_repr_never_leak_key():
    c = creds.AuvikCredentials("svc@example.com", "super-secret-key", "us1")
    assert c.fingerprint == hashlib.sha256(b"super-secret-key").hexdigest()[:8]
    assert "super-secret-key" not in repr(c)
    assert "super-secret-key" not in str(c)
    assert c.fingerprint in repr(c)


def test_default_region_is_a_real_region_and_env_always_wins():
    """The default must be a CONCRETE region, never a placeholder, and the
    environment must always be able to override it.

    This is not cosmetic. A stdio launch (`claude mcp add ... -- python
    server.py --stdio`) passes no environment at all, so whatever is baked in
    here is what every request uses. A wrong region with a perfectly good key
    fails in a way that looks like a bad key rather than a bad hostname, which
    is an expensive hour for whoever debugs it. "us1" is Auvik's own documented
    example region; anyone elsewhere sets AUVIK_REGION, and this pins that the
    override actually reaches the credential."""
    assert creds.DEFAULT_REGION and creds.DEFAULT_REGION.strip() == creds.DEFAULT_REGION
    assert "<" not in creds.DEFAULT_REGION and creds.DEFAULT_REGION.isalnum(), (
        f"DEFAULT_REGION {creds.DEFAULT_REGION!r} looks like a placeholder, not a region"
    )
    fake_cred = types.SimpleNamespace(username="svc@example.com", password="k-1")
    fake_keyring = types.SimpleNamespace(get_credential=lambda s, u: fake_cred)
    assert creds.load_credentials(env={}, keyring_module=fake_keyring).region == creds.DEFAULT_REGION
    other = "us9" if creds.DEFAULT_REGION != "us9" else "us8"
    assert creds.load_credentials(env={"AUVIK_REGION": other}, keyring_module=fake_keyring).region == other
    assert creds.load_credentials(
        env={"AUVIK_USERNAME": "u@example.com", "AUVIK_API_KEY": "k", "AUVIK_REGION": other}
    ).region == other


# --- the startup log line: the one place the code deliberately touches the key -

def test_startup_log_emits_the_fingerprint_and_never_the_key(monkeypatch, capsys):
    """`server.py` logs one startup line carrying a key FINGERPRINT. README.md
    makes "the key is never logged" a headline security claim, and until this
    test existed nothing checked it: replacing `creds.fingerprint` with
    `creds.api_key` on that line left the entire suite green while the server
    printed the raw credential to its log stream on every start.

    This covers the startup line specifically. The structural rule that no
    OTHER site may even read the raw key lives in
    tests/test_credential_never_written.py - written after review found two more
    printing sites that this test, by construction, could never have seen."""
    import json  # noqa: PLC0415

    import server  # noqa: PLC0415

    secret = "SUPER-SECRET-AUVIK-KEY-do-not-log"
    fake_creds = creds.AuvikCredentials("svc@example.com", secret, "us1")

    class _Client:
        def __init__(self, c):
            self.creds = c

        def tenant_map(self):
            return {"T1": "acme-hq"}

    server.reset_state_for_tests(None, [])
    monkeypatch.setattr(server, "load_credentials", lambda: fake_creds)
    monkeypatch.setattr(server, "AuvikClient", _Client)
    try:
        server._ensure_started()
        line = capsys.readouterr().err.strip().splitlines()[-1]
    finally:
        server.reset_state_for_tests(None, [])

    assert secret not in line, "the raw API key was written to the startup log line"
    record = json.loads(line)
    assert record["event"] == "startup"
    assert record["key_fingerprint"] == fake_creds.fingerprint
    assert record["key_fingerprint"] != secret
    assert len(record["key_fingerprint"]) == 8, "the fingerprint must stay an 8-char digest"
    assert secret not in json.dumps(record), "the key reached the startup record by some other field"
