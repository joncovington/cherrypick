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
from cherrypick.core import regime as _regime

from cherrypick.advisor import bounds as _bounds
from cherrypick.advisor import clock as _clock
from cherrypick.advisor import enactment as _enactment
from cherrypick.advisor import paths as _paths
from cherrypick.advisor import settings as _settings
from cherrypick.advisor import store as _store
from cherrypick.advisor import verdicts as _verdicts

PACK_VERSION = 1

LIGHT_SLOTS = ("open", "am1", "am2", "midday", "pm1", "pm2", "close")
DEEP_SLOT = "deep"
SLOTS = (*LIGHT_SLOTS, DEEP_SLOT)

# Derived from `bounds`, not restated. This was a literal tuple until 2026-08-26 and it was the
# third hand-kept module list in this package — bwb and curve were absent from it while
# `enactment.MODULES` (which does derive) already had them, so the same pack could reconcile a
# module's enactment and carry no facts about it at all.
MODULES = _bounds.MODULES

# How much a model should be asked to READ AT ONCE, in bytes. This is an attention budget, and
# saying so is a 2026-08-26 correction: the earlier numbers were derived from token targets and the
# warning was worded like a resource overrun, which is what nobody acted on it for nine sessions.
#
# It is not a capacity limit and not a cost limit, and both were checked rather than assumed. The
# deep pack at 472KB is ~130-160k tokens against a 1M-token window — about 15%, so it could
# quadruple and still fit. At list rates the whole schedule (seven light slots on the cheap model,
# one deep on the strong one) runs about $1.40 a day. Neither is a reason to trim anything. The
# reason to trim is that a finding buried in 470KB of context is a finding nobody acts on, which is
# the same failure as not recording it.
#
# The bar moved once, here, deliberately: light 32,000 -> 48,000 and deep 120,000 -> 200,000. The
# light target predated the suite having SEVEN modules — `paper` alone is 23KB of the 42KB light
# pack, and that is the section the pack exists to carry. A ceiling that every honest pack breaches
# is not a ceiling. Moving it to meet the artifact is still how a ceiling stops meaning anything,
# so: this is the last move that gets made without cutting something first.
#
# Enforced against the REAL pack by `write` — the gap that let it run from 250KB to 731KB unnoticed
# was a budget test asserting against a seeded fixture, i.e. measuring a pack nobody reads.
# Exceeding the ceiling is reported, never fatal: a session's advice must not be lost to a size check.
LIGHT_MAX_BYTES = 48_000
DEEP_MAX_BYTES = 200_000

# How many rows a "top N" section may carry. Refusal reasons have a long tail of one-offs; the head
# is the story, and the tail costs tokens that the deep sections need.
TOP_N = 8
TREND_SESSIONS = 5
JOURNAL_SESSIONS = 10

# How many of those sessions keep their proposals, observations and non-critical flags IN FULL.
# Beyond this an entry keeps its identity — enough to not re-propose it — and drops the argument.
# See `_advisor_journal`.
JOURNAL_FULL_SESSIONS = 2

# Flag severities that survive the taper at ANY age, carried verbatim. A critical flag is a standing
# caveat about a module — the kind of thing that must never age out of view — and there are twelve
# of them across the whole ten-session window, costing 8.3KB. `warn` and `info` are 210 flags and
# 86.5KB, and past the full-detail window they are history rather than a standing caveat.
JOURNAL_STANDING_SEVERITIES = frozenset({"critical"})
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
    return (
        _paths.module_data_dir(module)
        / {
            "meic": "meic_trades.db",
            "earnings": "earnings_trades.db",
            "flies": "live_trades.db",
        }[module]
    )


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


def carried_advice_params(module: str) -> list[dict[str, Any]] | None:
    """The frozen advice params on the module's currently-OPEN advised positions.

    Lives here rather than in `enactment.py` because this is a live read of another package's
    ledger, and every one of those is fenced into this file by the package contract. `enactment`
    forms the verdict; this only reports what the rows say.

    Three distinguishable answers, and the distinction is the point:

    * ``None`` — the module's ledger declares no ``advice_params`` column anywhere, so it CANNOT
      carry advice across sessions. A missing decision there is a real miss (meic and flies are
      flat overnight and decide every session).
    * ``[]`` — it can carry and currently carries nothing.
    * a list of param dicts — advice frozen at entry is still governing open positions.

    The table is DISCOVERED from the schema rather than named in a table here: a module that starts
    freezing advice on its rows is covered the moment it declares the column, and one that stops is
    uncovered the moment it drops it. A hand-kept list would be a second declaration free to drift
    from the ledger it describes.
    """

    def read(conn):
        table = None
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            name = row[0]
            columns = {c[1] for c in conn.execute(f"PRAGMA table_info({name})")}
            if {"advice_params", "status"} <= columns:
                table = name
                break
        if table is None:
            return None
        out: list[dict[str, Any]] = []
        for row in conn.execute(
            f"SELECT DISTINCT advice_params FROM {table}"  # noqa: S608 - name from sqlite_master
            " WHERE advice_params IS NOT NULL AND status != 'closed'"
        ):
            try:
                params = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
            except (TypeError, ValueError):
                continue  # an unreadable stamp proves nothing either way; it is not evidence
            if isinstance(params, dict) and params not in out:
                out.append(params)
        return out

    return _read(_paper_db(module), read)


