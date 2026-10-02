"""OCC's daily option volume and the pack's `hot_options` ranking.

The figures the ranking must not get wrong: OCC states both sides of every trade, so contracts are
half its quantities (VIX on 2026-09-30: 1,070,156 in OCC's file, 535,078 at Cboe); a fund or an
index is never ranked as a single-name equity; and the session read is strictly before the pack's.
"""

from __future__ import annotations

import importlib.util
import json
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from cherrypick.overview import facts, occ, render

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_market_files.py"
HEADER = "quantity,underlying,symbol,actype,porc,exchange,actdate"


def _csv(rows: list[tuple], session: str = "09/30/2026") -> str:
    return "\n".join([HEADER] + [",".join(map(str, (*r, session))) + "," for r in rows]) + "\n"


@pytest.fixture(autouse=True)
def _small_files(monkeypatch):
    monkeypatch.setattr(occ, "OCC_MIN_UNDERLYINGS", 1)
    monkeypatch.setattr(occ, "LISTINGS_MIN_ROWS", 1)


def _listings(rows: list[tuple[str, str, str]]) -> str:
    head = "Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|ETF|Round Lot Size|Test Issue"
    body = [f"Y|{sym}|{sym} Inc|N| |{etf}|100|{test}" for sym, etf, test in rows]
    return "\n".join([head, *body, "File Creation Time: 1001202621:33|||||||"]) + "\n"


# --------------------------------------------------------------------------- parsers


def test_every_root_and_exchange_sums_into_its_side_and_account_type():
    parsed = occ.parse_occ(
        _csv(
            [
                (100, "SPX", "SPX", "C", "C", "CBOE"),
                (40, "SPX", "SPXW", "C", "C", "CBOE"),
                (60, "SPX", "2SPX", "M", "C", "CBOE"),
                (10, "SPX", "SPXW", "F", "P", "CBOE"),
                (30, "SPX", "SPXW", "M", "P", "C2"),
                (8, "SPX", "SPXW", "Q", "P", "C2"),  # an account type OCC has not used: kept, as other
            ]
        )
    )
    assert parsed["session"] == "2026-09-30"
    assert parsed["underlyings"]["SPX"] == [140, 0, 60, 0, 0, 10, 30, 8]


def test_a_file_that_is_not_the_volume_report_parses_to_nothing():
    assert occ.parse_occ("Symbol is required.") is None
    assert occ.parse_occ("") is None


def test_rows_from_two_sessions_are_refused_not_merged():
    text = _csv([(2, "AAPL", "AAPL", "C", "C", "CBOE")]) + "2,AAPL,AAPL,M,C,CBOE,09/29/2026,\n"
    assert occ.parse_occ(text) is None


def test_a_truncated_file_is_refused(monkeypatch):
    monkeypatch.setattr(occ, "OCC_MIN_UNDERLYINGS", 2)
    assert occ.parse_occ(_csv([(2, "AAPL", "AAPL", "C", "C", "CBOE")])) is None


def test_the_directory_flags_funds_spells_share_classes_as_occ_does_and_skips_test_issues():
    kinds = occ.parse_listings(_listings([("SPY", "Y", "N"), ("BRK.B", "N", "N"), ("ZZZT", "N", "Y")]))
    assert kinds == {"SPY": "etf", "BRKB": "stock"}
    assert occ.listings_as_of(_listings([("SPY", "Y", "N")])) == "2026-10-01"


def test_a_directory_too_short_to_be_the_real_one_is_empty(monkeypatch):
    monkeypatch.setattr(occ, "LISTINGS_MIN_ROWS", 3)
    assert occ.parse_listings(_listings([("SPY", "Y", "N"), ("AAPL", "N", "N")])) == {}
    assert occ.parse_listings("not|the|file") == {}


# --------------------------------------------------------------------------- ranking


def _sides(calls: int, puts: int, customer_share: float = 0.5) -> list[int]:
    """Sides for `calls` and `puts` contracts: twice each, `customer_share` of them customer."""

    def split(n):
        c = round(2 * n * customer_share)
        return [c, 0, 2 * n - c, 0]

    return split(calls) + split(puts)


