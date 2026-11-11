"""Accounts on two broker logins: each judged on its own history, never another's.

A cash IRA (main, designated) and a Reg T margin account (the `margin` login, flagged manage) hold the
same name. The snapshot's top-level history is the designated account's (`history_account`); the
margin account's is in `accounts_history`. What must hold:

- each account's wheel cycles come from its own history only;
- a long call in one account and a short in the other are never one PMCC;
- an account the snapshot carries no history for gets no history views, and says why;
- a snapshot from before named logins (no `history_account`) reads exactly as before.
"""

from test_pmcc import HIST, LONG, SHORT, history, legs
from test_wheel import CALL52, PLAIN, opt_leg, share_leg

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

RULES = _rules.effective(None)[0]


def _acct(key, login, legs_, designated=False, role=None, kind="cash"):
    return {
        "account": f"****{key[-4:]}",
        "account_key": key,
        "login": login,
        "role": role or ("live" if designated else "managed"),
        "designated": designated,
        "underlyings": [{"underlying": "XYZ", "legs": legs_}],
        "balances": {"net_liquidating_value": 100_000, "derivative_buying_power": 50_000},
        "account_type": {"margin_or_cash": "Cash" if kind == "cash" else "Margin", "type_name": "Roth IRA"},
    }


def _snapshot(accounts, top_history, others, session="2026-09-01", history_account="main-1234"):
    return {
        "session": session,
        "positions": {"ok": True, "accounts": accounts},
        "metrics": {"XYZ": {"last": 51.0}},
        "transactions": {"XYZ": top_history},
        "working_orders": [],
        "account_transactions": [],
        "history_account": history_account,
        "accounts_history": others,
        "underlying_history": HIST,
    }


def _by_key(out):
    return {a["account_key"]: a for a in out["accounts"]}


def test_each_account_s_wheel_comes_from_its_own_history():
    wheel_legs = [share_leg(100, 51.0, 50.0), opt_leg(CALL52, -1, 0.50, 0.80)]
    ira = _acct("main-1234", "main", wheel_legs, designated=True)
    margin = _acct("margin-1234", "margin", wheel_legs, kind="margin")
    # The margin account's own history is empty: it must NOT borrow the IRA's cycle.
    snap = _snapshot(
        [ira, margin],
        PLAIN[:4],
        {"margin-1234": {"transactions": {"XYZ": []}, "transactions_error": None, "working_orders": []}},
    )
    out = _by_key(ev.evaluate(snap, RULES))
    assert [(c["status"], c["reconciled"]) for c in out["main-1234"]["wheel"]["cycles"]] == [("open", True)]
    # Its own (empty) history never traded the call it holds: unreconciled -- had it read the IRA's
    # history it would have reconciled like the IRA's.
    (theirs,) = out["margin-1234"]["wheel"]["cycles"]
    assert theirs["reconciled"] is False
    assert any("never traded" in p for p in theirs["problems"])
    assert out["margin-1234"]["login"] == "margin" and out["margin-1234"]["role"] == "managed"


def test_the_margin_account_s_own_history_builds_its_own_cycle():
    wheel_legs = [share_leg(100, 51.0, 50.0), opt_leg(CALL52, -1, 0.50, 0.80)]
    ira = _acct("main-1234", "main", [share_leg(100, 51.0, 50.0)], designated=True)
    margin = _acct("margin-1234", "margin", wheel_legs, kind="margin")
    snap = _snapshot(
        [ira, margin],
        [],  # the IRA never wheeled
        {
            "margin-1234": {
                "transactions": {"XYZ": PLAIN[:4]},
                "transactions_error": None,
                "working_orders": [],
            }
        },
    )
    out = _by_key(ev.evaluate(snap, RULES))
    (cycle,) = out["margin-1234"]["wheel"]["cycles"]
    assert cycle["status"] == "open" and cycle["reconciled"] is True
    # The premium it collected is the margin account's (the put and the call), never the IRA's.
    assert all(not c.get("contracts") for c in out["main-1234"]["wheel"]["cycles"])


