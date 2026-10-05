# Backlog — deferred on purpose, each with the condition that reopens it

Not a to-do list. Each item here was considered and set down with a reason, and the reason names
what would have to be true before it is picked up. Findings go in [experiment-log.md](experiment-log.md);
rules go in [../CLAUDE.md](../CLAUDE.md); this file holds the things that are neither yet.

## `miss_stop_minutes` on the live pilot

The one half of the two-open change that did not land. The deferral that became item "Two open
spreads" (Done, below) said to turn it on live *with* the second spread -- it is the gate built for a
second entry into a tape that already stranded the first -- but 69f74f3a landed the cap-only rule
alone, and nothing tracked the gate after that.

- **First:** replay live's entries since 2026-09-25 under `miss_stop_minutes` (the `replay_gates.py`
  pattern): how often a second entry followed a stranded first, and what the gate would have kept.
  Deterministic, costs nothing.
- **Reopens when** the live pilot has 15 sessions under the cap-only rule (the plan's Step 1 bar), and
  the replay shows the gate paying. Then it lands at a declared live boundary, never mid-clock.
- **Measured 2026-10-03** (live control, 09-25..10-02, 6 sessions, 37 entries; experiment-log): not
  paying. Every threshold from 15 to 90 minutes nets below no gate (−$368 → −$570 to −$979), and
  paper on the same dates agrees. Entries made while a spread that went on to strand was still open
  completed 91% (20 of 22) and made +$1,127. Re-run at 15 sessions (about 10-15).

## Variants of the delta-placed debit-first pair, by config only

- A second target (`debit_delta_target: 0.05`) is a second pair of arms, no code. Hold until the
  0.15 pair's own 15-20 session read (mid-to-late October from its 2026-09-21 start; 7 sessions at
  2026-09-29, up 22/37 completed +$48, down 17/41 -$1,276), then add it as an `arm_added` boundary.
  Completion at 5 delta needs roughly twice the move.
- A naked-long first leg ("buy the 5-delta put, sell 2 / buy 1 to complete") is a new entry mode,
  not config. Same end state as `debit-first-down`'s completion; the vertical first leg pays less
  premium for the same completed structure. Build only if the vertical version reads well.

## Sell-to-cover on the hedge overlay needs a path, not a running max

`hedge_best_mid` supports the threshold family (sell at N× premium) as an upper bound. A trailing
rule — the shape [completion-timing.md](completion-timing.md) argues is optimal under favourable
drift — needs a per-tick mark path this ledger does not keep. Record one only if `hedge-overlay`
shows the hedge recovering more than it costs **net of the completed branch's cost** -- a trade only
learns which branch it is on at settlement, so a branch that pays alone is not a hedge that pays. If it
never does, the path would be measuring a rule for a position not worth holding.

- **Measured 2026-09-29** (control, SPX, from 09-19; 62 tracked entries): the stranded branch cost $822
  and recovered $3,430 on two payouts; the completed branch cost $3,794 and recovered nothing; the book
  was +$774 unhedged and -$413 hedged. The old wording ("on some branch") had fired on the stranded
  branch alone. Not met net; the best sell-at-N-times cell (3.0x, +$756) is an upper bound and still
  short of unhedged.

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

## Re-deriving the delta bwb pairs' credit floor

`scripts/flies_bwb_floor_replay.py` prices every recorded bwb attempt from its own `proposed_legs`,
re-runs the arm's gates at any floor (or none), and settles each entry at the print, unrolled. The
first read (experiment-log 2026-10-03, three sessions) says the market refuses the delta pairs, not
the slippage model, and that a lower floor's P&L split by side with the tape.

- **Reopens when** there are 15 or more sessions with `proposed_legs`, including several down days
  (09-30 is the only one so far). Then: is the unrolled P&L at a lower floor positive on both sides,
  and on net ÷ tail rather than raw net? A floor change is measurement-affecting and lands at a
  declared boundary.
- **The replay is unrolled.** The roll needs later quotes at two strikes that the ledger does not
  keep, so every replayed entry carries its whole tail. On the real fills the roll moved results both
  ways (bwb-up +$109 unrolled → +$30 rolled; bwb-atm −$240 → −$144), so the replay overstates both
  tails. A positive result is not a rolled-arm result.

## The selector arm: landing it on 2026-10-19

Built and off (2026-10-04, [selector.md](selector.md)). **Declared for 2026-10-19**, with its
judging rule in the experiment log (2026-10-04): the first read comes at 10 departing sessions, on
per-session net against `control`. Before that date:

