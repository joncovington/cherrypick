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

## 2026-10-02 — live completions fill where paper's model says no; fill realism recorded from today

Of the 48 live completions 2026-07-30..10-02, **43 filled below the best modelled completing debit
the live loop saw while the spread was open**, by a median 0.14 points (about $14 a position, range
0.025 worse to 0.49 better). Paper's gate (`debit < credit − fee_buffer`, on mid plus 0.125 of each
leg's spread) would have refused those completions on the same quotes. Two readings, not yet
separable: the live loop samples once a minute and the resting limit catches dips between samples,
or the modelled haircut overcharges a resting order. Either way it questions the live-trading
plan's assumption that paper's completion rate is a ceiling on live's.

At the moment the loop noticed each fill, spot sat a median 6.2 points past the centre in the
completing direction (−0.3 to 13.1; about 1.2 wing widths at 5-wide SPX), and median latency was
22.7 minutes.

From today the live ledger records every order and what it saw while it worked, and each paper
legged entry carries a live-like completion shadow ([fill-model.md](fill-model.md)). The ledger's
live `debit`/`credit` turned out to be each order's limit, not its fill, so any price improvement
was invisible until now. Telemetry only: no gate moved, not a break. Read the shadow and the rule fit
after a few dozen quoted completion orders; any switch of paper's completion rule lands at a
declared boundary.

**The backfill, same day** (`fill_facts backfill --write`; broker fill times from the transactions,
spot from the gex trail; 2026-08-01..10-02, SPX and XSP):

| | Orders | Filled | Spot past centre at fill (p25 / p50 / p75) | Past the completing long strike (p50) | Better than limit |
|---|---|---|---|---|---|
| Entries | 66 | 55 (11 cancelled) | −2.3 / −1.6 / −0.8 pts | — | none above 0.10 |
| Completions | 55 | 42 (13 cut off at 15:30) | 3.6 / 5.1 / 6.0 pts (0.71 / 1.03 / 1.20 widths) | +0.14 pts | none above 0.05 |

