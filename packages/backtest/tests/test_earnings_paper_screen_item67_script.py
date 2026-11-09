"""Item 67 (packages/backtest/scripts/earnings_paper_screen_item67.py): item 31's replay with its two spread
checks off. Invented chains, bars and records only: no Dolt, no store of real data.

Each guard is shown firing and shown failing, by breaking the invariant it holds. The tests that run
the module's functions need the earnings package and are skipped without it (CI installs per job).
"""

from __future__ import annotations

import importlib.util
import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from cherrypick.core import bs, robustness

from cherrypick.backtest import chainfetch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e67 = _load("earnings_paper_screen_item67", "earnings_paper_screen_item67.py")
e31 = e67.e31


@pytest.fixture
def earnings():
    return pytest.importorskip("cherrypick.earnings.management")


@pytest.fixture(autouse=True)
def few_resamples(monkeypatch):
    monkeypatch.setattr(e31, "RESAMPLES", 400)


# --------------------------------------------------------------------------- the config


def test_the_spread_ceiling_is_off_in_every_strategy_and_nothing_else_moves():
    cfg, base = e67.study_config(), e31.study_config()
    assert e67.spread_checks_off(cfg) and not e67.spread_checks_off(base)
    for name, block in cfg["strategies"].items():
        rest = {k: v for k, v in block.items() if k != "max_bid_ask_spread_pct"}
        theirs = {k: v for k, v in base["strategies"][name].items() if k != "max_bid_ask_spread_pct"}
        assert rest == theirs, name
    assert {k: v for k, v in cfg.items() if k != "strategies"} == {
        k: v for k, v in base.items() if k != "strategies"
    }
    assert base["strategies"]["iron_fly"]["max_bid_ask_spread_pct"] == 0.15, "item 31's config is untouched"


def test_the_off_check_fails_when_one_strategy_keeps_its_ceiling():
    cfg = e67.study_config()
    cfg["strategies"]["iron_condor"]["max_bid_ask_spread_pct"] = 0.15
    assert not e67.spread_checks_off(cfg)


def test_the_spread_ceiling_is_read_by_the_module_only_in_the_liquidity_gate(earnings):
    from cherrypick.earnings import scanner

    crit = {"combined_open_interest": 0, "bid_ask_spread_pct": 0.40, "has_weekly_options": True}
    ours = e67.study_config()["strategies"]["iron_fly"]
    off, on, missing = [], [], []
    scanner.apply_liquidity_gates(crit, ours, off)
    scanner.apply_liquidity_gates(crit, e31.study_config()["strategies"]["iron_fly"], on)
    assert off == [] and on == ["bid_ask_spread_too_wide"]
    scanner.apply_liquidity_gates({**crit, "bid_ask_spread_pct": None}, ours, missing)
    assert missing == ["bid_ask_spread_pct_unverified"], "a missing spread still keeps a name out"


# --------------------------------------------------------------------------- the replay


def _store() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(chainfetch.SCHEMA)
    return conn


def _rows(spot, years, vol, spread):
    out = []
    for k in range(70, 131):
        for right in ("C", "P"):
            mid = bs.price(spot, float(k), years, vol, right, 0.0)
            half = max(spread, 0.03 * mid) / 2
            out.append({"strike": float(k), "right": right, "bid": max(0.0, mid - half), "ask": mid + half,
                        "close": mid, "volume": 100.0})  # fmt: skip
    return out


SYM = "TST"
ENTRY = date(2024, 3, 4)


