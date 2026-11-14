"""The paper path and the backtest path decide the same on the same market.

The backtest builds a snapshot from store rows and calls `engine.decide`; the paper loop records the
snapshot it saw (`to_dict`, through JSON) and decides again from the record (`paper.replay`). Both must
return the same decision, entry and prices included, for every arm. The test is shown to fail by
changing the recorded market and by disabling an arm.
"""

from __future__ import annotations

import json

from market import CALENDAR, spx_snapshot

from cherrypick.ensemble import arms, engine, paper
from cherrypick.ensemble.model import Snapshot

A = arms.load_arms()


def summary(d):
    e = d.entry
    return (
        d.arm,
        d.enter,
        d.reason,
        None
        if e is None
        else ([(g.name, g.strike) for g in e.legs], e.quantity, e.prices, e.slippage, e.fees_open),
    )


def test_backtest_and_paper_paths_agree_for_every_arm():
    snap = spx_snapshot(vol=0.2, oi_call=2000)
    backtest = [summary(engine.decide(a, snap)) for _, a in sorted(A.items()) if a["symbol"] == "SPX"]
    recorded = json.loads(json.dumps(snap.to_dict()))
    paper_side = [summary(d) for d in paper.replay(A, recorded, CALENDAR)]
    assert backtest == paper_side
    assert any(s[1] for s in backtest) and not all(s[1] for s in backtest)  # a mix of entries and skips


def test_the_agreement_check_can_fail():
    snap = spx_snapshot(vol=0.2)
    recorded = json.loads(json.dumps(snap.to_dict()))
    recorded["chains"][snap.session]["rows"][0]["bid"] += 0.0  # unchanged: still agrees
    same = [summary(d) for d in paper.replay(A, recorded, CALENDAR)]
    changed = json.loads(json.dumps(snap.to_dict()))
    changed["prev_close"] = 6100.0  # the market the paper loop saw differs from the backtest's
    differs = [summary(d) for d in paper.replay(A, changed, CALENDAR)]
    assert same != differs
    off = {**A, "iron-fly-1000": {**A["iron-fly-1000"], "enabled": False}}
    assert (
        len(paper.decide_all(off, Snapshot.from_dict(recorded, CALENDAR)))
        == len(paper.replay(A, recorded, CALENDAR)) - 1
    )


def test_the_test_imports_the_worktree_not_the_live_checkout():
    from pathlib import Path

    import cherrypick.core.fees as fees

    import cherrypick.ensemble as pkg

    here = Path(__file__).resolve()
    assert Path(pkg.__file__).resolve().is_relative_to(here.parents[1])
    assert Path(fees.__file__).resolve().is_relative_to(here.parents[2])  # the checkout's core, not another's
