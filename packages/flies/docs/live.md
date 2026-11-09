# Flies live trading

The rulebook for the flies live path: what it is, the gates in front of it, how it is built, and how its
results are kept apart from paper. Read it before changing anything about how live runs. The module is
retired and ships off; `/live-flies-start` and `--arm` refuse while `modules.flies.enabled` is false. The
rules in [../CLAUDE.md](../CLAUDE.md) take precedence.

Live is a small, tightly bounded measurement pilot: real orders, real money, one arm, one symbol, one
contract per order. It exists to surface real trading-mechanics issues (order fills, timing,
cancellations) and to measure the live completion rate, not to prove the strategy works. It is not a claim
that the statistical bar below has been cleared; running it is a logged, named exception to that bar.

Every suite guardrail applies: paper and live money are kept apart in every report, nothing on the
trading-decision path uses AI or the network, credentials live only in the operating system's credential
store, account numbers are always masked (`****1234`), and only strictly defined-risk structures trade live.

## The current settings

- **One arm:** `live.arm`, `control` (plain at-the-money centring, the arm every alternative is measured
  against). Its settings do not change mid-pilot, because changing them would make the measurement
  meaningless. Only a variant that has earned it trades live.
- **One symbol:** SPX, never SPX and XSP in the same pilot. Both are European and cash-settled, so neither
  carries assignment risk. SPX is the choice because credit scales with structure width while the flat
  $5-per-ITM-strike settlement fee does not: SPX's 5-wide structure collects far more per fee dollar than
  1-wide XSP (about a 10.9% fee drag against 41.4%), at the price of a 5x larger per-contract risk.
- **Sizing:** a local worst-case buying-power cap, `live.max_open_margin_dollars`, computed from the ledger
  and the order alone (never a broker balance read, so a balance read cannot block a legitimate entry),
  with an uncompleted vertical counted at its worst case. There is no limit on incomplete positions by
  default; `live.max_incomplete_spreads: 1` restores one at a time.
- **Entry window:** the live loop starts at `live.no_entry_before` (10:15), later than paper's 10:00,
  because under a one-at-a-time rule an early entry blocks the better slots.
- **Completion:** the completing order rests as a Day limit at `live_orders.max_safe_completion_debit` the
  moment the entry fills, and lives to `live.completion_cutoff` (the 16:00 close). Changing the cutoff is a
  live `completion_rule` break.
- **Outright entries are not traded live.** They are off in paper too (`entry_modes` is `["legged"]`).
  Any live version would need a real buying-power check and a run through paper first.
- **Booked prices** are the broker's fill prices, never the order limit; the limit stays on
  `fly_live_orders.limit_price`.

## Gate 0: the bar the strategy needs to clear

This is the "is it a working strategy" question, answered from the paper data with the module's own
analytics. A person reads the reports; no switch flips by itself.

1. **Completion rate at or above the real break-even rate**, sustained over at least 20 trading sessions
   and 100 legged entries. Break-even is recalculated from the window's own ratio of what a miss costs to
   what a completion earns, never a fixed number.
2. **The book is net profitable after all fees** over that window, and clears the suite's hardened bar:
   at least 14 days, at least 20 sample trades, a significance check, and a stress test with
   worse-than-modelled slippage.
3. **The winning arm is genuinely separable from the baseline** (it made different choices, not the same
   choices measured twice; ATM arms agree on centre by construction, so their agreement proves nothing).
4. **The wide-wing question is answered one way or the other**; a negative answer is an equally valid
   outcome.

The rollout is: a broker smoke test (a real order pair run through the broker's dry-run check, placing
nothing); the measurement pilot (one arm under the cap, at least 15 sessions, collecting the real
completion rate, fill quality, real fees against modelled, and completion latency against paper); then
normal size at one contract only if the live completion rate clears the recalculated break-even. Scaling
beyond one contract is a separate decision for a person and is not addressed here.

## The paper-ceiling rule and the abort check

In paper, "did the completing leg get cheap enough" is a clean check against a recorded price. Live, the
first leg fills first and the completing purchase rests as a limit that may sit unfilled or fill worse, so
the paper completion rate is a ceiling for the live one, not a prediction. A built-in abort check guards
against a bad surprise: once at least 30 live legged entries exist, `analytics.live_vs_paper` flags
`abort_rule.triggered` when the live completion rate is more than 15 points below paper over the same days.
It appears in `live_loop --status` and halts nothing; stopping the pilot is a person's call. The
strategy's whole edge is the completion rate, so a persistent gap that size means the paper ceiling is
not achievable.

## How live is built

Live reuses the paper decision logic unchanged (`fly.py` and `engine.py` do not know whether the order that
follows is real) and adds only the parts that talk to the broker.

- **A separate live process and ledger.** Live runs beside paper, which keeps running the same days as the
  point of comparison. Live results appear only in the live view; every "what's working" or calibration
  report is paper-only, by construction and by test.
- **The streamer comes before API calls.** Pricing reads the shared stream cache. The broker is used only
  to act (place or cancel) or to confirm what only it knows (a fill). Narrow exceptions are listed in
  [../CLAUDE.md](../CLAUDE.md) (the fresh re-price before an entry, `live.entry_pricing`, the order-alert
  stream).
- **Several independent kill switches**, any of which stops new trades: the master `live.enabled` setting
  (off by default, checked every tick); the suite-wide halt flag, which blocks new entries within about a
  minute without touching anything open; the worst-case buying-power cap on every order; and the per-day
  arm. Open positions are always fully defined-risk and run to settlement.
- **Settlement discipline.** Results book against the official closing print where one exists; the loop
  fetches it first and settles provisionally on the last trade only when no source has posted one, then
  keeps retrying. A daily reconciliation compares what the system believes is open with the account, and
  `fee_reconcile` compares modelled fees with real ones per settlement symbol.
- **An automated monitor** watches that the live process is running and healthy in market hours. It can
  alert or set the halt flag, and never places or cancels an order. It also backstops the end-of-day
  shutoff with the halt flag.
- **Fills are noticed through the broker.** Every tick polls pending orders; while one is pending a short
  helper checks about every 10 seconds, gated by the cache. Only a broker-confirmed fill updates the
  ledger. The entry order is re-checked each minute and cancelled and replaced only when the picked
  strike or price has genuinely moved; the completing order is placed once, at a price that already builds
  in the floor requirement, so there is nothing to chase.

## Arming, day to day

Live is armed one day at a time. `/live-flies-start` with a fresh confirmation (or, without Claude Code,
`python -m cherrypick.flies.live_loop --arm --confirm YES`, which prints `DISCLAIMER.md` first) writes the
day's arm record; both refuse while `modules.flies.enabled` is false. While the record is valid the
supervisor runs the `flies-live` job once a minute. It turns itself off at `live.disarm_time` (17:00 ET
by default), so the next day needs a fresh confirmation. `--readiness` lists unmet gates, `--status` shows
the state, `--disarm` and `/live-flies-start --stop` stop it, and setting the suite halt flag stops new
trades within about a minute.

## What live does not do

No adjusting a position after it opens (hold to settlement, in paper and live). No outright entries. No
more than one arm live at once. No SPX and XSP live together. No automated or AI-driven decision near a
live order. **One written exception:** the intraday agent may record decisions against the live arm in
shadow and, only when its qualification allows and the day's selection names it, block live entries on the
trend's losing side ([intraday-agent.md](intraday-agent.md)). It never places an order; the live closing
path is not built.
