# cherrypick-curve

**Retired; off by default.** To reinstate: set `modules.curve.enabled` to true in the suite config and
`enabled` to true on the arm or arms you want (`control`, `noflip`, `near`) in `curve.json`; `advice.enabled`
is separate and stays off. Every arm and `advice` are off in the example config.

This module's old word `book` is the root's `arm`; the column is still `curve_positions.book`.

VXX call credit spreads harvesting the VIX term-structure roll yield, gated by a daily VIX/VIX3M
regime read (posture: root file). Every arm trades the same shape — a short call (~30-delta; 0.40 in
`near`), long wing a declared width higher, same monthly-cycle expiration — and differs only in
entry gate, exit rule or short delta. Ledger schema: **`curve_vx`**.
Suite-wide context: the root [documentation index](../../docs/README.md).

## Measurement boundaries (never pool across these)

- **2026-10-06** (journaled in `measurement_breaks`): `spread_width` 1.0 -> **2.0**; the credit floor
  becomes **`min_net_credit_to_max_loss` 0.10**; `max_wing_spread_abs` 0.05 -> **0.25**; arm **`near`**
  added. Before it is the $1 structure (one control trade), never pooled with what follows.
- **2026-10-15**: arm `hook` retired (it never traded). Its block stays in the config with `enabled` false,
  never deleted: an absent built-in arm used to run by default.
- **2026-10-30**: an arm the config does not declare stays off, `control` excepted
  (`engine.DEFAULT_ENABLED`); the loop warns once, naming any such arm. A `near` row from an install that
  never declared the arm before this date is control's trade under another name, and should be read that
  way.

## The structure's calibration

- **`spread_width` 2.0.** A credit spread's credit cannot exceed the short's premium, so `credit / width`
  is capped at `short_mid / width`. Costs are near-fixed dollars, so a wider spread keeps more of its
  credit, up to where the credit stops keeping up; $2 was the best of $1/$2/$5/$10 in a VXX replay.
  `tests/test_structure_coherence.py` pins width against the shipped config's credit floor, so a pair
  that cannot coexist fails at declaration, not after a month of silent refusals (it has its own case
  shown to fail).
- **Credit floor `min_net_credit_to_max_loss` 0.10:** what the spread keeps after modelled entry costs,
  against its max loss. A percentage-of-width floor scores a wider spread as worse even when it keeps
  more per dollar risked. A config without the key falls back to the fee-adjusted floor:
  `engine.plan_entry` nets modelled entry commission AND slippage against the dollar floor and refuses
  `credit_below_fee_adjusted_floor`. The net floor refuses `net_credit_below_floor`. A percentage floor
  is a ratio of mids and cannot see opening cost. Only the entry side is netted, since the exit's cost
  depends on a close path not yet decided.
- **`max_leg_spread_pct` 0.30:** the short itself quotes 0.27–0.28 wide on VXX.
- **A wing's spread is money, not a ratio.** The long leg is refused only when wide in percent AND in
  dollars (`max_wing_spread_abs`): a zero bid makes `spread_pct` 2.0 whatever the option costs. The SHORT
  leg keeps the plain percentage test, since its premium is the whole credit and paying up there is what
  the gate prevents.
- **`quantity` 2:** two contracts amortise the fixed round-trip cost. Width is deliberately not widened
  with it. **Sizing is a human decision outside the advice bounds.**

## The experiment design

- **`control`** — enter on a contango day (`ratio < contango_max`, 0.97, a buffer below 1.0), close at
  `profit_take_pct` of entry credit, OR the **regime-flip hard exit** (a MEASURED ratio at or above
  `contango_max` mid-trade closes next tick regardless of P&L — never on an unmeasured or stale read),
  OR `close_dte`.
- **`noflip`** — control's entry EXACTLY (same tick, same fills), minus the flip exit. Until a flip
  fires the two are byte-identical — an expected `find_identical_readings` collision. Every read
  surface shows `flip_divergence_count` beside the pair: that, not trade count, is the effective
  sample. A season of contango with zero flips proves nothing about the flip rule.
- **`near`** — control's gate and exits, flip exit included, on a **0.40-delta** short. Planned from its
  own params, so it is NOT fill-paired with control; arms whose params resolve identically still share
  one plan (`paper_loop.plan_arms`), which is what keeps control and noflip byte-identical.