- **Completions fill when spot reaches the completing long strike.** The median sits 0.14 points
  past it (0.21 of the entry's straddle past the centre). Measured at the moment the loop
  noticed instead, the same fills read 6.2–6.4 points: notice lag, not market.
- **There is no price improvement to speak of.** Every fill was within 0.05 of its limit, so the
  ledger's limit-as-price was a fair record. The gap to paper is when the market reaches a limit,
  not what it fills at.
- **Entries fill on submission**, at the price asked; the 11 that did not were cancelled and
  re-priced by the loop.
- **A spot-distance rule** ("fill at the first time spot is X widths past the centre", over each
  order's trail path) agrees with live on 84% of the 55 completion orders at 0.25–0.75 widths, and
  fires a median 2.5–6.5 minutes early. At 1.0 width it agrees on only 65%: 18 fills arrived before
  spot got there, because the limit depends on the credit as well as on spot. Spot alone is a
  coarse rule. The price rules need quoted paths, which start today.

## 2026-10-02 — live vs paper completions, read from the backfill; paper pays the limit from 10-05 (a break)

Read-only over the backfilled live ledger (SPX, 2026-08-03..10-02) and paper on the same days, in
the arm live traded each day.

| | Live | Paper (same days, same arm) |
|---|---|---|
| Completion rate | 76% (42 of 55) | 79% (81 of 103) |
| Completion rate, 19 matched entries (same side and centre, within 10 min) | 89% | 84% |
| Spot past the centre at completion (median) | 5.1 pts (1.03 widths) | 7.1 pts |
| Credit minus completion price, per share (median) | 0.25, every time | 0.29 |
| Minutes to complete (median) | 19 | 31 |

- **The live limit is credit − 0.25 on every order**, set by the `min_floor_dollars` bound, and the
  broker fills at exactly it: no improvement above 0.05, median 0.
- **Paper completes later, further out, and about $4 a completion better than live ever got.** Its
  trigger waits until mid plus the haircut is under the limit, and by its tick the market has usually
  run past. On the 16 matched pairs where both completed, paper needed about 2.7 points more spot
  travel. The rates are level, so paper is neither a ceiling nor a floor on completion: it is late
  and generous.
- **Spot distance is not the mechanism.** 18 of 42 fills came before spot reached the completing long
  strike: decay and volatility bring the debit down too. When spot did reach it, the order filled 24
  times out of 25, a median 68 seconds later. The exception is 2026-09-30, the 7715 put: 1.49 widths
  at 10:54 and no fill.
- **The misses are the P&L.** 42 completions made +$3,350 and 13 misses lost −$3,442, about $265
  each. In 8 of the misses spot never got 0.65 widths past the centre, and in 3 it went straight the
  wrong way.
- **Entries fill on submission at the limit**, conceding 0.05 to the fresh mid. Paper charges about
  0.04 there: close enough to leave alone.

**Declared: from 2026-10-05 every paper legged completion pays the live limit** (`engine.pays_limit`)
on the unchanged trigger. It is a book-wide `completion_rule` break, journaled by the paper loop.
Never pool completion P&L across it; the regime-cut era restarts there. The trigger is the next
step, fitted from quoted live paths against the shadow, and it will be a second break.

## 2026-10-02 — entry credit as a gate: neither a floor nor a band (a negative result)

Asked whether `control` should enter only on a larger credit. Replayed by dropping the refused
entries and keeping each remaining spread's recorded P&L, the `replay_gates` method (structures
settle independently). Paper `control`: 345 settled 5-wide SPX spreads, 43 sessions,
2026-08-03..10-02, no floor net +$2,932. The deployed `min_credit_pct_of_width` of 0.20 (1.00 on a
5-wide) refuses nothing at the money.

| Rule | Spreads kept | Net | Per spread | Sessions better / worse |
|---|---|---|---|---|
| no floor (deployed) | 345 | +$2,932 | +$8.50 | — |
| credit ≥ 2.30 | 183 | +$3,686 | +$20.14 | 16 / 27 |
| credit ≥ 2.40 | 145 | +$2,530 | +$17.45 | 17 / 26 |
| credit ≥ 2.60 | 58 | +$2,508 | +$43.25 | 16 / 27 |
| credit 2.40–2.59 only | 87 | +$22 | +$0.25 | 15 / 28 |

- **A floor raises the per-spread figure and loses more sessions than it wins at every level.**
- **What a high credit is.** It is the short strike sitting in the money at entry (r +0.41) and
  low skew (r −0.43). 57 of the 58 entries at 2.60 or more were call spreads.
- **The ≥ 2.60 edge is five sessions.** Those five give $2,298 of its $2,508.
- **Other arms do not reproduce it.** Credits of 2.60 or more lost in `gex` (−$66 a spread),
  `advised:miss-stop-90` (−$78) and `advised:no-entry-on-up-trend` (−$14).
- **The completion rate is 78% in every credit bucket.** A larger credit only softens a miss
  (−$164 against about −$255).
- **The band looked like live's best bucket, and paper does not reproduce it.** Live's 2.40–2.59
  entries completed 88% (22 of 25, +$58 a spread) against 67% and −$51 for the other 27 control-era
  entries. That gap holds inside the control era alone, so it is not the earlier gex period. But it
  sits in eight sessions, the completion difference is Fisher p 0.11, and paper `control` on the same
  dates (09-17..10-02, 101 spreads) shows nothing: +$13.00 a spread in the band against +$12.51
  outside it. Live credits also run about 0.12 above paper's for the same structure (the fresh
  re-price), so a live credit bucket is not the paper bucket of the same name.

No gate, no arm. The credit is a proxy for where the entry sits and which side it takes. If anything
here is worth testing, it is the side split: calls +$17 a spread, puts +$1. That is a two-arm
question held out over future sessions, and in a single up-trending tape it is more likely the tape
than a rule.

## 2026-10-02 — what moves the completion rate; the `vol-floor` arm declared (no result yet)

The question was what would raise `control`'s completion rate. Read over paper `control`
(345 settled 5-wide SPX spreads, 43 sessions, 2026-08-03..10-02). Completed spreads averaged +$80
and misses −$242, so break-even is about 75% completion. Control runs 77.7%, and each point is worth
about $3.20 a spread.

