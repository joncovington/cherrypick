# Earnings screener plan

Status: plan, 2026-10-16. Step 1 (history) done 2026-10-17: no edge after costs (below). Shipping
anyway as an earnings calendar laid out like the vendor's (decided 2026-10-17).

| Step | State (2026-10-17) |
|---|---|
| 2, the calendar builder (`technicals earnings-calendar`) | built, with its two nightly jobs |
| 3, pricing | built: the expected move's per-expiration IVs (the IV fetch), and the suggested spreads snapped, quoted and judged at 11:30 / 14:30 ET for strong reads reporting within the week (`fetch_screener_greeks.py measure`, `earnings-legs/`) |
| 4, the console tab | built |
| 5, the forward record | built: marks on the reaction day and the next at 11:30 / 14:30 ET (`earnings-marks/`), the record rebuilt each morning at 06:50 (`technicals earnings-record`), and an "earnings record" tab |

A nightly earnings calendar: every upcoming announcement, with a direction read, strength, IV rank
and expected move. Since 2026-10-17 the direction and strength are ours (Bearing) and the IV rank
the broker's, which picks the spread's side; the vendor's are kept in the file, never shown or used. Strong reads also get the directional spread the vendor's
earnings-navigator rule builds, priced on our own chains and cut to liquid trades. It is a read
surface, a fourth tab on the console's Screeners page beside the three income lists. It places no
order and opens no paper position.

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
(`vendor-securities/<day>.json`: `directionalEdge` `sb`/`sbe`, `liquidityRank`, `ivRank`). Since
2026-10-17 the calendar reads whichever list `technicals securities` names current: the vendor's
while the collector is on, else a public one whose liquidity rank is ours (see packages/technicals). Bearing
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

**Decided 2026-10-17, after step 1:** ship anyway, laid out like the vendor's earnings calendar
(the user shared its page). It is a calendar first: every upcoming announcement, grouped by day,
with the vendor's columns and filters. The navigator's spread is a column on it, under a page note
that history shows no edge.

**The vendor's calendar, and our source for each column.** Checked against its page for Oct 20-21:
all ten names it showed under the default filters are on our list too. CB and ELV were missing only
because the broker's report dates don't cover them, and Dolt does.

