"""The intraday agent's fact pack sees nothing after its own instant (docs/intraday-agent-plan.md).

The guard is truncation invariance, the `core.rangefeatures` rule: the pack at T must be identical
whether the stores also hold everything after T or have it deleted. A pack that leaked one later
reading would let the replay -- and the rule fitted from stored packs -- learn from the future.
Guard mutant `flies-intraday-pack-lookahead` drops the bound and must make this fail.
"""

import shutil
import sqlite3
from datetime import datetime

import pytest

from cherrypick.flies import db, intraday_pack
from cherrypick.flies.clock import ET

SESSION = "2026-09-21"


def _t(hh, mm):
    return datetime(2026, 9, 21, hh, mm, tzinfo=ET).timestamp()


def _iso(hh, mm):
    return datetime(2026, 9, 21, hh, mm, tzinfo=ET).isoformat()


T = _t(12, 30)


def _gex(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE gex_spot_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL);
        CREATE TABLE gex_regime_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL, net_gex REAL,
            zero_gamma REAL, call_wall REAL, put_wall REAL, net_gex_vol REAL, call_volume REAL,
            put_volume REAL, call_wall_volume REAL, put_wall_volume REAL);
        CREATE TABLE market_regime_history (trade_date TEXT, ts REAL, reading TEXT, symbol TEXT, value REAL,
            basis_ts REAL, usable INTEGER, reason TEXT);
        """
    )
    # A trend-up morning to 12:30, then a reversal the pack must never see.
    for i in range(0, 390, 1):
        ts = _t(9, 30) + i * 60
        spot = 7700 + i * 0.25 if ts <= T else 7700 + 45 - (ts - T) / 60 * 0.6
        conn.execute("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, ?)", (SESSION, ts, spot))
        if i % 5 == 0:
            gex = 3.0e9 - i * 1e7 if ts <= T else -2.0e9
            conn.execute(
                "INSERT INTO gex_regime_history VALUES ('SPX', ?, ?, ?, ?, 7690, ?, 7650, ?, ?, ?, ?, ?)",
                (
                    SESSION,
                    ts,
                    spot,
                    gex,
                    7750 if ts <= T else 7720,
                    1e9 if ts <= T else -9e9,
                    1000 * i,
                    600 * i if ts <= T else 99999 * i,
                    50 * i,
                    20 * i,
                ),
            )
        for reading, base in (("vix", 15.0), ("vix1d", 10.0), ("rsp", 180.0), ("spy", 660.0)):
            value = base + (i * 0.001 if ts <= T else 5.0)
            if reading == "spy":
                conn.execute(
                    "INSERT INTO market_regime_history VALUES (?, ?, 'spy_volume', 'SPY', ?, ?, 1, NULL)",
                    (SESSION, ts, 100000.0 * (i + 1) if ts <= T else 9e9, ts),
                )
            conn.execute(
                "INSERT INTO market_regime_history VALUES (?, ?, ?, 'SPX', ?, ?, 1, NULL)",
                (SESSION, ts, reading, value, ts),
            )
    conn.commit()
    return conn


def _ledger(path):
    conn = db.connect(str(path))
    rows = [
        # completed before T, completed after T (an open vertical at T), entered after T, another arm
        ("X-20260921100500000001", "fly", "call", 7720, _iso(10, 5), _iso(11, 0), "control"),
        ("X-20260921113000000002", "fly", "call", 7745, _iso(11, 30), _iso(14, 10), "control"),
        ("X-20260921130000000003", "short_vertical", "put", 7740, _iso(13, 0), None, "control"),
        ("X-20260921114500000004", "short_vertical", "call", 7750, _iso(11, 45), None, "gex"),
    ]
    for pid, kind, side, center, entered, completed, arm in rows:
        conn.execute(
            "INSERT INTO fly_positions (position_id, trade_date, arm, kind, side, center, wing_width, quantity, "
            "credit, entry_time, completed_at, status, gross_pnl) VALUES (?, ?, ?, ?, ?, ?, 5, 1, 2.4, ?, ?, 'settled', -250)",
            (pid, SESSION, arm, kind, side, center, entered, completed),
        )
    conn.commit()
    return conn


def _truncate(gex_path, ledger_path):
    g = sqlite3.connect(gex_path)
    for table in ("gex_spot_history", "gex_regime_history", "market_regime_history"):
        g.execute(f"DELETE FROM {table} WHERE ts > ?", (T,))
    g.commit()
    led = sqlite3.connect(ledger_path)
    t_iso = _iso(12, 30)
    led.execute("DELETE FROM fly_positions WHERE entry_time > ?", (t_iso,))
    led.execute(
        "UPDATE fly_positions SET completed_at = NULL, kind = 'short_vertical' WHERE completed_at > ?",
        (t_iso,),
    )
    led.execute("UPDATE fly_positions SET status = 'open', gross_pnl = NULL")
    led.commit()
    return g, led


@pytest.fixture
def stores(tmp_path):
    gex_path, ledger_path = tmp_path / "gex.db", tmp_path / "paper.db"
    _gex(gex_path).close()
    _ledger(ledger_path).close()
    return gex_path, ledger_path


def _pack(gex_path, ledger_path):
    g = sqlite3.connect(gex_path)
    led = sqlite3.connect(ledger_path)
    try:
        return intraday_pack.build_pack(gex_conn=g, ledger_conn=led, session=SESSION, as_of=T)
    finally:
        g.close()
        led.close()


def test_the_pack_is_identical_with_everything_after_its_instant_deleted(stores, tmp_path):
    gex_path, ledger_path = stores
    full = _pack(gex_path, ledger_path)
    cut_gex, cut_ledger = tmp_path / "gex_cut.db", tmp_path / "paper_cut.db"
    shutil.copy(gex_path, cut_gex)
    shutil.copy(ledger_path, cut_ledger)
    g, led = _truncate(cut_gex, cut_ledger)
    g.close()
    led.close()
    assert full == _pack(cut_gex, cut_ledger)


def test_the_pack_reads_the_session_as_it_stood(stores):
    raw = _pack(*stores)
    pack = intraday_pack.for_model(raw)
    # Times of day only, never the date -- position ids carry it, so they are labels in the pack.
    assert pack["now"] == "12:30" and "2026" not in str(pack) and "0921" not in str(pack)
    assert raw["flies"]["_position_ids"] == {"p1": "X-20260921100500000001", "p2": "X-20260921113000000002"}
    spx = pack["spx"]
    assert spx["open"] == 7700.0 and spx["last"] == pytest.approx(7700 + 180 * 0.25)
    assert spx["trend_bucket"] == "up" and spx["below_high_points"] == 0
    assert pack["gex"]["now"]["call_wall"] == 7750 and pack["gex"]["now"]["net_gex_bn"] > 0
    assert pack["market"]["vix1d"]["change"] == pytest.approx(0.18)
    flies = pack["flies"]
    assert [(p["id"], p["state"]) for p in flies["positions"]] == [
        ("p1", "completed"),
        ("p2", "open_vertical"),
    ]
    assert flies["positions"][1]["spot_past_short_points"] == pytest.approx(spx["last"] - 7745)
    assert "gross_pnl" not in str(pack) and "settled" not in str(pack)


def test_candles_price_action_flow_and_vwap(stores):
    pack = intraday_pack.for_model(_pack(*stores))
    spx = pack["spx"]
    bars = spx["candles_5min"]
    # 09:30 bucket: spot 7700 + 0.25 per minute over minutes 0-4.
    assert bars[0] == {"t": "09:30", "o": 7700.0, "h": 7701.0, "l": 7700.0, "c": 7701.0}
    assert bars[-1]["t"] == "12:30" and bars[-1]["partial"] is True  # the candle at the instant is forming
    assert spx["candle_gaps"] == []
    pa = spx["price_action"]
    assert pa["opening_range"]["high"] == pytest.approx(7700 + 29 * 0.25)
    assert pa["opening_range"]["first_break"] == {"side": "above", "t": "10:00"}
    assert pa["higher_highs"] == pa["structure_bars"] and pa["lower_lows"] == 0  # a steady climb
    flow = pack["flow"]
    assert flow["call_volume"]["last_15min"] == 15000  # 1000 contracts a minute
    assert flow["put_volume"]["session"] == 600 * 180
    vw = pack["spy_vwap"]
    assert vw["volume_session"] == 100000 * 181 and vw["vwap"] < 660.0 + 180 * 0.001
