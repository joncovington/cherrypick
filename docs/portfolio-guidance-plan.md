# Portfolio delta and theta guidance — plan

Agreed 2026-10-18. Adds a portfolio-level reading to `packages/manage`, beside its per-trade
evaluation: where the real account stands on beta-weighted delta and theta each morning, whether that
is inside the bands we set, what is driving it, what would bring it back, and which screener trades
would fit, sized to the account.

It stays advice. Nothing here places an order; every hedge and suggestion is something a person would
enter, priced at mid. The orchestrator takes every broker read, and manage stays a pure function of
the stored snapshot and the ruleset, so `reevaluate` over a past session gives the same answer twice.
None of it is measurement-affecting (manage runs no arm), so each piece lands when it is ready.

## Why manage the book this way

On 2026-10-16 the account held 37 legs on 13 underlyings against about $15k of net liq. Trade by
trade, each position can look roughly neutral while the book leans one way. Two numbers say how it
leans:

- **Beta-weighted delta (BWD)**, in SPY shares: Σ quantity × multiplier × delta × (S / S_SPY) × β.
  "+40" means a 1% SPY fall costs about 40 × 6.6 = $264. One XSP spread can then correct the whole
  book rather than reworking several trades, each paying its own spread.
- **Theta**, in dollars a day and as a share of net liq: how hard the book is selling premium.

Theta is paid for by gamma (θ ≈ −½ Γ S² σ²), so it is not edge; positive theta earns only if implied
volatility beats realised. The useful form is the **break-even move**, √(2θ / |Γ|): the daily move that
wipes out a day's theta, set against what the market expects to move. A theta *floor* would push
toward selling more gamma, so the theta band has a ceiling that matters and a floor that only informs.

What it cannot see: single-name gaps (earnings), which the per-trade rules still cover; and beta's
instability, which runs toward 1 in a selloff, so a low-beta name reads as hedged until it is not. A
book-level stress line (below) covers the second in part.

All the bands are retail heuristics with no published out-of-sample evidence. Their confidences say so.

## The account type decides buying power

Sizing follows the account type the snapshot reads from the broker's account record. It is never
assumed from one install's account. The current account is an IRA, but the suite is installed by
others too.

| Account | Short put ties up | Covered call ties up | Credit spread ties up |
|---|---|---|---|
| Cash / IRA | strike × 100 − premium | the shares' full cost − premium | max loss |
| Margin (Reg T) | the greater of 20% of the underlying − the OTM amount + premium, and 10% of the strike + premium, × 100 (15% for broad indexes) | 50% of the shares' cost − premium | max loss |
| Portfolio margin | not modelled: the broker's risk arrays cannot be reproduced from a snapshot | not modelled | max loss |

When the type is missing, sizing assumes cash-secured, the cautious reading. Portfolio margin sizes
by stress loss alone, and the page says buying power was not modelled. A margin requirement is a
modelled figure and says so, as manage's max loss already does.

On margin, buying power stops being a risk limit. A short put ties up a fifth of what it does in an
IRA, so the buying-power cap allows about five times the exposure. The **stress-loss cap** is what
holds size in check there, so it applies the same way on every account type. The per-name cap is on
**notional exposure** (strike × 100, or share cost), not on buying power, so leverage cannot quietly
raise it. Its default differs by type (below).

## Pieces

**1. Snapshot v3 (orchestrator, read-only).**
- Keep the broker's `beta` and `corr_spy_3month` from the market metrics `_metric` already fetches
  (and currently drops), and always request SPY. The broker's beta is the one its own BWD display
  uses, so our figure should match the screen.