class World:
    def __init__(self, study_cls, cfg, *, spread, volume=2_000_000.0, close=100.0):
        self.front = e31.front_expiry(ENTRY, "after_close", ENTRY)
        self.back = e31.back_expiry(self.front, e31.weekly_candidates(ENTRY))
        self.conn = _store()
        bars, d = {}, ENTRY - timedelta(days=130)
        while d <= self.front + timedelta(days=40):
            if e31.cal.is_trading_day(d):
                bars[d] = (close, volume)
            d += timedelta(days=1)
        iv = {SYM: [(ENTRY - timedelta(days=i), 1.0, 1.5) for i in range(1, 8)]}
        self.data = e31.MarketData({SYM: bars}, iv)
        self.study = study_cls(self.data, e31.Chains(self.conn), cfg, {})
        yf, yb = (self.front - ENTRY).days / 365.0, (self.back - ENTRY).days / 365.0
        for exp, years, vol in ((self.front, yf, 1.0), (self.back, yb, 0.5)):
            chainfetch.write_chain(
                self.conn, (SYM, ENTRY.isoformat(), exp.isoformat()), _rows(100.0, years, vol, spread)
            )
        for s in e31.sessions_between(e31.cal.next_trading_day(ENTRY), self.front):
            y = max((self.front - s).days / 365.0, 1e-6)
            chainfetch.write_chain(
                self.conn, (SYM, s.isoformat(), self.front.isoformat()), _rows(100.0, y, 0.5, spread)
            )

    def trade(self, strategy="iron_fly"):
        return self.study.trade(strategy, e31.event_row(SYM, ENTRY, "after_close"), "event")


WIDE = 0.9  # dollars across a roughly four-dollar at-the-money mid: over both the 7.5% and the 15% caps


def test_item_31_rejects_a_wide_spread_name_and_this_screen_admits_it(earnings):
    theirs = World(e31.Study, e31.study_config(), spread=WIDE).trade()
    ours = World(e67.PaperScreenStudy, e67.study_config(), spread=WIDE).trade()
    assert theirs["atm_spread_pct"] > 0.15, "the invented spread is wider than both caps"
    assert not theirs["admitted"]
    assert "pit_spread" in theirs["reject"] and "bid_ask_spread_too_wide" in theirs["reject"]
    assert ours["admitted"] and ours["reject"] == [] and ours["ok"]
    assert e67.item31_admits(ours) is False and e67.item31_admits(theirs) is False


def test_a_tight_spread_name_is_admitted_by_both_and_built_the_same(earnings):
    theirs = World(e31.Study, e31.study_config(), spread=0.04).trade()
    ours = World(e67.PaperScreenStudy, e67.study_config(), spread=0.04).trade()
    assert theirs["ok"] and ours["ok"] and e67.item31_admits(ours)
    assert ours["exits"] == theirs["exits"] and ours["credit"] == theirs["credit"]


def test_a_low_priced_name_is_still_rejected_with_the_spread_checks_off(earnings):
    rec = World(e67.PaperScreenStudy, e67.study_config(), spread=WIDE, close=5.0).trade()
    assert not rec["ok"] and "price_below_minimum" in rec["reject"]


def test_a_thinly_traded_name_is_still_rejected_on_dollar_volume(earnings):
    rec = World(e67.PaperScreenStudy, e67.study_config(), spread=WIDE, volume=100.0).trade()
    assert not rec["ok"] and "pit_dollar_volume" in rec["reject"]


def test_the_override_drops_pit_spread_and_keeps_the_dollar_volume_reason(earnings):
    args = (e31.MarketData({SYM: {}}), e31.Chains(_store()))
    ours = e67.PaperScreenStudy(*args, e67.study_config(), {})
    base = e31.Study(*args, e31.study_config(), {})
    crit = {"bid_ask_spread_pct": 0.5}
    assert ours._pit_reasons(SYM, ENTRY, crit) == ["pit_dollar_volume_unverified"]
    assert base._pit_reasons(SYM, ENTRY, crit) == ["pit_dollar_volume_unverified", "pit_spread"]


# --------------------------------------------------------------------------- the admission split


