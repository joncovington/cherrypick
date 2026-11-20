"""An automation's backtest reference (`automationref`, `read automation-reference`): the usual range of
a trade's net, the entry rate and why sessions were missed, the exit mix, the band of running-net paths
and the computed sample, from one Builder run. Synthetic runs only."""

from __future__ import annotations

import json

import pytest

from cherrypick.backtest import automationref, paths, readout


def _trade(i: int, net: float, kind: str = "settlement", arm: str = "mine") -> dict:
    day = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
    return {"arm": arm, "session": day, "exit_session": day, "exit_minute": 960, "exit_kind": kind,
            "gross": net + 2, "fees": 2, "settlement": 0, "slippage": {"verdict": 0},
            "net": {"verdict": net, "mid": net}}  # fmt: skip


def _doc(
    nets: list[float],
    *,
    reasons: dict | None = None,
    kinds: list[str] | None = None,
    name: str = "builder-mine-2611201000",
) -> dict:
    trades = [_trade(i, n, (kinds or [])[i] if kinds else "settlement") for i, n in enumerate(nets)]
    trades.append(_trade(0, 999.0, arm="other"))  # another automation's trade in the same run
    reasons = reasons or {"entered": len(nets)}
    sessions = sum(reasons.values())
    return {
        "name": name,
        "symbol": "SPX",
        "source": "recorded-0dte",
        "sessions": ["2026-01-01", "2026-06-30"],
        "session_count": sessions,
        "generated_at": "2026-11-20T10:00:00-05:00",
        "summary": {"mine": {"sessions": sessions, "reasons": reasons}},
        "trades": trades,
    }


def test_quantile_interpolates_between_ranks():
    assert automationref.quantile([0.0, 10.0], 0.5) == 5.0
    assert automationref.quantile([1.0, 2.0, 3.0, 4.0, 5.0], 0.25) == 2.0
    assert automationref.quantile([7.0], 0.9) == 7.0


def test_the_usual_range_is_the_runs_own_trade_percentiles():
    nets = [float(x) for x in range(-50, 50)]  # 100 trades, one dollar apart
    ref = automationref.reference(_doc(nets), "mine", resamples=200)
    assert ref["insufficient"] is False
    assert ref["trades"] == 100
    assert ref["percentiles"] == {"p10": -40.1, "p25": -25.25, "p50": -0.5, "p75": 24.25, "p90": 39.1}
    assert ref["mean"] == -0.5
    assert ref["first"] == "2026-01-01" and ref["last"] == "2026-06-30"


def test_entry_rate_and_misses_are_shares_of_the_runs_sessions():
    nets = [10.0] * 40
    ref = automationref.reference(
        _doc(nets, reasons={"entered": 40, "vix_max": 8, "no_quote": 2}), "mine", resamples=50
    )
    assert ref["sessions"] == 50
    assert ref["entry_rate"] == 0.8
    assert ref["missed"] == {"vix_max": 0.16, "no_quote": 0.04}


def test_exit_mix_is_each_kinds_share_of_the_trades():
    kinds = ["settlement"] * 30 + ["stop"] * 9 + ["profit_target"] * 1
    ref = automationref.reference(_doc([5.0] * 40, kinds=kinds), "mine", resamples=50)
    assert ref["exits"] == {"settlement": 0.75, "stop": 0.225, "profit_target": 0.025}
    assert list(ref["exits"]) == ["settlement", "stop", "profit_target"]


def test_the_band_widens_with_trades_and_reproduces():
    nets = [20.0] * 45 + [-150.0] * 5
    a = automationref.reference(_doc(nets), "mine", resamples=500)
    b = automationref.reference(_doc(nets), "mine", resamples=500)
    assert a["band"] == b["band"]
    assert [r["k"] for r in a["band"]] == list(range(1, automationref.HORIZON + 1))
    first, last = a["band"][0], a["band"][-1]
    assert first["p10"] <= first["p50"] <= first["p90"]
    assert last["p90"] - last["p10"] > first["p90"] - first["p10"]
    assert last["p50"] > 0


def test_the_computed_sample_needs_a_positive_mean():
    up = automationref.reference(_doc([30.0, -10.0] * 20), "mine", resamples=50)
    assert up["sample_computed"] is not None and up["sample_computed"] > 0
    down = automationref.reference(_doc([-30.0, 10.0] * 20), "mine", resamples=50)
    assert down["sample_computed"] is None


def test_too_few_trades_is_no_reference():
    ref = automationref.reference(_doc([5.0] * (automationref.MIN_TRADES - 1)), "mine")
    assert ref["insufficient"] is True
    assert ref["trades"] == automationref.MIN_TRADES - 1
    assert "percentiles" not in ref and "band" not in ref


def test_read_finds_the_named_run_and_marks_it_licensed(tmp_path, monkeypatch):
    own, lic = tmp_path / "own", tmp_path / "lic"
    monkeypatch.setattr(paths, "automations_dir", lambda licensed: lic if licensed else own)
    lic.mkdir(parents=True)
    own.mkdir(parents=True)
    run = "builder-mine-2611201000"
    (lic / f"{run}.json").write_text(json.dumps(_doc([5.0] * 40)), encoding="utf-8")
    out = readout.automation_reference("mine", run)
    assert out["ok"] is True and out["licensed"] is True and out["run"] == run
    assert readout.automation_reference("mine", "builder-mine-2601010000")["ok"] is False
    assert readout.automation_reference("../x", run)["ok"] is False


@pytest.mark.parametrize("bad", [None, "x", {"verdict": "x"}])
def test_an_unreadable_net_counts_as_zero(bad):
    doc = _doc([5.0] * 40)
    doc["trades"][0]["net"] = bad
    assert automationref.reference(doc, "mine", resamples=50)["insufficient"] is False
