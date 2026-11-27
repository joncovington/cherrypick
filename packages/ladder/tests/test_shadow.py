"""The shadow report lays each armed day's live dry-run buys beside paper's arm. Synthetic ledgers only."""

import json
from datetime import datetime

import pytest
from cherrypick.core.clock import ET

from cherrypick.ladder import cli, db, live_db, shadow

DAY = "2026-11-20"


def _t(hhmm: str, day: str = DAY) -> str:
    h, m = hhmm.split(":")
    return datetime.fromisoformat(day).replace(hour=int(h), minute=int(m), tzinfo=ET).isoformat()


@pytest.fixture
def ledgers(tmp_path):
    return live_db.connect(str(tmp_path / "live_trades.db")), db.connect(str(tmp_path / "paper_trades.db"))


def live_buy(live, fund, n, shares, cents, day=DAY):
    """A dry-run intent as live_tick journals it: the client id in the reason, the terms in `detail`."""
    eid = f"ldr.b.{fund}.{day.replace('-', '')}.{n}.{shares}x{cents}"
    detail = json.dumps(
        {"reason": None, "lot": None, "ok": True, "shares": shares, "limit": cents / 100, "tif": "Day"}
    )
    live.execute(
        "INSERT INTO ladder_decisions (trade_date, arm, symbol, mode, reason, accepted, occurrences, "
        "first_ts, last_ts, detail) VALUES (?, 'session', ?, 'dry_run', ?, 1, 1, ?, ?, ?)",
        (day, fund, f"would place buy {fund} {eid}", _t("08:15", day), _t("08:15", day), detail),
    )
    live.commit()


def test_intents_are_numbered_in_level_order_and_one_per_client_id(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 2, 23, 4238)
    live_buy(live, "TQQQ", 1, 21, 4557)
    live_buy(live, "TQQQ", 1, 21, 4557)  # the same intent journaled again
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    paper_lot(paper, "TQQQ", 2, 23, 42.38, "14:26")
    got, _ = rows(shadow.report(live, paper, arm="session", funds=["TQQQ"]))
    assert got == [(1, "agree"), (2, "agree")]


def live_block(live, fund, reason, first, last, day=DAY):
    live.execute(
        "INSERT INTO ladder_decisions (trade_date, arm, symbol, mode, reason, accepted, occurrences, "
        "first_ts, last_ts) VALUES (?, 'session', ?, 'blocked', ?, 0, 9, ?, ?)",
        (day, fund, reason, _t(first, day), _t(last, day)),
    )
    live.commit()


def live_ticks(live, *hhmms, day=DAY):
    for hhmm in hhmms:
        stamp = datetime.fromisoformat(_t(hhmm, day)).timestamp()
        live_db.record_iteration(live, ran_at=stamp, session_date=day, phase="session", status="ok")


def paper_lot(paper, fund, n, shares, level, hhmm, arm="session", day=DAY):
    db.open_lot(
        paper,
        {
            "position_id": f"{arm}:{fund}:{day}:{n}",
            "arm": arm,
            "symbol": fund,
            "entry_rule": "session",
            "target_rule": "fixed",
            "quantity": shares,
            "buy_level": level,
            "buy_price": level,
            "buy_session": day,
            "buy_time": _t(hhmm, day),
            "status": "open",
            "entry_value": round(-shares * level, 2),
        },
    )


def paper_ask(paper, fund, ask, day=DAY):
    db.record_snapshot(paper, trade_date=day, symbol=fund, kind="tick", status="ok", bid=ask - 0.02, ask=ask)


def rows(out, fund="TQQQ", day=DAY):
    d = next(d for d in out["days"] if d["day"] == day)
    return [(r["n"], r["verdict"]) for r in d["funds"][fund]["rows"]], d