def _rec(spread, admitted=True, strategy="iron_fly", role="event", value=0.0, session=date(2020, 3, 2),
        symbol="AAA", window="design"):  # fmt: skip
    ex = {"net": value * 1000, "shares": {f: value for f in e31.FILL_READINGS}, "reason": "x",
          "hold_sessions": 1, "stale_marks": 0}  # fmt: skip
    return {
        "symbol": symbol, "strategy": strategy, "role": role, "event_report": session.isoformat(),
        "report_date": session.isoformat(), "entry_session": session.isoformat(), "timing": "after_close",
        "year": session.year, "window": window, "time_half": 0 if session <= e31.HALF_END else 1,
        "name_half": robustness.name_half(symbol), "ok": True, "drop": None, "admitted": admitted,
        "reject": [], "g1": None, "g2": True, "em_pct": 0.05, "term": -0.1, "atm_spread_pct": spread,
        "dispersion": 0.05, "exits": {r: dict(ex, shares=dict(ex["shares"])) for r in e31.EXIT_READINGS},
    }  # fmt: skip


def test_item_31_admits_within_both_caps_and_not_a_missing_or_wider_spread():
    assert e67.item31_admits(_rec(0.075))
    assert not e67.item31_admits(_rec(0.0751)), "the study's own cap"
    assert not e67.item31_admits(_rec(0.12)), "inside the module's 15% but over the study's cap"
    assert not e67.item31_admits(_rec(None))
    assert not e67.item31_admits(_rec(0.02, admitted=False)), "another criterion rejected it"


def test_the_admission_split_cuts_events_and_pairs_and_the_halves_add_up():
    events = [_rec(0.02, symbol="A"), _rec(0.10, symbol="B"), _rec(0.20, symbol="C"), _rec(0.05, symbol="D")]
    pairs = [(e, _rec(0.02, role="control", symbol=e["symbol"])) for e in events]
    cut = e67.split_by_admission(events, pairs)
    assert [e["symbol"] for e in cut["item31_admitted"][0]] == ["A", "D"]
    assert [e["symbol"] for e in cut["only_this_screen"][0]] == ["B", "C"]
    assert [e["symbol"] for e, _ in cut["only_this_screen"][1]] == ["B", "C"]
    assert sum(len(v[0]) for v in cut.values()) == len(events)
    assert sum(len(v[1]) for v in cut.values()) == len(pairs)


def test_the_split_means_come_from_the_groups_own_trades():
    sessions = [date(2020, 3, 2) + timedelta(days=7 * i) for i in range(4)]
    recs = []
    for s, spread, value, sym in zip(
        sessions, (0.02, 0.03, 0.10, 0.12), (0.10, 0.10, -0.20, -0.20), "ABCD", strict=True
    ):
        recs += [
            _rec(spread, value=value, session=s, symbol=sym),
            _rec(spread, value=0.0, session=s, symbol=sym, role="control"),
        ]
    got = e67.admission_split(e31.index_records(recs))["iron_fly"]["design"]
    assert got["item31_admitted"]["mean_net"] == pytest.approx(0.10)
    assert got["only_this_screen"]["mean_net"] == pytest.approx(-0.20)
    assert got["only_this_screen"]["pairs"] == 2
    assert got["item31_admitted"]["mean_paired"] == pytest.approx(0.10)


# --------------------------------------------------------------------------- the verdicts


def _names(half: int, n: int) -> list[str]:
    out, i = [], 0
    while len(out) < n:
        s = f"N{i:03d}"
        if robustness.name_half(s) == half:
            out.append(s)
        i += 1
    return out


NAMES = [x for pair in zip(_names(0, 20), _names(1, 20), strict=True) for x in pair]


def _sessions(start, n, step=5):
    out, d = [], start
    while len(out) < n:
        out.append(d)
        for _ in range(step):
            d = e31.cal.next_trading_day(d)
    return out


def _noise(i):
    return ((i * 37) % 11 - 5) / 100.0