def test_a_long_in_one_account_and_a_short_in_the_other_is_never_one_pmcc():
    # The PMCC's long sits in the IRA, its short in the margin account; each account's history holds
    # only its own leg's orders.
    rows = history()
    long_rows = [r for r in rows if r.get("symbol") == LONG]
    short_rows = [r for r in rows if r.get("symbol") != LONG]
    long_leg, short_leg = legs()
    ira = _acct("main-1234", "main", [long_leg], designated=True)
    margin = _acct("margin-1234", "margin", [short_leg], kind="margin")
    snap = _snapshot(
        [ira, margin],
        long_rows,
        {
            "margin-1234": {
                "transactions": {"XYZ": short_rows},
                "transactions_error": None,
                "working_orders": [],
            }
        },
        session="2026-10-13",
    )
    out = _by_key(ev.evaluate(snap, RULES))
    for key in ("main-1234", "margin-1234"):
        for p in out[key]["pmcc"]["positions"]:
            held = {p.get("long", {}).get("symbol"), *(s.get("symbol") for s in p.get("shorts") or [])}
            assert not ({LONG, SHORT} <= held), f"{key} linked legs from two accounts"
    # Each account's trades hold only its own legs.
    assert {leg["symbol"] for t in out["main-1234"]["trades"] for leg in t["legs"]} == {LONG}
    assert {leg["symbol"] for t in out["margin-1234"]["trades"] for leg in t["legs"]} == {SHORT}


def test_an_account_with_no_history_in_the_snapshot_gets_no_history_views():
    ira = _acct("main-1234", "main", [share_leg(100, 51.0, 50.0)], designated=True)
    stray = _acct("margin-5678", "margin", [share_leg(100, 51.0, 50.0)], kind="margin")
    out = _by_key(ev.evaluate(_snapshot([ira, stray], PLAIN[:4], {}), RULES))
    assert out["margin-5678"]["wheel"]["error"] == ev.NO_ACCOUNT_HISTORY
    assert out["margin-5678"]["pmcc"]["error"] == ev.NO_ACCOUNT_HISTORY
    assert out["margin-5678"]["premium"] is None and out["margin-5678"]["ledger"] is None


def test_a_snapshot_from_before_named_logins_reads_as_before():
    wheel_legs = [share_leg(100, 51.0, 50.0), opt_leg(CALL52, -1, 0.50, 0.80)]
    acct = {
        "account": "****1234",
        "designated": True,
        "underlyings": [{"underlying": "XYZ", "legs": wheel_legs}],
    }
    other = {
        "account": "****5678",
        "designated": False,
        "underlyings": [{"underlying": "XYZ", "legs": wheel_legs}],
    }
    snap = {
        "session": "2026-09-01",
        "positions": {"ok": True, "accounts": [acct, other]},
        "metrics": {"XYZ": {"last": 51.0}},
        "transactions": {"XYZ": PLAIN[:4]},
        "working_orders": [],
    }
    mine, theirs = ev.evaluate(snap, RULES)["accounts"]
    assert len(mine["wheel"]["cycles"]) == 1 and mine["account_key"] == "main-1234"
    assert theirs["wheel"]["cycles"] == [] and theirs["wheel"]["error"] == ev.NO_ACCOUNT_HISTORY


def test_account_snapshot_never_leaks_the_top_level_history_to_another_account():
    snap = {
        "transactions": {"XYZ": ["top"]},
        "working_orders": ["top"],
        "history_account": "main-1234",
        "accounts_history": {},
    }
    view = ev.account_snapshot(snap, {"account_key": "margin-1234"}, 2)
    assert "transactions" not in view and "working_orders" not in view
    assert ev.account_snapshot(snap, {"account_key": "main-1234"}, 2) is snap
