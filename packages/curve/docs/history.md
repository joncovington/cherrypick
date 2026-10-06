# curve — history and calibration record

Moved out of `packages/curve/CLAUDE.md`, which keeps the resulting rules and numbers.

## The re-scale to VXX (2026-08-27, zero trades on the books)

The module had refused entry on every attempt since it began evaluating — 62 `spread_too_wide`,
31 `no_hook_signal`, no position ever opened — for reasons measured against the live 50-DTE chain:

- **`spread_width` 5.0 → 1.0.** At the ~30-delta short (worth ~$0.88 on VXX near $18) a 5-wide
  spread ceilings at 17.6% against a 15% floor, leaving under $0.13 for a wing that actually cost
  $0.435 — it landed at 8.9%. Across every strike, 5-wide cleared 15% only at delta 0.46+, an
  essentially at-the-money short and a different strategy. $5 on an $18 underlying is 27.6% of spot;
  MEIC's 5-point SPX wings are 0.06% of spot — the 5.0 was an index-scale number never rescaled. At
  1-wide the same short pays 17.0% of width; max loss per spread $500 → $100, and VXX's monthly
  chain has $1 increments.
- **`max_leg_spread_pct` 0.25 → 0.30.** The ~30-delta short itself quotes 0.27–0.28 wide on VXX, so
  0.25 refused the very leg the module sells.
- **Wing spread in money.** 56 of the 62 refusals were far-OTM bid-less wings, where a zero bid makes
  `spread_pct` exactly 2.0 whatever the option costs.

Both calibrated numbers come from one session's chain; the ceiling argument is structural.

## The fee-adjusted credit floor (2026-09-02)

The first trade cleared `min_credit_pct_of_width` at 0.18 on a 1.00-wide spread and then paid $2.24
open commission plus $4.25 open slippage against $18.00 gross — $11.51 before its first mark.
Slippage was 72% of that trade's modelled cost. That session opened and closed one spread in
`control` and `noflip`, which is also when the `/curve` console page's "wait for real positions"
condition was met.

## `quantity` 1 → 2 (2026-09-16)

At one contract the fixed round-trip cost (~$2.24 fee plus ~$2 slippage a side) consumed the whole
~$15–18 gross credit of a 1-wide spread, and the fee-adjusted floor refused every entry after the
single 09-02 fill. Width was measured on 2026-09-15 against the 2026-10-16 chain: credit-of-width
falls as width grows (13.0% at 1-wide, 11.75% at 2-wide, 9.3% at 3-wide), so width stayed at 1.

## The 2026-10-06 boundary: $2 wide, a net credit floor, the wing gate, `near`

Six weeks in, control had one trade: refused on 26 of 27 sessions, 587 times on spread width (60% of
those the long wing, quoting $0.11-0.30 against a $0.05 money test) and 198 on the credit floor (a
median 11.5% of width against 15%). Every session was contango, so the flip never fired and hook
never woke.

A replay on VXX's real closes (2018-01..2026-10) settled the shape. The contango gate is real (short
VXX when the prior ratio < 0.97: CAGR 7.7% against 0.4% ungated). The $1 spread made +8.8% of width
a trade before costs and -0.2% after them -- costs were half the credit. Per width, with 2026-10-05's
credits and costs, $1 lost 3.8% of max loss a trade and $2 made 2.5%, the best of $1/$2/$5/$10:
costs are near-fixed dollars, so a wider spread keeps more, up to where the credit stops keeping up.

So: width 2.0; the credit floor becomes net credit after entry costs over max loss >= 0.10 (a
percentage-of-width floor scores a wider spread as worse even when it keeps more per dollar risked);
`max_wing_spread_abs` 0.25; and `near`, a 0.40-delta arm, promoted from the advisor experiment that
had entered twice where control could not (killed at the boundary, base redefined). Capacity stays
the limit on size: about ten contracts bid at the short strikes.

