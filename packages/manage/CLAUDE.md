# cherrypick-manage — Operational Instructions

> Operating contract for the suite's daily **position-management evaluation**. Suite-wide context is
> in the root [documentation index](../../docs/README.md); the ruleset, its sources and the evidence
> behind each confidence are in this package's [README](README.md).

Every trading day this package answers **what should happen to each position in the real account,
by which rule, and how sure is the ruleset** — the question that was being answered by hand, a
position at a time, with the rules re-derived each time.

**It is advice, and read-only.** It evaluates a snapshot file and writes only into
`~/.cherrypick/data/manage`. It has no broker credentials and no network: the snapshot is taken by
the orchestrator's `manage` verb (`orchestrator/managesnap.py`, two read-only broker calls), which then
runs this package by subprocess. Nothing here places, closes, rolls or sizes an order, and no loop may
import it. The closing order an evaluation lists is what a person would enter, priced at mid.

## The artifact is the product

- `eval-<session>.json` — the evaluation, and the only thing any surface reads (the console's
  Live → manage tab). Nothing downstream re-derives a verdict, a level or a confidence.
- `snapshot-<session>.json` — the snapshot it was judged on, kept so `reevaluate` can re-run a past
  session under changed rules and get the same answer twice.

The evaluation embeds the **effective ruleset** it was judged by, so a page read later shows the rules
of that day, not today's.

## Rules the evaluation enforces

- **A trade is not an underlying.** `trade_grouping: expiry_chain` splits a name by expiry and folds an
  expiry into the next only while its shorts are uncovered inside it — a diagonal's short stays with
  its long, an unrelated weekly spread does not drag a monthly one into its exit. Share legs make the
  name one trade.
- **Credit or debit is decided by the net open cost**, never by the strategy's name.
- **`None` is not zero.** An unpriced leg makes the trade `REVIEW` with no rule judged; a field with
  no input is null.
- **Every modelled number says so.** Max loss, downside and upside are Black-Scholes at each leg's own
  IV at the nearest expiry. They bound what a trade can still do; they are never a price.
- **P&L is a mark, gross of fees** — the snapshot carries no fee record. Labelled that way everywhere.
- **The ruleset is data.** Thresholds and confidences live in `rules.DEFAULT_RULES` and the operator's
  `~/.cherrypick/config/manage.json`; an override key the default lacks is reported, never applied.
  `config.example.json` is the documented default and `tests/test_rules.py` fails if it drifts.

## Changing a rule

A new rule needs: a default block with `confidence`, an id in `RULE_IDS`, an action from the fixed
vocabulary (`CLOSE`, `TAKE_PROFIT`, `ROLL_OR_CLOSE`, `CLOSE_LEGS`, `REVIEW`, `SETTLES`), a test that
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
| `python run.py manage` (orchestrator) | Take the broker snapshot and evaluate it — what the 10:00 ET `manage-eval` job runs. |
| `python -m cherrypick.manage evaluate --snapshot PATH [--rules PATH]` | Evaluate one snapshot file and store both. |
| `python -m cherrypick.manage reevaluate [--session YYYY-MM-DD] [--rules PATH]` | Re-run a stored snapshot (latest by default) under the current or a trial ruleset. |
| `python -m cherrypick.manage rules [--rules PATH]` | Print the effective ruleset and any override that could not be applied. |
