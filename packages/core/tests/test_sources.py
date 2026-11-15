"""The source registry: every declaration is well formed, `providing` and `coverage` answer from the
declarations and an inventory, and the inventory reads each kind of store read-only."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cherrypick.core import sources as src
from cherrypick.core.sources import inventory as inv


def test_every_source_is_declared_in_the_registry_words():
    names = [s.name for s in src.SOURCES]
    assert len(names) == len(set(names))
    for s in src.SOURCES:
        assert set(s.fields) <= src.FIELDS, s.name
        assert s.kind in src.KINDS, s.name
        assert s.cadence in src.CADENCE_SECONDS, s.name
        assert set(s.expiries) <= set(src.EXPIRIES), s.name
        assert s.holds and s.writer, s.name
        assert (s.kind == "chain") == bool(s.expiries), f"{s.name}: a chain says which expiries it holds"
        assert s.probe.backend in ("sqlite", "dolt", "csv", "store_cache"), s.name
        if s.probe.backend == "sqlite":
            assert s.probe.table and s.probe.date_col and s.probe.symbol_col, s.name
    # Only the vendor's stores are licensed, and each says how long it is kept.
    assert {s.name for s in src.SOURCES if s.licensed} == {
        s.name for s in src.SOURCES if s.name.startswith("vendor-")
    }
    # chainmeasures' needs are this registry's words, so a measure's data can be looked up.
    from cherrypick.core import chainmeasures

    assert {n for needs in chainmeasures.NEEDS.values() for n in needs} - {"legs"} <= src.FIELDS


def test_providing_filters_by_cadence_expiry_and_licence():
    daily = {s.name for s in src.providing("closes", cadence="1d")}
    assert {"stock-history", "cboe-vol"} <= daily
    assert not {s.name for s in src.providing("closes", cadence="5m")} & {"stock-history", "cboe-vol"}
    assert "vol-curve-5m" in {s.name for s in src.providing("closes", cadence="1h")}, "finer serves coarser"
    # Weeklies: Dolt holds monthlies only, the vendor holds every expiry but is licensed.
    assert src.providing("chain", expiry="weekly") == []
    assert {s.name for s in src.providing("chain", expiry="weekly", licensed=True)} == {
        "vendor-eod-chains",
        "vendor-intraday-chains",
    }
    assert "dolt-chains" in {s.name for s in src.providing("chain", expiry="monthly")}
    assert "recorded-0dte" in {s.name for s in src.providing("chain", expiry="0dte")}
    with pytest.raises(ValueError, match="unknown field"):
        src.providing("price")
    with pytest.raises(ValueError, match="unknown cadence"):
        src.providing("closes", cadence="2m")


def _inventory(**spans):
    return {"sources": {name: {"symbols": syms} for name, syms in spans.items()}}


def _days(first, last):
    # A stand-in calendar: every listed day counts, so the arithmetic is plain.
    from datetime import date

    return (date.fromisoformat(last) - date.fromisoformat(first)).days + 1


def test_coverage_names_the_source_that_covers_and_why_the_others_do_not():
    doc = _inventory(
        **{
            "stock-history": {"SPY": {"first": "2011-01-01", "last": "2026-11-13", "sessions": 5796}},
            "technicals-eod": {"SPY": {"first": "2023-08-30", "last": "2026-11-13", "sessions": 1172}},
            "index-sessions": {"SPY": {"first": "2021-02-16", "last": "2026-11-12", "sessions": 100}},
        }
    )
    out = src.coverage(doc, "closes", "spy", "2019-01-02", "2026-11-13", cadence="1d", days=_days)
    assert out["covered"] and out["best"] == "stock-history" and out["symbol"] == "SPY"
    why = {x["source"]: x["why"] for x in out["sources"]}
    assert why["stock-history"] is None
    assert "starts 2023-08-30, after 2019-01-02" in why["technicals-eod"]
    assert "holds about 5% of the trading days" in why["index-sessions"], "a sparse span never covers"
    assert why["cboe-vol"] == "not inventoried yet"
    assert out["sources"][0]["source"] == "stock-history", "a covering source sorts first"


def test_a_licensed_store_counts_only_when_asked_and_a_store_span_stands_for_its_symbol():
    doc = {
        "sources": {
            "vendor-0dte-spx-60s": {"store": {"first": "2022-05-16", "last": "2026-11-13", "sessions": 1131}},
            "vendor-eod-chains": {"store": {"first": "2016-01-04", "last": "2026-11-13", "sessions": None}},
        }
    }
    assert not src.coverage(doc, "chain", "SPX", "2023-01-03", "2023-12-29", expiry="0dte")["covered"]
    out = src.coverage(
        doc, "chain", "SPX", "2023-01-03", "2023-12-29", expiry="0dte", licensed=True, days=_days
    )
    by = {x["source"]: x for x in out["sources"]}
    assert by["vendor-0dte-spx-60s"]["first"] == "2022-05-16"
    assert by["vendor-0dte-spx-15s"]["why"] == "not inventoried yet"
    assert by["vendor-eod-chains"]["covers"] is False and "not confirmed" in by["vendor-eod-chains"]["why"]
    # A one-symbol store holds nothing for another symbol.
    assert (
        "holds nothing for QQQ"
        in {
            x["source"]: x["why"]
            for x in src.coverage(doc, "chain", "QQQ", "2023-01-03", "2023-12-29", licensed=True)["sources"]
        }["vendor-0dte-spx-60s"]
    )


def test_trading_days_counts_both_ends_on_the_exchange_calendar():
    assert src.trading_days("2026-11-09", "2026-11-13") == 5
    assert src.trading_days("2026-11-14", "2026-11-15") == 0  # a weekend
    assert src.trading_days("2026-11-26", "2026-11-27") == 1  # Thanksgiving, then a session


# --- the inventory ---------------------------------------------------------------------------------


def _source(name, probe, **kw):
    base = {"holds": "x", "kind": "bars", "fields": ("closes",), "cadence": "1d", "writer": "test"}
    return src.Source(name=name, probe=probe, **{**base, **kw})


def test_sqlite_and_csv_probes_read_spans_per_symbol(tmp_path):
    db = tmp_path / "m" / "bars.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE b (symbol TEXT, day TEXT, interval TEXT)")
    rows = [
        ("spy", "2026-01-02", "1d"),
        ("SPY", "2026-01-05", "1d"),
        ("SPY", "2026-01-05", "1d"),
        ("SPY", "2026-01-06", "1h"),
    ]
    conn.executemany("INSERT INTO b VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    s = _source("b", src.Probe("sqlite", "m/bars.db", "b", "day", "symbol", "interval = '1d'"))
    assert inv.probe_sqlite(tmp_path, s) == {
        "symbols": {"SPY": {"first": "2026-01-02", "last": "2026-01-05", "sessions": 2, "years": {"2026": 2}}}
    }
    folder = tmp_path / "cboe"
    folder.mkdir()
    (folder / "VIX.csv").write_text("DATE,VIX\n01/02/1990,17.24\n11/12/2026,15.1\n", encoding="utf-8")
    c = _source("c", src.Probe("csv", "cboe", date_format="mdy"))
    assert inv.probe_csv(tmp_path, c)["symbols"]["VIX"] == {
        "first": "1990-01-02",
        "last": "2026-11-12",
        "sessions": 2,
        "years": {"1990": 1, "2026": 1},
    }
    years = tmp_path / "ust"
    years.mkdir()
    (years / "2025.csv").write_text("Date,1 Mo\n12/31/2025,4.1\n", encoding="utf-8")
    (years / "2026.csv").write_text("Date,1 Mo\n01/02/2026,4.0\n11/13/2026,4.1\n", encoding="utf-8")
    u = _source("u", src.Probe("csv", "ust", date_format="mdy", series="UST"))
    assert inv.probe_csv(tmp_path, u) == {
        "symbols": {
            "UST": {
                "first": "2025-12-31",
                "last": "2026-11-13",
                "sessions": 3,
                "years": {"2025": 1, "2026": 2},
            }
        }
    }


def test_the_store_cache_probe_reads_the_backtests_counts_never_the_store(tmp_path):
    cache = tmp_path / "backtest" / "results" / "store_inventory.json"
    cache.parent.mkdir(parents=True)
    shape = {"tables": {"vendor_sessions": {"rows": 1131, "span": ["2022-05-16", "2026-11-13"]}}}
    cache.write_text(json.dumps({"SPX-60s.db": {"key": "k", "shape": shape}}), encoding="utf-8")
    s = src.by_name("vendor-0dte-spx-60s")
    assert inv.probe_store_cache(tmp_path, s) == {
        "store": {"first": "2022-05-16", "last": "2026-11-13", "sessions": 1131}
    }
    assert (
        "not in the backtest's store inventory"
        in inv.probe_store_cache(tmp_path, src.by_name("vendor-oi"))["error"]
    )


class _FakeDolt:
    """A date-keyed table: answers the two queries the probe asks, and counts them."""

    def __init__(self, rows):
        self.rows = rows
        self.asked = []

    def __call__(self, database):
        return self

    def cursor(self):
        return self

    def close(self):
        pass

    def execute(self, sql, params=()):
        self.asked.append((sql, params))
        if "SELECT DISTINCT `date`" in sql:
            after = params[0] if params else ""
            self._out = [(d,) for d in sorted({d for d, _ in self.rows}) if d > after]
        else:
            self._out = sorted({(s,) for d, s in self.rows if d == params[0]})

    def fetchall(self):
        return self._out


def test_a_dolt_source_is_read_a_date_at_a_time_and_carries_on_from_the_last_run():
    s = src.by_name("dolt-iv")
    dolt = _FakeDolt([("2019-02-09", "QCOM"), ("2019-02-16", "QCOM"), ("2019-02-16", "SPY")])
    first = inv.probe_dolt(s, dolt, None)
    assert first["read_through"] == "2019-02-16"
    assert first["symbols"]["QCOM"] == {
        "first": "2019-02-09",
        "last": "2019-02-16",
        "sessions": 2,
        "years": {"2019": 2},
    }
    dolt.rows.append(("2019-02-23", "SPY"))
    dolt.asked.clear()
    second = inv.probe_dolt(s, dolt, first)
    assert second["symbols"]["SPY"] == {
        "first": "2019-02-16",
        "last": "2019-02-23",
        "sessions": 2,
        "years": {"2019": 2},
    }
    assert second["symbols"]["QCOM"]["sessions"] == 2
    per_date = [p for sql, p in dolt.asked if "WHERE `date` = " in sql]
    assert per_date == [("2019-02-23",)], "only the new date is read"


def test_one_failing_source_is_recorded_and_the_rest_still_run(tmp_path):
    good = tmp_path / "g.db"
    conn = sqlite3.connect(good)
    conn.execute("CREATE TABLE t (symbol TEXT, d TEXT)")
    conn.execute("INSERT INTO t VALUES ('A', '2026-01-02')")
    conn.commit()
    conn.close()
    sources = [
        _source("missing", src.Probe("sqlite", "nope.db", "t", "d", "symbol")),
        _source("broken", src.Probe("sqlite", "g.db", "absent", "d", "symbol")),
        _source("good", src.Probe("sqlite", "g.db", "t", "d", "symbol")),
        _source("dolt", src.Probe("dolt", "options.x", date_col="date", symbol_col="s")),
    ]
    prev = {
        "sources": {"dolt": {"symbols": {"Z": {"first": "2019-01-01", "last": "2019-01-01", "sessions": 1}}}}
    }
    doc = inv.run(sources, root=tmp_path, previous=prev)
    got = doc["sources"]
    assert got["missing"]["error"] == "missing: nope.db"
    assert "no such table" in got["broken"]["error"]
    assert got["good"]["symbols"]["A"]["sessions"] == 1
    assert got["dolt"]["symbols"]["Z"], "with no connection a Dolt source keeps its last entry"


def test_undeclared_names_a_data_file_no_source_declares(tmp_path):
    for rel in (
        "technicals/history.db",  # declared
        "gex/gex_history.db",  # declared
        "market-files/cboe/VIX.csv",  # inside a declared folder
        "meic/paper_trades.db",  # a module's own
        "backtest/vendor/SPX-60s.db",  # the vendor's, declared in backtest.stores
        "market-files/new_bars.db",  # nobody's
        "gex/notes.txt",  # not a data file
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
    assert inv.undeclared(tmp_path) == ["market-files/new_bars.db"]


def test_the_cache_round_trips_and_a_missing_one_reads_empty(tmp_path):
    where = tmp_path / "state" / "sources_inventory.json"
    assert inv.load(where) == {"sources": {}}
    inv.write({"sources": {"x": {}}}, where)
    assert inv.load(where) == {"sources": {"x": {}}}


def test_a_source_sparse_in_its_early_years_still_covers_a_recent_window():
    """Dolt's chains are thin in 2019 and 2020 and full after: density is read inside the window, year
    by year, so a recent window is covered and one reaching the thin years is not."""
    full = src.trading_days("2021-01-01", "2021-12-31")
    span = {
        "first": "2019-02-09",
        "last": "2021-12-31",
        "sessions": 52 + 104 + full,
        "years": {"2019": 52, "2020": 104, "2021": full},
    }
    doc = {"sources": {"dolt-chains": {"symbols": {"QCOM": span}}}}
    recent = src.coverage(doc, "chain", "QCOM", "2021-01-04", "2021-12-31", expiry="monthly")
    assert recent["covered"] and recent["sources"][0]["density"] == 1.0
    early = src.coverage(doc, "chain", "QCOM", "2019-03-01", "2021-12-31", expiry="monthly")
    assert not early["covered"] and "holds about" in early["sources"][0]["why"]
    # A caller that accepts a thinner source (a weekly entry) says so, and the same window is covered.
    loose = src.coverage(doc, "chain", "QCOM", "2019-03-01", "2021-12-31", expiry="monthly", min_density=0.3)
    assert loose["covered"]


def test_the_inventory_keeps_years_only_for_a_sparse_span(tmp_path):
    db = tmp_path / "b.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (symbol TEXT, d TEXT)")
    every = [("DENSE", d) for d in src._trading_list() if "2025-01-01" <= d <= "2026-06-30"]
    thin = [("THIN", d) for d in src._trading_list()[::5] if "2025-01-01" <= d <= "2025-12-31"]
    thin += [("THIN", d) for d in src._trading_list() if "2026-01-01" <= d <= "2026-06-30"]
    conn.executemany("INSERT INTO t VALUES (?,?)", every + thin)
    conn.commit()
    conn.close()
    s = _source("b", src.Probe("sqlite", "b.db", "t", "d", "symbol"))
    got = inv.run([s], root=tmp_path)["sources"]["b"]["symbols"]
    assert "years" not in got["DENSE"]
    assert set(got["THIN"]["years"]) == {"2025", "2026"}