- **Misses are wrong-direction days, not short ones.** Using the gex spot trail, spot travelled a full
  wing width past the centre in at least one direction before 15:30 on 343 of 345 spreads. The worst
  8 sessions hold 44% of the misses; 9 sessions had none.
- **What does not move completion:**
  - where spot sat in the strike interval at entry (77–80% across it);
  - the entry credit (78% in every bucket);
  - trend from the open, with or against (75% against 77%);
  - flipping `choose_side`. A spot-travel proxy, calibrated on the side actually sold (93% agreement
    with paper's own completions), gives the other side 77.4% against 76.8%;
  - keeping live's completion order past 15:30. None of the 13 live misses saw spot travel a width
    after the cutoff;
  - wider wings (width-2 63%, down to width-10 12%).
- **Stacking on a completed fly is a negative result.** The replay allowed a new legged entry on the
  centre of a fly already completed (the duplicate rule refuses these today; the legs share the fly's
  strikes and signs).
  - 136 stacks completed only 67% (by the proxy), and lost about $13 each net of the shared
    settlement fee. They come late: spot has to have completed one fly and come back.
  - Unlimited stacking: 811 entries, −$3,433, worst day −$3,720 against −$836.
  - Even completing a stack at net 0.15 (risk-free at two contracts) only breaks even.
- **What separates completion:**
  - volatility: the low-vol third of sessions 70% and −$11 a spread, the rest 80–82% and +$17;
  - time of entry: 10:00–11:00 83–84%, 11:00–13:00 68–70%, after 14:00 50%;
  - a flat open: within 5 points of the open, before 11:00, 91% on 34 spreads.

  Each rule raised completion 3–6 points in both halves of the era, but only the volatility floor
  was ahead on P&L in both halves. Midday skip is already under test as an advised arm.

**Declared: the `vol-floor` arm, from 2026-10-05.**
- **The arm:** `control` plus no entry while the ATM straddle is under 0.0022 of spot (about 17
  points at 7,700). The ratio was matched to the 17-point cut's 21% refusal rate, not re-optimised.
- **In-sample:** it kept 277 of 345 entries, raised completion from 78% to 81%, and was ahead in
  both halves (+$762, +$1,308). That is not significant: 12 sessions better, 9 worse.
- **How it will be read:** against `control` on the same sessions after at least 14 sessions
  (`MIN_EFFECTIVE_N`), on per-session net (sign test) and on completion rate. It earns promotion
  only if it is ahead on both and not on the strength of one or two sessions.
- **Its book starts at its own `arm_added` break,** and it shares the 10-05 completion-price break
  with every legged arm.

## 2026-10-02 — release days: no gate, no morning restriction; every entry now tagged with its day's releases

The question was whether scheduled releases (PCE, JOLTS, CPI and the rest) should gate entries. It
was asked after the 10-02 jobs report, when paper `control` completed 6 of 10 and live 2 of 5. Read
over paper `control` (345 settled 5-wide SPX spreads, 43 sessions, 08-03..10-02). The release dates
come from BEA's file, FRED's history and the curated FOMC list.

| Session | Sessions | Completion | Per spread | Losing days |
|---|---|---|---|---|
| No major release | 29 | 76% | +$4.93 | 12 / 29 |
| Major release at 8:30 | 11 | 75% | +$1.52 | 4 / 11 |
| JOLTS at 10:00 | 3 | 94% | +$47.79 | 0 / 3 |
| **NFP** | **3** | **61%** | **−$41.65** | **3 / 3** |

- **Release days as a whole complete like quiet ones.** CPI, PCE, GDP, PPI, JOLTS and FOMC days were
  all positive. A whole-day gate on releases would have cost money.
- **Restricting the morning is a negative result.** Release days' 10:00–10:30 entries completed 82%,
  the same as quiet days: an 8:30 release has been absorbed by 10:00. Every morning rule on release
  days lost money: start 10:30 (−$269, 5 sessions better and 6 worse), start 11:00 (−$823, 4/7),
  skip 10:20–11:30 (−$783, 2/9). The weak stretch on release days is 11:00–12:30 (50–62%), not the
  morning.
- **Only NFP stands apart, and three sessions do not establish it.** All three NFP days lost, but
  their misses fell at different times. On 08-07 and 09-04 they came at 10:00–10:15; on 10-02 at
  10:27–11:20, after the early entries had completed. No time window holds across all three. Every
  rule that wins on them amounts to trading less on NFP days, and three sessions chosen after the
  fact give p 0.25 at best. A lead, not a finding.

**Declared: the `event` regime tag, from 2026-10-02.** Every entry and completion records the
day's releases (`event_bucket`, `event_value` minutes since the latest major release,
`event_labels`). Earlier rows are backfilled from the calendar store, and the regime cuts carry the
dimension. Tag only, so not a break. Read NFP again once there are several more of them.

