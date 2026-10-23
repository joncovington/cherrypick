# cherrypick-manage — Operational Instructions

> Operating contract for the suite's daily **position-management evaluation**. Suite-wide context is
> in the root [documentation index](../../docs/README.md); the ruleset, its sources and the evidence
> behind each confidence are in this package's [README](README.md).

Every trading day this package answers **what should happen to each position in the real account,
by which rule, and how sure is the ruleset** — the question that was being answered by hand, a
position at a time, with the rules re-derived each time.

**It is advice, and read-only.** It evaluates a snapshot file and writes only into
`~/.cherrypick/data/manage`, plus its own stream request (`stream_request.py`, 2026-10-22): the newest
evaluation's held legs, their underlyings and the benchmark as quote-only `legs`, never `symbols`, so
the console can mark them live between runs. A re-run of a past session does not rewrite it. It has
no broker credentials and no network: the snapshot is taken by
the orchestrator's `manage` verb (`orchestrator/managesnap.py`: positions and balances, market metrics, trade history,
the XSP hedge chain, the quoted chain of each expiry holding a short strangle, the next quoted
expiries after each covered call, PMCC short and short put (`roll_out_chains`, v14, capped; `pmcc.py`
and `wheel.py` price their rolls there first and fall back to the income reading), the broker's greeks
for each option leg held and the day's orders, all read-only; and from local files the income reading
and the held names' skate bands, copied as the band file states them, which `wheel.py` moves to
today's price, and each long call's entry-day close with the name's dividends and splits from the
technicals history store, which `pmcc.py` solves the long's delta at entry from), which then runs
this package by subprocess. Nothing here places, closes, rolls or sizes an order, and no loop may
import it. The closing order an evaluation lists is what a person would enter, priced at mid.

## The artifact is the product

- `eval-<session>.json` — the evaluation, and the only thing any surface reads (the console's
  Live → positions and Live → portfolio tabs). Nothing downstream re-derives a verdict, a level or a confidence.
- `snapshot-<session>.json` — the snapshot it was judged on, kept so `reevaluate` can re-run a past
  session under changed rules and get the same answer twice.

The evaluation embeds the **effective ruleset** it was judged by, so a page read later shows the rules
of that day, not today's.

## Rules the evaluation enforces

- **A trade is its order chain.** `trade_grouping: order_chain` (`chains.py`) rebuilds the broker's
  chain from trade history — opening order and every roll — and takes its net cash as the basis; the
  positions endpoint's per-leg average price drops a roll's credit. A chain counts only when replaying
  its orders reproduces today's quantities; otherwise the trade falls back to `expiry_chain` (split by
  expiry, a diagonal's short kept with its long) priced from the open legs, labelled `open_legs`.
  Two joins: a short call alone in its chain and the long call the PMCC view links it to, when that
  link reconciles, are one trade (`grouped_by: "pmcc link"`), its basis every linked order; past the
  link's 60 days, a lone short call and the one lone long call covering it (later or equal expiry,
  lower or equal strike, its quantity), both reconciled chains and no shares on the name, are one
  trade too (`grouped_by: "covered call"`), its basis the two chains.
- **Credit or debit is decided by the net open cost**, never by the strategy's name.
- **`None` is not zero.** An unpriced leg makes the trade `REVIEW` with no rule judged; a field with
  no input is null.
- **Every modelled number says so.** Max loss, downside and upside are Black-Scholes at each leg's own
  IV at the nearest expiry. They bound what a trade can still do; they are never a price.
- **A wheel cycle is linked, never guessed** (`wheel.py`). An assignment counts only when an option
  row and a share row match (side, contracts x 100, at the strike, within days, each share row once);
  a running cycle that does not replay to today's positions is `unreconciled`; shares older than the
  history have `basis_known: false`, never the positions' open price.
- **A PMCC is recognised, never assumed** (`pmcc.py`). The shape is a call diagonal; it is a PMCC only
  when the long's delta at entry (its fill and that day's close, dividend yield carried) clears the
  floor, or `positions.<SYMBOL>.intent` says `"pmcc"`. A delta that cannot be solved is unknown and
  recognises nothing; a PMCC whose linked orders do not replay to today's legs gets a review only.
- **P&L is a mark.** Gross always; net of the chain's recorded fees where a chain reconciled, and null (never gross relabelled) where it did not.
- **The ruleset is data.** Thresholds and confidences live in `rules.DEFAULT_RULES` and the operator's
  `~/.cherrypick/config/manage.json`; an override key the default lacks is reported, never applied.
  `config.example.json` is the documented default and `tests/test_rules.py` fails if it drifts.

## Changing a rule

A new rule needs: a default block with `confidence`, an id in `RULE_IDS`, an action from the fixed
vocabulary (`ROLL_FOR_CREDIT`, `CLOSE`, `TAKE_PROFIT`, `ROLL_OR_CLOSE`, `CLOSE_LEGS`, `REVIEW`,
`SETTLES`, `HOLD`), a test that
shows it firing and not firing, the regenerated example, and a README row with its source and the
outside evidence. Its confidence is a statement about evidence — say where it came from.

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> Suite-wide guardrails apply — see root CLAUDE.md.
> - **Advice only.** No order path, no credentials, no network; the orchestrator owns the broker read.
> - **Deterministic.** A pure function of snapshot + rules; no AI anywhere in the verdict.

## Tool Reference

| Command | Purpose |
|---|---|
| `python run.py manage` (orchestrator) | Take the broker snapshot and evaluate it — what the 10:00 ET `manage-eval` job (and each `manage.also_at` run) runs. |
| `python -m cherrypick.manage evaluate --snapshot PATH [--rules PATH]` | Evaluate one snapshot file and store both. |
| `python -m cherrypick.manage reevaluate [--session YYYY-MM-DD] [--rules PATH]` | Re-run a stored snapshot (latest by default) under the current or a trial ruleset. |
| `python -m cherrypick.manage check-roll --trade ID --close LEG ... --open LEG ... [--session YYYY-MM-DD] [--rules PATH]` | Judge a proposed roll of one trade against the latest snapshot before sending it: credit, shape change, shorts moving into the long's expiry, net-short coverage (shares held on the name cover its calls), breakeven, max loss and earnings. Verdict reject / risky / ok. |
| `python -m cherrypick.manage rules [--rules PATH]` | Print the effective ruleset and any override that could not be applied. |
