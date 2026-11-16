# cherrypick-automations

⚠️ **EXPERIMENTAL.** A rules engine for short-dated option arms, declared as research item 75 (eight SPX
and GLD arms from a third-party portfolio, read over all history in one pass). **Paper-only,
credential-free, no live path** (posture: root file). The package is the engine and its paper loop: a
position is a ledger row (`~/.cherrypick/data/automations/paper_trades.db`), never an order. Off by default.

**The old name resolves.** The package was `packages/ensemble`. `cherrypick.ensemble.*` still imports, as
the same module objects (`src/cherrypick/ensemble/__init__.py`), so the finished item 75 scripts and a
suite config's old argv run unchanged. `config/ensemble.json`, `data/ensemble/` and the `ENSEMBLE_CONFIG`
and `ENSEMBLE_DB_PATH` variables are read while only the old one exists (`core.home.RENAMED`); the
orchestrator reads `modules.ensemble` as `modules.automations` (`config.resolve_renamed`). The ledger's
tables and its `ensemble_trade` schema keep their names: they are stored data.

## What it is

An **arm** is configuration (`arms.py`): the underlying, the expiry rule, the legs and how each strike is
placed, the entry trigger, the weekdays, the event skips, the filter gates, the exits and the size. A new
arm is a new entry (or a machine override read by `arms.load_arms`); an unknown key is refused with its
path. A new kind of filter is one function in `gates.REGISTRY`, a new strike rule one branch in
`strikes.pick`.

**Automation groups.** An arm names its `group`, a set of like automations switched as one through the
config's `groups` block (`{"ensemble": {"enabled": false}}`). `config.load_arms` switches off every arm
of a group that is off, so none of them enters; what they hold is still managed and settled, because a
position carries its own exits. A group the config names but no arm belongs to is refused. The shipped
eight are the `ensemble` group, and each carries its research `register` label, item 75's verdict
(`passed`, `failed`, `no_verdict`), written from the register and checked against its forward list
(`tests/test_groups.py`), never read from a result file. The loop's `--status` shows each group with its
arms and verdicts.

**Safeguards are stated, never defaulted.** Every arm's `limits` names `max_open` and `per_day`; the
engine reads them with no fallback, so an arm cannot run under a cap it did not state. `per_day` is 1:
the entry register keeps one row per arm and day. A replay record from before `per_day` existed runs
under what its arms ran under, one a day (`record.as_they_ran`).

**Gates run through `core.entrychecks`.** Each gate is a core condition (`gates.CONDITIONS`, core's
registry extended, its parameters read from the gate function's signature), so `arms.validate` refuses a
misspelt gate parameter at load and a gate's result is recorded as every module's are. The engine still
runs the gates in the arm's own order, market gates before the legs and structure gates after, so a
decision's first failure and its checks are what they were (item 75 reproduces identical). `CORE_STAGE`
labels each gate with core's stage by what it reads, for the builder and `core.trace`.

**A `trigger` entry** (`{"kind": "trigger", "trigger": ..., "bar_minutes": N}`) is `core.entrychecks`'
trigger: a `core.signals` firing on the trail cut into N-minute bars, or a fixed time. The shipped arms
keep their `time` and `orb` entries: the opening-range entry fires on the minute its spot crosses, a core
signal at a bar's close a minute later, so moving them would be a measurement change. An **event signal**
(on, before or after an event date, at a time) reads the event calendar's dates (`engine.events_for`):
a label whose stretch around the session is not wholly known is left out, so core refuses the signal
and the entry reads `event_dates_unknown`, never quiet.

**A daily signal** (a trigger with `"daily": true`, entry or exit, no `bar_minutes`) reads completed
daily bars only: the snapshot carries the symbol's daily bars before the session (`Snapshot.daily`, only
when an arm reads them, `arms.reads_daily`; the stream request then asks for `DAILY_HISTORY` sessions),
and core acts today, from the window's open, on a firing on the previous trading day's close. Bars
that stop before that day read `stale_bars`. The item 75 backtest driver carries no daily bars, so a
daily signal is backtested only once the automation driver exists (#1201).

**Library signals.** A trigger's signal (or an `only_when` entry) can be a reference to the signal
library, `{"library": name, "version": hash}` (`core.signallib`). `config.load_arms` swaps it for
that version's spec before validating, reading the library file only when an arm holds a reference;
one it cannot answer stops the load. The version is always named, so moving a paper automation to a
new version is a config edit, a measurement change made at a boundary.

**Volume.** A signal that reads bar volume (a volume-weighted VWAP, relative volume, a volume ratio;
`core.signals.needs_volume`) names the symbol whose volume weighs the bars: `volume` on the trigger
entry or the signal exit (`"volume": "SPY"` for SPX, which has none of its own). Without it the arm is
refused at load, as is a `volume` on a signal that reads none, a daily one, or relative volume over
more than `PRIOR_SESSIONS` sessions. Paper records that symbol's spot and the feed's day volume each
minute (`ensemble_trail.day_volume`, and the stream request asks for it); a minute's volume is the
counter's rise to the next recorded minute (`db.minute_volumes`), absent where a minute was not
sampled or the counter fell. The driver takes the same minutes from the recorded one-minute bars.
`core.signals.with_volume` puts them on the bars for both; a bar missing any minute has no volume, and
the trigger reads `no_volume` rather than a zero. A VWAP with equal weights reads no volume.

**One firing rule.** An intraday signal reads the earlier sessions too, as a signal study over the whole
history does: a seeded indicator is warm at the open, arming carries across the close, and a gap or a
prior-session break has the prior session. So for an arm that reads one (`arms.reads_prior_trail`) the
snapshot carries up to `PRIOR_SESSIONS` (5) earlier sessions' minute trail (`Snapshot.prior`; paper
reads its recorded trail, `feed.prior_trails`, and a day the loop did not run is absent), and the engine
builds bars over them and today. `tests/test_one_firing_rule.py` holds that a study, core's trigger and
the engine ticking minute by minute fire on the same minute.

