# Iron completion — retired 2026-08-03, before it ever traded

**Decision: the `iron` arm is disabled and the iron completion path stays unreachable in config.
Keep this negative result (rule 6).** The arm was built to isolate the *completion choice* — complete
a legged credit spread by buying the same-type debit spread, or by selling the opposite-type credit
spread into an iron butterfly. It cannot isolate anything, because on European cash-settled
SPX/XSP the two completions are **the same trade**, pinned together by put-call parity. Where they
differ it is fees and execution, and both go against the iron.

## The hypothesis, and why it is void

Held: a short put spread at centre `K`, wings `K` / `K−W`, collected for `credit1`.

- **Debit completion** buys the `K`/`K+W` bear put spread: `D = P(K+W) − P(K)`
- **Iron completion** sells the `K`/`K+W` call spread: `C2 = C(K) − C(K+W)`

Both use the identical strike pair. Put-call parity at a single strike gives `C(X) − P(X) = F − X`,
so

```
D + C2 = (C(K) − P(K)) − (C(K+W) − P(K+W)) = (F − K) − (F − K − W) = W
```

Every implied-vol term cancels. `D + C2 = W` holds for **any** smile, any skew, any forward — skew
is a property of IV *across* strikes, and parity is an arbitrage *at* a strike, so no amount of skew
opens a gap. The mirror case (held call spread, completed by selling the put spread) is the same
algebra reflected.

The consequence is that the two completions are the same position in different clothing:

```
iron net − W  =  (credit1 + C2) − W  =  credit1 + (W − D) − W  =  credit1 − D  =  fly net
```

The iron's larger credit is not extra money. It buys exactly `W` of extra liability, at every
settlement price. There is no surplus out of which the iron's extra costs could be paid.

Therefore: the price gates are algebraically identical. `engine.evaluate_completion`'s
`D < credit1 − fee_buffer` and `engine.evaluate_iron_completion`'s `credit1 + C2 > W + fee_buffer`
are the same inequality. Both fire on the same tick or neither does. There is no drift regime, no
volatility regime, and no skew regime the iron monetizes that the debit does not.

## Verified twice — synthetically and on a real chain

A parity-consistent Black-Scholes sweep through both gate functions (`engine.evaluate_completion`
vs `engine.evaluate_iron_completion`, held 6400/6395 put spread, `W = 5`):

```
    spot   debit D    ironC2    D+C2-W   floor_debit   floor_iron   gates
    6395    3.3262    1.6738    0.0000       -134.50      -129.50   both refuse
    6405    2.2551    2.7449    0.0000        -27.40       -22.40   both refuse
    6410    1.7299    3.2701    0.0000         25.13        30.13   both pass
    6420    0.8710    4.1290    0.0000        111.01       116.01   both pass
```

And on a live SPX Aug-4 1DTE chain, 18 strikes spanning 7560–7645, the implied forward
`F = K + C_mid − P_mid` came out **7617.69 with 0.25 points of scatter** — parity holding to within
a tick across the whole surface. At the 7600 centre:

```
  MID   debit D = 1.30   iron C2 = 3.70   D + C2 = 5.00   (W = 5.00)
```

That chain also disposes of the intuition that motivated the arm — "the ITM side's credit is so
rich the iron must come out risk-free." It looked rich because the **spot print was stale**: the
screen showed SPX Last 7600.50 / Chg 0.00% while the options priced a forward of 7617.69 (a +17
point one-day basis would be ~85%/yr — impossible). The 7600 call at 25.30 was not skew; the strike
was 17.7 points in the money. Same failure mode as the overnight-SPX issue: options quote fine
while the index does not update.

## Where they actually differ — both against the iron

**Assignment fees.** An iron butterfly always has one side in the money; a same-type fly can settle
entirely out of the money. This book's completions arrive *after* spot has walked away from the
short strike (119 of 143 completions drifted favorably), which is exactly where the same-type fly
settles clean and the iron does not. Measured over all 143 completed legged flies in the ledger,
re-scored under both geometries at their real settlement prices:

| | mean ITM strikes | total assignment fees |
|---|---|---|
| same-type fly | 1.10 | **$785** |
| iron geometry | 1.79 | **$1,280** |

**+$3.46 per position, $495 across the book**, for an identical payoff.

**Execution.** Parity holds at mid, not at the prices you trade. On the real chain above, the debit
route crosses 0.20 (1.30 → 1.50) and the iron route crosses 0.40 (3.70 → 3.30) — the ATM calls
carry the wider absolute markets because they carry the deeper premium. **$20/contract worse**,
which combined with the fee gap makes the iron ~$30/contract worse on that tick.

This module cannot see that difference: `slippage_frac` (0.125) is applied flat to both sides, so
paper would have scored the two routes equal while live paid the spread. An arm whose only real
variable is invisible to the experiment measuring it cannot produce a finding.

## The dispatch bug this exposed, which must be fixed before any revival

`book.py` step 1 chose between the two by post-fee floor:

```python
take_iron = iron_done and (not debit_done or iron_plan["floor"] > debit_plan["floor"])
```

In the sweep above `floor_iron` is **exactly $5.00 above `floor_debit` at every price**. That is not
edge — it is `fly.WORST_CASE_ITM_LEGS`: `fly: 3` strikes ($15 reserved) against `iron_fly: 2` ($10).
Each reserve is individually correct, but they are evaluated at *different* worst-case settlement
prices, so the floor is not a valid tiebreaker **across kinds**. Had the arm run, it would have
converted ~100% of completions to iron on a $5 accounting artifact while realizing $3.46/position
worse. Any future comparison of two structures must score them at a common settlement price, not
each at its own worst case.

## What was actually retired

- `config.example.json`: the `iron` arm is `"enabled": false`. It was never present in the deployed
  `~/.cherrypick/config/flies.json`, so it produced **zero** ledger rows — there is no attribution
  to preserve and no data lost.
- `completion_modes` remains `["debit"]` everywhere. The iron path is unreachable without a
  deliberate config edit.
- The code (`fly.iron_fly_payoff`, `position_floor`'s `iron_fly` branch,
  `engine.evaluate_iron_completion`, `book.py`'s dispatch, the `iron_fly` kind and its schema
  columns) is **kept**, unchanged and still tested. It is correct code for a structure the module
  simply has no reason to prefer, and deleting it would delete the ability to re-derive this
  result.

## What would reopen it

Nothing about payoff — that argument is closed by static arbitrage and does not depend on data.
Only microstructure could, and only with evidence this experiment is not currently built to
produce:

1. a slippage model that prices each leg's **actual** bid-ask rather than a flat fraction, and
2. measured fills showing the opposite-side spread is reliably enough tighter to beat the ~$3.46
   assignment-fee gap.

Until both exist, iron completion is a strictly more expensive way to buy the same payoff.

There is one narrow use that does **not** require the above and is worth remembering: as a
**fallback** when the same-type completion returns `missing_leg_quotes` while the opposite side is
quotable. That is availability, not edge, and it would be gated on realized-fee neutrality rather
than on the floor comparison — a different feature from the arm retired here.
