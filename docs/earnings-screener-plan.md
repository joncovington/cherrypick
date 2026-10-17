# Earnings screener plan

Status: plan, 2026-10-16. Nothing built yet.

A nightly list of upcoming earnings announcements, each with a directional spread built by the
vendor's earnings-navigator rule, priced on our own chains and cut to trades that are actually
liquid. It is a read surface, a fourth tab on the console's Screeners page beside the three income
lists. It places no order and opens no paper position.

The user chose (2026-10-16) to build it from the stated rule only. We do NOT capture the vendor's
own navigator list, so there is no answer key: fidelity can't be scored as it is for the income
lists. The check on the rule is history instead (step 1).

## The rule, as the material states it

Reconstructed from the vendor's webinars on disk (`market-report/vendor-education/`). The rule
changed over time: the 2025 version gated on valuation; the version from July 2026 is purely
technical. This plan follows the 2026 version.

| Part | Rule | Source confidence |
|---|---|---|
| Liquidity | "Very Liquid" options only: liquidity rank 1 on the dashboard's security list | stated |
| Direction | Strong Bull or Strong Bear direction read only; every mixed state passes | stated |
| Structure | low IV rank → debit spread in the direction; high → credit spread on the other side | stated |
| IV rank cut | 50 in the week-3 navigator deck (Oct 2026); 33 in the general IV-rank lesson; earnings examples behave like ~40-45 | **conflicting** |
| Expected move | ATM straddle on the first expiry after the report ÷ price, shown ±% | stated (no 0.85 factor; a week adjustment is mentioned, formula not given) |
| Debit legs | long ATM to ~0.40Δ, short near the expected-move edge (~0.16Δ); debit about a quarter of the width | stated, long leg conflicting |
| Credit legs | short just outside the expected move (2026) or ATM (2025); long ~0.25Δ; credit above a third of the width | conflicting short leg; **user's choice below** |
| Expiry | not a single rule: about 30 DTE for debits, 45 for credits in 2025; "about three weeks" and "the monthly, not the weekly" in 2026 | **conflicting** |
| Window | "only a couple of days at a time" | stated, no number |
| Timing | enter in the last session before the report (the same day's close for after-close, the prior close for before-open); out within a few days; never to expiry | stated |
| Track record | none published | — |

**Credit legs, decided by the user (2026-10-16):**
- The short leg is at the money: the strike nearest the price.
- The long leg is just outside the expected move: the first listed strike beyond price − EM for a
  bull put spread, or beyond price + EM for a bear call spread.
- The width is therefore set by the expected move, not by a fixed delta, and the max loss per
  spread grows with it. The row shows both the width and the max loss.
- The EM is the one the row displays: the suite's definition, 0.85 × the ATM straddle on the first
  expiry after the report. That is the event's move, priced on the event expiry, even when the
  spread itself sits on a later expiry.
- The credit's share of the width is shown, never used as a gate. A third of the width was the
  income lists' floor, not this rule's.

This is neither of the vendor's versions:
- the 2025 version paired the ATM short with a ~0.25Δ long;
- the 2026 version put the short outside the move.

History still prices the vendor's two as comparisons (step 1).

Direction read and liquidity rank come from the security list the collector already saves nightly
(`vendor-securities/<day>.json`: `directionalEdge` `sb`/`sbe`, `liquidityRank`, `ivRank`). Bearing
(`packages/technicals` `compass.py`) names the strong states 95% / 99% of the time from the list's
own inputs. Those inputs rebuild from our bars, so history needn't depend on the captured lists,
but the full chain from bars to label still has to be measured (gap 4).

Size today (2026-10-16): 168 liquidity-rank-1 stocks read strong. 19 report within 7 days and 61
within 14 days. About three quarters of them have IV rank ≥ 50, so at a 50 cut most rows would be
credit spreads.

## What exists, and what's missing

Exists:
- **Calendars.** Broker report dates to ~8 weeks, with a confirmed/estimated flag
  (`iv/tastytrade.json`). Dolt to ~5 weeks, with before/after-the-open timing about half blank.
- **The suite's one expected-move definition**, `core.structures.expected_move`, and the earnings
  module's front-expiry rule, already copied into `scripts/fetch_earnings_moves.py`.
- **Chain recording for arbitrary names, with the leg bars** (`fetch_screener_greeks.py`: evening
  greeks, 11:30/14:30 regular-hours readings, `rebar`).
- **The liquidity verdicts, the Screeners page, and its earnings-in-cycle logic.**
- **The history:** Dolt option chains and events, which the EM-fly study already prices
  (`scripts/emfly_history.py`).

Missing:
- one joined row per announcement;
- leg selection for a directional earnings spread;
- a kept history of each night's list and what happened after;
- the console tab;
- a second timing source, so an unknown before/after-the-open time is shown as unknown, never
  assumed.

## Steps

### 1. History first: does the rule make money after costs?

Answers in weeks, not years. Before anyone reads the list as advice, replay the rule over the Dolt
events the EM-fly study already prices (2020 onward, ~5,000 priced events):
- direction from Bearing, point in time;
- IV rank from Dolt IV;
- the spread priced on the recorded chain;
- entered at the last close before the report and exited at the reaction-day close (and day +2,
  as a variant);
- the same entry and settlement costs the EM-fly study charged.

Variants to separate, each a single switch:
- IV-rank cut 33 / 45 / 50;
- debit long ATM vs 0.40Δ;
- credit legs: the user's (short ATM, long just outside the EM) is the default. Compare it with the
  vendor's two (ATM short with a 0.25Δ long; short outside the EM), and with the long placed
  outside the raw straddle rather than 0.85 of it;
- expiry: first after the report vs ~30 / ~45 DTE vs the cycle monthly;
- direction strong-only vs strong plus the next tier.

Also report the rr25-skew split. The EM-fly study found skew contrarian (53%), which may say
something about the direction read on report days.

Output: `scripts/earnings_navigator_history.py`, JSON with `--from-json` re-runs, and a short
results section in this doc. This step decides the defaults below. If no variant is positive after
costs, the list still ships as a read surface, but the page says so, as the EM-fly result is
recorded.

### 2. The rule as a pure function (packages/technicals)

`earnings_screen.py`: from files already on disk, never the network, so it is credential-free like
the rest of the package. For each announcement in the window it:
- joins broker date (preferred), Dolt date and timing, the security list's direction, liquidity
  rank and IV rank, the tradable verdict, and the type (stocks only);
- works out the entry session from the timing. Unknown timing is shown as unknown, never assumed
  after the close. If the two date sources disagree by more than a day, the row is flagged;
- picks the structure and the target legs (deltas or expected-move edge) from a declared config
  block, with defaults from step 1.

Output `technicals/screeners/earnings/<day>.json`, kept per day, never overwritten in place.
CLI `score-earnings` (name open). Job at 06:45 ET, after `technicals-screeners`.

### 3. Pricing and liquidity (scripts/fetch_screener_greeks.py)

An `earnings` plan beside the list rows and candidates:
- **Evening (19:00):** price the planned legs and the straddle for the expected move, for
  announcements in the window.
- **11:30 and 14:30 on the entry day:** regular-hours leg readings under the same leg bars (10% of
  mid or a nickel, 100 open, 10 traded). Those are the readings that matter, taken hours before the
  entry close.

A row whose legs fail is marked thin and hidden by default, exactly as on the income tabs.
This adds to the existing paced feed session; it is not a new collector.

### 4. The console tab

A new reader and route (`/api/screeners/earnings`) with its own payload type; the income lists'
row shape is vendor-specific and is not stretched to fit.

Columns:
- symbol;
- report date and before/after the open (or unknown, and whether the date is confirmed);
- entry session;
- direction, IV rank and the expected move ±%;
- the spread: legs, expiry, debit or credit, and its share of the width;
- leg liquidity.

Defaults: tradable names, a liquid trade, confirmed dates. Each has its escape, as on the other tabs.

The expected move shown is the suite's definition (0.85 × straddle). The vendor shows the raw
straddle, so its figure reads about 18% wider; the column says which.

### 5. Record and score forward

After each report, record:
- the reaction-day close;
- the realised move against the expected move;
- the spread's mark at the reaction close and at day +2.

These go into the same per-day file, under a `results` block. A fidelity-style tab then shows the
rule's running result on our own prices. This is the forward check step 1 can't give, since the
history is end-of-day chains.

## Gaps

Checked 2026-10-17 against Dolt (`nav_gap_checks`, a one-off): 458 tradable names, earnings events
2020 to 2026-10-02.

**History (step 1)**
1. **IV rank is inflated before a report, and its level is set by the regime.**
   - Across 6,026 events, the median IV rank on the entry day is 50, against 26 five weeks earlier.
     The share at 50 or above is 50% overall: 10% in 2021, 80% in 2022 and 2024, 73% in 2026.
   - The debit side does fire, but which side the rule picks follows the volatility regime more
     than the name.
   - Step 1 adds the cut read on IV rank five weeks before the report, beside the entry-day reading.
   - Dolt's rank is 30-day IV against its year range. The vendor's own IV-rank definition isn't
     stated, so the two may differ.
2. **The chain history is thin before 2025.**
   - Snapshots are Monday/Wednesday/Friday only before 2024 (AAPL: 150-ish a year), partly daily in
     2024, and daily from 2025.
   - In a 420-event sample, an entry-day snapshot existed for about half the events before 2025.
     Both entry and reaction-day snapshots existed for about a quarter before 2024, and for about
     two thirds in 2025-26.
   - Where a snapshot exists, everything the rule needs was present in nearly every case: the event
     expiry, a 21-60 day trade expiry, the at-the-money strike, and a strike beyond the expected
     move on both sides.
   - That gives about 2,500 events with exact snapshots, half of them from 2025-26, and fewer after
     the strong-direction filter. Report 2025-26 separately. Don't fill the missing days with
     modelled prices for the headline result.
3. **The long leg sits further out than the expected move.** The first listed strike past the move
   lands a median 20-30% of an EM beyond it, because trade-expiry strikes are coarse. So the rule's
   long leg is effectively at ~1.25× EM. That is a property of the rule, live as well as in history.
4. **The direction read isn't proven on history.** Bearing names the strong states 95% / 99% when fed
   the vendor's own inputs. Our bars rebuild those inputs (trends 99.7%, score 52 of 62), but the
   full chain from bars to label is unmeasured. Measure it against the captured security lists
   before step 1 leans on it.

**The live list**
5. **Timing is unknown for about half the announcements.** Dolt's before/after-the-open field is about
   47% blank right now (only 4% blank across 2020-26 events for these names). The broker gives no
   time of day. Timing sets the entry session, so the row says unknown rather than assuming.
6. **Broker dates miss some names.** The broker's dates (confirmed flag, ~8 weeks out) cover 141 of
   today's 168 candidates, because the nightly IV-rank fetch has its own universe. The rest fall
   back to Dolt (~5 weeks), which is enough for this window. Adding the candidates to that fetch
   closes it.
7. **The direction read is captured from 2026-10-16 only.** Earlier days need Bearing.
8. **Evening prices are after-hours quotes.** The 19:00 credits and debits are indicative only. The
   entry-day 11:30 / 14:30 readings are the real liquidity check.
9. **A before-open report's entry session is the day before.** A date added or moved late can miss
   its regular-hours reading. The row then says "no reading" rather than passing.

**Recording**
10. **No per-event record of the expected move against the realised move exists yet.** Step 5 creates
    it.
11. **There is no answer key.** Declined 2026-10-16, so we measure whether the rule works, not how
    faithfully we reproduce it.

## Not in this plan

- **A paper arm.** The earnings module already has seven strategies and its own entry scan. Paper
  trading the navigator spread there would be measurement-affecting and lands at a declared
  boundary, only if step 1 or step 5 earns it.
- **Capturing the vendor's own navigator list** (declined 2026-10-16).
- **A valuation gate** (the 2025 version).

## Open questions for the user

1. **IV-rank cut:** 50 (the newest deck) or what history picks?
2. **Expiry:** the rule's short expiries, or your standard monthly 30-60 DTE (the income tabs show
   both: the listed trade and yours)?
3. **Window:** the vendor's couple of days, or a week so the list is visible ahead of entry day?