**A `signal` exit** (`exit.signal`: `{"trigger": ..., "bar_minutes": N}`, the entry trigger's shape)
closes a position when its signal fires on the underlying, inside its window. `manage` checks it after
the touch and the profit target and before the time exit. On the entry day only a firing after the
entry minute counts, and core takes the day's first firing in the window, so a signal that first fired
before the entry does not exit that day. The backtest driver and the paper loop both run it through
`manage`, so a signal exit is backtested exactly as it trades. Paper manages a position on a snapshot
that carries what its exit reads (`feed.position_snapshot`): today's recorded trail, the earlier
sessions or the daily bars, the event calendar, and each leg's delta for a delta limit.

**The other exits**, checked in this order with the touch first: `avoid_events` (a forced close
`before_close_min`, default 60, before the close on the session before a listed event; a position
carried into the event day closes at once, one entered on it is not closed for it, and a day the
calendar cannot answer holds), the stops (`stop_credit_multiple`, a credit entry's loss at m times its
credit, never on a debit; `stop_debit_pct`, a debit entry's loss at p% of its debit, never on a credit;
`stop_dollars`), `trailing_stop` (`{"dollars": d, "activate_dollars": a}`: once the open P&L has reached
a, a fall of d from its best), the profit targets (`profit_target_pct` of max profit, `profit_dollars`),
`delta_limit` (the structure's delta per unit either way; a leg without a delta is no reading, never
zero), the signal, `close_at_dte` (calendar days to expiry, from the first tick of that session, never
on the entry day), then minutes before expiry. Dollar rules read the open P&L at the mid, whole-position
dollars. `max_spread: x` holds every exit while the structure's bid/ask is wider than $x a share, and a
mark read through it does not move the trailing stop's best; a close held to expiry settles. The best
open P&L is the position's (`Position.peak_pnl`): the driver keeps the object, paper keeps
`ensemble_positions.peak_pnl`. On end-of-day chains every exit is read once a session, at 15:59.

`engine.decide(arm, snapshot)` is the one decision: pure and deterministic, it returns the entry (legs,
quantity, prices at mid, natural and working, the slippage for each fill kind, the opening fee) or the
first failing check, with every check it read. `manage` says whether an open position should close now,
`close_position` and `settle_position` return the trade. The backtest driver and the paper loop differ
only in how they **build** the `Snapshot` (`snapshot.build`, store rows against stream-cache rows of the same
shape), so `tests/test_paths_agree.py` holds that a recorded snapshot replays to the same decision.

**The automation driver** (`driver.py`, over `history.StoreHistory`) is the backtest path for any arm:
every stored minute it manages open positions and then decides the arms in their entry window through
`paper.decide_all`, each on a snapshot `snapshot.build` makes from the stored day as `feed.snapshot_for`
makes one from the stream cache, and settles what is open at its expiry's close. `StoreHistory` reads
any file in the gex recorder's schema (our recorder, the vendor's 0DTE stores) through
`core.quotehistory` and `core.regime`, never a reader of its own. `tests/test_driver.py` holds a day run
through the paper loop and through the driver to the same trade, to the cent. Choosing the store and
writing the result is the backtest package's `scripts/automation_backtest.py`. Item 75's own driver
stays as its study left it.

