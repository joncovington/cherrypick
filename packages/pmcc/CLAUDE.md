# cherrypick-pmcc

> **Vocabulary.** The arm column is `pmcc_positions.arm`. Configs still spelling the registry `books`
> resolve through `cherrypick.core.config`, as every existing spelling does.

PMCC — a deep-ITM call with a short call sold against it, on XSP, QQQ, GLD, IWM and SLV (posture:
root file). Three arms (below):
- `control` re-buys a ~21-DTE long every cycle;
- `shield` and `shield_hold` hold a ~1-year long while a weekly short rolls against it.

Ledger schema: **`pmcc`** (`pmcc_99` until the 2026-10-04 rename; a config still naming it resolves
through `core.ledgers.SCHEMA_ALIASES`).

Elsewhere:
- history and incidents: [docs/history.md](docs/history.md);
- the evidence behind the held-long arms and the five symbols: [docs/shield-study.md](docs/shield-study.md);
- window sizing: [docs/window-parameters.md](docs/window-parameters.md);
- suite-wide context: the root [documentation index](../../docs/README.md).

## Measurement breaks — never pool across these

- **2026-08-23 redesign**: from 3 symbols/3 books (TNA/TQQQ/UPRO; `control`/`keltner`/`roll`) to one
  `control` arm with a delta-band long, nearest-spot short and hold-to-expiry exit. Pre-redesign rows
  are history, not comparable measurement.
- **2026-08-23 XSP added** to `control` (additive). **Always read `pmcc` grouped by `symbol`, never
  pooled**: each symbol is its own population.
- **2026-08-28 entry spread gate**: `max_leg_spread_pct` is enforced at entry as well as at exit.
- **2026-10-05 shield boundary** (`paper_loop.SHIELD_FROM`). All of these land together, and the loop
  journals four `measurement_breaks` rows (`era`, `arms`, `symbols`, `pacing`), read from the config
  as it stands:
  - the held-long arms join control;
  - the symbols become XSP/QQQ/GLD/IWM/SLV (TQQQ takes no new entries and runs off);
  - `max_positions` goes from 3 to 6;
  - held-long first entries are paced (`entry_symbols_per_session`);
  - the era becomes `shield`.

  XSP control's rules are unchanged across it, but its roster and cap are not, so it is read per era.
  If the deploy slips past the date, move `SHIELD_FROM` with it.

## The experiment design

| arm | long | short | management |
|---|---|---|---|
| `control` | 17–25 DTE nearest 21, delta 0.85–0.90, re-bought every cycle | ATM, soonest Friday 5–11 DTE | held to the short's expiry; the long rides to the next session's disposal |
| `shield_hold` | the standard monthly nearest 360 DTE in [240, 540], delta 0.90–0.95, no extrinsic fallback | 0.70 delta (band 0.65–0.78), soonest Friday 5–11 DTE, above the long's strike, never past its expiry | rolled on its expiry day; closed at 45 DTE on the long or on the 30% stop |
| `shield` | as `shield_hold` | as `shield_hold` | also rolls early at 85% of the short's extrinsic decayed, or spot at its strike |

**How the arms are defined and enabled.**
- What makes each arm itself is in code, `engine.ARM_RULES`, merged between `defaults` and the arm's
  config block (`engine.merged_params`). A thin block cannot turn a held-long arm into a copy of
  control. An open position keeps its arm's rules even if the arm leaves the config.
- An arm other than control that the config does not declare stays off (`engine.DEFAULT_ENABLED`).

**What the shield arms leave out**, declared rather than tuned later:
- Tom King's discretionary entry setup;
- his 50–100% profit target;
- adding lots.

A stop followed by mechanical re-entry is a costly re-strike of the long. The replay finds the stop
mixed (docs/shield-study.md); it is kept because it is the design's own rule, and it is measured.

**Advised twins.** `advised:<experiment name>` (paper, off by default) shadows **control only**. The
mechanism is in `packages/core/CLAUDE.md`, with params frozen at entry and restated through
`management.effective_params`. The experiment worth running is `tv_managed_exit`/`tv_close_threshold`
against hold-to-expiry; `tv_managed_exit` is a no-op for a held-long position. The pre-2026-09-17
`advised:control` tag and the retired `advised:keltner`/`advised:roll` still resolve for history.
**Every twin is on the stream-request roster** (see Data source).

