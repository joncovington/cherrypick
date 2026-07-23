"""Performance rework of the earnings entry scan: thread-local tt cache, the Dolt-cheap pre-gate,
and the parallel scan phase.

The scan was killed at its 30-minute external timeout because it could not evaluate a full earnings
calendar in the window: ~30s/symbol, dominated by live tastytrade calls, over 70 symbols. These tests
pin the three mechanisms that cut that: (1) the read-through tt cache is thread-local so concurrent
symbols don't wipe each other's cache (the race that silently defeated it under the symbol thread
pool); (2) a symbol whose cheap Dolt signals already hard-fail every strategy skips the ~30s of broker
calls entirely; (3) the paper scan evaluates symbols concurrently while keeping all DB writes serial.
"""

import json
import threading
import time
import types

import rank_strategies as rs
import scanner
import strategy_test_runner as r


# --------------------------------------------------------------------------- thread-local tt cache
def _fake_run(counter):
    def run(argv, capture_output, text, timeout):
        counter["n"] += 1
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "argv": argv[2:]}), stderr="")
    return run


def test_tt_cache_memoizes_identical_calls_within_a_thread(monkeypatch):
    counter = {"n": 0}
    monkeypatch.setattr("subprocess.run", _fake_run(counter))
    scanner.begin_tt_cache()
    try:
        a = scanner.call_tt(["get_quote", "--symbol", "AAPL"])
        b = scanner.call_tt(["get_quote", "--symbol", "AAPL"])
    finally:
        scanner.end_tt_cache()
    assert a == b
    assert counter["n"] == 1, "the second identical call must be served from cache, not re-spawned"


