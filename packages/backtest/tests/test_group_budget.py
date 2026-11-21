"""`read group-budget`: a group's max risk, each member's share and its drawdown from the members' pinned
Builder runs, laid over the sessions every run covers (`groupbudget`). Synthetic trades only."""

from __future__ import annotations

import json

from cherrypick.backtest import groupbudget, paths, readout


def _t(arm, entry, exit_, risk, net):
    return {"arm": arm, "session": entry, "exit_session": exit_, "risk": risk, "net": {"verdict": net}}


def _m(name, trades, span=None, licensed=False):
    return {
        "name": name,
        "run": f"builder-{name}-2611201000",
        "trades": trades,
        "span": span,
        "licensed": licensed,
    }


def test_the_max_risk_is_the_most_held_at_once_rounded_up_and_the_shares_fit_it():
    span = ["2026-11-02", "2026-11-04"]
    a = _m(
        "a",
        [_t("a", "2026-11-02", "2026-11-02", 400, 50), _t("a", "2026-11-03", "2026-11-03", 400, -200)],
        span,
    )
    b = _m("b", [_t("b", "2026-11-03", "2026-11-04", 230, 30)], span)
    out = groupbudget.read([a, b])
    # 11-03 holds 400 + 230 at once: 630, rounded up to 650.
    assert out["max_risk"] == 650 and out["sessions"] == 3 and out["overlap"] == ["2026-11-02", "2026-11-04"]
    assert out["shares"] == {"a": 61.6, "b": 35.4} and out["scaled"] is False and out["cut"] == {}
    # Net by exit session: +50, -200, +30: the fall from 50 to -150.
    assert out["drawdown"] == 200.0
    assert [r["peak"] for r in out["members"]] == [400.0, 230.0]


def test_peaks_on_different_sessions_compete_and_the_cut_is_counted():
    a = _m("a", [_t("a", "2026-11-02", "2026-11-02", 500, 0), _t("a", "2026-11-03", "2026-11-03", 100, 0)])
    b = _m("b", [_t("b", "2026-11-02", "2026-11-02", 100, 0), _t("b", "2026-11-03", "2026-11-03", 500, 0)])
    out = groupbudget.read([a, b])
    assert out["max_risk"] == 600 and out["scaled"] is True
    assert sum(out["shares"].values()) <= 100
    # Each, at 50% of 600 = 300, would have been cut on the one session it held 500.
    assert out["cut"] == {"a": 1, "b": 1}


def test_only_the_sessions_every_run_covers_are_read():
    a = _m("a", [_t("a", "2026-10-01", "2026-10-01", 900, -900), _t("a", "2026-11-03", "2026-11-03", 100, 0)],
           span=["2026-10-01", "2026-11-30"])  # fmt: skip
    b = _m("b", [_t("b", "2026-11-03", "2026-11-03", 100, 0)], span=["2026-11-01", "2026-11-30"])
    out = groupbudget.read([a, b])
    assert out["overlap"] == ["2026-11-01", "2026-11-30"] and out["max_risk"] == 200  # a's 900 is outside
    assert out["drawdown"] == 0.0  # and so is its loss


def test_runs_that_share_no_session_suggest_nothing_and_say_why():
    a = _m("a", [_t("a", "2026-10-01", "2026-10-01", 100, 0)], span=["2026-10-01", "2026-10-31"])
    b = _m("b", [_t("b", "2026-11-03", "2026-11-03", 100, 0)], span=["2026-11-01", "2026-11-30"])
    out = groupbudget.read([a, b])
    assert out["why"] == "the runs share no session" and "max_risk" not in out


def test_one_licensed_run_makes_the_reading_licensed():
    a = _m("a", [_t("a", "2026-11-02", "2026-11-02", 100, 0)], licensed=True)
    assert groupbudget.read([a])["licensed"] is True


def _write(d, name, trades, **over):
    d.mkdir(parents=True, exist_ok=True)
    doc = {"name": name, "licensed": False, "sessions": ["2026-11-02", "2026-11-04"], "trades": trades,
           "generated_at": name[-10:], **over}  # fmt: skip
    (d / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_the_read_takes_each_automations_own_run_and_only_its_trades(tmp_path, monkeypatch):
    own, lic = tmp_path / "own", tmp_path / "lic"
    monkeypatch.setattr(paths, "automations_dir", lambda licensed: lic if licensed else own)
    _write(
        own,
        "builder-alpha-2611201000",
        [_t("alpha", "2026-11-02", "2026-11-02", 300, 0), _t("other", "2026-11-02", "2026-11-02", 9000, 0)],
    )
    _write(lic, "builder-beta-2611201000", [_t("beta", "2026-11-02", "2026-11-02", 200, 0)])
    out = readout.group_budget("alpha=builder-alpha-2611201000,beta=builder-beta-2611201000")
    assert out["ok"] is True and out["max_risk"] == 500 and out["licensed"] is True
    assert readout.group_budget("alpha=builder-beta-2611201000")["ok"] is False
    assert readout.group_budget("alpha=builder-alpha-2611209999")["ok"] is False