- The account type (margin or cash, and the broker's name for it), through the shared
  `core.broker.cli.account_info`, so every module's `get_account_info` carries it.
- Anything unavailable is recorded, never fatal; older snapshots evaluate with these fields null,
  never zero.

The XSP chain slice (piece 3) and the candidate re-quotes (piece 4) join the snapshot with the piece
that reads them, each under its own version bump, so no field is recorded before anything uses it.

**2. Portfolio greeks and bands (manage).**
- Gamma, theta and vega in `pricing.py`, from each leg's own solved IV; shares count as delta 1.
- A `portfolio` section in the evaluation: BWD, theta ($/day and % of net liq), the break-even move
  against SPY's expected daily move, DTE-weighted vega (√(30/DTE)), buying power used, each
  underlying's share of BWD and of theta, and which beta was used. An unpriced leg makes the figures
  partial and names the leg.
- A stress line: SPY −10% (and +5%) repricing each position at its beta-scaled move, in dollars and %
  of net liq.
- Rules, each with a default block and a low confidence:
  - `portfolio_theta` — theta 0.1–0.3% of net liq a day; above the top is flagged as oversizing.
  - `portfolio_delta` — BWD within ±(theta ÷ 2) SPY shares, leaning slightly long; outside it, name
    the trades driving it, largest first.
  - `portfolio_breakeven` — the break-even move is smaller than the expected move.
  - `portfolio_concentration` — one underlying above 25% of theta or of BWD.
- An attribution check over the stored snapshots: each day's actual P&L change against BWD × SPY move
  + theta + ½Γ·move². If the greeks do not explain daily P&L after about 20 sessions, the page says
  the guidance is noise.

**3. Hedge sizing.** When BWD is outside its band, size an XSP vertical that brings it to the nearer
band edge, priced at mid. Show both kinds with their effect on BWD, theta, break-even and buying
power: a credit spread on the opposite side (cuts delta, adds theta) and a debit spread (cuts delta,
costs theta). XSP is European and cash-settled, so a hedge carries no early-assignment risk.

**4. Screener candidates, sized to the account.** Short puts, credit spreads, and covered calls on
shares the account holds. The candidate universe is our own options-tradable list (weeklies, $100M a
day), not the vendor's evening lists, which stop when the subscription ends on 2026-10-24; strikes
follow our own scored rules (short put 0.40Δ, covered call 0.165Δ, credit spread short at the first
OTM strike with the long at 0.25Δ). Filters: liquid by the screener leg bars, tradable, no earnings
inside the trade, the monthly at 30–60 DTE preferring 45–60.

Ranked by theta per dollar of buying power, inside the bands, never past them. Each candidate gets a
quantity: the smallest of five caps, every one scaled by net liq, with the binding cap named.

| Cap | Credit spread | Short put | Covered call |
|---|---|---|---|
| Per-trade risk | max loss ≤ 3% of net liq | stress loss ≤ 3% of net liq | stress loss on the shares less premium ≤ 3% |
| Notional per name | max loss | strike × 100 ≤ 25% of net liq (cash/IRA), 50% (margin) | share cost ≤ 25% (cash/IRA), 50% (margin) |
| Total buying power | used after the trade ≤ 50%, by the account type's rule above | same | same |
| Delta and theta room | BWD and theta stay in band | same | same |
| Concentration | the name stays ≤ 25% of theta | same | same |

Stress loss is the name's own 1st-percentile return over the holding period, from the local
end-of-day store, priced through Black-Scholes. A candidate that does not fit is shown at quantity 0
with the cap that stopped it and the nearest version that fits: a short put too large to secure is
offered as a put spread on the same short strike.

The worked figures below are the current IRA's. On a margin account of the same size, a $35-strike
short put would tie up about $700 of buying power rather than $3,500, so the stress cap, not buying
power, would set the quantity.

On 2026-10-16 (net liq $15,026, 44% of buying power used) the total cap left about $930: one small
credit spread. A $5-wide spread taking $1.50 ($350 max loss) fits at one lot under the 3% cap ($450).
A cash-secured put is limited to strikes of $37.50 by the per-name cap and to roughly $18 by the stress
cap. The caps scale: at $50k the spread cap is $1,500 and the per-name strike limit $125; at $150k,
$4,500 and $375.

**5. Console.** A portfolio card on Live → manage, reading the evaluation and computing nothing.

## Checks before it is trusted

- For the first few sessions, set our BWD and theta beside the broker's screen and record the gap
  (the broker uses its own model; American exercise and dividends will move it a little).
- Each guard shown to fail: a missing beta, an unpriced leg, a band pushed past its limit, a candidate
  with earnings inside its cycle, an IRA account offered an uncovered short, a margin account sized
  past its stress cap, and a missing account type sized as margin instead of cash-secured.
- The attribution reading at about 20 sessions.

All thresholds live in `manage.json` beside the existing rules; `config.example.json` and the README's
rule table are regenerated with each new block.
