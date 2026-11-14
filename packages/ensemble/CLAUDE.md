# cherrypick-ensemble

⚠️ **EXPERIMENTAL.** A rules engine for short-dated option arms, declared as research item 75 (eight SPX
and GLD arms from a third-party portfolio, read over all history in one pass). **Paper-only,
credential-free, no live path** (posture: root file). The package is the engine and its paper loop: a
position is a ledger row (`~/.cherrypick/data/ensemble/paper_trades.db`), never an order. Off by default.

## What it is

An **arm** is configuration (`arms.py`): the underlying, the expiry rule, the legs and how each strike is
placed, the entry trigger, the weekdays, the event skips, the filter gates, the exits and the size. A new
arm is a new entry (or a machine override read by `arms.load_arms`); an unknown key is refused with its
path. A new kind of filter is one function in `gates.REGISTRY`, a new strike rule one branch in
`strikes.pick`.

`engine.decide(arm, snapshot)` is the one decision: pure and deterministic, it returns the entry (legs,
quantity, prices at mid, natural and working, the slippage for each fill kind, the opening fee) or the
first failing check, with every check it read. `manage` says whether an open position should close now,
`close_position` and `settle_position` return the trade. The backtest driver and the paper loop differ
only in how they **build** the `Snapshot` (`snapshot.build`, store rows against stream-cache rows of the same
shape), so `tests/test_paths_agree.py` holds that a recorded snapshot replays to the same decision.

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
- **Reward/risk** is maximum profit over maximum loss at the structure's mid; **bid/ask** is the whole
  structure's width (the sum of the legs' spreads).
- **"Up to $X"** sizes to the largest whole count whose maximum loss at the mid fits, never fewer than one
  (`size.minimum`); a fixed count is taken as stated.
- **A strike "$0.01 above the previous close"** is the listed strike nearest that price. "Or lower" is the
  highest strike at or below. A target that is not listed is no trade, except where the rule says nearest.
- **"Exactly N market days"** is the next N trading days; **~20 days** is the listed expiry within three
  calendar days of the target, the nearest first.
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

`cherrypick-core` and the technicals package's pure indicator functions (`sma`, `ema`, `rsi`) only. No
broker, no network, no streamer: the loop reads the stream cache and never writes it.

## The paper loop

`python -m cherrypick.ensemble.paper_loop --once` (the orchestrator ticks it every 60 s while
`modules.ensemble.enabled`). It takes any arm `arms.load_arms` returns: nothing in the loop is written for
an arm, only for what an arm reads (`feed.reads`).

- **Config** (`config.example.json`, copied to `~/.cherrypick/config/ensemble.json`): `arms` laid over the
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
  `~/.cherrypick/data/ensemble/replay/<session>.jsonl`: the compact snapshot, the arms as they stood and the
  decisions. `python run.py replay <file>` runs them again through `paper.replay` and lists where the
  decisions differ, which is how paper is compared with the backtest's path. Event labels are read from the
  current event file on replay.
- **Fills.** Entries and closes at the mid with the suite's slippage model (`core.fees`) as its own cost,
  fees from `core.fees`, per `docs/conventions/money-layout.md`. `net_pnl` is the verdict fill; `net_stress`
  and `net_mid` are kept beside it.
- **Exits** are the arm's, read from the rules the position was entered under (`exit_json`): a spot touch, a
  profit target and minutes before expiry are checked every tick and close at the legs' quotes (a close whose
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
`profit_target`, `time` or `settlement`.

Entries are measured against item 75's replay, as weeklyput's are against item 57's. Measurement-affecting
changes (a window, a gate, an arm's rule, the fill) go to a declared boundary with a `measurement_breaks`
row.

## Tests

`python -m pytest` from this directory. `tests/market.py` invents Black-Scholes quotes and
`tests/cachebuilder.py` writes them into a stream cache built through the real DDL; each gate is shown
firing and failing; a new gate or loop guard is shown to fail by breaking its invariant on purpose
(working-rules).
