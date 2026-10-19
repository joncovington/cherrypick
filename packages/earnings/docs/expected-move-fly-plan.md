# Expected-move butterfly: plan

A debit butterfly with its body one expected move from spot, on the call side above or the put side
below, opened before an earnings print. Status: **history study run 2026-10-13 (step 1): don't
build.** The fly is fairly priced at mid, costs take about a sixth of the debit, and no direction
signal comes close to the hit rate the costs demand. **Built anyway, paper only (2026-10-13), to
measure real fills against the study's cost model**: `strategies/expected_move_butterfly.py`, opening in
`strat_test` from 2026-10-14 (step 2 below, as built).

## The trade

The example that started this (SCHW, 3 DTE): spot 96.31, expected move ±3.51. Buy the 97 call, sell
two 100 calls, buy the 103 call, 0.51 debit at mid (0.38 bid / 0.65 ask). The body is spot + EM, the
wings are 3 wide (about 0.85 × EM). Max profit 2.49 at 100, breakevens 97.51 and 102.49, so it wins
on a rise of +1.2% to +6.4% and loses at most the debit, about 5:1. Four contracts to open costs
about $4–5 in fees on a $51 debit, so fee drag is close to 10% before any slippage.

At mid the fly is close to fairly priced by the market's own distribution; that is what a quoted
price is. So a fly on a coin-flip side has roughly zero expected value before costs and a negative
one after. It can earn only if:

1. **the body is better placed than the market expects** — `iron_fly`'s premise is that names it
   accepts usually move *less* than implied, in which case a body at the full EM sits beyond where
   moves land; or
2. **the side can be called better than the options market calls it.**

Both are answered from history before any strategy code exists.

## The direction inputs

- **Skew.** The literature finds a steep put smirk (25-delta puts rich to calls) predicts weaker
  returns, including over earnings announcements (Xing, Zhang & Zhao 2010; Cremers & Weinbaum 2010
  on the ATM call-put IV spread; Jin, Livnat & Zhang 2012 on announcement returns). Read that way, a
  flat or inverted smirk is bullish. But skew is also price: the steeper the put skew, the dearer
  the put fly, so following the signal means buying the side the market already charges more for.
  And `scanner.select_side` returns the *richer* side to *sell*: reused as-is for a debit fly it
  would invert the trade.
- **Fundamentals.** The consensus revision into the print (`eps_estimate`, weekly snapshots from
  2017) and the beat record (`eps_history`).
- **The symbol's own reactions.** `_compute_winrate_uncached` already pairs the pre and reaction
  closes for 8 quarters and keeps only `abs()`; the sign is free.
