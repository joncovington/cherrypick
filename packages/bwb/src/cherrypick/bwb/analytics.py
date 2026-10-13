"""The one query layer every read surface goes through. Read-only.

`None` never means zero — a position not yet closed reports `net_pnl: None`, and an average over an
empty bucket is `None`.
"""

from __future__ import annotations


def headline(conn) -> dict:
    """Per-arm, per-symbol results over CLOSED positions, plus what is still open and each arm's
    fire count — the real effective sample for an arm-vs-control comparison, not the trade count
    (until an arm's add-on fires, its rows are byte-identical to control's, an expected
    `find_identical_readings` collision)."""
    arms: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT arm, symbol, COUNT(*) AS n, SUM(gross_pnl) AS gross, SUM(fees) AS fees, "
        "SUM(gross_pnl) - SUM(fees) AS net, SUM((gross_pnl - fees) > 0) AS wins "
        "FROM bwb_positions WHERE status = 'closed' GROUP BY arm, symbol ORDER BY arm, symbol"
    ):
        arms.setdefault(row["arm"], {})[row["symbol"]] = {
            "positions": row["n"],
            "gross_pnl": round(row["gross"], 2) if row["gross"] is not None else None,
            "fees": round(row["fees"], 2) if row["fees"] is not None else None,
            "net_pnl": round(row["net"], 2) if row["net"] is not None else None,
            "win_rate": round(row["wins"] / row["n"], 4) if row["n"] else None,
        }
    open_rows = conn.execute("SELECT COUNT(*) AS n FROM bwb_positions WHERE status != 'closed'").fetchone()
    return {"arms": arms, "open_positions": open_rows["n"], "fire_counts": fire_counts(conn)}


def fire_counts(conn) -> dict:
    """Per-arm add-on fire counts — trade count vs fire count, the plan's own honesty rule.
    `delta` fires most (raw proximity); `bounce` needs the move plus a turn; `flip` needs spot to
    have entered negative-gamma territory at all. A quiet `flip` arm is the honest state."""
    out: dict[str, dict] = {}
    # A missed add-on (`bwb addon-missed`) is neither a fire nor a "never triggered": its trigger may
    # have been met unmeasured. It is counted apart and left out of the fire rate's denominator.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bwb_positions)")}
    missed_sql = "SUM(addon_missed_at IS NOT NULL)" if "addon_missed_at" in cols else "0"
    for row in conn.execute(
        f"SELECT arm, COUNT(*) AS n, SUM(addon_fired_at IS NOT NULL) AS fired, {missed_sql} AS missed "
        "FROM bwb_positions GROUP BY arm ORDER BY arm"
    ):
        judged = row["n"] - (row["missed"] or 0)
        out[row["arm"]] = {
            "positions": row["n"],
            "fired": row["fired"] or 0,
            "missed": row["missed"] or 0,
            "fire_rate": round((row["fired"] or 0) / judged, 4) if judged else None,
        }
    return out


def worksheet(conn) -> list[dict]:
    """The live per-position worksheet: one row per open position with its entry metrics and the
    latest usable close-cost mark."""
    out = []
    for p in conn.execute("SELECT * FROM bwb_positions WHERE status != 'closed' ORDER BY symbol, arm"):
        p = dict(p)
        latest = conn.execute(
            "SELECT close_cost, spot, marked_at FROM bwb_marks WHERE position_id = ? "
            "AND close_cost IS NOT NULL AND usable = 1 ORDER BY marked_at DESC LIMIT 1",
            (p["position_id"],),
        ).fetchone()
        out.append(
            {
                "position_id": p["position_id"],
                "symbol": p["symbol"],
                "arm": p["arm"],
                "status": p["status"],
                "body_strike": p["body_strike"],
                "near_strike": p["near_strike"],
                "far_strike": p["far_strike"],
                "expiration": p["expiration"],
                "entry_spot": p["entry_spot"],
                "entry_credit": p["entry_credit"],
                "entry_max_loss": p["entry_max_loss"],
                "peak_abs_delta": p["peak_abs_delta"],
                "below_flip_seen": bool(p["below_flip_seen"]),
                "armed_at": p["armed_at"],
                "addon_fired_at": p["addon_fired_at"],
                "addon_credit": p["addon_credit"],
                "current_close_cost": latest["close_cost"] if latest else None,
                "current_spot": latest["spot"] if latest else None,
            }
        )
    return out


