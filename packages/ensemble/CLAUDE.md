# cherrypick-ensemble

⚠️ **EXPERIMENTAL.** A rules engine for short-dated option arms, declared as research item 75 (eight SPX
and GLD arms from a third-party portfolio, read over all history in one pass). **Paper-only,
credential-free, no live path** (posture: root file). There is no paper loop, ledger or console page yet:
this package is the engine, its tests and the scaffold the loop is added to.

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
broker, no network, no streamer: the stream cache is read by the paper loop, which does not exist yet.

## What the paper loop needs (not built)

Stream requests for the SPX and GLD chains at the entry minutes plus the one-minute spot trail; a
`Snapshot` built from them with `snapshot.build`; the event data from `scripts/fetch_event_history.py`'s
output; a ledger of entries and trades; an orchestrator job; a console page. Entries are measured against
item 75's replay, as weeklyput's are against item 57's.

## Tests

`python -m pytest` from this directory. `tests/market.py` invents Black-Scholes quotes; each gate is shown
firing and failing; a new gate is shown to fail by breaking its invariant on purpose (working-rules).
