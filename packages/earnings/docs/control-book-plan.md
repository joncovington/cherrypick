# The control book: making entry screening measurable

**Status:** phase 1 landed 2026-08-25. Phases 2-4 planned, nothing built.

## Decisions taken 2026-08-25

- **Phase 1 landed same day**, ahead of that session's 15:35 entry scan, rather than waiting for
  two or three clean sessions first. The confound is accepted and journalled explicitly: this is
  also the first session after an 11-session outage in which a stale Dolt clone left the earnings
  calendar ending 2026-08-14, so a change in entry volume alone does not distinguish "the screen
  widened" from "the data came back". The `measurement_breaks` note says so in as many words.
- **`atm_delta_abs` and `front_expiration_days` stay hard gates. `insufficient_skew_signal` moves
  to the edge category** and comes out with phase 2 — skew is a market opinion, and whether it
  should gate entry is exactly the kind of claim this exercise exists to test.
- **Correlation moves to the read side**: record sector on the row, score any correlation rule
  retroactively. The control book knowingly holds correlated overnight risk. This is acceptable at
  one contract in paper and **must not be inherited by any live path without being reconsidered
  from scratch.**
- **No parallel narrow book.** The wide book is a strict superset, so the old screen's results are
  reconstructible exactly by filtering recorded outcomes. Running both would spend real positions on
  data already derivable.

## The problem

The advisor's twin mechanism cannot express an entry-side change, and that is not a gap to be
patched — it is structural. An `advised:<base>` twin works by opening an identical position beside
its control: same legs, same credit, same quantity, same session. That pairing is the whole source
of its power, because it leaves the parameter under test as the only difference between the books.

An entry screen breaks the pairing outright. If the advisor proposes a looser IV/RV floor, the twin
does not hold the same position as its control — it holds a position the control does not have at
all. There is nothing to pair against, and no amount of care in the twin's construction recovers it.

This is why this package's own contract fences advice bounds to management and exit params, and
leaves entry-side screens, tiering and sizing propose-only, for a human to read and decide. That
fence is correct and should stay. But it leaves the entry screen permanently unmeasured, which
matters more than it sounds: the screen is where nearly all of this module's opinions live.

## What the screen currently costs us

Two numbers frame it.

**619.** The count of candidates rejected by `iv_rv_ratio_below_minimum` as the *sole* blocker —
names that cleared every other gate and died on this one alone. A sole blocker is the only kind a
threshold change can rescue, so this is the single most consequential number in the screen. It rests
on an untested hypothesis, and under the current design it cannot be tested, because the 619 names
that would settle it were never traded and so have no outcome.

**5.** The count of distinct names accepted across the entire recorded history (XYZ, CVNA, FTNT,
BSX, CNC — 30 accept rows, six strategies apiece). At that rate no strategy reaches a 30-trade
sample this year. `strat_test`'s forced sampling was built to fight exactly this starvation, but it
multiplies *strategies per name*, and the binding scarcity is *names*.

A screen that admits five names in six weeks is not producing evidence about which trades are good.
It is producing evidence about itself, and not much of that.

## The approach

Entry screening moves to the **read side**. Trade a deliberately wide control, record the full
metric vector for every name, and evaluate any proposed screen retroactively as a filter over
recorded outcomes.

This is the shape calendars already uses and has already validated: a permissive `path` book that
holds everything and records a per-tick mark path, plus `exit_policies.py` scoring profit targets,
stops and timings over that path — with the replay checked to the cent against the real books on
every run. The same structure, applied to the entry side instead of the exit side.

The consequence worth stating plainly: **a replay is only ever as wide as the book beneath it.** A
screen can be loosened in replay only if the control actually traded the names the looser screen
would admit. Everything below follows from that.

## The partition

Not every gate is the same kind of thing, and the redesign lives or dies on cutting in the right
place. Three categories, and the middle one is the one that gets misfiled.

### Measurement preconditions — stay hard entry gates

`no_listed_options`, `no_weekly_options`, `bid_ask_spread_too_wide`,
`combined_open_interest_below_minimum`, `chain_complete_unverified`, `avg_volume`,
`combined_option_volume`, `price_below_minimum`.

These decide whether a recorded fill is a number or a fiction. A paper fill at the midpoint of a
40%-wide bid-ask on a micro-cap is not a conservative estimate of a real trade — it is a trade that
could never have happened, and it corrupts every downstream statistic in a way no replay can undo.
A sub-$5 name has strike granularity too coarse to build the structure the strategy names.

