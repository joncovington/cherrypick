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

## 2026-09-19 — the OTM debit-first pair: the trade debit-first never traded

Design record, no measurement yet. Two new paper arms, `debit-first-up` and `debit-first-down`
(`center_rule: delta`, target 0.15 as a magnitude, `debit_direction` the only difference between
them): buy the cheap debit vertical whose centre sits at 15 delta away from spot, complete by selling
the same-centre credit spread once spot has walked into it, and hold a risk-free fly peaked where spot
now is. The uncompleted branch is bounded at the debit paid.

**Why this is new rather than a revival.** The `debit-first` arm that ran 08-03..08-20 was GEX-centred,
and `select_center` had no rule that could place a centre away from spot — so every debit vertical it
bought was the near-ATM spread, a different trade from the one the construction was drawn for. The
08-21 retirement line above ("void, not falsified") was about bwb; debit-first was retired without a
verdict and its ATM twin never ran. Nothing in the ledger measures the OTM trade.

**What was built.** Delta now rides on every leg quote in the snapshot (`provider._attach_deltas`,
filtered at the quote age limit, not the GEX one — 0DTE delta moves with spot); a `delta` centre rule
that refuses rather than degrading (no fresh delta, nothing within 0.05 of target, or a spread that
would not sit wholly beyond spot); and `entry_center_delta` stamped on every arm's rows so the 0.15
can be re-cut later instead of costing a pair per value. The arms override `min_debit_pct_of_width`
to 0.02 — the shared 0.20 floor would refuse the very spread they exist to buy. New books, not a
measurement break: no existing arm changes meaning.

**What it will be read on.** 15–20 sessions against `control`: completion rate, net after fees, the
drift-alignment split, and the direction question — up vs down on the `trend_bucket` already tagged at
entry, a replay over the two books rather than a third arm. Watch `post_best_completing_credit` from
day one: a move *through* the centre richens the completing spread toward `W`, and the first-tick
completion rule will leave most of that on the table; whether a wait-for-better rule earns its place
is [completion-timing.md](completion-timing.md)'s question, and this pair is where it has teeth.

## 2026-09-19 — two overlays on the legged book: the hedge counterfactual and the reversal pairing

Design record, no measurement yet. The proposal was "enter the ATM credit spread and buy a 5-delta put
with it — sell it to cover if it rises, or sell 2 / buy 1 to complete a lower fly", with the stated
aim of cutting drawdown and setting up for a reversal. Neither half needed an arm.

**The hedge, as telemetry.** Every legged entry now stamps the ~5-delta option on its losing side —
strike, delta, modeled buy premium, single-leg fee — without buying it (`hedge_*` columns), keeps the
running max of what selling it would have fetched, and values it at the settlement print. `hedge-overlay`
reprices every settled spread with and without it, split stranded/completed, with a sell-at-Nx replay
over the running max. The prior it tests: the stranded branch's loss sits between K and K−W and a
5-delta long sits far beyond K−W, so at settlement it should only pay in a crash and cost premium
everywhere else. If the overlay shows that, it is a clean negative result and nothing intraday gets
built on it; the replay is an upper bound (best-ever, not a fill) and says so.

**The reversal set-up, as a pairing.** "Sell 2 / buy 1 to complete the lower fly" is `debit-first-down`'s
completion with a single long as the first leg; the vertical version is already running. `reversal-book`
pairs each control entry with the same-losing-side debit-first entry nearest in time in the same session,
and reports the combined settled P&L by which of the two completed. That pairing *is* the two-fly book;
a combined arm would add nothing and confound two variables.

Both are new columns and read-side queries — no existing arm changes meaning, no measurement break.
Rows before this date carry NULL hedge columns and are counted `untracked`, never as a free hedge.

## 2026-09-19 — the bwb question, re-asked on the delta rule

Design record, no measurement yet. Four paper arms: `bwb-up`/`bwb-down` at the default width and
`bwb-up-w2`/`bwb-down-w2` at two strikes, all placed by the `delta` centre rule at the same 0.15 target
the debit-first pair uses. The point is the pairing: at a given centre a bwb is debit-first with the
completion sold at entry for a wider wing, paid for by carrying the tail until the roll buys it back,
so the two constructions now sit on the same strikes in the same session and differ in exactly that.

