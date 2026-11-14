"""The morning pack's `profile` block: the prior session's Market Profile levels from the profile-bars
store, strictly before the pack's session, built by core.marketprofile. The store is made from the
recorder script's own SCHEMA, so a renamed column fails here."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import marketprofile as mp

from cherrypick.overview import profile, render

ET = ZoneInfo("America/New_York")
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_profile_bars.py"


def _script():
    spec = importlib.util.spec_from_file_location("fetch_profile_bars", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fpb = _script()


def _session(day: dt.date, base: float, drift: float) -> list[dict]:
    """A regular session of 1-minute bars walking from `base` by `drift` a minute."""
    out, px = [], base
    for i in range(390):
        t = dt.datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET) + dt.timedelta(minutes=i)
        nxt = px + drift
        out.append(
            {"t": t.timestamp(), "o": px, "h": max(px, nxt) + 0.5, "l": min(px, nxt) - 0.5, "c": nxt, "v": 10}
        )
        px = nxt
    return out


def _store(tmp_path: Path, bars_by_symbol: dict[str, list[dict]]) -> Path:
    path = tmp_path / "profile_bars.db"
    conn = sqlite3.connect(path)
    conn.execute(fpb.SCHEMA)
    for sym, bars in bars_by_symbol.items():
        for b in bars:
            et = dt.datetime.fromtimestamp(b["t"], ET)
            conn.execute(
                "INSERT INTO profile_bars VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sym,
                    int(b["t"] * 1000),
                    sym,
                    et.date().isoformat(),
                    et.strftime("%H:%M"),
                    b["o"],
                    b["h"],
                    b["l"],
                    b["c"],
                    b["v"],
                    0,
                ),
            )
    conn.commit()
    conn.close()
    return path


D10, D11, D12, D13 = (dt.date(2026, 11, d) for d in (10, 11, 12, 13))


def test_the_prior_session_strictly_before_the_pack(tmp_path):
    spx = _session(D10, 6800, 0.05) + _session(D11, 6900, -0.05) + _session(D12, 6850, 0.02)
    today = _session(D13, 7500, 1.0)  # the pack's own session: never read
    path = _store(tmp_path, {"SPX": spx + today})
    block = profile.block_for("2026-11-13", path)
    s = block["symbols"]["SPX"]
    assert block["reason"] is None and s["session"] == "2026-11-12" and s["basis"] == "prior"
    row = mp.row_size_for("SPX", spx[-1]["c"])
    p = mp.build_profile(spx, row_size=row, bar_seconds=60, session=D12)
    assert (s["poc"], s["val"], s["vah"]) == (p.poc, p.val, round(p.vah + row, 10))
    assert (s["ib_low"], s["ib_high"], s["close"]) == (p.ib_low, p.ib_high, p.close)
    assert s["untested_through"] == "2026-11-12"
    # 11-10's and 11-11's POCs sit hundreds of points from 11-12's range: untested
    want = mp.untested_pocs(spx, sessions=[D10, D11], row_size=row, bar_seconds=60)
    assert s["untested_pocs"] == [{"session": d.isoformat(), "poc": poc} for d, poc in want]
    assert len(s["untested_pocs"]) == 2


def test_a_symbol_with_nothing_recorded_and_a_missing_store_say_why(tmp_path):
    path = _store(tmp_path, {"SPX": _session(D12, 6800, 0.01)})
    block = profile.block_for("2026-11-13", path)
    assert block["symbols"]["NDX"] == {"session": None, "reason": "no_session_recorded"}
    assert profile.block_for("2026-11-13", tmp_path / "absent.db") == {
        "source": "profile_bars",
        "symbols": {},
        "reason": "no_profile_bars",
    }


def test_the_render_lists_each_symbol(tmp_path):
    path = _store(tmp_path, {"SPX": _session(D11, 6900, -0.05) + _session(D12, 6850, 0.02)})
    lines = render._profile(profile.block_for("2026-11-13", path))
    text = "\n".join(lines)
    assert "## Market profile (prior session)" in text
    assert "| SPX | 2026-11-12 |" in text and "| NDX | not measured (no_session_recorded)" in text
    assert "untested through the prior session's close" in text
