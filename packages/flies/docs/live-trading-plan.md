# Flies live trading — plan and gates

**Status: not implemented, and deliberately conditional.** Flies is a paper module built to
make a negative result usable, and as of the last settled window the result *is* negative:
over 07-20…07-24, 40 legged entries completed 23 (57.5%) against a ≈65% break-even rate, the
book lost $1,175, and a miss cost ~1.9× what a completion earned. Honesty rule 6 applies —
if the floor comes out negative after fees, the answer is to stop, not to go live harder.
This document therefore defines **what a "yes" would have to look like and exactly how live
would then be built**, so that if the paper verdict turns, the path is already designed and
none of it gets improvised under enthusiasm.

Everything here inherits the suite guardrails: paper↔live isolation, no AI/MCP/network on
the decision path, credentials in the OS keyring, account numbers masked, defined-risk only.

---

## Gate 0 — the paper verdict (nothing below happens until this passes)

All four, measured by the existing analytics layer, over a window that starts **after** the
2026-07-27 config changes (min_floor_dollars 10, legged-only, wide_wing arm added), because
data from before those changes measures a different strategy:

1. **Completion rate ≥ the live break-even rate, sustained.** Break-even is recomputed from
   the window's own miss-cost/completion-earn ratio (the frozen "≈65%" is the 5-session
   number, not a constant). Minimum sample: ≥20 sessions and ≥100 legged entries.
2. **Book net positive after fees over the window**, and the winning arm's reading passes
   the hardened promotion thresholds the suite already uses (≥14 days, ≥20 samples,
   significance guard, survives 0.25 slippage) — the same bar every other promotion clears.
3. **A winning arm separable from control.** Arm divergence must show the winner actually
   made different choices (gex vs control is the comparison with power; the ATM arms agree
   on centre by construction), and the per-arm delta must favor it beyond noise.
4. **The wide_wing hypothesis resolved** — either wider wings bracket the observed drift
   (completions land inside wings, the book earns more than its floor) or they don't, in
   which case the drift is fundamental to the mechanism and rule 6 likely ends the project.

Gate 0 is evaluated by a human reading `strategy` reports, not by any automated trigger.

## The two documented blockers, resolved on paper (in CLAUDE.md "If this ever goes live")

**Legging is where live diverges hardest from paper.** Paper's completion gate is a clean
inequality; live, step 1 fills and step 2 is a working limit that may sit unfilled or fill
worse. Paper completion is therefore an **upper bound** on live completion — so the pilot's
first job is to measure the live rate directly, at 1-lot, before anything scales. Live
mechanics: step 1 is a 2-leg credit-spread limit (sell to open); step 2 is the completing
2-leg debit vertical as a working Day limit at `D ≤ C − fee_buffer`, subject to the same
post-fee floor gate, repriced only within that bound and canceled at a configured cutoff.
A never-filled completion leaves an ordinary defined-risk short vertical held to cash
settlement — the exact branch paper already accounts for. **Abort rule:** once ≥30 live
legged entries exist, if live completion runs more than 15 percentage points below the
contemporaneous paper rate, halt the pilot; the strategy's edge *is* the completion rate,
and that gap means paper's upper bound is not achievable.

**`fund_from_open_credit` needs a real buying-power check.** Mooted for v1: `entry_modes`
has been legged-only since 2026-07-27 (outright lost 4 of 4 and confounded the gex arm),
and live v1 **forbids outright entirely**. If outright ever returns, it returns paper-first,
and its live funding must clear `core.broker`'s preflight and deploy governor like any other
order. Out of scope here.

## Architecture — the MEIC phase-5 shape, reused

The engine already returns decisions rather than performing fills (the same split MEIC
uses), so live is an *applier*, not a rewrite:

- **`src/live_loop.py`** as `paper_loop.py`'s sibling: same provider snapshot (read-only
  from the shared stream cache — the streamer stays the single producer), same pure
  `engine.py`/`fly.py` decisions, but entries and completions submitted through
  `cherrypick.core.broker` (`build_order` + `place_order` with the deploy governor). The
  paper loop **keeps running in parallel** as the control for live-vs-paper divergence.
- **`src/broker_cli.py`** — flies has no `tt.py`; a minimal broker CLI on `core.broker` +
  `core.auth` with its own keyring service (`fliesagent`), onboarded via the orchestrator's
  existing `connect`/`account` flow. Order placement, working-order status, cancel. Nothing
  else — quotes keep coming from the stream cache.
