"""The PIN verifier, its lockout, and the audit journal.

For the PIN the properties that matter are what is NOT stored (reading the keyring entry must not
yield the PIN), where it may come from (an interactive terminal only), what changing it needs (the
current one), and what repeated failures do (lock it). For the journal: refusals are recorded as
faithfully as submissions, nothing sensitive lands in a line, the strict write raises, and "today"
is the ET session date.
"""

import hashlib
import json
import secrets

import pytest
from conftest import PIN, REAL_PBKDF2_ITERATIONS

from cherrypick.desk import config as cfgmod
from cherrypick.desk import journal, pin

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- the verifier
def test_a_set_pin_verifies():
    pin.set_pin(PIN)
    assert pin.is_set() is True
    assert pin.check(PIN) == pin.OK


def test_a_wrong_pin_is_rejected():
    pin.set_pin(PIN)
    assert pin.check(PIN[:-1]) == pin.BAD
    assert pin.check("") == pin.BAD


def test_the_raw_pin_is_never_stored(fake_keyring):
    pin.set_pin(PIN)
    stored = "".join(fake_keyring.store.values())
    assert PIN not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_new_verifiers_use_600k_iterations(monkeypatch, fake_keyring):
    assert REAL_PBKDF2_ITERATIONS == 600_000
    monkeypatch.setattr(pin, "PBKDF2_ITERATIONS", REAL_PBKDF2_ITERATIONS)
    pin.set_pin(PIN)
    stored = fake_keyring.store[(cfgmod.KEYRING_SERVICE, "confirm_pin_verifier")]
    assert stored.split("$")[1] == "600000"