Keeping these is not screening. It is refusing to fabricate data. They are the reason the control is
"every liquid AMC/BMO name" rather than "every AMC/BMO name".

Note `avg_volume_below_minimum` carries 612 sole rejections — nearly as many as IV/RV. It stays,
and the contrast is the point: two gates of near-identical weight, one doing real work and one
resting on an untested premise.

### Edge opinions — become recorded covariates

`iv_rv_ratio`, `winrate`, `market_cap` (all three already switchable via `symbol_screen`), plus
`term_structure_insufficient`, `expected_move_pct_below_minimum`, `realized_move_too_inconsistent`,
`insufficient_skew_signal` (reclassified here 2026-08-25), and `move_tail_veto`.

Every one is a hypothesis about which trades are *good*. None has been tested. Each is recorded on
`entry_reviews` already, whether or not the symbol traded, so the replay input largely exists.

`move_tail` is the precedent to copy rather than a problem to solve: it already defaults to `"off"`
and is explicitly record-only until the recorded evidence justifies promoting it. That is precisely
the posture every gate in this category should hold.

### Strategy-applicability — case by case, and do not sweep these in

`atm_delta_abs_above_maximum`, `insufficient_skew_signal`, `front_expiration_days_too_far_out`.

These are the trap. They look like edge opinions and are not: they answer "is this structure
*constructible and coherent* for this name", not "is this trade good". A directional credit spread
with no directional signal is not a worse trade — it is an arbitrary one, and recording its outcome
teaches nothing about directional spreads.

**Decided 2026-08-25:** `atm_delta_abs` and `front_expiration_days` stay hard. `insufficient_skew_signal`
was judged edge rather than applicability and moves to phase 2 — skew is a market opinion, and whether
it should gate entry is precisely the sort of claim this exercise exists to test.

### Unverified stays a rejection

`*_unverified` means the data fetch failed, and you cannot record a covariate you do not have. These
keep rejecting — but they are already separated by `screen_metrics` as coverage gaps rather than
screening results, and that separation becomes more load-bearing here, not less. A widened control
whose replay silently treats fetch failures as screening outcomes would be worse than the status quo.

## What already exists

Most of the vehicle is built. This is the part that makes the plan cheap, and it is worth being
precise about, because it also narrows the work considerably.

- **`strat_test_harness` is already the wide-book vehicle.** By its own docstring it "never respects
  `max_concurrent_earnings_positions` or the correlation block list — the test book intentionally
  holds many overlapping positions at once." The concurrency cap of 3 in live config does **not**
  bind it.
- **Sizing is already neutral.** `max_contracts_per_leg: 1` and `max_risk_per_trade_pct: null`, so
  every trade is unit-sized and no risk cap culls the wide tail. Per-unit edge is what gets measured,
  which is what a measurement book wants.
- **Three of the seven edge gates are already config-switchable** to `"off"` through `symbol_screen`,
  via `scanner._soft_gate`. No code change for `iv_rv_ratio`, `winrate`, `market_cap`.
- **`entry_reviews` already records the full metric vector** for every symbol reviewed, traded or
  not, including `iv_rv_ratio`, `winrate`/`winrate_sample`, `term_structure`, `expected_move_pct`,
  `implied_vs_avg_actual`, `iv_rank`, `move_dispersion_pct`.
- **`screen_metrics.classify` already handles the vocabulary problem** that any replay over
  `scan_log` will hit immediately.

So the redesign is mostly *configuration plus a read-side replay*, not a rebuild.

## The changes

### Phase 0 — the entry-window write deadline (landed 2026-08-25)

Not in the original plan; found while landing phase 1, and a prerequisite for it rather than a
nicety.

The harness's write phase is **unbounded by construction** — one live chain fetch per accepted
(symbol, strategy) pair, with no deadline and no cap. That was harmless while the screen admitted
about one name a night and six order builds followed. Widening the screen turns it into potentially
hundreds, and the loop can then run past the 15:55 window into a closed market. A position priced
against a market that is not there is a bad number, and a bad number cannot be un-recorded — it is
strictly worse than a slow scan or a missed entry.

`_past_entry_window` is checked at the call site, immediately before the order build. Two properties
are load-bearing and each has a test that was verified to fail without it:

- **The screen row is still written for every candidate.** The replay wants every verdict whether or
  not the clock allowed the trade; only the *opening* respects the window.
- **The refusal is recorded** as an ordinary execution-stage drop (`entry_window_closed`), so "the
  window closed" can never be misread later as "nothing qualified" — the same reason the
  `execution` stage exists at all.

