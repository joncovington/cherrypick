"""The two-phase commit — fingerprint binding, single use, expiry, tamper evidence.

The property that carries the design: the confirmation code is a *fingerprint of the order*, not a
password checked against a stored copy. Every test here is really one question — can a confirmation
ever ratify an order other than the exact one that produced it?
"""

import json
import time

import pytest

from cherrypick.desk import config as cfgmod
from cherrypick.desk import ticket

pytestmark = pytest.mark.unit

ACCOUNT = "5WI62375"


@pytest.fixture(autouse=True)
def _tmp_desk_home(tmp_path, monkeypatch):
    """Point the desk's state dir at a tmp path so tickets never touch the real home."""
    monkeypatch.setattr(cfgmod, "desk_dir", lambda: tmp_path / "desk")
    return tmp_path


def _leg(symbol, action, qty):
    return {"instrument_type": "Equity Option", "symbol": symbol, "action": action, "quantity": qty}


def _order(price=1.10, qty=2, symbol="XYZ   260807C00091000"):
    return {
        "order_type": "Limit",
        "time_in_force": "Day",
        "price": price,
        "price_effect": "debit",
        "legs": [
            _leg("XYZ   260807C00085000", "buy to open", 1),
            _leg(symbol, "sell to open", qty),
            _leg("XYZ   260807C00097000", "buy to open", 1),
        ],
    }


# --------------------------------------------------------------------------- the binding
def test_the_code_is_a_fingerprint_of_the_order():
    """The load-bearing property. Confirming attests to CONTENTS, so any change to the order that
    matters — price, size, strike, account — must produce a different code."""
    base = ticket.code_for(_order(), ACCOUNT)
    assert ticket.code_for(_order(price=1.11), ACCOUNT) != base
    assert ticket.code_for(_order(qty=3), ACCOUNT) != base
    assert ticket.code_for(_order(symbol="XYZ   260807C00092000"), ACCOUNT) != base
    assert ticket.code_for(_order(), "9WI99999") != base  # same structure, different account


def test_the_code_is_stable_for_the_same_order():
    """It must be deterministic, or a legitimate confirmation would fail intermittently."""
    assert ticket.code_for(_order(), ACCOUNT) == ticket.code_for(_order(), ACCOUNT)


def test_leg_order_does_not_change_the_fingerprint():
    """The broker echoes legs back in its own order. If leg ordering changed the fingerprint, a
    perfectly valid confirmation would fail depending on how the caller happened to list them."""
    a = _order()
    b = dict(a, legs=list(reversed(a["legs"])))
    assert ticket.code_for(a, ACCOUNT) == ticket.code_for(b, ACCOUNT)


def test_code_uses_an_unambiguous_alphabet():
    """Codes get read aloud and typed back; 0/O and 1/I/L are where that goes wrong."""
    code = ticket.code_for(_order(), ACCOUNT)
    assert len(code) == 6
    assert not (set(code) & set("01ILOU"))


# --------------------------------------------------------------------------- consumption
def test_a_matching_code_consumes_the_ticket():
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=60)
    got = ticket.consume(rec["ticket_id"], rec["code"])
    assert got["order"] == _order()


def test_a_wrong_code_is_refused():
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=60)
    with pytest.raises(ticket.TicketError, match="does not match"):
        ticket.consume(rec["ticket_id"], "ZZZZZZ")


def test_the_code_is_case_insensitive():
    """Typed back by a human — case is not a security boundary here, just friction."""
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=60)
    assert ticket.consume(rec["ticket_id"], rec["code"].lower())


def test_a_ticket_cannot_be_used_twice():
    """Single use. The claim is an atomic rename BEFORE any check that could pass, so a second
    confirmation of the same ticket cannot also reach the broker."""
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=60)
    ticket.consume(rec["ticket_id"], rec["code"])
    with pytest.raises(ticket.TicketError, match="no pending ticket"):
        ticket.consume(rec["ticket_id"], rec["code"])


def test_a_failed_confirmation_still_spends_the_ticket():
    """A wrong code claims the file too. Otherwise a bad code could be retried indefinitely against
    a live ticket; instead the human re-proposes, which re-runs every gate."""
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=60)
    with pytest.raises(ticket.TicketError):
        ticket.consume(rec["ticket_id"], "ZZZZZZ")
    with pytest.raises(ticket.TicketError, match="no pending ticket"):
        ticket.consume(rec["ticket_id"], rec["code"])


def test_an_expired_ticket_is_refused():
    """An abandoned proposal must not be revivable later from scrollback."""
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=1)
    time.sleep(1.1)
    with pytest.raises(ticket.TicketError, match="expired"):
        ticket.consume(rec["ticket_id"], rec["code"])


def test_an_unknown_ticket_is_refused():
    with pytest.raises(ticket.TicketError, match="no pending ticket"):
        ticket.consume("deadbeef", "ABCDEF")


# --------------------------------------------------------------------------- tamper evidence
def test_editing_the_stored_order_invalidates_the_ticket(tmp_path):
    """The attack this closes: propose a small order, get it past the gates, then rewrite the
    pending file to a larger one and confirm with the code you were already shown. Recomputing the
    fingerprint from the stored order catches it."""
    rec = ticket.create(_order(price=1.10), ACCOUNT, ttl_seconds=60)
    path = cfgmod.desk_dir() / f"pending-{rec['ticket_id']}.json"
    stored = json.loads(path.read_text())
    stored["order"]["legs"][0]["quantity"] = 50  # 50x the size, same ticket + code
    path.write_text(json.dumps(stored))

    with pytest.raises(ticket.TicketError, match="does not match its own order"):
        ticket.consume(rec["ticket_id"], rec["code"])


def test_swapping_in_a_matching_fingerprint_still_fails_the_code_check(tmp_path):
    """Belt and braces: even if an editor recomputed the fingerprint to match their swapped order,
    the code derived from that order no longer matches the one the human was shown."""
    rec = ticket.create(_order(price=1.10), ACCOUNT, ttl_seconds=60)
    path = cfgmod.desk_dir() / f"pending-{rec['ticket_id']}.json"
    stored = json.loads(path.read_text())
    swapped = _order(price=9.99)
    stored["order"] = swapped
    stored["fingerprint"] = ticket.fingerprint(swapped, ACCOUNT)  # consistent, but different order
    path.write_text(json.dumps(stored))

    with pytest.raises(ticket.TicketError, match="confirmation code does not match"):
        ticket.consume(rec["ticket_id"], rec["code"])


# --------------------------------------------------------------------------- housekeeping
def test_purge_removes_only_expired_tickets():
    live = ticket.create(_order(price=1.0), ACCOUNT, ttl_seconds=60)
    dead = ticket.create(_order(price=2.0), ACCOUNT, ttl_seconds=1)
    time.sleep(1.1)
    assert ticket.purge_expired() == 1
    assert ticket.consume(live["ticket_id"], live["code"])
    with pytest.raises(ticket.TicketError):
        ticket.consume(dead["ticket_id"], dead["code"])
