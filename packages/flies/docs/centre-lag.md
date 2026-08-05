# Centre lag — why the gex arm ends up on the losing side of a trending day

**Finding (2026-08-04), no configuration changed.** On a day the gex book lost $386.30 at 60%
completion while control made $613.15 at 95%, the difference was not the centring signal being
wrong about where price would settle. It was that `max_total_gamma` lags spot, and centre-vs-spot
is what silently picks which side we leg into. This document records the measurement and the one
gate worth watching, so a later decision has something to read rather than a memory of a bad day.

## Completion is nearly the whole P&L

> ⚠️ **Both figures in this section are wrong — see corrections 1 and 2 in the second addendum.**
> Breakeven completion is ≈76%, not 92%, and a miss is not automatically a loss (7 of 43 won). The
> paragraph is left as written because the reasoning built on it below is still legible that way.

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

---

# Second addendum — 2026-08-05, the falling day

The session this document asked for arrived the next day: opened 7771.62, settled 7723.55. It
confirmed the mechanism and corrected four things stated above.

## The mirror held

The prediction was that on a falling day a lagging centre sits **above** spot, legs into puts, needs
an up move, and fails — the exact inverse of 08-04. It did:

| time | offset | needs | trend from open | completed | P&L |
|---|---:|---|---:|---|---:|
| 10:01 | **+14.7** | up | +13.6 | no | −$228 |
| 10:23 | −1.4 | down | +4.8 | yes | +$29 |
| 10:30 | −22.1 | down | +0.4 | yes | +$23 |
| 11:50 | **+2.5** | up | −34.1 | no | −$286 |
| 12:54 | **+1.3** | up | −27.9 | no | −$282 |

All three misses were up-completions on a day that fell; both completions were down-completions.
The arm result repeated too — gex −$744 at 40% against control's +$862 at 100%. Two sessions is not
a base rate, but the sign of the effect **flipped with the market rather than persisting**, which is
what separates a mechanism from an artefact of an up-trending sample.

Note the 10:30 entry, centred **22 points below spot** — the largest lag of the day — completed
without trouble. Lag alone is not the problem. Lag *against the direction of travel* is.

## Correction 1 — the completion asymmetry was overstated

Stated above as "a completed fly nets ~$20 and a miss costs ~$220-286, so breakeven completion is
≈92%." That came from a single session's completions. Over all 137 SPX 5-wide legged entries:

- completions average **+$59.87** (median $31.23) — some settle inside the wings for real payoffs
- misses average **−$185.21**

which puts breakeven completion at **≈76%, not 92%**. Still a punishing asymmetry and still the
number the strategy turns on, but the earlier figure was wrong, and anything reasoned from it should
be re-checked.

## Correction 2 — a miss is not automatically a loss

Described above as though every non-completion costs the full defined risk. **7 of 43 misses won**,
averaging **+$213.88**: the short vertical finished on the right side of settlement and kept its
whole credit. Two of 08-05's control misses made +$264 and +$262. A miss is *directional* — about
−$263 when settlement goes through the short strike, the credit when it does not. Under rule 4 the
uncompleted branch is bad on average, not bad by construction.

## Correction 3 — `center_offset` cleared its retirement condition

The condition was to retire it if it never fired outside `trend`. On 08-04 it never had, but that
rested on 2 qualifying rows. One session later the cell is populated, and on 08-05 the two rules
caught **different** entries:

- `center_offset` flagged the 10:01 miss (centre +14.7 above spot) that `trend` read as `flat`
- `trend` flagged the 11:50 and 12:54 misses, whose centres sat **inside one strike** and which
  `center_offset` structurally cannot see

Kept; condition answered rather than still pending. Current cross-tab, SPX-only across the three
sessions with `day_open` coverage (n=76 — earlier versions blended the XSP era, which the symbol
rules say not to do):

| | n | completion | avg |
|---|---:|---:|---:|
| trend ok, offset ok | 50 | 88% | **+$40** |
| trend ok, offset fails | 5 | 80% | −$29 |
| trend fails, offset ok | 19 | 37% | −$133 |
| both fail | 2 | 0% | −$222 |

## The band was wrong, and the fix is measured

`regime_trend_points` was 5 — one SPX strike — which was reaching for a familiar number instead of
measuring one. A strike is the resolution the *centre* moves in; it says nothing about how far a
session must travel before its direction carries information. Split by how far the day had
committed, the 5-point tag is **inverted** in the middle:

```
|drift| 10-25 pts:  opposing entries completed 100%  (n= 5)   <- a gate here would be actively wrong
|drift| 25-60 pts:  opposing entries completed   0%  (n= 8)
|drift|   60+ pts:  opposing entries completed  14%  (n= 7)
```

Sweeping the band:

```
band  5:  kept 87% comp | opposing n=21  comp 33%  avg -$142
band 20:  kept 89% comp | opposing n=15  comp  7%  avg -$209
band 25:  identical to 20
band 30:  kept 84% comp | opposing n=12  comp  8%  avg -$195
```

**Changed to 20.** 20 and 25 give identical splits, so this is a plateau rather than one lucky cut —
but it is still chosen on the same 76 rows that measure it, so it is the current best estimate, not
a calibrated constant.

This also names the dimension's own failure mode. 08-05 10:01 sat at +13.6 from the open, inside the
dead zone, so a 5-point band called it a trend, approved an up-completion, and the day then reversed
to settle 48 points below its open. **Trend-from-open lags too** — slower than a trailing window,
not immune — and no band value fixes a reversal.

## Correction 4 — `trend` is backfillable after all, for now

Stated above as un-backfillable. True in principle, since no position row records its session's open
— but `stream_summary` currently retains a row per (symbol, trade_date) back to 2026-07-29, so those
sessions can be reconstructed by joining on trade_date, which is how every number in this addendum
was produced. The cache offers no retention guarantee, so this is a window that will close rather
than a property to rely on. Anything wanted from those sessions should be computed while it is open.

## Ops note: the tags did not record on 08-05

Every figure here was reconstructed, because the session wrote NULL to both new columns. The repo
was on another branch when the loop ran, and the paper loop imports from the working tree — so
**whichever branch happens to be checked out silently determines what the ledger records.** The four
older dimensions populated normally, which is exactly what made it look fine at a glance. This is a
data-integrity failure that leaves no error behind, only absent columns, and it deserves a guard.