An absent `entry_window_end` means no enforcement: a guard that refuses every entry when a config
key is missing would be worse than the unbounded loop it replaces.

### Phase 1 — widen the control (config only, landed 2026-08-25)

Set `symbol_screen` to `{avg_volume: "pass", combined_option_volume: "pass", winrate: "off",
iv_rv_ratio: "off", market_cap: "off"}`.

Liquidity preconditions keep their strict bar; the three switchable edge gates go silent. This alone
should move the daily accepted set from ~0-1 names to a meaningful fraction of the 30-90 that
historically cleared the liquid prefilter.

**Land this alone and let it run before anything else.** It is a single, self-contained
measurement-affecting change, and it is the one that produces the sample everything downstream needs.

### Phase 2 — the five hardcoded edge gates

`term_structure_insufficient` (5 strategies), `realized_move_too_inconsistent` (6),
`expected_move_pct_below_minimum` (4), `insufficient_skew_signal` (2), `move_tail_veto` (1, already
off).

These are appended directly to `hard_fail` inside each strategy, so unlike phase 1 they need code.
Extend the `_SOFT_CRITERIA` mechanism to cover them rather than inventing a second switch — one
level vocabulary, one place `screen_metrics` has to understand.

Ordering note: `realized_move_too_inconsistent` is tied to `winrate_lookback_quarters`, which is
documented as *not* a winrate-only knob — it moves the dispersion gate in `atm_calendar` and
`double_calendar` too. Do not touch that value while doing this, or two changes land as one.

### Phase 3 — the entry replay

New read-side module, modelled on calendars' `exit_policies.py`. Takes a proposed screen (a set of
criterion thresholds), applies it to recorded `entry_reviews` rows joined to their `trades`
outcomes, and reports what that screen would have admitted **and what those admitted trades actually
returned**.

The honesty rule this must carry, and the one thing most likely to erode: `screen_report --what-if`
today refuses to report P&L, because its candidates were never traded and a name with no outcome has
no return. Widening the control lifts that restriction *only for names inside the new bar*. Anything
outside it — still-refused micro-caps, anything the liquidity preconditions dropped — remains
counts-and-symbols-only, forever. **The replay must state which side of that line each answer came
from.** A single report that mixes measured returns with counterfactual counts and does not say
which is which is worse than two reports.

### Phase 4 — the advisor's entry-side role

With phases 1-3 landed, the advisor's entry proposals become checkable: it proposes screen
thresholds, and the replay scores them against a control wide enough to contain the counterfactual.

This needs **no new twin machinery and no change to `advice.bounds`.** The twin keeps doing what it
is good at — management and exit params, where both books genuinely can hold the same position, and
where `management.effective_config` already gives exit continuity for free. Entry advice is scored by
replay and applied, if at all, by a human moving a config value. That is a real upgrade over
propose-only, and it does not weaken the fence.

## The measurement break

Phase 1 is measurement-affecting under the suite rule: it changes which trades exist, so results
either side must never be pooled. Journal it with `paper_loop record-break` on the day it lands, and
again for phase 2.

The suite rule says to batch measurement-affecting changes to a declared boundary. Phases 1 and 2
are candidates for landing together on that reading. I would still separate them, and the reason is
specific rather than a general preference for small steps: phase 1 is config-only and instantly
revertible, phase 2 touches six strategies' screening code. Landing them together means a surprising
result cannot be attributed to either, and the recovery is a code revert rather than a config edit.
If they do land together, they are one boundary and one journal entry — not two.

## Open questions

- **Does the correlation block list survive?** It is hand-maintained and cannot cover 30-90 names a
  day. For a measurement book I would stop pretending it does: record sector on the row and handle
  correlation at read time, consistent with everything else moving to the read side. This does mean
  the control book knowingly holds correlated overnight risk — acceptable in paper at one contract,
  and it must not be inherited by any live path without being reconsidered from scratch.
- **Does the live/agent path diverge?** `rank_strategies` still applies the cap and the block list
  and picks one best strategy per name. The control book is a measurement instrument, not a
  proposed live posture, and nothing here argues the live path should widen. Worth stating
  explicitly so a future reader does not infer it.
- **How long before the replay is trustworthy?** A screen replay over 20 trades will find a
  flattering threshold by chance. The promotion gate question from the metrics plan — deferred until
  sessions accumulate — is the same question in a new place, and probably wants the same answer.