- **`hook`** (retired) — entered ONLY on the two-day-confirmed hook (`ratio > hook_threshold` AND below
  yesterday's), on its own tick; exited by control's rules. Nearly always idle by design.
- **`advised:<experiment name>`** (paper, off by default; mechanism in `packages/core/CLAUDE.md`). A twin
  is gated and planned as its base arm, and its `base_book` is what the flip exit keys on (a `noflip`
  twin never flips). Advice is bounded to `profit_take_pct`, `short_delta_target` and `contango_max`;
  `hook_threshold` and `close_dte` are **not advisable** (the hook's value is a fixed rare-event
  definition, and `close_dte` bounds the settlement path). The module is a structurally slow advisor
  target (~2-4 closes a month), so short experiments are `underpowered` against the promotion gate.

**Pairing is partial, deliberately**: `control`/`noflip` share one plan and tick; `near` is planned from
its own params. Never treat the arms as a fully paired grid.

## The regime series is the module's second product

The daily VIX/VIX3M ratio, its classification and the hook flag are written to `curve_regime`
**every session, traded or not** — the series' value is its continuity. **RTH-gated and basis-stamped**:
a recorder that freezes on the last value overnight double-weights the session's closing sign. A stale or
missing read writes an unusable row (`usable = 0` with the refusal), never a frozen ratio. This is NOT
`overview`'s binary contango gate.

## The honesty rules

1. **Net of the modelled fee and slippage stack.** Gross is not a result.
2. **Early assignment is unmodelled but MEASURED; the paper result is an UPPER BOUND.** VXX pays no
   dividend, but a spike still puts the short ITM. Marks with extrinsic under `assignment_exposure_tv` are
   flagged `assignment_exposed` (`curve_marks`, `analytics.exposure`).
3. **VXX reverse splits are unmodelled** (roughly biennial): a mid-position split is a manual `--settle`
   plus a journaled break; the OCC-root filter refuses adjusted roots like `VXX1`.
4. **ETN plumbing risk is declared, not modelled** (a creation halt can decouple shares from the index).
   Undetectable here; one more reason the result is a bound, not a forecast.
5. **A hole in the mark path is refused, never zero** (`curve_marks.usable = 0`) — a stalled feed and a
   quiet vol market must never look identical.
6. **Missing regime data blocks entry and can never force an exit.** No ratio -> no position
   (`regime_unmeasured`); an open position with no ratio holds its last verdict.
7. **The regime row is written every session**, traded or not.
8. **Short-delta selection degrades to a computed delta, never a moneyness guess.** Missing feed delta ->
   `engine.bs_call_delta` (Black-Scholes from spot, strike, DTE and the feed's own IV), and the row records
   `short_selected_by = "delta_computed"` so it stays excludable. Only a chain with neither refuses
   `no_delta_for_selection`.

**Settlement**: American physical delivery, the calendars/pmcc decomposition verbatim (intrinsic plus 100
shares per contract at the settlement spot, disposed next session). `close_dte = 7` makes expiry the
exception path, but it is built because `noflip` can ride deep into backwardation.

## Data source and backtesting

Its one target expiration comes from the `expirations` request field; VIX and VIX3M ride as quote-only
`legs`, **never `symbols`** (which would make the producer maintain chains nothing reads). The provider
refuses rather than guesses, including on an unmeasured regime.

**The signal backtests inside this package; the trade was tested outside it.** `regime-history` replays
the classification over stored `stream_summary` closes with no look-ahead (a session's regime from the
PRIOR session's closes), benchmarked against VXX's next-session move — a **separation benchmark, never
suite P&L**; it schedules and decides nothing. Research item 37 priced the spreads at real quotes: every
arm failed after costs. Every position's per-tick mark path is forward-recorded and never
vendor-imagined.

## Live-trading prerequisites (none built)

No live path, no order code, no keyring, no live account. Before any live rung: (a) post-assignment
management; (b) VXX reverse-split detection and a halt procedure; (c) the exposure telemetry read as the
paper-to-live gap. **`live.enabled` is an inert placeholder** (the pmcc pattern): nothing checks it and it
is `configedit.GUARDED`.

## Layout

| file | role |
|---|---|
| `src/cherrypick/curve/regime.py` | ratio/regime/hook over cached quotes + stored history. Pure. |
| `src/cherrypick/curve/clock.py` | ET clock, monthly-cycle target expiration. Pure. |
| `src/cherrypick/curve/engine.py` | spread selection, worksheet math, settlement decomposition, fee stack. Pure. |
| `src/cherrypick/curve/provider.py` | cache snapshots; refuses rather than guesses. |
| `src/cherrypick/curve/management.py` | per-arm verdicts + advised-params choke point + exposure flag. Pure. |
| `src/cherrypick/curve/book.py` | decisions -> ledger rows: entries, closes, settlement, share disposal. |
| `src/cherrypick/curve/paper_loop.py` | regime write, mark, manage, enter, settle. |
| `src/cherrypick/curve/analytics.py` | the one query layer, including `flip_divergence_count`. |
| `src/cherrypick/curve/regime_history.py` | the read-side regime replay — a benchmark, never P&L. |
| `src/cherrypick/curve/db.py` | schema (`curve_positions`, `curve_marks`, `curve_regime`, `curve_snapshots`, attempts/refusals), migrations, stale-writer guard. |
| `src/cherrypick/curve/stream_request.py` | VXX underlying, VIX/VIX3M quote-only legs, target expiration, `history_days`. |
| `src/cherrypick/curve/cli.py` | `status` / `regime` / `worksheet` / `exposure` / `excursions` / `equity` / `headline` / `regime-history`. |

## Commands

Commands to run the package, its tests and its lint are in [README.md](README.md#commands).

Config: `config.example.json` -> `config.json` (git-ignored) or `~/.cherrypick/config/curve.json`; the
example is the design document — read its `_note` keys first.

## Guardrails

- **Paper only; no live path.**
- **Deterministic decision path**: `regime.py`, `engine.py`, `clock.py`, `management.py` are pure.
- Settlement is always `physical`; the module trades exactly one underlying.
- Tests isolate by an **autouse** temp-home fixture (`tests/conftest.py`), never opt-in.
- The module appears in the Review page via `curve_vx`; the `/curve` console page is live.
