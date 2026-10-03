"""cherrypick.core.impliedvar: model-free implied variance, held to Cboe's own worked example.

The fixtures are Cboe's published numbers, never this module's output: `fixtures/cboe_vix_example_quotes.csv`
is Appendix 4 of the VIX methodology (v6.0, Feb 2026: every SPX/SPXW bid and ask in the 2022-09-27
10:45:15 ET example) and `fixtures/cboe_vix_example_contributions.csv` is Appendix 5 (each included
strike's midpoint, delta-K and contribution, printed to 1e-10). The headline figures are from Appendix 3.
"""

import csv
import json
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cherrypick.core import impliedvar as iv
from cherrypick.core import streamcache
from cherrypick.core.impliedvar import __main__ as cli

DATA = Path(__file__).parent / "fixtures"
# Appendix 3's inputs: time to expiration in years and the CMT-interpolated rates.
TERMS = {"near": (0.0656088, 0.00031664), "next": (0.0855289, 0.00028797)}


def _quotes(term: str, *, below: float | None = None) -> list[dict]:
    out = []
    with open(DATA / "cboe_vix_example_quotes.csv", newline="") as f:
        for r in csv.DictReader(f):
            k = float(r["strike"])
            if r["term"] != term or (below is not None and k < below):
                continue
            for side in ("call", "put"):
                bid, ask = float(r[f"{side}_bid"]), float(r[f"{side}_ask"])
                out.append({"strike": k, "option_type": side, "bid": bid, "ask": ask})
    return out


def _published_contributions(term: str) -> dict[float, dict]:
    with open(DATA / "cboe_vix_example_contributions.csv", newline="") as f:
        return {float(r["strike"]): r for r in csv.DictReader(f) if r["term"] == term}


def _term(term: str, **kw) -> dict:
    years, rate = TERMS[term]
    return iv.single_term(_quotes(term, **kw), years=years, rate=rate, detail=True)


@pytest.mark.parametrize(
    "term, forward, put_end, call_end, variance",
    [("near", 1962.89996, 1370.0, 2125.0, 0.019233906), ("next", 1962.40006, 1275.0, 2200.0, 0.019423884)],
)
def test_single_term_reproduces_cboes_worked_example(term, forward, put_end, call_end, variance):
    r = _term(term)
    assert r["ok"] and r["complete"]
    assert r["forward"] == pytest.approx(forward, abs=1e-4)
    assert r["k0"] == 1960.0
    assert (r["put_wing"]["last_strike"], r["call_wing"]["last_strike"]) == (put_end, call_end)
    # Appendix 3 prints T rounded to 7 places; that rounding is the residual.
    assert r["variance"] == pytest.approx(variance, abs=2e-8)


@pytest.mark.parametrize("term", ["near", "next"])
def test_every_strike_matches_appendix_5(term):
    """The included strip is exactly Cboe's — no strike missing, none extra — and each strike's
    midpoint, delta-K and contribution agree. This is the test that catches the classic slips: K0
    dropped or priced off one leg, delta-K taken over the listed rather than the included strikes,
    a zero bid that does not reset the two-zero run (the near-term 1400 put's delta-K of 7.5 exists
    because the 1405 put is skipped and the walk continues)."""
    got = {row["strike"]: row for row in _term(term)["contributions"]}
    published = _published_contributions(term)
    assert set(got) == set(published)
    for k, pub in published.items():
        assert got[k]["mid"] == pytest.approx(float(pub["mid"]), abs=1e-9), k
        assert got[k]["dk"] == float(pub["dk"]), k
        assert got[k]["contribution"] == pytest.approx(float(pub["contribution"]), abs=5e-11), k
    assert got[1960.0]["type"] == "put/call"


def test_constant_maturity_reproduces_the_published_vix():
    r = iv.constant_maturity(_term("near"), _term("next"), days=30)
    assert r["ok"] and r["complete"]
    assert r["vol"] / 100 == pytest.approx(0.13927842, abs=1e-7)
    assert round(r["vol"], 2) == 13.93


def test_a_strip_cut_off_by_the_window_says_so_and_reads_low():
    """The stream cache holds a strike window: cutting the near-term puts at 1600 leaves a wing that
    stopped because the quotes ran out, not because the bids ran dry. The result must say so, and is
    a lower bound — the missing tail can only have added variance."""
    full, cut = _term("near"), _term("near", below=1600.0)
    assert cut["ok"] and not cut["complete"]
    assert cut["put_wing"]["complete"] is False and cut["call_wing"]["complete"] is True
    assert cut["variance"] < full["variance"]
    assert not iv.constant_maturity(cut, _term("next"), days=30)["complete"]


def test_constant_maturity_never_extrapolates():
    near, nxt = _term("near"), _term("next")
    assert iv.constant_maturity(near, nxt, days=20)["reason"] == "not_bracketed"
    assert iv.constant_maturity(near, nxt, days=40)["reason"] == "not_bracketed"
    assert iv.constant_maturity(nxt, near, days=30)["reason"] == "not_bracketed"
    assert iv.constant_maturity(near, {"ok": False}, days=30)["reason"] == "term_unavailable"