def test_contracts_are_half_of_occs_sides():
    # 2026-09-30, VIX: 685,828 call sides and 384,328 put sides in OCC's file; Cboe reports
    # 342,914 calls and 192,164 puts.
    row = occ.hot_options({"VIX": [342_914, 0, 342_914, 0, 192_164, 0, 192_164, 0]}, [], {})["indexes"][0]
    assert (row["contracts"], row["calls"], row["puts"]) == (535_078, 342_914, 192_164)
    assert row["put_call"] == 0.56
    assert row["customer_side_pct"] == 50.0


def test_funds_and_the_index_segment_never_rank_as_single_name_equities():
    day = {
        "SPY": _sides(9_000, 9_000),
        "TLT": _sides(5_000, 1_000),
        "NVDA": _sides(4_000, 2_000),
        "XSP": _sides(3_000, 3_000),  # a cash index the directory does not list
        "HTZ": _sides(500, 500, customer_share=0.05),
    }
    kinds = {"SPY": "etf", "TLT": "etf", "NVDA": "stock", "HTZ": "stock"}
    block = occ.hot_options(day, [], kinds)
    assert [r["symbol"] for r in block["equities"]] == ["NVDA", "HTZ"]
    assert [r["symbol"] for r in block["funds"]] == ["TLT"]
    assert block["unclassified"] == ["XSP"]
    assert [r["symbol"] for r in block["indexes"]] == list(occ.INDEXES)
    assert block["indexes"][1]["rank"] == 1  # SPY, overall
    assert block["indexes"][0] == {"symbol": "VIX", "rank": None, "contracts": 0}  # did not trade
    assert block["equities"][1]["customer_side_pct"] == 5.0
    assert block["total_contracts"] == 18_000 + 6_000 + 6_000 + 6_000 + 1_000


def test_no_directory_leaves_equities_unranked_rather_than_guessed():
    block = occ.hot_options({"NVDA": _sides(10, 10)}, [], None)
    assert block["equities"] is None and block["funds"] is None
    assert block["classification"] == "no_listings_file"


def test_relative_volume_needs_a_baseline_and_counts_an_absent_session_as_zero():
    day = {"NVDA": _sides(150, 150)}
    short = occ.hot_options(day, [{"NVDA": _sides(100, 100)}] * (occ.MIN_BASELINE - 1), {"NVDA": "stock"})
    assert short["equities"][0]["relative_volume"] is None
    baseline = [{"NVDA": _sides(100, 100)}] * (occ.MIN_BASELINE - 1) + [{}]
    row = occ.hot_options(day, baseline, {"NVDA": "stock"})["equities"][0]
    assert row["avg_contracts"] == 160  # (4 x 200 + 0) / 5
    assert row["relative_volume"] == round(300 / 160, 2)


# --------------------------------------------------------------------------- the pack's block


def _store(session: str, underlyings: dict) -> None:
    path = occ.session_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session": session, "underlyings": underlyings}), encoding="utf-8")


def test_the_block_reads_the_newest_session_strictly_before_the_pack():
    _store("2026-09-29", {"NVDA": _sides(1, 1)})
    _store("2026-09-30", {"NVDA": _sides(2, 2)})
    _store("2026-10-01", {"NVDA": _sides(3, 3)})  # the pack's own session: not yet closed at 08:30
    block = occ.block_for("2026-10-01")
    assert block["session"] == "2026-09-30" and block["lag_sessions"] == 0
    assert block["reason"] is None and block["baseline_sessions"] == 1
    assert block["classification"] == "no_listings_file"


def test_a_session_behind_is_counted_and_a_stale_one_refused():
    _store("2026-09-29", {"NVDA": _sides(1, 1)})
    assert occ.block_for("2026-10-01")["lag_sessions"] == 1
    stale = {"session": "2026-09-29", "lag_sessions": 4, "reason": "stale_occ_file"}
    assert occ.block_for("2026-10-06") == stale


def test_no_occ_file_is_reported_and_the_pack_carries_it():
    assert occ.block_for("2026-10-01") == {"session": None, "reason": "no_occ_file"}
    assert facts.build("2026-10-01")["hot_options"]["reason"] == "no_occ_file"