def closed_advice_params(module: str, session: str) -> list[dict[str, Any]] | None:
    """The frozen advice params on advised positions this module CLOSED during `session`.

    `carried_advice_params`'s sibling, for the morning that motivated it (2026-09-01): earnings
    closed 13 advised iron condors at 09:45, all managed under the params their entry had frozen,
    then held nothing — so the open-row read said "carries nothing" and the enactment check warned
    hourly about an artifact that was in force for every decision the module actually made.

    Same discovery, same three answers as the open read (None = cannot stamp; [] = stamps but
    closed nothing advised this session; a list = the frozen params those exits ran under). The
    close-date column is discovered from the schema too: `closed_session` where the table declares
    one, else epoch `closed_at` read with 'localtime' (earnings' convention — a bare 'unixepoch'
    is UTC and shifts evening closes into the wrong session). A table that dates its closes no way
    at all returns [] — an undated close proves nothing about any particular session.
    """

    def read(conn):
        table, columns = None, set()
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            name = row[0]
            cols = {c[1] for c in conn.execute(f"PRAGMA table_info({name})")}
            if {"advice_params", "status"} <= cols:
                table, columns = name, cols
                break
        if table is None:
            return None
        if "closed_session" in columns:
            where = "closed_session = ?"
        elif "closed_at" in columns:
            where = "date(closed_at, 'unixepoch', 'localtime') = ?"
        else:
            return []
        out: list[dict[str, Any]] = []
        for row in conn.execute(
            f"SELECT DISTINCT advice_params FROM {table}"  # noqa: S608 - name from sqlite_master
            f" WHERE advice_params IS NOT NULL AND status = 'closed' AND {where}",
            (session,),
        ):
            try:
                params = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
            except (TypeError, ValueError):
                continue  # an unreadable stamp proves nothing either way; it is not evidence
            if isinstance(params, dict) and params not in out:
                out.append(params)
        return out

    return _read(_paper_db(module), read)


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
            conn,
            "SELECT vix, vix1d, vix1d_ratio FROM market_context WHERE context_date = ?",
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
        "regime": _regime_now(session, fallback=lambda: _read(_paper_db("meic"), vix)),
        "gex": _read(_gex_history(), gex),
        "day_range": _read(_stream_cache(), day_range) or [],
        "_note": "day_range is today's rows only; a stale row is omitted rather than relabeled",
    }


