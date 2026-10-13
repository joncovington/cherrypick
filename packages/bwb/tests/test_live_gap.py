"""The live-vs-paper read and the entry-context read the console bridges (`analytics.live_gap`,
`analytics.entry_context`, `bwb live-gap` / `entry-context`).

Pinned where a shortcut would mislead: a cancelled live entry is not the session's position, a fee
variance exists only once a row is reconciled, a term-structure refusal is named with its gate, and
an empty live ledger reads as no sessions rather than as a failure. The verbs never write a ledger.
"""

from __future__ import annotations

import inspect
import json
import sqlite3

from cherrypick.bwb import analytics, cli, db, live_loop

DAY = "2026-10-13"


def _ledger(path) -> sqlite3.Connection:
    db.connect(str(path)).close()  # migrate once; the reads only read
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _row(conn, pid, arm, *, status="closed", credit=1.20, fired=None, gross=100.0, fees=10.0, **extra):
    row = {
        "position_id": pid,
        "symbol": "SPX",
        "arm": arm,
        "entry_session": DAY,
        "structure_signature": "sig",
        "expiration": "2026-10-16",
        "body_strike": 6400.0,
        "near_strike": 6405.0,
        "far_strike": 6390.0,
        "entry_credit": credit,
        "addon_fired_at": fired,
        "status": status,
        "gross_pnl": gross if status == "closed" else None,
        "fees": fees,
        **extra,
    }
    cols = ", ".join(row)
    conn.execute(
        f"INSERT INTO bwb_positions ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values())
    )
    conn.commit()


def test_live_gap_pairs_the_held_live_row_with_papers_same_arm(tmp_path):
    live, paper = _ledger(tmp_path / "live.db"), _ledger(tmp_path / "paper.db")
    _row(live, "L-cancel", "bounce", status="cancelled", credit=None, gross=None)
    _row(live, "L1", "bounce", credit=1.25, fired=f"{DAY}T11:00", gross=140.0, fees=12.0,
         fees_source="reconciled", modeled_fees=10.5, modeled_gross_pnl=135.0, entry_limit=1.20)  # fmt: skip
    _row(paper, "P1", "bounce", credit=1.10, fired=None, gross=110.0, fees=11.0)

    out = analytics.live_gap(live, paper, "bounce")
    (s,) = out["sessions"]
    assert s["cancelled"] == 1 and s["live_position"] == "L1" and s["same_structure"]
    assert s["credit_gap"] == 0.15  # the broker's fill over paper's modelled mid
    assert (s["addon"], s["addon_match"]) == ("live_only", False)
    assert s["fee_variance"] == 1.5  # broker 12.00 against the 10.50 the row was modelled at
    assert (s["live_net"], s["paper_net"], s["net_gap"]) == (128.0, 99.0, 29.0)
    assert out["totals"]["addon_judged"] == 1 and out["totals"]["addon_matched"] == 0


def test_an_unreconciled_row_has_no_fee_variance(tmp_path):
    live, paper = _ledger(tmp_path / "live.db"), _ledger(tmp_path / "paper.db")
    _row(live, "L1", "bounce", status="open", fees_source="broker_estimate", modeled_fees=None)
    _row(paper, "P1", "bounce", status="open")
    (s,) = analytics.live_gap(live, paper, "bounce")["sessions"]
    assert s["fee_variance"] is None and s["net_gap"] is None


def test_term_structure_refusals_are_named_with_their_gate(tmp_path):
    live = _ledger(tmp_path / "live.db")
    for outcome in ("regime_inverted", "regime_inverted", "halt_flag_present"):
        db.record_entry_attempt(live, trade_date=DAY, symbol="SPX", arm="bounce", outcome=outcome)
    live.commit()
    out = analytics.live_gap(live, None, "bounce")
    assert out["sessions"] == []
    assert out["refusals"]["regime_inverted"]["attempts"] == 2
    assert out["refusals"]["regime_inverted"]["gate"] == "live.vix9d_vix_max"
    assert out["refusals"]["halt_flag_present"]["gate"] is None


def test_the_named_reasons_are_the_ones_the_live_gate_returns():
    source = inspect.getsource(live_loop.regime_gate)
    for reason in analytics.LIVE_GATE_OF:
        assert f'"{reason}"' in source, reason


def test_entry_context_derives_ratios_and_never_sides_an_unmeasured_row(tmp_path):
    conn = _ledger(tmp_path / "paper.db")
    _row(conn, "A", "control", entry_vix9d=14.0, entry_vix=16.0, entry_vix3m=20.0, entry_iv_vol=12.5,
         entry_iv_complete=0)  # fmt: skip
    _row(conn, "B", "delta", entry_vix=16.0, entry_regime_reason="VIX9D:stale")
    rows = {r["position_id"]: r for r in analytics.entry_context(conn)}
    assert (
        rows["A"]["vix9d_vix"] == 0.875
        and rows["A"]["vix_vix3m"] == 0.8
        and rows["A"]["gate_state"] == "open"
    )
    assert rows["A"]["entry_iv_complete"] == 0  # a lower bound, carried as recorded
    assert rows["B"]["vix9d_vix"] is None and rows["B"]["gate_state"] == "unmeasured"
    assert rows["B"]["entry_regime_reason"] == "VIX9D:stale"


def test_the_read_verbs_never_write_a_ledger(tmp_path, capsys):
    path = tmp_path / "paper.db"
    _ledger(path).close()
    before = path.read_bytes()
    for verb in (["entry-context"], ["regime-split"], ["iv-premium"]):
        assert cli.main(["--db", str(path), *verb]) == 0
        assert json.loads(capsys.readouterr().out)["ledger"] == "ok"
    assert (
        cli.main(["--db", str(path), "live-gap", "--arm", "bounce", "--live-db", str(tmp_path / "no.db")])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["ledger"] == "absent"
    assert path.read_bytes() == before
    assert not (tmp_path / "no.db").exists()