| Vendor column / filter | Our source |
|---|---|
| Date, grouped by day; Today / Next 7 days / Next 30 days | Dolt calendar ∪ the broker's dates (confirmed flag), deduplicated; a disagreement over a day flagged |
| Time (before / after the open) | Dolt's `when`; blank shown as unknown, never assumed |
| Company | Dolt `stocks.symbol.security_name`, with the share-class boilerplate trimmed |
| D-Edge, Strength n/10, IV Rank, liquidity (very / somewhat), optionable | The nightly security list: `directionalEdge`, `technicalRank`, `ivRank`, `liquidityRank`, `hasOptions`. These are the vendor's own values (CB 9/10 at 67%, ELV 10/10 at 43%, NFLX 2/10 at 74%, as on its page), one session old |
| Last price | Our bars' last close |
| Exp. move | A chain per name, nightly (below) |
| Sector | `universe/sectors.json` (the vendor's) where it has the name, else Yahoo's (`scripts/fetch_sectors.py`, 18:40 ET, relabelled to the vendor's names; it agreed on all 120 names sampled, 2026-10-17). The row's `sector_source` says which |
| Region, industry | Not carried (US names only; no industry source) |

**The expected move shown is one standard deviation**, chosen 2026-10-17 to match the vendor's.

**How the expected move is calculated** (researched 2026-10-17):
- **The straddle prices the expected absolute move.** Under a normal distribution that is √(2/π) ≈
  0.8 of one standard deviation, so ATM straddle ≈ 0.8 × price × IV × √t.
- **The broker's own content gives two rules:**
  - for a binary event, 0.85 × the front-month ATM straddle. This is the suite's
    `STRADDLE_TO_EM_FACTOR`, about two thirds of an SD;
  - otherwise price × IV × √(days / 365), described as the one-SD, 68% range.
- **The broker's market-metrics API has no expected-move field.** It does return implied volatility
  for every listed expiration. Its earnings date carries no time of day.
- **A weighted 60/30/10 straddle-and-strangles version is quoted by third parties** as the broker
  platform's figure. Nothing from the broker confirms it.
- **The vendor's figures are one SD.** On nine names for Oct 20-21, they sit 1.16-1.29× the raw
  straddle. The two one-SD formulas both match them about equally well:
  - the broker's IV for the event expiry × price × √(calendar days / 365): mean miss 0.32 points;
  - the straddle ÷ 0.8: mean miss 0.27 points.

  The raw straddle misses by about 1.6 points and 0.85 × straddle by about 2.9. NFLX is the largest
  miss on both (vendor 11.0, ours ~11.9); the vendor's unstated week adjustment may explain it.

**So the column is the broker's IV for the first expiry on or after the reaction day × price ×
√(calendar days / 365)**, labelled "±1 SD":
- It needs no chain request. The nightly market-metrics call already returns every expiration's IV,
  batched, for ~490 names. Widening its universe to the calendar's names, and keeping the event
  expiry's IV, covers the whole calendar.
- The straddle-based figures (÷ 0.8, and the suite's 0.85) are in the tooltip where a chain was
  priced.

**The user's credit long leg stays on the suite's 0.85 definition**, which step 1 priced.

### 2. The calendar as a pure function (packages/technicals)

`earnings_calendar.py`, from local data only (files and the local Dolt clone, as `land` reads it),
never the broker, so it stays credential-free. Each night it writes every announcement in the next
30 days: one row with the columns above, plus:
- the entry session, worked out from the timing (unknown timing: unknown);
- the navigator's structure for strong reads: debit under the IV-rank cut, credit over it, with
  target legs (debit: long ATM, short at the 0.85 EM edge; credit: the user's legs). The cut and
  the trade expiry come from a declared config block, defaulting to 50 and the cycle monthly.

Output `technicals/earnings-calendar/<day>.json`, kept per day, never overwritten in place. CLI
`earnings-calendar`. Job after the evening security-list capture, so a row's direction and rank are
the freshest the list has.

### 3. Pricing (scripts)

Two broker reads, both paced, both in jobs that already run:
- **The expected move for every stock in the calendar's 30 days:** the event expiry's IV from
  market-metrics (`scripts/fetch_iv_rank.py`, 18:50). That needs two changes:
  - widen the job's universe to the calendar's names (it misses CB and ELV today);
  - keep each name's per-expiration IVs, or just the event expiry's.

  No chain is needed.
- **The suggested spread's legs**, for strong reads only, evening and again at 11:30 / 14:30 on the
  entry day under the leg bars (10% of mid or a nickel, 100 open, 10 traded). This is an `earnings`
  plan in `fetch_screener_greeks.py`. A row whose legs fail is marked thin.

### 4. The console tab

A fourth Screeners tab, "Earnings", with its own reader and route (`/api/screeners/earnings`) and
payload type; the income lists' row shape is not stretched to fit.

- **Layout:** rows grouped by report day, the vendor's columns in its order, then ours: the
  suggested spread (debit or credit, legs, expiry, its share of the width, max loss), and its
  liquidity once read.
- **Filters:**
  - window (Today / 7 days / 30 days);
  - before / after the open;
  - direction (multi-select);
  - IV-rank range;
  - liquidity (very / somewhat);
  - optionable;
  - sector;
  - symbol.
- **Defaults:** the vendor's view (strong bull + strong bear, very liquid, optionable, 7 days),
  plus a liquid suggested trade. Each has its escape.
- **A standing note:** the 2020-26 replay of this spread lost 4.3% on risk, and the direction read
  called the reaction on 48-49% of events. Linked to this doc.
- **On each row:** the session the direction read describes, and whether the report date is
  confirmed.

### 5. Record and score forward

After each report, record:
- the reaction-day close;
- the realised move against the expected move;
- the spread's mark at the reaction close and at day +2.

These go into the same per-day file, under a `results` block. A fidelity-style tab then shows the
rule's running result on our own prices. This is the forward check step 1 can't give, since the
history is end-of-day chains.

## Step 1 result (2026-10-17): no edge after costs

Run: `scripts/earnings_navigator_history.py`, 2020-01 to 2026-10-02. Output in
`~/.cherrypick/data/research/earnings-navigator/run-2026-10-17.json`; re-analyse it with
`--from-json`.

- **Sample.** 14,364 events from 1,049 names passed the price and volume screen. 3,587 were priced:
  1,939 strong bull, 1,040 strong bear, 608 in the tier below.
- **The direction read doesn't call earnings reactions.**
  - A strong bull moved up on 48.8% of reactions; a strong bear moved down on 48.4%; the tier below
    did no better.
  - That is a coin flip, leaning contrarian like the EM-fly study's trend signals.
- **The default rule loses.** Cut 50 on entry-day IV rank, cycle monthly, the user's credit legs,
  out at the reaction close:
  - 750 trades with entry and exit on the right days;
  - -4.3% return on risk, 42% won, -$47 a trade;
  - the same trades in the opposite direction: -5.0%.
- **Every setting loses** (return on risk, exact trades):

  | Setting | Return on risk | Note |
  |---|---|---|
  | IV-rank cut 33 / 45 / 50 | about -4% each | barely differ |
  | Always credit | -3.6% | |
  | Always debit | -8.3% | |
  | Cut read five weeks before the report | -6.2% | |
  | Event expiry | -1.4% | +5.2% before 2025, -3.7% since |
  | d30 / d45 / cycle | -2.4% / -5.1% / -4.3% | |
  | Credit legs (user, user_raw, v2025, v2026) | -4.3%, -3.5%, -3.4%, -6.4% | |
  | Exit day 2 | -3.7% | |
  | Exit at expiry | -6.8% | |
  | Strong plus the tier below | -3.8% | |

  The best of the 480 grid cells, +1.2% (cut on IV rank five weeks earlier, at 45; event expiry;
  credit beyond the raw straddle; held to expiry), is selection noise. Its neighbours lose, and its
  opposite-direction twin is -4.4%.
- **The check run agrees.** `strict` (both legs quoted) is worse, as its bias predicts: about -10%
  across the board.
- **Limits.**
  - The liquidity screen stands in for liquidity rank 1.
  - The direction read is 85-95% faithful.
  - Dolt's IV rank may not be the vendor's.
  - Two thirds of the exact trades are from 2025-26.
  - None of these is the size of the loss, and the direction coin-flip doesn't depend on pricing at
    all.

What this means for the plan: the list can still ship as a read surface, as the plan said it would,
but the page must say that history shows no edge. And step 5's forward record is what would show a
change.

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
4. **The direction read on history: measured 2026-10-17, good enough.**
   - Method: fit Bearing on one captured list and name the other list's labels from inputs built
     from our own bars. Both ways round, 10-07 and 10-16.
   - **The security list is a session old.** Both lists were captured after the close (17:16 and
     19:56 ET), yet their trend readings match the PREVIOUS session's close. Our trend inputs read
     one session back agree on 92-98% (1M) and 95-97% (6M) of very-liquid stocks; read on the same
     session, 62-84%.
   - The 1-10 score is the remaining error: exact on about two thirds, tier right on about 90%.
   - End to end, one session back, very-liquid stocks:
     - strong bull: recall 88-96%, precision 80-88%;
     - strong bear: recall 77-88%, precision 97-98%.
   - Misses fall on a neighbouring state (pullback, bear), not the opposite direction.
   - So step 1 reads Bearing one session before the entry close, as the live list does, and says
     the direction is ~85-95% faithful.
   - What it means for the live list: the direction read is always at least a session behind the
     entry close. Show the list's read and the session it describes; never silently swap in a
     fresher one of ours.

**The live list**
5. **Timing: largely closed 2026-10-17.** Dolt's before/after-the-open field was about 47% blank,
   so two more sources were added:
   - the broker's market-metrics `time-of-day`, which agreed with Dolt on all 179 names both timed;
   - Nasdaq's public calendar, which agreed with both on all 378 and timed 64 they did not.

   Where none of the three gives a time, the company's last timed report stands in as a marked
   estimate (right 97% of the time on Dolt 2022-26). It never sets the entry session.

   Coverage on 10-16, under the vendor's default view: 19 of 20 timed in the next week. Over 30 days,
   97 of 118 are confirmed, 19 estimated and 2 unknown. Yahoo was looked at and left out: its times
   look like placeholders, with dates that disagree.
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

**Note (2026-10-17):** better backtesting may become available in the future. If it does, re-run
step 1 on it, and treat this study's result as the first read, not the last.

## Not in this plan

- **A paper arm.** The earnings module already has seven strategies and its own entry scan. Paper
  trading the navigator spread there would be measurement-affecting and lands at a declared
  boundary, only if step 1 or step 5 earns it.
- **Capturing the vendor's own navigator list** (declined 2026-10-16).
- **A valuation gate** (the 2025 version).

## Open questions for the user

1. **IV-rank cut:** history barely separates 33 / 45 / 50 (all about -4%), so the default is 50,
   the newest deck's.
2. **Trade expiry:** the default is your cycle monthly. History's least-bad was the event expiry
   (-1.4%), but that is positive only before 2025.
3. **Window:** settled by the calendar layout: Today / 7 / 30 days, defaulting to 7, as the vendor
   does.
