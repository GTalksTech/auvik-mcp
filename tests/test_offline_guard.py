"""The offline suite is offline, by construction.

Background: the suite was green for a whole session and then failed about 1 run
in 6, on test_unavailable::test_empty_upstream_is_ok_zero - `auvik_list_alerts`
returned real live alerts where the test expected a fixture zero. A stray
background warm thread, started by a transport test's call to server.main() and
never joined, had loaded a REAL credential from the OS keyring and swapped a
live AuvikClient into the next test's state. The suite was nondeterministic AND
had a path to the live API.

These tests pin the guard in tests/conftest.py::_offline_guard and the specific
sequence that leaked. Running this file on a machine that HAS a credential in
its keyring is the meaningful case; on a machine without one the guard is
redundant and these still pass.
"""
from __future__ import annotations

import pytest
import requests

from conftest import FakeClient, default_routes
from credentials import CredentialsMissing


def test_server_cannot_load_a_real_credential_under_the_suite():
    import server

    with pytest.raises(CredentialsMissing, match="offline suite"):
        server.load_credentials()


def test_nothing_can_open_a_socket_under_the_suite():
    with pytest.raises(AssertionError, match="reached the network"):
        requests.Session().request("GET", "https://auvikapi.us1.my.auvik.com/v1/tenants")


def test_warm_start_on_a_cold_process_stays_cold_and_offline():
    """The exact thing the stray thread did. Without the guard, on any machine
    that has a credential stored, this call loads it and leaves a live client in
    _state - which the next test then unknowingly uses."""
    import server

    server.reset_state_for_tests(None, [])
    server.warm_start()
    assert server._state.client is None, "warm_start built a client with no injected fake - it loaded a real credential"
    assert server._state.error == "" and server._state.last_attempt == 0.0
    server.reset_state_for_tests(None, [])


def test_warm_start_never_replaces_an_injected_fake_client():
    """The leak's second half: a warm racing a test must not swap the test's
    FakeClient for anything else."""
    import server

    fake = FakeClient(default_routes())
    server.reset_state_for_tests(fake, ["T1"])
    server.warm_start()
    assert server._state.client is fake
    assert [c[1] for c in fake.calls if c[1] == "/v1/inventory/device/info"], "warm did not prime the fake estate"
    server.reset_state_for_tests(None, [])


def test_main_stdio_does_not_leave_a_thread_that_can_reach_the_network(monkeypatch):
    """main() with the real start_background_warm: the thread it starts must
    finish having touched nothing real. Joined here so the assertion is on a
    finished thread, not a hope."""
    import threading

    import server

    started: list[threading.Thread] = []
    real_start = server.start_background_warm

    def _capture():
        t = real_start()
        started.append(t)
        return t

    monkeypatch.setattr(server, "start_background_warm", _capture)
    monkeypatch.setattr(server.mcp, "run", lambda transport: None)
    server.reset_state_for_tests(None, [])
    server.main(["--stdio"])
    assert len(started) == 1
    started[0].join(timeout=10)
    assert not started[0].is_alive(), "warm thread still running after 10 s - it is doing real I/O"
    assert server._state.client is None, "the warm thread built a real client inside the offline suite"
    server.reset_state_for_tests(None, [])
    import envelope as env
    import sys
    env.set_log_stream(sys.stdout)