**Not a rerun.** The 08-21 line above says "void, not falsified", and that is the whole record: the
25 rows before 08-07 rested on a roll priced 3x too wide, the corrected orientation accumulated no
sample before the arm was retired, and `bwb-atm` never reached the roster. So there is nothing to
respect and nothing to pool with. Three things are different this time: placement is by the chain's
own probability rather than GEX or ATM; the far wing's delta is stamped on every row
(`entry_far_wing_delta`), which is the measured tail probability the flat `min_bwb_credit_pct_of_tail`
can be re-derived against; and the width is declared per arm, because the tail scales with it and
the practitioner's 0DTE bwb is a wider structure than this module's default.

**What is expected.** Refusals. The 08-07 correction note already says an OTM bwb's credit collapses
as it is pushed out, and the floor is left at its default on purpose — the refusal count under a
probability-placed centre is the measurement, and loosening the gate to make the arm trade would be
the loosen-until-it-looks-better move rule 6 names. The reading, when rows exist: `best_roll_debit`
(was the tail ever buyable back), unrolled vs rolled P&L, and the same-centre comparison against
`debit-first-up/down` by outcome.

The direction key is `center_direction` from today (`debit_direction` is still read); the arms-seam
test now pins `engine.ARMS` equal to the example config's arm set.

## 2026-09-22 — the opening range, declared as a holdout (no finding yet)

An in-sample pass over 38 joinable sessions split control completion 68.5% (narrow 09:30–10:00
range) vs 82.2% (wide) — the direction the mechanism predicts, since completion IS travel. Then
conditioning on the volatility regime collapsed it: low-vol 78.2% vs 79.7%, gone; high-vol 65.2%
vs 76.9%, surviving on 8–9 sessions a cell, under the floor.

That is a lead, not a result, and it was chosen on the rows that measure it. So the feature
definitions are frozen as of today and every session from here is out-of-sample. The contract —
definitions, outcomes in causal order, the sign expected per module, the decision rule and what
would retire it — is [openingrange.md](openingrange.md). Implementation is
`cherrypick.core.openingrange`; it reports and gates nothing.

Recorded here now so the declaration has a date in the log that the finding can later be read
against. **No conclusion is claimed.**

## 2026-09-24 — settled-book floors recomputed under the corrected expiry fee (a correction, not a break)

Settlement folds the expiry fee its real price charged into each position's `fees`, and
`fly.position_pnl` reused that figure at every price, so a settled book's `worst` priced a
hypothetical settlement with the real one's fees (fixed in 184afc9c). Every settled `fly_books`
floor was recomputed by `scripts/flies_recompute_book_floor.py`; `pnl` did not move, because the
correction cancels at the settlement price, and the script refuses to write if it would.

**Paper:** 248 settled books. Recomputing under the old rule reproduces every stored `worst`, so each
change is this fix alone: 196 `worst` values move (median −$9, range −$240 to +$151), 49 bands move,
and three books flip `floor_holds` from held to broken — 2026-07-30 `time_window` XSP (+$10.67 to
−$18.33), 2026-07-31 `gex` XSP (+$22.32 to −$12.68), 2026-08-04 `control` SPX (+$113.15 to
−$126.85). The large moves are real: a many-position book's worst price leaves dozens of strikes in
the money at $5 each, where the old rule charged only the handful its actual settlement did.
**Live:** 9 books; 7 `worst` values move (−$29.98 to +$5.03), none flips. Six live rows had been
stored before broker fee reconciliation rewrote their positions' fees, so their recompute absorbs
that too. 2026-09-23's stored book `pnl` ($201.04) is stale against its own reconciled positions
($199.04) and was left as recorded.

Consequences for reads of the band classifier (`python run.py bands`) and any `floor_holds` rate:
re-run them; figures taken before today rest on the old floors. Backups sit beside each ledger as
`*.bak-pre-floor-recompute-20260924-*`.

## 2026-09-28 — the stranded edge spreads: five rules replayed, none adopted (a negative result)

Asked after live control's 09-28 book (+$166.11 at 7683.69): five flies and one 7670/7675 call
spread sold at 10:49 near the session low, which never completed and settled −$253.44 as SPX rallied
back into the forest. The question was whether a rule could stop an uncompleted spread at the edge
of the forest ending as a full loss. Nothing was changed. The replay is
`scripts/flies_stranded_replay.py` (read-only; `--arm`, `--all`, `--ledger live`), so every figure
below can be re-run on later sessions rather than re-derived.

**The shape, paper control 08-11..09-28 (33 sessions, 238 legged entries).** 53 stranded: 48 settled
fully through the long wing (avg −$284), 5 partially, **none** out of the money. That is structural —
a spread whose short strike ends out of the money had its completion on the way there — so the
stranded branch is close to a binary −$285 against +$88 per completed fly: break-even completion
74.8% against 77.7% observed. Live since 09-17 is the same shape: 5 of 5 full.