**Two kinds of history.** `StoreHistory` is sampled through the session (same-day expiries).
`DailyChainHistory` is end-of-day chains (Dolt, the vendor's store): one tick a session at 15:59, the
last minute paper acts in, positions held across sessions and settled at their expiry's close (on the
next session read, if the run skips the expiry day). An arm whose entry window does not hold that tick
counts `needs_intraday` for the session, never moved to the close; a session the source holds nothing
for counts `no_data`. A close that does not price on the tick waits for the next session's, and at
expiry the position settles instead: an end-of-day source has no later minute.

## The rules and their resolutions

The arms are item 75's in our own words; the declaration is the authority. Where the source's text left a
reading open, the engine fixes one and says so here, because each is a measurement choice:

- **Standard-deviation move** is the move from the previous close over one day's sigma, taken from the
  previous close's VIX (`indicators.daily_sigma`).
- **GEX ratio** is `(call + put) / min(call, |put|)` over the window of strikes (`gates` docstring); the
  source does not define it.
- **Intraday RSI(14) and Stoch(14,3,3)** read completed five-minute bars of the spot trail. The slow
  stochastic's smoothed %K is the line compared with a level.
- **Gap** is the open against the previous close; **change** is spot against the previous close.
- **IV gates** read the underlying's Cboe vol index (`core.volindex`: VIX for SPX, GVZ for GLD), its close
  before the session, as the signal study's expected move does: `vol_index_rank` and
  `vol_index_percentile` against its `lookback` closes before that (252 by default), `vol_index_minus_rv`
  against the underlying's realized vol over `n` returns. An underlying with no index, or a history
  shorter than the lookback, fails the gate. Core's `iv_rank` conditions rank a chain's live ATM IV
  against a history of the same reading, which no module keeps yet.
- **Reward/risk** is maximum profit over maximum loss at the structure's mid; **bid/ask** is the whole
  structure's width (the sum of the legs' spreads).
- **"Up to $X"** sizes to the largest whole count whose maximum loss at the mid fits, never fewer than one
  (`size.minimum`); a fixed count is taken as stated.
- **A strike "$0.01 above the previous close"** is the listed strike nearest that price. "Or lower" is the
  highest strike at or below. A target that is not listed is no trade, except where the rule says nearest.
- **Match modes.** Every strike rule that aims at a price or an option value says how it meets the listed
  strikes (`match`: exact, nearest, above, below; `strikes` docstring). The rules that predate it keep
  their defaults; a newer rule must state one. The GEX picks and `same_as` place the strike itself.
- **Expected move and standard deviation** come from the at-the-money straddle's mid on the arm's expiry,
  one standard deviation being that straddle over sqrt(2/pi). They need quotes only, no model greeks,
  so they read alike on every source.
- **The entry's level** is the spot at the minute the entry fired (the breakout minute, the trigger's
  firing, a time entry's tick), from the trail; a daily trigger has no such minute and is refused.
- **"Exactly N market days"** is the next N trading days; **~20 days** is the listed expiry within three
  calendar days of the target, the nearest first. **At least N** is the nearest listed expiry N or more
  calendar days out; **between N and M** the nearest in that window (or the farthest, `prefer: last`).
- **Opening-range entry** evaluates once, at the first minute after the range whose spot is above its high
  (`single_shot`), as the bot's tree tags the breakout and does not look again.
- **Touch exits** read the spot against the entry spot: a negative `pct` or `points` is a fall.
- **Event labels** with a calendar rule are computed (`events.py`); the rest are data. A date a label has
  no data for passes its gate and is recorded `unknown`, never read as quiet.
- **Sold legs need a bid.** A structure whose sold leg has none is `unpriced` and skipped, as everywhere
  in the suite (`core.legbars`).

## Money

`pricing.py`, following `docs/conventions/money-layout.md`: gross is mid to mid (or mid to the settlement
intrinsic), fees (`core.fees`), settlement and slippage are separate columns, and net is reported at three
fills: **verdict** (the suite's slippage model, `fees.slippage_dollars`), **stress** (halfway from mid to the
natural fill) and **mid** (context). Per-share prices are signed so a credit is `+`.

## Dependencies

`cherrypick-core` only, its indicators (`core.indicators`) and entry checks (`core.entrychecks`) included. No
broker, no network, no streamer: the loop reads the stream cache and never writes it.

