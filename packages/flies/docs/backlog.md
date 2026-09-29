# Backlog — deferred on purpose, each with the condition that reopens it

Not a to-do list. Each item here was considered and set down with a reason, and the reason names
what would have to be true before it is picked up. Findings go in [experiment-log.md](experiment-log.md);
rules go in [../CLAUDE.md](../CLAUDE.md); this file holds the things that are neither yet.

## Measure `book.py`'s four entry-construction blocks before folding them

The legged, debit_first, bwb_roll and outright entry blocks in `book.process_snapshot` share a
~12-key row shape (`position_id`, `book_id`, `trade_date`, `arm`, `experiment_id`, `symbol`,
`entry_time`, `entry_window`, `center_reason`, `underlying_at_entry`, the regime and leg columns)
and differ in the fields that are the trade — `credit` vs `debit`, `far_width` on one,
`completing_direction` on two of four, a different floor rationale on each. Each block also
builds the in-memory `pos` dict the same tick's entry gates read, so a shared builder has to get
every mode's live fields right, not just the row.

- **Do:** compare the four with the mode name normalized out, per the root file's dedup rule, and
  read the call sites back individually. Expected result: the shared part is the boilerplate, and
  a small `_entry_row_base(...)` for those keys alone is the most that is safe — roughly 40 lines,
  no behaviour change. Expected non-result: the per-mode fields stay copied, the
  `_wing_width_multiple` posture, because they shape an order.
- **When:** next time someone is in that file for another reason. Not on its own.
- **Deferred 2026-09-19** because it is pre-existing code, larger than the session that noticed
  it, and downstream of live-order construction.

## Two open spreads at once in the live pilot -- DONE 2026-09-25 (cap only, no count limit)

Landed in a0a820d4: `live.max_incomplete_spreads` defaults to no limit, so the `$1,000` cap is the
only sizing gate (1 restores the old rule). The replay was run first (control, 08-21..09-24): the
one-incomplete rule netted +$2,130 (max drawdown -$883, worst session -$298), the cap alone +$3,119
(-$1,256, -$818). `miss_stop_minutes` was NOT turned on live with it. The original deferral note
follows for the record.

`live_loop._is_blocking` admits one incomplete spread; the `$1,000` margin cap already admits two.
Mechanically small. Deferred because a second entry ten minutes after the first sells the same side
into the same tape (`choose_side`), so it is the same bet twice — the era's losing sessions were
runs of misses — and because control went live on 2026-09-17 and the plan's Step 1 bar is ≥15
sessions under one rule set.

- **First:** replay control's paper entries under a two-open rule, the `replay_gates.py` pattern,
  against the 09-17 one-open replay (~55% of P&L retained). Deterministic, costs nothing.
- **Then, only if it pays:** land it at a declared boundary after the live clock has run, together
  with `miss_stop_minutes` turned on live — the gate built for a second entry into a tape that
  already stranded the first.

## `debit-first-atm` and `bwb-atm` -- deferral reversed 2026-09-19, both on the roster from 2026-09-21

Designed 2026-08-07 as the single-variable controls (legging order for debit-first, construction for
bwb), lost to the arms-seam bug (experiment-log 08-21), and on merge of PR #136 deferred here as
"only worth its book if the delta-placed pair shows something the ATM comparison would help
attribute". Reversed the same day, for one reason: arms are read as paired comparisons on the SAME
sessions, so a twin started after the pair shows something cannot be paired with the sessions that
showed it. Without the twin, debit-first-up/down differ from control in two things at once (legging
order AND centre placement), which this module's own one-variable rule forbids reading; with it,
control vs debit-first-atm isolates legging order and debit-first-atm vs the pair isolates the 15-delta
placement. Same for bwb-atm against bwb-up/down. Both journaled as arm_added breaks at 2026-09-21, the
same boundary as the six delta arms, so the roster change is one break. Do not read either twin as a
result on its own.

## Variants of the delta-placed debit-first pair, by config only

- A second target (`debit_delta_target: 0.05`) is a second pair of arms, no code. Hold until the
  0.15 pair has a handful of sessions; completion at 5 delta needs roughly twice the move.
- A naked-long first leg ("buy the 5-delta put, sell 2 / buy 1 to complete") is a new entry mode,
  not config. Same end state as `debit-first-down`'s completion; the vertical first leg pays less
  premium for the same completed structure. Build only if the vertical version reads well.

## Sell-to-cover on the hedge overlay needs a path, not a running max

`hedge_best_mid` supports the threshold family (sell at N× premium) as an upper bound. A trailing
rule — the shape [completion-timing.md](completion-timing.md) argues is optimal under favourable
drift — needs a per-tick mark path this ledger does not keep. Record one only if `hedge-overlay`
first shows the hedge recovering more than it costs on some branch; if it never does, the path
would be measuring a rule for a position not worth holding.

