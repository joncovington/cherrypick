"""Desk order notifier: cards on submit, cards on terminal state, and never a card twice.

The broker is always injected (`status_fn`), so nothing here touches a network or a keyring.
"""

from __future__ import annotations

import json

import pytest

from cherrypick.orchestrator import desk_notifier


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point every piece of module state at a temp dir so runs can't see the real journal."""
    monkeypatch.setattr(desk_notifier, "_STATE", tmp_path / "desk_notify.json")
    monkeypatch.setattr(desk_notifier, "_LOCK", tmp_path / "desk_notify.lock")
    monkeypatch.setattr(desk_notifier.cfgmod, "ensure_dirs", lambda: None)
    return tmp_path


def _journal(tmp_path, *entries) -> str:
    path = tmp_path / "journal.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    return str(path)


def _submitted(order_id="111", **over):
    # A deliberately old timestamp: seeding adopts *today's* orders onto the watch list, so a
    # fixture dated "now" would make these tests pass or fail depending on the wall clock.
    entry = {
        "ts": "2020-01-01T14:32:07+00:00",
        "event": "submitted",
        "order_id": order_id,
        "account": "****2375",
        "classification": "closing",
        "underlyings": ["VG"],
        "max_loss": 96.0,
        "legs": [
            {
                "action": "buy to close",
                "quantity": 1,
                "symbol": "VG 260814P00014000",
                "right": "P",
                "strike": 14.0,
                "expiration": "2026-08-14",
            },
        ],
    }
    entry.update(over)
    return entry


class _Spy:
    """Stands in for Notifier, recording each push."""

    def __init__(self, *_a, **_k):
        self.pushes: list[dict] = []

    def notify(self, level, key, title, message, embed=None):
        self.pushes.append({"key": key, "title": title, "message": message, "embed": embed})
        return {"log": {"ok": True}}


@pytest.fixture
def spy(monkeypatch):
    holder = _Spy()
    monkeypatch.setattr(desk_notifier, "Notifier", lambda *a, **k: holder)
    return holder


def _cfg(journal, enabled=True):
    return {"desk_notify": {"enabled": enabled, "journal_path": journal, "channels": ["log"]}}


def test_disabled_by_default_does_nothing(tmp_path, spy):
    out = desk_notifier.run({}, status_fn=lambda oid: None)
    assert out["skipped"] == "disabled in config (desk_notify)"
    assert spy.pushes == []


def test_first_run_seeds_without_backfilling(tmp_path, spy):
    """Enabling on a machine with history must not fire a card for every past order."""
    journal = _journal(tmp_path, _submitted("111"), _submitted("222"))
    out = desk_notifier.run(_cfg(journal), status_fn=lambda oid: None)
    assert out["seeded"] is True and out["orders"] == 2
    assert spy.pushes == []


def test_new_submission_pushes_one_card(tmp_path, spy):
    journal = _journal(tmp_path, _submitted("111"))
    desk_notifier.run(_cfg(journal), status_fn=lambda oid: None)  # seed
    journal = _journal(tmp_path, _submitted("111"), _submitted("222"))
    out = desk_notifier.run(_cfg(journal), status_fn=lambda oid: None)

    assert out["pushed"] == 1
    (push,) = spy.pushes
    assert push["key"] == "desk.submitted.222"
    assert push["embed"]["color"] == desk_notifier.COLOR_SUBMITTED
    assert "order 222" in push["embed"]["footer"]["text"]


def test_fill_pushes_a_second_card_then_stops(tmp_path, spy):
    journal = _journal(tmp_path, _submitted("111"))
    desk_notifier.run(_cfg(journal), status_fn=lambda oid: None)  # seed
    journal = _journal(tmp_path, _submitted("111"), _submitted("222"))
    desk_notifier.run(_cfg(journal), status_fn=lambda oid: None)  # submitted card
    spy.pushes.clear()

    filled = {"status": "Filled", "price": "0.96", "filled": True}
    out = desk_notifier.run(_cfg(journal), status_fn=lambda oid: filled)
    assert out["pushed"] == 1
    (push,) = spy.pushes
    assert push["key"] == "desk.filled.222"
    assert push["embed"]["color"] == desk_notifier.COLOR_FILLED
    assert "0.96" in push["embed"]["fields"][0]["value"]

    # A settled order is never carded again, however many passes run.
    spy.pushes.clear()
    out = desk_notifier.run(_cfg(journal), status_fn=lambda oid: filled)
    assert out["pushed"] == 0 and spy.pushes == []


