**What this covers:** the dated record of what the flies paper experiment has actually measured,
session by session. Append-only — entries are kept even when a later one overturns them, because the
overturning is usually the finding. Part of the [flies module](../README.md) in the cherrypick suite.

For what the strategy *is* and the rules it runs under, see [CLAUDE.md](../CLAUDE.md): the standing
conclusions and the constraints live there, and this file is the evidence behind them.

# Experiment log

Read the sample size before the conclusion. Most entries here rest on tens of positions over a handful
of sessions, and several were chosen on the same rows that measure them — that is stated where it
applies, and it is the difference between a lead and a result.

## The build

Decision engine, floor accounting, paper DB, snapshot provider, session driver, CLI, and the
orchestrator `fly_book` wiring across all four schema registries are complete and tested. 300 tests,
including a provider suite built against the real `cherrypick.core.streamcache` DDL so an upstream
schema change fails here rather than silently producing empty snapshots. The package runs in CI (its
own cell in the `.github/workflows/ci.yml` matrix, `ruff` + `pytest` on every push and PR).

## 2026-07-20 — first live paper session

Eleven structures, 80% completion rate, +$14.89 net — which is the floor and nothing more, since no fly
finished inside its wings. Fees were 82% of gross.

Two things to keep watching, both visible in that one session: completions arrived only after 10–21
points of drift away from the centre (the mechanism that makes completion cheap is the one that walks
spot out of the wings), and `control` vs `time_window` wanted the identical centre on 141 of 141 shared
iterations, so only the disjoint windows separate them. `gex` vs `control` disagreed 84% of the time and
is the comparison with real power.

## 2026-07-24 — five sessions in, the uncompleted branch is the whole result

Rule 4 said completion rate would be the number that decides this, and it now has a threshold to clear.
Settled: 40 legged entries, 23 completed. Every completed fly made money (avg **+$110.47**, min +$51.86
— the floor doing what it promises). The book still lost **−$1,175**, because the 17 misses averaged
**−$208.51** each, and 4 outright flies lost on all four. A miss costs ~1.9× what a completion earns, so
break-even completion rate is **≈65%** against **57.5%** observed.

07-24 settled on the official print (7411.98, confirmed), so its four inside-wings flies stand — but they
carry ~half the positive P&L, and one session driving the result is a concentration caveat, not a
validation.

## 2026-07-27 — three changes out of those five sessions

`min_floor_dollars` 50 → **10** (the old value assumed refusing a completion frees the position slot; it
does not — it leaves the losing short vertical, so turning down a guaranteed +$9.36 to keep a lottery
averaging −$208 was backwards; 5 completions were blocked by that gate alone at floors of $9.36–$39.36),
`entry_modes` → **legged only** (outright lost 4 of 4 and only `gex` was taking them, quietly confounding
gex vs control), and the `wide_wing` arm.

None of this separates the arms — 40 entries over 5 sessions, and the 50%/62%/62% spread is 2 trades
wide. These are mechanism and accounting changes, not signal findings.

## 2026-08-06 — the arms separated, and reading them blended hid a working one

(`analytics.break_even`.) The blended figure said 66.0% completion against a 78.3% break-even — a book
comfortably under water — and that reading shaped three issues before anyone split it. Per arm, on 97
settled legged positions:

| arm | n | completion | break-even | margin | net |
|---|---|---|---|---|---|
| `control` | 56 | **78.6%** | 75.3% | **+3.2** | **+$409** |
| `time_window` | 23 | 52.2% | 72.2% | −20.0 | −$1,154 |
| `gex` | 18 | 44.4% | 91.8% | −47.3 | −$2,228 |

**`control` clears its own bar; `gex` and `time_window` carry the entire −$2,973.** The blended number
was not a summary of the arms, it was an average across a working one and two broken ones, and it
pointed at the *construction* when the evidence points at the *centring and the timing*. That is exactly
what `control` exists for — this is the first time it has paid for itself.

Two things this does **not** license. The samples are small and four sessions long — control's +$409
rests on 56 positions, `gex` on 18 — so this is a separation worth pursuing, not a validated result. And
`gex` is worse on *both* branches, not just completion: its completions average $21.55 against control's
$55.76 and its strandings −$240.06 against −$170.40, so "the centre lags" does not by itself explain it.
On this slice `entry_center_offset_value` does **not** separate gex's own completions from its misses
(medians +1.1 vs +1.9 points), so whatever is driving it is not captured by the dimension built for
exactly that question.

## 2026-08-07 — drift alignment is the sharpest split, and it does not survive the era change

(`analytics.by_drift_alignment`.) A legged entry completes only when spot moves the way
`fly.completing_side_direction` requires. Split by whether that agreed with the session's committed
drift (past ±0.26% of spot), across 97 SPX positions:

| drift vs completing direction | n | completed | rate | net |
|---|---|---|---|---|
| with | 51 | 42 | **82%** | +$1,136 |
| flat | 31 | 21 | 68% | −$980 |
| **against** | **15** | **1** | **7%** | **−$3,129** |

**Fifteen entries lost more than the entire era.** It holds in both trend directions (12 opposing on up
days, 3 on down), and it is concentrated by arm — `control` 4%, `gex` 28%, `time_window` 35%. Strip the
opposing entries and `time_window` flips −$1,154 → **+$210** while `gex` still loses (−$940), which is
most of why those two arms fail.

