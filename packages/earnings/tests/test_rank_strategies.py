import json
from datetime import date

from cherrypick.earnings import rank_strategies


def _make_entry(name, accepted=True, score_criteria=None):
    score_criteria = score_criteria or {"term_structure": -0.1}
    return {
        "name": name,
        "fetch_criteria_fn": lambda symbol, ed, et, cfg: {"ok": True, "criteria": dict(score_criteria)},
        "apply_tiering_fn": lambda criteria, cfg: {
            "accepted": accepted,
            "reject_reasons": [] if accepted else ["x"],
        },
        "strategy_config_fn": lambda cfg: {},
    }


def test_evaluate_symbol_returns_one_result_per_strategy(monkeypatch):
    monkeypatch.setattr(
        rank_strategies,
        "STRATEGY_REGISTRY",
        [
            _make_entry("strat_a", accepted=True),
            _make_entry("strat_b", accepted=False),
        ],
    )
    monkeypatch.setattr(rank_strategies.scanner, "fetch_avg_volume", lambda *a, **k: 2000000)
    monkeypatch.setattr(
        rank_strategies.scanner, "fetch_iv_rv_ratio", lambda *a, **k: {"ok": True, "iv_rv_ratio": 1.5}
    )
    monkeypatch.setattr(
        rank_strategies.scanner,
        "compute_winrate",
        lambda *a, **k: {"winrate": 0.6, "sample_size": 8, "quarters": [], "realized_move_quarters": []},
    )

    results = rank_strategies.evaluate_symbol("AAPL", date(2026, 7, 7), "After market close", {})
    assert [r["name"] for r in results] == ["strat_a", "strat_b"]
    assert results[0]["accepted"] is True
    assert results[1]["accepted"] is False
    assert results[0]["composite_score"] is not None


def test_evaluate_symbol_records_broker_error_when_fetch_fails(monkeypatch):
    entry = _make_entry("strat_a")
    entry["fetch_criteria_fn"] = lambda symbol, ed, et, cfg: {"ok": False, "error": "no data"}
    monkeypatch.setattr(rank_strategies, "STRATEGY_REGISTRY", [entry])
    monkeypatch.setattr(rank_strategies.scanner, "fetch_avg_volume", lambda *a, **k: None)
    monkeypatch.setattr(rank_strategies.scanner, "fetch_iv_rv_ratio", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(
        rank_strategies.scanner,
        "compute_winrate",
        lambda *a, **k: {"winrate": None, "sample_size": 0, "quarters": [], "realized_move_quarters": []},
    )

    results = rank_strategies.evaluate_symbol("AAPL", date(2026, 7, 7), "After market close", {})
    assert results[0]["broker_data_error"] == "no data"


def test_reverify_symbol_unknown_strategy():
    result = rank_strategies.reverify_symbol(
        "AAPL", "not_a_strategy", date(2026, 7, 7), "After market close", {}
    )
    assert result["ok"] is False
    assert "unknown_strategy" in result["reason"]


def test_reverify_symbol_fetch_failure(monkeypatch):
    entry = _make_entry("strat_a")
    entry["fetch_criteria_fn"] = lambda symbol, ed, et, cfg: {"ok": False, "error": "no chain"}
    monkeypatch.setattr(rank_strategies, "_REGISTRY_BY_NAME", {"strat_a": entry})

    result = rank_strategies.reverify_symbol("AAPL", "strat_a", date(2026, 7, 7), "After market close", {})
    assert result["ok"] is False
    assert result["reason"] == "reverify_failed_no chain"


def test_reverify_symbol_succeeds_when_still_accepted(monkeypatch):
    entry = _make_entry("strat_a", accepted=True)
    monkeypatch.setattr(rank_strategies, "_REGISTRY_BY_NAME", {"strat_a": entry})
    monkeypatch.setattr(rank_strategies.scanner, "fetch_avg_volume", lambda *a, **k: 2000000)
    monkeypatch.setattr(
        rank_strategies.scanner, "fetch_iv_rv_ratio", lambda *a, **k: {"ok": True, "iv_rv_ratio": 1.5}
    )
    monkeypatch.setattr(
        rank_strategies.scanner,
        "compute_winrate",
        lambda *a, **k: {"winrate": 0.6, "sample_size": 8, "quarters": [], "realized_move_quarters": []},
    )

    result = rank_strategies.reverify_symbol("AAPL", "strat_a", date(2026, 7, 7), "After market close", {})
    assert result["ok"] is True
    assert "criteria" in result


def test_save_entry_review_calls_call_db_with_richest_criteria(monkeypatch):
    calls = []
    monkeypatch.setattr(
        rank_strategies, "_call_db", lambda args, paper_mode: calls.append((args, paper_mode))
    )

    symbol_result = {
        "symbol": "AAPL",
        "earnings_timing": "After market close",
        "outcome": "selected",
        "reason": "selected iron_fly (score 0.5000) within this symbol; ranked 1/1",
        "strategies": [
            {"name": "strat_a", "criteria": {"price": 150.0}, "composite_score": 0.2},
            {
                "name": "strat_b",
                "criteria": {"price": 150.0, "winrate": 0.6, "iv_rv_ratio": 1.4},
                "composite_score": 0.5,
            },
        ],
    }
    rank_strategies._save_entry_review("2026-08-07", symbol_result, paper_mode=True)

    assert len(calls) == 1
    args, paper_mode = calls[0]
    assert args[0] == "save_entry_review"
    assert paper_mode is True
    spec = json.loads(args[2])
    assert spec["scan_date"] == "2026-08-07"
    assert spec["symbol"] == "AAPL"
    assert spec["strategy"] == "strat_b"  # richest criteria dict (3 keys) wins over strat_a's (1 key)
    assert spec["winrate"] == 0.6
    assert spec["selected"] is True
    assert spec["composite_score"] == 0.5


def test_save_entry_review_never_raises_on_call_db_failure(monkeypatch):
    def boom(args, paper_mode):
        raise RuntimeError("db subprocess failed")

    monkeypatch.setattr(rank_strategies, "_call_db", boom)
    symbol_result = {
        "symbol": "AAPL",
        "earnings_timing": "After market close",
        "outcome": "rejected_no_viable_strategy",
        "reason": "no edge",
        "strategies": [],
    }
    rank_strategies._save_entry_review("2026-08-07", symbol_result, paper_mode=True)  # must not raise


def test_reverify_symbol_fails_when_rejected(monkeypatch):
    entry = _make_entry("strat_a", accepted=False)
    monkeypatch.setattr(rank_strategies, "_REGISTRY_BY_NAME", {"strat_a": entry})
    monkeypatch.setattr(rank_strategies.scanner, "fetch_avg_volume", lambda *a, **k: 2000000)
    monkeypatch.setattr(
        rank_strategies.scanner, "fetch_iv_rv_ratio", lambda *a, **k: {"ok": True, "iv_rv_ratio": 1.5}
    )
    monkeypatch.setattr(
        rank_strategies.scanner,
        "compute_winrate",
        lambda *a, **k: {"winrate": 0.6, "sample_size": 8, "quarters": [], "realized_move_quarters": []},
    )

    result = rank_strategies.reverify_symbol("AAPL", "strat_a", date(2026, 7, 7), "After market close", {})
    assert result["ok"] is False
    assert result["reason"] == "reverify_failed_x"