**What was replayed, all on 5-wide control unless named:**

- **A stop (spot or mark).** Every entry is sold at the money, so it starts at its short strike; the
  stranded spreads crossed the long wing a median **6 minutes** after entry, and **85 of the 185
  completed flies went past their long wing before completing**. The two branches are
  indistinguishable for the first half hour, and past the wing the loss is already taken. No stop
  threshold separates them. Nothing further to test here.
- **Completion at a small loss** (limit `credit − fee_buffer` for T minutes, then `credit + x`).
  Every stranded spread's best completing debit came within +1.00 of its credit and 25 within
  +0.25, which looks like a near miss. It is not: the best debit came a median **3.0 minutes after
  entry**, when the completing half of an ATM fly costs about what the first half sold for, by
  construction. Relaxing the limit buys ATM flies for a debit and gives up the drift the legging is
  for. Charged pessimistically (a late completion fills at `credit + x`; a stranding is rescued only
  if its best debit came after T), **0 of 24** (x, T) cells beat the base; the best, x=0 T=90, is
  +$1,072 against +$2,416.
- **The hedge overlay** (`run.py hedge-overlay`, 45 tracked entries since 09-19): stranded
  −$2,514 → +$326, completed +$2,707 → +$90, net +$223, all of it from two paid hedges. Insurance
  priced at its payout.
- **Entry location** (spot's position in the running session range, distance to the running extreme
  in the completing direction): no separation; the sign flips between call and put entries.
- **`miss_stop_minutes`** (`run.py replay-gates`, 08-21 era): 45 min +$3,526 → +$3,847 and a better
  worst day, but losing days 6 → 11 and 21 of 36 strandings still happen.
- **Refusing a second entry on a side with an uncompleted spread open** (the stranded runs are
  same-side: five calls on 09-21, four puts on 08-11). Control 08-11 era: +$2,416 → +$2,641, worst
  day −$836 → −$396, losing days 11 → 13. The 08-21 era alone: **+$3,526 → +$2,100**. Across the
  paper arms it helps sharply on every arm that traded **only** 08-11..08-20 (gex-intrinsic −$182 →
  +$1,241, time_window, floor-must-hold, the width sweep) and hurts or does nothing on every arm
  that traded after 08-21 (no-entry-on-up-trend +$520 → −$84, forecast-range-gate +$736 → −$566,
  callwall flat). Those August arms shared eight sessions, so that is one tape counted six times, not
  six confirmations.

