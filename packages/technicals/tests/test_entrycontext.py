"""B4: entry context, as of the close before the entry session -- never the entry day's own bar."""

from cherrypick.technicals import entrycontext as ec
from cherrypick.technicals.adjust import AdjustedBar


def _bars(n, *, start_close=100.0, step=1.0, volume=1000.0, last_volume=None):
    out = []
    for i in range(n):
        c = start_close + i * step
        v = last_volume if (last_volume is not None and i == n - 1) else volume
        out.append(AdjustedBar(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", c, c + 1, c - 1, c, v, 1.0))
    return out


SPX = _bars(250)
NAME = _bars(30, volume=1000.0, last_volume=3000.0)


def _of(sym):
    return {"SPX": SPX, "AAPL": NAME}.get(sym, [])


def test_reads_the_close_before_entry_and_never_the_entry_day():
    day = SPX[-1].date  # entering on the last bar's date: that bar must not be read
    rec = {"symbol": "SPX", "session": day, "entry_time": f"{day}T14:05:00"}
    out = ec.context(rec, _of, {})
    prior = SPX[:-1]
    avg50 = sum(b.close for b in prior[-50:]) / 50
    assert out["spx_vs_50d"] == round(prior[-1].close / avg50 - 1, 4)
    assert out["spx_vs_200d"] is not None and out["sector"] is None


def test_name_context_atr_and_relative_volume():
    day = "2026-12-28"  # after every bar
    out = ec.context({"symbol": "AAPL", "session": day, "entry_session": day}, _of, {"AAPL": "Technology"})
    assert out["sector"] == "Technology"
    assert out["rel_volume"] == 3.0
    assert out["atr_pct"] is not None and 1.5 < out["atr_pct"] < 2.5  # true range 2 on a ~128 close


def test_index_roots_read_their_index_and_unknown_names_read_none():
    rec = {"symbol": "XSP", "session": "2026-12-28"}
    assert ec.context(rec, _of, {})["atr_pct"] is not None
    out = ec.context({"symbol": "ZZZZ", "session": "2026-12-28"}, _of, {})
    assert out["atr_pct"] is None and out["rel_volume"] is None


def test_too_little_history_is_none_not_a_guess():
    short = {"SPX": SPX[:40]}
    out = ec.context({"symbol": "SPX", "session": "2026-12-28"}, lambda s: short.get(s, []), {})
    assert out["spx_vs_50d"] is None and out["spx_vs_200d"] is None


def test_holding_period():
    rec = {"entry_session": "2026-10-01", "session": "2026-10-09"}
    assert ec.holding(rec) == {"holding_days": 8, "holding_minutes": None}
    rec = {"entry_time": "2026-10-15T14:03:00", "exit_time": "2026-10-15T15:33:00", "session": "2026-10-15"}
    assert ec.holding(rec) == {"holding_days": 0, "holding_minutes": 90}


def test_annotate_reads_each_symbol_once():
    calls = []

    def bars_of(s):
        calls.append(s)
        return _of(s)

    recs = [{"symbol": "AAPL", "session": "2026-12-28", "net_pnl": 1.0}] * 3
    rows = ec.annotate(recs, bars_of, {})
    assert len(rows) == 3 and rows[0]["net_pnl"] == 1.0
    assert sorted(calls) == ["AAPL", "SPX"]