def _pairs(n, event_value, strategy="iron_fly", window="design", start=date(2020, 2, 3), step=5, spread=0.02):
    out = []
    for i, s in enumerate(_sessions(start, n, step)):
        sym = NAMES[i % len(NAMES)]
        out.append(
            _rec(spread, strategy=strategy, value=event_value(i), session=s, symbol=sym, window=window)
        )
        out.append(
            _rec(
                spread,
                strategy=strategy,
                role="control",
                value=_noise(i),
                session=s,
                symbol=sym,
                window=window,
            )  # fmt: skip
        )
    return out


HOLD = date(2024, 1, 3)


def _winning(i):
    return 0.08 + _noise(i)


def _losing(i):
    return -0.05 + _noise(i)


def _arm_records(strategy="iron_fly", design=_winning, hold=_winning, n_design=160, n_hold=110):
    return _pairs(n_design, design, strategy) + _pairs(n_hold, hold, strategy, "holdout", HOLD, 4)


def test_a_winning_arm_passes_and_the_arms_are_the_three_ungated_strategies():
    arms = e67.evaluate_arms(e31.index_records(_arm_records()))
    assert list(arms) == ["iron_fly", "iron_condor", "directional_credit_spread"]
    assert arms["iron_fly"]["verdict"] == "PASS" and all(arms["iron_fly"]["checks"].values())
    assert arms["iron_condor"]["verdict"] == "NO VERDICT", "no trades is no verdict, not a pass"


def test_a_losing_arm_fails_and_a_negative_holdout_fails_a_design_pass():
    losing = e67.evaluate_arms(e31.index_records(_arm_records(design=_losing)))
    assert losing["iron_fly"]["verdict"] == "FAIL"
    flipped = e67.evaluate_arms(e31.index_records(_arm_records(hold=_losing)))
    assert flipped["iron_fly"]["design_pass"] and flipped["iron_fly"]["verdict"] == "FAIL"


def test_the_minimum_trades_are_item_31s_and_an_arm_under_them_has_no_verdict():
    assert e31.MIN_TRADES == {"design": 150, "holdout": 100}
    short_design = e67.evaluate_arms(e31.index_records(_arm_records(n_design=149)))
    short_holdout = e67.evaluate_arms(e31.index_records(_arm_records(n_hold=99)))
    assert short_design["iron_fly"]["verdict"] == "NO VERDICT"
    assert short_holdout["iron_fly"]["verdict"] == "NO VERDICT"


def test_holm_runs_across_three_arms_not_nine(monkeypatch):
    seen = []
    real = e67.core_stats.holm
    monkeypatch.setattr(e67.core_stats, "holm", lambda p, alpha: seen.append(len(p)) or real(p, alpha=alpha))
    e67.evaluate_arms(e31.index_records(_arm_records()))
    assert seen == [3, 3]


def test_results_json_carries_a_verdict_per_arm_a_window_and_the_splits():
    wide = date(2020, 4, 6)
    recs = _arm_records() + [
        _rec(0.12, symbol="WIDE", session=wide),
        _rec(0.02, role="control", symbol="WIDE", session=wide),
    ]
    out = e67.summarise_results(recs, set(), None)
    assert set(out["verdict"]) == set(e67.ARMS)
    assert out["verdict"]["iron_fly"]["verdict"] == "PASS" and out["window"] == e31.WINDOW_TEXT
    assert out["run_id"].startswith("item67-")
    assert set(out["admission_split"]["iron_fly"]["design"]) == {"item31_admitted", "only_this_screen"}
    assert out["admission_split"]["iron_fly"]["design"]["only_this_screen"]["events"] == 1
    assert set(out["atm_spread_terciles"]) == set(e67.ARMS)
    assert "Item 67" in e67.report(e31.clean(out)) and out["declared_limits"]


def test_no_replay_correction_is_declared_yet():
    assert e67.REPLAY_CORRECTIONS == ()
    assert math.isinf(e67.study_config()["strategies"]["iron_fly"]["max_bid_ask_spread_pct"])