**The other paper arms.** Every 5-wide legged arm (gex, gex-intrinsic, control-drift, time_window,
callwall, and the advised twins) shows control's shape: strandings almost all full, the wing crossed
within minutes (callwall's 30 is the exception — it sells at the call wall, away from spot), the best
completing debit within a few minutes of entry, and 0–1 of 24 salvage cells beating the base
(callwall 4/24). `debit-first-atm` does not escape it: its uncompleted long vertical averages −$258,
the same size, since an ATM debit spread costs about half the width. The delta-placed
`debit-first-up`/`-down` do bound the branch (−$82 / −$70) at a completion cost (62% / 41%), on six
sessions — that is the pair's own read at 15–20 sessions, not this one's.

**The one place the salvage conclusion does not hold: the 10-wide wing.** On
`advised:narrow-wing-vs-control` (wing 10, 11 sessions 08-21..09-04) strandings are not binary (19
full, 9 partial, 5 out of the money), the wing is crossed a median 23 minutes after entry, the best
completing debit comes a median 21 minutes after entry, and **11 of 24** salvage cells beat the base
(best x=0.5 T=15: +$3,019 → +$4,307, 9 rescued). `width-2` (the same 10-wide, four sessions of the
August tape) shows 9 of 24. So "the near miss is an entry artefact" is a statement about the 5-wide
structure, where the whole wing is one strike, not about legged flies. Recorded as a lead in
[backlog.md](backlog.md); not re-run as an arm here.

**No conclusion is drawn from the 10-wide lead, and nothing is gated.** The finding that stands:
on the 5-wide the stranded loss is the price of the completions, not a leak a rule can plug, and
the uncompleted branch is shrunk only by a different construction.

## 2026-09-29 — the 10-wide's own case, checked before re-proposing it (a negative result)

Asked whether the 10-wide wing deserved another run. The salvage lead above is not a reason to run
an arm (backlog), and `exp-2026-08-20-flies-1` failed its primary (smaller worst book) and was killed
on band containment, so the only case left was the one the width sweep was revived for on
2026-07-27: completions arrive after spot has drifted past a 5-point wing, so the book collects its
floor and little else, and a wider wing should catch that drift. That was checked on the rows we
already hold before anything was proposed. Nothing was changed.

Paper `fly_positions`, settled rows, each 10-wide arm paired with `control` on the same sessions.
"Risk" is `(wing_width − credit) × 100 × quantity` summed over entries — the uncompleted branch's
defined loss, a like-for-like measure across widths, not a margin figure.

| Window | Arm | Entries | Completion | Completed inside wings | Avg completed | Avg stranded | Net | Net ÷ risk |
|---|---|---|---|---|---|---|---|---|
| 08-21..09-04 (11) | `advised:narrow-wing-vs-control` | 82 | 60% | 25/49 (51%) | +$258.44 | −$292.25 | +$3,019.27 | +6.4% |
| | `control` | 79 | 78% | 18/62 (29%) | +$105.58 | −$255.94 | +$2,194.99 | +10.5% |
| 08-17..08-20 (4) | `width-2` | 27 | 63% | 5/17 (29%) | +$141.95 | −$547.75 | −$3,064.41 | −19.6% |
| | `control` | 26 | 77% | 2/20 (10%) | +$41.81 | −$311.15 | −$1,030.68 | −14.7% |

**The mechanism holds.** In both windows more completed flies settle inside the wider wing and each
earns more than twice as much.

**It does not survive the completion rate.** A wider completion costs more, so fewer complete, and
each entry carries about twice the risk (credit 4.29 on 10 against 2.35 on 5). The 10-wide's higher
raw net in the advisor window is entirely the larger risk: per dollar at risk it trailed control in
both windows, and on 5 of 15 paired sessions only. Both widths clear their break-even completion by
about the same margin (10-wide 53% needed against 60% observed; control 71% against 78%) — the same
strategy at double the size, not a more efficient one.

**Conclusion: not re-proposed.** The drift argument is answered on its own terms; what remains is the
salvage replay, which is not a reason to run the arm, and the band-containment objection, which is
untouched. Fifteen sessions from one late-summer tape — enough to retire an argument, not to settle
width for good. [backlog.md](backlog.md)'s reopen condition now says what would change this.

## 2026-09-30 — the delta bwb pairs, first seven sessions: refused as expected, but not by how much

The 09-19 entry predicted refusals, and that is what arrived. Paper `fly_entry_attempts`,
2026-09-21..09-29 (7 sessions):

| Arm | Floor | Fills | `bwb_credit_below_floor` | `no_delta_quotes_beyond_spot` |
|---|---|---|---|---|
| `bwb-up` (5/10) | 0.75 | 3 (1.06, 0.82, 0.87 credit) | 6,521 | 236 |
| `bwb-down` (5/10) | 0.75 | 0 | 7,292 | 236 |
| `bwb-up-w2` (10/20) | 1.50 | 0 | 7,268 | 236 |
| `bwb-down-w2` (10/20) | 1.50 | 0 | 7,278 | 236 |

`bwb-atm` filled 43 times over the same days, so the entry path works; the delta pairs are held by
their own floor. The call side clears it now and then at 15Δ. The put side never has; put skew making
the far put dear is the likely reason, but nothing stored shows it. The w2 floor doubles with the
tail while the credit cannot, because the far wing 20 points past a 15Δ 0DTE body is worth close to
nothing.

**The gap.** None of the ~28,000 floor refusals recorded the credit it refused: `plan` is `None` on a
refusal, so `would_be_credit` was NULL, and the ledger keeps no chain quotes to rebuild it. The rows
said *that* the arms failed the floor, not by how much, which is the number a retune against
`P(tail) × tail` needs. The `proposed_legs` column meant to hold the offered structure had never been
written by any mode (NULL on all 325,476 rows).

From today every bwb attempt row, refused or filled, carries `proposed_legs` (each leg's strike,
sign, quantity, bid, ask and delta) and, once priced, `would_be_credit` (the legged ceiling's
2026-08-11 fix). That is enough to settle a refused bwb against the session's print, so a read-side
replay can give each arm's P&L at any floor, or none, and re-price it at mid to separate "the market
never paid it" from "the slippage model ate it". The body's delta shows where in the 0.10–0.20
tolerance each attempt landed. The tick's shared `gate_detail` is now cleared before each entry mode,
so one mode's legs or credit cannot land on another's row. Telemetry only: no gate moved, not a
break. Build the replay after a few sessions; read it before touching the floor.
