"""regimecuts.stamp_today: the session's conditions, and each arm's record in them, read only where
the cut can carry it."""

from cherrypick.core import regimecuts as rc


def _cell(bucket, sessions, net, thin=False):
    return {
        "bucket": bucket,
        "sessions": sessions,
        "trades": sessions * 3,
        "net_pnl": net,
        "avg_pnl": round(net / (sessions * 3), 2),
        "win_rate": 0.7,
        "thin": thin,
        "fragile": False,
    }


def _doc(symbol=None):
    return {
        "session": "2026-11-20",
        "symbol": symbol,
        "arms": [
            {
                "arm": "control",
                "dimensions": {
                    "gex": {
                        "underpowered": False,
                        "degenerate": False,
                        "buckets": [_cell("negative", 11, -500.0)],
                    },
                    "trend": {
                        "underpowered": True,
                        "degenerate": False,
                        "buckets": [_cell("down", 8, -90.0)],
                    },
                    "vol_implied": {
                        "underpowered": False,
                        "degenerate": False,
                        "buckets": [_cell("high", 2, 40.0, thin=True)],
                    },
                    "vol_event": {"underpowered": False, "degenerate": True, "buckets": []},
                    "vol_realized": {
                        "underpowered": False,
                        "degenerate": False,
                        "buckets": [_cell("low", 9, 10.0)],
                    },
                },
            }
        ],
    }


READING = {
    "at": "2026-11-20 16:04:27-05:00",
    "symbols": {
        "SPX": {
            "gex": "negative",
            "trend": "down",
            "vol_implied": "high",
            "vol_event": "event",
            "vol_realized": "high",
            "skew": None,
        },
    },
}


def test_each_dimension_reads_only_where_the_cut_can_carry_it():
    doc = rc.stamp_today(_doc(), READING)
    assert doc["today"]["at"] == READING["at"]
    assert doc["today"]["buckets"]["gex"] == "negative"
    t = doc["arms"][0]["today"]
    assert t["gex"]["readable"] is True and t["gex"]["net_pnl"] == -500.0 and t["gex"]["sessions"] == 11
    assert (t["trend"]["readable"], t["trend"]["why"]) == (False, "underpowered")
    assert (t["vol_implied"]["readable"], t["vol_implied"]["why"]) == (False, "thin")
    assert t["vol_implied"]["net_pnl"] == 40.0  # counted, not read
    assert (t["vol_event"]["readable"], t["vol_event"]["why"]) == (False, "degenerate")
    assert (t["vol_realized"]["readable"], t["vol_realized"]["why"]) == (False, "no_trades")
    assert (t["skew"]["readable"], t["skew"]["why"]) == (False, "no_reading")


def test_no_reading_stamps_an_empty_today():
    doc = rc.stamp_today(_doc(), None)
    assert doc["today"] == {"session": "2026-11-20", "at": None, "symbols": {}, "buckets": {}}
    assert doc["arms"][0]["today"] == {}


def test_a_pooled_cut_takes_a_bucket_only_where_every_symbol_agrees():
    reading = {
        "at": "t",
        "symbols": {"SPX": {"gex": "negative", "trend": "up"}, "XSP": {"gex": "negative", "trend": "down"}},
    }
    assert rc.today_buckets(reading, None) == {"gex": "negative", "trend": None}
    assert rc.today_buckets(reading, "XSP") == {"gex": "negative", "trend": "down"}
    assert rc.today_buckets(reading, "ALL") == {"gex": "negative", "trend": None}
    assert rc.today_buckets(reading, "QQQ") == {}
