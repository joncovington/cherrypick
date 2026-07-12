from datetime import date

import rank_strategies


def _make_entry(name, tier="Tier 1", score_criteria=None):
    score_criteria = score_criteria or {"term_structure": -0.1}
    return {
        "name": name,
        "fetch_criteria_fn": lambda symbol, ed, et, cfg: {"ok": True, "criteria": dict(score_criteria)},
        "apply_tiering_fn": lambda criteria, cfg: {"tier": tier, "hard_fail_reasons": [] if tier != "Reject" else ["x"], "near_miss_reasons": []},
        "strategy_config_fn": lambda cfg: {},
    }


def test_evaluate_symbol_returns_one_result_per_strategy(monkeypatch):
    monkeypatch.setattr(rank_strategies, "STRATEGY_REGISTRY", [
        _make_entry("strat_a", tier="Tier 1"),
        _make_entry("strat_b", tier="Reject"),
    ])
    monkeypatch.setattr(rank_strategies.scanner, "fetch_avg_volume", lambda *a, **k: 2000000)
    monkeypatch.setattr(rank_strategies.scanner, "fetch_iv_rv_ratio", lambda *a, **k: {"ok": True, "iv_rv_ratio": 1.5})
    monkeypatch.setattr(rank_strategies.scanner, "compute_winrate", lambda *a, **k: {"winrate": 0.6, "sample_size": 8})

    results = rank_strategies.evaluate_symbol("AAPL", date(2026, 7, 7), "After market close", {})
    assert [r["name"] for r in results] == ["strat_a", "strat_b"]
    assert results[0]["tier"] == "Tier 1"
    assert results[1]["tier"] == "Reject"
    assert results[0]["composite_score"] is not None


def test_evaluate_symbol_records_broker_error_when_fetch_fails(monkeypatch):
    entry = _make_entry("strat_a")
    entry["fetch_criteria_fn"] = lambda symbol, ed, et, cfg: {"ok": False, "error": "no data"}
    monkeypatch.setattr(rank_strategies, "STRATEGY_REGISTRY", [entry])
    monkeypatch.setattr(rank_strategies.scanner, "fetch_avg_volume", lambda *a, **k: None)
    monkeypatch.setattr(rank_strategies.scanner, "fetch_iv_rv_ratio", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(rank_strategies.scanner, "compute_winrate", lambda *a, **k: {"winrate": None, "sample_size": 0})

    results = rank_strategies.evaluate_symbol("AAPL", date(2026, 7, 7), "After market close", {})
    assert results[0]["broker_data_error"] == "no data"


def test_reverify_symbol_unknown_strategy():
    result = rank_strategies.reverify_symbol("AAPL", "not_a_strategy", date(2026, 7, 7), "After market close", {})
    assert result["ok"] is False
    assert "unknown_strategy" in result["reason"]


def test_reverify_symbol_fetch_failure(monkeypatch):
    entry = _make_entry("strat_a")
    entry["fetch_criteria_fn"] = lambda symbol, ed, et, cfg: {"ok": False, "error": "no chain"}
    monkeypatch.setattr(rank_strategies, "_REGISTRY_BY_NAME", {"strat_a": entry})

    result = rank_strategies.reverify_symbol("AAPL", "strat_a", date(2026, 7, 7), "After market close", {})
    assert result["ok"] is False
    assert result["reason"] == "reverify_failed_no chain"


def test_reverify_symbol_succeeds_when_still_tier1(monkeypatch):
    entry = _make_entry("strat_a", tier="Tier 1")
    monkeypatch.setattr(rank_strategies, "_REGISTRY_BY_NAME", {"strat_a": entry})
    monkeypatch.setattr(rank_strategies.scanner, "fetch_avg_volume", lambda *a, **k: 2000000)
    monkeypatch.setattr(rank_strategies.scanner, "fetch_iv_rv_ratio", lambda *a, **k: {"ok": True, "iv_rv_ratio": 1.5})
    monkeypatch.setattr(rank_strategies.scanner, "compute_winrate", lambda *a, **k: {"winrate": 0.6, "sample_size": 8})

    result = rank_strategies.reverify_symbol("AAPL", "strat_a", date(2026, 7, 7), "After market close", {})
    assert result["ok"] is True
    assert result["tier"] == "Tier 1"


def test_reverify_symbol_fails_when_tier_dropped(monkeypatch):
    entry = _make_entry("strat_a", tier="Reject")
    monkeypatch.setattr(rank_strategies, "_REGISTRY_BY_NAME", {"strat_a": entry})
    monkeypatch.setattr(rank_strategies.scanner, "fetch_avg_volume", lambda *a, **k: 2000000)
    monkeypatch.setattr(rank_strategies.scanner, "fetch_iv_rv_ratio", lambda *a, **k: {"ok": True, "iv_rv_ratio": 1.5})
    monkeypatch.setattr(rank_strategies.scanner, "compute_winrate", lambda *a, **k: {"winrate": 0.6, "sample_size": 8})

    result = rank_strategies.reverify_symbol("AAPL", "strat_a", date(2026, 7, 7), "After market close", {})
    assert result["ok"] is False
    assert result["reason"] == "reverify_failed_x"
