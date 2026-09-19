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

## Two open spreads at once in the live pilot

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
