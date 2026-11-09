**What this covers:** how MEIC's paper arms are designed and read: the breakeven identity every arm is
measured against, the independent-sampling model, the derived stop policies, the per-arm keys, and how to
read results without fooling yourself. See [paper-trading.md](paper-trading.md) for the paper engine
itself and [risk-profiles.md](risk-profiles.md) for the design rules every arm obeys. Part of the
[MEIC module](../README.md) in the cherrypick suite.

The arm registry is each machine's own `~/.cherrypick/config/meic.risk.json` (or the file
`$MEIC_RISK_CONFIG` names). The package ships only `config.risk.example.json`, which holds `control`; MEIC
falls back to it when a machine has no registry. `packages/advisor` designs and runs the experiments;
this module's arms are the substrate they run on.

## The breakeven identity

MEIC sits just below its own breakeven. Under the per-side buy-back design an IC has three outcomes:
both sides expire (+credit), one side stops (a scratch, about −fees), both stop (about −credit). Expected
value is `credit × [P(clean) − P(double)] − fees`, so the strategy pays exactly when

```
P(both expire clean)  −  P(both stop)   >   fees / credit
```

`analytics.breakeven_scorecard` computes this per arm and per period (`margin_pct` is clean minus double
minus the fee bar; negative means structurally below breakeven, not merely unlucky). Expiry outcomes are
split into `expired_otm` / `expired_itm` / `expired_unknown` (`analytics.expired_detail`), because ITM
cash settlements hide inside a nominal "expired" bucket and a "clean" win that settles ITM pays far less
than its credit.

**The per-side stop keyed to the whole IC's credit is the published MEIC convention**, not a bug: it is
designed so a single-side stop scratches the trade (`docs/strategy-sources.md`). `stop_trigger_ratio:
0.95` is the documented "MEIC+" variant (stop $0.10 below breakeven). Its cost is real and unpriced by
the convention: most stopped sides with a recorded settlement counterfactual settled OTM, so the stop paid
to close a side that would have expired worthless. The derived policies below measure that gap.

## Independent sampling

Every paper arm is an uncapped sample stream, not a capped book: `max_concurrent_ics: 99`,
`daily_ic_trade_target: 200` (a never-binding backstop), no entry spacing, `overlap_scope: "shorts"` (only
an exact short-pair repeat is refused). The short pair is the profit zone, as a butterfly's centre is, and
a second structure on the same zone doubles the bet without adding a zone. Pacing is the market's, not a
clock's: entries are limited by how many distinct short pairs the path offers during the window.

- **`stagger_entries` stays on even with zero spacing.** It keeps `daily_ic_trade_target` a hard cap and
  skips the over-target credit-floor tightening. Without it, later entries face a stricter floor than
  earlier ones and every comparison silently measures floor drift.
- **Paper models no buying power**, so an uncapped stream's P&L is a sum of independent samples, **not a
  book's P&L**, and must not be read as one. Rows before 2026-08-01 have book semantics and are not
  comparable across that date.
- **More samples do not proportionally raise the effective N.** Same-day trades share a regime, so
  sessions remain the unit of independence.

## The sampling arm and what is read from it

`control` is the permissive sampling substrate: study gates off, no per-side stop, `overlap_scope:
"none"`, full per-side path recording (running max cost, the settle value on every exit path, the first
strike-touch time). Everything else is a read-side split of its rows rather than another arm:

- **Entry gates.** With `overlap_scope: "none"` every gate variant is a pure entry filter and a
  deterministic function of a float `control` already records (IV rank, ATR over spot, GEX bucket and
  value, skew, centre offset, trend; `regime.py`). Answer them with
  `analytics.by_regime(conn, dimension, arm="control")` instead of running a dedicated arm.
- **Exits.** A fully marked, un-stopped path contains every stop policy's outcome. `stop_policies.derive()`
  prices four policies at 1x position cost with exact pairing (same entries, strikes, credit, session):

| policy | rule | why it is interesting |
|---|---|---|
| `stop-none` | hold to settlement | the flagship candidate; the tail risk is real, so its scorecard carries a held-through-ITM count and a worst-loss line, not just an average |
| `stop-0.75-net` | cost >= 0.75 x net_credit | a clean tightening of the deployed basis, no censoring |
| `stop-2.0-side` | cost >= 2.0 x that side's own credit | the "gave back its own premium" rule; 1.0x side credit does not mean that (the side would exit at its entry price having given back nothing) |
| `strike-touch` | stop only on an actual spot breach of the short strike | most stops fire on sides that were never breached |

