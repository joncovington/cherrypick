**What this covers:** the selector arm: what it is, the contract it keeps (written so MEIC can adopt
the same shape), what the out-of-sample replay said before it ever traded, and what was considered
and set aside. Part of the [flies module](../README.md). Findings go to
[experiment-log.md](experiment-log.md); this file is the design record.

# The selector

Every other flies arm is its own portfolio testing one variable. The selector is one portfolio
that, on each tick, asks its declared source arms what they would do against **its** book, scores
each proposal from a model fitted on the sources' own settled rows, and books at most one. The
variable under test is the choosing procedure itself.

It is **off** until its declared boundary, **2026-10-19** (the judging rule is in the experiment log,
2026-10-04). `selector` is registered in `engine.ARMS` and the code paths exist, but no config
enables it. Enabling it changes which arms exist, so it lands at a declared boundary, journaled once,
with its judging rule written in the experiment log beforehand.

## The contract (module-agnostic)

Each rule below is stated so another module can keep it without borrowing flies' specifics.

1. **Candidates are generated against the selector's own book.** Each source's own entry function
   runs with the source's own merged params, gated by the selector's open and day positions. The
   source arm's book plays no part. Cadence, the sign rule and the duplicate check therefore describe
   the portfolio the selector actually holds, and a retired arm's params can still be a source.
2. **Identical proposals merge, never double-count.** The key is mode, side and geometry. Geometry
   alone (`engine.structure_key`) deliberately ignores kind and side, which is right for the
   duplicate rule and wrong here: a legged and a debit-first entry at one centre are different
   trades at different prices.
   - A merged candidate is scored strictest proposer first. The declared `sources` order runs least
     to most strict within a structure.
   - The proposers' rows are never pooled. `vol-floor`'s trades are a subset of control's tape.
3. **The model is frozen per session.** It is fitted the night before (`run.py selector-fit
   --write`), pinned at the session's first tick in `selector_active.json`, and replayed thereafter.
   "No model" is pinned too, so a model landing at 11:00 cannot switch the arm on mid-session.
4. **With no model, the selector is the default's twin.** It books the default source's plan
   whenever that source offers one, and nothing else. A test runs control and the selector over the
   same ticks and requires identical books through settlement.
5. **The procedure is fixed; only its parameters move.** Refitting nightly is not a measurement
   break. Changing the features, the shrinkage or the decision rule is, and bumps
   `PROCEDURE_VERSION`.
6. **Fitting reads only rows strictly before the session, each source scoped to its era.** Era
   scoping comes from the ledger's own `measurement_breaks` (`core.regimecuts.era_bounds`). The fit
   is truncation-invariant, and a test adds future rows and requires an identical model.
7. **Completion follows the position.** A booked position completes under the params of the source
   that proposed it (`selected_from`), never the selector's own. A test gives the source a different
   fee buffer and requires the selector's position to behave like the source's.

## The decision rule (procedure version 2, from 2026-10-12)

**Features:** `vol_bucket` × `trend_bucket` only. The regime cuts showed one paired contrast in 46
under p 0.10, against about 4.6 by chance, so more features would fit noise.

**Scoring.** A candidate's evidence is the first proposer, strictest first, with a cell of at least
`min_sessions` (5) sessions. None qualifying leaves it ineligible. A source's all-regime average is
never evidence: it would let an arm that is good on average displace the default in a regime
nothing is known about.