def test_tt_cache_is_thread_local(monkeypatch):
    """Each thread scopes its own cache. Under a shared module global, one symbol thread's
    begin_tt_cache() wiped another's entries and its end_tt_cache() disabled caching mid-evaluation,
    silently defeating the cache. With thread-local scoping, two concurrent symbols each memoize
    their own repeated call -> exactly one spawn per symbol."""
    counter = {"n": 0}
    monkeypatch.setattr("subprocess.run", _fake_run(counter))
    barrier = threading.Barrier(2)

    def worker(sym):
        scanner.begin_tt_cache()
        try:
            scanner.call_tt(["get_quote", "--symbol", sym])  # miss -> 1 spawn
            barrier.wait()  # both threads now mid-evaluation, each with an active cache
            scanner.call_tt(["get_quote", "--symbol", sym])  # must hit THIS thread's cache
        finally:
            scanner.end_tt_cache()

    threads = [threading.Thread(target=worker, args=(s,)) for s in ("AAA", "BBB")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert counter["n"] == 2, "2 symbols x 1 spawn each; the repeated calls stayed cached per-thread"


# --------------------------------------------------------------------------- Dolt-cheap pre-gate
_NAMES = [e["name"] for e in rs.STRATEGY_REGISTRY]


def _cfg(volume_floor=1_000_000, ivrv_floor=1.0, winrate_floor=0.4):
    sub = {
        "near_miss_min_avg_volume": volume_floor,
        "near_miss_min_iv_rv_ratio": ivrv_floor,
        "near_miss_min_winrate": winrate_floor,
    }
    return {"strategies": {name: dict(sub) for name in _NAMES}, "winrate_lookback_quarters": 8}


def _stub_dolt(monkeypatch, avg, ivrv, winrate):
    monkeypatch.setattr(scanner, "fetch_avg_volume", lambda s, c: avg)
    monkeypatch.setattr(scanner, "fetch_iv_rv_ratio", lambda s, c: {"ok": ivrv is not None, "iv_rv_ratio": ivrv})
    monkeypatch.setattr(scanner, "compute_winrate", lambda s, c, lb: {"winrate": winrate, "sample_size": 5})


def test_dolt_only_hard_fails_flags_only_present_below_floor():
    sc = {"near_miss_min_avg_volume": 1_000_000, "near_miss_min_iv_rv_ratio": 1.0, "near_miss_min_winrate": 0.4}
    assert rs._dolt_only_hard_fails(sc, 10_000, 2.0, 0.9) == ["avg_volume_below_near_miss"]
    assert rs._dolt_only_hard_fails(sc, None, 2.0, 0.9) == [], "a missing signal is a near-miss, not a hard fail"
    assert rs._dolt_only_hard_fails(sc, 5_000_000, 0.5, 0.9) == ["iv_rv_ratio_below_near_miss"]
    assert rs._dolt_only_hard_fails(sc, 5_000_000, 2.0, 0.2) == ["winrate_below_near_miss"]
    assert rs._dolt_only_hard_fails(sc, 5_000_000, 2.0, 0.9) == []


def test_pre_gate_skips_all_broker_calls_when_every_strategy_dolt_hard_fails(monkeypatch):
    _stub_dolt(monkeypatch, avg=10_000, ivrv=2.0, winrate=0.9)  # volume far below the 1M floor
    called = {"broker": False}
    monkeypatch.setattr(scanner, "begin_tt_cache", lambda: called.__setitem__("broker", True))

    res = rs.evaluate_symbol("AAA", "2026-07-23", "After market close", _cfg())

    assert called["broker"] is False, "must short-circuit before any broker cache/fetch"
    assert len(res) == len(rs.STRATEGY_REGISTRY)
    assert all(x["tier"] == "Reject" for x in res)
    assert all("avg_volume_below_near_miss" in x["hard_fail_reasons"] for x in res)


def test_pre_gate_does_not_skip_on_a_missing_signal(monkeypatch):
    """A None signal is a near-miss, not a hard fail -- it must never trigger the skip, or a name with
    merely-unavailable Dolt data would be dropped without the live evaluation that could pass it."""
    _stub_dolt(monkeypatch, avg=None, ivrv=2.0, winrate=0.9)
    reached = {"broker": False}
    monkeypatch.setattr(rs, "_has_listed_options", lambda s: reached.__setitem__("broker", True) or False)

    rs.evaluate_symbol("AAA", "2026-07-23", "After market close", _cfg())
    assert reached["broker"] is True


def test_pre_gate_does_not_skip_when_signals_pass(monkeypatch):
    _stub_dolt(monkeypatch, avg=5_000_000, ivrv=2.0, winrate=0.9)  # all above floors
    reached = {"broker": False}
    monkeypatch.setattr(rs, "_has_listed_options", lambda s: reached.__setitem__("broker", True) or False)

    rs.evaluate_symbol("AAA", "2026-07-23", "After market close", _cfg())
    assert reached["broker"] is True


# --------------------------------------------------------------------------- parallel scan phase
def _cal(*syms):
    return [{"symbol": s, "date": "2026-07-23", "timing": "After market close"} for s in syms]


def test_parallel_scan_aligns_results_and_classifies_failures(monkeypatch):
    def fake_eval(sym, d, t, cfg):
        if sym == "BBB":
            raise ValueError("boom")
        if sym == "CCC":
            time.sleep(5)  # exceeds the per-symbol timeout below
        return [{"name": "iron_fly", "tier": "Reject"}]

    monkeypatch.setattr(rs, "evaluate_symbol", fake_eval)
    out = r._parallel_scan(_cal("AAA", "BBB", "CCC"), {}, workers=3, symbol_timeout=0.3, budget_seconds=30)

    by = {e["symbol"]: (res, reason) for e, res, reason in out}
    assert len(out) == 3
    assert by["AAA"] == ([{"name": "iron_fly", "tier": "Reject"}], None)
    assert by["BBB"][0] is None and "evaluate_symbol_error" in by["BBB"][1]
    assert by["CCC"][0] is None and "evaluate_symbol_timeout" in by["CCC"][1]


def test_parallel_scan_budget_marks_unfinished_symbols(monkeypatch):
    def slow(sym, d, t, cfg):
        time.sleep(1)
        return [{"name": "x", "tier": "Reject"}]

    monkeypatch.setattr(rs, "evaluate_symbol", slow)
    out = r._parallel_scan(_cal("AAA", "BBB"), {}, workers=2, symbol_timeout=10, budget_seconds=0.2)

    assert [reason for _, _, reason in out] == ["entry_scan_budget_exceeded"] * 2
