# Which levels the vendor's chart draws

The vendor's data for a name carries every level it has computed: support, resistance, gap support
and gap resistance, often 10–20 in all. Its chart page draws only a few of them. Our chart used to
draw them all, which is why CSCO showed 18 lines where the vendor's chart showed four.

This is a **display rule**. It is not the unsolved question of how the vendor computes its levels
in the first place (`level_selection.py`, the package CLAUDE.md's "Unsolved" section).

## The rule

> The vendor's chart draws the **two nearest levels from its support list and the two nearest from
> its resistance list**, measured from the last close. It never draws a gap level.

When a list has fewer than two levels, it draws what there is (ARE and ORCL had one of each). A far
level is still drawn if it is one of the two nearest on its side: AMD showed a support 70% below
price, MRNA one 88% below.

The vendor's lists are already ordered nearest-first, so "the first two of each list" is the same
rule. Across all 247 captures held on 2026-10-03, the two never picked differently.

`chart.vendor_view` applies it (`VIEW_PER_SIDE = 2`, `VIEW_KINDS`), and every vendor level in the
chart file carries `vendor_view: true/false` (`chart_version` 4).

**Since 2026-10-04 this view is for comparison only.** The chart opens on our own levels
(`swings.py`, see [setups.md](setups.md)), and "Vendor's view" sits beside them for checking ours
against. Nothing in the setups or the watchlist reads the vendor's levels.

## How it was measured (2026-10-03)

- **The evidence.** The vendor's chart page prints a summary under the chart, "Support $106.60
  (−4.99%) $94.10 (−16.13%) … Resistance $113.60 (1.25%) $129.38 (15.31%)", listing the levels it
  draws.
  - The tie between that summary and the drawn lines was checked on one chart, CSCO: the four lines
    in the user's own screenshot are exactly the four summary levels.
  - A headless page does not draw the lines, so the summary is what was collected.
- **The panel.** 18 names were captured with the collector's own browser profile and sign-in, paced
  30–60 s apart (`probes/levels-20261003-210017/`: page text, screenshot and the data behind it).
  They were chosen so the candidate rules would disagree:
  - CDNS: its three nearest levels are all on one side.
  - ARE, ORCL: fewer than three plain levels.
  - AMD, AVGO, MGM, MSFT, MRNA, KEYS, NKE, AME, GDX, SNPS, VEEV, XLI: a gap level nearer than some
    plain level.
  - CSCO, SPY, QQQ.
- **The scores.**

| Rule | Names where it gives exactly the summary's levels |
|---|---|
| **two nearest per side, plain levels only** | **18 / 18** |
| first two of each list | 18 / 18 (the same rule, see above) |
| at most two per side, three in all | 16 / 18 (misses CSCO, VEEV, which show four) |
| three nearest, plain only | 15 / 18 (misses CDNS, which shows its lone far support) |
| three nearest per side | 8 / 18 |
| two nearest per side, gaps included | 2 / 18 |
| three nearest, gaps included | 2 / 18 |

The test `test_the_vendor_view_is_the_two_nearest_of_each_list_and_never_a_gap` pins two of these
names, CSCO and CDNS. It was shown to fail with three per side and with gap levels counted.

## What our chart can and cannot match

- **We draw the levels as of our last capture of them.** The vendor recomputes levels, sometimes
  between captures.
  - Of the 16 probed names we also chart, 13 matched our chart file exactly.
  - The other three (AMD, ARE, MSFT) had their levels recomputed between the nightly capture
    (2026-10-02, about 21:20) and the probe the next evening: MSFT gained a support at 514.54, and
    AMD and ARE each moved one level.
  - Applied to the probe's own data, the rule matched all three.
  - The chart's vendor card shows which capture its levels are from.
- **Each line starts at the level's date**, as the vendor draws it. A level set last week is a
  short line at the right, not a line across the whole chart.
- **Levels are left out of the price scale's auto-fit**, as the vendor's are, so a support 70%
  below price doesn't flatten the candles.