**Each cell carries its session-level interval:** the 90% seeded-bootstrap interval on its total
net (`core.regimecuts.robustness`, the regime cuts' own stamp), None below three sessions.

**The choice:**
- **Depart from the default** only for a candidate whose own cell interval is wholly above zero and
  whose net per entry beats the default's by `margin` (`model_preferred`).
- **Skip** only when the default's own cell interval is wholly below zero (`default_cell_negative`).
- **Otherwise take the default.** The reason is `thin_default` when it has no cell,
  `default_negative_unclear` when its cell is negative but not clear of zero, `default` otherwise.

**Why version 1 was replaced before it traded.** Version 1 skipped on any negative cell mean and
could depart on a source's overall average (`shrunk`). The 2026-10-13 model, fitted on the new era
(from 10-05), had one qualifying cell, normal-vol × flat: five sessions, −$8.51 an entry, interval
[−$1,916, +$1,353]. Version 1 would have skipped every entry in it, half the book. Version 2 keeps
control there. Over the original window (08-21..10-02) version 2 still skips the up-from-open cell
from 09-03: 32 entries dropped, +$5,734.52 against control's +$4,436.50 (version 1: 35, +$5,976.74).
It keeps the signal and drops the noise.

Every tick writes a `fly_selector_choices` row: each source's refusal or scored plan, the choice,
and the reason (with the model's absence reason appended when there was none).

## Before it traded: the walk-forward replay (2026-10-04)

`run.py selector-replay` refits per session from everything before it and decides that session out
of sample.

**Take/skip on control (exact),** 29 sessions from 08-21:

| | Entries kept | Net | Losing days | Worst day |
|---|---|---|---|---|
| control | 218 | +$4,436.50 | 7 | −$836.05 |
| selector | 183 | +$5,976.74 | 6 | −$549.43 |

- **What it skipped:** 35 entries, every one in the normal-vol, up-from-open cell. That cell
  reached five sessions on 09-03 already negative and stayed negative at every later fold. The
  skipped entries had netted −$1,540.24.
- **Robustness:** the session-level difference is positive on 6 sessions. Its 90% interval,
  [−$444, +$3,823], **includes zero**.
- **Against the fixed gates:** up-from-open skip +$6,076.31, `miss_stop` 45 min +$5,313.48,
  vol-floor's gate +$5,162.17. Each of those was chosen with hindsight over the same rows, so the
  walk-forward selector matching them out of sample is the result. Beating them is not established.

**Two structures (an upper bound; a smoke test),** control and `debit-first-atm` over their 10
shared sessions: the selector +$1,500.89 over 76 entries, control +$1,129.81, `debit-first-atm`
+$261.40, the hindsight oracle +$5,983.96. It chooses only among trades that were actually filled
and ignores cross-construction interactions. At 10 sessions it is a check of the machinery.

## Considered and set aside

- **An AI inside the paper loop.** It was set aside for four reasons:
  - It cannot be honestly backtested, because a model may already know how a historical session
    ended, which leaves a forward-only sample.
  - It is not reproducible.
  - A slow or failed call would put the tick at risk.
  - It would contradict the suite's rule that AI lives outside the packages.

  If it is revisited, the contained shape is a `scripts/` sidecar writing short-lived intents that
  the loop reads, with skip as the default. It would choose among engine-generated candidates only,
  and be benchmarked against a random chooser with the same take rate.
- **1-minute candle features.** The suite's intraday history (the gex spot trail, from 2026-07-16)
  is too short; [range-features.md](../../../docs/range-features.md) declined that question for the
  same reason, and its daily version closed with no finding. An opening-range feature joins
  procedure version 2 only if the [openingrange.md](openingrange.md) holdout passes its checkpoint.
- **Re-enabling retired legged arms** (width-2, gex) as sources. Their evidence is thin and from
  before the cutover. Revisit if selection is shown to pay at all.
- **Closing or force-completing a position to free buying power.** That is never the selector's
  power. The question is replayed offline (`scripts/flies_cap_swap_replay.py`) and would be its own
  live rule if it ever paid.

## Porting it

MEIC is the one other module where the shape fits:
- it ticks intraday;
- its profiles differ by entry rule;
- `entry_attempts` records each profile's proposal before its gates;
- it already writes regime cuts.

The selector there would be a synthetic profile over those candidates. When MEIC adopts it, the
model-file contract, `fit`, `arm_starts` and `validate_model` move to `cherrypick.core.selector`.
That follows core's own bar: code goes there only once two packages would otherwise disagree. Until
then it stays here.

In bwb, calendars and pmcc every arm takes the same entry and they differ only in exits, so a
selector there would choose a management policy. That is a different design. Curve decides once a
day, which leaves a frozen model very little to learn from.
