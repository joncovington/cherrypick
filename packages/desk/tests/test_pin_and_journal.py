"""The PIN verifier and the audit journal.

For the PIN the property that matters is what is NOT stored: reading the keyring entry must not
yield the PIN. For the journal it is that refusals are recorded as faithfully as submissions, and
that nothing sensitive ever lands in a line.
"""

import json

import pytest

from cherrypick.desk import config as cfgmod
from cherrypick.desk import journal, pin

pytestmark = pytest.mark.unit


class _FakeKeyring:
    """In-memory stand-in — these tests must not touch the real OS credential store."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, key, value):
        self.store[(service, key)] = value

    def get_password(self, service, key):
        return self.store.get((service, key))

    def delete_password(self, service, key):
        self.store.pop((service, key), None)


@pytest.fixture
def fake_keyring(monkeypatch):
    kr = _FakeKeyring()
    monkeypatch.setattr(pin, "_keyring", lambda: kr)
    return kr


# --------------------------------------------------------------------------- the PIN
def test_a_set_pin_verifies(fake_keyring):
    pin.set_pin("hunter2-desk")
    assert pin.is_set() is True
    assert pin.verify("hunter2-desk") is True


def test_a_wrong_pin_is_rejected(fake_keyring):
    pin.set_pin("hunter2-desk")
    assert pin.verify("hunter2-des") is False
    assert pin.verify("") is False


def test_the_raw_pin_is_never_stored(fake_keyring):
    """The keyring is readable by anything running as this user, so storing the PIN there would
    make it pointless. Only a salted PBKDF2 verifier goes in."""
    pin.set_pin("hunter2-desk")
    stored = "".join(fake_keyring.store.values())
    assert "hunter2-desk" not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_the_same_pin_stores_a_different_verifier_each_time(fake_keyring):
    """Per-PIN salt: two accounts with the same PIN must not produce the same stored value."""
    pin.set_pin("hunter2-desk")
    first = dict(fake_keyring.store)
    pin.set_pin("hunter2-desk")
    assert dict(fake_keyring.store) != first


def test_no_pin_configured_reads_as_not_set_and_verifies_nothing(fake_keyring):
    assert pin.is_set() is False
    assert pin.verify("anything") is False


def test_a_corrupt_verifier_refuses_rather_than_admits(fake_keyring):
    """A damaged keyring entry must fail closed — the tempting bug is to treat 'cannot check' as
    'no PIN required'."""
    fake_keyring.set_password(cfgmod.KEYRING_SERVICE, "confirm_pin_verifier", "garbage$$$")
    assert pin.verify("hunter2-desk") is False


def test_a_broken_keyring_backend_refuses(monkeypatch):
    """No keyring at all is still a refusal, not a bypass."""

    def boom():
        raise RuntimeError("no backend")

    monkeypatch.setattr(pin, "_keyring", boom)
    assert pin.is_set() is False
    assert pin.verify("hunter2-desk") is False


def test_short_pins_are_rejected(fake_keyring):
    with pytest.raises(pin.PinError, match="at least"):
        pin.set_pin("abc")


def test_clear_removes_the_pin(fake_keyring):
    pin.set_pin("hunter2-desk")
    pin.clear_pin()
    assert pin.is_set() is False


# --------------------------------------------------------------------------- the journal
@pytest.fixture
def tmp_journal(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    monkeypatch.setattr(cfgmod, "journal_path", lambda: path)
    return path


def test_events_append_one_line_each(tmp_journal):
    journal.record("proposed", ticket_id="a1")
    journal.record("submitted", ticket_id="a1", max_loss=110.0)
    lines = tmp_journal.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["event"] == "submitted"


def test_account_numbers_are_masked_in_every_record(tmp_journal):
    journal.record("submitted", account_number="5WI62375")
    written = tmp_journal.read_text()
    assert "5WI62375" not in written
    assert "****2375" in written


def test_refusals_are_recorded_too(tmp_journal):
    """A log of only successes cannot answer 'what was attempted and what stopped it?' — which is
    the question actually asked after something goes wrong."""
    journal.record("refused", phase="propose", refusals=["desk.enabled is false"])
    assert json.loads(tmp_journal.read_text().strip())["event"] == "refused"


def test_daily_totals_count_only_submissions(tmp_journal):
    """A refused or merely proposed order consumed no risk budget. Counting proposals would let a
    rejected attempt eat the day's allowance."""
    journal.record("proposed", max_loss=500.0)
    journal.record("refused", max_loss=500.0)
    journal.record("submitted", max_loss=110.0)
    journal.record("submitted", max_loss=350.0)
    day = json.loads(tmp_journal.read_text().splitlines()[0])["ts"][:10]
    orders, risk = journal.today_totals(day)
    assert orders == 2
    assert risk == pytest.approx(460.0)


def test_totals_ignore_other_days(tmp_journal):
    journal.record("submitted", max_loss=110.0)
    assert journal.today_totals("1999-01-01") == (0, 0.0)


def test_a_corrupt_line_does_not_break_reading(tmp_journal):
    journal.record("submitted", max_loss=110.0)
    with tmp_journal.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    journal.record("submitted", max_loss=10.0)
    assert len(journal.read_all()) == 2


def test_an_unwritable_journal_never_raises(monkeypatch, tmp_path):
    """An audit write must not be able to break a decision that was already made correctly. (The
    reverse — failing open on a GATE — is what must never happen, and that is policy.py's job.)"""
    monkeypatch.setattr(cfgmod, "journal_path", lambda: tmp_path / "nope" / "x" / "j.jsonl")
    monkeypatch.setattr("pathlib.Path.mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    assert journal.record("submitted")["event"] == "submitted"