def test_a_live_buy_that_paper_bought_agrees_and_a_later_lot_the_cap_blocked_is_capped(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    live_block(live, "TQQQ", "name_cap_full", "08:16", "19:59")
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    paper_lot(paper, "TQQQ", 2, 23, 42.38, "14:26")
    out = shadow.report(live, paper, arm="session", funds=["TQQQ"])
    got, day = rows(out)
    assert got == [(1, "agree"), (2, "capped")]
    assert day["consistent"] and out["summary"] == {"days": 1, "consistent_days": 1, "mismatches": 0}


def test_a_live_buy_paper_never_reached_is_resting(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 13, 3831)
    paper_ask(paper, "TQQQ", 44.44)
    got, day = rows(shadow.report(live, paper, arm="session", funds=["TQQQ"]))
    assert got == [(1, "resting")] and day["consistent"]


def test_a_live_buy_whose_level_paper_reached_without_buying_is_live_only(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 13, 3831)
    paper_ask(paper, "TQQQ", 38.20)
    got, day = rows(shadow.report(live, paper, arm="session", funds=["TQQQ"]))
    assert got == [(1, "live_only")] and not day["consistent"]


def test_a_lot_with_other_terms_differs(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    paper_lot(paper, "TQQQ", 1, 20, 45.57, "10:37")
    got, day = rows(shadow.report(live, paper, arm="session", funds=["TQQQ"]))
    assert got == [(1, "differs")] and not day["consistent"]


def test_a_paper_lot_live_had_no_reason_to_skip_is_paper_only(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    live_ticks(live, "14:25", "14:26", "14:27")
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    paper_lot(paper, "TQQQ", 2, 23, 42.38, "14:26")
    got, day = rows(shadow.report(live, paper, arm="session", funds=["TQQQ"]))
    assert got == [(1, "agree"), (2, "paper_only")] and not day["consistent"]


def test_a_paper_lot_while_the_live_loop_was_not_ticking_says_so(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    live_ticks(live, "10:36", "10:37")
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    paper_lot(paper, "TQQQ", 2, 23, 42.38, "14:26")  # no live tick near 14:26
    got, _ = rows(shadow.report(live, paper, arm="session", funds=["TQQQ"]))
    assert got == [(1, "agree"), (2, "live_not_ticking")]


def test_the_disarm_block_does_not_explain_a_lot(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    live_block(live, "TQQQ", "after_disarm_at", "08:16", "20:00")
    live_ticks(live, "14:26")
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    paper_lot(paper, "TQQQ", 2, 23, 42.38, "14:26")
    got, _ = rows(shadow.report(live, paper, arm="session", funds=["TQQQ"]))
    assert got == [(1, "agree"), (2, "paper_only")]


def test_only_the_live_pilots_funds_are_compared(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    paper_lot(paper, "SOXL", 1, 30, 30.00, "10:40")  # paper trades it; the live pilot does not
    out = shadow.report(live, paper, arm="session", funds=["TQQQ"])
    assert list(out["days"][0]["funds"]) == ["TQQQ"] and out["funds"] == ["TQQQ"]


def test_another_paper_arm_is_not_read(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37", arm="chain")
    paper_ask(paper, "TQQQ", 50.0)
    got, _ = rows(shadow.report(live, paper, arm="session", funds=["TQQQ"]))
    assert got == [(1, "resting")]


def test_since_drops_the_earlier_days(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    live_buy(live, "TQQQ", 1, 13, 3831, day="2026-11-23")
    out = shadow.report(live, paper, arm="session", funds=["TQQQ"], since="2026-11-23")
    assert [d["day"] for d in out["days"]] == ["2026-11-23"]


def test_the_cli_reads_both_ledgers_and_the_live_arm_and_funds(ledgers, tmp_path, monkeypatch, capsys):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    live.close()
    paper.close()
    monkeypatch.setattr(
        cli, "load_config", lambda path=None: {"live": {"arm": "session", "symbols": ["tqqq"]}}
    )
    code = cli.main(
        ["--db", str(tmp_path / "paper_trades.db"), "shadow", "--live-db", str(tmp_path / "live_trades.db")]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["arm"] == "session" and out["funds"] == ["TQQQ"]
    assert out["summary"]["consistent_days"] == 1
