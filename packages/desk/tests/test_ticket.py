"""The two-phase commit — fingerprint binding, single use, expiry, tamper evidence.

Every test here is really one question — can a confirmation ever ratify an order other than the
exact one that produced it, on an account other than the one it was proposed on, or after its time?
"""

import json
import os
import time

import pytest

from cherrypick.desk import config as cfgmod
from cherrypick.desk import ticket

pytestmark = pytest.mark.unit

ACCOUNT = "5WT01234"
TTL = 120


def _leg(symbol, action, qty):
    return {"instrument_type": "Equity Option", "symbol": symbol, "action": action, "quantity": qty}


def _order(price=1.10, qty=2, symbol="XYZ   260807C00091000", **extra):
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
        **extra,
    }


def _path(rec):
    return cfgmod.desk_dir() / f"pending-{rec['ticket_id']}.json"


def _consume(rec, code=None, account=ACCOUNT, ttl=TTL):
    return ticket.consume(
        rec["ticket_id"], rec["code"] if code is None else code, account, max_ttl_seconds=ttl
    )


# --------------------------------------------------------------------------- the binding
def test_the_code_is_a_fingerprint_of_the_order():
    base = ticket.code_for(_order(), ACCOUNT)
    assert ticket.code_for(_order(price=1.11), ACCOUNT) != base
    assert ticket.code_for(_order(qty=3), ACCOUNT) != base
    assert ticket.code_for(_order(symbol="XYZ   260807C00092000"), ACCOUNT) != base
    assert ticket.code_for(_order(), "9WI99999") != base


def test_price_is_fingerprinted_exactly_not_rounded():
    """The old canonical form rounded to four decimals, so 1.1 and 1.10001 shared a code while the
    broker would have received different prices."""
    assert ticket.fingerprint(_order(price=1.1), ACCOUNT) != ticket.fingerprint(
        _order(price=1.10001), ACCOUNT
    )
    assert ticket.fingerprint(_order(price=1.1), ACCOUNT) == ticket.fingerprint(_order(price=1.10), ACCOUNT)


@pytest.mark.parametrize(
    "extra",
    [{"stop_trigger": 1.0}, {"external_identifier": "x"}, {"something_new": 1}, {"order_type": "Market"}],
)
def test_every_order_field_is_in_the_fingerprint(extra):
    assert ticket.fingerprint({**_order(), **extra}, ACCOUNT) != ticket.fingerprint(_order(), ACCOUNT)


def test_extra_leg_fields_are_in_the_fingerprint():
    o = _order()
    o2 = json.loads(json.dumps(o))
    o2["legs"][0]["ratio"] = 2
    assert ticket.fingerprint(o, ACCOUNT) != ticket.fingerprint(o2, ACCOUNT)


def test_the_code_is_stable_for_the_same_order():
    assert ticket.code_for(_order(), ACCOUNT) == ticket.code_for(_order(), ACCOUNT)


def test_leg_order_does_not_change_the_fingerprint():
    a = _order()
    b = dict(a, legs=list(reversed(a["legs"])))
    assert ticket.code_for(a, ACCOUNT) == ticket.code_for(b, ACCOUNT)


def test_code_uses_an_unambiguous_alphabet():
    code = ticket.code_for(_order(), ACCOUNT)
    assert len(code) == 6
    assert not (set(code) & set("01ILOU"))


# --------------------------------------------------------------------------- the HMAC key
def test_the_key_is_created_once_in_the_keyring(fake_keyring):
    ticket.code_for(_order(), ACCOUNT)
    keys = [k for (_, k) in fake_keyring.store]
    assert keys == ["ticket_hmac_secret"]
    first = dict(fake_keyring.store)
    ticket.code_for(_order(), ACCOUNT)
    assert dict(fake_keyring.store) == first


def test_a_different_key_gives_a_different_code(fake_keyring):
    """Without the key, nobody can compute a matching code for an order they wrote to disk."""
    a = ticket.code_for(_order(), ACCOUNT)
    fake_keyring.store.clear()
    assert ticket.code_for(_order(), ACCOUNT) != a


def test_a_broken_keyring_refuses_to_mint_or_check(fake_keyring):
    fake_keyring.fail = True
    with pytest.raises(ticket.TicketError, match="ticket key unavailable"):
        ticket.create(_order(), ACCOUNT, ttl_seconds=TTL)


def test_a_malformed_key_refuses(fake_keyring):
    fake_keyring.store[(cfgmod.KEYRING_SERVICE, "ticket_hmac_secret")] = "zz"
    with pytest.raises(ticket.TicketError, match="malformed"):
        ticket.code_for(_order(), ACCOUNT)


# --------------------------------------------------------------------------- what is on disk
def test_the_ticket_file_holds_only_the_masked_account_and_no_code():
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=TTL)
    text = _path(rec).read_text()
    assert ACCOUNT not in text
    assert '"account": "****1234"' in text
    assert rec["code"] not in text


# --------------------------------------------------------------------------- consumption
def test_a_matching_code_consumes_the_ticket():
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=TTL)
    assert _consume(rec)["order"] == _order()


def test_a_wrong_code_is_refused():
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=TTL)
    with pytest.raises(ticket.TicketError, match="does not match"):
        _consume(rec, code="ZZZZZZ")


