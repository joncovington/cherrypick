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


def verdicts(out, fund="TQQQ", day=DAY):
    d = next(d for d in out["days"] if d["day"] == day)
    return [(r["level"], r["verdict"]) for r in d["funds"][fund]["rows"]], d


def run(live, paper, **kw):
    return shadow.report(live, paper, arm="session", funds=["TQQQ"], **kw)


def test_a_live_buy_that_paper_bought_agrees_and_a_later_lot_the_cap_blocked_is_capped(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    live_block(live, "TQQQ", "name_cap_full", "08:16", "19:59")
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    paper_lot(paper, "TQQQ", 2, 23, 42.38, "14:26")
    out = run(live, paper)
    got, day = verdicts(out)
    assert got == [(45.57, "agree"), (42.38, "capped")]
    assert day["consistent"] and out["summary"] == {"days": 1, "consistent_days": 1, "mismatches": 0}


def test_a_live_buy_paper_never_reached_is_resting(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 13, 3831)
    paper_ask(paper, "TQQQ", 44.44)
    got, day = verdicts(run(live, paper))
    assert got == [(38.31, "resting")] and day["consistent"]


def test_a_live_buy_whose_level_paper_reached_without_buying_is_live_only(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 13, 3831)
    paper_ask(paper, "TQQQ", 38.20)
    got, day = verdicts(run(live, paper))
    assert got == [(38.31, "live_only")] and not day["consistent"]


def test_a_live_buy_on_a_day_paper_recorded_no_quote_is_not_agreement(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 13, 3831)
    got, day = verdicts(run(live, paper))
    assert got == [(38.31, "paper_no_data")] and not day["consistent"]


def test_a_lot_with_other_shares_at_the_same_level_differs(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    paper_lot(paper, "TQQQ", 1, 20, 45.57, "10:37")
    got, day = verdicts(run(live, paper))
    assert got == [(45.57, "differs")] and not day["consistent"]


def test_a_re_sized_intent_at_a_level_replaces_the_old_one(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 22, 4557)  # the terms moved within the session
    live_buy(live, "TQQQ", 1, 21, 4557)
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    got, _ = verdicts(run(live, paper))
    assert got == [(45.57, "agree")]


def test_pairing_is_by_level_so_a_skipped_level_does_not_shift_the_rest(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    live_buy(live, "TQQQ", 3, 25, 3940)  # live journaled levels 1 and 3, not 2
    live_block(live, "TQQQ", "name_cap_full", "08:16", "19:59")
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    paper_lot(paper, "TQQQ", 2, 23, 42.38, "14:26")
    paper_lot(paper, "TQQQ", 3, 25, 39.40, "15:10")
    got, _ = verdicts(run(live, paper))
    assert got == [(45.57, "agree"), (42.38, "capped"), (39.40, "agree")]


def test_a_paper_lot_live_had_no_reason_to_skip_is_paper_only(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    live_ticks(live, "14:25", "14:26", "14:27")
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    paper_lot(paper, "TQQQ", 2, 23, 42.38, "14:26")
    got, day = verdicts(run(live, paper))
    assert got == [(45.57, "agree"), (42.38, "paper_only")] and not day["consistent"]


def test_a_paper_lot_while_the_live_loop_was_not_ticking_says_so(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    live_ticks(live, "10:36", "10:37")
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    paper_lot(paper, "TQQQ", 2, 23, 42.38, "14:26")  # no live tick near 14:26
    got, _ = verdicts(run(live, paper))
    assert got == [(45.57, "agree"), (42.38, "live_not_ticking")]


@pytest.mark.parametrize(
    "reason", ["after_disarm_at", "no_reference_close", "suspected_split", "quote_stale"]
)
def test_a_block_that_is_not_one_of_the_live_limits_does_not_explain_a_lot(ledgers, reason):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    live_block(live, "TQQQ", reason, "08:16", "20:00")
    live_ticks(live, "14:26")
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    paper_lot(paper, "TQQQ", 2, 23, 42.38, "14:26")
    got, _ = verdicts(run(live, paper))
    assert got == [(45.57, "agree"), (42.38, "paper_only")]


def test_a_limit_journaled_for_every_fund_covers_this_one(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    live_block(live, "-", "halted", "14:00", "15:00")
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    paper_lot(paper, "TQQQ", 2, 23, 42.38, "14:26")
    got, _ = verdicts(run(live, paper))
    assert got == [(45.57, "agree"), (42.38, "capped")]


def test_only_the_live_pilots_funds_are_compared(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    paper_lot(paper, "SOXL", 1, 30, 30.00, "10:40")  # paper trades it; the live pilot does not
    out = run(live, paper)
    assert list(out["days"][0]["funds"]) == ["TQQQ"] and out["funds"] == ["TQQQ"]


def test_another_paper_arm_is_not_read(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37", arm="chain")
    paper_ask(paper, "TQQQ", 50.0)
    got, _ = verdicts(run(live, paper))
    assert got == [(45.57, "resting")]


def test_since_drops_the_earlier_days(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    live_buy(live, "TQQQ", 1, 13, 3831, day="2026-11-23")
    out = run(live, paper, since="2026-11-23")
    assert [d["day"] for d in out["days"]] == ["2026-11-23"]


def test_no_pilot_funds_is_a_refusal_not_an_empty_agreement(ledgers):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    out = shadow.report(live, paper, arm="session", funds=[])
    assert out["ok"] is False and "live.symbols" in out["error"]


def _cli(monkeypatch, capsys, *argv):
    monkeypatch.setattr(
        cli, "load_config", lambda path=None: {"live": {"arm": "session", "symbols": ["tqqq"]}}
    )
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_the_cli_reads_both_ledgers_and_the_live_arm_and_funds(ledgers, tmp_path, monkeypatch, capsys):
    live, paper = ledgers
    live_buy(live, "TQQQ", 1, 21, 4557)
    paper_lot(paper, "TQQQ", 1, 21, 45.57, "10:37")
    live.close()
    paper.close()
    code, out = _cli(
        monkeypatch,
        capsys,
        "--db",
        str(tmp_path / "paper_trades.db"),
        "shadow",
        "--live-db",
        str(tmp_path / "live_trades.db"),
    )
    assert code == 0 and out["arm"] == "session" and out["funds"] == ["TQQQ"]
    assert out["summary"]["consistent_days"] == 1


def test_the_cli_answers_in_json_when_the_live_loop_has_never_ticked(tmp_path, monkeypatch, capsys):
    code, out = _cli(monkeypatch, capsys, "shadow", "--live-db", str(tmp_path / "never.db"))
    assert code == 0 and out["ok"] is False and "has not ticked" in out["error"]
    assert not (tmp_path / "never.db").exists()  # read only: nothing was created


def test_the_cli_refuses_a_since_that_is_not_a_date(monkeypatch, capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["shadow", "--since", "11/20"])
    assert stop.value.code == 2 and "not a YYYY-MM-DD date" in capsys.readouterr().err