## The held-long lifecycle

`management.evaluate_held_long` decides, in this order:
1. stop (`stop_loss`);
2. long roll (`long_roll_due`);
3. sell a short when none is open (`no_short`);
4. deadline buyback (`roll_deadline`);
5. expiry roll (`expiry`);
6. decay roll (`decayed`, `shield` only);
7. breach roll (`breach`, `shield` only);
8. hold.

**Times are measured from the session's own close** (`clock.session_close_min`, so 13:00 on an early
close):
- the roll from `roll_time_offset` (60) minutes before it;
- a buyback-only deadline at `roll_deadline_offset` (20);
- an early roll at most once a session (`db.rolled_today`).

**What can refuse:**
- a new short spanning a declared ex-date (`ex_dividend_span`); the position runs without a short
  until a later day clears it, and the tracker counts those weeks;
- an entry's long: the pick comes from the producer's own listing (`streamcache.stream_expirations`,
  via `provider.listed_expirations`), standard monthlies first, because on 2026-10-04 only they listed
  deep strikes (`scripts/pmcc_leap_probe.py`). Refusals: `no_leap_listed` (nothing in the band),
  `no_listing` (no listing cached), `no_short_delta` (no greeks for the short), `entry_pacing`.

**After a close or at the bell:**
- a stop or long-roll close re-enters the **next session**;
- settlement keeps a held-long position open while its long survives;
- `_dispose_longs` skips held-long positions.

**`account_policy`** (`ira` default) is read only by held-long arms, so paper shield trades under
its live twin's rules:
- `ira`: every expiring short is bought back before the bell at any moneyness, since an IRA cannot
  carry short stock;
- `margin`: a short further than `pin_buffer_pct` out of the money may expire.

## Evidence era

`pmcc_positions.era` follows MEIC's `CURRENT_ERA` convention:
- `analytics.headline(conn)` and `excursions` scope to `analytics.CURRENT_ERA` by default;
  `era="ALL"` (CLI `--era ALL`) pools explicitly.
- The eras are **`"shield"`** (2026-10-05 ->), **`"redesign"`** (2026-08-23 -> 10-04) and
  pre-column rows reading `NULL` (the console calls them `pre-redesign`). `book.enter_position`
  stamps the current era on every new row.
- The console's era picker scopes the arm comparison and the weekly A/B. `test/pmcc-era.test.ts`
  fails when the console's copy of `CURRENT_ERA` lags this one.
- `worksheet()` is not era-scoped.

**Closed-only readers see the held-long arms late.** A shield position closes about ten months after
entry, so the arm comparison's closed table, the review fact set and the advisor's history show
nothing from it for most of a year. Its evidence is marked to market:
- `analytics.open_mtm`, on the console's "open, marked to market";
- `analytics.weekly_by_arm`, the paired readout per symbol and ISO week ("weekly by arm");
- `tracker.tracker`, one position week by week, the spreadsheet view (the console's tracker tab).

All three value through `tracker.value_at`:
- closed legs at their close;
- open legs at the latest usable mark at or before the moment;
- each cost at its own time.

Their identities: entry + exit = gross; gross − costs = net; the last weekly row equals the header.

## The honesty rules

1. **Net of the modelled fee and slippage stack**:
   - commissions, clearing, ORF and TAF;
   - the $5-per-ITM-symbol settlement/assignment event;
   - SEC and FINRA pass-throughs on the share side of an assignment;
   - the suite's slippage model.

   Each leg carries its own share of its ticket's cost (`engine.allocate`; the remainder goes on the
   last leg, so the legs sum to the ticket). ETFs carry no index exchange fee; XSP is looked up on
   `cherrypick.core.fees.INDEX_EXCHANGE_FEE_PER_CONTRACT`.
