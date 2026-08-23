"""Per-schema readers for every module's paper/live ledger — the one place the net, cost,
capital and session rules live.

Each trading module keeps its own SQLite ledger with its own shape, so anything that wants to
compare them has to normalise first. That normalisation is load-bearing and easy to get subtly
wrong, and it had already been written twice: once in the orchestrator's report and again, by
hand, in the console's TypeScript port (`services/report.ts`, whose own docstring flags the net
rules as "copied exactly"). The two had already drifted -- the orchestrator reads flies from
`fly_positions` where the console reads `fly_books` -- so a third copy was not an option. This
module is the single Python home; anything else derives from it or from the artifact it feeds.

Every closed reader yields the same record shape, keyed by `paper.trade_schema`:

    {profile, symbol, strategy, gross_pnl, cost, net_pnl, slippage, capital, session}

  - "meic_ic"  : MEIC's `ic_trades`; closed = exit_time set; net = pnl - fees; tag = risk_profile.
                 Capital at risk is derived: (wing_width - net_credit) x multiplier x quantity.
  - "earnings" : earnings' `trades`; closed = closed_at set; net = pnl - entry_cost - exit_cost;
                 tag = profile; capital = capital_at_risk stored at entry.
  - "fly_book" : flies' `fly_positions`; closed = status 'settled'; net = gross_pnl - fees;
                 tag = arm. P&L only -- the module's book-level floor and the price band it holds
                 over live in `fly_books` and are not summarisable as a per-trade number.
  - "dc_week"  : calendars' `dc_positions`; closed = status 'closed'; net = gross_pnl - fees
                 (fees is the TOTAL modeled cost, entry+exit+settlement, by that module's own
                 convention -- one subtraction, no double counting); tag = book (control / path /
                 advised:control -- comparing the books is the module's point, same reasoning as
                 flies' arm tag); strategy = the structure tag (dc_4_7 vs dc_3_6), because holiday
                 variants are distinct trades that must never pool; capital = entry_debit x 100 x
                 quantity, a long calendar's defined max loss.
  - "pmcc_99"  : pmcc's `pmcc_positions`; closed = status 'closed'; net = gross_pnl - fees (fees
                 is the TOTAL modeled cost, entry+exit+rolls+settlement, that module's own
                 convention); tag = book (control / advised:control); capital =
                 net_debit x 100 x quantity, the structure's defined max loss.

`None` never means zero anywhere in here. A row written before an instrumentation column existed
reports `slippage: None` and `capital: None`, because "not recorded" and "was zero" are different
facts and averaging them together is how a cost model quietly flatters itself.

OPEN_READERS is a deliberately separate registry answering "what is still on the book", not "what
settled". Only a multi-day strategy carries overnight; the 0DTE modules are empty by construction
rather than by a query that could surface an unsettled 0DTE position as though it were a hold.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from cherrypick.core import db as core_db

# Read-only connection, shared suite-wide. core.db.connect_ro percent-escapes the path, so
# '?'/'#'/'%' in a directory name cannot silently change the URI's meaning.
connect_ro = core_db.connect_ro


MEIC_UNTAGGED = "unassigned"
EARNINGS_UNTAGGED = "default"
FLIES_UNTAGGED = "unassigned"




# --------------------------------------------------------------------------- per-schema readers
def session_from_epoch(closed_at) -> str:
    """Trading-session date (ISO) from an epoch-seconds close time; '' if unparseable."""
    try:
        return date.fromtimestamp(float(closed_at)).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _table_cols(conn, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _session_where(column_expr: str, start: str | None, end: str | None) -> tuple[str, list]:
    """SQL fragment + params bounding a session-date expression to [start, end] (inclusive,
    either side optional). Pushed into the readers so an all-time table scan isn't the only
    way to ask about one day or one range."""
    clauses, params = "", []
    if start:
        clauses += f" AND {column_expr} >= ?"
        params.append(start)
    if end:
        clauses += f" AND {column_expr} <= ?"
        params.append(end)
    return clauses, params


def _meic_closed(conn, start: str | None = None, end: str | None = None) -> list[dict]:
    # slippage_dollars (cumulative modeled slippage on the trade's fills) rides along when
    # the column exists; older DBs degrade to slippage=None rather than failing the reader.
    # Slippage is linear in the modeled fraction, so net at a stressed 2x fraction is
    # exactly net - slippage — the cost-sensitivity column every reading carries.
    cols = _table_cols(conn, "ic_trades")
    has_slip = "slippage_dollars" in cols
    slip_col = ", slippage_dollars" if has_slip else ""
    has_capital = {"wing_width", "net_credit", "quantity"} <= cols
    cap_cols = ", wing_width, net_credit, quantity" if has_capital else ""
    mult_col = ", dollar_multiplier" if "dollar_multiplier" in cols else ""
    where, params = _session_where("substr(exit_time, 1, 10)", start, end)
    rows = conn.execute(
        f"SELECT symbol, risk_profile, pnl, fees, exit_time{slip_col}{cap_cols}{mult_col} "
        f"FROM ic_trades WHERE exit_time IS NOT NULL{where}",
        params,
    ).fetchall()

    def _capital(r):
        # An IC's capital at risk = (wing width - credit received) x multiplier x quantity.
        # This is what makes return-on-capital comparable: a 2-wide and a 10-wide IC stop
        # weighing equally. None (not zero) when the row can't support the computation.
        if not has_capital or r["wing_width"] is None:
            return None
        mult = r["dollar_multiplier"] if mult_col and r["dollar_multiplier"] else 100.0
        cap = (float(r["wing_width"]) - float(r["net_credit"] or 0.0)) * mult * (r["quantity"] or 1)
        return round(cap, 2) if cap > 0 else None

    return [
        {
            "profile": r["risk_profile"] or MEIC_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": None,
            # gross = spread P&L (already at the modeled fill prices); cost = exchange fees.
            "gross_pnl": (r["pnl"] or 0.0),
            "cost": (r["fees"] or 0.0),
            "net_pnl": (r["pnl"] or 0.0) - (r["fees"] or 0.0),
            "slippage": (r["slippage_dollars"] if has_slip else None),
            "capital": _capital(r),
            # Session date for calibration (distinct-days count); ISO date prefix of exit_time.
            "session": (r["exit_time"] or "")[:10],
        }
        for r in rows
    ]


def _earnings_closed(conn, start: str | None = None, end: str | None = None) -> list[dict]:
    cols = _table_cols(conn, "trades")
    has_slip = "entry_slippage" in cols and "exit_slippage" in cols
    slip_cols = ", entry_slippage, exit_slippage" if has_slip else ""
    has_capital = "capital_at_risk" in cols
    cap_col = ", capital_at_risk" if has_capital else ""
    # No SQL pushdown here on purpose: closed_at is epoch seconds and the session date is
    # the LOCAL calendar day (session_from_epoch); SQLite's date(...,'unixepoch') is UTC,
    # so a SQL bound would shift evening closes across the session boundary. The table is
    # small (one row per position); run() applies the tz-correct Python filter.
    rows = conn.execute(
        f"SELECT symbol, profile, strategy, pnl, entry_cost, exit_cost, closed_at{slip_cols}{cap_col} "
        "FROM trades WHERE closed_at IS NOT NULL"
    ).fetchall()

    def _slip(r):
        if not has_slip or (r["entry_slippage"] is None and r["exit_slippage"] is None):
            return None  # pre-instrumentation row — unknown, not zero
        return (r["entry_slippage"] or 0.0) + (r["exit_slippage"] or 0.0)

    return [
        {
            "profile": r["profile"] or EARNINGS_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": r["strategy"],
            # gross = mid-priced spread P&L; cost = commission + pass-through + slippage.
            "gross_pnl": (r["pnl"] or 0.0),
            "cost": (r["entry_cost"] or 0.0) + (r["exit_cost"] or 0.0),
            "net_pnl": (r["pnl"] or 0.0) - (r["entry_cost"] or 0.0) - (r["exit_cost"] or 0.0),
            "slippage": _slip(r),
            # sizing.compute_position_size's defined max loss, stored at entry.
            "capital": (r["capital_at_risk"] if has_capital else None),
            "session": session_from_epoch(r["closed_at"]),
        }
        for r in rows
    ]


def _flies_closed(conn, start: str | None = None, end: str | None = None) -> list[dict]:
    """cherrypick-flies' `fly_positions`; closed = settled. The attribution tag is the ARM, because
    comparing the arms is the entire point of the module — a per-symbol view would hide the one
    contrast the experiment exists to draw. Read straight off the row rather than against a known list,
    so an arm added on the module side (wide_wing joined gex / time_window / control on 2026-07-27)
    appears here without a change on this side."""
    # center_reason arrived after the first release, so an older ledger simply has no centring
    # provenance -- degrade to None rather than failing the whole read, the same way this file
    # already treats slippage_dollars and the capital columns.
    has_reason = "center_reason" in _table_cols(conn, "fly_positions")
    reason_col = ", center_reason" if has_reason else ""
    where, params = _session_where("trade_date", start, end)
    rows = conn.execute(
        f"SELECT symbol, arm, entry_mode, gross_pnl, fees, trade_date{reason_col} "
        f"FROM fly_positions WHERE status = 'settled'{where}",
        params,
    ).fetchall()
    return [
        {
            "profile": r["arm"] or FLIES_UNTAGGED,
            "symbol": r["symbol"],
            # legged vs outright: the two entry mechanisms perform differently enough that
            # aggregating them would average away the finding.
            "strategy": r["entry_mode"],
            # How the centre was actually chosen. A GEX-centred arm degrades to ATM when the
            # streamer has no OI cached, at which point it IS the control arm under a different
            # name -- the module records this precisely so those samples can be excluded, and
            # nothing was excluding them. On 2026-08-12 `gex-intrinsic` centred `atm` on all four
            # entries and reported results identical to `control` to the cent, which a reader
            # would otherwise take as two arms agreeing rather than one arm run twice.
            "center_reason": (r["center_reason"] if has_reason else None),
            "gross_pnl": (r["gross_pnl"] or 0.0),
            "cost": (r["fees"] or 0.0),
            "net_pnl": (r["gross_pnl"] or 0.0) - (r["fees"] or 0.0),
            # Slippage capture deferred for flies: its decisive metrics are floor/completion
            # based, and its fills already price the haircut into gross_pnl. Unknown != zero,
            # and likewise capital: a legged book's risk depends on completion state.
            "slippage": None,
            "capital": None,
            "session": r["trade_date"] or "",
        }
        for r in rows
    ]


CALENDARS_UNTAGGED = "unassigned"


def _calendars_closed(conn, start: str | None = None, end: str | None = None) -> list[dict]:
    """calendars' `dc_positions`; closed = status 'closed'. The attribution tag is the BOOK
    (control / path / advised:control) — the exit experiment's contrast, same reasoning as flies'
    arm tag — and `strategy` carries the structure tag, because a Tuesday-entry dc_3_6 is a
    different trade from a dc_4_7 and pooling them would blend structures."""
    where, params = _session_where("closed_session", start, end)
    rows = conn.execute(
        f"SELECT symbol, book, structure, gross_pnl, fees, entry_slippage, exit_slippage, "
        f"entry_debit, quantity, closed_session FROM dc_positions WHERE status = 'closed'{where}",
        params,
    ).fetchall()

    def _slip(r):
        if r["entry_slippage"] is None and r["exit_slippage"] is None:
            return None  # pre-instrumentation row — unknown, not zero
        return round((r["entry_slippage"] or 0.0) + (r["exit_slippage"] or 0.0), 2)

    def _capital(r):
        # A long calendar's defined max loss is the debit paid.
        if r["entry_debit"] is None:
            return None
        return round(float(r["entry_debit"]) * 100 * (r["quantity"] or 1), 2)

    return [
        {
            "profile": r["book"] or CALENDARS_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": r["structure"],
            "gross_pnl": (r["gross_pnl"] or 0.0),
            # `fees` is that module's TOTAL modeled cost (entry+exit+settlement, slippage included),
            # so net is one subtraction and slippage is NOT additionally deducted here.
            "cost": (r["fees"] or 0.0),
            "net_pnl": (r["gross_pnl"] or 0.0) - (r["fees"] or 0.0),
            "slippage": _slip(r),
            "capital": _capital(r),
            "session": r["closed_session"] or "",
        }
        for r in rows
    ]


PMCC_UNTAGGED = "unassigned"


def _pmcc_closed(conn, start: str | None = None, end: str | None = None) -> list[dict]:
    """pmcc's `pmcc_positions`; closed = status 'closed'. The attribution tag is the BOOK
    (control / advised:control) — the module's contrast, same reasoning as flies'
    arm tag; `strategy` is the schema constant, since every position is the same two-leg structure.
    `fees` is that module's TOTAL modeled cost (entry+exit+rolls+settlement, slippage included), so
    net is one subtraction. Capital = the net debit paid ×100×qty — the defined max loss of the
    spread-like structure. A breached position held as a covered call realizes within that bound;
    the delivered-shares weekend leg is the one exposure not bounded by it (the module CLAUDE.md's
    caveat, same as calendars' path book)."""
    where, params = _session_where("closed_session", start, end)
    rows = conn.execute(
        f"SELECT symbol, book, gross_pnl, fees, entry_slippage, exit_slippage, "
        f"net_debit, quantity, closed_session FROM pmcc_positions WHERE status = 'closed'{where}",
        params,
    ).fetchall()

    def _slip(r):
        if r["entry_slippage"] is None and r["exit_slippage"] is None:
            return None  # pre-instrumentation row — unknown, not zero
        return round((r["entry_slippage"] or 0.0) + (r["exit_slippage"] or 0.0), 2)

    def _capital(r):
        if r["net_debit"] is None:
            return None
        return round(float(r["net_debit"]) * 100 * (r["quantity"] or 1), 2)

    return [
        {
            "profile": r["book"] or PMCC_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": "pmcc_99",
            "gross_pnl": (r["gross_pnl"] or 0.0),
            "cost": (r["fees"] or 0.0),
            "net_pnl": (r["gross_pnl"] or 0.0) - (r["fees"] or 0.0),
            "slippage": _slip(r),
            "capital": _capital(r),
            "session": r["closed_session"] or "",
        }
        for r in rows
    ]


CURVE_UNTAGGED = "unassigned"


def _curve_closed(conn, start: str | None = None, end: str | None = None) -> list[dict]:
    """curve's `curve_positions`; closed = status 'closed'. The attribution tag is the BOOK
    (control / noflip / hook / advised:control) — the module's contrast, same reasoning as pmcc's
    book tag; `strategy` is the schema constant, since every position is the same call-credit-
    spread structure. `fees` is that module's TOTAL modeled cost (entry+exit+settlement, slippage
    included), so net is one subtraction. Capital = the spread's max loss (width - credit) x100xqty
    — the defined max loss of the structure. A leg assigned/exercised at expiry and held as shares
    over a weekend is the one exposure not bounded by it, same caveat as pmcc's delivered shares."""
    where, params = _session_where("closed_session", start, end)
    rows = conn.execute(
        f"SELECT symbol, book, gross_pnl, fees, entry_slippage, exit_slippage, "
        f"entry_max_loss, quantity, closed_session FROM curve_positions WHERE status = 'closed'{where}",
        params,
    ).fetchall()

    def _slip(r):
        if r["entry_slippage"] is None and r["exit_slippage"] is None:
            return None  # pre-instrumentation row — unknown, not zero
        return round((r["entry_slippage"] or 0.0) + (r["exit_slippage"] or 0.0), 2)

    def _capital(r):
        if r["entry_max_loss"] is None:
            return None
        return round(float(r["entry_max_loss"]) * 100 * (r["quantity"] or 1), 2)

    return [
        {
            "profile": r["book"] or CURVE_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": "curve_vx",
            "gross_pnl": (r["gross_pnl"] or 0.0),
            "cost": (r["fees"] or 0.0),
            "net_pnl": (r["gross_pnl"] or 0.0) - (r["fees"] or 0.0),
            "slippage": _slip(r),
            "capital": _capital(r),
            "session": r["closed_session"] or "",
        }
        for r in rows
    ]


def _curve_open(conn) -> list[dict]:
    """curve's `curve_positions` still on the book — a position lives ~30-45 DTE and carries
    overnight throughout. Capital at risk is the spread's max loss (defined risk); a
    `short_settled` position's delivered/received shares are the one leg that bound does not
    cover, per the module's own caveat."""
    rows = conn.execute(
        "SELECT symbol, book, entry_max_loss, quantity, entry_session FROM curve_positions "
        "WHERE status != 'closed'"
    ).fetchall()
    return [
        {
            "profile": r["book"] or CURVE_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": "curve_vx",
            "capital_at_risk": (
                round(float(r["entry_max_loss"]) * 100 * (r["quantity"] or 1), 2)
                if r["entry_max_loss"] is not None
                else 0.0
            ),
            "session": r["entry_session"] or "",
        }
        for r in rows
    ]


BWB_UNTAGGED = "unassigned"


def _bwb_closed(conn, start: str | None = None, end: str | None = None) -> list[dict]:
    """bwb's `bwb_positions`; closed = status 'closed'. The attribution tag is the BOOK
    (control / delta / bounce / flip / advised:control) — the module's contrast, same reasoning as
    curve's book tag; `strategy` is the schema constant, since every position starts as the same
    put-BWB structure (a fired add-on turns it into a 1-3-2, but the schema doesn't fork). `fees`
    is that module's TOTAL modeled cost (entry + addon entry + settlement), so net is one
    subtraction. Capital = the structure's worst-case max loss (entry_max_loss, already the
    larger of the up/down loss) x100xqty."""
    where, params = _session_where("closed_session", start, end)
    rows = conn.execute(
        f"SELECT symbol, book, gross_pnl, fees, entry_max_loss, quantity, closed_session "
        f"FROM bwb_positions WHERE status = 'closed'{where}",
        params,
    ).fetchall()

    def _capital(r):
        if r["entry_max_loss"] is None:
            return None
        return round(float(r["entry_max_loss"]) * 100 * (r["quantity"] or 1), 2)

    return [
        {
            "profile": r["book"] or BWB_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": "bwb_132",
            "gross_pnl": (r["gross_pnl"] or 0.0),
            "cost": (r["fees"] or 0.0),
            "net_pnl": (r["gross_pnl"] or 0.0) - (r["fees"] or 0.0),
            # bwb does not yet split entry/exit slippage on the row (a single modeled cost total),
            # so this stays unknown rather than a misleading zero -- same posture as flies' capital.
            "slippage": None,
            "capital": _capital(r),
            "session": r["closed_session"] or "",
        }
        for r in rows
    ]


def _bwb_open(conn) -> list[dict]:
    """bwb's `bwb_positions` still on the book — the daily ladder holds ~5-7 concurrent positions
    per book at steady state, all carrying overnight through their ~7 DTE life. Capital at risk is
    the structure's defined max loss."""
    rows = conn.execute(
        "SELECT symbol, book, entry_max_loss, quantity, entry_session FROM bwb_positions "
        "WHERE status != 'closed'"
    ).fetchall()
    return [
        {
            "profile": r["book"] or BWB_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": "bwb_132",
            "capital_at_risk": (
                round(float(r["entry_max_loss"]) * 100 * (r["quantity"] or 1), 2)
                if r["entry_max_loss"] is not None
                else 0.0
            ),
            "session": r["entry_session"] or "",
        }
        for r in rows
    ]


READERS = {
    "meic_ic": _meic_closed,
    "earnings": _earnings_closed,
    "fly_book": _flies_closed,
    "dc_week": _calendars_closed,
    "pmcc_99": _pmcc_closed,
    "curve_vx": _curve_closed,
    "bwb_132": _bwb_closed,
}


# --------------------------------------------------------------------------- per-schema open readers
# The closed readers above answer "what settled" (realized P&L). These answer "what is still on the
# book, carried past the close" (capital at risk, no P&L yet). Only a multi-day strategy carries
# overnight; the two 0DTE modules open and settle inside one session, so their overnight view is
# structurally empty — see _no_overnight. Kept as a SEPARATE registry from _READERS on purpose: it
# feeds only the report/digest, not calibrate/reconcile/notifier, so it is not one of the "four
# registries extended together" the module CLAUDE.md warns about.
def _no_overnight(conn) -> list[dict]:
    """MEIC and flies are 0DTE: every position opens and cash-settles within the same session, so
    nothing is deliberately carried overnight. A position still open at digest time is a settlement
    lag for the watchdog to flag, not overnight risk for this roll-up — so the carried-overnight
    view is empty here by construction rather than by a query that could accidentally surface an
    unsettled 0DTE position as though it were an earnings hold."""
    return []


def _earnings_open(conn) -> list[dict]:
    """EarningsAgent's `trades` still open (closed_at NULL): defined-risk earnings structures entered
    one afternoon and carried over the earnings event to the next morning. `capital_at_risk` is the
    known max loss set at entry — the honest overnight-exposure number, since these have no realized
    P&L until they settle. `session` is the OPEN session (opened_at), so the digest for a day shows
    what that day put on, matching the earnings module's own 'Opened this session' EOD section."""
    rows = conn.execute(
        "SELECT symbol, profile, strategy, capital_at_risk, opened_at FROM trades WHERE closed_at IS NULL"
    ).fetchall()
    return [
        {
            "profile": r["profile"] or EARNINGS_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": r["strategy"],
            "capital_at_risk": (r["capital_at_risk"] or 0.0),
            "session": session_from_epoch(r["opened_at"]),
        }
        for r in rows
    ]


def _calendars_open(conn) -> list[dict]:
    """calendars' `dc_positions` still on the book — a weekly structure carries overnight Monday
    through Friday AND over the weekend (the path book's longs), so this module needs the real
    overnight view the 0DTE modules are structurally spared. Capital at risk is the entry debit
    (defined max loss); a `short_settled` position's true remaining risk is smaller, but the debit
    stays the honest conservative bound without re-deriving marks here."""
    rows = conn.execute(
        "SELECT symbol, book, structure, entry_debit, quantity, entry_session FROM dc_positions "
        "WHERE status != 'closed'"
    ).fetchall()
    return [
        {
            "profile": r["book"] or CALENDARS_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": r["structure"],
            "capital_at_risk": (
                round(float(r["entry_debit"]) * 100 * (r["quantity"] or 1), 2)
                if r["entry_debit"] is not None
                else 0.0
            ),
            "session": r["entry_session"] or "",
        }
        for r in rows
    ]


def _pmcc_open(conn) -> list[dict]:
    """pmcc's `pmcc_positions` still on the book — a position lives ~1–2 weeks and carries
    overnight throughout, plus the assigned-shares weekend between a Friday settlement and the
    Monday disposal. Capital at risk is the net debit (defined max loss); a `short_settled`
    position's delivered shares are the one leg that bound does not cover, per the module's own
    caveat — the debit stays the honest conservative bound without re-deriving marks here."""
    rows = conn.execute(
        "SELECT symbol, book, net_debit, quantity, entry_session FROM pmcc_positions "
        "WHERE status != 'closed'"
    ).fetchall()
    return [
        {
            "profile": r["book"] or PMCC_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": "pmcc_99",
            "capital_at_risk": (
                round(float(r["net_debit"]) * 100 * (r["quantity"] or 1), 2)
                if r["net_debit"] is not None
                else 0.0
            ),
            "session": r["entry_session"] or "",
        }
        for r in rows
    ]


OPEN_READERS = {
    "meic_ic": _no_overnight,
    "earnings": _earnings_open,
    "fly_book": _no_overnight,
    "dc_week": _calendars_open,
    "pmcc_99": _pmcc_open,
    "curve_vx": _curve_open,
    "bwb_132": _bwb_open,
}
