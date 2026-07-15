"""Unit tests for paper_practice.py pure helpers — no network / no 0DTESPX token required.
Covers the OCC instrument formatting, candidate building from a 0DTESPX chain snapshot, and the
position-reconciliation / per-side stop-cost logic that backs fill confirmation and stops."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import paper_practice as pp

# ── OCC / date formatting ────────────────────────────────────────────────────

def test_occ_format_put_and_call():
    assert pp.occ(7450, "P", "260709") == "SPXW  260709P07450000"
    assert pp.occ(7545, "C", "260709") == "SPXW  260709C07545000"


def test_occ_pads_strike_to_eight_digits():
    # strike * 1000, zero-padded to 8 — a 3-digit-ish strike still pads correctly
    assert pp.occ(600, "C", "260709") == "SPXW  260709C00600000"


def test_yymmdd_of():
    assert pp.yymmdd_of("2026-07-09") == "260709"


def test_vix_band_iv_rank_monotonic_and_bounded():
    # documented VIX->pseudo-rank bands (ToS-safe, no historical reads)
    assert pp.vix_band_iv_rank(11) == 0.10
    assert pp.vix_band_iv_rank(15) == 0.25
    assert pp.vix_band_iv_rank(16) == 0.40    # 15 < 16 <= 18
    assert pp.vix_band_iv_rank(40) == 0.95    # above the top band
    assert pp.vix_band_iv_rank(None) == 0.40  # neutral default when VIX unavailable
    # monotonic non-decreasing in VIX
    ranks = [pp.vix_band_iv_rank(v) for v in range(10, 45, 2)]
    assert ranks == sorted(ranks)


def test_session_quality_bands():
    assert pp.session_quality(9 * 60 + 40) == "open_volatile"  # 09:40
    assert pp.session_quality(11 * 60) == "prime"              # 11:00
    assert pp.session_quality(13 * 60) == "midday"             # 13:00
    assert pp.session_quality(14 * 60 + 30) == "afternoon"     # 14:30
    assert pp.session_quality(15 * 60 + 30) == "late"          # 15:30


# ── build_candidates ─────────────────────────────────────────────────────────

def _snap():
    # spot ~7500; ~0.15-delta shorts at 7450P / 7540C, with wings for widths 5 and 10.
    return {
        "put_7450": {"bid": 4.1, "ask": 4.3, "delta": 0.158},
        "put_7445": {"bid": 3.5, "ask": 3.6, "delta": 0.137},
        "put_7440": {"bid": 3.1, "ask": 3.2, "delta": 0.120},
        "call_7540": {"bid": 2.7, "ask": 2.8, "delta": 0.144},
        "call_7545": {"bid": 1.9, "ask": 1.95, "delta": 0.112},
        "call_7550": {"bid": 1.4, "ask": 1.5, "delta": 0.095},
        "put_9999": None,  # unlisted strike (null) must be skipped
    }


def test_build_candidates_picks_near_delta_shorts_and_wings():
    cands = pp.build_candidates(_snap(), spot=7500, widths=[5, 10], target_delta=0.15, yymmdd="260709")
    by_w = {c["wing_width"]: c for c in cands}
    assert set(by_w) == {5, 10}
    c5 = by_w[5]
    assert c5["short_put"]["strike"] == 7450 and c5["long_put"]["strike"] == 7445
    assert c5["short_call"]["strike"] == 7540 and c5["long_call"]["strike"] == 7545
    # streamer_symbol is the OCC instrument, so the chosen candidate maps straight to an order
    assert c5["short_put"]["streamer_symbol"] == "SPXW  260709P07450000"
    c10 = by_w[10]
    assert c10["long_put"]["strike"] == 7440 and c10["long_call"]["strike"] == 7550


def test_build_candidates_skips_width_without_wings():
    # only the 5-wide wings exist (7445P / 7545C); the 10-wide long legs are absent
    snap = {
        "put_7450": {"bid": 4.1, "ask": 4.3, "delta": 0.158},
        "put_7445": {"bid": 3.5, "ask": 3.6, "delta": 0.137},
        "call_7540": {"bid": 2.7, "ask": 2.8, "delta": 0.144},
        "call_7545": {"bid": 1.9, "ask": 1.95, "delta": 0.112},
    }
    cands = pp.build_candidates(snap, spot=7500, widths=[5, 10], target_delta=0.15, yymmdd="260709")
    assert [c["wing_width"] for c in cands] == [5]


def test_build_candidates_empty_when_no_shorts():
    assert pp.build_candidates({}, spot=7500, widths=[5], target_delta=0.15, yymmdd="260709") == []


# ── mark_map / reconcile / side_cost ─────────────────────────────────────────

def _ic():
    return {"legs": {"sp": "SPXW  260709P07450000", "lp": "SPXW  260709P07445000",
                     "sc": "SPXW  260709C07540000", "lc": "SPXW  260709C07545000",
                     "sp_k": 7450.0, "sc_k": 7540.0},
            "net_credit": 1.40, "call": "open", "put": "open", "retry": {"call": 0, "put": 0}}


def _all_marks():
    return {"SPXW  260709P07450000": 0.90, "SPXW  260709P07445000": 0.40,
            "SPXW  260709C07540000": 1.60, "SPXW  260709C07545000": 0.30}


def test_mark_map_from_positions():
    positions = [{"instrument": "SPXW  260709P07450000", "price": "0.90", "quantity": "1"},
                 {"instrument": "SPXW  260709C07540000", "price": "1.60", "quantity": "1"},
                 {"instrument": "BAD", "price": None, "quantity": "1"}]
    m = pp.mark_map(positions)
    assert m == {"SPXW  260709P07450000": 0.90, "SPXW  260709C07540000": 1.60}


def test_mark_map_excludes_zero_quantity_closed_legs():
    # 0DTESPX keeps closed legs in the list with quantity "0" / direction "zero" — they must be
    # excluded so a closed side reconciles to gone rather than looking perpetually open.
    positions = [{"instrument": "SPXW  260709P07450000", "price": "0.90", "quantity": "1", "direction": "short"},
                 {"instrument": "SPXW  260709C07540000", "price": "0.00", "quantity": "0", "direction": "zero"}]
    m = pp.mark_map(positions)
    assert m == {"SPXW  260709P07450000": 0.90}
    # and a whole side of zero-qty legs reconciles closed
    ic = _ic()
    pp.reconcile(ic, pp.mark_map([
        {"instrument": ic["legs"]["sc"], "price": "0", "quantity": "0"},
        {"instrument": ic["legs"]["lc"], "price": "0", "quantity": "0"},
        {"instrument": ic["legs"]["sp"], "price": "0.9", "quantity": "1"},
        {"instrument": ic["legs"]["lp"], "price": "0.4", "quantity": "1"},
    ]))
    assert ic["call"] == "closed" and ic["put"] == "open"


def test_side_cost_is_short_minus_long():
    ic, marks = _ic(), _all_marks()
    assert pp.side_cost(ic, "call", marks) == 1.60 - 0.30
    assert pp.side_cost(ic, "put", marks) == 0.90 - 0.40


def test_side_cost_none_when_leg_unmarked():
    ic = _ic()
    partial = {"SPXW  260709C07540000": 1.60}  # missing the long call
    assert pp.side_cost(ic, "call", partial) is None


def test_reconcile_closes_side_when_both_legs_gone():
    ic = _ic()
    # call legs have left the book (filled close) -> call confirmed closed; put legs remain
    marks = {"SPXW  260709P07450000": 0.90, "SPXW  260709P07445000": 0.40}
    pp.reconcile(ic, marks)
    assert ic["call"] == "closed"
    assert ic["put"] == "open"


def test_reconcile_keeps_side_open_while_legs_present():
    ic = _ic()
    pp.reconcile(ic, _all_marks())
    assert ic["call"] == "open" and ic["put"] == "open"


def test_stop_triggers_when_side_cost_reaches_ratio():
    # mirrors the driver's per-side rule: cost >= stop_trigger * net_credit
    ic, marks = _ic(), _all_marks()
    stop_trigger = 0.90
    call_cost = pp.side_cost(ic, "call", marks)  # 1.30
    assert call_cost >= stop_trigger * ic["net_credit"]   # 1.30 >= 0.90*1.40=1.26 -> stop
    put_cost = pp.side_cost(ic, "put", marks)             # 0.50
    assert not (put_cost >= stop_trigger * ic["net_credit"])


# ── SPX-eligible profile selection ───────────────────────────────────────────

def test_spx_eligible_profiles_includes_ladder_and_spx_cells_only():
    names = pp.spx_eligible_profiles()
    # ladder tiers trade all base symbols (SPX included) and the SPX-pinned cells qualify
    assert {"conservative", "moderate", "aggressive", "very-aggressive",
            "large-spx", "explore-spx-tightcredit"} <= set(names)
    # XSP/QQQ/IWM-pinned cells are excluded
    for excluded in ("small-xsp", "small-iwm", "medium-qqq", "medium-xsp-wide", "explore-xsp-loosecredit"):
        assert excluded not in names


# ── settlement / per-IC P&L accounting ───────────────────────────────────────

def _finalizable_ic(**over):
    ic = {"legs": {"sp_k": 7450.0, "sc_k": 7540.0}, "net_credit": 1.40, "open_fee": 6.88, "wing": 5,
          "exit": {"call": None, "put": None}, "exit_fee": {"call": 0.0, "put": 0.0},
          "exit_reason": {"call": None, "put": None}}
    ic.update(over)
    return ic


def test_finalize_ic_both_sides_expire_otm_keeps_full_credit():
    # spot 7500 between the shorts (7450 put / 7540 call) -> both settle worthless
    ic = _finalizable_ic()
    pnl, fees, status, reason = pp._finalize_ic(ic, spot_close=7500.0)
    assert pnl == 140.0                      # 1.40 credit * 100, nothing paid to exit
    assert fees == 6.88                       # open fee only (expiry has no fee)
    assert status == "expired" and reason == "expired_settlement"


def test_finalize_ic_stopped_put_plus_expired_call():
    # put side was stopped intraday for a 1.50 debit (+0.72 fee); call left to expire OTM
    ic = _finalizable_ic(exit={"call": None, "put": 1.50}, exit_fee={"call": 0.0, "put": 0.72},
                         exit_reason={"call": None, "put": "per_side_stop"})
    pnl, fees, status, reason = pp._finalize_ic(ic, spot_close=7500.0)
    assert pnl == round((1.40 - 0.0 - 1.50) * 100, 2)   # -10.0
    assert fees == round(6.88 + 0.0 + 0.72, 2)          # 7.60
    assert status == "stopped"                          # any stopped side -> stopped
    assert "per_side_stop" in reason and "expired_settlement" in reason


def test_days_in_range_intersects_available_sessions():
    available = {"2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10",
                 "2026-07-13", "2026-06-30"}
    # weekend 07-11/07-12 aren't sessions and are excluded; bounds inclusive
    assert pp._days_in_range("2026-07-07", "2026-07-10", available) == \
        ["2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10"]
    # a range with no available sessions -> empty
    assert pp._days_in_range("2026-07-11", "2026-07-12", available) == []


def test_finalize_ic_settles_itm_call_at_intrinsic():
    # spot 7546 -> call short 7540 is 6 ITM (< wing 5? capped at wing) -> settles at wing 5
    ic = _finalizable_ic()
    pnl, fees, status, reason = pp._finalize_ic(ic, spot_close=7546.0)
    # call exit = min(7546-7540, wing 5) = 5 ; put OTM = 0 -> pnl = (1.40 - 5 - 0)*100
    assert pnl == round((1.40 - 5.0 - 0.0) * 100, 2)    # -360.0 (near max loss on the ITM side)
    assert status == "expired"
