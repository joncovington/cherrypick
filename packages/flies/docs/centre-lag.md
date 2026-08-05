# Centre lag — why the gex arm ends up on the losing side of a trending day

**Finding (2026-08-04), no configuration changed.** On a day the gex book lost $386.30 at 60%
completion while control made $613.15 at 95%, the difference was not the centring signal being
wrong about where price would settle. It was that `max_total_gamma` lags spot, and centre-vs-spot
is what silently picks which side we leg into. This document records the measurement and the one
gate worth watching, so a later decision has something to read rather than a memory of a bad day.

## Completion is nearly the whole P&L

A completed fly in the SPX 5-wide book nets about $18–22. A non-completion leaves an open short
vertical that settles at -5.00 and costs $220–286. That is an 11:1 asymmetry, so **breakeven
completion rate is about 92%**. Control at 95% barely clears it; gex at 60% is nowhere near. The
gex book decomposes exactly: three completions +$58, two misses -$444.

Read control's headline with the same suspicion. $521 of its $613 came from two flies (centres
7735 and 7740) that happened to sit on where SPX settled, 7736.52. Strip those and control made
$92 on eighteen trades and lost $286 on its one miss. Its real advantage over gex was completion
rate, not edge.

## The mechanism: centre position picks the side, and the side needs a direction

`engine.choose_side` is mechanical — `PUT if spot <= centre else CALL` — and
`fly.completing_side_direction` makes the call side complete on a **down** move. So a centre below
spot legs us into calls and needs a retracement. Both losers were exactly that:

| time | centre | spot | offset | needs | result |
|---|---:|---:|---:|---|---|
| 10:01 | 7650 | 7659.43 | **-9.43** | down | never completed, -$226 |
| 10:16 | 7675 | 7674.27 | +0.73 | up | completed, +$22 |
| 11:03 | 7700 | 7691.16 | +8.84 | up | completed, +$18 |
| 12:45 | 7725 | 7723.51 | +1.49 | up | completed, +$18 |
| 14:01 | 7730 | 7736.65 | **-6.65** | down | never completed, -$218 |

`max_total_gamma` put them there, and it does so systematically on a trending day. Total gamma
concentrates where open interest is, which is where the market **has been**, not where it is
going. Across the session's 389 gex iterations the centre sat below spot **78% of the time, median
-9.1 points**, moving in a discrete staircase — 7650, 7675, 7700, 7725, 7730, 7750 — that always
trailed a spot running +115 points. That lag is the call-side bias. Nothing about the signal was
"bearish"; it was late.

## Control dodged this by accident, not by design

All twenty control entries had centre **above** spot. Under ATM rounding that is a one-in-a-million
coincidence, so something else selected it: `center_already_occupied`, which fired 1,016 times.
Control can only open a strike it does not already hold, so on a rising day the first moment a new
ATM strike frees up is the moment spot has just crossed into the lower half of that strike's zone —
spot just below centre, put side, needs up, completes. **The occupancy gate quietly turned the
control arm into a trend-follower.** On a falling day it mirrors; on a chop day it stalls. This is
worth knowing before anyone reads a control win as evidence that ATM centring is good.

## What the evidence says about gating

**GEX surface features carry no completion signal.** Over the 52 regime-tagged rows, completed and
missed entries are indistinguishable: net GEX 44.25B vs 45.54B, spot-minus-flip 105 vs 94,
concentration 0.43 vs 0.43. And the day itself was the arm's own thesis stated as clearly as it
ever gets — spot above the gamma flip with strongly positive net GEX, the textbook dealers-long-
gamma pinning setup — and the index ran 115 points anyway. One session is not a refutation, but it
is a hit, and it is the kind this module exists to take honestly.

**A trailing-window trend read looked good and was measuring the wrong thing.** Requiring the
completing direction to agree with the trailing 20-minute spot drift (±2 points) blocks 42 trades
averaging -$85 while keeping 233 averaging -$25, and the sign holds across every look-back and
threshold tried (20/30/45/60 minutes by 2/4 points). But it separates P&L without separating
**completion** — 64% blocked vs 67% kept — which for a book where completion is 11:1 of the
economics means it is not touching the mechanism. On the gex arm it inverts outright (blocked gex
trades netted **+$107 at 90% completion**), and it does not even explain this session: at 14:01 the
trailing drift was -7.8, which *agrees* with the down direction, so the gate waves the second loser
through. Rejected.

**Measured against the session's OPEN instead, it is the sharpest split in the ledger.** See the
addendum below.

**Opening credit predicts completion but not money.** Within SPX 5-wide (113 trades) completion
falls monotonically across credit terciles, 76% → 68% → 62%; a rich credit spread is rich because
the market expects it to go against you. P&L is flat across the same terciles (-$21/-$21/-$25), so
it separates the mechanism without separating the outcome.

**Staleness is a red herring, but note it.** Every gex entry rode a surface whose oldest input was
24.5–30 minutes old against the 1800-second cap — riding the ceiling all session, never once
fresh. It does not explain the losses (completed vs missed median age 29.6 vs 29.05 minutes) and is
probably dominated by open interest, which updates infrequently by nature. Still worth confirming
separately that the *greeks* are not the stale component.

## The rule to keep in mind

We already compute the discriminator and do not use it: **signed centre-to-spot distance at
entry**. Gex arm, all sessions:

| centre position | n | completed | net |
|---|---:|---:|---:|
| below spot by >2pt | 7 | 4 | **-$449** |
| within 2pt of spot | 20 | 16 | +$130 |
| above spot by >2pt | 7 | 6 | -$69 |