## The paper loop

`python -m cherrypick.automations.paper_loop --once` (the orchestrator ticks it every 60 s while
`modules.automations.enabled`). It takes any arm `arms.load_arms` returns: nothing in the loop is written for
an arm, only for what an arm reads (`feed.reads`).

- **Config** (`config.example.json`, copied to `~/.cherrypick/config/automations.json`): `arms` laid over the
  shipped arms (a new name is a new arm), `symbols` (the OCC root and the stream window per underlying),
  `defaults` (quote and spot age, mark interval, stream lead, trail grace), `events.path`. A misspelt arm
  key stops the loop with its path. A resident loop reads the file at start; the orchestrator's `--once`
  reads it each tick.
- **Snapshot** (`feed.py`, `provider.py`): quote rows, spot, daily bars and the posted close come from the
  stream cache only (read-only), in the shape the backtest's store rows have, and go through
  `snapshot.build`. A quote older than `max_quote_age_seconds`, a crossed one or a chain the cache does not
  hold is a refusal named on the arm, never a guess. Greeks and open interest are read only for an arm that
  places a strike by delta or reads GEX; the VIX only for an arm that reads it.
- **The spot trail.** The cache keeps only the last print, so the loop records one sample a minute per
  symbol (`ensemble_trail`) and builds the opening range and the five-minute bars from that. A trail that
  began more than `trail_grace_minutes` after the open (a loop started at noon) refuses every arm that reads
  it for the day (`trail_incomplete`) rather than feed it a partial morning. The opening-range entry is
  single-shot on the first minute above the range, so a tick that misses that minute does not trade it.
- **Decisions.** `paper.decide_all` over the arms of the day whose entry is due, stacked in arm-name order
  so a later arm sees an earlier arm's entry (`no_position_today`), as the backtest stacks them. Each
  decision is a journal row (`ensemble_decisions`, the engine's first failing check and the value it read)
  and each arm-day a register row (`ensemble_entries`: entered, pending, or missed with the last reason).
  **Nothing fills after the entry window.**
- **The replay record.** Every decision tick writes one line to
  `~/.cherrypick/data/automations/replay/<session>.jsonl`: the compact snapshot, the arms as they stood and the
  decisions. `python run.py replay <file>` runs them again through `paper.replay` and lists where the
  decisions differ, which is how paper is compared with the backtest's path. Event labels are read from the
  current event file on replay.
- **Fills.** Entries and closes at the mid with the suite's slippage model (`core.fees`) as its own cost,
  fees from `core.fees`, per `docs/conventions/money-layout.md`. `net_pnl` is the verdict fill; `net_stress`
  and `net_mid` are kept beside it.
- **Exits** are the arm's, read from the rules the position was entered under (`exit_json`): every exit
  above is checked every tick and closes at the legs' quotes (a close whose
  legs do not price is retried); otherwise the position is held to the expiry's **posted close**
  (`stream_summary`, usually the next morning), never a last trade, and settled at intrinsic. A GLD position
  held to settlement is settled at intrinsic with the engine's cash-settlement charge, as the backtest does;
  a physically delivered ETF leg is not modelled.
- **Stream request** (`stream_request.py`): the arms' symbols with `history_days` for the longest moving
  average, VIX when an arm reads it, `expirations` and a `window_hints` strike window for an arm only from
  `stream_lead_minutes` before its entry window to two minutes after it, and every open position's legs
  through a ledger `leg_source`.
- **Events** (`events.load_calendar`): the file `packages/backtest/scripts/event_history_item75.py` writes.
  Without coverage for a date a data label passes its gate and is recorded `unknown`.

`ensemble_positions` holds the whole money layout on the row: `entry_value` + `exit_value` = `gross_pnl`;
`gross_pnl` - `fees` (open plus close) - `settlement` - `slippage` = `net_pnl`. `exit_reason` is `touch`,
`event`, `stop`, `trailing_stop`, `profit_target`, `delta`, `signal`, `dte`, `time` or `settlement`.

Entries are measured against item 75's replay, as weeklyput's are against item 57's. Measurement-affecting
changes (a window, a gate, an arm's rule, the fill) go to a declared boundary with a `measurement_breaks`
row.

## Tests

`python -m pytest` from this directory. `tests/market.py` invents Black-Scholes quotes and
`tests/cachebuilder.py` writes them into a stream cache built through the real DDL; each gate is shown
firing and failing; a new gate or loop guard is shown to fail by breaking its invariant on purpose
(working-rules).
