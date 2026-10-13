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

    {arm, symbol, strategy, gross_pnl, cost, net_pnl, slippage, capital, max_profit, session,
     experiment_id}

plus, for the multi-day schemas (earnings, dc_week, pmcc, curve_vx, bwb_132, contango_etf),
`entry_session` -- the session the position was entered, where `session` is the one it closed
(added 2026-10-12 for `core.compare`, which pairs those arms on the entry they share).

`arm` is the suite's word for one configured variant run as its own portfolio (root `CLAUDE.md`).
This module is where the four source spellings become one: `risk_profile` (meic), `arm` (flies),
`book` (calendars/pmcc/curve/bwb) and `profile` (earnings) all land here as `arm`. The columns
themselves still read what they always did, and move later; this is the seam, which is exactly
what it is for. Note that an artifact key is NOT this record — `concentration`'s `out` parameter
exists because the review fact set is still written `by_profile`.

`experiment_id` (added 2026-09-16) is the advisor experiment the row was entered under, stamped by
the module from its session advice decision -- set only on an advised book's rows, None on a
control's and on every row written before the column existed. It is what lets one `advised:<base>`
tag be split back into the experiments that ran on it in turn; the tag names a book, and every
experiment on that base reuses it.

`max_profit` (added 2026-09, for `core.metrics.capture_rate`) is the structure's own defined
ceiling at expiry -- only ever a number for a plain credit structure whose ceiling IS the credit
received (meic_ic, curve_vx): `None` for a debit structure (dc_week, pmcc) whose profit depends
on where the underlying settles, and `None` for a structure whose ceiling needs strike geometry
this per-trade row does not carry (fly_book, bwb_132) or that mixes credit/debit shapes
(earnings) -- see each reader's own comment for why. Deliberately not derived by guessing at a
formula from `capital` alone.

  - "meic_ic"  : MEIC's `ic_trades`; closed = exit_time set; net = pnl - fees; tag = `risk_profile`.
                 Capital at risk is derived: (wing_width - net_credit) x multiplier x quantity.
  - "earnings" : earnings' `trades`; closed = closed_at set; net = pnl - entry_cost - exit_cost;
                 tag = `profile`; capital = capital_at_risk stored at entry.
  - "fly_book" : flies' `fly_positions`; closed = status 'settled' and not voided (`void_reason`
                 NULL); net = gross_pnl - fees;
                 tag = arm. P&L only -- the module's book-level floor and the price band it holds
                 over live in `fly_books` and are not summarisable as a per-trade number.
  - "dc_week"  : calendars' `dc_positions`; closed = status 'closed'; net = gross_pnl - fees
                 (fees is the TOTAL modeled cost, entry+exit+settlement, by that module's own
                 convention -- one subtraction, no double counting); tag = book (control / path /
                 advised:control -- comparing the books is the module's point, same reasoning as
                 flies' arm tag); strategy = the structure tag (dc_4_7 vs dc_3_6), because holiday
                 variants are distinct trades that must never pool; capital = entry_debit x 100 x
                 quantity, a long calendar's defined max loss.
  - "pmcc"     : pmcc's `pmcc_positions` (`pmcc_99` until 2026-10-04, still resolved -- see
                 SCHEMA_ALIASES); closed = status 'closed'; net = gross_pnl - fees (fees
                 is the TOTAL modeled cost, entry+exit+rolls+settlement, that module's own
                 convention); tag = book (control / advised:control); capital =
                 net_debit x 100 x quantity, the structure's defined max loss.

  - "contango_etf": contango's `contango_positions`, one row per HOLDING STINT (the shares an arm
                 bought on one switch and sold on the next); closed = status 'closed'; net =
                 net_pnl as stored (gross - fees - slippage: fills are at mid and slippage is its
                 own column there, so `cost` here is fees + slippage); tag = arm; capital = the
                 stint's purchase (shares x entry mid), all of it at risk in a fund.

