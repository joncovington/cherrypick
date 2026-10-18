# Replaying rules against recorded quotes — flies and MEIC

*Drafted 2026-10-12. **The recording landed the same day** (gex PRs #172–#174): the gex recorder keeps
SPX's 0DTE chain every tick (~15 s), and `cherrypick.core.quotehistory` reads it back. **First
recorded session: 2026-10-13.** Nothing below that line is built yet. Amended the same evening: the
storage made compact and held strikes added before the first session, and six gaps in this plan
named (*Before Phase 1*).*

## Why

Until now no rule a module never ran could be priced honestly after the fact. The stream cache keeps
only the latest quote per leg; the ledgers record a market only for positions a module actually held.
Three questions on 2026-10-12 hit that wall:

- **Flies' live credit floor.** The estimate had to borrow paper's fills for the trades live never
  placed, and its gain rested on two trend days.
- **Restack on live.** The replay of one live session could only *estimate* the restack's credit,
  and could only infer from the spot path whether it would have completed.
- **MEIC's profit side and stop fills.** Its max favourable excursion is `None`, because "the cache
  keeps no quote history", and a derived stop is priced at its threshold rather than where a real
  stop would have filled. (This bullet first said the stop curve was censored. It is not: see
  *Corrections* below.)

## The data

| | |
|---|---|
| Tables | `gex_quote_samples` (one row per symbol and tick) + `gex_quotes` (one per side and strike of a sample) in `~/.cherrypick/data/gex/gex_history.db`; read only through `core.quotehistory` |
| Written by | `service.record_quotes`, every gex recorder tick in RTH |
| Content | 20 strikes each side of spot (~±100 SPX points), both rights: bid, ask, mid, quote age, delta; plus every strike of the chain a module declares it holds (`held_strikes`) |
| Quotes kept | only those `core.streamcache.usable_quote` accepts at 120 s; a missing row is a missing quote |
| Spot | not stored again; `core.quotehistory.session` joins `gex_spot_history` at or before each sample, within 60 s |
| Switch | the gex config's `quote_history` block, **off by default**; on for SPX on the trading machine |
| Size | ~80 quotes a tick, ~8 MB a session (7.9 MB measured on a synthetic 1,560-tick session; the first layout was 15.3 MB), ~2 GB a year |

What it covers: every flies structure (ATM centres; 15-delta debit-first and bwb wings fit inside
±100), and every MEIC SPX condor since 09-01 (13,462 trades: all legs within 85 points of spot at
entry; the threatened side stays inside the window as spot moves). A leg that drifts past ±100 on a
big trend day is still recorded while a module declares it held (flies' and MEIC's open legs are,
through their `leg_sources`). What it can miss: a strike past the window nobody holds, and touches
shorter than one tick.

**What it does not cover.** SPX's nearest expiration only. bwb (SPX ~7 DTE), calendars and pmcc
(multi-week), and MEIC on XSP, NDX or RUT get **nothing**: the recorder's `quote_history.symbols` is
SPX, and even on SPX it keeps only the chain the GEX profile reads (the nearest expiration with live
greeks). A replay of any of those needs the recorder extended first, which is a size decision (each
symbol is another ~8 MB a session), not a code one.

## Phase 0 — check the recording (2026-10-13)

About four samples a minute, gaps around 15 s, ~80 rows each, spot joined on most samples, no
recorder warnings. A one-off check is scheduled for 10:07 ET.

## Before Phase 1 — gaps found in review (2026-10-12)

A review the evening before the first session found the plan below under-specified in six places.
The first is already fixed. Each of the others is a precondition of Phase 1, not a refinement of it.

**1. Flies stamped the wall clock — fixed (#178).** `book.process_snapshot` wrote `_now()` beside a
decision made on the snapshot's `now_min`, so a replayed entry carried the hour the replay ran, its
latency measured the replay's runtime, and the cadence clock paced a past session against today.
`flies.clock.pinned(when)` now sets the instant a replayed tick stands for, and `process_snapshot` and
`settle_book` take an optional `now`. Phase 1's first validation check is still that a replayed
entry's time is its sample's.

**2. What the snapshot needs that the recording lacks, and the rule for each.** Flies' paper
snapshot (`provider.build_snapshot`) carries more than quotes. Each field is either rebuilt from a
recorded table by a declared rule, or the replay refuses the tick; it never approximates silently.
- *Per-strike IV, gamma and OI* are not in the quote history. `gex_profile_history` has them per
  side, every ~5 minutes. Rule: the profile row at or before the sample, used only within 360 s (one
  5-minute cadence plus a minute's slack); older is a refusal (`profile_stale`), as flies' own
  `max_gex_input_age_seconds` refuses a stale live surface.
- *The GEX surface and walls* are rebuilt from that same profile row through `core.gex`, the one
  engine, under the same staleness rule. They therefore move in 5-minute steps where paper's moved
  every tick: a declared difference, measured during validation (entries the `gex` gates admitted
  or refused differently), never assumed small. `recorded_gex` (the `wall-clear` arm's input) is
  already a `core.regime.gex_at` read and replays exactly.
- *The ATM straddle* (`vol-floor`, `low_vol_refusal`) is two quote mids: recorded.
- *The selector* is pure over the snapshot plus a model frozen for the session. A past session
  replays only if that session's model file is kept; check that `selector-fit` keeps dated copies,
  and if it does not, add them before the selector is replayed.
- *The intraday pack* (`intraday_pack.build_pack`) already reads the gex history at or before
  `as_of` and replays exactly. The agent's reply does not (a model), so the `intraday-agent` arm is
  never re-run; its recorded decisions are re-priced.
- *Quote age and cadence.* The recorder kept quotes no older than 120 s at its own tick and stores
  each one's age; a replay applies paper's `max_quote_age_seconds` to that recorded age. The
  recorder's tick and paper's are separate ~15 s clocks, so a replayed tick can sit up to ~15 s from
  the paper tick it is compared with; "within one tick" below means exactly that.

**3. A statistics plan, declared before any number is read.**
- *Validation tolerance* (proposed here, fixed before the first comparison runs): entries matching on time within one tick, centre and side; credit within
  one price tick ($0.05); completion within one tick; per-session net within $25 or 5% of the
  session's gross, whichever is larger, on at least 9 of 10 sessions. A session outside tolerance is
  explained, not averaged away.
- *Power:* the intraday comparison's own arithmetic (`packages/flies/docs/intraday-agent-plan.md`:
  paired, one-sided 5%, 80% power, correlation 0.78, $385 a session) needs **about 42 sessions to
  see $100 a session**, 19 for $150. A replay of fewer sessions is a mechanics check, not a finding.
- *Grids declared up front.* Each question names its grid (credit floors, restack thresholds, start
  times) before the run, counts every cell, and corrects for the count (the `core.stats` module
  being added alongside this; Bonferroni or Holm over the declared family, as
  `core.rangefeatures` did). A cell that wins only uncorrected is reported as such.

**4. Coverage is SPX 0DTE only** (see *What it does not cover* above): bwb, calendars, pmcc, and
MEIC on XSP, NDX or RUT get nothing from this recording.

**5. Validate against the live ledger first, or alongside paper.** The questions this exists for
(the credit floor, restack, the start time) are live questions, and the live ledger is the one whose
fills the broker confirmed. Paper is the easier match (same tick rules, no fill model), so it is
done too, but the live-rules replay is validated against live `control`'s rows from the first
session, not after ten paper sessions.

**6. Operations** (below): a replay reads a copy or relies on WAL, and the file has a roll policy.

## Phase 1 — the flies replay

*Progress, 2026-10-17 (stage 1, mechanics).*

**Built.** `flies.replay` rebuilds each recorded sample as paper's snapshot and runs
`paper_loop.run_once` over it (`snapshot_fn=`, `replay=True`) into a scratch ledger. The rebuild
follows gap 2's rules (the profile within 360 s, session bounds from the spot trail up to the
instant). A replayed tick touches no live file: advice is skipped because `advice_decision` writes,
and the selector and agent arms are refused. CLI: `replay --date --out`.

**First run, control, against paper control on the four recorded sessions:**

| Session | Entries, replay / paper | Net, replay / paper | Agreement |
|---|---|---|---|
| 10-15 | 11 / 11 | −$878 / −$843 | every entry on side and centre, times within seconds |
| 10-16 | 22 / 22 | −$762 / −$879 | as 10-15 |
| 10-13 | 6 / 4 | | diverge from the first entry of the day |
| 10-14 | 12 / 13 | | diverge from the first entry of the day |

- **Why 10-13 and 10-14 diverge.** `choose_side` is `put if spot <= centre`, and control centres at
  the money. On 10-14 paper saw spot 0.36 below the 7760 centre and the recording 0.18 above it,
  so the first entry took the other side, and alternation carried the difference through the day.
  The side is a coin flip whenever spot sits on the centre.
- **Credits** differ by about one to three ticks: the two readers catch 0DTE quotes about a tick
  apart per leg (measured 2026-10-17, against live flies' own marks).

**What this means for gap 3's tolerances.** They were declared before the reader noise was
measured. Entries within one tick and credits within $0.05 cannot hold path for path on every
session, so stage 2 has to say how a diverged session is judged before the comparison is run.

**Stage 2, declared 2026-10-17 before any decision-level number was read: validate decisions, not
paths.** At every recorded sample, the replay is handed paper's own book as it stood at that instant
(positions entered before it; one completed later still an open vertical) and runs one tick. Its
decision is compared with what paper did:
- **Entries:** each paper entry is matched if the replay entered on the same side and centre in the
  30 s before it.
- **Completions:** each paper completion is matched if the replay completed that position in the
  30 s before it.
- **Episodes:** replay entries or completions paper never made are counted once per run of
  consecutive samples.
- **Divergence report:** the path replay's per-session report (where a session first diverges, and
  why) is kept beside the check, as a report and not a pass/fail.

Pass, per arm, over at least 10 sessions (the decision-level counts were not seen when these were
set):

| Measure | Threshold |
|---|---|
| Paper entries matched | ≥ 90% |
| Replay-only entry episodes | ≤ 10% of paper's entries |
| Paper completions matched | ≥ 85% |
| Replay-early completion episodes | ≤ 10% of paper's completions |
| Matched credits, absolute difference | median ≤ $0.10 and p90 ≤ $0.30 |

The credit bound is the reader noise measured on 2026-10-17 against live flies' own marks (per
contract: median $0.05, p90 $0.30 on verticals), set from that measurement and not from any replay.
A session that fails is listed with its cases, never averaged away.

**First read, 2026-10-17: the four recorded sessions** (`flies.replay_check`, CLI `replay-check`).
These are fewer than the 10 the test needs, so this is not a verdict.

| Measure | Result | Declared | Check |
|---|---|---|---|
| Paper entries matched | 47 / 50 (94%) | ≥ 90% | pass |
| Replay-only entry episodes | 2 / 50 (4%) | ≤ 10% | pass |
| Matched credits, absolute difference | median $0.10, p90 $0.26 | median ≤ $0.10, p90 ≤ $0.30 | pass |
| Paper completions matched within 30 s | 21 / 35 (60%) | ≥ 85% | **fail** |
| Replay completions with no paper twin within 30 s | 12 / 35 (34%) | ≤ 10% | **fail** |

- **The three missed entries.** One is 10-13's first entry, the at-the-money side coin flip. The
  other two are refused as duplicates in the same second paper completed one position and entered
  another, which is ordering inside one tick, finer than the timestamps.
- **Completions miss on timing.** A completion is a touch, and the two quote readers catch touches
  minutes apart in both directions: late by 0.5 to 56 minutes, and early on 10-14 and 10-16.
  - Under paper's live-limit rule the debit paid is the position's own limit, so the minute barely
    moves P&L. Whether a position completes at all does.
  - On that measure the replay agrees on 49 of 50 positions: it completes 34 of paper's 35
    completions at some point in the session, and none of paper's 15 strands.
  - That measure was found after reading these sessions, so here it is a report, not a pass.

**Declared 2026-10-17, for sessions from 2026-10-19 on (none recorded yet):** the completion test
is the outcome. A position paper completed must also be completed by the replay that session; one
paper stranded must also be stranded.
- **Agreement:** ≥ 95% of positions over at least 10 sessions from 10-19.
- **Timing:** the 30 s measures stay in the report as timing, no longer pass/fail.
- **Everything else stays as declared above.**

Stage 3 (the live-rules variant: the cap, the 10:15 start, resting completion orders, the fill model)
is built on this one only after it passes.

**Shape.** Rebuild each recorded tick as the snapshot the paper loop would have built, and feed it
to flies' own code: `book.process_snapshot` against a scratch ledger per arm. There is no second copy
of the rules. Fields beyond quotes are enriched from what is already recorded:
- GEX surface and walls from `gex_profile_history` / `gex_regime_history` (5-minute rows);
- the session's open, high and low from the spot trail;
- the vol complex from `market_regime_history`;
- the event calendar from `core.events`.

**Validation, before any number is used** (the suite's rule: a guard has to be shown to fail):
- **Paper:** replayed `control` against paper `control`'s real ledger, session by session for at
  least 10 sessions. Entries must match on time (within one tick), centre and side. Credits must
  match within one price tick. Completions and their timing must agree, and per-session net must
  agree within a declared tolerance.
- **Prove it can fail:** break a rule on purpose (spacing off, one rule changed) and confirm the
  comparison reports it.
- **Live rules:** the same replay driven by `live_loop`'s admission: the $1,000 cap via
  `exposure_at`, the 10:15 start, the resting completion limit, and the `fill_model` measurements
  that turn a touch into a fill. Validated against live `control`'s real ledger: entries per
  session, completions, and the cap refusals in `fly_decisions`.

**First uses, once validated:**
- the live credit floor;
- restack under live rules;
- the live start time and the cap size;
- which sources could join the selector;
- the intraday agent's tagged closes, re-priced at other times;
- the live tick cadence (declared below);
- any paper arm judged over sessions recorded since it was declared.

**Declared question: the live tick cadence (declared 2026-10-13, before any recorded session was
read).** The live loop ticks every 60 s; paper ticks every 15 s, and `live-shadow` lists the
1-minute tick as a difference paper cannot express. The tick decides when a new entry is evaluated
and when a resting entry is cancelled and replaced. It does not decide completions (the completing
order rests at the broker from the entry fill) or fill confirmation (the burst watcher polls on its
own clock), and the replay must hold both identical across cells. The start-time replay (#227) found
net swinging by up to ~$5k with the second-offset a 60 s tick lands on, more than any gap between
start times, so cadence plausibly matters, in an unknown direction.
- *Grid, fixed now:* 60 s at four phases (offsets 0/15/30/45 s), 30 s at two phases (0/15 s), and
  15 s. 15 s is the recording's own resolution and the finest cell; nothing finer is replayable.
  The baseline is 60 s as deployed, read as the mean of its four phases; the family is the two
  alternatives (30 s, 15 s), Holm-corrected.
- *Measures, per session, paired against the baseline:* structures admitted, strands, completion
  rate, net, and broker load (entry orders placed, cancel/replaces and fresh-quote calls). The spread
  across 60 s phases is reported beside every comparison, because it is the noise a cadence change
  has to clear.
- *Decision rule:* a faster cadence is adopted only if its paired per-session net difference has a
  one-sided 95% lower bound above zero after correction, over at least 20 sessions recorded and
  replayed out of sample, and its added broker load fits the broker's rate limits with room left.
  Short of that the tick stays at 60 s, and the read says "not shown", not "60 s is better".
- *Landing:* if adopted, the change lands at a declared boundary with a live-ledger break
  (`tick_cadence`), and `live-shadow`'s list of what paper cannot express drops the 1-minute tick.

**Declared question: the wing width (declared 2026-10-14, before any recorded session was replayed
at a width other than control's).** The 10-wide was set aside on 2026-09-29 (flies experiment log)
on net ÷ risk. Read through the three outcomes, its 15 paper sessions (08-17..09-04) also say:

- a miss cost about 1.1 times a completion, against 2.4 on 5-wide (−$292 / +$258 against −$256 /
  +$106), so break-even completion fell from about 71% to 53%;
- fees were 6% of winning gross against 14%;
- strands were not all-or-nothing (19 full, 9 partial, 5 worthless), and 11 of 24 staged-salvage
  cells beat holding, against 0 of 24 on 5-wide (flies backlog, "Staged salvage completion").

Against that, completion fell from 78% to 60%. Net ÷ maximum risk was 6.4% against 10.5% over
08-21..09-04 and −19.6% against −14.7% over 08-17..08-20. The quote history can settle it at real
prices without running an arm.

- *Grid, fixed now:* control's rules at `wing_width_strikes` 1 (5-wide, the baseline), 2, 3 and 4
  (10, 15 and 20 points).
  - **Everything else is control's:** the ticks, centre and side rule, the entry gates, and the
    completion rule (`credit − fee_buffer` on the paper side, live's resting limit on the live-rules
    side). The width is the one variable.
  - **Nothing wider is replayed.** A far leg can leave the ±100-point window on a trend day, and a
    replayed structure's legs are not declared held, so its quotes would be missing.
  - **Missing quotes are reported, not dropped.** Every replay reports its missing-quote ticks per
    width, and a session where an open leg lost its quotes is listed.
- *Buying-power cap on the live-rules side scales with width:* $1,000 per 5 points, so $2,000 at
  10-wide, $3,000 at 15 and $4,000 at 20. The paper side stays uncapped, as paper is.
- *Salvage, a second stage, on widths of 10 and over:* `scripts/flies_stranded_replay.py`'s staged
  completion. The completion limit relaxes from `credit − fee_buffer` to `credit + x` after T
  minutes.
  - x as a share of the width: 0, 2%, 5% and 10% (its 0 / 0.10 / 0.25 / 0.50 on 5-wide).
  - T: 5, 15, 30, 45, 60 and 90 minutes.
  - Read only for a width that passes the first stage, against that width's own no-salvage result,
    with the 24 cells Holm-corrected.
- *Measures, every width, paired per session against the 5-wide:*
  - **Both reported side by side:** net per session, and net ÷ maximum risk (the sum of
    `(width − credit) × 100` over entries, the buying power a live account holds).
  - **Beside them:** entries, completion rate against its break-even, average completed and
    stranded, strands full / partial / worthless, and fees and settlement as a share of winning
    gross. On the live-rules side, cap refusals too.
- *Decision rule:* a width advances to a declared paper arm (control plus `wing_width_strikes`,
  landed at a boundary) only if two things hold over at least 20 sessions replayed out of sample:
  - (a) its paired per-session net difference against the 5-wide has a one-sided 95% lower bound
    above zero, Holm-corrected across the three widths; and
  - (b) its net ÷ maximum risk is at least the 5-wide's over the same sessions.
  - Passing (a) alone reads "pays more by risking more" and does not advance. Passing neither reads
    "not shown". The live width changes only after that arm's own paper read and `live-shadow`
    agreement.
- *Out of sample:* every session recorded from 10-13 on. No width other than 5 has been replayed or
  read on them.

**Declared question: dealer gamma at entry (declared 2026-10-14, after looking at control's settled
rows through 10-14 and nothing later).** What decides a credit-first entry is the tape after it.
Over control's 419 entries (07-20..10-14, SPX spot trail):

- in the 60 minutes after entry, the straightest fifth of paths completed 67% for −$55 an entry and
  the choppiest fifth 78% for +$43;
- spreads that saw spot go 5 points both ways within the hour completed 85% (+$27), against 72%
  (−$7);
- chop before entry does not forecast that usefully. Swing counts persist (rho about 0.55), but
  trailing measures show no stable link to P&L, and their sign flips between eras.

Dealer gamma at entry did forecast it, the opposite way to the pinning story:

- with net GEX below zero (dealers short gamma), entries saw more swings after (6.3 against 5.9 of
  3 points or more in 60 minutes) and more two-way hours (33% against 25%);
- they completed 80% against 74% and netted +$13 an entry against −$2 (162 against 231 entries);
- that gap is about one standard error;
- the GEX sign was wrong on about 14% of entries before the 10-06 surface fix, and the 10-12
  one-gamma change redefined the inputs again. The 41 entries since 10-06 are too few to read P&L
  on, though swings tracked GEX more strongly there (rho −0.46).

- *The signal, fixed now:* the sign of net GEX at entry as the paper loop sees it (`entry_net_gex`;
  in the replay, the recorded GEX surface at or before the tick, as Phase 1 enriches it).
  - Sign only, not a level or concentration threshold. A level would be one more searched cut.
  - Zero or missing is no reading; the gate fails open on it.
- *Grid:* control, and control refusing a legged entry while net GEX at entry is zero or above
  (`short-gamma-only`). One variable, one comparison: no family correction.
  - `long-gamma-only` is replayed beside it as the mirror, to confirm the split, never as a
    candidate.
  - Width, window, gates and completion rule are control's.
- *Measures, paired per session against control:*
  - **The result:** net per session, entries, completion rate against its break-even, and net ÷
    maximum risk.
  - **The mechanism, reported beside it:** swings of 3 points or more and the two-way-hour share in
    the 60 minutes after each entry, split by GEX sign. The result is only believed if the mechanism
    shows too.
- *Decision rule:* `short-gamma-only` becomes a declared paper arm, and GEX sign a `selector`
  feature, at the next boundary only if all three hold over at least 20 sessions out of sample:
  - its paired per-session net difference against control has a one-sided 95% lower bound above
    zero;
  - its entries complete at a higher rate than control's;
  - short-gamma entries show more post-entry swings than long-gamma ones over the same sessions.
  - Anything less reads "not shown". It never becomes a live gate without its own paper read.
- *Out of sample:* sessions from 10-15 on, under the 10-12 GEX definition. 10-06..10-14 were looked
  at in the 41-entry aggregate above, so they are reported but not counted. Rows before 10-06 are
  never pooled with later ones.

## Phase 2 — the MEIC replay

**Shape.** The same foundation: `core.quotehistory`, the same enrichment, MEIC's own entry and
stop-management functions, and a scratch ledger per arm.

**Validation:**
- **Stops:** every arm that really stopped must be reproduced to the cent. This is MEIC's existing
  `analytics.validate_stop_derivation` rule.
- **Entries:** `control`'s entries must match its ledger, on the same tolerances as flies.

**What it unlocks:**
- **Gap-through stop fills.** A stop fills at the first recorded cost past its trigger, instead of
  at the trigger itself (currently flagged as optimistic). With MFE below, this is what MEIC truly
  needs the quote history for.
- **Profit-side exits.** Max favourable excursion becomes measurable, and with it profit targets,
  time stops, and hold-to-expiry against early exits.
- **Entry variants MEIC never ran.** Short delta, wing width, OTM floors, entry windows and
  spacing, priced on strikes never traded.
- **The advisor's `advised:*` experiments,** checked over recorded sessions instead of forward only.

**Corrections (2026-10-12).** This phase first promised "an uncensored stop curve". MEIC already
has one: its risk-book `control` runs with `per_side_stop_management: false`
(`config.risk.example.json`), holding every side to settlement, so its recorded maximum-cost path is
never cut short by a real stop, and `stop_policies` already derives each stop threshold's outcome
from it (whether a threshold fired is exact; `validate_against_control` checks the arithmetic). What
that path cannot give is WHERE a fired stop would have filled between ticks (priced at the
threshold today, optimistic by the gap-through) and the favourable side (MFE, `None`). Those two are
the quote history's contribution to MEIC; looser stop ratios are not.

**Order.** After the flies replay validates, because the shared parts (snapshot rebuilding,
enrichment, the validation harness) are proved there first. Where flies and MEIC would otherwise
disagree, the shared code moves to `cherrypick.core`, following core's own bar.

**Built and declared (2026-10-18), before any MEIC check number was read.** The replay is
`python -m cherrypick.meic.replay run` (MEIC's `paper.process_symbol` tick by tick over a recorded
session, at paper's 60 s cadence) and its check is `... replay check` (`meic.replay_check`). The
shared day loader moved to `core.quotehistory.RecordedDay` first (flies' replay unchanged, shown
byte-identical). How each snapshot field is rebuilt, including VIX, VIX1D and IV rank from Cboe's
daily files, is in `meic/replay.py`'s docstring.

- **The stops check is replaced, and why.** No SPX arm ran a per-side stop in the recorder era:
  `control` holds to settlement, and so do the `bp-*` arms; the arms that stop trade XSP, which the
  recorder does not keep. "Every arm that really stopped reproduced to the cent" has nothing to
  match. In its place: each side's recorded worst cost (`put_max_cost`/`call_max_cost`) on a
  full-day replay against paper's, paired by arm, strikes and width within a tick of entry. That is
  the path `stop_policies` derives every threshold from, so it validates what a stop study stands on.
- **Entries:** tested on paper's own book as of each entry (flies' method), matched on arm, short put,
  short call and width within 30 s; replay-only entries counted as episodes on the replay's 60 s grid.
- **The pass test**, over at least 10 recorder-era sessions, a failed session listed with its cases:

| Measure | Bar |
|---|---|
| paper entries matched | ≥ 0.90 |
| replay-only entry episodes per paper entry | ≤ 0.10 |
| matched credit difference, median / p90 | ≤ 0.10 / ≤ 0.30 |
| side worst-cost difference, median / p90 | ≤ 0.10 / ≤ 0.30 |

  The share of paper's condors the full-day replay paired is reported beside it, the base the path
  measures stand on. The backtest's history runs for MEIC unlock on the same rule as flies' (the
  vendor against the recording at the vendor's cadence, PR #383).

## Operations

- **Reading while the recorder writes.** `gex_history.db` is WAL from 2026-10-12, so a read-only
  connection (`core.db.connect_ro`, which `core.quotehistory` uses) never blocks the recorder and is
  never blocked by it. A long replay should still read a **copy**: one `sqlite3` backup (or `VACUUM
  INTO`) at the start gives it a fixed input it can be re-run against, which a live file is not. A
  bare file copy of a WAL database without its `-wal` misses the newest writes; use the backup API.
- **Growth.** ~8 MB a session for SPX, ~2 GB a year, on a file that also holds the regime series.
  Nothing prunes it.
- **Monthly roll** (to build before the file passes ~1 GB, i.e. by about February 2027): after
  each month's last session, copy that month's samples and quotes into
  `gex_quotes_YYYY-MM.db` beside it (backup API, verified by row count), then delete them from the
  live file. `core.quotehistory.session` then looks in the month's file when the live one has no
  rows for the date. The regime, profile and spot tables are not rolled.

## Rules that hold throughout

- **Read-side only.** A replay never writes a module's ledger or touches a broker. Building it is
  not measurement-affecting.
- **A rule a replay supports still lands at a declared boundary,** and on live through the live
  plan, the same as any other change.
- **Out of sample means sessions after the hypothesis was declared.** A rule found by searching
  recorded sessions is judged on later ones, never on the sessions it was found on.
- **The paper loop keeps running.** It is the replay's reference, it runs the model-driven arms
  (`intraday-agent`, `selector`) in real time, and live's `live_vs_paper` abort rule reads it.
  Moving rule-based paper arms to replay is a separate decision, taken only after validation.

## Timeline

| When | What |
|---|---|
| 2026-10-13 | First recorded session; Phase 0 check |
| from 10-13 | Phase 1 built while data accrues |
| ~10-26 (10 sessions) | Phase 1 validation, live-rules and paper side by side (gap 5) |
| after Phase 1 validates | Phase 2 (MEIC) |
| ~14 sessions out of sample | First tuning reads (credit floor, restack, start time), against their declared grids; an effect of ~$100 a session needs ~42 (gap 3) |
| ≥20 sessions out of sample | Live tick cadence read (60 s vs 30 s vs 15 s), against its declared grid and decision rule |
| ≥20 sessions out of sample | Wing-width read (5 / 10 / 15 / 20 at width-scaled caps), then the salvage grid on any width that passes |
| ≥20 sessions from 10-15 (~11-12) | Dealer-gamma read: `short-gamma-only` against control, with the post-entry chop beside it |
