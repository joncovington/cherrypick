"""The history studies in scripts/: the modelled bwb backtest, its validation guard, the regime-gate
and CVaR studies built on it, and the implied-variance readers.

Synthetic bars and a synthetic ledger only: no store, no Dolt, no network. The validation guard is
shown to FAIL (a shifted ledger net, a fire the model does not see), because a check that cannot
fire reads as coverage it is not.
"""

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from cherrypick.core import calendar as _cal

from cherrypick.bwb import engine

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bwb_cvar_calibration as cvar_study  # noqa: E402
import bwb_history_backtest as bt  # noqa: E402
import bwb_regime_gate_history as gate  # noqa: E402
import impliedvar_validate as ivv  # noqa: E402
import spy_variance_premium as vp  # noqa: E402


def sessions(start: str, end: str) -> list[str]:
    d, out = date.fromisoformat(start), []
    while d <= date.fromisoformat(end):
        if _cal.is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def flat_context(start: str, end: str, level: float = 6600.0, vol: float = 15.0) -> bt.Context:
    days = sessions(start, end)
    bars = {d: (level, level, level, level, "spx") for d in days}
    v9 = {(date.fromisoformat(days[0]) - timedelta(days=3)).isoformat(): vol}
    v9.update({d: vol for d in days})
    return bt.Context(bars, v9)


def test_a_quiet_week_keeps_the_credit_and_fires_nothing():
    ctx = flat_context("2026-09-11", "2026-09-18")
    pos = bt.simulate(ctx, "2026-09-14")
    assert pos["exp"] == "2026-09-18"
    # the module's own shape: near one increment above the body, far two below
    assert pos["near"] == pos["body"] + 5 and pos["far"] == pos["body"] - 10
    assert pos["body"] < 6600 - 0.5 * pos["em"]
    assert "delta" not in pos and "bounce" not in pos
    expected = pos["credit"] * 100 - bt.ENTRY_FEE - bt.ENTRY_SLIP - engine.settlement_fee(0)
    for arm in bt.ARMS:
        assert pos["net_" + arm] == pytest.approx(expected)


def test_a_selloff_fires_delta_on_the_touch_and_bounce_on_the_recovery():
    ctx = flat_context("2026-09-11", "2026-09-18")
    entry = bt.simulate(ctx, "2026-09-14")
    deep = entry["near"] - 20
    # Tuesday trades down through the near wing and closes there; Wednesday rallies back above spot
    ctx.bars["2026-09-15"] = (6600.0, 6600.0, deep, deep, "spx")
    ctx.bars["2026-09-16"] = (deep, 6600.0, deep, 6600.0, "spx")
    pos = bt.simulate(ctx, "2026-09-14")
    assert pos["delta"]["day"] == "2026-09-15"
    assert pos["bounce"]["day"] == "2026-09-16"
    assert pos["delta"]["credit"] > pos["bounce"]["credit"] > 0


def test_scaling_is_the_same_trade_at_another_index_level():
    small = flat_context("2026-09-11", "2026-09-18", level=1400.0)
    big = flat_context("2026-09-11", "2026-09-18", level=7000.0)
    a = bt.simulate(small, "2026-09-14", scale=7000.0)
    b = bt.simulate(big, "2026-09-14")
    assert a["body"] == b["body"] and a["credit"] == pytest.approx(b["credit"])
    assert a["net_control"] == pytest.approx(b["net_control"])


def make_ledger(
    path: Path, ctx: bt.Context, entries: list[str], *, shift: float = 0.0, fire_delta: bool = False
):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE bwb_positions (entry_session TEXT, arm TEXT, status TEXT, entry_credit REAL,"
        " entry_expected_move REAL, addon_fired_at TEXT, addon_credit REAL, gross_pnl REAL, fees REAL,"
        " addon_missed_at TEXT)"
    )
    for s in entries:
        pos = bt.simulate(ctx, s)
        for arm in bt.ARMS:
            fired = f"{s}T11:00:00-04:00" if (fire_delta and arm == "delta") else None
            conn.execute(
                "INSERT INTO bwb_positions VALUES (?, ?, 'closed', ?, ?, ?, NULL, ?, 0, NULL)",
                (s, arm, pos["credit"], pos["em"], fired, pos["net_" + arm] + shift),
            )
    conn.commit()
    conn.close()


