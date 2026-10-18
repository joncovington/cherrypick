"""The quote-history replay (`meic.replay`) and its check's rewind (`meic.replay_check.as_of`), on a
synthetic recorded day: one tick a minute whatever the history's cadence; VIX and IV rank from the
session's open and prior closes only; MEIC's own candidate builder over the recorded chain; a replayed
day that enters through `paper.process_symbol` and settles with every exit on the session date."""

import json
import math
import sqlite3
from datetime import datetime

import pytest
from cherrypick.core import home
from cherrypick.core.clock import ET

from cherrypick.meic import paper_loop, replay, replay_check

DAY = "2026-10-16"
SPOT = 6000.0
SIGMA = 30.0  # the day's one-sigma move in points


def _ts(hhmm: str, day: str = DAY) -> float:
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=ET).timestamp()


def _phi(x):
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def _cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _quote(right: str, k: float, s: float) -> tuple[float, float, float]:
    """Bachelier price and delta: enough structure for a 16-delta condor to clear control's floor."""
    d = (s - k) / SIGMA
    if right == "C":
        price, delta = (s - k) * _cdf(d) + SIGMA * _phi(d), _cdf(d)
    else:
        price, delta = (k - s) * _cdf(-d) + SIGMA * _phi(d), -_cdf(-d)
    mid = max(price, 0.05)
    return round(mid - 0.05, 2), round(mid + 0.05, 2), round(delta, 4)