def _regime_now(session: str, *, fallback) -> dict[str, Any]:
    """The market regime at fact-pack time, from the suite's canonical series.

    Replaces this pack's old habit of reading MEIC's private `market_context` table — a cross-module
    scrape that worked only because MEIC happened to record what the advisor happened to need, and
    that carried VIX alone. `cherrypick.core.regime.regime_at` is the one join every consumer uses,
    so the pack, the console and any read-side analysis now describe the same moment the same way,
    and it carries the whole complex: the vol curve, breadth, credit, the commodity pair, the VIX
    futures term structure, and the per-symbol chain measures.

    ONE regime block, never two sources side by side. A fact pack that showed a canonical VIX beside
    a scraped one would invite exactly the contradiction the GEX counts note below records the model
    chasing for six sessions. When the series is unmeasured — a recorder outage, or a checkpoint
    outside RTH when nothing is sampled — this falls back to the old reading and SAYS SO in
    `source`, rather than presenting a hole as a calm market.
    """
    try:
        out = _regime.regime_at(_clock.now_et().timestamp())
        market = out.get("market") or {}
    except Exception:  # noqa: BLE001 — a fact pack must never fail on a telemetry read
        market = {"status": "unmeasured", "reason": "regime_read_failed"}
    if market.get("status") == "measured":
        readings = {
            name: r.get("value") for name, r in (market.get("readings") or {}).items() if r.get("usable")
        }
        refused = sorted(name for name, r in (market.get("readings") or {}).items() if not r.get("usable"))
        return {
            "source": "market_regime_history (canonical)",
            "age_seconds": market.get("age_seconds"),
            "readings": readings,
            "derived": market.get("derived") or {},
            "chain": market.get("chain") or {},
            "refused": refused,
            "_refused_note": "readings the recorder marked unusable at that sample — a stale or "
            "missing quote, never a value it guessed.",
        }
    return {
        "source": "meic.market_context (fallback)",
        "reason": market.get("reason"),
        "readings": fallback() or {},
        "_fallback_note": "the canonical series had no usable sample at this moment, so this is "
        "the older single-source read; it carries VIX only.",
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
        # The advised-twin pairing, MEASURED — because unstated it reads as a defect. An advised
        # entry mirrors its control's fills by design: identical entry_credit and capital, its own
        # order_id ('advised-' + the control's), divergent management thereafter. The pack carried
        # none of that, so the model flagged the pairs as "double-tagging" twice (#94 on 08-31,
        # #103 on 09-01) and reasoned that their delta contribution was zero by construction —
        # which is wrong precisely because the twins are managed separately (the 13-vs-1 close
        # split of 09-01 was the unmanaged-twin backlog of 08-26→08-31 flushing through the fixed
        # loop, not doubled rows). `paired` is the by-design case; `unpaired_advised` — a twin
        # whose control row is missing under the stripped id — is the actual defect to flag.
        twins = _store.rows(
            conn,
            "SELECT COUNT(*) advised_rows,"
            " SUM(CASE WHEN c.order_id IS NOT NULL THEN 1 ELSE 0 END) paired,"
            " SUM(CASE WHEN c.order_id IS NULL THEN 1 ELSE 0 END) unpaired_advised"
            " FROM trades a LEFT JOIN trades c ON c.order_id = substr(a.order_id, 9)"
            " WHERE a.order_id LIKE 'advised-%'",
        )
        return {
            "scans": scans,
            "top_reject_reasons": rejects,
            "open_positions": open_rows,
            "closed_today": closed,
            "loop_health": health,
            "management_events": events,
            "advised_twins": dict(twins[0]) if twins else None,
            "_twin_note": (
                "an advised row sharing entry_credit/capital with a control row under the same "
                "stripped order_id is the PAIRED-TWIN DESIGN (identical fills, divergent "
                "management), not double-tagging; only unpaired_advised > 0 is a defect. "
                "management_events carry a `profile` stamp from 2026-09-01 (NULL before), so "
                "whether an advised exit param fired is answerable from that table directly."
            ),
        }

    out = _read(_paper_db("earnings"), read) or {"_absent": "no earnings paper ledger"}
    out["advice_active"] = _store.read_json(_advice_active("earnings"), default=None)
    return out


def _mark_coverage(conn, table: str, session: str) -> dict[str, Any]:
    """Usable-vs-refused mark counts for one session, in words rather than in a flag column.

    This was `SELECT usable, COUNT(*) n ... GROUP BY usable`, which serialises as
    `[{"usable": 0, "n": 1}, {"usable": 1, "n": 2201}]` -- and on 2026-08-24 the advisor read that
    as "usable 1 of 664" and concluded, in a proposal, that 663 of 664 marks were unusable and that
    "the assignment-exposure metric every pmcc experiment is scored on" had a meaningless
    denominator. The true figure was 664 of 664 usable. A boolean flag sitting next to a count is
    two numbers that look like one ratio, and a reader that misreads it does not misread it
    slightly -- it inverts the finding and proposes rebuilding something that works.

    So the pack states the denominator, the numerator, and the refusal reasons by name.
    """
    total = 0
    usable = 0
    for row in _store.rows(
        conn,
        f"SELECT usable, COUNT(*) n FROM {table} WHERE session_date = ? GROUP BY usable",
        (session,),
    ):
        total += row["n"]
        if row["usable"]:
            usable += row["n"]
    return {
        "marks_total": total,
        "marks_usable": usable,
        "marks_refused": total - usable,
        "usable_fraction": round(usable / total, 4) if total else None,
        "refusals_by_reason": _store.rows(
            conn,
            f"SELECT refusal, COUNT(*) n FROM {table} WHERE session_date = ? AND usable = 0"
            " GROUP BY refusal ORDER BY n DESC",
            (session,),
        ),
    }


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
        marks = _mark_coverage(conn, "dc_marks", session)
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
            " , p.era FROM pmcc_positions p WHERE p.status != 'closed' ORDER BY p.book, p.symbol",
        )
        # GROUPED BY ERA, and that is the point rather than an extra column. pmcc's 2026-08-23
        # redesign cut the module from three books to one; the four pre-redesign rows in this
        # ledger are ONE TQQQ trade recorded four times, once per retired book, with identical
        # economics. Pooled with a redesign-era row they read as four independent observations, and
        # on 2026-08-24 the advisor read exactly that and proposed rebuilding a book structure that
        # was already correct -- "one trade, four identical rows... a module net of four rows
        # overstates the evidence fourfold". The rows were real; the pooling was the defect. The
        # module's own analytics.py has defaulted to CURRENT_ERA since the redesign; this pack was
        # the one reader that did not.
        closed = _store.rows(
            conn,
            "SELECT COALESCE(era, '(pre-redesign)') era, book, symbol, exit_reason, COUNT(*) n,"
            " SUM(gross_pnl) gross, SUM(fees) fees, SUM(roll_count) rolls FROM pmcc_positions"
            " WHERE status = 'closed' GROUP BY era, book, symbol, exit_reason ORDER BY era, book",
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
        marks = _mark_coverage(conn, "pmcc_marks", session)
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


def _bwb(session: str) -> dict[str, Any]:
    """SPX daily-laddered put BWB. Four books trade the IDENTICAL base structure and differ only in
    whether, and on what trigger, a reversal add-on fires — so the advisable surface is the trigger
    thresholds and the entry geometry, never the base fly, and the book contrast is the experiment
    rather than something to average.

    `trigger_ticks` is the module's declared second product: a cohort-keyed path recorded every
    session for a future read-side threshold replay. It is carried here because a session with
    positions and no trigger ticks means that replay will have nothing to score, which is a fact
    about the evidence rather than about the trades.
    """

    def read(conn):
        open_rows = _store.rows(
            conn,
            "SELECT position_id, book, symbol, entry_session, body_strike, near_strike, far_strike,"
            " entry_credit, entry_max_loss, entry_dte, expiration, peak_abs_delta, below_flip_seen,"
            " armed_at, arm_reason, addon_fired_at, addon_credit, status"
            " FROM bwb_positions WHERE status != 'closed' ORDER BY book, entry_session",
        )
        closed = _store.rows(
            conn,
            "SELECT book, exit_reason, COUNT(*) n, SUM(gross_pnl) gross, SUM(fees) fees"
            " FROM bwb_positions WHERE status = 'closed' GROUP BY book, exit_reason ORDER BY book",
        )
        attempts = _store.rows(
            conn,
            "SELECT book, outcome, block_detail, COUNT(*) n FROM bwb_entry_attempts"
            " WHERE trade_date = ? GROUP BY book, outcome, block_detail ORDER BY n DESC LIMIT ?",
            (session, TOP_N),
        )
        events = _store.rows(
            conn,
            "SELECT action, reason, executed, gate, COUNT(*) n FROM bwb_management_events"
            " WHERE session_date = ? GROUP BY action, reason, executed, gate ORDER BY n DESC LIMIT ?",
            (session, TOP_N),
        )
        triggers = _store.rows(
            conn,
            "SELECT structure_signature, COUNT(*) ticks, MAX(peak_abs_delta) peak_abs_delta"
            " FROM bwb_trigger_ticks WHERE session_date = ? GROUP BY structure_signature",
            (session,),
        )
        marks = _mark_coverage(conn, "bwb_marks", session)
        # Which books exist OUTSIDE the four-book contrast. The 'wall' book (added 2026-08-31 by
        # config, opt-in: a call-side BWB at the GEX call wall) trades a DIFFERENT structure by
        # its own declaration — but the pack's note still said "the four books trade the identical
        # base fly", so the model read wall's rows as the contrast silently breaking (#101).
        # Derived from the rows, not a list: a book is non-base the moment it appears.
        base_books = {"control", "delta", "bounce", "flip"}
        seen = _store.rows(conn, "SELECT DISTINCT book FROM bwb_positions ORDER BY book")
        non_base = [
            r["book"]
            for r in seen
            if r["book"] not in base_books and not str(r["book"]).startswith("advised:")
        ]
        return {
            "open_positions": open_rows,
            "closed_by_exit_reason": closed,
            "entry_attempts": attempts,
            "management_events": events,
            "trigger_ticks": triggers,
            "mark_coverage": marks,
            "non_base_books": non_base,
            "_note": (
                "the four BASE books (control/delta/bounce/flip) trade the IDENTICAL base fly and "
                "differ only in the add-on trigger — their contrast is the experiment, and only "
                "they (plus advised:*) belong in it. Any book named in non_base_books (e.g. "
                "'wall', added 2026-08-31: an opt-in call-side BWB at the GEX call wall) is a "
                "SEPARATE structure by the module's own config declaration — never pool it into "
                "the contrast or a module net, and do not read its rows as the contrast breaking. "
                "The advisable surface is the trigger thresholds and entry geometry, not the base "
                "structure. SPX is cash-settled and European, so settlement carries no assignment "
                "model to caveat"
            ),
        }

    out = _read(_paper_db("bwb"), read) or {"_absent": "no bwb paper ledger"}
    out["advice_active"] = _store.read_json(_advice_active("bwb"), default=None)
    return out


def _curve(session: str) -> dict[str, Any]:
    """VXX call-credit-spread module gated on a daily VIX/VIX3M regime read.

    Its declared SECOND PRODUCT is that regime classification, recorded every session whether or not
    anything traded — so `regime_readings` is carried even when the book is empty, which it has been
    so far. A session with no reading is a gap in the module's own record rather than a quiet market,
    and entries are gated on the classification, so zero attempts beside zero readings is one fault
    rather than two.
    """

    def read(conn):
        open_rows = _store.rows(
            conn,
            "SELECT position_id, book, symbol, entry_session, short_strike, long_strike,"
            " expiration, status FROM curve_positions WHERE status != 'closed' ORDER BY book",
        )
        closed = _store.rows(
            conn,
            "SELECT book, exit_reason, COUNT(*) n, SUM(gross_pnl) gross, SUM(fees) fees"
            " FROM curve_positions WHERE status = 'closed' GROUP BY book, exit_reason ORDER BY book",
        )
        attempts = _store.rows(
            conn,
            "SELECT book, outcome, block_detail, COUNT(*) n FROM curve_entry_attempts"
            " WHERE trade_date = ? GROUP BY book, outcome, block_detail ORDER BY n DESC LIMIT ?",
            (session, TOP_N),
        )
        regime = _store.rows(
            conn,
            "SELECT tick, ratio, regime, hook, vix, vix3m FROM curve_regime"
            " WHERE trade_date = ? ORDER BY tick DESC LIMIT ?",
            (session, TOP_N),
        )
        marks = _mark_coverage(conn, "curve_marks", session)
        return {
            "open_positions": open_rows,
            "closed_by_exit_reason": closed,
            "entry_attempts": attempts,
            "regime_readings": regime,
            "mark_coverage": marks,
            "_note": (
                "control and noflip are byte-identical by construction until a regime flip fires, "
                "so a difference between them IS a flip and nothing else; the daily regime read is "
                "recorded traded or not, and is the module's second product. Early assignment and "
                "VXX reverse splits are measured, never modelled — treat paper net as an upper bound"
            ),
        }

    out = _read(_paper_db("curve"), read) or {"_absent": "no curve paper ledger"}
    out["advice_active"] = _store.read_json(_advice_active("curve"), default=None)
    return out


_MODULE_SECTIONS = {
    "meic": _meic,
    "flies": _flies,
    "earnings": _earnings,
    "calendars": _calendars,
    "pmcc": _pmcc,
    "bwb": _bwb,
    "curve": _curve,
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
            "SELECT COUNT(*) n, SUM(pnl) pnl FROM fly_positions WHERE trade_date = ? AND status = 'settled'",
            (session,),
        )
        return {
            "by_status": today,
            "entry_fills": _counts(fills, "entry_fill_status"),
            "settled_today": settled[0] if settled else None,
        }

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
        out.append(
            {
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
            }
        )
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


def _journal_proposal(row, *, full: bool) -> dict[str, Any]:
    """One journal proposal, in full or as its identity.

    The identity form keeps what "do not re-propose what was dismissed" needs — which module, what
    kind, what became of it, and the title that names the idea — and drops the body. A creative
    proposal's body runs several thousand characters of argument, and ten sessions of them is what
    took this section past the whole pack's budget.
    """
    payload = json.loads(row["payload_json"] or "{}")
    base = {
        "session": row["session"],
        "module": row["module"],
        "kind": row["kind"],
        "fate": row["status"],
        "reason": row["reject_reason"],
    }
    if full:
        return {**base, "payload": payload}
    # `title` for a creative proposal, `name` for an experiment spec, and the experiment id for a
    # verdict — each kind's own one-line identity, so none of them degrades to an anonymous row.
    return {
        **base,
        "title": payload.get("title") or payload.get("name") or payload.get("experiment_id"),
        "_elided": "older than the full-detail window; ask if the argument matters",
    }


def _journal_flags(flags_json: str | None, *, full: bool) -> dict[str, Any]:
    """One checkpoint's flags, tapered by SEVERITY once outside the full-detail window.

    Flags were carried verbatim at every age until 2026-08-26, on the reasoning that a flag is a
    standing caveat about a module. That reasoning is right about `critical` and wrong about the
    rest, and the cost of not separating them was the single largest item in the pack: 97.6KB of
    120KB of `checkpoints`, and 21% of the whole 472KB deep pack. The taper added the same day cut
    observations instead — the *small* half, 12.3KB — because nobody had measured which was which.

    Two things were checked before writing this rather than assumed. The flags are not repetition:
    222 instances across the window are 222 DISTINCT texts, so deduplicating them would have saved
    nothing (the first fix attempted here, and the measurement is why it was not the one shipped).
    And the severity split is lopsided in the useful direction — 12 `critical` flags cost 8.3KB,
    while 113 `warn` and 97 `info` cost 86.5KB.

    So `critical` survives at any age, verbatim. An aged `warn`/`info` keeps its module, severity
    and a truncated text — enough to know the caveat existed and ask for it — and drops the prose.
    """
    flags = json.loads(flags_json or "[]")
    if full:
        return {"flags": flags}
    kept, elided = [], 0
    for f in flags:
        if not isinstance(f, dict) or f.get("severity") in JOURNAL_STANDING_SEVERITIES:
            kept.append(f)
            continue
        elided += 1
        # No per-flag `_elided` marker: `advisor_journal._taper` states the rule once for the whole
        # section, and repeating the sentence on each of ~210 aged flags costs ~13KB to say a thing
        # already said. The truncated text is its own evidence that it was truncated.
        kept.append(
            {
                "module": f.get("module"),
                "severity": f.get("severity"),
                "text": (f.get("text") or "")[:120],
            }
        )
    return {"flags": kept} if not elided else {"flags": kept, "flags_elided": elided}


def _advisor_journal(conn, session: str) -> dict[str, Any]:
    """The advisor's own memory: what it has recently observed, proposed, and been told.

    Without this the model re-proposes the idea a human dismissed last Tuesday, every Tuesday. With
    it, a dismissal is a fact in evidence and a thread can build across sessions. Compact and
    capped, oldest first.

    **Tapered by age since 2026-08-26, and that is a change to what the model reads.** The window is
    and was ten sessions; what grew was the prose per session — a creative proposal runs ~7.7KB and
    ten sessions of them, carried verbatim, made this section 466KB of a 690KB pack against a stated
    ceiling of 150KB. The pack had grown from 250KB to 731KB in nine sessions and nothing caught it,
    because the budget test runs against a fixture rather than the real thing.

    So the recent sessions keep their full payloads and the older ones keep their IDENTITY —
    title, module, kind, fate, reason. That is what the section is actually for: "do not re-propose
    what was dismissed" needs the title and the fate, not the argument. The argument was written by
    the model that is reading it, and in practice it restates its own history in new proposals
    anyway ("I proposed this on 08-18, 08-20 and 08-21"), so the verbatim text is partly redundant
    with what the next proposal will say regardless.

    **That first taper cut the wrong half, and the correction is `_journal_flags`.** It exempted
    flags at every age and trimmed observations, which sounded right and was never measured: flags
    were 97.6KB of this section's 120KB and observations were 12.3KB. Flags now taper by severity —
    `critical` verbatim at any age, `warn`/`info` to identity outside the window. See that function
    for what was measured before it was written, including the dedup fix that was NOT shipped.
    """
    sessions = [session, *_clock.previous_sessions(session, JOURNAL_SESSIONS)]
    oldest = min(sessions)
    full_sessions = set(sorted(sessions)[-JOURNAL_FULL_SESSIONS:])
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
    return {
        "_note": "your own recent history. Do not re-propose what was dismissed; build on threads.",
        "checkpoints": [
            {
                "session": r["session"],
                "slot": r["slot"],
                "ok": bool(r["ok"]),
                **(
                    {"observations": json.loads(r["observations_json"] or "[]")}
                    if r["session"] in full_sessions
                    else {"observations_count": len(json.loads(r["observations_json"] or "[]"))}
                ),
                **_journal_flags(r["flags_json"], full=r["session"] in full_sessions),
            }
            for r in checkpoints
        ],
        "_taper": (
            f"proposals, observations and flags are carried in full for the most recent "
            f"{JOURNAL_FULL_SESSIONS} sessions; older entries keep title/module/kind/fate/reason "
            f"only. CRITICAL flags are the exception and are carried verbatim at every age, because "
            f"a critical flag is a standing caveat rather than history. Ask if you need an older "
            f"argument in full rather than assuming it was thin."
        ),
        "proposals": [_journal_proposal(r, full=r["session"] in full_sessions) for r in proposals],
        # Concluded experiments are NOT repeated here. They are carried once, in full, by
        # `experiments_full.concluded` in the deep pack — the same seven experiments were appearing
        # twice, 34KB and 17KB, saying the same thing in two shapes.
        "_concluded_experiments": "see experiments_full.concluded (deep slot); not duplicated here",
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
        out.append(
            {
                "session": prior,
                "status": facts.get("status"),
                "modules": {
                    name: {k: v for k, v in (block or {}).items() if k in ("net_pnl", "trades", "by_profile")}
                    for name, block in (facts.get("modules") or {}).items()
                },
            }
        )
    return out


def _settlement_integrity(session: str) -> dict[str, Any]:
    """Two settlement facts about meic's ledger that a reader needs before trusting an arm reading.

    The advisor asked for a full settlement audit on 2026-08-17, 08-18, 08-19, 08-20 and 08-21,
    escalating each time, and finally noted it was "upstream of the era's baseline rather than
    upstream of one arm". That audit was run on 2026-08-26 and lives in
    `meic.analytics.settlement_audit`: 7,908 of 7,908 resolved fills reproduce exactly from the
    stated convention, one settlement price per session throughout, and the sensitivity of each
    session's net to the price is bounded there.

    What is carried HERE is only the part that can recur, because a settled question does not need
    re-reporting every evening and this pack is paid for by the token:

    * `settlement_prices_today` must be 1. Every fill on one session and symbol shares an
      expiration and a settlement; two prices means the loop settled across iterations at drifting
      spot, and no same-session arm comparison survives that.
    * `settled_with_no_price_today` must be 0. A side that reached expiry with no settlement price
      was scored at zero intrinsic — full credit, the most favorable outcome available, on exactly
      the fills whose outcome nobody could see. `paper.evaluate_open_trade` refuses to settle
      without a price as of 2026-08-26; a non-zero count here means that guard has regressed.

    Deliberately a query rather than a call into `meic.analytics`: this package depends on
    `cherrypick.core` alone, the same reason the module sections below re-state their queries.
    """

    def read(conn):
        prices = _store.rows(
            conn,
            "SELECT symbol, COUNT(DISTINCT settle_underlying) n FROM ic_trades"
            " WHERE trade_date = ? AND settle_underlying IS NOT NULL GROUP BY symbol",
            (session,),
        )
        unpriced = _store.rows(
            conn,
            "SELECT COUNT(*) n FROM ic_trades WHERE trade_date = ?"
            " AND settle_underlying IS NULL AND exit_reason LIKE '%expired_settlement%'"
            " AND (put_stop_cost IS NULL OR call_stop_cost IS NULL)",
            (session,),
        )
        return {
            "settlement_prices_today": {r["symbol"]: r["n"] for r in prices},
            "settled_with_no_price_today": (unpriced[0]["n"] if unpriced else 0),
            "_note": (
                "prices per symbol must be 1 and no-price settlements must be 0; the full audit "
                "was run 2026-08-26 and lives in meic.analytics.settlement_audit"
            ),
        }

    return _read(_paper_db("meic"), read) or {"_absent": "no meic paper ledger"}


# Fixed binding-margin buckets (points). Fixed rather than the proposal's deciles, deliberately:
# at ~60 recorded books a decile holds six, and the bucket edges would move every session, so the
# same book would migrate between rows and the trend would describe the bucketing. Revisit at ~200.
_BAND_MARGIN_BUCKETS = ((float("-inf"), 0.0), (0.0, 10.0), (10.0, 25.0), (25.0, 50.0), (50.0, float("inf")))


def _flies_band_containment() -> dict[str, Any]:
    """Band placement vs the session's realized range, over every flies book ever recorded.

    The advisor requested exactly this derivation (proposal #102, 2026-09-01) after reporting the
    same finding on six consecutive sessions in exp-2026-08-20-flies-1's SECONDARY metric: whether
    a book's floor held has been classified perfectly by whether the session's realized range
    stayed inside the book's band — across the upper edge (08-17 through 08-31) and, on 09-01, the
    lower edge (advised:control's band_low sat 12.80 points above the session low; floor_holds 0,
    worst −1,171.34, while control's band_low cleared the low by 31.20 and held at +143.63).

    Day ranges come from the gex spot-trail (min/max recorded SPX spot per session) — the same
    recorded series the suite's own gates read, never a live fetch. A book whose session has no
    recorded range is counted `unmatched` and excluded rather than guessed. SPX-only, which today
    is all of flies.

    The `forecasts` block answers the proposal's second question — is the range forecastable at
    entry, so a containment rule could actually be implemented — for the two candidates the
    suite's own stores can answer: the trailing realized range and the morning GEX walls, each
    scored on whether its band contained the session's realized range. The proposal's third
    candidate (vix1d_implied) reads the gex recorder's own `market_regime_history` (the first
    usable VIX1D reading of the session) against the prior SPX close from `daily_closes` — the
    first cut of this derivation said no VIX1D series existed, which was wrong: the recorder has
    carried it since 2026-08-24.
    """

    def books(conn):
        return _store.rows(
            conn,
            "SELECT trade_date, arm, band_low, band_high, worst, floor_holds FROM fly_books"
            " WHERE band_low IS NOT NULL AND band_high IS NOT NULL AND floor_holds IS NOT NULL"
            " ORDER BY trade_date",
        )

    def ranges(conn):
        return _store.rows(
            conn,
            "SELECT trade_date, MIN(spot) lo, MAX(spot) hi, COUNT(*) n FROM gex_regime_history"
            " WHERE symbol = 'SPX' AND spot IS NOT NULL GROUP BY trade_date ORDER BY trade_date",
        )

    def walls(conn):
        return _store.rows(
            conn,
            "SELECT g.trade_date, g.spot open_spot, g.put_wall, g.call_wall FROM gex_regime_history g"
            " WHERE g.symbol = 'SPX' AND g.ts = (SELECT MIN(ts) FROM gex_regime_history"
            "  WHERE symbol = 'SPX' AND trade_date = g.trade_date)",
        )

    def vix1d(conn):
        return _store.rows(
            conn,
            "SELECT m.trade_date, m.value FROM market_regime_history m"
            " WHERE m.reading = 'vix1d' AND m.usable = 1 AND m.ts = (SELECT MIN(ts) FROM"
            "  market_regime_history WHERE reading = 'vix1d' AND usable = 1 AND trade_date = m.trade_date)",
        )

    def closes(conn):
        return _store.rows(
            conn, "SELECT trade_date, close FROM daily_closes WHERE symbol = 'SPX' ORDER BY trade_date"
        )

    rows = _read(_paper_db("flies"), books) or []
    day = {r["trade_date"]: r for r in (_read(_gex_history(), ranges) or [])}
    first = {r["trade_date"]: r for r in (_read(_gex_history(), walls) or [])}
    vix1d_open = {r["trade_date"]: float(r["value"]) for r in (_read(_gex_history(), vix1d) or [])}
    close_by_date = {r["trade_date"]: float(r["close"]) for r in (_read(_gex_history(), closes) or [])}
    if not rows:
        return {"_absent": "no flies books recorded"}

    def bucket_label(margin: float) -> str:
        for lo, hi in _BAND_MARGIN_BUCKETS:
            if lo <= margin < hi:
                lo_s = "-inf" if lo == float("-inf") else f"{lo:.0f}"
                hi_s = "inf" if hi == float("inf") else f"{hi:.0f}"
                return f"[{lo_s},{hi_s})"
        return "?"

    scored: list[dict[str, Any]] = []
    unmatched = 0
    for r in rows:
        rng = day.get(r["trade_date"])
        if rng is None:
            unmatched += 1
            continue
        lower = round(float(rng["lo"]) - float(r["band_low"]), 2)  # positive = band clears the low
        upper = round(float(r["band_high"]) - float(rng["hi"]), 2)  # positive = band clears the high
        binding = min(lower, upper)
        scored.append(
            {
                "arm": r["arm"],
                "held": bool(r["floor_holds"]),
                "worst": r["worst"],
                "binding_margin": binding,
                "binding_edge": "lower" if lower <= upper else "upper",
            }
        )

    def rate(rows_: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(rows_)
        held = sum(1 for s in rows_ if s["held"])
        worsts = [s["worst"] for s in rows_ if s["worst"] is not None]
        return {
            "n": n,
            "floor_holds_rate": round(held / n, 4) if n else None,
            "mean_worst": round(sum(worsts) / len(worsts), 2) if worsts else None,
            "min_worst": round(min(worsts), 2) if worsts else None,
        }

    by_bucket = []
    for lo, hi in _BAND_MARGIN_BUCKETS:
        members = [s for s in scored if lo <= s["binding_margin"] < hi]
        if members:
            by_bucket.append({"bucket": bucket_label(members[0]["binding_margin"]), **rate(members)})
    breached = [s for s in scored if s["binding_margin"] < 0]
    contained = [s for s in scored if s["binding_margin"] >= 0]

    # Forecast scoring: for each session with a recorded range, did the candidate band contain it?
    sessions = sorted(day)
    trailing_n = 5
    trailing_hits, trailing_scored = 0, 0
    wall_hits, wall_scored = 0, 0
    vix_hits, vix_scored = 0, 0
    for i, s in enumerate(sessions):
        rng = day[s]
        realized = float(rng["hi"]) - float(rng["lo"])
        if i >= trailing_n:
            mean_width = sum(float(day[p]["hi"]) - float(day[p]["lo"]) for p in sessions[i - trailing_n : i])
            mean_width /= trailing_n
            f = first.get(s)
            center = float(f["open_spot"]) if f and f.get("open_spot") is not None else None
            if center is not None:
                trailing_scored += 1
                if center - mean_width / 2 <= float(rng["lo"]) and center + mean_width / 2 >= float(
                    rng["hi"]
                ):
                    trailing_hits += 1
        f = first.get(s)
        if f and f.get("put_wall") is not None and f.get("call_wall") is not None:
            wall_scored += 1
            if float(f["put_wall"]) <= float(rng["lo"]) and float(f["call_wall"]) >= float(rng["hi"]):
                wall_hits += 1
        # vix1d_implied: prev_close * vix1d/100 / sqrt(252) as a symmetric band around the open.
        v = vix1d_open.get(s)
        prev_close = close_by_date.get(max((d for d in close_by_date if d < s), default=""))
        center = float(f["open_spot"]) if f and f.get("open_spot") is not None else None
        if v is not None and prev_close and center is not None:
            half = prev_close * v / 100.0 / (252**0.5)
            vix_scored += 1
            if center - half <= float(rng["lo"]) and center + half >= float(rng["hi"]):
                vix_hits += 1
        _ = realized

    return {
        "books_scored": len(scored),
        "books_unmatched": unmatched,
        "by_binding_margin": by_bucket,
        "by_edge": {
            edge: rate([s for s in scored if s["binding_edge"] == edge]) for edge in ("lower", "upper")
        },
        "by_arm": {
            arm: {
                "breached": rate([s for s in scored if s["arm"] == arm and s["binding_margin"] < 0]),
                "contained": rate([s for s in scored if s["arm"] == arm and s["binding_margin"] >= 0]),
            }
            for arm in sorted({s["arm"] for s in scored})
        },
        "separation": {
            "breached": rate(breached),
            "contained": rate(contained),
            "_reading": "the proposal's claim is that these two hold rates separate cleanly",
        },
        "forecasts": {
            "trailing_realized_5": {
                "hit_rate": round(trailing_hits / trailing_scored, 4) if trailing_scored else None,
                "n": trailing_scored,
                "_basis": "band = first recorded spot +/- half the mean of the prior 5 realized ranges",
            },
            "gex_walls": {
                "hit_rate": round(wall_hits / wall_scored, 4) if wall_scored else None,
                "n": wall_scored,
                "_basis": "band = the session's first recorded put_wall..call_wall",
            },
            "vix1d_implied": {
                "hit_rate": round(vix_hits / vix_scored, 4) if vix_scored else None,
                "n": vix_scored,
                "_basis": (
                    "band = first recorded spot +/- prev_close x (first usable VIX1D of the session)"
                    "/100/sqrt(252)"
                ),
            },
        },
    }


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
    """The last artifact written per module, including its rejections and whether it was APPLIED.

    Reject-all is silent from the loop's side (it just runs baseline), so this is where a proposal
    refused at the gate becomes visible. `enacted` is the other half, and until 2026-08-25 it was
    missing: an artifact that was written and never reached its loop looked, from here, exactly like
    one that was written and applied. It is not a cosmetic distinction -- five artifacts were issued
    in one batch on 2026-08-24 and two were dropped, on the two sessions their experiments most
    needed, and nothing in this pack said so.
    """
    out = {}
    enacted = _enactment.audit(session, MODULES)
    for module in MODULES:
        artifact = _store.read_json(_paths.advice_path(module, session), default=None)
        upcoming = _store.read_json(_paths.advice_path(module, _clock.next_session(session)), default=None)
        out[module] = {
            "for_today": artifact,
            "for_next_session": upcoming,
            "enacted": enacted[module],
        }
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
            # Every slot, not just the evening one: an artifact that was dropped is worth knowing
            # about at 10am, while the session can still be understood, rather than in the verdict
            # that scores it. Compact here; the full reconciliation rides on advice_audit below.
            "advice_enacted": {
                m: {
                    k: v
                    for k, v in _enactment.reconcile(m, session).items()
                    if k in ("status", "detail", "experiment_id")
                }
                for m in selected
                if m in _enactment.MODULES
            },
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
            # Today's drafts are already in `advisor_journal.proposals` in full — the current
            # session is inside the full-detail window — so carrying them here too was the same
            # rows twice, 31KB of a pack already past its ceiling. The light slots keep the real
            # section: they have no journal, and compounding earlier slots' output is the whole
            # reason it exists.
            pack["pending_proposals"] = "see advisor_journal.proposals (this session, in full)"
            pack["review_today"] = _review_today(session)
            pack["review_trend"] = _review_trend(session)
            pack["arm_readings"] = _arm_readings()
            pack["settlement_integrity"] = _settlement_integrity(session)
            # The derivation the advisor requested in proposal #102 (2026-09-01): band placement vs
            # realized range over every flies book, nightly. Deep-only — it aggregates all history
            # and the light slots have no verdict to inform with it.
            if "flies" in selected:
                pack["flies_band_containment"] = _flies_band_containment()
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


def pack_size(pack: dict, slot: str, *, warn=None) -> dict[str, Any]:
    """Measured size against the ceiling for this slot. Reports; never raises.

    Separated from `write` so a caller can ask the question without producing a pack, and so the
    check is testable against a real pack rather than only a fixture.

    Serialised the way `store.write_json` serialises it — indent=2 — because that is the file the
    checkpoint script reads and hands to the model. A compact measure understates the real cost by
    about a quarter (366KB against 472KB on the 2026-08-26 deep pack), and the number that matters
    is the one the model is actually charged for.
    """
    size = len(json.dumps(pack, indent=2, default=str))
    ceiling = DEEP_MAX_BYTES if slot == DEEP_SLOT else LIGHT_MAX_BYTES
    over = size > ceiling
    if over and warn is not None:
        warn(
            f"fact pack over its ATTENTION budget: {slot} slot is {size:,} bytes against a "
            f"{ceiling:,} ceiling ({size / ceiling:.1f}x). This is not a context-window or cost "
            f"problem — the pack is well inside the window and the whole schedule costs about "
            f"$1.40/day — it is that a finding buried in a pack this size is one nobody acts on. "
            f"Cut the largest section; do not raise the ceiling."
        )
    return {
        "slot": slot,
        "bytes": size,
        "ceiling": ceiling,
        "over_budget": over,
        "ratio": round(size / ceiling, 2),
    }


def write(session: str, slot: str, modules: tuple[str, ...] | list[str] | None = None) -> Path:
    """Build and persist the pack. Write-once by convention: the pack is the record of what the
    model was shown, so a re-run (`--force`) deliberately overwrites the whole (session, slot)
    triple — pack, raw reply and checkpoint — rather than leaving a pack that describes a different
    reply than the one on file.

    `build` stays pure; this is the write path, so the enactment reconciliation is persisted here.
    It runs every slot, which is the point: a dropped artifact should be visible on the console at
    10am while the session can still be understood, not in the verdict that scores it that evening.
    """
    pack = build(session, slot, modules)
    path = _store.write_json(_paths.pack_path(session, slot), pack)
    conn = _store.connect()
    try:
        _enactment.record(conn, session, modules)
    finally:
        conn.close()
    return path
