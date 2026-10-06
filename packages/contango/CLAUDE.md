# cherrypick-contango

The VIX term-structure switch, held in shares: SVXY (−0.5× short VIX futures) while VIX/VIX3M
says contango, SHV (T-bills) while it doesn't. One decision per session, ten minutes before the
close, off the shared stream cache. **Paper-only, credential-free, no live path** (posture: root
file). Ledger: `~/.cherrypick/data/contango/paper_trades.db`.

## Why this exists beside curve

Curve sells VXX call spreads on the same signal. Its edge is real, but costs take about half of
each credit and the market is about ten contracts deep (2026-10-05 analysis). Holding the fund
instead costs a few basis points a side at any size this suite could run, so this module measures
the thesis without the option costs, and curve keeps measuring the option expression. Neither
replaces the other.

## The arms (one variable each)

- **`control`**: in below 0.97, out at 0.97. A plain switch, the gate curve enters on.
- **`flipexit`**: in below 0.97, out only once the ratio reaches 1.0. Between the two it keeps
  whatever it holds, so it switches about half as often.

An arm is two thresholds (`enter_below`, `exit_at_or_above`) and two funds (`risk_symbol`,
`cash_symbol`), all config. An inverted pair is refused at the first tick. SVIX (−1×) is one
config block away and deliberately not declared, because it replayed worse.

**The replay is the reference** (`scripts/contango_replay.py`, run through `regime.target_state`,
the loop's own rule; SHV total return as cash; 2 bps a side):

| 2018-03 → 2026-10 | CAGR | max drawdown | switches |
|---|---|---|---|
| control | 7.4% | −39.8% | 165 |
| flipexit | 9.8% | −41.6% | 81 |
| buy-and-hold SVXY | 12.3% | −62.2% | 0 |

**The gate buys drawdown, not return.** Holding SVXY outright earned more. The forward question
is whether giving up about 2.5–5 points a year for about 20 points less drawdown holds up, and
whether real switching costs match the 2 bps. At 10 bps a side, control falls to 4.2% and
flipexit to 8.2%, which is why `flipexit` exists. The 2018-03 start is deliberate: SVXY was −1×
until 2018-02-27, a different fund.

## Honesty rules

1. **One decision per arm per session, in the window or not at all.** The window opens
   `decision_minutes_before_close` before the regular close (12:50 on an early close) and lasts
   `decision_window_minutes`. A tick that can't act (unmeasured regime, stale quote, spread over
   `max_spread_bps`) retries on the next tick. An arm still undecided after the window is
   recorded `missed` and keeps its holding. **Nothing fills after the window**: that would be a
   different rule from the one the replay measured. The guard mutant
   `contango-no-fill-after-window` shows the test fails if it does.
2. **A switch checks both legs before planning either**, and writes the closed stint, the new
   stint and the cash in one transaction. A sell whose buy then refused would leave an arm in
   cash its rule never chose.
3. **Fills at mid, with slippage as its own column.** Slippage is half the quoted spread, floored
   at `slippage_floor_bps` (2) of mid. Fees are `core.fees.stock_trade_fee`: buys free, sells
   pay SEC and TAF.
4. **Distributions are credited from the local technicals store**, never the network. SHV's
   monthly dividend is most of its return, so a cash stint without it would flatter the risk arm.
   A distribution is owed to a stint that held at the ex-date open: entered on an earlier session
   and not sold before the ex-date. The store's rows can land days late, and the credit lands with
   them, restating a closed stint so it still adds up.
5. **A stale VIX/VIX3M print refuses.** It is never carried forward. A day's first usable reading
   is final.
6. **An account is never re-capitalised by config.** `starting_capital` opens an arm once.
   Changing it later means a new arm and a journaled break.

## The money layout

A **position is one holding stint**: the shares bought on one switch and sold on the next. Rows
add up:

- `entry_value` (a negative debit) + `exit_value` + `distributions` = `gross_pnl`;
- `gross_pnl` − `fees` − `slippage` = `net_pnl`.

An arm's stints tile its life. `contango_sessions` is its daily NAV (cash plus the holding at
mid), and the series it is judged on: a stint's P&L can't show a drawdown inside it.

## Layout

`regime.py` (the reading and the switch rule), `engine.py` (fills, stint money, distribution
entitlement), `replay.py` (the rule over daily closes); all pure. `provider.py` (read-only stream
cache and technicals store), `db.py` (ledger), `paper_loop.py` (the tick), `stream_request.py`
(the funds and VIX/VIX3M as quote-only legs, no underlyings), `cli.py` (status, nav, stints,
metrics). `analytics.py` is the read side's one query layer: each arm's daily-NAV reading
(`core.metrics.nav`), buy-and-hold of its risk fund, and the **expected path**, which is `replay.run`
over this module's own recorded ratios and fund marks (`contango_marks`, each fund's quote at the
decision tick, from 2026-10-06), with the cash fund's distributions added back. The gap between an
arm's NAV and its expected path (`tracking`) is execution, not the rule: spread paid over 2 bps,
whole shares, missed windows.

## Suite wiring

Trade schema **`contango_etf`**: `core.ledgers` closed and open readers (cost = fees + slippage,
capital = the stint's purchase), report, reconcile, the trade notifier (a switch notifies as a
close and an entry), and the review (health = each arm's decision outcome; expected = decisions
due against decisions taken). It is in `eval_activity.NOT_APPLICABLE`: it acts only inside its
window, so an idle tick is the design and not a stall. It ships `enabled: false` in the
orchestrator's example, like curve.

**Console** (2026-10-06): `/contango`. Session (today's read, each arm's holding and decision),
regime, decisions, arms (each arm's daily-NAV tear sheet against buy-and-hold and against its own
rule), performance (NAV, underwater, monthly heat map, rolling 60-session return, stress windows),
costs (every fill's slippage against the 2 bps the replay assumed, missed windows, distributions),
positions, history (closed stints and the daily NAV log), help. The ledger is read by
`readers/contango.ts`; the tear sheet comes from `contango metrics` through `services/navBridge.ts`.