def _history(path, *, every_seconds=60, until="11:00"):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE gex_quote_samples (id INTEGER PRIMARY KEY, symbol TEXT, trade_date TEXT, ts REAL,
                                        expiration TEXT);
        CREATE TABLE gex_quotes (sample_id INTEGER, side INTEGER, strike REAL, bid REAL, ask REAL, mid REAL,
                                 age_s REAL, delta REAL);
        CREATE TABLE gex_spot_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL);
        CREATE TABLE gex_profile_history (symbol TEXT, trade_date TEXT, ts REAL, strike REAL, call_oi REAL,
                                          put_oi REAL, call_vol REAL, put_vol REAL, call_gamma REAL,
                                          put_gamma REAL, call_iv REAL, put_iv REAL);
        CREATE TABLE daily_closes (symbol TEXT, trade_date TEXT, close REAL);
        """
    )
    t, end, sid = _ts("09:30"), _ts(until), 0
    while t <= end:
        sid += 1
        conn.execute("INSERT INTO gex_quote_samples VALUES (?, 'SPX', ?, ?, ?)", (sid, DAY, t, DAY))
        conn.execute("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, ?)", (DAY, t, SPOT))
        for k in range(int(SPOT) - 100, int(SPOT) + 105, 5):
            for side, right in ((0, "P"), (1, "C")):
                bid, ask, delta = _quote(right, k, SPOT)
                conn.execute(
                    "INSERT INTO gex_quotes VALUES (?, ?, ?, ?, ?, ?, 1.0, ?)",
                    (sid, side, k, bid, ask, round((bid + ask) / 2, 3), delta),
                )
        t += every_seconds
    for i, d in enumerate(("2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15")):
        conn.execute("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, 5990)", (d, _ts("10:00", d)))
        conn.execute("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, ?)", (d, _ts("11:00", d), 6010 + i))
        conn.execute("INSERT INTO daily_closes VALUES ('SPX', ?, 6000)", (d,))
    conn.commit()
    conn.close()
    return path


def _cboe(rows: dict[str, list[tuple[str, float, float]]]):
    folder = home.data_dir("market-files") / "cboe"
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in rows.items():
        lines = ["DATE,OPEN,HIGH,LOW,CLOSE"]
        for day, o, c in data:
            mm, dd, yy = day[5:7], day[8:10], day[:4]
            lines.append(f"{mm}/{dd}/{yy},{o},{max(o, c)},{min(o, c)},{c}")
        (folder / f"{name}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


VIX_ROWS = [
    ("2026-10-13", 15.0, 16.0),
    ("2026-10-14", 16.0, 20.0),
    ("2026-10-15", 20.0, 18.0),
    (DAY, 17.0, 30.0),
]


def test_one_tick_a_minute_whatever_the_history_cadence():
    samples = [{"ts": 1000.0 + 15 * i} for i in range(9)]  # 15 s for two minutes
    assert [s["ts"] for s in replay.ticks(samples, 60.0)] == [1000.0, 1060.0, 1120.0]
    assert len(replay.ticks(samples, 15.0)) == 9


def test_vix_and_iv_rank_come_from_the_open_and_prior_closes_only():
    out = replay.vol_inputs(DAY, VIX_ROWS, [(DAY, 13.6, 20.0)])
    assert out["vix"] == 17.0 and out["vix1d_ratio"] == 0.8 and out["iv_rank_source"] == "vix_proxy"
    # rank of the open (17) in [16, 20, 18, 17]: (17 - 16) / (20 - 16); the day's own close (30) unseen
    assert out["iv_rank"] == 0.25 and out["iv_pct"] == pytest.approx(1 / 3, abs=1e-3)
    later_close = [*VIX_ROWS[:-1], (DAY, 17.0, 99.0)]
    assert replay.vol_inputs(DAY, later_close, [])["iv_rank"] == out["iv_rank"]
    assert replay.vol_inputs("2026-10-19", VIX_ROWS, []) == {}


def test_the_snapshot_is_paper_loops_shape_over_the_recorded_chain(tmp_path):
    _cboe({"VIX": VIX_ROWS})
    cfg, profiles = replay.replay_profiles(["control"])
    rday = replay.ReplayDay(DAY, cfg, profiles, history_db=_history(tmp_path / "h.db"))
    sample = rday.recorded.samples[30]  # 10:00
    snap = rday.snapshot(sample, datetime.fromtimestamp(sample["ts"], ET))
    assert snap["ok"] and snap["now_et"] == "10:00" and snap["underlying_price"] == SPOT
    assert snap["vix"] == 17.0 and snap["iv_rank"] == 0.25 and snap["atr_5day"] == pytest.approx(22.0)
    assert snap["gex"] == {"ok": False}  # no recorded profile: refused, never invented
    assert snap["candidates"], "MEIC's own builder found no condor in the recorded chain"
    legs = snap["candidates"][0]
    assert legs["short_put"]["streamer_symbol"].startswith(".SPXW261016P")
    assert set(snap["leg_quotes"]) >= {legs[k]["streamer_symbol"] for k in ("short_put", "long_put")}
    assert rday.snapshot({**sample, "underlying_price": None}, datetime.now(ET)) == {
        "ok": False,
        "reason": "no_spot_price",
    }


def test_the_candidate_menu_is_the_paper_loops_own(monkeypatch):
    """`candidates_from_chain` is what `_build_candidates` runs after the fetch: same input, same menu."""
    rows = []
    for k in range(5900, 6105, 5):
        for right in ("P", "C"):
            bid, ask, delta = _quote(right, k, SPOT)
            rows.append({"strike_price": str(k), "option_type": right, "streamer_symbol": f".X{right}{k}",
                         "bid": bid, "ask": ask, "delta": delta})  # fmt: skip
    monkeypatch.setattr(paper_loop, "_run_json", lambda argv: {"ok": True, "chain": {DAY: rows}})
    built = paper_loop._build_candidates("SPX", SPOT, [5, 10], [0.16], 0.16, DAY)
    assert built == paper_loop.candidates_from_chain(rows, SPOT, [5, 10], [0.16], 0.16)
    assert built[0] and built[2] is None


def test_a_replayed_day_enters_and_settles_on_the_session_date(tmp_path):
    _cboe({"VIX": VIX_ROWS})
    history = _history(tmp_path / "h.db", every_seconds=15, until="10:30")
    out = replay.replay_session(
        DAY, tmp_path / "day.db", arms=["control"], history_db=history, settle_price=SPOT
    )
    assert out["ok"] and out["settled"] and out["tick_seconds"] == 60.0
    assert out["samples"] == 241 and out["ticks"] == 61  # 15 s history, 60 s ticks
    conn = sqlite3.connect(tmp_path / "day.db")
    rows = conn.execute("SELECT arm, status, entry_time, exit_time FROM ic_trades").fetchall()
    conn.close()
    assert rows and {r[0] for r in rows} == {"control"}
    assert all(r[2] >= f"{DAY} 09:45" for r in rows)  # control's window, at the pinned instants
    assert all(r[1] == "expired" and r[3].startswith(DAY) for r in rows)


def test_what_the_replay_refuses(tmp_path):
    _cboe({"VIX": VIX_ROWS})
    history = _history(tmp_path / "h.db", until="09:40")
    with pytest.raises(ValueError, match="advised"):
        replay.replay_profiles(["advised:exp-1"])
    with pytest.raises(ValueError, match="unknown arm"):
        replay.replay_profiles(["no-such-arm"])
    (tmp_path / "taken.db").write_bytes(b"")
    with pytest.raises(FileExistsError):
        replay.replay_session(DAY, tmp_path / "taken.db", history_db=history)
    assert replay.replay_session("2026-10-19", tmp_path / "x.db", history_db=history)["ok"] is False
    _cboe({"VIX": VIX_ROWS[:-1]})
    no_vix = replay.replay_session(DAY, tmp_path / "y.db", history_db=history)
    assert no_vix["ok"] is False and "VIX" in no_vix["error"]


def test_the_check_rewinds_paper_rows_to_the_instant():
    row = {"entry_time": f"{DAY} 10:00:05-04:00", "exit_time": f"{DAY} 16:00:00-04:00", "status": "expired",
           "pnl": 120.0, "fees": 9.0, "settlement_fees": 5.0, "put_max_cost": 1.4, "settle_underlying": SPOT}  # fmt: skip
    assert replay_check.as_of(row, _ts("10:00")) is None
    mid = replay_check.as_of(row, _ts("12:00"))
    assert mid["status"] == "open" and mid["exit_time"] is None and mid["pnl"] is None
    assert mid["put_max_cost"] is None and mid["fees"] == 4.0


def test_the_cli_emits_one_json_document(tmp_path, capsys):
    _cboe({"VIX": VIX_ROWS})
    history = _history(tmp_path / "h.db", until="09:35")
    code = replay.main(["run", "--date", DAY, "--out", str(tmp_path / "c.db"), "--history-db", str(history)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["ok"] and out["settle_price"] is None and out["settled"] is False
