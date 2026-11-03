"""Unit tests for orchestrator.util.

first_json guards the exact class of bug seen in Stage 0: streamer.py --status prints a JSON status
line followed by extra log output, which a plain json.loads rejects with "Extra data". mask_account
guards the suite-wide rule that account numbers are never emitted in full.
"""

import os
import subprocess
import sys

import pytest

from cherrypick.orchestrator.util import (
    first_json,
    listen_owner_from_nettcp,
    mask_account,
    never_throttle,
)

pytestmark = pytest.mark.unit


def test_clean_json_object():
    assert first_json('{"running": true, "pid": 123}') == {"running": True, "pid": 123}


def test_json_with_trailing_extra_data():
    # The streamer-status shape: valid JSON on line 1, extra output after.
    out = '{"running": true, "pid": 999}\nINFO: streamer heartbeat ok\n'
    assert first_json(out) == {"running": True, "pid": 999}


def test_json_not_on_first_line():
    out = 'starting up...\n{"ok": true}\n'
    assert first_json(out) == {"ok": True}


def test_empty_and_none():
    assert first_json("") == {}
    assert first_json(None) == {}


def test_no_json_present():
    assert first_json("plain text, no json here") == {}


def test_top_level_non_dict_is_ignored():
    # A JSON array is valid JSON but not a status object.
    assert first_json("[1, 2, 3]") == {}


def test_mask_account_is_cores_rule():
    """One rule for the suite (cherrypick.core.redact); a local copy is how the two drifted on
    stripping before they were folded."""
    from cherrypick.core import redact

    assert mask_account is redact.mask_account


def test_mask_account_keeps_last_four():
    assert mask_account("5WT12345") == "****2345"
    assert mask_account(12345678) == "****5678"


def test_mask_account_short_or_empty_reveals_nothing():
    assert mask_account("12") == "****"
    assert mask_account("") == "****"
    assert mask_account(None) == "****"


def test_mask_account_never_contains_full_value():
    full = "5WU987654321"
    masked = mask_account(full)
    assert masked == "****4321" and full not in masked


@pytest.mark.skipif(os.name != "nt", reason="power throttling is a Windows process setting")
def test_never_throttle_marks_a_child_as_controlled_and_not_throttled():
    # Read back the way Windows reports it: the execution-speed control bit set, its state bit clear.
    import ctypes
    import ctypes.wintypes as wt

    class State(ctypes.Structure):
        _fields_ = [("Version", wt.ULONG), ("ControlMask", wt.ULONG), ("StateMask", wt.ULONG)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]

    def state(handle):
        s = State(1, 0, 0)
        assert k32.GetProcessInformation(handle, 4, ctypes.byref(s), ctypes.sizeof(s))
        return s.ControlMask, s.StateMask

    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"]) as child:
        try:
            handle = int(child._handle)
            assert state(handle) == (0, 0)
            assert never_throttle(handle) is True
            assert state(handle) == (1, 0)
        finally:
            child.kill()


def test_never_throttle_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert never_throttle(1234) is False


# `state remote_addr remote_port pid`, as port_owner_pid's Get-NetTCPConnection fallback prints them.
# On the suite's host a listener reports State 0, not Listen (2) -- the 2026-11-02 Dolt rows below.
def test_a_listener_is_found_by_its_unbound_remote_end_when_its_state_reads_0():
    assert listen_owner_from_nettcp("0 0.0.0.0 0 1848\n") == 1848
    assert listen_owner_from_nettcp("0 :: 0 1848\n") == 1848


def test_a_listener_is_found_by_its_listen_state():
    assert listen_owner_from_nettcp("2 0.0.0.0 0 77\n") == 77


def test_connections_to_the_port_are_not_its_owner():
    # Clients connected to the server, and the server's own end of those connections.
    text = "5 127.0.0.1 51234 1848\n5 127.0.0.1 3306 9999\n0 0.0.0.0 0 1848\n"
    assert listen_owner_from_nettcp(text) == 1848
    assert listen_owner_from_nettcp("5 127.0.0.1 51234 1848\n") is None


def test_any_doubt_is_none_never_a_guess():
    assert listen_owner_from_nettcp("") is None
    assert listen_owner_from_nettcp("0 0.0.0.0 0 11\n0 :: 0 22\n") is None  # two owners
    assert listen_owner_from_nettcp("0 0.0.0.0 0 0\n") is None  # the idle pseudo-process
    assert listen_owner_from_nettcp("garbage line\n") is None