- **One arm goes live: the Gate-0 winner.** The four-arm design is an experiment harness,
  not a live posture. The live config pins that arm's parameters; changing them mid-pilot
  invalidates the measurement.
- **A separate live ledger** (`live_trades.db`, the `fly_positions` schema plus broker
  order-id columns), declared to the orchestrator via its `live_db` key so it appears in
  `report --live` and never in a promotion reading. The paper DB is untouched.
- **Kill switches, all of them:** `enable_live_trading` (config, default false, checked
  every tick) + the suite halt flag (`state/halt-live.flag` — presence halts new entries,
  polled per tick; already surfaced on the hub's Live Ops card) + a daily-loss breaker
  (day's live net ≤ −limit ⇒ no new entries; open structures follow their normal
  hold-to-settlement rules — they are defined-risk, and improvising exits is rule 5's
  territory) + every entry through `core.risk.evaluate_deploy_limit`, fail-closed.
- **Settlement discipline:** live book results are recorded against the official settlement
  print (the `--price` path), never the last streamed trade. Daily scheduled `reconcile`
  (already built) is mandatory during the pilot; the designated flies account is expected
  to hold positions and is judged against the live ledger.
- **Watchdog:** live-loop freshness SLA during market hours, stdlib-only like everything
  else on the reliability path. The watchdog never places or cancels anything.

**Symbol and sizing decision — made by fee math, not preference.** Paper runs SPX; fees ate
82% of gross in the first session. XSP is 1/10 notional (smaller absolute risk per
structure) but the fee stack is roughly fixed per contract, so fee drag is proportionally
*worse* at XSP scale and may structurally sink the floor the strategy exists to earn. The
pilot symbol is chosen by recomputing the fee-adjusted floor for both under the winning
arm's parameters: if XSP floors positive after fees, pilot on XSP (smaller dollars at
risk); if only SPX does, pilot on SPX 1-lot and accept the larger per-structure risk as the
price of a real measurement. Both are European cash-settled — no assignment machinery
either way. Never both concurrently (the correlation guard doesn't exist).

## Rollout ladder — each rung graduates on data, human-confirmed

- **Rung 0 — broker smoke.** The MEIC pattern (`live_smoke.py`): build a real credit-spread
  and completing-vertical order pair from live quotes, preflight both through the dry-run
  endpoint against the designated account, verify buying-power effects and the governor
  verdict, place nothing. User-supervised.
- **Rung 1 — measurement pilot.** 1 structure per day maximum, one symbol, ≥15 sessions.
  Collect: live completion rate (the number), realized slippage per fill vs the modeled
  haircut, actual fees vs the modeled stack, completion latency live vs paper. The abort
  rule above is armed from entry one.
- **Rung 2 — normal cadence at 1-lot.** Only if rung 1's live completion clears the
  recomputed break-even. Config frozen; the hardened promotion rule evaluated on **live**
  readings via the live-tagged reader.
- **Scaling beyond 1-lot** is a separate human decision after rung 2 passes, and is out of
  scope for this plan.

## Measurement and isolation invariants

- Live P&L is visible only through `report --live`; `calibrate` and every promotion
  reading stay paper-only (enforced by construction and by test in the orchestrator).
- Every live fill records actual price vs quoted mid, so the suite's modeled slippage
  haircut finally gets replaced by measured values — closing the "unknown ≠ zero" caveat
  from the paper cost model.
- The paper loop's contemporaneous sessions are the control: live-vs-paper deltas in
  completion, latency, and floor are first-class report lines, not an afterthought.

## Explicit non-goals

No adjustments after establishment (rule 5). No outright or funded flies. No multi-arm
live. No SPX+XSP concurrently. No agent anywhere near the live decision path — the advise
pipeline may only ever touch the paper book, and nothing in phase 6's risk-reducing menu
applies to flies until this plan's rungs are complete.

## Work breakdown (when Gate 0 passes)

1. `broker_cli.py` + keyring onboarding + orchestrator `connect` wiring — **S/M**
2. Rung-0 smoke harness (clone the MEIC pattern for spread + completion orders) — **S**
3. Live ledger schema (+ order-id columns) and `live_db` declaration — **S**
4. `live_loop.py`: engine decisions → broker applier, kill switches, completion
   working-order management, settlement recording — **L**
5. Watchdog live SLA + trade notifications from the live ledger — **M**
6. Live-vs-paper comparison lines in the flies report/analytics — **M**

Until Gate 0 passes, the only work this plan calls for is the one it's already doing:
running the paper experiment honestly and letting the completion rate decide.