## 2026-10-02 — first read of the event tags: release days complete more, quiet low-vol days least

Read after the calendar was corrected (Census for its own releases, ISM and the rest by rule) and
every row re-tagged (`backfill-events --restamp`). Paper `control`, 345 settled 5-wide SPX
spreads, 43 sessions, 08-03..10-02.

| Entry was | Sessions | Completion | Per spread | Losing days |
|---|---|---|---|---|
| on a day with no major release | 26 | 75% | +$2.1 | 10 / 26 |
| after that day's major release | 17 | 81% | +$16.5 | 6 / 17 |

**It is not volatility in disguise.** The median entry straddle is the same on both (21.7 vs 21.5
points), and release days do as well or better in every implied-volatility tercile:

| Straddle | No release | After a release |
|---|---|---|
| Low (< 19.5 pts) | 67%, −$6.5 (18 sessions) | 76%, +$5.1 (11 sessions) |
| Middle | 80%, −$1.2 | 80%, +$14.8 |
| High (≥ 23.1 pts) | 81%, +$19.8 | 84%, +$24.6 |

The likely mechanism is that a release delivers realised movement the straddle did not price.

- **The weakest cell is a quiet, low-volatility day** (67% against a ~75% break-even). That is what
  `vol-floor` is really aimed at, but the arm also refuses low-volatility release days, which
  completed 76%. The arm stays as declared, with one variable. Its results are re-cut by
  `event_bucket` at its 14-session read, which tests "low vol and no release" without a second arm.
- **NFP is the one release with a consistent negative:** 3 sessions, 61%, −$41.7 a spread, every
  day losing. Live's one NFP day completed 40%. Retail sales is negative on 2 sessions (69%, −$39.4).
  Every other release is positive on 1–3 sessions each: JOLTS 94%, ISM Manufacturing 88%, PCE and
  GDP 100%.
- **Timing around a release is not the problem.** Entries within 15 minutes of a 10:00 release
  (ISM, JOLTS, Conference Board) completed 91%, against 80% in the same window on quiet days.
  Entries 4+ hours after a release did badly (50%, −$116, 5 sessions), but late entries are weak on
  every kind of day.
- **Across books:** the release-day edge holds in `control` and the advised arms built on it (not
  independent: same days, same base). It is flat in live (77% vs 76%) and reversed in the `gex`
  arms. NFP is lower in most books that traded one.

Caveats: about 20 release labels were compared over 43 sessions, each release seen 1–3 times, so one
or two extreme cells are expected by chance. Only the release-vs-quiet split and the quiet low-vol
cell rest on enough sessions to act on, and neither yet earns a gate.

**Re-read:**
- NFP after about six more reports (next 2026-11-06).
- `vol-floor` at 14 sessions, cut by `event_bucket`.
- Retail sales at five or more sessions.

## 2026-10-03 — `miss_stop_minutes` replayed on live under the cap-only rule: not paying (a negative result, early)

