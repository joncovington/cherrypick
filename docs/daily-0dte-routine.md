# A daily SPX 0DTE routine: rules reconstructed from five sessions

*Drafted 2026-10-17 from his live streams for Monday 2026-10-12 to Friday 2026-10-16 (YouTube, "Daily
Profit Plan"), read in full and checked against screenshots of his broker's order panel and charts.
This is hypothesis 8 of [edge-hypotheses-plan.md](edge-hypotheses-plan.md). Nothing is built. Its
box-edge test ran 2026-10-17 on licensed history
([history-studies-2026-10-c.md](history-studies-2026-10-c.md), section 2); the results are kept
locally, per the licence.*

## The short version

The Daily Profit Play (DPP) is not an iron condor sold at the open. It is a **resting order to sell a
condor at a credit well above the market**, placed before or at the open. It fills only if price runs
toward one of the short strikes. In practice it is **a fade of a move toward the edge of the day's
expected-move box**, packaged as a condor.

A second, "backup" order on the same strikes asks for width minus about $1. It fills only if price
reaches or crosses a short strike, and it turns the day into a roughly 1:1 bet that price closes back
inside. A small broken-wing butterfly near the threatened short strike is the defense.

So the edge, if there is one, would be **reversion at the expected-move box edge**: the box holds on
~82–90% of days by his own indicator's count. It is not the open-to-close variance premium a
mechanical condor sells. That is a narrower claim, and one our recorded data can test.

## Inputs he reads each morning

- **Box number:** the broker's expected move (tastytrade, pre-market) alongside a figure from his
  levels tool ("smart range"), rounded up: 30.11 on 10-12, 29 on 10-13, 41.43 on 10-14 (he kept the
  pre-CPI figure when the post-news number fell), 48 on 10-15, 41.96 on 10-16.
- **Gap adjustment:** the box is widened by the gap. On 10-12 and 10-13 the numbers fit "the edge on
  the gap side is measured from the open, the other from the prior close":
  - 10-12: open 7813 + 30.11 = 7843; prior close 7811 − 30.11 = 7781.
  - 10-13: prior close 7807 + 29 = 7836; open 7795 − 29 = 7766.

  On 10-15 he centred on the open instead. The rule is consistent in spirit, loose in detail.
- **Levels:** HVL (gamma pivot), call wall, put wall, absolute GEX, max pain, from a commercial GEX
  tool. Also the overnight high and low, and his own box indicator's "opening-bell" box.
- **His indicator's own statistic** (seen on screen): 82% of the last ~73 sessions closed inside the
  "BEM" box and 90% inside a one-sigma box.

## The DPP, as placed

| Session | Main order | Backup order | What happened |
|---|---|---|---|
| Mon 10-12 (holiday, EM 30) | 5-wide, short 7855C / 7765P, **$1.00 cr** | Same strikes, **$4.00 cr** | Pending during the stream; price stayed ~7800–7820 |
| Tue 10-13 (EM 29) | 5-wide, short 7850C / 7750P, $1.00 | Same, $4.00 | Never filled (SPX held 7794–7810) |
| Wed 10-14 (CPI, EM 41.43) | 10-wide, short 7835C / 7730P, **$1.50** (a second set at 7840C / 7725P cancelled) | Both strike sets at **$8.75** | Main filled ~9:58 as SPX fell to ~7760. Backups filled 2:00 pm (7730 set) and 3:00 pm (7725 set) as SPX broke below. All closed 3:33 pm. |
| Thu 10-15 (PPI, EM 48) | 5-wide, short 7785C / 7640P, $1.00 | not stated | Filled 9:54 as SPX hit the 7670 put wall |
| Fri 10-16 (OPEX, EM 41.96) | 10-wide, short 7820C / 7685P, $1.50; later a 5-wide 7790C / 7710P at **$4.00** | Same 10-wide at $8.75 | Pending at the end of the stream (SPX ~7735–7750) |

**Wednesday 10-14, reconstructed from the fills on his order panel**, per 1 lot, before fees:

| Leg | Entry | Exit | P&L |
|---|---|---|---|
| Main condor 7720/7730P · 7835/7845C | $1.50 cr | | |
| Backup on the same strikes | $8.75 cr (2:00 pm) | both closed $6.45 db each | 10.25 − 12.90 = **−$265** |
| Backup 7715/7725P · 7840/7850C | $8.75 cr (3:00 pm) | $4.65 db | **+$410** |
| Defense: put BWB 7740 / 7725×2 / 7695 | $1.05 db | $5.00 cr | **+$395** |
| **Net** | | | **≈ +$540** |

SPX fell well outside the morning box that day. That's the "we survived Wednesday" he refers to on
Friday. **The backups and the defense made the day.** Each was a bet, entered near the lows, that
price would settle back near 7725–7730.

## The rules, as he states and shows them

1. **Strikes:** short strikes just outside the gap-adjusted box, and beyond the walls and the overnight
   high/low. "Respect the box"; never tighten for premium.
2. **Credit:** 5 wide needs $1.00 (25% on risk); 10 wide needs $1.50–2.00. If the market won't pay
   that at box-respecting strikes, he doesn't force it. The order sits until price comes to it.
3. **Backup:** the same strikes at width minus ~$1 ($4 on 5 wide, $8.75 on 10 wide). It fills only on
   a breach, and makes the pair roughly 1:1.
4. **Never two normal-credit DPPs at once:** one main risk at most.
5. **Defense:** once filled and threatened, buy a broken-wing butterfly near the short strike for
   about $1 or less. Its profit zone should cover the condor's max loss.
6. **Exit:** take ~80–85% of the credit, or hold to the close. Manage the day's net, not each leg.
7. **On top:** a morning iron fly or butterflies at gamma levels ("Double Ups"). These are also
   resting orders far from the market ("risk 1 to make 5"), mostly unfilled.

## Where the edge would live, and how to test it

**The claim to test:** when SPX trades out to the edge of the gap-adjusted expected-move box during
the session, it closes back inside often enough that selling there pays. That holds after the price
he gets (a resting limit fills at its limit or better), fees, and the occasional breach.

**What would disprove it:** after a touch of the box edge, the close lands outside the short strike
about as often as the option prices at that moment imply. In that case the resting order is just
selling fairly priced risk at a moment of high realised volatility.

**What we already hold:**
- the GEX recorder's daily `expected_move` and the spot trail (07-29 on);
- the 0DTE quote history (10-13 on), which prices each of his orders at the moment it would have
  filled;
- ThetaData 1-minute history for years.

**Smallest test, in order:**

1. **Reconstruct the four sessions we have quotes for (10-13 to 10-16).** Apply his rules (box,
   strikes, $1 / $4 or $1.50 / $8.75 limits, fill when the quote reaches the limit) to our recorded
   data. Check that we reproduce his fills and his 10-14 result. This validates the reconstruction,
   not the edge.
2. **Touch-and-revert statistics over years.** Using the gap-adjusted box from the open's straddle,
   count how often a session touches each edge, and how often it closes back inside after a touch,
   by time of day and by distance beyond. No option pricing needed. If reversion after a touch is
   not clearly above what an unconditional box-containment rate (~82–90%) already implies, stop here.
3. **Price the mechanical rule set over ThetaData history.** Price both the resting main order and
   the backup at their limits, with the defense excluded first and then included.

**Build only if:** step 2 shows reversion after touch beyond chance in most years, and step 3 is
positive after fees in most years. The defense butterflies and the Double Ups are judgment calls and
payoff shaping, so they stay out of any rule set until the core order shows an edge on its own.

## Gaps in this reconstruction

- Fill and exit details are confirmed from screenshots only for 10-14, 10-15 and part of 10-16.
  10-12 and 10-13 rest on the audio.
- His exact gap-adjustment arithmetic varies between sessions.
- His levels tool (HVL, walls, smart range) is a vendor product. Our GEX engine computes similar
  levels, but they are not identical.
- Five sessions in one week, including CPI, PPI and OPEX, is a small and unusual sample.