def trigger_coverage(conn, session_date: str) -> dict:
    """How good the day's trigger-tick substrate is — a barren session should read as thin data,
    never as a market.

    Reports the two halves of `measured` separately and names the reasons. A single refusal share
    was technically correct and operationally useless: it sat at 1.00 for four sessions
    (2026-08-24..27) while two unrelated defects starved spot and gamma-flip at once, and nothing
    in the number said which input to look at — or that anything was wrong at all, since a share is
    just a number until something calls it a finding. `total_failure` is that call.
    """
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(measured = 0) AS refused,
                  SUM(spot_measured = 0) AS no_spot,
                  SUM(flip_measured = 0) AS no_flip
           FROM bwb_trigger_ticks WHERE session_date = ?""",
        (session_date,),
    ).fetchone()
    total = row["total"] or 0
    refused = row["refused"] or 0
    reasons = {
        r["refusal"]: r["n"]
        for r in conn.execute(
            """SELECT refusal, COUNT(*) AS n FROM bwb_trigger_ticks
               WHERE session_date = ? AND measured = 0 AND refusal IS NOT NULL
               GROUP BY refusal ORDER BY n DESC""",
            (session_date,),
        )
    }
    return {
        "session": session_date,
        "ticks": total,
        "refused": refused,
        "refusal_share": round(refused / total, 4) if total else None,
        "no_spot": row["no_spot"] or 0,
        "no_flip": row["no_flip"] or 0,
        "reasons": reasons,
        # A session that recorded ticks and measured NONE of them is a defect, not thin data: the
        # loop ran, the cohorts existed, and every read failed. Stated as a flag so a reader does
        # not have to notice that a share happens to equal 1.
        "total_failure": bool(total and refused == total),
    }


def mark_coverage(conn, session_date: str) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(usable = 0) AS refused FROM bwb_marks WHERE session_date = ?",
        (session_date,),
    ).fetchone()
    total = row["total"] or 0
    return {
        "session": session_date,
        "marks": total,
        "refused": row["refused"] or 0,
        "refusal_share": round((row["refused"] or 0) / total, 4) if total else None,
    }


# --------------------------------------------------------------------------- live vs paper
# The live refusals that belong to a named configured gate, so a reader sees which setting refused
# the entry rather than a bare reason code. `live_loop.regime_gate` is the term-structure gate's one
# implementation; `test_live_gap.py` checks both reasons are strings it returns.
LIVE_GATE_OF = {
    "regime_inverted": "live.vix9d_vix_max",
    "regime_unmeasured": "live.vix9d_vix_max",
}


def _net(row: dict) -> float | None:
    if row.get("status") != "closed" or row.get("gross_pnl") is None:
        return None
    return round(float(row["gross_pnl"]) - float(row.get("fees") or 0.0), 2)


def _fired(row: dict) -> bool:
    return bool(row.get("addon_fired_at"))


def live_gap(live_conn, paper_conn, arm: str) -> dict:
    """The live arm's sessions, each against the paper row of the same arm and entry session.

    Per session: the entry credit -- the broker's fill on the live row (`entry_credit` is overwritten
    with the actual credit on confirmation) against paper's modelled mid credit, with live's limit
    and submission mid beside it; whether the add-on fired on each side (`match` when both or
    neither did); the fee reconciliation (live's modelled fees and gross, snapshotted once by
    `fee_reconcile`, against the broker's; `fees_source` says which a row holds); and net per
    session on both sides once both are closed. Live refusals are listed by reason, each named
    with the configured gate it belongs to where it has one (`LIVE_GATE_OF`).

    Correlated ladder rows (honesty rule 5): one selloff can move several sessions' positions at
    once, so sessions are not independent draws. Read-only; nothing here is a verdict."""
    live_rows = [
        dict(r)
        for r in live_conn.execute(
            "SELECT * FROM bwb_positions WHERE arm = ? ORDER BY entry_session, id", (arm,)
        )
    ]
    sessions = []
    for day in sorted({r["entry_session"] for r in live_rows}):
        mine = [r for r in live_rows if r["entry_session"] == day]
        held = [r for r in mine if r.get("status") != "cancelled"]
        live = held[0] if held else None
        paper = None
        if paper_conn is not None:
            p = paper_conn.execute(
                "SELECT * FROM bwb_positions WHERE arm = ? AND entry_session = ? ORDER BY id LIMIT 1",
                (arm, day),
            ).fetchone()
            paper = dict(p) if p else None
        same = (
            live is not None
            and paper is not None
            and (live["body_strike"], live["near_strike"], live["far_strike"], live["expiration"])
            == (paper["body_strike"], paper["near_strike"], paper["far_strike"], paper["expiration"])
        )
        live_net = _net(live) if live else None
        paper_net = _net(paper) if paper else None
        addon = None
        if live is not None and paper is not None:
            lf, pf = _fired(live), _fired(paper)
            addon = "both" if lf and pf else "live_only" if lf else "paper_only" if pf else "neither"
        sessions.append(
            {
                "session": day,
                "cancelled": len(mine) - len(held),
                "live_position": live["position_id"] if live else None,
                "paper_position": paper["position_id"] if paper else None,
                "same_structure": same,
                "live_status": live.get("status") if live else None,
                "entry_fill_status": live.get("entry_fill_status") if live else None,
                "live_credit": live.get("entry_credit") if live else None,
                "live_limit": live.get("entry_limit") if live else None,
                "live_mid_at_submit": live.get("entry_mid_at_submit") if live else None,
                "paper_credit": paper.get("entry_credit") if paper else None,
                "credit_gap": round(live["entry_credit"] - paper["entry_credit"], 4)
                if same and live.get("entry_credit") is not None and paper.get("entry_credit") is not None
                else None,
                "addon": addon,
                "addon_match": addon in ("both", "neither") if addon else None,
                "live_addon_fired_at": live.get("addon_fired_at") if live else None,
                "paper_addon_fired_at": paper.get("addon_fired_at") if paper else None,
                "live_addon_credit": live.get("addon_credit") if live else None,
                "paper_addon_credit": paper.get("addon_credit") if paper else None,
                "fees_source": live.get("fees_source") if live else None,
                "live_fees": live.get("fees") if live else None,
                "modeled_fees": live.get("modeled_fees") if live else None,
                "fee_variance": round(float(live["fees"]) - float(live["modeled_fees"]), 2)
                if live and live.get("fees_source") == "reconciled" and live.get("modeled_fees") is not None
                else None,
                "live_gross": live.get("gross_pnl") if live else None,
                "modeled_gross": live.get("modeled_gross_pnl") if live else None,
                "paper_fees": paper.get("fees") if paper else None,
                "live_net": live_net,
                "paper_net": paper_net,
                "net_gap": round(live_net - paper_net, 2)
                if live_net is not None and paper_net is not None
                else None,
            }
        )
    refusals: dict[str, dict] = {}
    have = live_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'bwb_entry_attempts'"
    ).fetchone()
    if have:
        for r in live_conn.execute(
            "SELECT outcome, COUNT(*) AS n, COUNT(DISTINCT trade_date) AS days, MIN(trade_date) AS first, "
            "MAX(trade_date) AS last FROM bwb_entry_attempts WHERE arm = ? AND outcome != 'placed' "
            "GROUP BY outcome ORDER BY n DESC",
            (arm,),
        ):
            refusals[r["outcome"]] = {
                "attempts": r["n"],
                "sessions": r["days"],
                "first": r["first"],
                "last": r["last"],
                "gate": LIVE_GATE_OF.get(r["outcome"]),
            }
    closed = [s for s in sessions if s["net_gap"] is not None]
    judged = [s for s in sessions if s["addon_match"] is not None]
    return {
        "arm": arm,
        "sessions": sessions,
        "totals": {
            "sessions": len(sessions),
            "closed_pairs": len(closed),
            "live_net": round(sum(s["live_net"] for s in closed), 2) if closed else None,
            "paper_net": round(sum(s["paper_net"] for s in closed), 2) if closed else None,
            "net_gap": round(sum(s["net_gap"] for s in closed), 2) if closed else None,
            "credit_gap": round(sum(s["credit_gap"] for s in sessions if s["credit_gap"] is not None), 4)
            if any(s["credit_gap"] is not None for s in sessions)
            else None,
            "addon_matched": sum(1 for s in judged if s["addon_match"]),
            "addon_judged": len(judged),
            "reconciled": sum(1 for s in sessions if s["fees_source"] == "reconciled"),
        },
        "refusals": refusals,
    }


def entry_context(conn, arm: str | None = None) -> list[dict]:
    """Every position's entry vol readings as recorded (`entry_regime.py`, `entry_iv.py`): the three
    index prints with their ages, the reason a print was not taken, the VIX9D/VIX and VIX/VIX3M
    ratios derived on read, the gate's side (`entry_regime.gate_state`: unmeasured is never
    assigned a side) and the expiration's implied vol with its completeness. Newest first."""
    from cherrypick.bwb import entry_regime

    cols = {r[1] for r in conn.execute("PRAGMA table_info(bwb_positions)")}
    if "entry_vix9d" not in cols:
        return []
    where, params = ("WHERE arm = ?", [arm]) if arm else ("", [])
    out = []
    for r in conn.execute(f"SELECT * FROM bwb_positions {where} ORDER BY entry_session DESC, arm", params):
        row = dict(r)
        v9, vix, v3m = row.get("entry_vix9d"), row.get("entry_vix"), row.get("entry_vix3m")
        out.append(
            {
                "position_id": row["position_id"],
                "arm": row["arm"],
                "entry_session": row["entry_session"],
                "expiration": row["expiration"],
                "status": row["status"],
                "entry_vix9d": v9,
                "entry_vix9d_age": row.get("entry_vix9d_age"),
                "entry_vix": vix,
                "entry_vix_age": row.get("entry_vix_age"),
                "entry_vix3m": v3m,
                "entry_vix3m_age": row.get("entry_vix3m_age"),
                "entry_regime_reason": row.get("entry_regime_reason"),
                "vix9d_vix": round(v9 / vix, 4) if v9 is not None and vix else None,
                "vix_vix3m": round(vix / v3m, 4) if vix is not None and v3m else None,
                "gate_state": entry_regime.gate_state(row),
                "entry_iv_vol": row.get("entry_iv_vol"),
                "entry_iv_complete": row.get("entry_iv_complete"),
                "entry_iv_reason": row.get("entry_iv_reason"),
                "net_pnl": _net(row),
            }
        )
    return out
