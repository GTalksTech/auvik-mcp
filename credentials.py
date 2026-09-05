"""Resolve the Auvik API login.

Order: environment (the hosted path — AUVIK_USERNAME + AUVIK_API_KEY
[+ AUVIK_REGION]), then the OS keyring via `keyring` (service "auvik-api-key":
username = Auvik login email, password = API key) — Windows Credential Manager,
macOS Keychain or libsecret, whichever this platform provides. The key is never
logged; use .fingerprint.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Mapping

KEYRING_SERVICE = "auvik-api-key"
# The region is the subdomain of your Auvik URL: https://<tenant>.<region>.my.auvik.com
# and the API host https://auvikapi.<region>.my.auvik.com. "us1" is the region
# Auvik's own published spec and documentation use throughout, so it is the
# default here — but it is only right if it is YOUR region.
#
# Set AUVIK_REGION whenever it is not. This matters more than it looks: a
# stdio launch (`claude mcp add ... -- python server.py --stdio`) passes no
# environment at all, so a wrong default silently sends every request to the
# wrong host with a perfectly good credential — the failure looks like a bad
# key, not a bad hostname.
DEFAULT_REGION = "us1"

FIX_TEXT = (
    "Auvik credentials not found.\n"
    "Either set env vars AUVIK_USERNAME and AUVIK_API_KEY (and optionally AUVIK_REGION),\n"
    "or store them in your OS keyring (Windows Credential Manager, macOS Keychain, libsecret):\n"
    "    py -3.12 -c \"import keyring,getpass; keyring.set_password('auvik-api-key', "
    "input('auvik login email: '), getpass.getpass('api key: '))\"\n"
    "Region defaults to 'us1'; override with AUVIK_REGION — the subdomain of your Auvik URL,\n"
    "e.g. us2 if you sign in at https://yourcompany.us2.my.auvik.com."
)


class CredentialsMissing(RuntimeError):
    """Raised when neither env nor keyring yields a complete credential."""


@dataclass(frozen=True)
class AuvikCredentials:
    username: str
    api_key: str
    region: str

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.api_key.encode()).hexdigest()[:8]

    def __repr__(self) -> str:  # never include the key
        return f"AuvikCredentials(username={self.username!r}, region={self.region!r}, key=<{self.fingerprint}>)"

    __str__ = __repr__


def load_credentials(env: Mapping[str, str] | None = None, keyring_module=None) -> AuvikCredentials:
    env = os.environ if env is None else env
    region = env.get("AUVIK_REGION") or DEFAULT_REGION

    user, key = env.get("AUVIK_USERNAME"), env.get("AUVIK_API_KEY")
    if user or key:
        missing = [n for n, v in (("AUVIK_USERNAME", user), ("AUVIK_API_KEY", key)) if not v]
        if missing:
            raise CredentialsMissing(f"Env credential incomplete — missing {', '.join(missing)}.\n{FIX_TEXT}")
        return AuvikCredentials(user, key, region)

    if keyring_module is None:
        try:
            import keyring as keyring_module  # type: ignore[no-redef]
        except ImportError as exc:
            raise CredentialsMissing(f"keyring not installed ({exc}).\n{FIX_TEXT}") from exc

    cred = keyring_module.get_credential(KEYRING_SERVICE, None)
    if cred is None or not getattr(cred, "password", None):
        raise CredentialsMissing(FIX_TEXT)
    return AuvikCredentials(cred.username, cred.password, region)