- **The nightly fit job.** The orchestrator's scheduler knows only `regime_cuts_at` /
  `regime_cuts_argv`, so `selector-fit --write` (after 16:40) needs its own scheduled entry there,
  built and tested first.
- **The `arm_added` break note** for `selector` on 10-19, following `_note_vol_floor_arm`.
- **On the date:** merge to main, add the `selector` arm (`entry_modes: []`, its `selector` block)
  to the machine config, and journal the break once, together with any other roster change that
  fortnight.
- **Not live-eligible** until debit-first has a live order path (`live_orders.py` builds legged
  specs only). A legged-only selector is the live candidate if one is ever wanted.
- **Extract to `cherrypick.core.selector`** (model file, `fit`, `arm_starts`, `validate_model`) when
  MEIC adopts it, and not before.

## The shadow ladder and the hedge stamp: first reads

Both are telemetry from 2026-10-05.
- **The ladder** stamps only on an arm whose config sets `debit_ladder: {"offsets_strikes": [1, 2, 3,
  4, 5, 6]}`, which is `debit-first-atm` by design. First read `run.py debit-ladder` at 10 sessions;
  call it descriptive until 15–20. Read the calibration first: same-strike pairs with real delta-arm
  fills must agree on debit and outcome, or the ladder is wrong and nothing else in it is read.
- **`sell_at_completion`** in `hedge-overlay` (hold the hedge only while stranded): reopens the
  "Sell-to-cover" question if, at 15 sessions of stamped completions, it recovers more than the
  hedge costs **net of the completed branch**.

## Freeing live buying power at the cap

`scripts/flies_cap_swap_replay.py` (2026-10-04) could value only 3 of 58 refusal runs, and C1
(force-complete at a small debit) on none: the order path had no quotes before 10-02.

- **Reopens when** 15 live sessions of cap refusals have quoted `fly_order_path` rows. Re-run it. C1
  becomes a proposal only if it pays net on the sessions it acted on **and** the freed slot's value
  explains why it beats the general relaxed-completion result (0 of 24 cells). Even then it is a
  live completion-gate change at a declared break. C2 (abort) is not a candidate.

## Run-triggered book hedge

`hedge-overlay --run-k` (2026-10-04): k=3 reliably loses; k=2 +$25 on one payout with more losing
sessions. **Reopens when** the base book has ten or more losing sessions with stamped hedges. Re-run
k=2. Nothing intraday is built on it before then.

## Done -- kept for the record

Finished or reversed items stay here, so a later reader can see what was decided and on what evidence.

### `book.py`'s four entry-construction blocks -- DONE 2026-09-29 (shared keys only)

The legged, debit_first, bwb_roll and outright entry rows now build the keys they share verbatim
(identity, the plan's shared geometry and cost, the clock, the regime tags, `status`) through one
`entry_row_base(mode, kind, position_id, plan)` closure in `process_snapshot`, beside `journal`
and `record_attempt`. Everything that is the trade stays in each block: `net`, `credit`/`debit`,
`far_width`, the centre deltas, `completing_direction`, the leg symbols, the floor and its
rationale, the hedge columns. The in-memory `pos` dicts the entry gates read were left as they were.
Parity: the row dicts all four paths build, over eleven fixture cases, were captured before and
compared after -- equal, and a deliberate over-fold (`entry_center_delta` into the shared keys)
failed on the outright rows.

### Two open spreads at once in the live pilot -- DONE 2026-09-25 (cap only, no count limit)

Landed in 69f74f3a: `live.max_incomplete_spreads` defaults to no limit, so the `$1,000` cap is the
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

### `debit-first-atm` and `bwb-atm` -- deferral reversed 2026-09-19, both on the roster from 2026-09-21

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

### Four atomic-JSON writers -- DONE 2026-09-29

`core.advice`, `core.streamrequests`, `advisor/store.write_json` and `review/facts.write` now write
through `cherrypick.core.jsonio.write_json_atomic` -- the helper that was `core.regimecuts`'s (not
`core.home`'s, as this note used to say); `regimecuts` re-exports it. The copies differed in
formatting, and those differences are the helper's two knobs: stream requests stay compact
(`indent=None`), and advice and stream requests keep refusing a non-JSON value (`default=None`)
rather than writing its `str()`. File bytes are identical before and after for every writer. The
one visible change is advice's tmp name (`flies-<date>.tmp` became `flies-<date>.json.tmp`); every
reader of that directory matches `.json` or an exact name, so neither form was ever read.