2. **Early assignment is unmodelled but MEASURED, so every physical symbol's paper result is an
   UPPER BOUND.**
   - Both designs sell ITM calls.
   - Every mark where the short's extrinsic is under `assignment_exposure_tv` is flagged
     `assignment_exposed` (`pmcc_marks`, `analytics.exposure`).
   - **XSP is exempt**: it is European and cannot be exercised early.
3. **Ex-dividend spans are refused, not modelled — physical symbols only.**
   - A short spanning a declared ex-date refuses `ex_dividend_span`; a span the calendar cannot
     answer for refuses `dividend_calendar_lapsed`.
   - Dates are hand-declared from the issuer's schedule and hand-refreshed; QQQ's runs only to
     2026-12-31.
   - GLD and SLV, which pay nothing, are declared with empty lists, so "no dividend" and "no table"
     never look alike. XSP needs no entry.
4. **Rules are declared up front, never tuned mid-experiment.** A removed rule keeps its negative
   result on the record.
5. **A hole in the mark path is refused, never zero** (`pmcc_marks.usable = 0` with the refusal).
6. **Changing tick cadence is a journaled measurement break.**
7. **A degraded long selection stays excludable** (`long_selected_by = 'extrinsic'`). The held-long
   arms never take that fallback.
8. **A full arm is recorded, not silent** (`slot_held`, collapsed per day, arm and symbol).
9. **Settlement after the bell is a backstop.** It reads an ETF's last trade at 16:20, which can be
   an after-hours print. Under the `ira` policy every expiring short is already bought back before
   the bell, so this touches only a buyback that failed.

## Data source and the deep window

**Where the dates and strikes come from:**
- Expirations come from the `expirations` request field, per symbol (`stream_request.wanted_expirations`).
- Deep strikes come from `window_hints`.
- A held-long position's own long stays quoted through `leg_sources`, so its expiry is not requested
  again. The roll Friday is requested for every symbol holding a held-long position.

**The deep window has a width per arm.** `provider.deep_window_pct_for` reads an arm's own
`deep_window_pct_by_symbol` before `defaults`:
- control's 21-DTE long sits 4–10% in the money;
- the year-long long sits 20–40% in the money.

`stream_window.needed_width` counts strikes in the target expiry only.

**Rules for the widened window:**
- **It is gated on whether ANY arm can still enter**, advised twins included, through
  `paper_loop.session_books`. Asking only about control once starved a twin for two sessions.
- **The hint is declared downward only.** Open legs stay subscribed, so window risk is confined to
  entry pricing.
- **The producer persists every listed expiry** (`stream_expirations`). A request for a date the
  broker does not list is a WARN, never a stall restart.
- **The provider refuses rather than guesses.** The OCC-root filter admits only the configured root.
  A split mid-position is unmodelled: a manual `--settle` plus a measurement break.

## Physical settlement (QQQ, GLD, IWM, SLV; TQQQ until it runs off)

The calendars decomposition, verbatim: an ITM short at expiry books its intrinsic value AND delivers
**100 short shares per contract at the settlement spot** (`pmcc_assignments`).

For control:
- the next session's combined disposal covers the shares and sells the long;
- a position does not close while shares are outstanding;
- the Friday-to-Monday gap stays visible, because it IS the weekend exposure.

For a held-long position: the long stays open, delivered shares block a new short until they are
covered, and under `ira` a delivery means a buyback already failed.

XSP (`cash`) books intrinsic and finalizes: no shares, and no weekend carry.

## Live-trading prerequisites (none built)

**No live path exists yet** — no order code, no keyring. The SLV live pilot is designed but not built.
Its plan, kept in the session record:
- a persistent arm;
- standing position authority over open positions;
- a configurable `live.symbols` map with per-symbol and account-wide caps;
- the `account_policy` check against the broker's own account type;
- gate 0 per symbol.

**Before any live rung**, these are prerequisites, not enhancements:
- **post-assignment management**, including reconciliation against the broker;
- a **calculated ex-div decision**;
- the exposure telemetry (rule 2), read as the paper-to-live gap.

**`live.enabled` is an inert placeholder**: it is `configedit.GUARDED`, and flipping it does nothing.

## Layout

