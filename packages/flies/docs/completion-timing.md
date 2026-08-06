# Completion timing — why we take the first qualifying tick, and what would change that

**Decision (2026-08-03): keep completing at the first tick that clears the gate, and start
recording what waiting would have captured.** Nothing on the decision path changed. What changed
is that the ledger now keeps pricing the completing spread *after* a completion, so the question
"should we have waited for a richer price?" gets answered from our own recorded sessions instead
of argued from theory. This document records why, and what evidence would justify revisiting.

## The question

Both leg-in modes complete a position at the first tick that clears a break-even-plus-buffer
gate: `legged` buys the completing debit spread the moment `D < C - fee_buffer`; `debit_first`
sells the completing credit spread the moment `C > D + fee_buffer`. Neither waits for a better
price. For `debit_first` especially the question writes itself: when spot runs the long vertical
into the money, the completing credit spread can be worth several times the debit paid — and the
engine still completes at barely-past-break-even, because that is the tick the gate first clears.

## What our own ledger said (measured 2026-08-03, 156 completions / 88 misses)

- **Completions punch through the gate; they don't scrape past it.** On SPX, the first
  qualifying tick cleared the gate by a median of 0.50 points (~$50/contract) — quotes on a
  5-point grid move in chunks. Tightening the gate by 0.05–0.10 points changes *nothing*: 98–100%
  of historical completions would fire at the same tick and the same price.
- **The miss branch has no cushion of near-misses.** Median shortfall for uncompleted positions
  was 0.31 (SPX) / 0.61 (XSP) points — the market mostly never came close. Only ~4 per symbol
  were within 0.10 of the gate. A gate tight enough to chase better prices converts completions
  into misses, and a converted miss costs ~$365 (SPX) against a chased gain of ≤$20 — a wait
  that must succeed ~95% of the time to break even.
- **Spot keeps drifting after completion (98% of cases), but reverses back inside wing distance
  in 75–93% of them.** Whatever a patient policy might capture, it has to *time an exit from the
  wait* through that reversal — a genuinely new mechanism, not a threshold tweak.

Verdict: no fixed tightening of the gate plausibly pays. First-qualifying-tick stays.

## What the theory says (and why the door stays open)

The finite-deadline optimal-stopping literature (du Toit & Peskir, "Selling a stock at the
ultimate maximum") answers this exact shape of problem: sell an appreciating asset before a hard
deadline, under drift plus reversal risk. The result is bang-bang in the drift: when drift is
*against* you, sell immediately — first-qualifying-tick is optimal, which is what our ledger
found. When drift is *favorable*, the optimal rule is a **trailing retracement from the running
maximum, tightening to zero as the deadline approaches** — not a fixed threshold.

For `debit_first` the favorable-drift regime has a candidate observable: dealer-gamma pinning.
A positive-gamma concentration at the centre strike is the regime where hedging flow pulls spot
toward the strikes — exactly the drift that richens the completing credit. The `gex` arm's
centring bet is this same hypothesis, and it is not yet validated; GEX here is inferred from
trade-signing, not observed. So GEX stays a *recorded regime classifier*, not a trigger, until
our own data shows the conditional difference is real.

## What is now recorded (the telemetry, added 2026-08-03)

The pre-existing `best_completing_*` trackers stop at the completion tick by construction — a
completed position leaves the completion loop — so they answer "did the market ever offer it"
for a *miss*, and nothing answered "how much better did it get after we took it" for a
*completion*. The stream cache is latest-value-only, so that number is recorded live or lost.

- `book.py` step 1d: after the completion passes, every completed (not yet settled) fly gets its
  completing spread re-priced from the same snapshot — running MIN of the completing debit for
  `legged`, running MAX of the completing credit for `debit_first` — into four new columns
  (`post_best_completing_debit`/`_at`, `post_best_completing_credit`/`_at`). The completion tick
  itself seeds the tracker, so "never improved" reads as zero, not as missing. Iron and bwb
  completions are excluded (different geometry). Pure telemetry: no gate reads these columns.
- `analytics.left_on_table`: the headline counterfactual — how many tracked completions saw a
  better price later, median/max improvement in points and dollars — **split by
  `completion_gex_bucket`**, because that split is the drift-regime hypothesis under test.
- The paper EOD report prints the same numbers per mode once tracked completions exist.

## The bar for changing behavior

A wait-for-better rule (trailing retracement, GEX-gated) gets built only if the recorded data
shows, at a sample comparable to the suite's usual promotion bar (≥14 sessions, ≥20 tracked
completions per mode), that:

1. the median post-completion improvement is materially larger than the ~$5–10 noise floor, and
2. the improvement concentrates in an identifiable regime (e.g. `completion_gex_bucket =
   pinning`) rather than being spread thin across all completions, and
3. the implied wait would have survived the reversal paths actually recorded — evaluated against
   the per-tick record, not the end-of-day best.

If the data shows first-tick was already the best available — that is the finding, and this
document is its record (rule 6). The measurement runs either way.