- **Technicals.** The short-term trend score, momentum (the RS rank's input), and the 5-day drift,
  from `cherrypick.technicals` on the history store. Price trend is a weak predictor of the
  overnight jump, which is mostly the surprise itself; it is tested, not assumed.

## Step 1 — the history study (`scripts/emfly_history.py`)

Every Dolt earnings event from 2020, priced on the end-of-day chains, both sides, for body
multiples k = 0.5 / 0.75 / 1.0 / 1.25 × EM, wings 0.85 × EM, with six closing rules on the same
entries. Each signal's direction and tercile cuts are fitted on 2020–2023 and scored on 2024 onward.
The script's docstring states the data, the cost model and every approximation.

```
python scripts/emfly_history.py --cache <chains.jsonl> --json <out.json>   # ~75 min cold
python scripts/emfly_history.py --from-json <out.json>                      # re-analyse, seconds
```

**Sample.** 45,149 events on symbols the chain clone lists, 2020-01 to 2026-10-02; **4,950 priced**
after the screens (price ≥ $10, 20-day dollar volume ≥ $100M, ATM straddle spread ≤ 15%, a listed
expiration within 17 days of entry). Train 2020–2023 n 2,828, test 2024 on n 2,122.

**What the clone forces, stated so the numbers are read right:**

- Dolt lists few expirations per day and rarely the front weekly, so most priced flies are 10–17
  DTE (only ~215 are 1–4 DTE). A longer fly is less concentrated on the event; the 1–4 DTE subset
  is reported separately and agrees.
- Dolt samples a sparse, shifting set of strikes, so legs sit on the stock's usual grid and an
  unlisted strike is priced from that day's smile (`premium_cycle_replay.Slice`). When the held
  expiration is not listed on a mark day, the nearest listed one's smile is borrowed with the held
  expiration's time left (1,522 of 4,807 reaction marks, 32%).
- **Cost check against a real quote:** the SCHW example's package (0.38 / 0.65 on a 0.51 mid) costs
  $3.40 slippage + $4.56 fees = 15.5% of its debit under the suite's model; the study's median entry
  cost is 17%.

### Where moves land

|move| / EM over 4,950 events: median 0.66, quartiles 0.31 and 1.18, 68% inside the expected move;
up 50.1% of the time. The market's EM is a fair central estimate — wide on both sides, but no body
multiple is placed meaningfully better than another (below).

### Price vs costs, held to expiry (per dollar of debit)

| k × EM | DTE | n | call pays | put pays | entry cost | net/risk call / put | right side | wrong side | break-even hit |
|---|---|---|---|---|---|---|---|---|---|
| 0.5 | all | 4,903 | 0.94 | 1.03 | 0.16 | −22% / −14% | 1.30 | 0.66 | 83% |
| 1.0 | all | 4,792 | 0.91 | 0.97 | 0.16 | −24% / −19% | 1.40 | 0.46 | 77% |
| 1.0 | 1–4 | 210 | 0.94 | 0.93 | 0.18 | −23% / −24% | 1.73 | 0.12 | 68% |
| 1.0 | 7–11 | 1,815 | 0.93 | 0.98 | 0.15 | −22% / −17% | 1.53 | 0.37 | 70% |
| 1.25 | all | 4,683 | 0.92 | 0.95 | 0.18 | −24% / −21% | 1.48 | 0.38 | 75% |

"Right side" is the fly on the side the move went (perfect foresight); "break-even hit" is how often
a side call must be right to cover costs and settlement. At mid the put fly pays back about what it
costs (0.93–1.03 per dollar) and the call fly a little less (0.90–0.94); the loss is the ~16–18% entry cost plus ~2% settlement.
**Even with no costs at all, a 54% hit rate on the 1–4 DTE fly pays back 0.99 of its debit.**

### Closing rules (both sides, half size each, net per dollar risked)

| k | react (mid) | react (far touch) | day 2 | expiry | tp 2× debit | tp ½ width |
|---|---|---|---|---|---|---|
| 0.5 test | −28% | −73% | −29% | −20% | −18% | −16% |
| 1.0 test | −30% | −73% | −30% | −19% | −20% | −18% |
| 1.25 test | −30% | −75% | −31% | −18% | −19% | −18% |

Closing on the reaction day pays a second set of costs on a fly that has not yet matured; the far
touch (what earnings paper does today) is ruinous on a 1-2-1 structure. Holding, or holding with a
take-profit, loses least — and still loses.

### Direction signals (terciles and sign fitted on 2020–2023; extreme thirds traded)

| signal | which end means up | test picks | test hit | hit by year 2020–2026 |
|---|---|---|---|---|
| `rr25` (25Δ call − put IV / ATM IV) | **put-rich → up, call-rich → down** | 1,558 | **53.3%** | 54, 54, 55, 53, 50, 57, 54 |
| `mom` (RS input) | weaker → up | 1,496 | 52.7% | 52, 56, 53, 51, 53, 52, 53 |
| `trend` (short-term score) | lower → up | 634 | 52.7% | 49, 53, 56, 53, 53, 54, 51 |
| `drift5` | down week → up | 1,399 | 51.8% | 54, 53, 56, 50, 51, 51, 53 |
| `revision` | cut → up | 1,136 | 52.1% | 48, 59, 51, 50, 49, 57, 51 |
| `atm_spread`, `beat`, `past_dir` | — | | 47–50% | no edge |

- **Skew carries a real, stable direction read, but backwards from the published studies:** a
  rich call wing predicts a *lower* reaction, a rich put wing a higher one, in 6 of 7 years. The
  price-based signals agree and are all contrarian: names that ran into the print tend to give some
  back.
- **None comes close to the 68–83% the fly needs.** Every directional arm loses 8–41% of risk in
  both halves.
- The skew result points one way that may matter elsewhere: `scanner.select_side` sells the
  *richer* wing, so `directional_credit_spread` and `broken_wing_butterfly` already lean the way this
  signal says (sell rich calls into a call-rich name that tends to fall). Worth reading their paper
  books by `rr25` tercile — a read, not a change.

### Verdict

Don't build the expected-move fly as an earnings strategy. The market prices it fairly, the costs
take ~18–20% of the debit, and the best direction signal is worth about 3% of it.

## Closing rules

A rule is not an arm. Following `calendars`, the paper module would run one entry stream with a
`path` arm that holds every fly to the expiry close and records its mark at every management tick,
and a read-side `exit_policies` replay that derives each rule's net from those marks, validated to
the cent against the arms it runs beside. Then adding a rule to the comparison costs nothing in
evidence time, and pairing is exact (every rule sees the same fills). The history study's six rules
are the first cut of that grid.

## Step 2 — the strategy (only if step 1 earns it)

**As built (2026-10-13):** the side comes from rr25 alone, inside `strategies/expected_move_butterfly.py`.
There is no `direction.py` and no technicals read. It opened on 2026-10-14, off the boundary, under its
own `strat_test_strategies` break. It exits on a take-profit at half the width or at the 15:30 expiry
close, and it is outside `OVERNIGHT`. The plan as written before the build follows.

- `strategies/expected_move_butterfly.py` (under 500 lines), from `broken_wing_butterfly.py`'s shape;
  a pure `direction.py` that scores the side from pre-fetched inputs — no AI in the package.
- Strikes: body the listed strike nearest spot ± k × EM; wings symmetric at a listed width, refused
  rather than snapped independently into an asymmetric fly.
- Technicals read through `cherrypick.technicals.store` (read-only), not the nightly chart JSON: the
  study and the live read must use the same history, and the JSON keeps 250 sessions.
- Registered in the eight hand-kept lists (registry, `_ORDER_FNS`, `_EVALUATORS`/`OVERNIGHT`,
  `sizing.per_contract_max_loss` with max loss = debit × 100, the three report lists,
  `_REJECT_REASON_MAP`, both configs including the deployed `strategy_defaults`), plus the docs
  tables.
- First fix `management.unrealized_pnl`'s second multiplication by quantity (`management.py:204`):
  a 1-2-1 fly is the first structure with a leg ratio above 1 on that path. **Not done as of
  2026-10-18.** `unrealized_pnl` still multiplies by `quantity`, but the harness stores
  `entry_credit` and every `legs_json` leg quantity already scaled by the position quantity
  (`strat_test_harness._scaled_legs`), so a mark on a position of more than one contract is
  overstated by that factor. The fly's own take-profit check is unaffected because it divides by
  quantity. It binds on no row so far, since the shipped `max_contracts_per_leg` is 1.
- Lands at the next declared measurement boundary, with a `measurement_breaks` row.