The cost split (added 2026-10-12, for the suite's money layout in the root `CLAUDE.md`): beside
`cost` -- each module's TOTAL cost, unchanged, so `net_pnl = gross_pnl - cost` still holds -- every
closed record carries the parts:

    fees            trading fees only (commission, exchange): `cost` less settlement, less slippage
                    where slippage is a charged cost
    settlement      exercise / assignment / delivery charges, or None where the row never recorded
                    the split (then the charge, if any, is still inside `fees` -- never a zero)
    slippage_basis  "in_gross" (the modelled fill already concedes it: meic, flies -- slippage is a
                    measure, never subtracted again) or "cost" (fills at mid, slippage charged as
                    its own cost: earnings, calendars, pmcc, curve, bwb, contango)
    entry_credit    the credit received at entry, whole-position dollars, for a single-credit
                    structure (meic, curve, bwb); None elsewhere

so `gross_pnl - fees - (settlement or 0) - (slippage if basis is "cost" else 0) == net_pnl` on
every row. The rules are the console's (`readers/*TradeCash`), which drew the layout first.

`None` never means zero anywhere in here. A row written before an instrumentation column existed
reports `slippage: None` and `capital: None`, because "not recorded" and "was zero" are different
facts and averaging them together is how a cost model quietly flatters itself. The same holds for
the money itself where a ledger can close a row without it (meic's `pnl`/`fees`, earnings' `pnl`
and costs, contango's stored figures): `gross_pnl`, `cost` and `net_pnl` are None there, and
`core.metrics` and the orchestrator report leave such a record out of every sum and count it as
unscored. The other schemas write their money in the same statement that closes the row.

OPEN_READERS is a deliberately separate registry answering "what is still on the book", not "what
settled". Only a multi-day strategy carries overnight; the 0DTE modules are empty by construction
rather than by a query that could surface an unsettled 0DTE position as though it were a hold.
Its records carry `capital` under the same name the closed records use (they said `capital_at_risk`
until 2026-09-24 -- one quantity, two names across the two registries). The artifacts built from
them -- the report's `open` block, review's `carried_overnight` -- still write `capital_at_risk`,
which is an accurate name for a persisted key and not the record's. An open record whose capital
is unknown carries None, like a closed one (until 2026-09-24 the open readers wrote 0.0, which a
total then counted as a real, riskless position); the totals built from them sum what is known
and say how much is not (`capital_unknown`).
"""

from __future__ import annotations

import sqlite3
from datetime import date

from cherrypick.core import db as core_db

# Read-only connection, shared suite-wide. core.db.connect_ro percent-escapes the path, so
# '?'/'#'/'%' in a directory name cannot silently change the URI's meaning.
connect_ro = core_db.connect_ro


#: Statuses that are NOT on the book, for the open readers' `status NOT IN` filter. `cancelled` is
#: an order never established (bwb's live loop writes it when an entry does not fill); until
#: 2026-10-12 the open readers said `status != 'closed'` and counted one as carried capital.
#: calendars, pmcc and curve write neither today (open / short_settled / closed), so for them the
#: filter is the same rule stated once.
_NOT_ON_BOOK = "('closed', 'cancelled')"


def _plus(a: float | None, b: float | None) -> float | None:
    """a + b, or None when either is unrecorded -- a sum with a hole in it is not a total."""
    return None if a is None or b is None else a + b


def _less(a: float | None, b: float | None) -> float | None:
    """a - b, or None when either is unrecorded."""
    return None if a is None or b is None else a - b


def _round2(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


MEIC_UNTAGGED = "unassigned"
EARNINGS_UNTAGGED = "default"
FLIES_UNTAGGED = "unassigned"

SLIPPAGE_IN_GROSS = "in_gross"
SLIPPAGE_COST = "cost"
#: Where each schema's slippage sits: inside gross (the modelled fill concedes it) or charged as a
#: cost. A figure subtracted in one module and not in another is never one unqualified number.
SLIPPAGE_BASIS = {
    "meic_ic": SLIPPAGE_IN_GROSS,
    "fly_book": SLIPPAGE_IN_GROSS,
    "earnings": SLIPPAGE_COST,
    "dc_week": SLIPPAGE_COST,
    "pmcc": SLIPPAGE_COST,
    "curve_vx": SLIPPAGE_COST,
    "bwb_132": SLIPPAGE_COST,
    "contango_etf": SLIPPAGE_COST,
}


def _cost_split(schema: str, total: float | None, settlement, slippage) -> dict:
    """The record's `fees` / `settlement` / `slippage_basis`, from the module's total cost. Each
    part comes out of the total once: settlement where it was recorded, slippage where it is a
    charged cost and was recorded. What was never split stays inside `fees`. A total that was
    never recorded (`cost` None, the n/r rows above) leaves `fees` None too, never a zero."""
    basis = SLIPPAGE_BASIS[schema]
    charged = slippage if (basis == SLIPPAGE_COST and slippage is not None) else 0.0
    # Unrounded, like `cost`: a total over hundreds of rows must add back to the summed net, and
    # rounding each row's part to the cent drifted MEIC's day by ~$1 over 494 trades.
    return {
        "fees": None if total is None else total - (settlement or 0.0) - charged,
        "settlement": settlement,
        "slippage_basis": basis,
    }


def _opt(cols: set, name: str) -> str:
    """`, name` when the column exists, else '' -- for a column added after a ledger's first rows."""
    return f", {name}" if name in cols else ""


def _get(r, name: str):
    """A row's column, or None when the reader did not select it (an older ledger)."""
    return r[name] if name in r.keys() else None


# --------------------------------------------------------------------------- per-schema readers
def session_from_epoch(closed_at) -> str:
    """Trading-session date (ISO) from an epoch-seconds close time; '' if unparseable."""
    try:
        return date.fromtimestamp(float(closed_at)).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _net(gross, *costs) -> float | None:
    """gross less every cost given; None when the gross itself was never recorded. A missing cost
    column subtracts nothing (the closed readers' long-standing convention)."""
    if gross is None:
        return None
    return gross - sum((c or 0.0) for c in costs)


#: The net rule per schema, over one raw ledger row (anything indexable by column name). The
#: closed readers below compute `net_pnl` with the same `_net` over the same columns, and a surface
#: that shows a single row's P&L -- the trade notifier's exit cards -- calls these rather than
#: re-deriving: MEIC's `pnl` and
#: earnings' `pnl` are GROSS, and printing either under "P&L" overstated every exit by its fees.
#: None means the row has no gross yet (or, for contango, no stored net): render it as unrecorded.
#: MEIC and earnings follow their closed readers' stricter rule (2026-10-12): a missing cost
#: column there is n/r too, never a costless trade.
ROW_NET = {
    "meic_ic": lambda r: _less(r["pnl"], r["fees"]),
    "earnings": lambda r: _less(r["pnl"], _plus(r["entry_cost"], r["exit_cost"])),
    "fly_book": lambda r: _net(r["gross_pnl"], r["fees"]),
    "dc_week": lambda r: _net(r["gross_pnl"], r["fees"]),
    "pmcc": lambda r: _net(r["gross_pnl"], r["fees"]),
    "curve_vx": lambda r: _net(r["gross_pnl"], r["fees"]),
    "bwb_132": lambda r: _net(r["gross_pnl"], r["fees"]),
    # contango stores the whole layout on the row (gross - fees - slippage = net); read, not derived.
    "contango_etf": lambda r: r["net_pnl"],
}


def _table_cols(conn, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _experiment_select(conn, table: str) -> tuple[str, bool]:
    """(", experiment_id" or "", present): the optional attribution column, sniffed so a ledger
    that predates it reads with experiment_id=None rather than failing."""
    present = "experiment_id" in _table_cols(conn, table)
    return (", experiment_id" if present else ""), present


def _entry_select(conn, table: str, column: str = "entry_session") -> tuple[str, bool]:
    """(", <column>" or "", present) for the session a multi-day position was ENTERED. Closed
    records carry it as `entry_session` (added 2026-10-12) beside `session`, the close: arms of one
    module enter together and exit by different rules, so a paired comparison of a multi-day
    structure keys on the entry (`core.compare`). None where the ledger predates the column."""
    present = column in _table_cols(conn, table)
    return (f", {column}" if present else ""), present


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
    exp_col, has_exp = _experiment_select(conn, "ic_trades")
    arm = core_db.arm_column(conn, "ic_trades")  # `risk_profile` until meic's column moves
    rows = conn.execute(
        f"SELECT symbol, {arm} AS arm, pnl, fees, exit_time{slip_col}{cap_cols}{mult_col}{exp_col}"
        f"{_opt(cols, 'settlement_fees')} FROM ic_trades WHERE exit_time IS NOT NULL{where}",
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

    def _max_profit(r):
        # An IC's max profit AT EXPIRY is the credit received x multiplier x quantity -- the
        # trade's own defined ceiling, same columns _capital already reads with the same
        # confidence (both already selected for the capital computation above).
        if not has_capital or r["net_credit"] is None:
            return None
        mult = r["dollar_multiplier"] if mult_col and r["dollar_multiplier"] else 100.0
        mp = float(r["net_credit"]) * mult * (r["quantity"] or 1)
        return round(mp, 2) if mp > 0 else None

    return [
        {
            "arm": r["arm"] or MEIC_UNTAGGED,
            "experiment_id": (r["experiment_id"] if has_exp else None),
            "symbol": r["symbol"],
            "strategy": None,
            # gross = spread P&L (already at the modeled fill prices); cost = exchange fees.
            # A closed row can carry neither: the live book's 2026-06-29 stop has an exit_time and
            # NULL pnl and fees. That is n/r (None), never a flat, costless trade.
            "gross_pnl": r["pnl"],
            "cost": r["fees"],
            "net_pnl": _less(r["pnl"], r["fees"]),
            "slippage": (r["slippage_dollars"] if has_slip else None),
            **_cost_split(
                "meic_ic",
                r["fees"],
                _get(r, "settlement_fees"),
                r["slippage_dollars"] if has_slip else None,
            ),
            "entry_credit": _max_profit(r),
            "capital": _capital(r),
            "max_profit": _max_profit(r),
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
    exp_col, has_exp = _experiment_select(conn, "trades")
    open_col, has_open = _entry_select(conn, "trades", "opened_at")
    arm = core_db.arm_column(conn, "trades")  # `profile` until earnings' column moves
    rows = conn.execute(
        f"SELECT symbol, {arm} AS arm, strategy, pnl, entry_cost, exit_cost, closed_at"
        f"{slip_cols}{cap_col}{exp_col}{open_col}{_opt(cols, 'settlement_fees')} "
        "FROM trades WHERE closed_at IS NOT NULL"
    ).fetchall()

    def _slip(r):
        if not has_slip or (r["entry_slippage"] is None and r["exit_slippage"] is None):
            return None  # pre-instrumentation row — unknown, not zero
        return (r["entry_slippage"] or 0.0) + (r["exit_slippage"] or 0.0)

    return [
        {
            "arm": r["arm"] or EARNINGS_UNTAGGED,
            "experiment_id": (r["experiment_id"] if has_exp else None),
            "symbol": r["symbol"],
            "strategy": r["strategy"],
            # gross = mid-priced spread P&L; cost = commission + pass-through + slippage.
            # `save-close` writes pnl and exit_cost from whatever the caller passed, so a closed
            # row can lack either: n/r (None), never zero.
            "gross_pnl": r["pnl"],
            "cost": _plus(r["entry_cost"], r["exit_cost"]),
            "net_pnl": _less(r["pnl"], _plus(r["entry_cost"], r["exit_cost"])),
            "slippage": _slip(r),
            # entry_cost + exit_cost carry the slippage charge and (exit_cost) any settlement fee.
            **_cost_split(
                "earnings",
                _plus(r["entry_cost"], r["exit_cost"]),
                _get(r, "settlement_fees"),
                _slip(r),
            ),
            "entry_credit": None,  # credit and debit structures mixed; no one credit to name
            # sizing.compute_position_size's defined max loss, stored at entry.
            "capital": (r["capital_at_risk"] if has_capital else None),
            # Earnings mixes credit and debit-shaped defined-risk strategies (module CLAUDE.md) --
            # no single ceiling formula holds across `strategy`, so this stays unmeasured rather
            # than guessed. Deferred, not abandoned: see docs/metrics-plan.md phase 2 (excursions).
            "max_profit": None,
            "session": session_from_epoch(r["closed_at"]),
            "entry_session": (session_from_epoch(r["opened_at"]) or None) if has_open else None,
        }
        for r in rows
    ]


def _flies_not_void(conn) -> str:
    """The SQL that keeps flies' voided rows out. flies voids a row by stamping `void_reason` and
    leaving it `settled` with its P&L, so status alone counts them (47 paper rows, -994.41 as of
    2026-10-12: the 08-04..06 bwb rolls and the 10-08 outage). The module's own read surfaces and
    the console exclude them; a ledger older than the column has nothing voided, so it reads whole."""
    return " AND void_reason IS NULL" if "void_reason" in _table_cols(conn, "fly_positions") else ""


def _flies_closed(conn, start: str | None = None, end: str | None = None) -> list[dict]:
    """cherrypick-flies' `fly_positions`; closed = settled. The attribution tag is the ARM, because
    comparing the arms is the entire point of the module — a per-symbol view would hide the one
    contrast the experiment exists to draw. Read straight off the row rather than against a known list,
    so an arm added on the module side (wide_wing joined gex / time_window / control on 2026-07-27)
    appears here without a change on this side."""
    # center_reason arrived after the first release, so an older ledger simply has no centring
    # provenance -- degrade to None rather than failing the whole read, the same way this file
    # already treats slippage_dollars and the capital columns.
    cols = _table_cols(conn, "fly_positions")
    has_reason = "center_reason" in cols
    reason_col = ", center_reason" if has_reason else ""
    where, params = _session_where("trade_date", start, end)
    exp_col, has_exp = _experiment_select(conn, "fly_positions")
    extra = _opt(cols, "settlement_fees") + _opt(cols, "slippage_dollars")
    rows = conn.execute(
        f"SELECT symbol, arm, entry_mode, gross_pnl, fees, trade_date{reason_col}{exp_col}{extra} "
        f"FROM fly_positions WHERE status = 'settled'{_flies_not_void(conn)}{where}",
        params,
    ).fetchall()
    return [
        {
            "arm": r["arm"] or FLIES_UNTAGGED,
            "experiment_id": (r["experiment_id"] if has_exp else None),
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
            "net_pnl": _net(r["gross_pnl"] or 0.0, r["fees"]),
            # The fills already price the haircut into gross_pnl, so `slippage_dollars` is a
            # measure, never subtracted again (basis in_gross, like meic's). None on a row from
            # before the column -- unknown, not zero -- and likewise capital: a legged book's risk
            # depends on completion state.
            "slippage": _get(r, "slippage_dollars"),
            **_cost_split(
                "fly_book", r["fees"] or 0.0, _get(r, "settlement_fees"), _get(r, "slippage_dollars")
            ),
            "entry_credit": None,  # legged/debit/bwb entry modes: no single credit to name
            "capital": None,
            # A fly's max profit at expiry is a payoff-curve computation over the book's actual
            # centres and wing structure, not a fixed formula this per-trade row can support --
            # same reasoning as capital above (a legged book's shape depends on completion state).
            "max_profit": None,
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
    exp_col, has_exp = _experiment_select(conn, "dc_positions")
    ent_col, has_ent = _entry_select(conn, "dc_positions")
    settle_col = _opt(_table_cols(conn, "dc_positions"), "settlement_fees")
    rows = conn.execute(
        f"SELECT symbol, arm, structure, gross_pnl, fees, entry_slippage, exit_slippage, "
        f"entry_debit, quantity, closed_session{exp_col}{ent_col}{settle_col} FROM dc_positions "
        f"WHERE status = 'closed'{where}",
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
            "arm": r["arm"] or CALENDARS_UNTAGGED,
            "experiment_id": (r["experiment_id"] if has_exp else None),
            "symbol": r["symbol"],
            "strategy": r["structure"],
            "gross_pnl": (r["gross_pnl"] or 0.0),
            # `fees` is that module's TOTAL modeled cost (entry+exit+settlement, slippage included),
            # so net is one subtraction and slippage is NOT additionally deducted here.
            "cost": (r["fees"] or 0.0),
            "net_pnl": _net(r["gross_pnl"] or 0.0, r["fees"]),
            "slippage": _slip(r),
            **_cost_split("dc_week", r["fees"] or 0.0, _get(r, "settlement_fees"), _slip(r)),
            "entry_credit": None,  # a debit structure
            "capital": _capital(r),
            # A long calendar is a DEBIT structure: its max profit depends on where the underlying
            # sits relative to the short leg's strike at ITS expiry, not a fixed value known at
            # entry -- unlike a credit spread's ceiling (the credit received), there is no defined
            # number to put here without re-deriving the payoff curve.
            "max_profit": None,
            "session": r["closed_session"] or "",
            "entry_session": r["entry_session"] if has_ent else None,
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
    exp_col, has_exp = _experiment_select(conn, "pmcc_positions")
    # `era` is an ADDED column (2026-08-23); a row from before it reads NULL and never equals a
    # stamped era, which is the module's own rule (`analytics.headline`). See ERA_SCHEMAS.
    cols = _table_cols(conn, "pmcc_positions")
    has_era = "era" in cols
    era_col = ", era" if has_era else ""
    # pmcc's own expectation (review's) reads the entry yield and the holding period off the record.
    extra = "".join(_opt(cols, c) for c in ("settlement_fees", "entry_session", "entry_weekly_yield_pct"))
    rows = conn.execute(
        f"SELECT symbol, arm, gross_pnl, fees, entry_slippage, exit_slippage, "
        f"net_debit, quantity, closed_session{exp_col}{era_col}{extra} "
        f"FROM pmcc_positions WHERE status = 'closed'{where}",
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
            "arm": r["arm"] or PMCC_UNTAGGED,
            "experiment_id": (r["experiment_id"] if has_exp else None),
            "symbol": r["symbol"],
            "strategy": "pmcc",
            "gross_pnl": (r["gross_pnl"] or 0.0),
            "cost": (r["fees"] or 0.0),
            "net_pnl": _net(r["gross_pnl"] or 0.0, r["fees"]),
            "slippage": _slip(r),
            **_cost_split("pmcc", r["fees"] or 0.0, _get(r, "settlement_fees"), _slip(r)),
            "entry_credit": None,  # a debit structure
            "capital": _capital(r),
            "entry_session": _get(r, "entry_session"),
            "entry_weekly_yield_pct": _get(r, "entry_weekly_yield_pct"),
            # PMCC is a debit structure (deep-ITM long call as a stock substitute + a short call):
            # its max profit depends on where spot sits relative to the short strike at ITS
            # expiry, same reasoning as calendars' debit structure above -- no fixed ceiling known
            # at entry.
            "max_profit": None,
            "session": r["closed_session"] or "",
            "era": r["era"] if has_era else None,
        }
        for r in rows
    ]


def pmcc_closed_short_legs(conn, session: str, min_long_dte: int | None = None) -> list[dict]:
    """pmcc's short-call legs closed on `session` -- the weekly realisation a held-long position
    has, since the position itself closes ~10 months after entry. `min_long_dte` keeps legs whose
    position's long was bought at or beyond that DTE (the ledger's own entry_long_dte, never an arm
    name). Each: strike, entry_mid, entry_spot, close_value, close_spot, as recorded."""
    long_dte = " AND p.entry_long_dte >= ?" if min_long_dte is not None else ""
    params: tuple = (session,) + ((min_long_dte,) if min_long_dte is not None else ())
    rows = conn.execute(
        "SELECT l.strike, l.entry_mid, l.entry_spot, l.close_value, l.close_spot FROM pmcc_legs l "
        "JOIN pmcc_positions p ON p.position_id = l.position_id "
        "WHERE l.leg_role LIKE 'short_call%' AND l.status = 'closed' "
        f"AND substr(l.closed_at, 1, 10) = ?{long_dte}",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


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
    exp_col, has_exp = _experiment_select(conn, "curve_positions")
    ent_col, has_ent = _entry_select(conn, "curve_positions")
    settle_col = _opt(_table_cols(conn, "curve_positions"), "settlement_fees")
    rows = conn.execute(
        f"SELECT symbol, arm, gross_pnl, fees, entry_slippage, exit_slippage, "
        f"entry_max_loss, entry_credit, quantity, closed_session{exp_col}{ent_col}{settle_col} "
        f"FROM curve_positions "
        f"WHERE status = 'closed'{where}",
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

    def _max_profit(r):
        # A call-credit spread's max profit AT EXPIRY is the mid-priced credit received x
        # 100 x quantity -- `entry_credit` is `worksheet_metrics`'s `short_mid - long_mid`
        # (engine.py), the same per-contract, pre-multiplier gross credit `entry_max_loss`
        # is itself derived from (`width - credit`), so this uses no new relationship, only
        # the column `_capital` above already trusts.
        if r["entry_credit"] is None:
            return None
        mp = float(r["entry_credit"]) * 100 * (r["quantity"] or 1)
        return round(mp, 2) if mp > 0 else None

    return [
        {
            "arm": r["arm"] or CURVE_UNTAGGED,
            "experiment_id": (r["experiment_id"] if has_exp else None),
            "symbol": r["symbol"],
            "strategy": "curve_vx",
            "gross_pnl": (r["gross_pnl"] or 0.0),
            "cost": (r["fees"] or 0.0),
            "net_pnl": _net(r["gross_pnl"] or 0.0, r["fees"]),
            "slippage": _slip(r),
            **_cost_split("curve_vx", r["fees"] or 0.0, _get(r, "settlement_fees"), _slip(r)),
            "entry_credit": (
                round(float(r["entry_credit"]) * 100 * (r["quantity"] or 1), 2)
                if r["entry_credit"] is not None
                else None
            ),
            "capital": _capital(r),
            "max_profit": _max_profit(r),
            "session": r["closed_session"] or "",
            "entry_session": r["entry_session"] if has_ent else None,
        }
        for r in rows
    ]


def _curve_open(conn) -> list[dict]:
    """curve's `curve_positions` still on the book — a position lives ~30-45 DTE and carries
    overnight throughout. Capital at risk is the spread's max loss (defined risk); a
    `short_settled` position's delivered/received shares are the one leg that bound does not
    cover, per the module's own caveat."""
    rows = conn.execute(
        "SELECT symbol, arm, entry_max_loss, quantity, entry_session FROM curve_positions "
        f"WHERE status NOT IN {_NOT_ON_BOOK}"
    ).fetchall()
    return [
        {
            "arm": r["arm"] or CURVE_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": "curve_vx",
            "capital": (
                round(float(r["entry_max_loss"]) * 100 * (r["quantity"] or 1), 2)
                if r["entry_max_loss"] is not None
                else None
            ),
            "session": r["entry_session"] or "",
        }
        for r in rows
    ]


def _contango_closed(conn, start: str | None = None, end: str | None = None) -> list[dict]:
    """contango's closed holding stints. The module stores the whole money layout on the row
    (entry + exit + distributions = gross; gross - fees - slippage = net), so this reads it rather
    than re-deriving it. `max_profit` is None: a fund holding has no ceiling."""
    where, params = _session_where("exit_session", start, end)
    ent_col, has_ent = _entry_select(conn, "contango_positions")
    rows = conn.execute(
        f"SELECT arm, symbol, shares, entry_mid, gross_pnl, fees, slippage, net_pnl, exit_session{ent_col} "
        f"FROM contango_positions WHERE status = 'closed'{where}",
        params,
    ).fetchall()
    return [
        {
            "arm": r["arm"],
            "experiment_id": None,
            "symbol": r["symbol"],
            "strategy": "contango_etf",
            # Read as stored, and a NULL stays None (n/r): `or 0.0` reported an unrecorded stint as
            # a flat one, which a total then counted as a real, costless trade.
            "gross_pnl": r["gross_pnl"],
            "cost": _round2(_plus(r["fees"], r["slippage"])),
            "net_pnl": r["net_pnl"],
            "slippage": r["slippage"],
            # Shares: no exercise, assignment or delivery charge exists, and none is recorded --
            # settlement reads None (n/r), as the console's contango page shows it.
            **_cost_split("contango_etf", _round2(_plus(r["fees"], r["slippage"])), None, r["slippage"]),
            "entry_credit": None,
            "capital": round(r["shares"] * r["entry_mid"], 2) if r["entry_mid"] is not None else None,
            "max_profit": None,
            "session": r["exit_session"] or "",
            "entry_session": r["entry_session"] if has_ent else None,
        }
        for r in rows
    ]


def _contango_open(conn) -> list[dict]:
    """contango's open stint per arm: an arm always holds one fund, risk or cash, overnight."""
    rows = conn.execute(
        "SELECT arm, symbol, shares, entry_mid, entry_session FROM contango_positions WHERE status = 'open'"
    ).fetchall()
    return [
        {
            "arm": r["arm"],
            "symbol": r["symbol"],
            "strategy": "contango_etf",
            "capital": round(r["shares"] * r["entry_mid"], 2) if r["entry_mid"] is not None else None,
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
    is that module's TOTAL modeled cost (entry + add-on entry, each with its slippage, +
    settlement), so net is one subtraction. Capital = the structure's worst-case max loss
    (entry_max_loss, already the larger of the up/down loss) x100xqty."""
    where, params = _session_where("closed_session", start, end)
    exp_col, has_exp = _experiment_select(conn, "bwb_positions")
    cols = _table_cols(conn, "bwb_positions")
    has_slip = "entry_slippage" in cols
    slip_cols = (
        ", entry_slippage" + (", addon_slippage" if "addon_slippage" in cols else "") if has_slip else ""
    )
    src_col = ", fees_source" if "fees_source" in cols else ""
    ent_col, has_ent = _entry_select(conn, "bwb_positions")
    extra = _opt(cols, "settlement_fees") + _opt(cols, "entry_credit")
    rows = conn.execute(
        f"SELECT symbol, arm, gross_pnl, fees, entry_max_loss, quantity, closed_session{exp_col}"
        f"{ent_col}{slip_cols}{src_col}{extra} FROM bwb_positions WHERE status = 'closed'{where}",
        params,
    ).fetchall()

    def _capital(r):
        if r["entry_max_loss"] is None:
            return None
        return round(float(r["entry_max_loss"]) * 100 * (r["quantity"] or 1), 2)

    def _slip(r):
        # Paper fills are at mid and the modeled slippage is charged inside `fees` -- entry plus a
        # fired add-on's (the console's bwbTradeCash reads the same two columns). A live row
        # (`fees_source` set) MEASURES slippage against the mid it asked from and never charges it,
        # so it has no charged slippage to report. None, not zero, for a row that predates the column.
        # This was None for every row until 2026-10-12, so calibration's net_pnl_2x_slippage read
        # bwb as having no slippage sensitivity at all.
        if not has_slip or r["entry_slippage"] is None:
            return None
        if src_col and r["fees_source"] is not None:
            return None
        addon = r["addon_slippage"] if "addon_slippage" in r.keys() else None
        return round((r["entry_slippage"] or 0.0) + (addon or 0.0), 2)

    return [
        {
            "arm": r["arm"] or BWB_UNTAGGED,
            "experiment_id": (r["experiment_id"] if has_exp else None),
            "symbol": r["symbol"],
            "strategy": "bwb_132",
            "gross_pnl": (r["gross_pnl"] or 0.0),
            "cost": (r["fees"] or 0.0),
            "net_pnl": _net(r["gross_pnl"] or 0.0, r["fees"]),
            "slippage": _slip(r),
            **_cost_split("bwb_132", r["fees"] or 0.0, _get(r, "settlement_fees"), _slip(r)),
            # The base fly's credit; a fired add-on's credit is its own figure and not included.
            "entry_credit": (
                round(float(r["entry_credit"]) * 100 * (r["quantity"] or 1), 2)
                if _get(r, "entry_credit") is not None
                else None
            ),
            "capital": _capital(r),
            # A broken-wing fly's max profit depends on the strike geometry (which wing is
            # narrower, where the short strikes sit), not on entry_credit alone the way a plain
            # 2-leg credit spread's ceiling is (curve_vx, above) -- and a fired reversal add-on
            # turns the structure into a 1-3-2 mid-life, changing that geometry again. Unmeasured
            # rather than guessed; revisit alongside the module's own strike-level records.
            "max_profit": None,
            "session": r["closed_session"] or "",
            "entry_session": r["entry_session"] if has_ent else None,
        }
        for r in rows
    ]


def _bwb_open(conn) -> list[dict]:
    """bwb's `bwb_positions` still on the book — the daily ladder holds ~5-7 concurrent positions
    per book at steady state, all carrying overnight through their ~7 DTE life. Capital at risk is
    the structure's defined max loss."""
    rows = conn.execute(
        "SELECT symbol, arm, entry_max_loss, quantity, entry_session FROM bwb_positions "
        f"WHERE status NOT IN {_NOT_ON_BOOK}"
    ).fetchall()
    return [
        {
            "arm": r["arm"] or BWB_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": "bwb_132",
            "capital": (
                round(float(r["entry_max_loss"]) * 100 * (r["quantity"] or 1), 2)
                if r["entry_max_loss"] is not None
                else None
            ),
            "session": r["entry_session"] or "",
        }
        for r in rows
    ]


# Schemas whose closed records carry the module's own stamped `era`, so a reader can scope to the
# module's evidence window by the stamp rather than by a date guess that could disagree with it.
# pmcc stamps every row at entry (`book.enter_position`) and moves eras on a roster change, not a
# date a `start` bound could stand in for. A schema joins here only once its records carry `era`.
ERA_SCHEMAS = frozenset({"pmcc"})


READERS = {
    "meic_ic": _meic_closed,
    "earnings": _earnings_closed,
    "fly_book": _flies_closed,
    "dc_week": _calendars_closed,
    "pmcc": _pmcc_closed,
    "curve_vx": _curve_closed,
    "bwb_132": _bwb_closed,
    "contango_etf": _contango_closed,
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
    arm = core_db.arm_column(conn, "trades")
    rows = conn.execute(
        f"SELECT symbol, {arm} AS arm, strategy, capital_at_risk, opened_at FROM trades"
        " WHERE closed_at IS NULL"
    ).fetchall()
    return [
        {
            "arm": r["arm"] or EARNINGS_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": r["strategy"],
            "capital": r["capital_at_risk"],
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
        "SELECT symbol, arm, structure, entry_debit, quantity, entry_session FROM dc_positions "
        f"WHERE status NOT IN {_NOT_ON_BOOK}"
    ).fetchall()
    return [
        {
            "arm": r["arm"] or CALENDARS_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": r["structure"],
            "capital": (
                round(float(r["entry_debit"]) * 100 * (r["quantity"] or 1), 2)
                if r["entry_debit"] is not None
                else None
            ),
            "session": r["entry_session"] or "",
        }
        for r in rows
    ]


def _pmcc_open(conn) -> list[dict]:
    """pmcc's `pmcc_positions` still on the book — a held-long position lives about ten months (a
    retired weekly control one lived ~1–2 weeks) and carries overnight throughout, plus the
    assigned-shares weekend between a Friday settlement and the Monday disposal. Capital at risk is
    the net debit (defined max loss); a `short_settled` position's delivered shares are the one leg
    that bound does not cover, per the module's own caveat — the debit stays the honest conservative
    bound without re-deriving marks here."""
    rows = conn.execute(
        "SELECT symbol, arm, net_debit, quantity, entry_session FROM pmcc_positions "
        f"WHERE status NOT IN {_NOT_ON_BOOK}"
    ).fetchall()
    return [
        {
            "arm": r["arm"] or PMCC_UNTAGGED,
            "symbol": r["symbol"],
            "strategy": "pmcc",
            "capital": (
                round(float(r["net_debit"]) * 100 * (r["quantity"] or 1), 2)
                if r["net_debit"] is not None
                else None
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
    "pmcc": _pmcc_open,
    "curve_vx": _curve_open,
    "bwb_132": _bwb_open,
    "contango_etf": _contango_open,
}


def _meic_held(conn) -> list[dict]:
    """MEIC ICs held right now: no exit time and not cancelled. A cancelled entry was never a
    position -- it has no exit time because it never filled, or because a session was struck whole
    (the 2026-10-08 outage cancelled 184 unmanaged entries)."""
    cols = _table_cols(conn, "ic_trades")
    arm = core_db.arm_column(conn, "ic_trades")
    held = " AND COALESCE(status, '') != 'cancelled'" if "status" in cols else ""
    session = "substr(entry_time, 1, 10)" if "entry_time" in cols else "''"
    rows = conn.execute(
        f"SELECT symbol, {arm} AS arm, {session} AS session FROM ic_trades WHERE exit_time IS NULL{held}"
    ).fetchall()
    return [
        {"arm": r["arm"] or MEIC_UNTAGGED, "symbol": r["symbol"], "strategy": None, "capital": None,
         "session": r["session"] or ""}
        for r in rows
    ]  # fmt: skip


def _flies_held(conn) -> list[dict]:
    """flies positions held right now (status 'open', not voided), before the session's settlement."""
    rows = conn.execute(
        f"SELECT symbol, arm, trade_date FROM fly_positions WHERE status = 'open'{_flies_not_void(conn)}"
    ).fetchall()
    return [
        {"arm": r["arm"] or FLIES_UNTAGGED, "symbol": r["symbol"], "strategy": None, "capital": None,
         "session": r["trade_date"] or ""}
        for r in rows
    ]  # fmt: skip


#: "What is held right now", intraday included -- OPEN_READERS with the two 0DTE schemas answered
#: instead of emptied. OPEN_READERS is the overnight view a report rolls up, where an unsettled 0DTE
#: position is a settlement lag and not a hold; the orchestrator's reconcile shows the paper ledgers'
#: own position count beside the broker's, where a position open at 11:00 is exactly what it means.
#: Until 2026-10-12 reconcile kept eight hand-written readers of its own, which had drifted from
#: these on what "open" excludes.
HELD_READERS = {**OPEN_READERS, "meic_ic": _meic_held, "fly_book": _flies_held}


# Retired spellings of a schema id, resolved for good. A config is a file a person keeps across
# upgrades with no migration (root CLAUDE.md), so `"trade_schema": "pmcc_99"` -- every install before
# 2026-10-04, when the module's name lost its "-99" -- must keep reading. Registries are keyed by the
# canonical id only; every lookup by a configured or requested id goes through `canonical_schema`.
SCHEMA_ALIASES = {"pmcc_99": "pmcc"}


def canonical_schema(schema: str | None) -> str | None:
    """The canonical id for `schema` -- itself, unless it is a retired spelling."""
    return SCHEMA_ALIASES.get(schema, schema) if schema is not None else None


# --------------------------------------------------------------------------- concentration
# A module net is an average over arms, and averaging is exactly what hides the finding when the
# arms are the experiment. Requested by the advisor on 2026-08-19 after flies published +6,748.01
# for a session in which ONE seven-fill book returned +7,828.42 and the other twelve together came
# to -1,080.41: the sign of the day rested on an arm with an eleven-trade lifetime, whose book that
# session carried a modelled worst 3.5x the credit it collected and settled positive because price
# happened to stay put. Two sessions earlier in the same journal make the point in the other
# direction, -8,071.69 and -4,023.05, both dominated by width-ladder books on 4-7 fills.
#
# "No bounded parameter can fix a presentation defect" was the proposal's closing line, and it is
# right: this is not a rule about which trades to take, it is a rule about which totals may be read
# on their own.


def concentration(
    records: list[dict], *, field: str = "arm", out: str | None = None, net_key: str = "net_pnl"
) -> dict:
    """How much of a net rests on its single largest contributor.

    `field` is the record field to group by; `out` is what that field is CALLED in the result,
    defaulting to `field`. They are separate for one reason: this function's output goes straight
    into the review fact set, of which 22 are on disk keyed `by_profile`, and renaming a key in a
    persisted artifact is a versioned change rather than a rename. So review reads `arm` and writes
    `profile` until that artifact's own version bump. Nothing else passes `out`.

    Takes the normalised records every reader in this module yields, so it answers the same way for
    every schema — the point of the request was "for every module net", and a per-module
    implementation would be seven chances to disagree about what a share is.

    Two share denominators, because one of them lies in exactly the case worth flagging:

    * ``share_of_net`` is the signed arm/total. It is what a reader expects, and it goes past 100%
      whenever the other arms net against the leader — width-10's 116% of that flies session is the
      honest number and it is *why* the total cannot be read alone. ``None`` when the total is ~0,
      where the ratio is meaningless rather than large.
    * ``share_of_movement`` is |arm| / sum|arm|, which is bounded, stable near a zero total, and
      answers "how much of what happened was this one arm".

    ``sign_flips_without_largest`` is the field the request was really about. A total that changes
    sign when its biggest contributor is removed is not a measurement of the module; it is a
    measurement of that arm, and every reader of it should be told so.

    This function labels nothing PROVISIONAL. Whether the largest contributor clears its module's
    sample and day bars is that module's own rule, and importing qualification into the ledger layer
    would put two gates in play. The facts it needs — the leader's trade and session counts — are
    returned so the caller can apply its own.
    """
    out = out or field
    total = 0.0
    per: dict[str, dict] = {}
    for record in records:
        name = record.get(field) or "unassigned"
        net = record.get(net_key) or 0.0
        slot = per.setdefault(name, {out: name, "net": 0.0, "trades": 0, "sessions": set()})
        slot["net"] += net
        slot["trades"] += 1
        if record.get("session"):
            slot["sessions"].add(record["session"])
        total += net

    movement = sum(abs(slot["net"]) for slot in per.values())
    rows = []
    for slot in per.values():
        rows.append(
            {
                out: slot[out],
                "net": round(slot["net"], 2),
                "trades": slot["trades"],
                "sessions": len(slot["sessions"]),
                "share_of_net": round(slot["net"] / total, 4) if abs(total) > 1e-9 else None,
                "share_of_movement": round(abs(slot["net"]) / movement, 4) if movement > 0 else None,
            }
        )
    # Ranked by absolute contribution: the largest mover, not the largest winner. An arm that lost
    # more than everything else made is the same presentation problem wearing the other sign.
    rows.sort(key=lambda r: abs(r["net"]), reverse=True)

    if not rows:
        return {
            "net": 0.0,
            "by_" + out: [],
            "largest": None,
            "net_excluding_largest": 0.0,
            "sign_flips_without_largest": False,
            "contributors": 0,
        }

    largest = rows[0]
    without = round(total - largest["net"], 2)
    flips = abs(total) > 1e-9 and abs(without) > 1e-9 and (total > 0) != (without > 0)
    return {
        "net": round(total, 2),
        "by_" + out: rows,
        "largest": largest,
        "net_excluding_largest": without,
        # The headline caveat: the module's sign is this arm's sign.
        "sign_flips_without_largest": flips,
        "contributors": len(rows),
    }
