"""The backtest suggestion (#1201): an automation not wholly backtested is told what its blocks read and
whether our stores cover it; one wholly backtested is told nothing. Our own stores are asked first; a
licensed store only where they fall short and the licence allows new work."""

from __future__ import annotations

import copy
import json
import subprocess
from datetime import date

from cherrypick.core import sources as src

from cherrypick.automations import arms, enrolment
from cherrypick.automations.licence import allows_new_work as real_allows_new_work

A = arms.load_arms()
TODAY = date(2026, 11, 16)


def fake_coverage(covered: set[str]):
    calls = []

    def coverage(
        inventory, field, symbol, start, end, *, cadence=None, expiry=None, licensed=False, only=None, **_
    ):
        calls.append({"field": field, "symbol": symbol, "start": start, "end": end, "cadence": cadence,
                      "expiry": expiry, "licensed": licensed})  # fmt: skip
        ok = field in covered
        # The registry's own shape: `best` is a source name, `sources` each candidate with its span.
        part = {"source": f"part-{field}", "first": "2026-10-13", "last": "2026-11-13", "covers": False,
                "why": "starts 2026-10-13, after 2025-11-16"}  # fmt: skip
        return {"covered": ok, "best": f"src-{field}" if ok else None, "sources": [] if ok else [part]}

    coverage.calls = calls
    return coverage


def fresh(name="mine", **changes):
    a = copy.deepcopy(A["long-put-1030"])
    a.pop("register")
    a.update(name=name, **changes)
    return a


def test_a_wholly_backtested_automation_is_told_nothing():
    assert enrolment.suggestion(A["iron-condor-1345"], {}, TODAY, fake_coverage(set())) is None


def test_an_automation_not_backtested_is_told_what_it_reads_and_whether_it_is_covered():
    cov = fake_coverage({"chain", "greeks", "spot"})
    s = enrolment.suggestion(fresh(), {}, TODAY, cov)
    assert s["blocks"] == list(enrolment.BLOCKS) and s["possible"] is True
    assert s["window"] == ["2025-11-13", "2026-11-13"]  # up to Friday, the last finished session
    assert all(c["licensed"] is False and c["symbol"] == "SPX" for c in cov.calls)
    assert {r["field"] for r in s["needs"]} >= {"chain"}
    assert all(r["covered"] and r["source"] == f"src-{r['field']}" for r in s["needs"])
    short = enrolment.suggestion(fresh(), {}, TODAY, fake_coverage(set()))
    assert short["possible"] is False and not any(r["covered"] for r in short["needs"])
    assert short["needs"][0]["nearest"] == {
        "source": "part-chain",
        "first": "2026-10-13",
        "last": "2026-11-13",
        "why": "starts 2026-10-13, after 2025-11-16",
    }


def test_a_changed_block_alone_asks_for_a_backtest():
    arm = copy.deepcopy(A["iron-condor-1345"])
    arm["exit"] = {**arm["exit"], "close_at_dte": 1}
    s = enrolment.suggestion(arm, {}, TODAY, fake_coverage({"chain", "greeks"}))
    assert s["blocks"] == ["exit"]


def test_the_needs_follow_the_blocks():
    zero = fresh(expiry={"kind": "dte", "days": 0})
    assert enrolment.needs(zero)[0] == {"field": "chain", "expiry": "0dte", "cadence": "1m"}
    assert enrolment.needs(fresh(expiry={"kind": "dte", "days": 1}))[0]["expiry"] == "1dte"
    month = fresh(expiry={"kind": "between", "min": 30, "max": 60})
    assert enrolment.needs(month)[0]["expiry"] == "all"
    # daily when the window holds the end-of-day chains' 15:59 tick, by the engine's own window test
    for at, window, cadence in (
        ("15:55", 5, "1d"),
        ("15:59", 1, "1d"),
        ("15:54", 5, "1m"),
        ("16:00", 5, "1m"),
    ):
        close = fresh(entry={"kind": "time", "at": at, "window": window})
        assert enrolment.needs(close)[0]["cadence"] == cadence, at
    by_delta = fresh(
        legs=[{"name": "p", "right": "P", "side": -1, "strike": {"rule": "delta", "delta": -0.3}}]
    )
    assert "greeks" in {n["field"] for n in enrolment.needs(by_delta)}
    by_price = fresh(
        legs=[{"name": "p", "right": "P", "side": -1, "strike": {"rule": "prev_close", "points": 0}}]
    )
    assert "greeks" not in {n["field"] for n in enrolment.needs(by_price)}
    orb = fresh(entry={"kind": "orb", "minutes": 30, "direction": "above", "latest": "13:00"})
    assert {"field": "spot", "cadence": "1m"} in enrolment.needs(orb)