A rule refusing the leg-in when the gex centre lags spot by more than about a strike blocks **both**
losers and keeps **all three** winners, turning the session's gex book from -$386 to +$58. Its
general form is direction-neutral: on a falling day the centre would lag *above* spot and the same
rule would block put-side entries. It needs no new data source, it uses a number already recorded on
every row, and unlike the trend gate it does not contradict the historical gex record.

**It is 34 trades. That is a hypothesis to watch, not a configuration change.** The honest reading
is that a lagging centre and a trending day are the same event seen twice, and one session cannot
tell a real gate from a well-fitted one.

## What would justify acting

- The below-spot bucket stays worse than the at-spot bucket over roughly 30 more gex entries,
  across at least one clearly *down*-trending session — that is the test that separates "centre lag
  is bad" from "this sample was up-trending".
- If it holds, the change is a lag cap in `engine.select_center` (refuse, do not silently degrade to
  ATM — a silent degrade makes the gex arm indistinguishable from control and destroys the
  comparison, which is the trap `max_center_distance_pct` already documents).
- If it does not hold, the finding to keep is the one about `center_already_occupied` turning an arm
  into a trend-follower, which is a measurement artefact affecting every arm and worth fixing
  regardless.

---

# Addendum — the trend read we already had and were not using

Written the same day, after the above. It changes what the leading hypothesis is, so it is appended
rather than folded in: the reasoning above stands on its own evidence and should stay legible.

## The claim that blocked this for three weeks was false

`classify_regime` said, in its own docstring, that a trend read was impossible here — it "needs a
reference point in time (spot now vs. spot N minutes ago) that no single snapshot carries." That was
never true. The shared stream cache has carried `stream_summary` (`day_open`, `day_high`, `day_low`,
`prev_day_close`) and `orb_ranges` all along, and `provider.py` read neither. `spot - day_open` is a
single-row lookup: no history, no cross-tick state, no violation of the no-I/O-on-the-decision-path
rule. The discipline was never what stood in the way — only an assumption about what a snapshot
could contain.

## What it measures out at

Over the 210 legged entries in the five sessions with `stream_summary` coverage (2026-07-29
onwards), refusing an entry whose completing direction opposes a >5-point drift from the open:

| | n | completion | avg P&L |
|---|---:|---:|---:|
| kept | 197 | **72%** | -$13 |
| blocked | 13 | **15%** | -$177 |

Completion collapsing to 15% is the point. Every other candidate gate tested here moved P&L while
leaving completion flat, which for an 11:1 payoff asymmetry means it was picking up something
incidental. This one hits the mechanism directly.

It also catches **both** of the session's gex losers and keeps all three winners — including the
14:01 entry that the trailing-window version waved through:

| time | needs | vs. open | verdict | outcome |
|---|---|---:|---|---|
| 10:01 | down | +28.8 | blocked | never completed, -$226 |
| 10:16 | up | +43.7 | kept | completed |
| 11:03 | up | +60.5 | kept | completed |
| 12:45 | up | +92.9 | kept | completed |
| 14:01 | down | +106.0 | blocked | never completed, -$218 |

**Opening-range breakout was also tested and is not worth carrying.** `orb_ranges` blocks 55 trades
at 67% completion against 68% kept — no completion separation at all. A breakout stays broken out
all day, so it blocks a quarter of everything indiscriminately.

## How this relates to the centre-offset rule above

They are the same event seen from opposite sides — a centre left behind by a moving market — and are
**not** two independent confirmations. Cross-tabulated over the same 210 entries:

| | n | completion | avg |
|---|---:|---:|---:|
| trend ok, offset ok | 197 | 72% | -$13 |
| trend ok, offset **fails** | **0** | — | — |
| trend fails, offset ok | 11 | 18% | -$168 |
| trend fails, offset fails | 2 | 0% | -$222 |

`center_offset` never fires outside `trend`. On this sample `trend` is strictly the wider net, and
it applies to every arm where `center_offset` only ever has content on the GEX-centred ones
(`gex`, `debit-first`, `bwb`) — the ATM arms sit at offset ≈ 0 by construction.

**Both are kept anyway, because they imply opposite remedies.** `trend` is a property of the market
and argues for *skipping* the trade; `center_offset` is a property of our own centring rule and
argues for *fixing* it. Muting the gex arm and repairing it are different decisions, and only the
second leaves an arm worth running. Note also that the subsumption rests on **2 qualifying trades**,
which establishes nothing in either direction.

Retirement condition, so this stays falsifiable rather than a standing judgement call: **if after
~30 further GEX-centred entries `center_offset` still never fires outside `trend`, it is redundant
and should go** — keeping the negative result, per rule 6.

## Status

Recorded, gating nothing, same as everything else in `classify_regime`. `trend_bucket` /
`trend_value` are written at entry and completion from `provider._session_bounds`.

Unlike `center_offset`, this one **cannot be backfilled**: nothing on a position row records where
the session opened, and the cache keeps one summary row per (symbol, trade_date) rather than a
history. The 13-blocked-trade result above came from joining the ledger to the summary rows that
happen to survive for five sessions; it is not reproducible from the ledger alone, and it is thin.
The tag fills forward from here.

Deliberately **not** built: a chop/trend distinction. That needs the path between open and now —
whether spot travelled 106 points once or crossed the open nine times — which really is cross-tick
state this module does not keep. `day_high`/`day_low` are on the snapshot and could approximate it,
but inventing that on the strength of one session is the mistake the honesty rules exist to prevent.
