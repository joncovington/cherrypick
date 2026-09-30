# cherrypick-curve

> **Vocabulary.** This module's **book** is the suite's **arm** (root `CLAUDE.md`). The column
> (`curve_positions.book`) moves with the rest of the schema; prose and the console already say `arm`.

VXX call credit spreads harvesting the VIX term-structure roll yield, gated by a daily VIX/VIX3M
regime read (posture: root file). Every book trades the same shape — short call ~30-delta, long wing
a declared width higher, same monthly-cycle expiration — and differs only in declared entry gate and
exit rule. Ledger schema: **`curve_vx`**. Calibration history: [docs/history.md](docs/history.md).
Suite-wide context: the root [documentation index](../../docs/README.md).

## The structure's calibration (never pool across these breaks)

- **`spread_width` 1.0** (was 5.0 until 2026-08-27). A credit spread's credit cannot exceed the
  short's premium, so `credit / width` is capped at `short_mid / width` whatever the wing costs; at
  5-wide the cap sat barely above the 15% floor. At 1-wide the short pays ~17% of width.
  `tests/test_structure_coherence.py` pins width against floor off the shipped config, so a pair
  that cannot coexist fails at declaration, not after a month of silent refusals.
- **`max_leg_spread_pct` 0.30** (was 0.25): the short itself quotes 0.27–0.28 wide on VXX.
- **A wing's spread is money, not a ratio.** The long leg is refused only when wide in percent AND
  in dollars (`max_wing_spread_abs`) — a zero bid makes `spread_pct` 2.0 whatever the option costs.
  The SHORT leg keeps the plain percentage test: its premium is the whole credit, and paying up there
  is what the gate prevents.
- **Fee-adjusted credit floor** (2026-09-02): `engine.plan_entry` nets modelled entry commission
  AND slippage against the dollar floor and refuses `credit_below_fee_adjusted_floor`, with or
  without a config passed. A percentage floor is a ratio of mids and cannot see opening cost (the
  first trade lost $6.49 of $18 to it, 72% slippage). Unlike MEIC's, it nets slippage too, since
  this credit metric has none haircut out. Only the entry side is netted — the exit's cost depends
  on a close path not yet decided.
- **`quantity` 2** (2026-09-16, journaled as `defaults.quantity`): two contracts amortise the fixed
  round-trip cost; the floor is unchanged, so it still refuses genuinely uneconomic entries. Width was
  deliberately NOT widened with it — credit-of-width falls as width grows. **Sizing is a human
  decision outside the advice bounds.**

## The experiment design

- **`control`** — enter on a contango day (`ratio < contango_max`, a buffer below 1.0), close at
  `profit_take_pct` of entry credit, OR the **regime-flip hard exit** (a MEASURED ratio crossing
  >= 1.0 mid-trade closes next tick regardless of P&L — never on an unmeasured or stale read), OR
  `close_dte`.
- **`noflip`** — control's entry EXACTLY (same tick, same fills), minus the flip exit. Until a flip
  fires the two are byte-identical — an expected `find_identical_readings` collision. Every read
  surface shows `flip_divergence_count` beside the pair: that, not trade count, is the effective
  sample. A season of contango with zero flips proves nothing about the flip rule.