def test_every_need_must_be_covered_for_the_backtest_to_be_possible():
    arm = fresh(legs=[{"name": "p", "right": "P", "side": -1, "strike": {"rule": "delta", "delta": -0.3}}])
    s = enrolment.suggestion(arm, {}, TODAY, fake_coverage({"chain"}))  # the greeks are not covered
    assert [r["covered"] for r in s["needs"]] == [True, False] and s["possible"] is False


def test_the_status_carries_a_suggestion_for_an_automation_not_backtested(tmp_path):
    from cherrypick.automations import config as config_mod
    from cherrypick.automations import db, paper_loop

    config = {"automations": {"mine": fresh()}}
    loaded = config_mod.load_arms(config)
    st = paper_loop.run_status(config, loaded, db.connect(str(tmp_path / "p.db")), cache_path="none")
    assert set(st["backtest_suggestions"]) == {"mine"}  # the shipped eight are wholly backtested
    assert st["backtest_suggestions"]["mine"]["possible"] is False  # no inventory in a test home


def vendor_only_coverage():
    """Our own stores hold nothing; the vendor's (licensed) covers every need."""
    calls = []

    def coverage(
        inventory, field, symbol, start, end, *, cadence=None, expiry=None, licensed=False, only=None, **_
    ):
        calls.append(licensed)
        if licensed:
            return {"covered": True, "best": f"vendor-{field}", "sources": []}
        part = {
            "source": f"own-{field}",
            "first": "2026-10-13",
            "last": "2026-11-13",
            "why": "starts 2026-10-13",
        }
        return {"covered": False, "best": None, "sources": [part]}

    coverage.calls = calls
    return coverage


def test_a_licensed_store_is_never_asked_unless_the_licence_allows_new_work():
    cov = vendor_only_coverage()
    s = enrolment.suggestion(fresh(), {}, TODAY, cov)
    assert s["possible"] is False and s["licensed"] is False
    assert cov.calls and not any(cov.calls)  # only our own stores were asked
    assert all(r["licensed"] is False for r in s["needs"])


def test_where_our_stores_fall_short_a_licensed_store_covers_and_says_so():
    s = enrolment.suggestion(fresh(), {}, TODAY, vendor_only_coverage(), licensed_ok=True)
    assert s["possible"] is True and s["licensed"] is True
    assert all(r["licensed"] and r["source"] == f"vendor-{r['field']}" for r in s["needs"])


def test_our_own_store_is_preferred_and_the_vendor_not_asked_when_it_covers():
    cov = fake_coverage({"chain", "greeks", "spot"})
    s = enrolment.suggestion(fresh(), {}, TODAY, cov, licensed_ok=True)
    assert s["possible"] is True and s["licensed"] is False
    # The last year's needs ask our own stores only; the windows list asks the vendor's spans (the
    # inventory cache, no vendor call) to offer a longer history where it reaches further back.
    assert all(c["licensed"] is False for c in cov.calls if c["start"] == s["window"][0])


def test_the_suggestions_ask_the_licence_once_and_only_when_a_backtest_falls_short(monkeypatch):
    from cherrypick.core import sources

    from cherrypick.automations import paper_loop

    monkeypatch.setattr(sources, "coverage", vendor_only_coverage())
    asked = []

    def licence():
        asked.append(1)
        return True

    out = paper_loop.backtest_suggestions({"a": fresh("a"), "b": fresh("b")}, TODAY, licence=licence)
    assert len(asked) == 1 and all(s["possible"] and s["licensed"] for s in out.values())
    asked.clear()
    shut = paper_loop.backtest_suggestions({"a": fresh("a")}, TODAY, licence=lambda: asked.append(1) or False)
    assert len(asked) == 1 and shut["a"]["possible"] is False and shut["a"]["licensed"] is False
    asked.clear()
    monkeypatch.setattr(sources, "coverage", fake_coverage({"chain", "greeks", "spot"}))
    paper_loop.backtest_suggestions({"a": fresh("a")}, TODAY, licence=lambda: asked.append(1) or True)
    assert asked == []  # our own stores cover it: the licence is not asked