def test_the_code_is_case_insensitive():
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=TTL)
    assert _consume(rec, code=rec["code"].lower())


def test_a_different_full_account_with_the_same_last_four_is_refused():
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=TTL)
    with pytest.raises(ticket.TicketError, match="does not match its own order"):
        _consume(rec, account="9ZZ91234")


def test_a_different_account_is_refused():
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=TTL)
    with pytest.raises(ticket.TicketError, match="not the one this ticket was proposed on"):
        _consume(rec, account="9WI99999")


def test_a_ticket_cannot_be_used_twice():
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=TTL)
    _consume(rec)
    with pytest.raises(ticket.TicketError, match="no pending ticket"):
        _consume(rec)


def test_a_failed_confirmation_still_spends_the_ticket():
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=TTL)
    with pytest.raises(ticket.TicketError):
        _consume(rec, code="ZZZZZZ")
    with pytest.raises(ticket.TicketError, match="no pending ticket"):
        _consume(rec)


def test_an_expired_ticket_is_refused():
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=TTL)
    record = ticket.claim(rec["ticket_id"])
    with pytest.raises(ticket.TicketError, match="expired"):
        ticket.check_fresh(record, max_ttl_seconds=TTL, now=time.time() + TTL + 1)


def test_an_unknown_ticket_is_refused():
    with pytest.raises(ticket.TicketError, match="no pending ticket"):
        ticket.consume("deadbeef", "ABCDEF", ACCOUNT, max_ttl_seconds=TTL)


@pytest.mark.parametrize("bad", ["../../etc", "DEADBEEF", "dead", "", "deadbeef/../x"])
def test_ticket_ids_are_validated_before_touching_a_path(bad):
    with pytest.raises(ticket.TicketError, match="not a ticket id"):
        ticket.claim(bad)


# --------------------------------------------------------------------------- tamper evidence
def _rewrite(rec, mutate):
    path = _path(rec)
    stored = json.loads(path.read_text())
    mutate(stored)
    path.write_text(json.dumps(stored))


def test_editing_the_stored_order_invalidates_the_ticket():
    rec = ticket.create(_order(price=1.10), ACCOUNT, ttl_seconds=TTL)
    _rewrite(rec, lambda s: s["order"]["legs"][0].update(quantity=50))
    with pytest.raises(ticket.TicketError, match="does not match its own order"):
        _consume(rec)


def test_stretching_expires_at_breaks_the_seal():
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=TTL)
    _rewrite(rec, lambda s: s.update(expires_at=s["expires_at"] + 86400))
    with pytest.raises(ticket.TicketError, match="seal"):
        _consume(rec)


def test_a_lifetime_longer_than_the_configured_ttl_is_refused():
    """Even a properly sealed ticket (minted under a longer TTL) cannot outlive the TTL configured
    at confirm time."""
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=300)
    with pytest.raises(ticket.TicketError, match="lifetime exceeds"):
        _consume(rec, ttl=60)


def test_swapping_the_masked_account_breaks_the_seal():
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=TTL)
    _rewrite(rec, lambda s: s.update(account="****9999"))
    with pytest.raises(ticket.TicketError, match="seal"):
        _consume(rec, account="5WT99993")


def test_swapping_in_a_matching_fingerprint_still_fails():
    rec = ticket.create(_order(price=1.10), ACCOUNT, ttl_seconds=TTL)
    swapped = _order(price=9.99)

    def mutate(s):
        s["order"] = swapped
        s["fingerprint"] = ticket.fingerprint(swapped, ACCOUNT)

    _rewrite(rec, mutate)
    with pytest.raises(ticket.TicketError):
        _consume(rec)


# --------------------------------------------------------------------------- housekeeping
def test_purge_removes_only_expired_tickets():
    live = ticket.create(_order(price=1.0), ACCOUNT, ttl_seconds=60)
    dead = ticket.create(_order(price=2.0), ACCOUNT, ttl_seconds=60)
    _rewrite(dead, lambda s: s.update(expires_at=time.time() - 1))
    assert ticket.purge_expired() == 1
    assert _consume(live)
    with pytest.raises(ticket.TicketError):
        _consume(dead)


def test_purge_removes_stale_claimed_files():
    """A confirm that crashed mid-way leaves a `.claimed` file; purge must not leave it forever."""
    rec = ticket.create(_order(), ACCOUNT, ttl_seconds=60)
    ticket.claim(rec["ticket_id"])
    claimed = _path(rec).with_suffix(".json.claimed")
    assert claimed.exists()
    assert ticket.purge_expired() == 0  # not expired yet
    data = json.loads(claimed.read_text())
    data["expires_at"] = time.time() - 1
    claimed.write_text(json.dumps(data))
    assert ticket.purge_expired() == 1
    assert not claimed.exists()


def test_purge_leaves_a_fresh_tmp_file_alone():
    cfgmod.desk_dir().mkdir(parents=True, exist_ok=True)
    tmp = cfgmod.desk_dir() / "pending-deadbeef.json.tmp"
    tmp.write_text("{partial")
    assert ticket.purge_expired() == 0
    old = time.time() - 3600
    os.utime(tmp, (old, old))
    assert ticket.purge_expired() == 1