@pytest.fixture
def ledger_ctx():
    ctx = flat_context("2026-08-24", "2026-10-16")
    entries = sessions("2026-08-24", "2026-10-08")
    return ctx, entries


def test_validation_passes_on_a_ledger_the_model_reproduces(tmp_path, ledger_ctx):
    ctx, entries = ledger_ctx
    make_ledger(tmp_path / "paper.db", ctx, entries)
    rep = bt.validate(ctx, str(tmp_path / "paper.db"))
    assert rep["passed"], rep["checks"]
    assert rep["arms"]["delta"]["fire_agreement"] == 1.0


def test_validation_refuses_when_the_nets_drift(tmp_path, ledger_ctx, capsys):
    ctx, entries = ledger_ctx
    make_ledger(tmp_path / "paper.db", ctx, entries, shift=100.0)
    with pytest.raises(SystemExit) as exc:
        bt.require_validation(ctx, str(tmp_path / "paper.db"))
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "control_per_trade_bias" in out and "refusing to report" in out


def test_validation_refuses_when_the_model_misses_the_fires(tmp_path, ledger_ctx):
    ctx, entries = ledger_ctx
    make_ledger(tmp_path / "paper.db", ctx, entries, fire_delta=True)
    rep = bt.validate(ctx, str(tmp_path / "paper.db"))
    assert not rep["passed"]
    failed = {name for name, _, ok in rep["checks"] if not ok}
    assert "delta_fire_agreement" in failed


def test_validation_refuses_a_ledger_too_small_to_claim_anything(tmp_path, ledger_ctx):
    ctx, entries = ledger_ctx
    make_ledger(tmp_path / "paper.db", ctx, entries[:5])
    rep = bt.validate(ctx, str(tmp_path / "paper.db"))
    assert not rep["passed"]


def test_the_gate_reads_the_prior_close_never_the_same_day():
    rows = [{"entry": "2026-09-15"}]
    gate.tag_regime(rows, {"2026-09-14": 20.0, "2026-09-15": 10.0}, {"2026-09-14": 16.0, "2026-09-15": 16.0})
    assert rows[0]["ratio_prior"] == pytest.approx(1.25)
    assert rows[0]["ratio_same_day"] == pytest.approx(10 / 16)


def test_session_nets_key_on_the_settlement_date_like_the_ledger():
    rows = [
        {"entry": "2026-09-14", "exp": "2026-09-18", "net_control": 10.0},
        {"entry": "2026-09-15", "exp": "2026-09-18", "net_control": -40.0},
        {"entry": "2026-09-18", "exp": "2026-09-25", "net_control": 5.0},
    ]
    assert bt.session_nets(rows, "control") == {"2026-09-18": -30.0, "2026-09-25": 5.0}


def test_cvar_calibration_reads_a_capped_tail_at_once():
    # a capped loss every tenth session: every window long enough to hold one reads the cap
    series = [(-1000.0 if i % 10 == 0 else 50.0) for i in range(400)]
    res = cvar_study.calibrate(series, windows=(10, 20, 52))
    assert res["cvar_all"] == -1000.0
    assert res["windows"][20]["within_50pct"] == 1.0
    assert res["min_sessions_meeting_criterion"] == 10


def test_realised_adds_the_dividend_back_on_the_ex_date():
    closes = {"2026-09-17": 100.0, "2026-09-18": 99.0, "2026-09-21": 99.0}
    ss, n = vp.realised(closes, "2026-09-17", "2026-09-21", {"2026-09-18": 1.0})
    assert n == 2 and ss == pytest.approx(0.0)
    ss, _ = vp.realised(closes, "2026-09-17", "2026-09-21")
    assert ss > 0


def test_impliedvar_validation_refuses_a_drifted_reading():
    good = [
        {
            "date": f"2024-01-{i + 1:02d}",
            "ok": True,
            "iv30": 15 + i * 0.5,
            "vix": 15 + i * 0.5 + 0.1,
            "diff": -0.1,
            "complete": False,
        }
        for i in range(20)
    ]
    assert ivv.summarize(good)["passed"]
    bad = [dict(r, iv30=r["iv30"] - 3, diff=-3.1) for r in good]
    with pytest.raises(SystemExit) as exc:
        ivv.require(ivv.summarize(bad))
    assert exc.value.code == 2
