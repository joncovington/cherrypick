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