- **`hook`** — enters ONLY on the two-day-confirmed hook (`ratio > hook_threshold` AND below
  yesterday's), its own tick; exits by control's rules. **Nearly always idle** — honest, not a
  failure (~16% of days are backwardation, the hook rarer still); read surfaces must say so and carry
  sample-size warnings from day one.
- **`advised:<experiment name>`** (paper, off by default; mechanism in `packages/core/CLAUDE.md`).
  A twin is gated and planned as its base book (a `hook` twin waits for the hook), and its
  `base_book` is what the flip exit keys on (a `noflip` twin never flips). Pre-2026-09-17 tags
  `advised:control` / `advised:hook` still resolve. **`advice.enabled` stays false by design.**

**Pairing is partial, deliberately**: `control`/`noflip` share one plan and tick; `hook` enters on
its own. Never treat the three as a fully paired grid.

**A structurally slow advisor target**: ~2-4 closes a month, so a 15-session experiment is
`underpowered` against the promotion gate's min-14-days/min-sample-20 floor. Bounds ship anyway, but
`hook_threshold` and `close_dte` are **not advisable** — the hook's value is a fixed rare-event
definition, and `close_dte` bounds the settlement path; both are journaled-break territory.

## The regime series is the module's second product

The daily VIX/VIX3M ratio, its classification and the hook flag are written to `curve_regime`
**every session, traded or not** — the series' value is its continuity. **RTH-gated and
basis-stamped**: a recorder that freezes on the last value overnight double-weights the session's
closing sign (the advisor's GEX-counts lesson). A stale or missing read writes an unusable row
(`usable = 0` with the refusal), never a frozen ratio. This is NOT `overview`'s binary contango gate;
consumption by `overview`/`advisor` is future wiring, off by default, its own journal entry.

## The honesty rules

1. **Net of the modelled fee and slippage stack.** Gross is not a result.
2. **Early assignment is unmodelled but MEASURED; the paper result is an UPPER BOUND.** VXX pays no
   dividend (no ex-div calendar), but a spike still puts the short ITM. Marks with extrinsic under
   `assignment_exposure_tv` are flagged `assignment_exposed` (`curve_marks`, `analytics.exposure`).
3. **VXX reverse splits are unmodelled** (roughly biennial): a mid-position split is a manual
   `--settle` plus a journaled break; the OCC-root filter refuses adjusted roots like `VXX1`.
4. **ETN plumbing risk is declared, not modelled** (the 2022 Barclays creation halt decoupled shares
   from the index). Undetectable here; one more reason the result is a bound, not a forecast.
5. **A hole in the mark path is refused, never zero** (`curve_marks.usable = 0`) — a stalled feed
   and a quiet vol market must never look identical.
6. **Missing regime data blocks entry and can never force an exit.** No ratio → no position
   (`regime_unmeasured`); an open position with no ratio holds its last verdict.
7. **The regime row is written every session**, traded or not.
8. **Short-delta selection degrades to a computed delta, never a moneyness guess.** Missing feed
   delta → `engine.bs_call_delta` (Black-Scholes from spot, strike, DTE and the feed's own IV), and
   the row records `short_selected_by = "delta_computed"` so it stays excludable. Only a chain with
   neither refuses `no_delta_for_selection`.

**Settlement**: American physical delivery, the calendars/pmcc decomposition verbatim (intrinsic
plus 100 shares per contract at the settlement spot, disposed next session). `close_dte = 7` makes
expiry the exception path, but it is built because `noflip` can ride deep into backwardation.

## Data source and backtesting

Its one target expiration comes from the `expirations` request field; VIX and VIX3M ride as
quote-only `legs`, **never `symbols`** (which would make the producer maintain chains nothing reads —
the overview 2026-08-17 incident). No `window_hints` needed. The provider refuses rather than guesses,
including on an unmeasured regime.

**The signal backtests; the trade does not.** `regime-history` replays the classification over
stored `stream_summary` closes with no look-ahead (a session's regime from the PRIOR session's
closes), benchmarked against VXX's next-session move — a **separation benchmark, never suite P&L**;
it schedules and decides nothing. The spread P&L cannot be backtested (no historical VXX chains, and
the advisor contract rules out a replay engine); instead every position's per-tick mark path is
recorded so a read-side exit-policy replay is possible later — forward-recorded, never
vendor-imagined.

## Live-trading prerequisites (none built)

No live path, no order code, no keyring, no live account. Before any live rung: (a) post-assignment
management; (b) VXX reverse-split detection and a halt procedure; (c) the exposure telemetry read as
the paper-to-live gap. **`live.enabled` is an inert placeholder** (the pmcc pattern): nothing checks
it and it is `configedit.GUARDED`.

## Layout

| file | role |
|---|---|
| `src/cherrypick/curve/regime.py` | ratio/regime/hook over cached quotes + stored history. Pure. |
| `src/cherrypick/curve/clock.py` | ET clock, monthly-cycle target expiration. Pure. |
| `src/cherrypick/curve/engine.py` | spread selection, worksheet math, settlement decomposition, fee stack. Pure. |
| `src/cherrypick/curve/provider.py` | cache snapshots; refuses rather than guesses. |
| `src/cherrypick/curve/management.py` | per-book verdicts + advised-params choke point + exposure flag. Pure. |
| `src/cherrypick/curve/book.py` | decisions -> ledger rows: entries, closes, settlement, share disposal. |
| `src/cherrypick/curve/paper_loop.py` | regime write, mark, manage, enter, settle. |
| `src/cherrypick/curve/analytics.py` | the one query layer, including `flip_divergence_count`. |
| `src/cherrypick/curve/regime_history.py` | the read-side regime replay — a benchmark, never P&L. |
| `src/cherrypick/curve/db.py` | schema (`curve_positions`, `curve_marks`, `curve_regime`, `curve_snapshots`, attempts/refusals), migrations, stale-writer guard. |
| `src/cherrypick/curve/stream_request.py` | VXX underlying, VIX/VIX3M quote-only legs, target expiration, `history_days`. |
| `src/cherrypick/curve/cli.py` | `status` / `regime` / `worksheet` / `exposure` / `headline` / `regime-history`. |

## Commands

```bash
python -m cherrypick.curve.paper_loop --once        # one gated tick (what the supervisor spawns off-session)
python -m cherrypick.curve.paper_loop --interval 60 # the in-session resident loop
python -m cherrypick.curve.paper_loop --status      # one JSON health object (watchdog contract)
python -m cherrypick.curve.paper_loop --settle --date 2026-09-18 --price 42.10  # official print
python run.py status                                # open positions + target expiration + today's regime
python run.py regime                                # the stored daily regime series
python run.py worksheet                             # the live per-position worksheet
python run.py exposure                              # the early-assignment-exposure telemetry
python run.py regime-history                        # the VIX/VIX3M signal replay over stored history
python -m pytest                                    # temp CHERRYPICK_HOME; no broker, no streamer needed
ruff check . && ruff format .                       # line-length 110
```

Config: `config.example.json` -> `config.json` (git-ignored) or `~/.cherrypick/config/curve.json`;
the example is the design document — read its `_note` keys first.

## Guardrails

- **Paper only; no live path.**
- **Deterministic decision path**: `regime.py`, `engine.py`, `clock.py`, `management.py` are pure.
- Settlement is always `physical`; the module trades exactly one underlying.
- Tests isolate by an **autouse** temp-home fixture (`tests/conftest.py`), never opt-in.
- The module appears in the Review page via `curve_vx`; the `/curve` console page is live.