This is valid only because paper has no market impact and positions are independent, and it is validated,
not assumed: `analytics.validate_stop_derivation` re-derives every arm that really stopped, at the ratio
each row ran, and must reproduce its realised P&L to within fill noise. **If that validation fails, every
derived stop-policy number is void until it is fixed.** What the derivation prices and charges is in
[reference.md](reference.md) ("Deriving stop policies read-side").

Wing width is the one structural variant you cannot re-derive after the fact (it is not a float), so it
stays expressible through the advisor's `wing_width_points` bound or an arm that pins it.

## Per-arm keys

An arm is a partial overlay merged onto the base config. Beyond the base keys it can carry:

| Key | Meaning |
|---|---|
| `symbols` | The subset of the base `symbols` this arm trades. Pinning a symbol into an arm's identity fuses the arm axis with the symbol axis and makes "the same arm on another instrument" inexpressible, so study arms leave it unset and let the (arm x symbol) grain supply the instrument axis. |
| `wing_widths_by_symbol` | This arm's own wing shortlist per symbol. `paper_loop` builds each symbol's candidate menu from the union of every arm's widths; each arm picks from its own subset. |
| `wing_selection` | How to pick among clearing candidates: `widest` (default, fee-drag bias), `narrowest`, or `fixed` (the shortlist's own order). |
| `stagger_entries` | Opt-in. Enforces the entry window, a hard daily cap and spacing (`min_minutes_between_entries`). |
| `follows_live` | A follower resolves to the live parameters plus its one override (see [reference.md](reference.md)). |

QQQ and IWM are physically settled, so they exercise the engine's early force-close with modelled
friction and the pin-risk penalty (see [paper-trading.md](paper-trading.md)); a force-closed trade that
paid the penalty is flagged (`ic_trades.pin_risk_applied`) so those rows can be read gross and net of that
modelled cost.

**The kill rule.** An arm is retired only by an explicit written verdict, never by silent deletion: the
registry keeps its exact key set with `enabled: false` and a `_disabled_note`. A defined-but-forgotten arm
is worse than a documented-and-off one. An arm that enters zero trades for five consecutive sessions is
escalated, not quietly dropped.

## Reading the results

- **Session count before trade count.** Rows are not independent draws: this book takes hundreds of
  entries a session, so a dimension can carry a four-figure trade count and rest on two days.
  `analytics.regime_coverage` reports `sessions`, `daily_scale`, `effective_n` and `underpowered` beside
  the row counts, and `by_regime` reports sessions per bucket. `underpowered` is keyed on sessions, not
  `effective_n`, and is separate from `degenerate`: a degenerate dimension says re-cut the stored float; an
  underpowered one says collect more sessions, and re-cutting it instead fits a boundary to a handful of
  days.
- **Sessions are the sample.** `experiment.py` reports `sessions` beside every trade count and refuses a
  bootstrap interval below 14 sessions (`core.metrics.MIN_EFFECTIVE_N`, the suite-wide bar). The bootstrap
  resamples whole sessions, because with fewer than about 30 clusters per-trade inference is optimistic.
  Read at 14, decide at 20 or more.
- **Zero-entry sessions count as zero, not as missing.** An entry filter that blocked a whole session is
  informative. Gate splits use per-session means; stop and width policies use paired within-session
  contrasts (`analytics.session_bootstrap`, `core.stats.paired_diffs` with `fill=0.0`).
- **Bucket on whether the reference fired.** A stricter-gated arm can go dark on a session its looser
  siblings trade. Use `analytics.control_fired`, and never drop the dark sessions.
- **The refused ticks are recorded too.** Every regime column on `ic_trades` is conditioned on having
  entered, so on its own the ledger only answers "which regime does this arm win in" over ticks that
  already cleared every gate. `iteration_regime` (one row per iteration and symbol, see the CLAUDE.md
  Database section) and `entry_attempts` supply the denominator and the binding gate.
- **Measurement boundaries.** Rows carry an `era` column and `analytics.py` defaults to `CURRENT_ERA`, so
  eras are never silently pooled. The declared boundaries are listed in [reference.md](reference.md)
  ("Declared boundaries").

## Validation against history

Arms are validated forward by the unattended paper engine against live quotes. Recorded sessions replay
through `paper.process_symbol` with `python -m cherrypick.meic.replay` (the gex recorder's 15 s quote
history from 2026-10-13, or the local vendor stores of `packages/backtest`), checked against paper with
`replay check`; results derived from licensed vendor history stay under `~/.cherrypick`. The SPX
historical-replay tool `paper_replay.py` is disabled; see [0dtespx-api.md](0dtespx-api.md).