def test_an_older_240k_verifier_still_verifies(fake_keyring):
    """Existing installs hold 240k-iteration records; the count is read from the record."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", b"an-old-desk-pin", salt, 240_000)
    fake_keyring.store[(cfgmod.KEYRING_SERVICE, "confirm_pin_verifier")] = (
        f"pbkdf2_sha256$240000${salt.hex()}${digest.hex()}"
    )
    assert pin.check("an-old-desk-pin") == pin.OK


def test_the_same_pin_stores_a_different_verifier_each_time(fake_keyring):
    pin.set_pin(PIN)
    first = dict(fake_keyring.store)
    pin.set_pin(PIN, current=PIN)
    assert dict(fake_keyring.store) != first


def test_no_pin_configured_verifies_nothing():
    assert pin.is_set() is False
    assert pin.verify("anything-at-all") is False
    with pytest.raises(pin.PinError, match="no desk PIN"):
        pin.check("anything-at-all")


def test_a_corrupt_verifier_refuses_rather_than_admits(fake_keyring):
    fake_keyring.store[(cfgmod.KEYRING_SERVICE, "confirm_pin_verifier")] = "garbage$$$"
    assert pin.check(PIN) == pin.BAD


def test_a_broken_keyring_backend_refuses(fake_keyring):
    fake_keyring.fail = True
    assert pin.is_set() is False
    assert pin.verify(PIN) is False
    with pytest.raises(pin.PinError):
        pin.check(PIN)


@pytest.mark.parametrize("short", ["abc", "123456", "nine-char"])
def test_pins_shorter_than_ten_are_rejected(short):
    with pytest.raises(pin.PinError, match="at least 10"):
        pin.set_pin(short)


# --------------------------------------------------------------------------- changing it
def test_changing_a_pin_needs_the_current_one():
    pin.set_pin(PIN)
    with pytest.raises(pin.PinError):
        pin.set_pin("a-brand-new-pin")
    with pytest.raises(pin.PinRejected):
        pin.set_pin("a-brand-new-pin", current="not-the-pin-at-all")
    assert pin.check(PIN) == pin.OK
    pin.set_pin("a-brand-new-pin", current=PIN)
    assert pin.check("a-brand-new-pin") == pin.OK


def test_clearing_a_pin_needs_the_current_one():
    pin.set_pin(PIN)
    with pytest.raises(pin.PinRejected):
        pin.clear_pin(current="not-the-pin-at-all")
    assert pin.is_set()
    pin.clear_pin(current=PIN)
    assert not pin.is_set()


# --------------------------------------------------------------------------- the lockout
def test_five_bad_pins_in_an_hour_lock_it_for_an_hour():
    pin.set_pin(PIN)
    t0 = 1_000_000.0
    outcomes = [pin.check("wrong-pin-value", now=t0 + i) for i in range(5)]
    assert outcomes == [pin.BAD] * 4 + [pin.LOCKOUT]
    # Locked: even the right PIN is not compared.
    assert pin.check(PIN, now=t0 + 60) == pin.LOCKED
    assert pin.check(PIN, now=t0 + 3599) == pin.LOCKED
    assert pin.check(PIN, now=t0 + 4 + 3601) == pin.OK


def test_failures_older_than_an_hour_do_not_count():
    pin.set_pin(PIN)
    t0 = 1_000_000.0
    for i in range(4):
        pin.check("wrong-pin-value", now=t0 + i)
    assert pin.check("wrong-pin-value", now=t0 + 3700) == pin.BAD


def test_a_good_pin_resets_the_counter():
    pin.set_pin(PIN)
    for _ in range(4):
        pin.check("wrong-pin-value")
    assert pin.check(PIN) == pin.OK
    assert pin.check("wrong-pin-value") == pin.BAD


def test_the_counter_lives_in_the_keyring_not_the_journal(fake_keyring):
    pin.set_pin(PIN)
    pin.check("wrong-pin-value")
    assert (cfgmod.KEYRING_SERVICE, "confirm_pin_failures") in fake_keyring.store
    assert not cfgmod.journal_path().exists()


def test_a_corrupt_counter_refuses(fake_keyring):
    pin.set_pin(PIN)
    fake_keyring.store[(cfgmod.KEYRING_SERVICE, "confirm_pin_failures")] = "{nope"
    with pytest.raises(pin.PinError, match="failure counter"):
        pin.check(PIN)


def test_a_counter_that_cannot_be_written_refuses(fake_keyring, monkeypatch):
    pin.set_pin(PIN)
    real_set = fake_keyring.set_password

    def no_counter(service, key, value):
        if key == "confirm_pin_failures":
            raise RuntimeError("read-only")
        real_set(service, key, value)

    monkeypatch.setattr(fake_keyring, "set_password", no_counter)
    with pytest.raises(pin.PinError, match="cannot record"):
        pin.check("wrong-pin-value")


def test_set_pin_respects_the_lockout():
    pin.set_pin(PIN)
    for _ in range(5):
        pin.check("wrong-pin-value")
    with pytest.raises(pin.PinRejected) as info:
        pin.set_pin("a-brand-new-pin", current=PIN)
    assert info.value.outcome == pin.LOCKED


# --------------------------------------------------------------------------- terminal-only entry
def test_pin_prompt_refuses_without_a_tty():
    with pytest.raises(pin.PinError, match="interactive terminal"):
        pin.prompt("Desk PIN: ")


def test_pin_prompt_reads_getpass_on_a_tty(tty):
    tty.answers = ["typed-in-secret"]
    assert pin.prompt("Desk PIN: ") == "typed-in-secret"


def test_the_pin_environment_variable_is_ignored(monkeypatch, tty):
    monkeypatch.setenv("CHERRYPICK_DESK_PIN", PIN)
    assert not hasattr(pin, "env_pin")
    tty.answers = ["from-the-terminal"]
    assert pin.prompt("Desk PIN: ") == "from-the-terminal"


# --------------------------------------------------------------------------- the journal
def test_events_append_one_line_each():
    journal.record("proposed", ticket_id="a1")
    journal.record("submitted", ticket_id="a1", max_loss=110.0)
    lines = cfgmod.journal_path().read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["event"] == "submitted"


def test_every_line_carries_utc_ts_and_an_et_session_date():
    entry = journal.record("proposed")
    assert entry["ts"].endswith("+00:00")
    assert len(entry["session_date"]) == 10


def test_account_numbers_are_masked_in_every_record():
    journal.record("submitted", account_number="5WT01234")
    written = cfgmod.journal_path().read_text()
    assert "5WT01234" not in written
    assert "****1234" in written


def test_refusals_are_recorded_too():
    journal.record("refused", phase="propose", refusals=["desk.enabled is false"])
    assert json.loads(cfgmod.journal_path().read_text().strip())["event"] == "refused"


def test_every_submit_attempt_counts_once_per_ticket():
    """Placed, failed and uncertain attempts all spend the day's budget — a submit that raised may
    have reached the broker. Refused and proposed cost nothing; one ticket counts once."""
    journal.record("proposed", ticket_id="t0", max_loss=500.0)
    journal.record("refused", ticket_id="t0", max_loss=500.0)
    journal.record("submitting", ticket_id="t1", max_loss=90.0)
    journal.record("submitted", ticket_id="t1", max_loss=90.0)
    journal.record("submitting", ticket_id="t2", max_loss=50.0)
    journal.record("failed", ticket_id="t2", max_loss=50.0)
    journal.record("submitting", ticket_id="t3", max_loss=10.0)  # crashed mid-submit: still counts
    journal.record("uncertain", ticket_id="t4", max_loss=5.0)
    day = journal.read_all()[0]["session_date"]
    orders, risk = journal.today_totals(day)
    assert orders == 4
    assert risk == pytest.approx(155.0)


def test_totals_use_the_et_session_date_not_utc():
    """00:30 UTC on the 2nd is 20:30 ET on the 1st — the day it belongs to."""
    path = cfgmod.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ts": "2026-10-02T00:30:00+00:00", "event": "submitted", "ticket_id": "x"}) + "\n"
    )
    assert journal.today_totals("2026-10-01") == (1, 0.0)
    assert journal.today_totals("2026-10-02") == (0, 0.0)


def test_totals_ignore_other_days():
    journal.record("submitted", max_loss=110.0)
    assert journal.today_totals("1999-01-01") == (0, 0.0)


def test_a_corrupt_line_does_not_break_reading():
    journal.record("submitted", max_loss=110.0)
    with cfgmod.journal_path().open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    journal.record("submitted", max_loss=10.0)
    assert len(journal.read_all()) == 2


def _unwritable(monkeypatch):
    monkeypatch.setattr("pathlib.Path.mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))


def test_a_plain_record_never_raises(monkeypatch):
    _unwritable(monkeypatch)
    assert journal.record("refused")["event"] == "refused"


def test_a_strict_record_raises(monkeypatch):
    _unwritable(monkeypatch)
    with pytest.raises(journal.JournalError):
        journal.record_strict("submitting")
