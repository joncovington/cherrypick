"""The fact pack — everything the model is allowed to see, and nothing else.

One JSON document per (session, slot), built deterministically from files on disk. It is the model's
entire world: the checkpoint script puts it on stdin with every tool denied, so a claim the model
makes that is not in here is a claim it invented.

Three rules shape what goes in:

* **Aggregates, never row dumps.** Counts, group-bys, top-N. A pack that pastes the ledger costs a
  fortune in tokens and buries the one number that mattered. The light packs target ~8K tokens, the
  deep pack ~30K.
* **Tolerant of everything.** Every database is opened read-only through `store.ro`, every query
  through `store.rows` (missing table → no rows), every foreign JSON through `store.read_json`
  (absent or half-written → the default). A module that has never run contributes an empty section;
  it never takes the checkpoint down.
* **`None` is not zero.** The suite has already drawn a confident wrong conclusion from averaging
  unrecorded values as zeros. Where a fact was not recorded, the pack says null.

**This is the one file that reads live data**, read-only, clearly labeled, and the source scan in
tests/test_guardrails.py proves no other module in this package mentions it. The model sees live
posture because an observer who cannot see it gives worse advice about the paper books that shadow
it — and because enactment is structurally paper-only, showing it costs nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

from cherrypick.advisor import bounds as _bounds
from cherrypick.advisor import clock as _clock
from cherrypick.advisor import paths as _paths
from cherrypick.advisor import settings as _settings
from cherrypick.advisor import store as _store
from cherrypick.advisor import verdicts as _verdicts

PACK_VERSION = 1

LIGHT_SLOTS = ("open", "am1", "am2", "midday", "pm1", "pm2", "close")
DEEP_SLOT = "deep"
SLOTS = (*LIGHT_SLOTS, DEEP_SLOT)

MODULES = ("meic", "flies", "earnings", "calendars", "pmcc")

# How many rows a "top N" section may carry. Refusal reasons have a long tail of one-offs; the head
# is the story, and the tail costs tokens that the deep sections need.
TOP_N = 8
TREND_SESSIONS = 5
JOURNAL_SESSIONS = 10
CONCLUDED_SHOWN = 10

_NOTE_LIVE = (
    "read-only context; enactment is paper-only regardless. Nothing you propose can reach a live "
    "account: the only output this pipeline can produce is a bounded paper advice artifact."
)


# --------------------------------------------------------------------------- database locations
# Resolved through cherrypick.core.home (via paths), never hardcoded, so a relocated home moves
# every one of them together.


def _paper_db(module: str) -> Path:
    return _paths.module_data_dir(module) / "paper_trades.db"


def _live_db(module: str) -> Path:
    """Each module named its live ledger differently before there was a convention; the names are
    load-bearing in deployed homes, so they are recorded here rather than normalised."""
    return _paths.module_data_dir(module) / {
        "meic": "meic_trades.db",
        "earnings": "earnings_trades.db",
        "flies": "live_trades.db",
    }[module]


def _stream_cache() -> Path:
    return _paths.module_data_dir("marketdata") / "stream_cache.db"


def _gex_history() -> Path:
    return _paths.module_data_dir("gex") / "gex_history.db"


def _advice_active(module: str) -> Path:
    return _paths.module_data_dir(module) / "advice_active.json"


def _read(path: Path, fn):
    """Open `path` read-only, hand the connection to `fn`, and close it. Absent file → None, which
    every caller renders as "this module has nothing to say", not as an error."""
    if not Path(path).exists():
        return None
    conn = _store.ro(path)
    try:
        return fn(conn)
    finally:
        conn.close()


def _counts(rows: list[dict], key: str, value: str = "n") -> dict[str, Any]:
    return {str(r[key] or "unknown"): r[value] for r in rows}


# --------------------------------------------------------------------------- market


def _market(session: str) -> dict[str, Any]:
    """VIX from whoever recorded it, the current GEX regime, and the day's range so far.

    The day range is age-gated: `stream_summary` keys on the ET trade date, so a row for another
    day is stale by definition and reporting it as "today" would describe a session that already
    closed."""

    def vix(conn):
        rows = _store.rows(
            conn, "SELECT vix, vix1d, vix1d_ratio FROM market_context WHERE context_date = ?",
            (session,),
        )
        return rows[0] if rows else None

    def gex(conn):
        latest = _store.rows(
            conn,
            "SELECT symbol, ts, spot, net_gex, net_gex_vol, zero_gamma, call_wall, put_wall"
            " FROM gex_regime_history WHERE trade_date = ? ORDER BY ts DESC LIMIT 1",
            (session,),
        )
        # RTH rows only. The recorder runs around the clock and freezes on the last streamed value
        # once the market quiets, so an unbounded per-date count double-weights whatever sign the
        # session ENDED on: on 2026-08-21 the unfiltered count read 181 positive / 26 negative while
        # the RTH-only truth was 67 / 11 — two-thirds of the "distribution" was one frozen overnight
        # value repeated every five minutes. The model flagged the resulting snapshot-vs-counts
        # contradiction six sessions running; this was most of it.
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        rth_open = _dt.fromisoformat(f"{session}T09:30:00").replace(tzinfo=et).timestamp()
        rth_close = _dt.fromisoformat(f"{session}T16:00:00").replace(tzinfo=et).timestamp()
        buckets = _store.rows(
            conn,
            "SELECT CASE WHEN net_gex >= 0 THEN 'positive' ELSE 'negative' END sign, COUNT(*) n"
            " FROM gex_regime_history WHERE trade_date = ? AND ts BETWEEN ? AND ? GROUP BY sign",
            (session, rth_open, rth_close),
        )
        return {
            "latest": latest[0] if latest else None,
            "today_counts": _counts(buckets, "sign"),
            "_counts_note": "RTH snapshots only (09:30-16:00 ET); the recorder also logs frozen "
            "off-hours rows that would double-weight the closing sign.",
        }

    def day_range(conn):
        return _store.rows(
            conn,
            # trade_date rides along so the day a range belongs to is on the row itself, not
            # implied by the query that fetched it.
            "SELECT symbol, trade_date, day_open, day_high, day_low, day_close, prev_day_close"
            " FROM stream_summary WHERE trade_date = ? ORDER BY symbol",
            (session,),
        )

    return {
        "vix": _read(_paper_db("meic"), vix),
        "gex": _read(_gex_history(), gex),
        "day_range": _read(_stream_cache(), day_range) or [],
        "_note": "day_range is today's rows only; a stale row is omitted rather than relabeled",
    }


# --------------------------------------------------------------------------- per-module paper


def _meic(session: str) -> dict[str, Any]:
    def read(conn):
        attempts = _store.rows(
            conn,
            "SELECT risk_profile, outcome, COUNT(*) n FROM entry_attempts WHERE trade_date = ?"
            " GROUP BY risk_profile, outcome ORDER BY risk_profile, n DESC",
            (session,),
        )
        blocks = _store.rows(
            conn,
            "SELECT risk_profile, block_detail, COUNT(*) n FROM entry_attempts"
            " WHERE trade_date = ? AND block_detail IS NOT NULL"
            " GROUP BY risk_profile, block_detail ORDER BY n DESC LIMIT ?",
            (session, TOP_N),
        )
        book = _store.rows(
            conn,
            "SELECT risk_profile, status, COUNT(*) n, SUM(net_credit) credit, SUM(pnl) pnl,"
            " SUM(fees) fees FROM ic_trades WHERE trade_date = ? GROUP BY risk_profile, status",
            (session,),
        )
        regime = _store.rows(
            conn,
            "SELECT symbol, loop_time, underlying_price, vol_implied_bucket, vol_event_bucket,"
            " vol_realized_bucket, gex_bucket, trend_bucket FROM iteration_regime"
            " WHERE loop_date = ? ORDER BY loop_time DESC LIMIT 1",
            (session,),
        )
        stops = _store.rows(
            conn,
            "SELECT risk_profile, COUNT(*) n FROM ic_trades WHERE trade_date = ?"
            " AND (put_max_cost IS NOT NULL OR call_max_cost IS NOT NULL)"
            " AND exit_time IS NOT NULL GROUP BY risk_profile",
            (session,),
        )
        # Did the baseline trade at all today? control/control-drift carry a stricter iv_rank floor
        # than open/width-5/width-10, so on a low-IV-rank day control can go completely dark while
        # the looser arms trade -- 0 of 297 on 2026-08-14 -- and a width comparison drawn on such a
        # session has no same-session baseline under it. Stated as its own flag rather than left to
        # be inferred from book_by_profile's absent row: an absent row reads as "nothing to report"
        # far more easily than as "the control was gated out", which is the whole finding.
        fills = _store.rows(
            conn,
            "SELECT risk_profile, COUNT(*) n FROM ic_trades WHERE trade_date = ? GROUP BY risk_profile",
            (session,),
        )
        by_profile = _counts(fills, "risk_profile")
        return {
            "entry_attempts": [
                {"profile": r["risk_profile"], "outcome": r["outcome"], "n": r["n"]} for r in attempts
            ],
            "top_block_details": blocks,
            "book_by_profile": book,
            "latest_regime": regime[0] if regime else None,
            "_gex_gate_series_note": "MEIC's regime_gex_block_negative gate reads NONE of the "
            "market.gex series above. It recomputes GEX fresh on every entry tick from the stream "
            "cache (cherrypick.meic.tt cmd_get_gex): nearest expiration only (0DTE intraday), "
            "±20 strikes around spot, OI-weighted positioning basis, and blocks when that "
            "instantaneous net is negative. The engine series in market.gex is a wider-window "
            "recorder sampled ~5min; the two can legitimately disagree, especially late in a 0DTE "
            "session. Per-entry rows record BOTH bases (gex_net_at_entry, gex_net_vol_at_entry), "
            "so which basis better separates outcomes is a read-side derivation once session "
            "depth allows, not a reason for parallel experiments now.",
            "closed_with_stop_instrumentation": _counts(stops, "risk_profile"),
            "control_fired": {
                "fired": by_profile.get("control", 0) > 0,
                "fills_by_profile": by_profile,
                "_note": "bucket width comparisons on this; never drop a session because it is false",
            },
        }

    out = _read(_paper_db("meic"), read) or {"_absent": "no meic paper ledger"}
    out["advice_active"] = _store.read_json(_advice_active("meic"), default=None)
    return out


def _flies(session: str) -> dict[str, Any]:
    def read(conn):
        books = _store.rows(
            conn,
            "SELECT arm, symbol, credit_collected, debits_paid, fees, net_cash, worst, floor_holds,"
            " band_low, band_high, unbounded_below, completion_rate, pnl, status FROM fly_books"
            " WHERE trade_date = ? ORDER BY arm",
            (session,),
        )
        attempts = _store.rows(
            conn,
            "SELECT arm, outcome, COUNT(*) n FROM fly_entry_attempts WHERE trade_date = ?"
            " GROUP BY arm, outcome ORDER BY arm, n DESC",
            (session,),
        )
        refusals = _store.rows(
            conn,
            "SELECT arm, block_detail, COUNT(*) n FROM fly_entry_attempts WHERE trade_date = ?"
            " AND block_detail IS NOT NULL GROUP BY arm, block_detail ORDER BY n DESC LIMIT ?",
            (session, TOP_N),
        )
        positions = _store.rows(
            conn,
            "SELECT arm, status, COUNT(*) n, SUM(net) net FROM fly_positions WHERE trade_date = ?"
            " GROUP BY arm, status",
            (session,),
        )
        return {
            "books": books,
            "entry_attempts": attempts,
            "top_refusals": refusals,
            "positions_by_arm": positions,
            "_note": "a book's floor is only meaningful with the band it holds over — both travel here",
        }

    out = _read(_paper_db("flies"), read) or {"_absent": "no flies paper ledger"}
    out["advice_active"] = _store.read_json(_advice_active("flies"), default=None)
    return out


def _earnings(session: str) -> dict[str, Any]:
    def read(conn):
        scans = _store.rows(
            conn,
            "SELECT strategy, stage, outcome, COUNT(*) n FROM scan_log WHERE scan_date = ?"
            " GROUP BY strategy, stage, outcome ORDER BY n DESC LIMIT ?",
            (session, TOP_N * 2),
        )
        rejects = _store.rows(
            conn,
            "SELECT reason, COUNT(*) n FROM scan_log WHERE scan_date = ? AND reason IS NOT NULL"
            " GROUP BY reason ORDER BY n DESC LIMIT ?",
            (session, TOP_N),
        )
        # Open positions with their most recent usable mark. Earnings is the one module that holds
        # overnight, so "what is on the book right now" is a question only it can answer.
        open_rows = _store.rows(
            conn,
            "SELECT t.order_id, t.strategy, t.symbol, t.profile, t.entry_credit, t.capital_at_risk,"
            " t.hold_days, t.max_unrealized_pnl, t.min_unrealized_pnl,"
            " (SELECT unrealized_pnl FROM position_marks m WHERE m.order_id = t.order_id"
            "   AND m.usable = 1 ORDER BY m.marked_at DESC LIMIT 1) last_mark,"
            " (SELECT marked_at FROM position_marks m WHERE m.order_id = t.order_id"
            "   AND m.usable = 1 ORDER BY m.marked_at DESC LIMIT 1) last_mark_at"
            " FROM trades t WHERE t.status = 'open' ORDER BY t.strategy, t.symbol",
        )
        closed = _store.rows(
            conn,
            "SELECT profile, strategy, COUNT(*) n, SUM(pnl) pnl, SUM(entry_cost + exit_cost) cost"
            " FROM trades WHERE closed_at IS NOT NULL"
            " AND date(closed_at, 'unixepoch', 'localtime') = ? GROUP BY profile, strategy",
            (session,),
        )
        health = _store.rows(
            conn,
            "SELECT phase, status, COUNT(*) n, MAX(ran_at) last_ran FROM loop_iterations"
            " WHERE session_date = ? GROUP BY phase, status",
            (session,),
        )
        events = _store.rows(
            conn,
            "SELECT action, reason, executed, COUNT(*) n FROM management_events"
            " WHERE session_date = ? GROUP BY action, reason, executed ORDER BY n DESC LIMIT ?",
            (session, TOP_N),
        )
        return {
            "scans": scans,
            "top_reject_reasons": rejects,
            "open_positions": open_rows,
            "closed_today": closed,
            "loop_health": health,
            "management_events": events,
        }

    out = _read(_paper_db("earnings"), read) or {"_absent": "no earnings paper ledger"}
    out["advice_active"] = _store.read_json(_advice_active("earnings"), default=None)
    return out


def _calendars(session: str) -> dict[str, Any]:
    """Weekly double calendars: the advisable surface is exit parameters only (the entry is
    unconditional every week), so the pack carries what an exit judgement needs — each open
    position's entry economics and latest usable mark, the closed books' results by exit reason,
    and the mark-substrate health that decides whether the module's own derived policy table
    (`python -m cherrypick.calendars.cli policies`) is currently trustworthy."""

    def read(conn):
        open_rows = _store.rows(
            conn,
            "SELECT p.position_id, p.book, p.side, p.structure, p.strike, p.entry_debit,"
            " p.entry_em, p.entry_spot, p.status, p.front_expiration, p.back_expiration,"
            " (SELECT m.mid FROM dc_marks m WHERE m.position_id = p.position_id AND m.usable = 1"
            "   AND m.leg_role LIKE 'back%' ORDER BY m.marked_at DESC LIMIT 1) last_back_mid,"
            " (SELECT m.mid FROM dc_marks m WHERE m.position_id = p.position_id AND m.usable = 1"
            "   AND m.leg_role LIKE 'front%' ORDER BY m.marked_at DESC LIMIT 1) last_front_mid,"
            " (SELECT m.spot FROM dc_marks m WHERE m.position_id = p.position_id AND m.usable = 1"
            "   ORDER BY m.marked_at DESC LIMIT 1) last_spot"
            " FROM dc_positions p WHERE p.status != 'closed' ORDER BY p.book, p.side",
        )
        closed = _store.rows(
            conn,
            "SELECT book, structure, exit_reason, COUNT(*) n, SUM(gross_pnl) gross, SUM(fees) fees"
            " FROM dc_positions WHERE status = 'closed' GROUP BY book, structure, exit_reason"
            " ORDER BY book",
        )
        attempts = _store.rows(
            conn,
            "SELECT outcome, COUNT(*) n FROM dc_entry_attempts WHERE trade_date = ?"
            " GROUP BY outcome ORDER BY n DESC",
            (session,),
        )
        events = _store.rows(
            conn,
            "SELECT action, reason, executed, gate, COUNT(*) n FROM dc_management_events"
            " WHERE session_date = ? GROUP BY action, reason, executed, gate ORDER BY n DESC LIMIT ?",
            (session, TOP_N),
        )
        marks = _store.rows(
            conn,
            "SELECT usable, COUNT(*) n FROM dc_marks WHERE session_date = ? GROUP BY usable",
            (session,),
        )
        return {
            "open_positions": open_rows,
            "closed_by_exit_reason": closed,
            "entry_attempts": attempts,
            "management_events": events,
            "mark_coverage": marks,
            "_note": (
                "entries are weekly and unconditional; only exit params are advisable, and the "
                "module's read-side derivation already scores the standard grid over the recorded "
                "mark path — propose values, not new machinery"
            ),
        }

    out = _read(_paper_db("calendars"), read) or {"_absent": "no calendars paper ledger"}
    out["advice_active"] = _store.read_json(_advice_active("calendars"), default=None)
    return out


def _pmcc(session: str) -> dict[str, Any]:
    """PMCC-99 deep-ITM covered calls: the advisable surface is the tv-close threshold and the
    entry yield floor. The pack carries each open position's worksheet economics and its latest
    short time value (the number the exit rule reads), the closed books' results by exit reason,
    the assignment-exposure telemetry (the module measures early assignment, it does not model it —
    exposure beside net is the honest read), and the entry attempts, which this module makes most
    sessions."""

    def read(conn):
        open_rows = _store.rows(
            conn,
            "SELECT p.position_id, p.book, p.symbol, p.long_strike, p.long_expiration,"
            " p.short_strike, p.short_expiration, p.net_debit, p.entry_net_tv,"
            " p.entry_weekly_yield_pct, p.entry_downside_protection_pct, p.roll_count, p.status,"
            " (SELECT m.short_tv FROM pmcc_marks m WHERE m.position_id = p.position_id"
            "   AND m.usable = 1 AND m.short_tv IS NOT NULL ORDER BY m.marked_at DESC LIMIT 1)"
            "   last_short_tv,"
            " (SELECT m.spot FROM pmcc_marks m WHERE m.position_id = p.position_id AND m.usable = 1"
            "   ORDER BY m.marked_at DESC LIMIT 1) last_spot"
            " FROM pmcc_positions p WHERE p.status != 'closed' ORDER BY p.book, p.symbol",
        )
        closed = _store.rows(
            conn,
            "SELECT book, symbol, exit_reason, COUNT(*) n, SUM(gross_pnl) gross, SUM(fees) fees,"
            " SUM(roll_count) rolls FROM pmcc_positions WHERE status = 'closed'"
            " GROUP BY book, symbol, exit_reason ORDER BY book",
        )
        attempts = _store.rows(
            conn,
            "SELECT book, outcome, COUNT(*) n FROM pmcc_entry_attempts WHERE trade_date = ?"
            " GROUP BY book, outcome ORDER BY n DESC",
            (session,),
        )
        events = _store.rows(
            conn,
            "SELECT action, reason, executed, gate, COUNT(*) n FROM pmcc_management_events"
            " WHERE session_date = ? GROUP BY action, reason, executed, gate ORDER BY n DESC LIMIT ?",
            (session, TOP_N),
        )
        exposure = _store.rows(
            conn,
            "SELECT position_id, COUNT(*) exposed_ticks FROM pmcc_marks"
            " WHERE session_date = ? AND assignment_exposed = 1 GROUP BY position_id",
            (session,),
        )
        marks = _store.rows(
            conn,
            "SELECT usable, COUNT(*) n FROM pmcc_marks WHERE session_date = ? GROUP BY usable",
            (session,),
        )
        return {
            "open_positions": open_rows,
            "closed_by_exit_reason": closed,
            "entry_attempts": attempts,
            "management_events": events,
            "assignment_exposure": exposure,
            "mark_coverage": marks,
            "_note": (
                "early assignment is measured (assignment_exposure), never modelled — treat paper "
                "net as an upper bound; the roll-vs-hold choice is the book contrast and is not "
                "advisable, only tv_close_threshold and target_weekly_yield_min are"
            ),
        }

    out = _read(_paper_db("pmcc"), read) or {"_absent": "no pmcc paper ledger"}
    out["advice_active"] = _store.read_json(_advice_active("pmcc"), default=None)
    return out


_MODULE_SECTIONS = {
    "meic": _meic,
    "flies": _flies,
    "earnings": _earnings,
    "calendars": _calendars,
    "pmcc": _pmcc,
}


# --------------------------------------------------------------------------- live (read-only)


def _live(session: str) -> dict[str, Any]:
    """Live posture and a live-vs-paper shape, for context only.

    Deliberately duplicated rather than imported: this package depends on `cherrypick.core` alone
    and spawns no subprocess, so `cherrypick.flies.analytics`' live-vs-paper summary cannot be
    called from here. The duplication is labeled with the shape tag it matches so a future divergence
    is visible, and it is facts-for-context — never a report surface. See
    packages/flies/src/cherrypick/flies/analytics.py's cross-reference.
    """
    state = _paths.state_dir()
    posture: dict[str, Any] = {}
    for module in ("meic", "earnings"):
        cfg = _store.read_json(_paths.module_config_path(module), default={}) or {}
        posture[module] = {"enable_live_trading": bool(cfg.get("enable_live_trading"))}
    flies_cfg = (_store.read_json(_paths.module_config_path("flies"), default={}) or {}).get("live") or {}
    arm = _store.read_json(state / "flies-live-arm.json", default=None)
    posture["flies"] = {
        "enabled": bool(flies_cfg.get("enabled")),
        # The same record the supervisor's job enablement reads. Presence and date only — arming
        # authority is the module's human-confirmed command and nothing here can touch it.
        "arm_record": {
            "date": (arm or {}).get("date"),
            "armed_today": bool(arm) and (arm or {}).get("date") == session,
        },
    }

    def live_flies(conn):
        today = _store.rows(
            conn,
            "SELECT status, COUNT(*) n, SUM(net) net, SUM(fees) fees FROM fly_positions"
            " WHERE trade_date = ? GROUP BY status",
            (session,),
        )
        fills = _store.rows(
            conn,
            "SELECT entry_fill_status, COUNT(*) n FROM fly_positions WHERE trade_date = ?"
            " GROUP BY entry_fill_status",
            (session,),
        )
        settled = _store.rows(
            conn,
            "SELECT COUNT(*) n, SUM(pnl) pnl FROM fly_positions WHERE trade_date = ?"
            " AND status = 'settled'",
            (session,),
        )
        return {"by_status": today, "entry_fills": _counts(fills, "entry_fill_status"),
                "settled_today": settled[0] if settled else None}

    def paper_flies(conn):
        rows = _store.rows(
            conn,
            "SELECT COUNT(*) n, SUM(pnl) pnl, AVG(completion_latency_min) latency FROM fly_positions"
            " WHERE trade_date = ? AND status = 'settled'",
            (session,),
        )
        return rows[0] if rows else None

    live_vs_paper = {
        "shape": "flies.analytics.live_vs_paper/v1",
        "live": _read(_live_db("flies"), live_flies),
        "paper": _read(_paper_db("flies"), paper_flies),
        "_note": "computed here with the advisor's own read-only SQL; see the docstring on why",
    }

    desk_journal = state / "desk" / "journal.jsonl"
    try:
        desk_events = sum(1 for _ in desk_journal.open(encoding="utf-8", errors="replace"))
    except OSError:
        desk_events = None

    return {
        "_note": _NOTE_LIVE,
        "halt_flag_present": (state / "halt-live.flag").exists(),
        "posture": posture,
        "flies_live": live_vs_paper,
        # Presence and count only. The desk is a human's discretionary path; what it did is not the
        # advisor's business, but whether it was used today is context for an unexplained position.
        "desk": {"journal_present": desk_journal.exists(), "events": desk_events},
    }


# --------------------------------------------------------------------------- advisor's own state


def _experiments_running(conn) -> list[dict[str, Any]]:
    """Active experiments with their running advised-vs-base delta, so the model can see whether
    what it started is working before it decides what to do next."""
    out = []
    for exp in _store.experiments(conn, status="active"):
        params = json.loads(exp["params_json"] or "{}")
        pairs = _verdicts.for_experiment(exp)["pairs"]
        out.append({
            "id": exp["id"],
            "module": exp["module"],
            "base_profile": exp["base_profile"],
            "name": exp["name"],
            "hypothesis": exp["hypothesis"],
            "params": params,
            "sessions_run": exp["sessions_run"],
            "expires_after_sessions": exp["expires_after_sessions"],
            "reading": [
                {"advised_tag": p["advised_tag"], "delta": p["delta"], "underpowered": p["underpowered"]}
                for p in pairs
            ],
        })
    return out


def _pending_proposals(conn, session: str) -> list[dict[str, Any]]:
    """Drafts from earlier slots today. Light output compounds into the deep slot rather than
    evaporating — checkpoints that cannot see each other produce the same shallow observation over
    and over."""
    return _store.rows(
        conn,
        "SELECT p.id, p.module, p.kind, p.payload_json, p.status, c.slot FROM proposals p"
        " JOIN checkpoints c ON c.id = p.checkpoint_id WHERE c.session = ?"
        " ORDER BY p.id",
        (session,),
    )


def _advisor_journal(conn, session: str) -> dict[str, Any]:
    """The advisor's own memory: what it has recently observed, proposed, and been told.

    Without this the model re-proposes the idea a human dismissed last Tuesday, every Tuesday. With
    it, a dismissal is a fact in evidence and a thread can build across sessions. Compact and
    capped, oldest first.
    """
    sessions = [session, *_clock.previous_sessions(session, JOURNAL_SESSIONS)]
    oldest = min(sessions)
    checkpoints = _store.rows(
        conn,
        "SELECT session, slot, ok, observations_json, flags_json FROM checkpoints"
        " WHERE session >= ? ORDER BY session, slot",
        (oldest,),
    )
    proposals = _store.rows(
        conn,
        "SELECT c.session, p.module, p.kind, p.status, p.reject_reason, p.payload_json"
        " FROM proposals p JOIN checkpoints c ON c.id = p.checkpoint_id"
        " WHERE c.session >= ? ORDER BY c.session, p.id",
        (oldest,),
    )
    concluded = _store.rows(
        conn,
        "SELECT id, module, params_json, status, created_session, sessions_run, verdict_json"
        " FROM experiments WHERE status IN ('expired', 'killed') ORDER BY updated_at DESC LIMIT ?",
        (CONCLUDED_SHOWN,),
    )
    return {
        "_note": "your own recent history. Do not re-propose what was dismissed; build on threads.",
        "checkpoints": [
            {
                "session": r["session"],
                "slot": r["slot"],
                "ok": bool(r["ok"]),
                "observations": json.loads(r["observations_json"] or "[]"),
                "flags": json.loads(r["flags_json"] or "[]"),
            }
            for r in checkpoints
        ],
        "proposals": [
            {
                "session": r["session"],
                "module": r["module"],
                "kind": r["kind"],
                "fate": r["status"],
                "reason": r["reject_reason"],
                "payload": json.loads(r["payload_json"] or "{}"),
            }
            for r in proposals
        ],
        "concluded_experiments": [
            {
                "id": r["id"],
                "module": r["module"],
                "params": json.loads(r["params_json"] or "{}"),
                "status": r["status"],
                "created_session": r["created_session"],
                "sessions_run": r["sessions_run"],
                "verdict": json.loads(r["verdict_json"]) if r["verdict_json"] else None,
            }
            for r in concluded
        ],
    }


# --------------------------------------------------------------------------- deep-only sections


def _review_today(session: str) -> Any:
    """Review's fact set for today, verbatim. It is provisional at 17:00 — earnings settles the
    next morning — and the prompt says so, because a provisional number presented as final is how a
    narrative records something that never happened."""
    return _store.read_json(_paths.module_data_dir("review") / f"eod-{session}.json", default=None)


def _review_trend(session: str) -> list[dict[str, Any]]:
    """The prior fact sets, thinned to the module totals. The full sets would swamp the pack; the
    totals are what a trend question needs."""
    out = []
    for prior in _clock.previous_sessions(session, TREND_SESSIONS):
        facts = _store.read_json(_paths.module_data_dir("review") / f"eod-{prior}.json", default=None)
        if not isinstance(facts, dict):
            continue
        out.append({
            "session": prior,
            "status": facts.get("status"),
            "modules": {
                name: {k: v for k, v in (block or {}).items() if k in ("net_pnl", "trades", "by_profile")}
                for name, block in (facts.get("modules") or {}).items()
            },
        })
    return out


def _arm_readings() -> dict[str, Any]:
    """Every arm's reading and qualification, per module — the numbers a verdict reasons FROM.

    Computed through the suite's own chain (ledger readers → compare_profiles → qualify_readings),
    so the model is looking at exactly what `verdicts.py` will compute when an experiment expires.

    `collisions` (added 2026-08-14) flags tags whose readings are byte-identical across sample,
    win_rate, days, net_pnl, sharpe and max_drawdown — either the same underlying book trading
    under two names, or a config mistake that never actually differentiated them. Found live the
    same day: meic's `gex-open`/`gex-blocked` and `small-xsp`/`explore-xsp-loosecredit` read
    identical in every field. Detected, never merged — `readings` keeps one row per tag so nothing
    downstream (verdicts, qualification) changes shape; this is a warning laid alongside it so the
    model doesn't read two names as two independent pieces of evidence.

    Each module is qualified against ITS OWN configured rule (2026-08-15, acting on proposal #4).
    This used to call `qualify_readings(readings)` bare, which applied the library default and
    showed the model a weaker gate than `calibrate` applies on the same numbers — the pack said an
    arm was qualified while the suite's own calibration said it was not. The resolved thresholds
    travel beside the verdict as `rule` so the model can cite what it was actually judged against
    rather than assuming the default three.
    """
    from cherrypick.core.profiles import QUALIFICATION_RULE, find_identical_readings, qualify_readings

    out: dict[str, Any] = {}
    cfg = _store.read_json(_home.config_path(), default={}) or {}
    for module in MODULES:
        readings = _verdicts.readings(module)
        rule = _settings.calibration_rule(module, cfg)
        out[module] = {
            "readings": readings,
            "qualification": qualify_readings(readings, rule=rule) if readings else {},
            "rule": {**QUALIFICATION_RULE, **rule},
            "collisions": find_identical_readings(readings) if readings else [],
        }
    return out


def _advice_audit(session: str) -> dict[str, Any]:
    """The last artifact written per module, including its rejections. Reject-all is silent from
    the loop's side (it just runs baseline), so this is where a proposal that was refused at the
    gate becomes visible."""
    out = {}
    for module in MODULES:
        artifact = _store.read_json(_paths.advice_path(module, session), default=None)
        upcoming = _store.read_json(
            _paths.advice_path(module, _clock.next_session(session)), default=None
        )
        out[module] = {"for_today": artifact, "for_next_session": upcoming}
    return out


# --------------------------------------------------------------------------- build


def build(session: str, slot: str, modules: tuple[str, ...] | list[str] | None = None) -> dict[str, Any]:
    """Assemble one pack. Pure read + aggregate; writes nothing."""
    if slot not in SLOTS:
        raise ValueError(f"unknown slot {slot!r}; expected one of {SLOTS}")
    selected = tuple(modules or MODULES)

    conn = _store.connect()
    try:
        pack: dict[str, Any] = {
            "pack_version": PACK_VERSION,
            "session": session,
            "slot": slot,
            "generated_at": _store.now_iso(),
            "modules": list(selected),
            "market": _market(session),
            "paper": {m: _MODULE_SECTIONS[m](session) for m in selected if m in _MODULE_SECTIONS},
            "live": _live(session),
            "experiments": _experiments_running(conn),
            "pending_proposals": [
                {
                    "id": r["id"],
                    "slot": r["slot"],
                    "module": r["module"],
                    "kind": r["kind"],
                    "status": r["status"],
                    "payload": json.loads(r["payload_json"] or "{}"),
                }
                for r in _pending_proposals(conn, session)
            ],
        }

        if slot == DEEP_SLOT:
            pack["review_today"] = _review_today(session)
            pack["review_trend"] = _review_trend(session)
            pack["arm_readings"] = _arm_readings()
            pack["bounds"] = _bounds.all_modules(selected)
            pack["experiments_full"] = {
                "active": _store.experiments(conn, status="active"),
                "queued": _store.experiments(conn, status="queued"),
                "concluded": _store.rows(
                    conn,
                    "SELECT * FROM experiments WHERE status IN ('expired', 'killed')"
                    " ORDER BY updated_at DESC LIMIT ?",
                    (CONCLUDED_SHOWN,),
                ),
            }
            pack["advice_audit"] = _advice_audit(session)
            pack["advisor_journal"] = _advisor_journal(conn, session)

        return pack
    finally:
        conn.close()


def write(session: str, slot: str, modules: tuple[str, ...] | list[str] | None = None) -> Path:
    """Build and persist the pack. Write-once by convention: the pack is the record of what the
    model was shown, so a re-run (`--force`) deliberately overwrites the whole (session, slot)
    triple — pack, raw reply and checkpoint — rather than leaving a pack that describes a different
    reply than the one on file."""
    pack = build(session, slot, modules)
    return _store.write_json(_paths.pack_path(session, slot), pack)
