# cherrypick-weeklyput

⚠️ **EXPERIMENTAL.** Item 57's unhedged arm in paper: on each weekly expiry that is not a standard
monthly, sell the put nearest 10 delta on SPX (SPXW), SPY, QQQ and IWM seven calendar days before
the expiry, and hold it to the expiry's posted close. No hedge, no stop, no exit. **Paper-only,
credential-free, no live path** (posture: root file). Ledger:
`~/.cherrypick/data/weeklyput/paper_trades.db`. Declared for paper as item 69
(`research/edge-hypotheses-plan.md`).

## Why it exists

Item 46 passed one cell of forty, the equity-index 10-delta put at 7 days on the monthly expiries,
hedged. Item 57 read the same trade on the weekly expiries item 46 never saw: the hedged arm failed
and the **unhedged** arm passed every check (Holm across the two arms, both time-halves, 1.5x costs,
3 of 4 names). Its pass branch is a paper arm, and this is that arm. Its figures stay local
(`~/.cherrypick/data/backtest/results/research/item57/`), as every licensed result does.

## The rule (item 57's, unchanged)

- **Expiries** (`clock.py`): a week's Friday, or the Thursday when the Friday is a holiday; never a
  standard monthly (third Friday, or the session before it); never a weekly whose 7-day trade shares
  a session with a monthly's. `test_clock.py::test_calendar_matches_item57` checks every week
  2016-03 to 2026-10 against the study's own calendar.
- **Entry session:** the first session on or after the expiry minus 7 calendar days.
- **Leg** (`engine.py`): the put whose Black delta, on the parity forward at zero rate, is nearest
  0.10, among puts with a positive bid and an implied vol, up to 5% in the money. The 8- and
  12-delta neighbours are recorded, never traded.
- **Money:** one contract a name, sold at mid; slippage `core.fees`' fraction of the spread as its
  own column; trading fees `core.fees.ic_open_fee` for one sold leg; settled at intrinsic, with the
  cash-settlement charge on SPX and, on an ETF, the assignment and the delivered shares sold at the
  same close. `test_engine.py::test_matches_item57_unhedged_trade` checks pick, credit, costs and
  net against item 57's `trade_pair` to the cent.
- **Measured:** net in bp of the entry forward notional (100 x F); per cycle, the equal-weight mean
  over the names that traded it (`analytics.cycles`).

## What paper adds, and says

1. **The entry is a close-of-session fill, not an end-of-day quote.** The window opens 15 minutes
   before the close (15:45, or 12:45 on an early close) and stays open 10. A name not entered by its
   end is `missed` for that cycle; **nothing fills after the window** (guard mutant
   `weeklyput-no-fill-after-window`). Item 69's `scripts/weeklyput_item69.py track` reads the gap
   against item 57's replay of the same trades.
2. **A 10-delta that is not bracketed is refused** (`delta_not_bracketed`). The study's chains held
   every strike; the stream cache holds what was subscribed. The nearest quoted put is a different
   trade when the window stops short of the true 10-delta strike.
3. **The settlement print is the posted close** (`stream_summary`: the next session's
   `prev_day_close`, or the session's own `day_close` once it is over), never the last trade, which
   for an ETF runs after the bell. So a Friday expiry usually settles on Monday morning. A close
   still missing two sessions on is logged for `--settle --date --symbol --price`.
4. **Early assignment is measured, never modelled.** An in-the-money ETF put whose time value falls
   under `assignment_extrinsic` is flagged (`assignment_exposed_at`); the paper result books the
   put to expiry regardless, so the ETF names stay an upper bound, as item 57 said.
5. **No share is carried past the expiry session.** An assigned ETF put is booked as shares sold at
   the same close, the study's convention; a real assignment would carry the weekend gap.
6. **A refused mark is still a row**, and so is a missed cycle (`weeklyput_entries`).

## The money layout

A position is one short put. Signed cash flow, whole-position dollars:
`entry_value` (the credit, +) + `exit_value` (minus the intrinsic paid, or 0) = `gross_pnl`;
`gross_pnl` − `fees` − `settlement` − `slippage` = `net_pnl`. `exit_reason` is `expired`,
`settled` (SPX in the money) or `assigned` (an ETF in the money). `net_bp` is net over `notional`.

## Stream needs (`stream_request.py`)

The four names as underlyings, Quote-only, `nearest_window` false; the cycle's expiry only from the
session before its entry session through the entry session; on the entry session alone, the listed
puts `put_band` below spot (1.5% to 12%), named one by one, because a per-symbol `window_hints`
would widen every SPX window in the suite; every open put through a ledger `leg_source`. About 300
extra subscriptions on an entry session and a handful the rest of the week.

## Layout

`clock.py` (the calendar and the window, pure), `engine.py` (forward, pick, sale, settlement,
pure), `provider.py` (read-only cache views), `db.py` (ledger), `paper_loop.py` (the tick),
`stream_request.py`, `analytics.py` (cycles and the forward read), `cli.py` (status, positions,
cycles, summary, marks, calendar).

## Commands

Commands to run the package, its tests and its lint are in [README.md](README.md#commands).

`paper_loop --force` acts outside the window, for testing only.

## Boundaries

Measurement-affecting changes (the window, the band, the names, the delta, the settlement print)
go to a declared boundary with a `measurement_breaks` row. The rule is item 57's: a change to it is
a different trade from the one judged, and needs its own item.