def test_refusals_name_the_reason():
    q = _quotes("near")
    assert iv.single_term(q, years=0.0, rate=0.0)["reason"] == "nonpositive_time"
    calls_only = [r for r in q if r["option_type"] == "call"]
    assert iv.single_term(calls_only, years=0.05, rate=0.0)["reason"] == "no_parity_strike"
    no_k0_put = [r for r in q if not (r["strike"] == 1960.0 and r["option_type"] == "put")]
    assert iv.single_term(no_k0_put, years=0.0656088, rate=0.0)["reason"] == "k0_unpriced"


def test_years_between_counts_calendar_minutes():
    assert iv.years_between(0.0, 525_600 * 60.0) == 1.0
    assert iv.years_between(0.0, 1_440 * 60.0) == pytest.approx(1 / 365)


# --- the cache reader and the check ---------------------------------------------------------------

EXPIRES = "2026-10-09T20:00:00Z"


def _cache(tmp_path, *, now: float, strips: dict[str, tuple[str, list[dict]]], index: tuple | None = None):
    """A stream cache holding SPXW strips: {expiration: (expires_at, quotes)}; each quote may carry
    `age` (seconds before `now`) and `root`."""
    conn = streamcache.connect(tmp_path / "sc.db")
    for expiration, (expires_at, quotes) in strips.items():
        for i, q in enumerate(quotes):
            root = q.get("root", "SPXW")
            sym = f".{root}{expiration}{q['option_type'][0]}{q['strike']}"
            yymmdd = expiration[2:].replace("-", "")
            occ = f"{root:<6}{yymmdd}{q['option_type'][0].upper()}{i:08d}"
            opt = {
                "streamer_symbol": sym,
                "symbol": occ,
                "strike_price": q["strike"],
                "option_type": q["option_type"][0].upper(),
                "expires_at": expires_at,
                "exercise_style": "European",
                "settlement_type": "PM",
            }
            conn.execute(
                "INSERT INTO stream_chain(streamer_symbol, expiration, underlying_symbol, data_json,"
                " updated_at) VALUES (?,?,?,?,?)",
                (sym, expiration, "SPX", json.dumps(opt), now),
            )
            conn.execute(
                "INSERT INTO stream_quotes(symbol, bid, ask, mid, updated_at) VALUES (?,?,?,?,?)",
                (sym, q["bid"], q["ask"], (q["bid"] + q["ask"]) / 2, now - q.get("age", 0.0)),
            )
    if index:
        conn.execute(
            "INSERT INTO stream_trades(symbol, last, updated_at, event_at) VALUES (?,?,?,?)",
            (index[0], index[1], now, now),
        )
    conn.commit()
    return conn


def test_strip_from_cache_drops_stale_crossed_and_foreign_rows(tmp_path):
    now = time.time()
    quotes = [
        {"strike": 100.0, "option_type": "call", "bid": 1.0, "ask": 1.2},
        {"strike": 100.0, "option_type": "put", "bid": 1.0, "ask": 1.2, "age": 60.0},  # stale
        {"strike": 105.0, "option_type": "call", "bid": 1.5, "ask": 1.0},  # crossed
        {"strike": 110.0, "option_type": "call", "bid": 0.2, "ask": 0.3, "root": "SPX"},  # the AM monthly
    ]
    conn = _cache(tmp_path, now=now, strips={"2026-10-09": (EXPIRES, quotes)})
    got = iv.strip_from_cache(conn, "SPX", "2026-10-09", "SPXW", now_ts=now, max_age_seconds=10.0)
    assert [(q["strike"], q["option_type"]) for q in got["quotes"]] == [(100.0, "call")]
    assert (got["listed"], got["missing"]) == (3, 2)
    assert got["expires_at"] == datetime(2026, 10, 9, 20, tzinfo=UTC).timestamp()
    assert (got["exercise_style"], got["settlement_type"]) == ("European", "PM")


def test_check_agrees_with_an_index_built_from_the_same_strips(tmp_path, capsys):
    """End to end over a cache: Cboe's two strips, placed so 30 days falls between them, against a
    published print equal to the example's VIX. The rate is Cboe's, so this is the whole pipeline —
    expiration pick, reader, both terms, interpolation, verdict."""
    near_years, next_years = TERMS["near"][0], TERMS["next"][0]
    now = 1_790_000_000.0
    near_exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + near_years * 525_600 * 60))
    next_exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + next_years * 525_600 * 60))
    conn = _cache(
        tmp_path,
        now=now,
        strips={"2026-10-21": (near_exp, _quotes("near")), "2026-10-28": (next_exp, _quotes("next"))},
        index=("VIX", 13.93),
    )
    conn.close()
    argv = [
        "check",
        "--db",
        str(tmp_path / "sc.db"),
        "--as-of",
        str(now),
        "--rate",
        "0.0003",
        "--index",
        "VIX",
    ]
    assert cli.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "agrees" and abs(out["diff"]) < 0.05
    assert (out["near"]["expiration"], out["next"]["expiration"]) == ("2026-10-21", "2026-10-28")