def test_the_licence_reads_as_allowing_new_work_only_on_a_plain_active_answer():
    def answer(stdout, code=0):
        return lambda *a, **k: subprocess.CompletedProcess([], code, stdout=stdout, stderr="")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("python", 60)

    assert real_allows_new_work(run=answer(json.dumps({"license": {"active": True}}))) is True
    assert real_allows_new_work(run=answer(json.dumps({"license": {"active": False}}))) is False
    assert real_allows_new_work(run=answer(json.dumps({"license": {"active": "true"}}))) is False
    assert real_allows_new_work(run=answer(json.dumps({"license": {"active": True}}), code=1)) is False
    assert real_allows_new_work(run=answer("not json")) is False
    assert real_allows_new_work(run=answer(json.dumps({"other": 1}))) is False
    assert real_allows_new_work(run=boom) is False


def _sources(**by):
    """A coverage stand-in answering per (field, expiry): a covering store's row, or none at all."""

    def coverage(
        inventory, field, symbol, start, end, *, cadence=None, expiry=None, licensed=False, only=None, **_
    ):
        hit = by.get(expiry)
        if hit is None:
            return {"covered": False, "best": None, "sources": []}
        return {"covered": True, "best": hit["source"], "sources": [hit]}

    return coverage


def test_a_monthly_rule_asks_for_monthlies_and_the_run_ends_where_the_store_does():
    arm = fresh(expiry={"kind": "between", "min": 45, "max": 60, "monthly": True},
                entry={"kind": "time", "at": "15:55", "window": 5})  # fmt: skip
    assert enrolment.needs(arm)[0] == {"field": "chain", "expiry": "monthly", "cadence": "1d"}
    dolt = {"source": "dolt-chains", "first": "2019-02-09", "through": "2026-11-12"}  # a session behind
    s = enrolment.suggestion(arm, {}, TODAY, _sources(monthly=dolt))
    assert s["possible"] and s["window"][1] == "2026-11-12"
    assert {n["through"] for n in s["needs"]} == {"2026-11-12"}


def test_a_need_no_store_holds_says_so_and_names_monthlies_when_they_would_cover():
    arm = fresh(
        expiry={"kind": "between", "min": 45, "max": 60}, entry={"kind": "time", "at": "15:55", "window": 5}
    )
    dolt = {"source": "dolt-chains", "first": "2019-02-09", "through": "2026-11-13"}
    s = enrolment.suggestion(arm, {}, TODAY, _sources(monthly=dolt))
    chain = s["needs"][0]
    assert not s["possible"] and chain["expiry"] == "all"
    assert chain["why"] == "none of our stores holds all expiries at 1d"
    assert chain["monthly"] == {"source": "dolt-chains", "first": "2019-02-09", "through": "2026-11-13"}
    assert "monthly" not in enrolment.suggestion(arm, {}, TODAY, _sources())["needs"][0]


def test_the_driver_replays_a_chain_only_from_a_store_it_can_read():
    """A same-day chain from a recorder-schema store, a later expiry at the close from end-of-day chains,
    and one inside the session from the full set's 1- and 5-minute stores; never a study's own intraday
    store, and never an intraday store for a close entry."""
    by = src.by_name
    zero = {"field": "chain", "expiry": "0dte", "cadence": "1m"}
    assert enrolment.replays(zero, by("recorded-0dte")) and enrolment.replays(zero, by("vendor-0dte-spx-60s"))
    daily = {"field": "chain", "expiry": "monthly", "cadence": "1d"}
    assert enrolment.replays(daily, by("dolt-chains")) and enrolment.replays(daily, by("vendor-eod-chains"))
    assert not enrolment.replays(daily, by("vendor-mid-spy"))
    intraday = {"field": "chain", "expiry": "1dte", "cadence": enrolment.INTRADAY_CADENCE}
    assert enrolment.replays(intraday, by("vendor-near-spy")) and enrolment.replays(
        intraday, by("vendor-mid-spy")
    )
    assert not enrolment.replays(intraday, by("vendor-intraday-chains"))
    assert not enrolment.replays(intraday, by("dolt-chains"))
    assert enrolment.replays({"field": "spot", "cadence": "1m"}, by("spot-trail"))


def _span(first, last, sessions):
    return {"store": {"first": first, "last": last, "sessions": sessions}}


