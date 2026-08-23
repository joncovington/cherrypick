# cherrypick-curve

curve: **VXX call credit spreads** harvesting the VIX term-structure roll yield, gated by a daily
VIX/VIX3M regime read — **paper-only and credential-free**, a pure stream-cache consumer in the
calendars/pmcc posture. Every book trades the same shape (short call ~30-delta, long wing a
declared width higher, same monthly-cycle expiration); the books differ only in their declared
entry gate and exit rule, never in what is traded. Ledger schema: **`curve_vx`**.

Suite-wide context is the root [documentation index](../../docs/README.md).

## The experiment design

Three books, one variable each, plus the advisor's synthetic twin:

- **`control`** — the strategy as pitched: enter on a contango day (`ratio < contango_max`, a
  buffer below 1.0 so a knife-edge 0.999 day doesn't count), close at `profit_take_pct` of the
  entry credit, OR the **regime-flip hard exit** (a MEASURED ratio crossing >= 1.0 mid-trade
  closes next tick regardless of P&L — never on an unmeasured or stale read), OR `close_dte`.
- **`noflip`** — control's entry EXACTLY, same tick, same fills (the exact-pairing property); its
  exit is control's MINUS the flip rule, so it holds through backwardation to target or
  `close_dte`. **Until a flip actually fires, control and noflip are byte-identical by
  construction** — an expected `find_identical_readings` collision, not a defect. Every read
  surface presents `flip_divergence_count` beside the pair (positions where control's flip fired
  while noflip held): that count, not the trade count, is the noflip comparison's effective
  sample. A season of pure contango with zero flips proves nothing about the flip rule.
- **`hook`** — enters ONLY on the two-day-confirmed hook signal (`ratio > hook_threshold` AND
  below yesterday's — a deep-backwardation spike that has started to mean-revert), its own tick,
  its own variable; exits by control's rules. Expected to be **nearly always idle** — the pmcc
  keltner precedent: the idleness is the honest state, not a failure, and its read surface must
  say so.
- **`advised:<base>`** (paper, off by default) — the admitted params frozen on each row at entry
  and restated through one choke point (`management.effective_params`) every tick.

**curve is a structurally slow advisor target, stated honestly in config.** One position per book
at ~30-45 DTE with 50% takes closes maybe 2-4 trades a month; a 15-session advised experiment is
`underpowered` by construction against the promotion gate's min-14-days/min-sample-20 floor. The
bounds ship anyway (the contract is cheap) but `hook_threshold` and `close_dte` are deliberately
**not** advisable — the hook book's value is a fixed rare-event definition accruing a sample, and
`close_dte` bounds the settlement path; both are journaled-break territory, not overlay territory.

**Pairing is partial, and deliberately so** (the pmcc precedent): `control`/`noflip` enter from the
SAME plan on the same tick; `hook` enters on its own rare tick because its variable IS the entry
condition. Read surfaces must not treat the three as a fully paired grid.

## The regime series is the module's second product

The daily VIX/VIX3M ratio, its contango/backwardation classification, and the hook flag are
written to `curve_regime` **every session, whether or not any book trades** — the series' value is
its continuity. It is **RTH-gated and basis-stamped from day one**: the advisor's GEX-counts lesson
(2026-08-21) is the reason — a recorder that freezes on the last streamed value overnight
double-weights whatever sign the session ended on. A stale or missing VIX/VIX3M read writes a row
marked unusable (`curve_regime.usable = 0` with the refusal), never a frozen ratio. Note: this is
NOT `overview`'s binary contango gate — that stays the pre-open Met/Not-met read; this is the
richer record (ratio value, trend, hook detection, replayable history). Consumption by
`overview`/`advisor` is future wiring, off by default, its own journal entry.

## The honesty rules

1. **Every result is net of the modeled fee and slippage stack.** Gross is not a result.
2. **Early assignment is unmodelled but MEASURED, and the paper result is therefore an UPPER
   BOUND.** VXX pays no dividend (no ex-div calendar needed — one real simplification vs. pmcc),
   but a VXX spike still puts the short call ITM. Every mark where the short's extrinsic sits
   under `assignment_exposure_tv` is flagged `assignment_exposed` (`curve_marks`, aggregated by
   `analytics.exposure`).
3. **VXX reverse splits are unmodelled.** VXX reverse-splits on a roughly biennial cadence as
   contango grinds it down; a split mid-position is a manual `--settle` plus a journaled
   measurement break, and the OCC-root filter admits only the standard root (adjusted roots like
   `VXX1` are refused) — the pmcc split posture verbatim.
4. **ETN plumbing risk is declared, not modelled.** VXX is an ETN; the 2022 Barclays creation-halt
   precedent showed shares can decouple from the index (premium to indicative value). This module
   cannot detect that; the risk register says so, and it is one more reason the paper result is a
   bound, not a forecast.
5. **A hole in the mark path is refused, never zero** (`curve_marks.usable = 0` with the refusal).
   A stalled feed and a quiet vol market must never look identical — this module exists partly
   because of the 2026-07-01 lesson.
6. **Missing regime data blocks entry and can never force an exit.** No ratio -> no new position
   (recorded refusal `regime_unmeasured`); an open position with no ratio holds its last verdict.
   The flip-exit fires only on a MEASURED crossing.
7. **The regime row is written every session**, traded or not.

## Settlement

American physical delivery, the calendars/pmcc decomposition verbatim: an ITM leg at expiry books
intrinsic and delivers/receives 100 shares per contract at the settlement spot; the next session's
combined disposal covers/sells them. In practice `close_dte = 7` makes expiry settlement the
exception path, not the plan — but it is built, because `noflip` can ride deep into backwardation
and the row must still book honestly.

## Layout (mirrors pmcc)

| file | role |
|---|---|
| `src/cherrypick/curve/regime.py` | the ratio/regime/hook computation over cached quotes + stored history. Pure. |
| `src/cherrypick/curve/clock.py` | ET clock, the monthly-cycle target-expiration plan. Pure. |
| `src/cherrypick/curve/engine.py` | spread selection (delta-target short, width wing), worksheet math, the settlement decomposition, the fee stack. Pure. |
| `src/cherrypick/curve/provider.py` | snapshots from the shared stream cache; refuses rather than guesses. |
| `src/cherrypick/curve/management.py` | per-book verdicts (profit-take, regime-flip, close_dte) + the advised-params choke point + the exposure flag. Pure. |
| `src/cherrypick/curve/book.py` | engine decisions -> ledger rows: entries, closes, settlement, share disposal. |
| `src/cherrypick/curve/paper_loop.py` | the session driver: regime write, mark, manage, enter, settle. |
| `src/cherrypick/curve/analytics.py` | the one query layer every read surface goes through, including `flip_divergence_count`. |
| `src/cherrypick/curve/regime_history.py` | the read-side regime replay (`regime-history`) — a separation benchmark, never suite P&L. |
| `src/cherrypick/curve/db.py` | schema (`curve_positions`, `curve_marks`, `curve_regime`, `curve_snapshots`, attempts/refusals), additive migrations, the stale-writer guard. |
| `src/cherrypick/curve/stream_request.py` | declares VXX as the underlying, VIX/VIX3M as quote-only legs, the target expiration, and history_days. |
| `src/cherrypick/curve/cli.py` | `status` / `regime` / `worksheet` / `exposure` / `headline` / `regime-history`. |

## Commands

```bash
python -m cherrypick.curve.paper_loop --once        # one gated tick (what the supervisor spawns off-session)
python -m cherrypick.curve.paper_loop --interval 60 # the in-session resident loop
python -m cherrypick.curve.paper_loop --status      # one JSON health object (watchdog contract)
python -m cherrypick.curve.paper_loop --settle --date 2026-09-18 --price 42.10  # official print
python run.py status                                # open positions + target expiration + today's regime
python run.py regime                                # the stored daily regime series
python run.py worksheet                              # the live per-position worksheet
python run.py exposure                               # the early-assignment-exposure telemetry
python run.py regime-history                         # the VIX/VIX3M signal replay over stored history
python -m pytest                                     # temp CHERRYPICK_HOME; no broker, no streamer needed
ruff check . && ruff format .                        # line-length 110
```

Config: copy `config.example.json` -> `config.json` (git-ignored), or place
`~/.cherrypick/config/curve.json`. The example file is the design document — read its `_note` keys
before changing a value.

## Data source

This module runs no streamer and holds **no broker credentials at all**. Its one target expiration
exists in the shared cache because `stream_request.py` declares it via the registry's
`expirations` field every tick; VIX and VIX3M ride as quote-only `legs` (never `symbols` — that
would have the producer maintain 0DTE chains for two symbols nothing here reads, the overview
2026-08-17 incident this module's plan explicitly avoids repeating). No `window_hints`: a
~30-delta short and a wing a few dollars out both sit inside any default ATM window.

The provider refuses rather than guesses: stale or crossed quotes, a missing chain, a spot that
won't print, an unmeasured regime — each is a recorded refusal the loop steps past. The OCC-root
filter admits only the configured root: VXX splits periodically, and a post-split adjusted root
shares expiration dates with the standard one. A split mid-position is unmodelled — manual
`--settle` plus a measurement break.

## Backtesting — what can and cannot be replayed

**The signal backtests; the trade does not.** `regime-history` replays the contango/backwardation/
hook classification over stored `stream_summary` history (VIX/VIX3M closes, no look-ahead — a
session's regime comes from the PRIOR session's closes) and reports VXX's own next-session move
per regime as the benchmark. It is a **separation benchmark, not suite P&L** — no trade was taken
on any of those sessions, and it schedules nothing and decides nothing.

The credit-spread P&L cannot be honestly backtested in-suite: it would need historical VXX option
chains, which exist nowhere in the suite's data, and the advisor's own contract already settled
this — there is no replay engine and there will not be one; a proposal's test is a next-session
paper arm beside its control. The accepted middle path is the calendars pattern: every open
position's per-tick mark path is recorded (`curve_marks`), so a read-side exit-policy replay
(different profit takes, different flip thresholds, exact pairing) becomes possible later over
data this module itself recorded — forward-recorded, then replayed, never vendor-imagined.

## Live-trading prerequisites (none built; gates any future live plan here)

This module has **no live path** — no order code, no keyring, no live account designation. Before
any live rung: (a) post-assignment management — detecting a surprise early assignment and a
defined cover/repair procedure; (b) a VXX-reverse-split detection and halt procedure (unmodelled
here on purpose); (c) the paper exposure telemetry (rule 2) read as the gap between the paper
result and a live expectation.

**The config's `live.enabled` field is an inert placeholder, not a rung** — the pmcc pattern
verbatim. It lets the suite's config/console surfaces show this module as "paper only" instead of
"unknown"; no code anywhere checks it, and it is `configedit.GUARDED` so the settings surface
can't touch it.

## Guardrails (suite-wide)

- **Paper only. There is no live path.** `live.enabled` in config is a documented placeholder only.
- **The decision path is deterministic.** `regime.py`, `engine.py`, `clock.py` and `management.py`
  are pure functions over pre-fetched data — no model, no MCP, no network in the decision itself.
- Declared settlement only (VXX is always `physical`); a symbol this module is not built for is
  out of scope by construction (it trades exactly one underlying).
- Credentials in the OS keyring only (this module holds none). Account numbers masked to
  `****1234`. Portable paths only; scratch work in `.tmp/`. Human-voice docs and commits, no AI
  attribution. Instruction files hold no code.
- Tests isolate by an **autouse** temp-home fixture (`tests/conftest.py`), never opt-in — the
  flies 2026-07-20 lesson.

## Known risks, stated up front

1. **The short-call delta selection degrades to a computed delta, never to a moneyness guess.**
   When the feed's own delta is missing, `engine.bs_call_delta` computes one via Black-Scholes
   from quantities the chain already publishes (spot, strike, DTE, the feed's own IV) — a
   computation, not a heuristic — and the row records `short_selected_by = "delta_computed"` so
   degraded entries stay excludable read-side (the pmcc `long_selected_by` pattern). Only a chain
   with no strike offering either a real or computable delta (IV also missing) refuses
   `no_delta_for_selection`.
2. **Early assignment** — rule 2. The paper result is an upper bound.
3. **VXX reverse splits** — unmodelled; manual settle + break.
4. **ETN plumbing risk** — rule 4; undetectable by this module.
5. **The hook book's sample accrues slowly by design** — ~16% of days are backwardation and the
   hook is rarer still. Read surfaces must carry sample-size warnings from day one.

## Status

Built 2026-08-22, no paper data yet. First paper session begins with the first scheduled run; the
regime series accumulates value immediately regardless of whether any book has traded. The module
appears in the generic Review page from day one via the `curve_vx` schema registration; a
dedicated `/curve` console page is deferred until first real positions exist (the pmcc sequencing).