def test_check_reports_truncation_instead_of_a_verdict(tmp_path, capsys):
    now = 1_790_000_000.0
    near_years, next_years = TERMS["near"][0], TERMS["next"][0]
    near_exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + near_years * 525_600 * 60))
    next_exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + next_years * 525_600 * 60))
    windowed = [q for q in _quotes("near") if q["strike"] >= 1600.0]
    conn = _cache(
        tmp_path,
        now=now,
        strips={"2026-10-21": (near_exp, windowed), "2026-10-28": (next_exp, _quotes("next"))},
        index=("VIX", 13.93),
    )
    conn.close()
    assert cli.main(["check", "--db", str(tmp_path / "sc.db"), "--as-of", str(now), "--index", "VIX"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "truncated" and out["near"]["put_wing"]["complete"] is False


def test_check_refuses_without_a_bracketing_pair(tmp_path, capsys):
    now = 1_790_000_000.0
    soon = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 7 * 86_400))
    conn = _cache(tmp_path, now=now, strips={"2026-10-09": (soon, _quotes("near"))}, index=("VIX9D", 12.0))
    conn.close()
    assert cli.main(["check", "--db", str(tmp_path / "sc.db"), "--as-of", str(now), "--index", "VIX9D"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["reason"] == "not_bracketed" and out["offered"] == [["2026-10-09", 7.0]]


# --- the declaration ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "today, days, near, nxt",
    [
        ("2026-10-05", 9, "2026-10-13", "2026-10-14"),  # Columbus Day trades; nothing to skip
        ("2026-11-18", 9, "2026-11-25", "2026-11-27"),  # Thanksgiving skipped; its early close counts
        ("2026-10-05", 30, "2026-11-03", "2026-11-04"),
    ],
)
def test_bracket_dates_hold_for_the_whole_session(today, days, near, nxt):
    got = iv.bracket_dates(date.fromisoformat(today), days)
    assert got == (date.fromisoformat(near), date.fromisoformat(nxt))
    # From the open, the near expiry (16:00) is within `days`; from the close, the next is not.
    open_, close = datetime.fromisoformat(f"{today}T09:30"), datetime.fromisoformat(f"{today}T16:00")
    assert (datetime.combine(got[0], close.time()) - open_).total_seconds() <= days * 86_400
    assert (datetime.combine(got[1], close.time()) - close).total_seconds() >= days * 86_400


def test_strip_query_selects_only_the_declared_strip(tmp_path):
    rows = [
        {"strike": 100.0, "option_type": "call", "bid": 1.0, "ask": 1.1},
        {"strike": 100.0, "option_type": "put", "bid": 1.0, "ask": 1.1},
        {"strike": 40.0, "option_type": "put", "bid": 0.0, "ask": 0.05},  # below 0.5x spot
        {"strike": 125.0, "option_type": "call", "bid": 0.0, "ask": 0.05},  # above 1.2x spot
        {"strike": 100.0, "option_type": "call", "bid": 1.0, "ask": 1.1, "root": "SPX"},  # AM monthly
    ]
    conn = _cache(
        tmp_path,
        now=time.time(),
        strips={
            "2099-01-09": (EXPIRES, rows),
            "2099-01-16": (EXPIRES, rows[:1]),
            "2099-01-23": (EXPIRES, rows),
        },
        index=("SPX", 100.0),
    )
    query = iv.strip_query("SPX", "SPXW", (date(2099, 1, 9), date(2099, 1, 16)))
    # The producer runs only a single SELECT and takes every string cell as a symbol.
    assert query.lower().startswith("select") and ";" not in query
    got = sorted(r[0] for r in conn.execute(query))
    assert got == [".SPXW2099-01-09c100.0", ".SPXW2099-01-09p100.0", ".SPXW2099-01-16c100.0"]
    with pytest.raises(ValueError):
        iv.strip_query("SPX'; --", "SPXW", (date(2099, 1, 9),))


def test_declare_writes_and_clears_the_request(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    from cherrypick.core import streamrequests

    db = tmp_path / "sc.db"
    assert cli.main(["declare", "--db", str(db), "--today", "2026-10-05"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["expirations"] == ["2026-10-13", "2026-10-14"]
    (payload,) = streamrequests.read_all()
    assert payload["expirations"] == {"SPX": ["2026-10-13", "2026-10-14"]}
    assert payload["leg_sources"] == [{"db": str(db), "query": out["query"]}]
    # Never growth for the producer's launch snapshot: narrower events, no nearest window.
    assert payload["window_events"] == {"SPX": ["Quote"]} and payload["nearest_window"] == {"SPX": False}
    assert cli.main(["declare", "--clear"]) == 0
    assert json.loads(capsys.readouterr().out)["existed"] is True
    assert streamrequests.read_all() == []