**The XSP era inverts it**: `against` completed **94%** there (16 of 17). Blended the two read 53%, which
is why this is reported per symbol and never pooled. The likeliest reading is scale, not contradiction —
XSP ran 1-wide wings on a ~750 underlying, so a completion needs a point or two of drift and arrives
almost regardless of direction, while 5-wide on ~7710 needs proportionally far more. If that is right the
signal is entangled with **wing width** and is a statement about the SPX structure rather than about
drift as such.

**Nothing gates on it.** `choose_side` is what generates these — on a trending day spot moves away from
the centre, so it sells the side that then needs a reversal — so a fix belongs there, not in a bolt-on
filter. But the band and the rule were both chosen on the rows that measure them, the down-day cell is
n=3, and the era inversion is unexplained. [centre-lag.md](centre-lag.md) sets the bar at a second
clearly down-trending session; this is a stated prior to read the next one against, not a result.

**And the misses are near misses, not absent markets.** Of the 26 that never saw a qualifying debit, the
median best offer sat **+0.49 points** above the gate (1.16× the credit, worst 1.34×) — nothing like the
1.88–4.00× the bwb roll showed when it was genuinely unreachable. Eight were within 0.25 points. So "the
market never offered it" overstates: the completion was consistently close and the structure did not
quite get there.

## 2026-08-09 — tick cadence 60s to 15s, a measurement break

The orchestrator replaced its per-task Task Scheduler entries with one supervisor daemon, which removed
the 1-minute floor the paper cadence was pinned to. In-session the loop now runs as the module's own
resident `--interval 15` process (supervised: restarted on death and on 120 s of log silence; a shared
PID lock keeps it and any `--once` from ever overlapping), while off-session ticks stay 1-minute `--once`
spawns so settlement at 16:20 keeps its exact shape.

A faster poll catches transient completing-debit dips a slower one missed, so the **completion rate — the
headline number — is not comparable across this date**. The break is recorded as a `mode='cadence'` row
in the decision journal, written by `_note_cadence_change` on the first resident tick at the new cadence.

Live arming re-keyed the same day: the armed signal is now the arm record in the shared state dir, not a
schtasks registration.

## 2026-08-21 — the advisor era: every variant arm retired, one experiment mechanism

The suite-wide cutover: from this session, `packages/advisor` designs and runs every experiment.
Eleven arms retired at once, each with its verdict in the deployed config's `_note` (originals
preserved after a `||` separator). The roster is `control` + `advised:control`.

- **gex** — centring lags spot (offsets −22..+23 on the 08-04/08-05 mirror sessions; it centres on
  where OI is, which is where price was). Superseded: `center_rule` is now an advisable bound.
- **time_window** — finding banked: monotone completion decline 72% → 63% → 58% across the re-cut
  windows; the mechanism (less session left to drift in) is the result.
- **debit-first / bwb** — void, not falsified: the roll-pricing defect voided 25 rows and the
  corrected orientation never accumulated a sample.
- **width-2..5 / width-10** — underpowered on the corrected strike-count basis (3–7 SPX sessions).
  The width question moves to advisor experiments via the `wing_width_strikes` bound — and the
  era's first experiment (`exp-2026-08-20-flies-1`) is exactly that, `wing_width_strikes: 2`
  against control.
- **gex-intrinsic / control-drift** — under 8 sessions; underpowered, no reading.

**Finding worth its own line: `bwb-atm` and `debit-first-atm` never ran at all.** `engine.ARMS`
carries them, this file designed them (2026-08-07) to fix their parents' two-variable confound —
and the deployed config never gained arm entries, so `enabled_arms` (registry ∩ config) excluded
them silently from the day they were written. Check that seam whenever an arm is added.

**The era boundary is journaled** (`measurement_breaks`, 2026-08-21, `advisor_era_cutover`) and the
console's era control carries it (`advisor` era from 08-21; the hand-designed-arms era closed at
08-20). **Asymmetry to know:** this module's own `analytics.py` scopes by date, not era — a
Python-side read that should honour the boundary must date-bound at 2026-08-21 itself.

## 2026-08-21 — GEX concentration tag recut (read-side calibration, no measurement break)

The second degeneracy on the same tag, caught by the same `regime_coverage` guard as the first.
The 2026-08-01 windowing fix made the share vary, but the 0.60 'pinning' cut was a guess that sat
above the p95 of everything the tag then recorded — 605 settled SPX entries over 15 sessions:
median 0.359, p90 0.511, max 0.838 — so 'thin' still swallowed 97% of rows and the dimension could
never accumulate gate evidence.

Cuts are now the recorded distribution's own terciles, rounded (p33=0.291, p67=0.412 → **0.30 /
0.42**), three ways: **diffuse / clustered / pinning**. Kept on the same standard as the
11:00/13:00 time recut — the direction matches the mechanism rather than a boundary flattering
itself: a legged fly completes only when spot drifts off the centre, near-spot gamma concentration
suppresses exactly that drift, and completion falls monotonically **68% → 63% → 55%** across the
three buckets, in both halves of the calibration window (72/63/59 and 64/63/52). The alternative
0.28/0.40 cut separated P&L harder but left 'diffuse' with 7 sessions — the overfit shape, not the
honest one.

A tag, not a gate — nothing entries on it, so no measurement break. Chosen on the rows that measure
it: a current best estimate, to be re-derived again when the advisor era has its own depth.
Historical rows re-bucket at read time via `analytics.by_regime(..., bucket_edges=[0.30, 0.42])`
(159/185/141 legged trades, +76 unknown); rows tagged before this date carry 'thin'/'pinning'
labels from the old binary scheme and the stored float is the truth either way.