| file | role |
|---|---|
| `src/cherrypick/pmcc/clock.py` | ET clock; control's expiration plan, the held-long plan (`leap_expiration`, `short_expiration`), session-close times. Pure. |
| `src/cherrypick/pmcc/engine.py` | arm roster and rules, leg selection (band long, ATM or delta short), worksheet math, settlement decomposition, fee stack and per-leg allocation. Pure. |
| `src/cherrypick/pmcc/provider.py` | read-only cache snapshots, the listing, the one-sided deep window; refuses rather than guesses. |
| `src/cherrypick/pmcc/management.py` | control verdict (or advised tv exit), the held-long verdict, execution gates, exposure flag. Pure. |
| `src/cherrypick/pmcc/book.py` | decisions → ledger rows: entries, closes, rolls, short sales and buybacks, settlement, disposal. |
| `src/cherrypick/pmcc/paper_loop.py` | mark, manage, enter, dispose, settle at the bell; journals the shield boundary. |
| `src/cherrypick/pmcc/analytics.py` | the one read-only query layer (`headline`, `excursions`, `exposure`, re-exports the tracker). |
| `src/cherrypick/pmcc/tracker.py` | `value_at` and everything valued through it: the tracker, `open_mtm`, `weekly_by_arm`. |
| `src/cherrypick/pmcc/db.py` | schema, additive migrations, stale-writer guard, writers. |
| `src/cherrypick/pmcc/stream_request.py` | declares symbols, open legs, per-symbol expirations, window hints. |
| `src/cherrypick/pmcc/stream_window.py` | deep-window width: computed per target expiry, escalated on misses. |
| `src/cherrypick/pmcc/cli.py` | `status` / `headline` / `worksheet` / `exposure` / `tracker` / `tracker-index` / `weekly`. |

## Commands

```bash
python -m cherrypick.pmcc.paper_loop --once        # one gated tick (what the supervisor spawns off-session)
python -m cherrypick.pmcc.paper_loop --interval 60 # the in-session resident loop
python -m cherrypick.pmcc.paper_loop --status      # one JSON health object (watchdog contract)
python -m cherrypick.pmcc.paper_loop --settle --date 2026-10-09 --price 71.23 [--symbol SLV]  # official print
python run.py status                               # open positions + the expiration plan
python run.py headline [--era ALL]                 # closed results per arm and symbol
python run.py tracker-index                        # every position the tracker can show
python run.py tracker --position SLV:shield:2026-10-05   # one position, week by week
python run.py weekly [--era ALL]                   # the arms' weekly A/B
python run.py exposure                             # the early-assignment-exposure telemetry
python ../../scripts/pmcc_leap_probe.py            # what each symbol lists ~1 year out (network, read-only)
python ../../scripts/pmcc_shield_replay.py         # the 2011- replay behind the shield arms
python -m pytest                                   # temp CHERRYPICK_HOME; no broker, no streamer needed
ruff check . && ruff format .                      # line-length 110
```

Config: `config.example.json` → `config.json` (git-ignored) or `~/.cherrypick/config/pmcc.json`; the
example is the design document — read its `_note` keys first.

## Guardrails and couplings

- **Paper only; no live path** (see prerequisites).
- **Deterministic decision path**: `engine.py` and `management.py` are pure over pre-fetched data.
- Declared settlement only (`settlement_style`); a symbol declared as neither is refused.
- **Orchestrator coupling — don't change silently:**
  - the paper DB path (`~/.cherrypick/data/pmcc/paper_trades.db`, also load-bearing for review and
    the advisor fact pack) and its `pmcc` schema, read through `cherrypick.core.ledgers`;
  - the trade notifier, which pings entries, notable short trades (early rolls, buybacks; never the
    routine Friday roll), each settled short leg, and closes.
- **The console's `/pmcc` page mirrors `analytics.py` in TypeScript.** `test/pmcc-mirror.test.ts`
  checks the arm comparison in both era scopes and the open mark-to-market, to the cent, against
  `python run.py headline`. The tracker, the index and the weekly A/B are bridged from the module's
  CLI rather than re-derived.
- Tests isolate by an **autouse** temp-home fixture (`tests/conftest.py`), never opt-in.