The backlog's first step for turning the gate on live. `run.py --db <live ledger> replay-gates
--start 2026-09-25`, live `control`, SPX, 09-25..10-02: 6 sessions, 37 settled entries. Nothing was
changed.

| Rule | Kept | Net | Losing days | Worst day | Paper `control`, same dates |
|---|---|---|---|---|---|
| no gate (deployed) | 37 | −$368.06 | 3 / 6 | −$784.10 | +$1,296.79 (54 entries) |
| 15 min | 22 | −$604.17 | 5 / 6 | −$267.82 | +$1,138.34 |
| 30 min | 25 | −$569.81 | 5 / 6 | −$265.33 | +$859.85 |
| 45 min | 23 | −$907.11 | 5 / 6 | −$265.33 | +$1,274.40 |
| 60 min | 26 | −$763.32 | 4 / 6 | −$545.66 | +$853.10 |
| 90 min | 28 | −$978.64 | 4 / 6 | −$784.10 | +$686.25 |

- **Entries made after a stranded first leg are where live made its money.** 22 of the 37 were
  entered while a spread that went on to strand was still open. They completed 91% (20 of 22) and
  made +$1,127. The 10 entered with nothing open completed 70% (−$711). The 5 entered with only
  later-completing spreads open completed 40% (−$784). Paper on the same dates is less extreme (16 such
  entries, 75%, +$287), but it has the same sign.
- **The pattern the gate is built for happened once.** On 10-02, two spreads entered while a miss was
  open (11:02, 11:25) also missed. Gating at 15–45 minutes cuts that day from −$784 to −$265, but
  gives back more on 09-28, 09-29 and 10-01. Those days' biggest completions came after a stranding
  (+$385, +$424, +$371).
- **Caveats.** Six sessions. Dropping an entry frees room under the $1,000 margin cap that the replay
  cannot spend, so a gated live loop would have entered trades the replay never sees. The 09-29 break
  (live start 10:30 → 10:15) falls inside the window.

The backlog's reopen condition is "15 sessions under the cap-only rule, and the replay shows the
gate paying". The second half is not met so far. Re-run at 15 sessions.

## 2026-10-03 — the refused delta bwbs, first replay: the market refuses them, and a lower floor splits by side with the tape

The replay the 09-30 entry asked for, over the first three sessions with `proposed_legs`
(09-30..10-02, SPX). `scripts/flies_bwb_floor_replay.py` (read-only; `--arm`, `--since`, `--per-day`)
re-prices each attempt from its stored quotes, re-runs the gates after the floor (ceiling, tail cap,
fees, then `portfolio_gates` against its own entries), and settles each entry at the `fly_books`
print, **unrolled**. It validates itself on every run, and an arm that fails gets no floor table
and the run exits 1:
- Re-priced credits match `would_be_credit` to 0.0001 on every priced row.
- At the deployed floor, the replay enters exactly the real fills made before each session's first
  real roll, after which the real book holds flies the replay cannot have. That is 1 of 1 for
  `bwb-up` and `bwb-down`, and 4 of 4 for `bwb-atm` (14 vs 16 after the first roll).
- The settlement path reproduces all 16 real unrolled `bwb-atm` fills since 09-21 to the cent.

**The w2 pair has no real fill, so its gating is unvalidated**; its tables are marked so. Each
check was broken on purpose (wrong slippage, portfolio gates skipped, fees dropped from settlement),
and each failed the run.

**Mostly the market, not the slippage model.** Mid and modelled credits differ by only 0.04–0.10.
The best credit each session offered (modelled, then mid):

| Arm | Floor | 09-30 | 10-01 | 10-02 |
|---|---|---|---|---|
| `bwb-up` (5/10) | 0.75 | 0.58 / 0.62 | 1.16 / 1.25 | 0.60 / 0.65 |
| `bwb-down` (5/10) | 0.75 | 0.53 / 0.57 | 0.86 / 0.95 | 0.59 / 0.65 |
| `bwb-up-w2` (10/20) | 1.50 | −0.05 / 0.00 | 0.52 / 0.62 | 0.25 / 0.35 |
| `bwb-down-w2` (10/20) | 1.50 | 0.28 / 0.32 | 1.03 / 1.10 | 0.53 / 0.58 |

At the deployed floor, pricing at mid adds no entries. The w2 pair never reached its floor:
`bwb-up-w2` peaked at 0.62 against 1.50, and `bwb-down-w2` came closest at 1.10 (mid) on 10-01.

**What a lower floor would have made, unrolled and held to the print.** With no floor, only the fee
gate applies (about 0.16).

| Arm | Entries | Net | Tail hits | 09-30 | 10-01 | 10-02 |
|---|---|---|---|---|---|---|
| `bwb-up` | 9 | +$534.90 | 0 | +$44.34 | +$423.72 | +$66.84 |
| `bwb-down` | 12 | −$745.76 | 2 | −$990.02 | +$134.31 | +$109.95 |
| `bwb-up-w2` | 4 | +$862.46 | 0 | | | |
| `bwb-down-w2` | 9 | −$993.10 | 2 | | | |

This is the tape, not the floor. On 09-30, SPX fell from 7,710.77 at 10:00 to a 7,651.54
settlement. The other two sessions ended within about 10 points of their 10:00 level. So the one
large move was down, into the side where a 15-delta put body sits. The P&L is not
monotonic in the floor (`bwb-up` at 0.10 of tail: −$258; at 0.075: +$590), because one early entry
blocks later ones through the cadence, duplicate and sign rules.

**The roll moves the answer both ways.** On the arms' own real fills: `bwb-up` +$29.67 as traded
against +$109.36 never rolled, `bwb-down` +$16.55 against +$79.36, and `bwb-atm` (20 fills, 17
rolled) −$143.64 against −$239.98. The replay cannot roll, because the roll needs later quotes the
ledger does not keep. Its figures therefore overstate both a winner and a loser.

No conclusion and no floor change: three sessions, one down day. [backlog.md](backlog.md) holds the
re-read condition.

## 2026-10-04 — four first reads before anything trades: the selector walked forward, two hedge shapes, freeing the live cap, debit-first by distance

Design record and first reads. No arm, gate or parameter changed; everything below is read-side
replays plus two telemetry streams that start filling on 2026-10-05. Design:
[selector.md](selector.md).

**The selector, walked forward (`run.py selector-replay`).** It refits per session from everything
before that session, scoped to its era by the ledger's breaks, and decides the session out of
sample. Version 1 reads vol × trend buckets only.
- **Take/skip on control, exact.** Over 08-21..10-02 (29 sessions) it kept 183 of 218 entries for
  **+$5,976.74 against control's +$4,436.50**. Losing days went from 7 to 6, and the worst day from
  −$836.05 to −$549.43.
- **What it skipped:** all 35 skips fell in one cell, normal-vol and up-from-open. That cell
  reached five sessions on 09-03 already negative and never recovered. The skipped entries had
  netted −$1,540.24.
- **Robustness:** the session-level difference is positive on 6 sessions. Its 90% interval,
  [−$444, +$3,823], **includes zero**. It is the same finding `advised:no-entry-on-up-trend` is
  testing forward, here reached without hindsight.
- **Against the fixed gates:** up-from-open skip +$6,076.31, miss-stop 45 +$5,313.48, vol-floor's
  gate on control's rows +$5,162.17. All three were chosen in hindsight over the same rows, so the
  selector matching them out of sample is the result. Beating them is not established.
- **Two structures** (control vs `debit-first-atm`, their 10 shared sessions; an upper bound): the
  selector +$1,500.89, control +$1,129.81, `debit-first-atm` +$261.40, the oracle +$5,983.96. It
  chooses only among filled trades, so at this size it checks the machinery and nothing more.

**The run-triggered book hedge (`run.py hedge-overlay --run-k 2,3`).** It buys one ~5-delta hedge
per (session, side) when the k-th open, uncompleted same-side spread opens, priced from that
entry's own stamp. The window is control 09-21..10-02, 10 sessions with stamped hedges, unhedged
+$1,129.81 (the session-kept book, which includes 3 unstamped rows).
- **k=2:** +$1,154.96, and the worst session improved from −$836.05 to −$683.53. But losing
  sessions went from 2 to 4, the gain rests on one payout (09-21's 7750 call, 14.7 points in the
  money), and the interval includes zero.
- **k=3:** +$417.94, 0 positive sessions, interval [−$1,087, −$359]. **It reliably loses:** by the
  third same-side entry the run has already happened.
- This is a smoke test. The per-position hedge stays a no (backlog: "Sell-to-cover").

**Hold the hedge only while stranded.** From 10-05 each legged completion stamps
`hedge_mid_at_completion`, the sale value of the overlay's hedge on the completion tick.
`hedge-overlay`'s `sell_at_completion` block reads it. Today every completed row is `untracked`
there, by design, and there is no backfill.

**Freeing the live cap (`scripts/flies_cap_swap_replay.py`).** At each `max_open_margin_reached`
refusal run it values two options for the stalest uncompleted live spread, force-completing it (C1)
or aborting it (C2), plus paper control's entry on that tick as the freed slot.
- **Coverage:** 58 runs over 3 sessions; only 3 have a paper twin. Paper control's own gates had
  stopped it entering on those afternoons.
- **C1 is unmeasurable so far.** Every pre-10-02 `fly_order_path` row is a trail backfill with no
  bid/ask. Valuing C1 at the resting limit gives an upper bound of +$216.60 (sign flips when one
  session is dropped).
- **C2 (abort):** −$287.40, as the 07-30 pre-close exit found.
- Neither is supported. Real C1 readings come only from refusals recorded with quotes.

**Debit-first by distance (`run.py debit-first-offsets --start 2026-09-21`).** 200 entries over 10
sessions: gross **−$34.77**, fees $1,979.25, net −$2,014.02. As a family, debit-first loses its
money to fees.
- **By strikes out:** 0 (ATM) +$261.40 over 90; 3 out −$624.08, 0% completed over 7; 4 out −$509;
  5 out +$307; 6 out −$805; 7 out −$321. One and two strikes out have **one row** between them.
- **By delta:** 0.35–0.45 +$747.19 at 83% completion; at or above 0.45 −$644.68; 0.10–0.20
  −$1,849.31.
- These are descriptive. The two cuts share a vol confound (a 15-delta strike sits further out on
  high-vol days), which is why both are shown.

**The shadow ladder** fills the 1–6 strike gap from 10-05 if `debit_ladder` is set on
`debit-first-atm`. At every fill it prices the same trade k strikes out each way, carries each rung
by the arm's own completion function, and settles it at the print. A rung is never a position.
`run.py debit-ladder` reads it, with a calibration against real delta-arm fills at the same centre.
The first read is at 10 sessions.

## 2026-10-04 — declared: the `selector` arm, from 2026-10-19

- **The arm:** `selector`, with sources `control`, `vol-floor` and `debit-first-atm`. Procedure
  version 1 (vol × trend buckets), `min_sessions` 5, `margin` 0, defaulting to `control`.
  [selector.md](selector.md) holds the contract.
- **Why 10-19 and not 10-05.** The 10-05 `completion_rule` break is book-wide, so from 10-05 the
  selector's model learns only from rows on or after that date. Starting earlier would buy only
  sessions as control's twin. By 10-19 the model is fitted on about ten new-era sessions, and the
  nightly `selector-fit` job can be built and merged first. Any other roster change planned for the
  fortnight lands at this same break.
- **Its book starts at its own `arm_added` break on 10-19.** Changing the procedure (features,
  shrinkage, decision rule) is a new break. Refitting nightly is not.
- **How it will be read:**
  - **Only departing sessions count:** sessions where it booked something other than exactly what
    `control` booked. Twin sessions carry no information, the rule curve's `noflip` keeps.
  - **The first read** comes after **10 departing sessions**, against `control` on the same
    sessions, on **per-session net alone** (sign test).
  - **Ahead:** the arm is kept and re-read at 20 departing sessions. **Behind:** it is retired and
    written up as a negative result.
  - Each source's own book, the best fixed gate over the same sessions and the robustness stamps
    are reported beside the verdict but do not decide it.
- **Stated now so it is not discovered later:** a sign test over 10 sessions has little power. A
  "keep" at the first read means "not yet refuted", never "proven", and it says nothing about live.