def test_a_store_the_driver_cannot_read_never_covers_a_need():
    """Over a real coverage call: SPY's middle store would cover a monthly at the close, but the driver
    reads only end-of-day chains for it, so the suggestion takes those or nothing."""
    days = src.trading_days("2020-01-02", "2026-11-13")
    inv = {"sources": {"vendor-mid-spy": _span("2020-01-02", "2026-11-13", days)}}
    arm = fresh(symbol="SPY", expiry={"kind": "between", "min": 45, "max": 60, "monthly": True},
                entry={"kind": "time", "at": "15:55", "window": 5})  # fmt: skip
    s = enrolment.suggestion(arm, inv, TODAY, src.coverage, licensed_ok=True)
    chain = next(n for n in s["needs"] if n["field"] == "chain")
    assert chain["covered"] is False and chain["source"] is None
    inv["sources"]["vendor-eod-chains"] = {
        "symbols": {"SPY": {"first": "2020-01-02", "last": "2026-11-13", "sessions": days}}
    }
    s = enrolment.suggestion(arm, inv, TODAY, src.coverage, licensed_ok=True)
    assert next(n for n in s["needs"] if n["field"] == "chain")["source"] == "vendor-eod-chains"


def test_an_intraday_entry_on_a_later_expiry_reads_the_intraday_stores_or_says_none_holds_it():
    arm = fresh(
        symbol="SPY", expiry={"kind": "dte", "days": 1}, entry={"kind": "time", "at": "10:30", "window": 5}
    )
    days = src.trading_days("2020-01-02", "2026-11-13")
    assert enrolment.needs(arm)[0] == {"field": "chain", "expiry": "1dte", "cadence": "5m"}
    inv = {"sources": {"vendor-near-spy": _span("2020-01-02", "2026-11-13", days)}}
    s = enrolment.suggestion(arm, inv, TODAY, src.coverage, licensed_ok=True)
    chain = next(n for n in s["needs"] if n["field"] == "chain")
    assert s["possible"] is True and chain["source"] == "vendor-near-spy"
    s = enrolment.suggestion(arm, {"sources": {}}, TODAY, src.coverage, licensed_ok=True)
    chain = next(n for n in s["needs"] if n["field"] == "chain")
    assert s["possible"] is False and chain["why"] == enrolment.NO_REPLAY


def _held(**spans):
    """An inventory doc: each source's SPY span, as `inventory.run` writes one."""
    out = {}
    for name, (first, last, density) in spans.items():
        span = {"first": first, "last": last, "sessions": round(src.trading_days(first, last) * density)}
        out[name] = {"symbols": {"SPY": span}, "store": span}
    return {"sources": out}


MONTHLY = {"expiry": {"kind": "between", "min": 45, "max": 60, "monthly": True},
           "entry": {"kind": "time", "at": "15:55", "window": 5}, "symbol": "SPY"}  # fmt: skip


def test_the_windows_offer_the_last_year_and_everything_held_our_own_first():
    inv = _held(
        **{
            "dolt-chains": ("2019-02-11", "2026-11-13", 0.67),
            "stock-history": ("2010-01-04", "2026-11-13", 1.0),
        }
    )
    arm = fresh(**MONTHLY)
    ws = enrolment.windows(arm, inv, TODAY, src.coverage)
    assert [(w["source"], w["start"]) for w in ws] == [
        ("dolt-chains", "2025-11-13"),
        ("dolt-chains", "2019-02-11"),
    ]
    assert ws[1]["thin"] is True and ws[1]["density"] < src.DENSE and ws[0]["licensed"] is False
    assert ws[1]["holds"] == src.by_name("dolt-chains").holds
    assert enrolment.suggestion(arm, inv, TODAY, src.coverage)["windows"] == ws


def test_a_licensed_window_only_where_it_reaches_further_back_and_only_when_allowed():
    inv = _held(**{"dolt-chains": ("2019-02-11", "2026-11-13", 0.67),
                   "vendor-eod-chains": ("2016-01-04", "2026-11-13", 1.0)})  # fmt: skip
    arm = fresh(**MONTHLY)
    assert all(not w["licensed"] for w in enrolment.windows(arm, inv, TODAY, src.coverage))
    ws = enrolment.windows(arm, inv, TODAY, src.coverage, licensed_ok=True)
    lic = [w for w in ws if w["licensed"]]
    assert [(w["source"], w["start"]) for w in lic] == [("vendor-eod-chains", "2016-01-04")]


def test_a_store_that_stopped_early_offers_its_span_and_a_sample_store_is_not_offered():
    inv = _held(
        **{
            "dolt-chains": ("2019-02-11", "2026-10-09", 0.9),
            "vendor-eod-chains": ("2016-01-04", "2026-11-13", 0.01),
        }
    )
    ws = enrolment.windows(fresh(**MONTHLY), inv, TODAY, src.coverage, licensed_ok=True)
    assert {w["end"] for w in ws} == {"2026-10-09"}
    assert all(w["source"] == "dolt-chains" for w in ws)