def test_the_render_prints_the_ranking_and_a_reason_when_unmeasured():
    _store("2026-09-30", {"SPY": _sides(9, 9), "NVDA": _sides(4, 2)})
    occ.listings_path().write_text(_listings([("SPY", "Y", "N"), ("NVDA", "N", "N")]), encoding="utf-8")
    lines = render._hot_options(occ.block_for("2026-10-01"))
    text = "\n".join(lines)
    assert "## Hot options (2026-09-30)" in text and "| 2 | NVDA | 6 | 4 | 2 | 0.50 |" in text
    assert "Not measured (no_occ_file)" in "\n".join(
        render._hot_options({"session": None, "reason": "no_occ_file"})
    )


# --------------------------------------------------------------------------- the fetcher


def _module():
    spec = importlib.util.spec_from_file_location("fetch_market_files", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fmf = _module()


@pytest.fixture
def no_pause(monkeypatch):
    monkeypatch.setattr(fmf, "_pause", lambda *a: None)
    monkeypatch.setattr(occ, "OCC_SESSIONS", 3)
    monkeypatch.setattr(occ, "OCC_MIN_BYTES", 30)  # a "no data" reply is 19 bytes


def _fake_occ(published: dict[str, str]):
    calls = []

    def get(url: str) -> bytes:
        ymd = url.split("reportDate=", 1)[1][:8]
        calls.append(ymd)
        return published.get(ymd, "Symbol is required.").encode()

    return get, calls


def test_the_fetcher_lands_published_sessions_and_leaves_todays_for_the_next_run(no_pause):
    get, calls = _fake_occ(
        {
            "20260929": _csv([(2, "AAPL", "AAPL", "C", "C", "CBOE")], "09/29/2026"),
            "20260930": _csv([(4, "AAPL", "AAPL", "C", "C", "CBOE")], "09/30/2026"),
        }
    )
    report = {"problems": []}
    fmf.fetch_occ(report, date(2026, 10, 1), get=get)
    assert report["occ"]["landed"] == ["2026-09-29", "2026-09-30"]
    assert report["occ"]["unpublished"] == ["2026-10-01"] and report["problems"] == []
    assert occ.read_session("2026-09-30")["underlyings"]["AAPL"][0] == 4
    calls.clear()
    fmf.fetch_occ({"problems": []}, date(2026, 10, 1), get=get)
    assert calls == ["20261001"]  # a stored session is never fetched again


def test_a_file_for_another_session_is_refused_and_a_long_unpublished_one_is_a_problem(no_pause):
    get, _ = _fake_occ({"20260930": _csv([(4, "AAPL", "AAPL", "C", "C", "CBOE")], "09/29/2026")})
    report = {"problems": []}
    fmf.fetch_occ(report, date(2026, 10, 1), get=get)
    assert occ.stored_sessions() == []
    assert any("2026-09-29: still not published" in p for p in report["problems"])
    assert any("is for 2026-09-29" in p for p in report["problems"])


def test_a_throttling_reply_ends_the_step(no_pause):
    calls = []

    def get(url):
        calls.append(url)
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)

    report = {"problems": []}
    fmf.fetch_occ(report, date(2026, 10, 1), get=get)
    assert len(calls) == 1 and report["problems"] == ["OCC 2026-09-29: HTTP 429"]


def test_a_shrunken_directory_does_not_replace_the_one_on_disk(no_pause):
    big = _listings([(f"S{i}", "N", "N") for i in range(20)])
    fmf.fetch_listings({"problems": []}, get=lambda url: big.encode())
    report = {"problems": []}
    fmf.fetch_listings(report, get=lambda url: _listings([("SPY", "Y", "N")]).encode())
    assert "not replaced" in report["problems"][0]
    assert len(occ.read_listings()[0]) == 20


def test_occ_is_asked_slowly():
    assert fmf.OCC_PAUSE_RANGE_S[0] >= 5


def test_the_universe_reads_contracts_not_sides():
    _store("2026-09-30", {"AAPL": [1000, 0, 1040, 0, 0, 40, 0, 0], "BRKB": [8, 0, 8, 0, 0, 0, 0, 0]})
    assert occ.contracts_by_session() == {"2026-09-30": {"AAPL": 1040, "BRKB": 8}}