def test_still_working_order_pushes_nothing(tmp_path, spy):
    journal = _journal(tmp_path, _submitted("111"))
    desk_notifier.run(_cfg(journal), status_fn=lambda oid: None)
    journal = _journal(tmp_path, _submitted("111"), _submitted("222"))
    desk_notifier.run(_cfg(journal), status_fn=lambda oid: None)
    spy.pushes.clear()

    out = desk_notifier.run(_cfg(journal), status_fn=lambda oid: {"status": "Live"})
    assert out["pushed"] == 0 and out["watching"] == 1


def test_unreachable_broker_keeps_watching_rather_than_settling(tmp_path, spy):
    """`status_fn` returning None means 'could not ask', never 'nothing happened'."""
    journal = _journal(tmp_path, _submitted("111"))
    desk_notifier.run(_cfg(journal), status_fn=lambda oid: None)
    journal = _journal(tmp_path, _submitted("111"), _submitted("222"))
    desk_notifier.run(_cfg(journal), status_fn=lambda oid: None)

    out = desk_notifier.run(_cfg(journal), status_fn=lambda oid: None)
    assert out["watching"] == 1
    assert desk_notifier.classify(None) is None


@pytest.mark.parametrize(
    ("raw", "expected", "color"),
    [
        ("Filled", "filled", desk_notifier.COLOR_FILLED),
        ("Cancelled", "cancelled", desk_notifier.COLOR_CANCELLED),
        ("Rejected", "rejected", desk_notifier.COLOR_REJECTED),
        ("Expired", "expired", desk_notifier.COLOR_CANCELLED),
    ],
)
def test_terminal_states_classify_and_color(raw, expected, color):
    assert desk_notifier.classify({"status": raw}) == expected
    embed = desk_notifier.build_embed(_submitted(), state=expected)
    assert embed["color"] == color


def test_uncomputable_max_loss_never_reads_as_no_risk():
    text = desk_notifier.describe_order(_submitted(max_loss=None))
    assert "uncomputable" in text
    assert "$0" not in text


def test_torn_journal_line_is_skipped(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text(json.dumps(_submitted("111")) + "\n{not json", encoding="utf-8")
    assert [e["order_id"] for e in desk_notifier.read_submitted(path)] == ["111"]


def test_missing_journal_is_not_an_error(tmp_path):
    assert desk_notifier.read_submitted(tmp_path / "nope.jsonl") == []


def test_embed_field_stays_within_discord_limit(tmp_path):
    legs = [
        {
            "action": "buy to close",
            "quantity": 1,
            "symbol": f"VG 2608{i:02d}P00014000",
            "right": "P",
            "strike": 14.0 + i,
            "expiration": "2026-08-14",
        }
        for i in range(200)
    ]
    embed = desk_notifier.build_embed(_submitted(legs=legs), state="submitted")
    assert len(embed["fields"][0]["value"]) <= desk_notifier._FIELD_MAX
    assert len(embed["title"]) <= 256


def test_seed_watches_todays_orders_but_not_older_ones(tmp_path, spy):
    """An order submitted just before this was switched on is the one whose fill you want."""
    from datetime import UTC, datetime

    today = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    journal = _journal(
        tmp_path,
        _submitted("old", ts="2020-01-01T00:00:00+00:00"),
        _submitted("today", ts=today),
    )
    out = desk_notifier.run(_cfg(journal), status_fn=lambda oid: None)

    assert out["seeded"] is True and out["watching"] == 1
    assert spy.pushes == []

    # The seeded-today order still cards on fill; the ancient one never does.
    filled = {"status": "Filled", "price": "0.96"}
    out = desk_notifier.run(_cfg(journal), status_fn=lambda oid: filled)
    assert [p["key"] for p in spy.pushes] == ["desk.filled.today"]