## Four atomic-JSON writers, measured and left

`core.advice`, `core.streamrequests`, `advisor/store.write_json` and `review/facts.write` each
carry the same three-line write-then-rename. Measured on 2026-09-19 when the regime-cuts artifact
needed a fifth: the bodies differ only in tmp naming and whether they mkdir. The fifth is the
first shared one (`core.home.write_json_atomic`, used by `core.regimecuts`); folding the four onto
it is a change across four packages with four test files and was not worth carrying in the same
landing. Reopens when any of the four is next touched.

## Regime cuts: the sweep and the partial sessions

v1 carried one declared cross-tab (gex x trend); a second, gex x drift_alignment, was declared on
2026-09-21 when it earned its place the way the first did -- the first era-wide drift_alignment
read contradicted a stated prior (control's `with` and `against` both near 68% over 20 sessions
against 82% / 7%), and gex is the one dimension that can explain that either way. **A general
N x N sweep stays deferred**, and so does the three-way cross it would start with: 27 cells at
this sample is the fishing surface, and `trend` already sits inside `drift_alignment`. A third
declared pair needs its own reason of the same kind. `partial_session` breaks are listed as caveats inside
the era rather than excluded from it; excluding those dates would touch the shared
`_period_clause`, and none falls inside the current era. Curve is the natural third writer (one
dimension, `entry_regime`/`entry_ratio` already on the row) once its fee floor lets it enter.

## Staged salvage completion on a wider wing

`scripts/flies_stranded_replay.py` replays a completion limit that relaxes from `credit − fee_buffer`
to `credit + x` after T minutes. On the 5-wide it is dead by construction (experiment-log
2026-09-28: the best completing debit comes ~3 minutes after entry, so relaxing buys ATM flies for a
debit). On the 10-wide `advised:narrow-wing-vs-control` it beat the base in 11 of 24 cells, on 11
sessions, now retired, with pessimistic accounting in both directions.

- **Reopens when** a 10-wide (or wider) legged arm is back on the roster for its own reasons and has
  15+ sessions of its own — the salvage is then a replay over its rows, not a reason to run the arm.
- **The obvious "own reason" is already spent** (experiment-log 2026-09-29): the drift argument —
  a wider wing catches more of the post-completion drift — holds on the rows (51% of completed flies
  inside the wings against control's 29%) and still loses per dollar at risk (+6.4% against +10.5%;
  5 of 15 paired sessions), because completion falls 78% → 60%. Re-running the arm on that argument
  alone repeats a checked negative. A new case has to say why width would help risk-adjusted P&L
  where it did not — a different tape, or a completion rule that holds the 10-wide's completion rate
  up — and be scored on net ÷ risk, never raw net, since the 10-wide carries about twice the risk.
- **Then:** re-run the script on that arm, and replay the best cell under the live one-incomplete
  and margin-cap rules before any live question; the replay's charge on late completions is a
  bound, so a positive result is a floor on the benefit, and a negative one is not.
- **Never on the 5-wide.** Rule 6's limits apply: this completes at a known negative floor, which is
  a choice between two held positions, never a licence for an entry.

## An expected-move debit-spread pair as a hedge on the base book

Proposed 2026-09-29: just before the window, buy a call and a put debit spread at the expected move,
both as a breakout hedge and as first legs to build flies from. The companion idea -- selling an iron
condor there instead -- was dropped: it loses on the same breakout days the book does, its 5-wide
wings fail `min_credit_pct_of_width`, and it is MEIC's trade in a flies book. No arm is needed for the
debit version: `debit-first-up`/`-down` buy those spreads at 0.15 delta, and
`scripts/flies_em_pair_replay.py` scores each session's first pair against the base book, as traded
and held to settlement.

- **The two goals conflict.** Completion caps the spread at a fly's payoff, so on the day spot runs
  through it the hedge is gone: 2026-09-21, control −$836, the call spread completed at +$2 where
  held it was worth +$329. A proposal has to pick one.
- **First read, 7 sessions (09-21..09-29):** control +$800; pair traded −$165, held −$66. Held cut
  the worst day −$836 → −$507 and added a losing day. One control losing day in the window, so
  nothing is readable yet.
- **Reopens when** the base book has five or more losing days in the window (the script says when it
  has fewer) -- about when the debit-first pair reaches its own 15–20 session read. Then: if `held`
  pays on losing days more than it costs on the others, the held version is the proposal, as
  telemetry on the hedge overlay's pattern before any arm; if `traded` does, the debit-first pair's
  own verdict already answers it.
