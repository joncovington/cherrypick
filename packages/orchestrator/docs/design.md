# Cherrypick — Trading Suite: Shared Core + Modules — Research Report

> **A 2026-07-11 research report — design rationale, not current state.**
>
> This is *why* the suite is shaped the way it is: the duplication that justified a shared core,
> the reliability model, the standards, and the specs for parts still unbuilt. It is deliberately
> **not** updated as work ships, and it long predates the monorepo, `packages/core`, and the
> gex/flies/streamer modules.
>
> - **What actually shipped** → git log / commit history (`ROADMAP.md` is deprecated as of
>   2026-08-02 — see its own header; it is frozen, not maintained going forward)
> - **Current architecture** → [`../CLAUDE.md`](../CLAUDE.md) and the [suite docs](../../../docs/README.md)
>
> Sections that were purely point-in-time status (the original "Build Status", "What's next",
> "Stages 0–8", and Parts 6, 7, 14, 15) are dropped rather than left to read as fact — their content
> was superseded by ROADMAP.md at the time this document was written. Two durable blocks buried
> inside them are preserved up front. Where a surviving section contains a decision that has since
> been reversed, it is marked inline with **[SUPERSEDED]** rather than deleted, so the reasoning
> stays legible.

---

**Name (decided):** the suite is **Cherrypick** (cherry-adjacent, per tastytrade's cherry branding;
"cherry-picking" = selective trade selection, exactly what the gates do). The shared core library is
**cherrypick-core** (the pit = the thing at the center). Repo/package/import names align to
`cherrypick-core` / `cherrypick.core` (lowercase, import-friendly). Existing modules keep their `…Agent`
names under the Cherrypick umbrella.

**UPDATE (2026-07-10, packaging-naming decision — supersedes the above for pip/PyPI):** for pip
distribution the suite adopts a single **`cherrypick.*` namespace**; the import root is
**`cherrypick.core`** (the earlier `cherrypit`/`cherrypit-core` naming is retired — the "pit" wordplay
survives only as the flavor text above). Distribution → import: `cherrypick` → `cherrypick`
(+ `cherrypick.orchestrator`); `cherrypick-core` → `cherrypick.core`; `cherrypick-meic` →
`cherrypick.meic`; `cherrypick-earnings` → `cherrypick.earnings`. PEP 420 native namespace packages (no
root `__init__.py`), lowercase-hyphen distribution names, `cherrypick[all]` extra. **Migration EXECUTED
2026-07-10:** GitHub repo renamed `cherrypit-core`→`cherrypick-core`; package moved
`cherrypit/`→`cherrypick/core/`; both modules cut over (the `src/_core` bootstrap path is unchanged); CI
green. Rationale + full mapping: memory `packaging-naming-decision`.

> **[SUPERSEDED]** The `cherrypick.*` namespace decision held and is still exactly right. Two details
> did not: `src/_core` no longer exists (core is `packages/core`, an editable-installed dependency —
> every bootstrap was deleted 2026-08-01), and the module list grew to `meic, earnings, gex, flies,
> streamer, core, orchestrator`, all under `packages/`.



## The prime directive

**Prime directive (user's goal):** a user sets up trading/paper plans, **walks away** for the day / night
/ week, and trusts the process won't be silently interrupted — any failure is **notified**, or at an
absolute floor **warned through logging**. Unattended reliability is the #1 requirement.

This one paragraph is the root of the entire reliability architecture — the watchdog, the
notification state machine, and the "no network/AI on the reliability path" invariant all descend
from it.

---

## Design corrections found during extraction

Recorded 2026-07-11 while the shared core was being pulled out, and still the governing rule for
what belongs in `cherrypick.core`:

**Corrections to this plan's assumptions (found during extraction):**
- **"Unify the comparison metrics" (Part 10 / 4b) is NOT a clean unification.** MEIC's metrics run on a
  *daily aggregated return series* (Sharpe annualized ×√252); Earnings' run on *discrete event-trade
  P&L lists* (Sharpe deliberately **not** annualized — different by domain). Same names, different math.
  Treat as **parameterize-not-unify** (cf. Part 13.4), not a shared `metrics` function.
- **`logging` is n=1, not shared.** Earnings uses `print`/JSON-stdout (imports `logging` in 0 files);
  only MEIC uses stdlib logging (and its 3 setups vary). Not a core extraction; at most a MEIC-internal
  dedupe. Extract only when a 2nd module (likely a new one) adopts stdlib logging.
- **The Part 11 "grand" broker + trade-store interface is over-abstraction — deferred.** The only
  genuine cross-module duplication was the **cost model**, now shipped as `fees`. The full shared paper
  framework waits for a real 2nd paper consumer instead of being built speculatively (memory
  `paper-framework-scoping`; see the Part 11 callout).
- **Packaging naming reversed** to a single `cherrypick.*` namespace ("Option A"); the `cherrypit`
  import root is retired.
- **Kept module-local by design (single-consumer / module infra), not extracted:** MEIC's stream-cache/
  futures `_fetch_chain`; Earnings' strategy-vocabulary `sizing.py` (per-contract-max-loss); the
  dashboards themselves (MEIC 2987-line Flask app vs Earnings matplotlib/text); CLI response shaping.
- **tastytrade-mcp is NOT yet a cherrypick-core consumer** (no `src/_core` submodule). Its `risk.py` was
  the *source* of `cherrypick.core.risk` but hasn't been cut over; onboarding it is a separate effort.

**Guiding principle applied throughout:** extract on genuine cross-module duplication (≈2+ real
consumers), not on n=1 or same-name/different-math. This is why the "easy" duplicated infra
(auth…db, fees, gex) is done while the remaining items are either module-local or design efforts.

---

## Context

Started as two narrow asks (a stop-watcher for `streamer.py`; a shared DXLink library) and widened
into: *what else is duplicated across the trading repos, and what would a unified trading suite look
like where these projects are modules?* This document is the research report plus a phased roadmap.

**Decisions captured (from clarifying questions):**
- **Account topology: undecided** → the suite-wide risk/buying-power governor is designed as an
  **optional, pluggable, fail-closed** layer (off by default; switch on when the account is shared).
- **Structure: shared-core-now** → extract a `cherrypick-core` library consumed by each repo via **git
  submodule** (consistent with the earlier DXLink-lib decision); defer the monorepo question.
- **New modules in scope (all four):** shared market-calendar service, wheel/premium (CSP + covered
  calls), roll/assignment manager, reporting & alerting hub.

> **[SUPERSEDED]** Both decisions above were reversed by events. The monorepo question was decided —
> in favour of the monorepo — and the submodule mechanism it deferred was removed entirely on
> 2026-08-01 (`packages/core`, a plain editable path dependency). Of the four planned modules, the
> calendar and the reporting/alerting hub shipped; wheel and roll-manager did not; and **gex**,
> **flies**, and **streamer** shipped without ever appearing on this list.

---

## Part 1 — Findings

### 1.1 Repo/dir inventory (the pre-monorepo checkout root)
- **MEICAgent** (git) — 0DTE iron-condor + ORB agent; persistent streamer; live + paper.
- **EarningsAgent** (git) — 7 earnings strategies; once-daily; on-demand DXLink, no daemon.
- **tastytrade-mcp** (git) — MCP server wrapping tastytrade; own `credentials.py`/`session.py`; a
  useful `risk.py` (account-derived deploy limit).
- **MEICPaperTrader** — **NOT a git repo**, untracked, last touched 2026-06-29; `paper_trading.py`
  (211 lines) + `dashboard.py` superseded by MEICAgent's `src/paper.py` (743 lines) + `dashboard.py`;
  unreferenced. **Legacy — recommend archiving** (out of the suite).

### 1.2 Duplication map
| Concern | MEIC | Earnings | MCP | Note |
|---|---|---|---|---|
| `credentials.py` (keyring) | ✓ | ✓ | ✓ | **~95% identical, triplicated**; only `SERVICE_NAME` (+ MEIC legacy fallback) differs |
| `session.py` | thread-local | process-global | ✓ | ~90% identical; the difference is one flag (streamer needs per-thread; [MEIC session.py:14-36](../../meic/src/cherrypick/meic/session.py) vs [Earnings session.py:18-30](../../earnings/src/cherrypick/earnings/session.py)) |
| DXLink collectors (`_collect_events/greeks/quotes/…`) | ✓ | ✓ (+OI/volume) | — | Near-identical, already drifting ([MEIC tt.py:449-492](../../meic/src/cherrypick/meic/tt.py), [Earnings tt.py:133-192](../../earnings/src/cherrypick/earnings/tt.py)) |
| Broker commands (`get_quote`/`get_option_chain`/`get_account_info`/`execute_trade`/`list_accounts`/`secrets_*`/`get_connection_status`) | ✓ | ✓ | ✓ | 8 commands overlap verbatim in intent |
| `db.py` engine (WAL connect, upsert, argparse CLI, `daily_summary`) | ✓ | ✓ | — | Engine duplicated; **schema is domain-specific** (`ic_trades` vs `trades`, `loop_log` vs `scan_log`) |
| Fee/cost model (tastytrade schedule) | in `config.json` + `db.py get_fee_estimate` | clean `costs.py` | — | Same pricing model, two representations |
| Risk / buying-power ceiling | inline gates | `sizing.py` | `risk.py evaluate_deploy_limit` | Three takes on one concern |
| Paper-fill engine + cost haircut | `paper.py` | `db_paper.py` + `costs.py` | — | Overlapping |
| **Market calendars** (holidays/FOMC/quarterly/triple-witching) | **hardcoded `*_2026` lists in config** | **computed live** | — | Duplicated *and* can drift inconsistent |
| Dashboard render (HTML/matplotlib) | `dashboard.py` | `strategy_dashboard.py` | — | Pattern duplicated |
| Structured logging + rotation | `notify.py` | scan_log | — | Rotation pattern duplicated |
| Config + named-profile override | risk profiles | paper-trading profiles | — | Same "partial override onto config.json" mechanism |

### 1.3 Headline correctness gap — shared account, no shared budget
Both agents' entry gates (MEIC `max_concurrent_ics`/buying-power/NLV-drawdown; Earnings `sizing.py`)
**assume sole ownership of the account.** If both run live on one account, nothing coordinates total
deployed buying power → double-commit risk. The MCP's `risk.py evaluate_deploy_limit`
(`tastytrade-mcp/…/risk.py`) already measures live
`used_derivative_buying_power` and is the exact primitive a suite governor needs. This is the single
strongest *correctness* argument for a suite (gated on the account-topology decision).

---

## Part 2 — Suite architecture (shared-core-now)

Layered; `cherrypick-core` is a **library, not a service**, for anything on a loop's decision path — this
preserves MEICAgent's hard rule that entry/stop/logging depend only on local `tt.py`/`db.py`/streamer/
instructions and never gain a network failure mode.

```
Cherrypick  (suite umbrella — the whole system)

cherrypick-core/                         (new git repo; submodule as src/_core in each consumer)
  auth/        credentials (param SERVICE_NAME), session (param thread_local flag)
  broker/      shared tt commands: quote, chain, account, execute_trade(dry/live), secrets
  dxfeed/      collect_events/greeks/quotes/last_prices/open_interest/volume (session injected)
  gex/         standalone GEX engine: per-symbol net_gex/gamma_flip/call_wall/put_wall (Part 15)
  fees/        tastytrade cost model (from Earnings costs.py) + fee-estimate helpers
  risk/        evaluate_deploy_limit (from MCP) + sizing primitives  [governor: optional]
  profiles/    named-profile registry + merge engine + calibration harness + advisor (Part 10)
  paper/       synthetic-fill broker adapter + isolated paper store + paper-loop harness (Part 11)
  calendar/    holidays/FOMC/quarterly/triple-witching/earnings  [new shared service]
  db/          engine base: WAL connect, upsert, daily_summary, CLI dispatch
  logging/     structured log + rotation (from notify.py)
  viz/         shared HTML/matplotlib render kit

Suite modules (each pins cherrypick-core by SHA):
  MEICAgent      -> 0DTE IC + ORB + persistent streamer + stop-watcher (module-local)
  EarningsAgent  -> 7 earnings strategies

Non-suite cherrypick-core consumer (kept for future reuse, NOT a suite module):
  tastytrade-mcp -> interactive surface only; refactored to drop its own copy of
                    credentials/session and import cherrypick-core instead. Never on any loop
                    decision path. Not orchestrated, not capital-coordinated by the suite.

New modules (later phases): wheel/premium · roll-manager · reporting-alerting hub
Optional: suite orchestrator / risk governor (fail-closed; on only if account shared)
```

**Design invariants**
1. `cherrypick-core` imports nothing from a consumer's `src/`. Session/credentials are **injected or
   parameterized**, never reached back into.
2. Loop-path code calls `cherrypick-core` as an in-process library. The MCP and the orchestrator are separate
   surfaces; the loop never routes through them.
3. The risk governor is **fail-closed**: if it can't read live buying power, it **blocks new entries**
   rather than letting a module proceed uncoordinated.
4. Per-consumer submodule SHA pinning — no module is force-upgraded by another's changes.

> **[SUPERSEDED — invariant 4 only]** Invariants 1–3 held and are visibly the ancestors of the
> Invariants block in [`../CLAUDE.md`](../CLAUDE.md). Invariant 4 is **dead, and deliberately so**:
> the monorepo makes the opposite trade — one in-repo `packages/core` version, every package upgraded
> together, verified by one CI matrix run. Per-consumer pinning is what allowed the six submodules to
> silently drift to four different SHAs, which is the concrete failure the migration removed. The
> protection invariant 4 was reaching for now comes from CI covering all seven packages on every push,
> not from letting each one lag independently.

### What stays module-local (not shared)
- Domain DB schemas (`ic_trades` vs `trades`) and strategy logic.
- MEIC's **stop-watcher** (see Part 4) and persistent streamer — MEIC-specific; Earnings has no daemon
  by design.
- Each module's config/skills/loop.

---

## Part 3 — New modules (all four in scope)

1. **Shared market-calendar service** (`cherrypick-core/calendar/`) — *do first, highest leverage/lowest risk.*
   One source for `is_holiday`, `fomc_dates`, `quarterly_expiry`, `triple_witching`, `next_expiry`,
   earnings dates. Removes MEIC's hardcoded `nyse_holidays_2026`/`fomc_dates_2026`/
   `quarterly_expiry_dates_2026`/`triple_witching_dates_2026` config lists and unifies with
   EarningsAgent's live calendar. Both modules consume; no more annual hand-editing / drift.
2. **Wheel / premium module** (new consumer) — weekly CSP → assignment → covered-call cycle on
   indices/ETFs. Reuses `dxfeed`/`fees`/`risk`/`calendar` core. Distinct cadence from 0DTE and
   earnings; its own schema + loop.
3. **Roll / assignment manager** (cross-module service) — watches ITM shorts near expiry across every
   module's open positions and rolls out/down or closes. Especially valuable for physically-settled
   symbols (MEIC already flags this as a critical assignment risk). Reads each module's positions;
   places rolls via `broker/` (respecting the governor).
4. **Reporting & alerting hub** (new surface) — consumes every module's trade DB + loop logs for
   unified P&L / tax-lot reporting, and pushes alerts (Slack/email/SMS) on stops, fills, and failures.
   Read-mostly; the natural home for the "silent-stall" watchdog that MEIC incidents motivated. Its
   read-side UI is the **unified status/log dashboard (Part 14)** — the walk-away user's window into a
   headless system.

---

## Part 4 — MEIC stop-watcher (retained; module-local, data-plane only)

Unchanged from prior scoping and independent of the suite work. New `stream_stop_watch` cache table +
a ~3s watcher thread in `streamer.py` that joins open ICs (`ic_trades`) against leg mids already in
`stream_quotes`, computing per-side cost ratios + a **breach flag** using the exact formula in
[stop-management.md:22-27](../../meic/.claude/commands/stop-management.md). Exposed via a `stop_watch`
HTTP route + `tt.py` subcommand. **Zero trading authority; advisory-only; never load-bearing** — the
`/stop-management` skill keeps its own computation and falls back whenever the row is stale/incomplete
(`legs_complete`, `max_leg_quote_age_s`). Files: `streamer.py` (DDL + thread + route + sync reader),
`tt.py` (`cmd_stop_watch`), `config.json` (`stop_watch_poll_seconds`), `stop-management.md` pre-check,
`CLAUDE.md` tool-table row, `tests/test_streamer_cache.py`.

---

## Part 5 — Downstream problems & benefits

### Benefits
- **Fix-once** for the triplicated auth/session, the fee model, and the calendar; one place for dxfeed
  SDK quirks (the FULL/COMPACT-class fix would have been one edit, not three).
- **Shared-account safety** becomes *possible* (governor) instead of structurally absent.
- New strategies (wheel, etc.) bootstrap on a proven core instead of re-forking `tt.py`.
- Consistent logging / dashboards / alerting across modules.

### Risks / costs
- **Blast radius:** a core bug hits every module. Mitigated by SHA pinning + a strong core test suite +
  staged rollout (one consumer at a time).
- **Loop-path purity:** the shared core must remain an in-process library for loop code; routing the
  loop through the MCP or a networked governor would add exactly the failure mode CLAUDE.md forbids.
  Enforced by invariant 2 and a core lint that forbids importing consumer `src/`.
- **Governor correctness:** a buying-power governor that fails *open* is worse than none. Fail-closed
  (invariant 3), and test the unreachable-broker path explicitly.
- **Divergent needs must be parameterized, not assumed:** thread-local vs process-global session;
  daemon vs on-demand. The core exposes flags; it doesn't pick one.
- **Submodule friction:** fresh clones need `--recursive`; contributors forget `submodule update`;
  detached-HEAD commits. Call out in every consumer's setup docs/README + a CI check.

  > **[SUPERSEDED — and this risk is why the monorepo happened.]** Every failure mode predicted here
  > materialised, plus one worse than anything on the list: the six vendored copies drifted to four
  > different SHAs, so packages were silently running different core code. The mitigation proposed
  > above (docs + a CI check) was never enough. Resolved 2026-08-01 by deleting the submodules
  > outright — `packages/core` is a plain in-repo editable dependency, and a fresh clone needs no
  > `--recursive` at all. Read this bullet as the rationale for that migration, not as a live risk.

- **Account identity unknown:** credential service names differ (`meicagent`/`earningsagent`), so the
  two may point at different accounts today. The shared-budget value proposition is contingent on the
  account-topology decision — hence the governor is optional until that's settled.

  > **[SUPERSEDED]** Onboarding was redesigned around **one shared keyring login**
  > (`cherrypick-broker`), with per-module services kept only as overrides — so the two no longer
  > point at different accounts by default. See ROADMAP.md's onboarding-redesign entries.
- **Config unification is a real migration:** risk profiles vs paper profiles vs earnings config are
  different shapes; unify incrementally, not big-bang.
- **MCP scope:** `tastytrade-mcp` is refactored onto `cherrypick-core` for consistency/future reuse but is
  explicitly **not** part of the suite — it's an interactive surface only, never on a loop decision
  path, not orchestrated, and not capital-coordinated. `cherrypick-core` itself is a plain in-process library,
  not an MCP and not a network service.

---

## Part 8 — Reliability: watchers & watchdogs (research answer)

**Yes — there is a strong benefit, and the codebase already proves the pattern on one side only.**

### What exists today
- **Paper loop:** a self-healing **Windows Task Scheduler** job ([paper_loop.py:639-667](../../meic/src/cherrypick/meic/paper_loop.py))
  runs each iteration to completion, self-heals on failure, no-ops outside market hours, and survives
  process death. This is a genuine OS-level liveness watchdog.
- **Live loop:** agent-driven via `ScheduleWakeup` — **no OS-level backstop.** If the Claude session
  dies or a wakeup is missed, nothing re-fires it. That's the highest-stakes gap: a missed
  `physical_settlement_force_close_time` (15:30) / `force_close_time` (15:45) on a non-cash-settled
  position is a **critical assignment risk** by CLAUDE.md's own escalation rules.
- **Streamer:** `stale_warning`/`stale_reason` ([tt.py:1371-1374](../../meic/src/cherrypick/meic/tt.py)) + the SDK
  keepalive detect staleness, but nothing *actively restarts* a dead streamer — the agent must notice.
  This is what caused the 34h stall (2026-07-01).
- No cross-process supervisor; no heartbeat contract between modules.

### Watchdog taxonomy for the suite (four distinct jobs — keep them separate)
1. **Liveness / scheduler watchdog** — guarantees each module's loop *fires on cadence*, independent of
   any agent session. Adopt the existing paper-loop Task Scheduler pattern for the **live** loop too
   (cron/systemd-timer on other OSes). Highest priority — it closes the assignment-risk gap.
2. **Service-health watchdog** — verifies the streamer is not just PID-alive but *fresh* (writing
   events); auto-restarts on `stale_warning`. Directly targets the silent-stall class.
3. **Deadline / SLA watchdog** — asserts safety-critical actions *happened by their deadline*
   (all physically-settled positions flat by 15:30; EOD report written; NLV persisted) and alerts if
   not. This is the backstop-to-the-backstop.
4. **Reconciliation watchdog** — continuous broker-vs-DB position/BP drift check (today it's a
   read-only once-per-iteration step in 4a; promote to a standing check).

### Design principles for watchdogs
- **Simple, out-of-process, fail-loud.** A watchdog must be dumber and more robust than what it
  watches; OS-native schedulers over bespoke daemons. "Who watches the watchdog" → keep it trivial.
- **Restart infra, alert humans — do not open risk.** Watchdogs may restart the streamer and raise
  alerts freely. The only *trading* action they may take is **protective** (force-close on a missed
  deadline), under narrow, explicit, auditable, **fail-closed** rules — never open a new position.
- **Authority is risk-matched per settlement type (decided):**
  - **Cash-settled** symbols (`cash_settled_symbols`: SPX/XSP/NDX/RUT) → **alert + restart only.** No
    assignment risk exists, so the watchdog never places an order; it revives the loop and pages.
  - **Physically-settled** symbols (QQQ/IWM/equities/futures) → after a restart + short grace window
    fails to flatten, the watchdog itself submits a **marketable protective force-close** (close-only,
    never opens risk), then pages `CRITICAL`. This preserves assignment safety even if the agent stays
    down — the one case where a missed deadline has real consequences.
  - The deadline watchdog reads each open position's own `symbol` to pick the branch — same
    settlement-type dispatch the loop already uses in Step 2/Step 5.
- **Idempotent targets.** Because a watchdog may re-fire a loop iteration, iterations must be
  idempotent (see standard D below) so a re-run can't double-submit.
- **Alert-fatigue discipline:** severity tiers, dedup, and escalation — a noisy watchdog gets ignored.
- **Home in the suite:** the planned **reporting & alerting hub** is the natural supervisor for #2–#4
  (it already consumes every module's DB + logs). #1 stays OS-native per platform.

---

## Part 9 — Coding standards for a large coordinated suite (research answer)

Ground truth first: today there is **no CI, no linter, no type-checker, no formatter** in any repo
(`pyproject.toml` configures only pytest); the one explicit standard is CLAUDE.md's **"keep files under
500 lines"** with a documented-exceptions process (`docs/file-size-exceptions.md` in the earnings
module), already widely exceeded (`dashboard.py` 2980, `tt.py` 1589, `streamer.py` 1420). A coordinated
suite needs standards in three buckets:

> **[SUPERSEDED — the "ground truth" paragraph above is 2026-07-11 state, not today's.]** CI, ruff
> lint **and** format now run on every push across all seven packages, from one root `ruff.toml` the
> packages extend. Still genuinely unadopted from this section: **mypy** and **import-linter** — the
> latter matters, since it is the mechanism this document proposes for enforcing invariant 1 (core
> imports nothing from a consumer's `src/`). The file-size figures are three weeks stale.

**A. Structure & dependency direction (the coordination-critical ones)**
- Core never imports from a consumer; **consumers never import each other** — they coordinate only
  through `cherrypick-core` and shared DB/contracts. Enforce mechanically with `import-linter` (contracts).
- Stable, **versioned interfaces**: `cherrypick-core` follows semver; each consumer pins by submodule SHA;
  breaking changes are deliberate, not incidental.
- **Package over monolith**: prefer `cherrypick-core`'s sub-package layout to 1500-line files; treat the
  existing 500-line rule + exceptions doc as the suite-wide convention.

**B. Config & data**
- Config is data with **one validated schema** (validate at startup, fail-closed on invalid); unify the
  three "named-profile override" mechanisms (risk / paper / earnings) into one.
- **No hardcoded market dates** — the shared calendar service is the only source.

**C. Contracts at boundaries**
- The `tt.py`-style CLI/IPC boundaries already emit JSON; formalize with **JSON schemas** so a module
  and a watchdog agree on shape. Every command returns `{ok, ...}` (existing convention) uniformly.

**D. Determinism, idempotency, fail-closed**
- Loop iterations and force-closes must be **idempotent / re-runnable** (order idempotency keys; no
  double-submit) — this is what makes watchdog-triggered re-runs safe.
- Anything touching capital **fails closed** (block on missing data), per existing MEIC gate philosophy.

**E. Observability**
- One structured-logging standard (promote `notify.py`), a **heartbeat contract** every module writes,
  a per-iteration correlation ID, and standardized step-timing logs (MEIC already logs these).

**F. Testing & release gates**
- pytest everywhere (already the norm); **minimum coverage bar on `cherrypick-core`**; a hard rule:
  **no live-path change ships without a paper run** (mirrors the existing strategy-testing discipline).
- **Add CI** (currently absent): run every consumer's tests against the pinned core SHA on push.

**G. Tooling to adopt (the concrete gap)**
- `ruff` (lint + format), `mypy` (types on `cherrypick-core` at least), `import-linter` (layering),
  `pre-commit` hooks, and a CI workflow. Standardize `requires-python >= 3.11` (already MEIC's).

**H. Security/PII (promote existing rules suite-wide)**
- Keyring-only secrets; account-number masking to last 4; no secrets in logs/env/files — already in
  both CLAUDE.md files; make it a `cherrypick-core` convention with a shared masking helper.

**I. Time**
- One clock/calendar source; explicit ET/UTC everywhere; an **injectable clock** so deadline/watchdog
  logic is testable (`pytz` is already a dependency).

---

## Part 10 — Shared risk-profiling module (research answer)

**Yes — consolidate the profiling *mechanism* into `cherrypick-core/profiles/`; keep the profile
*definitions* per module.** Both repos already implement the same abstraction, differently:

- **MEIC** — profiles in a separate `config.risk.json`; `_merged_params()` flat-overrides base config,
  skipping `_`-prefixed keys ([paper.py:105-121](../../meic/src/cherrypick/meic/paper.py)); the paper loop runs **all
  profiles in parallel-shadow** as virtual accounts and tags every fill with `risk_profile`
  ([paper.py:291-332](../../meic/src/cherrypick/meic/paper.py)); `/set-risk-profile` flips the single **live** active
  profile.
- **EarningsAgent** — profiles inline under `config["profiles"]`; `_load_config(profile)` layers
  `strategy_defaults → per-strategy → profile`, with `strategy_overrides` deep-merge + `risk_pct_
  multiplier`/`tier_floor` specials, recording `config["_active_profile"]`
  ([scanner.py:31-77](../../earnings/src/cherrypick/earnings/scanner.py)).

Same four moving parts underneath → the shared module provides them once:

1. **Profile registry + merge engine.** Load named profiles from a standard location, validate the name
   exists, and merge onto a base config: partial override + reserved keys (`description`, `_`-prefixed)
   + named nested-override namespaces (`strategy_overrides`, and a `symbol_overrides` seam MEIC can
   use), recording `_active_profile`. Generalizes both loaders.
2. **Attribution contract.** One standard trade-tag column (`risk_profile`) written on every
   entry — MEIC's `ic_trades.risk_profile` and Earnings' `trades.profile` unify onto it.
3. **Calibration harness (metric-agnostic).** Run K profiles as parallel virtual paper accounts, tag
   fills, and produce a **comparison report** on a common metric set — win rate, expectancy net of
   costs, Sharpe, max drawdown, sample size vs target. MEIC's paper-report and Earnings'
   `strategy_metrics.py` are two implementations of this one comparison; consolidate them.
4. **Promotion advisor (advisory, human-gated).** Read the calibration DB and apply a documented rule
   (min sample, min win rate, min days) to *recommend* graduating conservative→moderate→aggressive.
   Today this is prose in [docs/risk-profiles.md](../../meic/docs/risk-profiles.md); codify it as a recommendation
   only.

**Stays per module (not shared):** the profile *definitions* and which config keys they touch (MEIC:
delta/OTM/stop/credit; Earnings: capital/tier_floor/risk_pct_multiplier), the base config schema, and
the strategy-eval logic that consumes merged params.

**Design decisions**
- **Fail-closed key validation.** The shared merge validates profile keys against the owning module's
  config schema and **rejects unknown keys** — today MEIC's flat `dict.update` would silently accept a
  typo'd key. This depends on Part 9 Standard B (one validated config schema).
- **Metric-agnostic, not tier-hardcoded.** The harness compares whatever a profile changes; it must not
  bake in MEIC's four tiers or Earnings' tiering.
- **One profile location/shape** (recommend inline `config.profiles`, with an optional external include
  for MEIC's `config.risk.json`) — a small migration.
- **Promotion stays a recommendation.** Auto-switching *live* risk from paper results is a
  capital-authority action → human/agent approves, consistent with the governor/watchdog fail-closed
  philosophy. Never auto-promote live.
- **Composes with the risk governor (Part 4):** the active profile sets *appetite within a module's
  slice*; the account governor enforces the *shared-budget ceiling*. Two independent layers.

**Downstream benefits/risks.** Benefits: one loader/validator/attribution/comparison engine; consistent
promotion discipline; new modules (wheel) get calibration for free; paper→live use the same profile
object. Risks: profiles reference module-specific keys, so the shared loader is only as safe as the
per-module schema (hence fail-closed validation); differing calibration cadences (MEIC weeks vs Earnings
quarters) mean the harness must be time/objective-agnostic; unifying the two profile locations is a
migration touching live config.

### Design research refinements (2026-07-10, from reading both merge engines)

Verified against `MEICAgent/src/paper.py::_merged_params` and `EarningsAgent/src/scanner.py::_load_config`.
Verdict: **genuine shared resource** (both implement the same abstraction; designed for future-module
reuse) — unlike logging/viz which came back n=1. Three refinements to the above:

1. **Earnings' merge is a strict superset of MEIC's** → one parameterized engine serves both:
   ```
   merge_profile(base, profile_def, *, reserved_keys, nested_namespaces, validate=None) -> effective cfg
   ```
   - MEIC = the flat top-level override step with **no** nested namespaces (`_merged_params` is exactly
     `dict.update` skipping `_`-keys).
   - Earnings adds a per-strategy `strategy_overrides` deep-merge (a `nested_namespaces` entry:
     `{"strategy_overrides": "strategies"}`); its `risk_pct_multiplier`/`tier_floor` are ordinary
     top-level keys read *after* merge. The plan's `symbol_overrides` seam is just another namespace.
   - Records `_active_profile`. Pair with a **dual-source loader** that reads profiles from *either*
     inline `config.profiles` **or** an external file — so **neither module migrates its config**
     (MEIC's `config.risk.json` is committed and asserted by `test_risk_profiles.py`; forcing inline
     would break those tests + the `/set-risk-profile` skill). This supersedes the earlier
     "recommend one location" note — support both instead.

2. **The calibration harness must be metric-AGNOSTIC — point 3 above is half-wrong.** MEIC's and
   Earnings' metrics are **domain-divergent, not two implementations of one comparison**: MEIC's Sharpe
   is annualized on a *daily return series*; Earnings' is deliberately *not* annualized on *discrete
   event trades* (its own docstring rejects √252). So the harness consolidates the **orchestration**
   (run K profiles as parallel virtual accounts, tag fills, emit a comparison table) with metric
   functions **injected by the module** — never the metric math. (This matches the "metric-agnostic,
   not tier-hardcoded" design decision; it only contradicts point 3's wording.) See the metrics note in
   the Build Status section.

3. **Fail-closed validation without waiting on Part 9 Standard B.** Interim: validate profile keys
   against the **base config's existing keys** (reject a key absent from base) — closes MEIC's
   silent-typo gap today without needing a full config schema.

**Phased scope (don't do as one lump):**
- **Phase A — merge engine + dual-source registry + interim validation.** ✅ **SHIPPED** as
  `cherrypick.core.profiles` (`load_profiles`/`select_profile`/`merge_profile`, opt-in `validate`); MEIC
  `paper.py` (`_merged_params`, `load_profiles`) and Earnings `scanner.py` (`_load_config` profile step)
  cut over, CI green. Validation capability present but left off in the cutover (needs per-module
  profile-vs-base key coverage check before enabling).
- **Phase B — attribution contract.** ✅ **SHIPPED** as `attribution_tag` — standardizes the
  `risk_profile`/`profile` trade tag (both already have the column; convention + helper).
- **Phase C — calibration harness** (couples to Part 11 paper framework). ✅ **primitive SHIPPED** as
  `compare_profiles` — metric-agnostic group-by-tag orchestration + comparison table, metrics injected
  by the module. The parallel-shadow *run* orchestration behind the paper-DB boundary is still pending.
- **Phase D — promotion advisor.** ✅ **primitive SHIPPED** as `recommend_promotion` — advisory-only
  rule (min sample/win-rate/days), human-gated, never auto-promotes live. Its *wiring* to live
  calibration data (behind the paper-DB boundary) is still pending.

Composes with the risk governor (Part 4, shipped): profile = appetite within a slice; governor =
account ceiling. Orthogonal layers.

---

## Part 11 — Shared paper-trading framework (research answer)

> **STATUS (2026-07-11): the "grand" broker + trade-store interface below is DEFERRED as
> over-abstraction.** The extraction found the only genuine cross-module duplication was the **cost
> model**, now shipped as `cherrypick.core.fees` — both modules' paper engines draw fills from it. The
> full shared paper framework (synthetic-fill broker adapter, isolated store, paper-loop harness, one
> shared gate evaluator) is **not** built speculatively; it waits for a real 2nd paper consumer (e.g. a
> new module) to justify the interface. The design below is retained as the target *if/when* that
> consumer arrives. See memory `paper-framework-scoping`.

**The original thesis — a new module gets paper trading + calibration *by construction* if it's built
against the shared interfaces.** Both repos already have the full pattern, independently reimplemented:

- **MEIC** — `paper.py` is "pure functions over an already-fetched snapshot; this module never calls
  the broker," mirroring the live Step-6 hard-stops **deterministically** (fixed policy, no agent
  judgment) so the four profiles compare on reproducible criteria; writes to an isolated
  `data/paper_trades.db`; `paper_loop.py` drives it + self-healing Task Scheduler automation.
- **EarningsAgent** — `db_paper.py` is a **deliberately separate** DB + CLI ("paper and real trade data
  must never be queryable through the same connection… no `--paper` flag on db.py"); `costs.py`
  models fills; `paper_trading_runner.py`/`strategy_test_runner.py` drive it.

### The clean abstraction: paper = same strategy logic + swapped broker + swapped store
If a module's loop is written against the `cherrypick-core` **broker interface** and **trade-store interface**,
then live-vs-paper is just dependency injection. The shared `cherrypick-core/paper/` provides:

1. **Synthetic-fill broker adapter** — implements the *same* interface as the real `broker/`, but
   computes a modeled fill from the snapshot via the shared `fees/` cost model + a slippage haircut
   instead of submitting to tastytrade. Swap it in and the identical loop runs in paper. *This is why
   new modules get paper "for free": they never hardcode the live broker.*
2. **Isolated paper store** — a parallel DB (separate file + connection, isolation enforced) mirroring
   the module's live schema + `risk_profile` tag + cost fields, built on the `cherrypick-core/db` engine.
   Preserves both repos' hard rule that paper and live data never share a path.
3. **Paper-loop harness** — drives iterations over live snapshots, runs the module's deterministic gate
   evaluator across K profiles (parallel-shadow), records synthetic fills/exits, feeds the Part 10
   calibration harness. Plus the shared self-healing scheduler (ties to Part 8).

### Module-supplied plug-ins (strategy-specific — the framework holds only the interface)
- The **deterministic gate evaluator** (MEIC's `evaluate_entry` encoding Step-6; Earnings' tiering).
- The **exit/settlement model** (MEIC: 0DTE cash settlement, pin risk, physical friction; Earnings:
  overnight hold → next-open → IV crush). Genuinely instrument-specific.
- The **order/leg builder**.

### Design principles
- **Hard paper/live isolation is non-negotiable** — separate file + connection, never a flag on the
  live store. Both repos enforce this today; the shared framework must preserve it through the refactor
  (a shared paper store that can accidentally point at the live DB is a serious footgun).
- **One gate evaluator, used by both live pre-flight and paper** — today MEIC's paper `evaluate_entry`
  is a *parallel implementation* of the live hard-stops and can drift from them. Extracting the
  deterministic hard-stop floor into a single shared evaluator (live keeps the agent's discretionary
  layer *above* that floor) removes the drift and is independently testable.
- **Determinism = comparability, and its limit:** paper applies fixed policy so profiles are compared
  reproducibly — but it therefore cannot capture the live agent's discretionary judgment. Paper
  calibrates the *policy thresholds* (the right target); it is not a prediction of live agent behavior.
  State this explicitly so calibration results aren't over-read.
- **Shared cost model** — paper fills and live fee estimates draw the same numbers from `fees/`.

### Downstream benefits/risks
**Benefits:** every new module inherits paper + calibration by construction (your stated goal);
one synthetic-fill/cost engine → consistent realism; the shared evaluator kills live-vs-paper drift;
standardized isolation lowers the paper/live-blend risk. **Risks:** the broker interface must be rich
enough that both real and synthetic satisfy it — some strategies lean on broker-specific fill behavior
(MEIC's IOC/day-improve pricing, working-order chasing) the synthetic broker must model or explicitly
approximate; exit modeling stays per-module (don't oversell "free"); converging two different paper
implementations onto one harness is real migration work; and isolation must survive that refactor.

---

## Part 12 — Setup, initialization & onboarding for a common trader (research)

**Goal:** a non-developer can download Cherrypick, connect their tastytrade account, and reach a first
**paper** trade with minimal friction — live trading only behind an explicit, deliberate gate.

### Where the friction actually is today (grounded)
- **OAuth acquisition is the #1 wall.** `secrets_set` ([tt.py:1131-1150](../../meic/src/cherrypick/meic/tt.py)) prompts
  for a `client_secret` + `refresh_token` the trader **must already possess** — obtaining them from
  tastytrade (register an OAuth app, run an auth-code flow) is beyond a common trader.
- **Developer-grade prereqs:** Python 3.11+, **Claude Code** (npm + Anthropic account), and — for the
  earnings module — a local **Dolt SQL server** + multi-GB datasets ([EarningsAgent docs/01-setup.md](../../earnings/docs/01-setup.md)).
- **Per-repo, hand-edited:** separate clones/installs; `config.json` edited by hand.
- **Already good:** the `/setup` skill's status→set→verify→account flow with **actionable error
  remediation**, and `enable_live_trading` defaulting off.

### Concept A — one entry point: the `cherrypick` CLI
A single console app (`pipx install cherrypick`) with a small verb set:
`cherrypick init` (config wizard) · `cherrypick connect` (brokerage OAuth wizard) ·
`cherrypick doctor` (preflight health check) · `cherrypick data init` (Dolt/earnings data) ·
`cherrypick service install` (scheduler) · `cherrypick start --paper`. All wizards **idempotent and
re-runnable** (no half-configured states).

### Concept B — the connect-to-brokerage wizard (the crux)
- **Best case (fully guided):** if tastytrade OAuth supports an authorization-code/PKCE **public
  client with a `http://127.0.0.1:<port>/callback` redirect**, the wizard opens the browser → the user
  logs in + consents on tastytrade → a tiny local callback server captures the code → PKCE exchange →
  `refresh_token` stored in keyring. The trader never sees a token; nothing leaves their machine.
- **Realistic fallback (needs verification — see open question):** if tastytrade only offers a
  *personal OAuth grant* (create client in account settings → copy `client_id`/`client_secret` →
  generate `refresh_token`), the wizard deep-links to the exact settings pages, walks each field, and
  auto-captures the token via the local callback if the redirect is configurable, else a clean paste
  step. A **device-code-style** path covers headless servers (show URL + code to enter elsewhere).
- Then: list accounts, let them pick, mask to last 4, verify via `get_connection_status`, and surface
  the existing remediation hints on failure.

### Concept C — safe-by-default progressive disclosure
Default to **paper mode** (no capital at risk), **conservative** profile, one cheap index (XSP),
dry-run everywhere. Flipping to live requires a deliberate **type-to-confirm** gate that acknowledges
risk — consistent with the fail-closed governor/watchdog philosophy and today's `enable_live_trading`
default-off.

### Concept D — `cherrypick doctor` (highest-leverage onboarding artifact)
A green/red preflight checklist with a remediation line per failure: Python/deps, **keyring backend**
(Linux Secret Service is the usual gap), broker connection, streamer health (`stream_status`),
scheduler registration, Dolt (if earnings), **system clock/timezone** (ET gating depends on it), and
market-calendar freshness. Systematizes the `/setup` remediation table into one self-diagnosis command
— the single biggest reducer of support burden.

### Concept E — config wizard writes validated config
`cherrypick init` interactively picks modules, symbols, risk profile, paper/live, capital, and a
notification channel (for the Part 8 alerting hub), then writes config **validated against the Part 9
schema** (fail-closed on invalid). No hand-editing.

### Concept F — guided data & service setup
- `cherrypick data init` automates the earnings module's Dolt clone/serve, or points at a hosted
  read replica so the trader needn't run Dolt locally (offer Docker **for this module specifically**).
- `cherrypick service install` registers the OS scheduler for streamer + loop (reuse the existing
  Task Scheduler pattern, add cron/systemd) — ties directly to the Part 8 liveness watchdog.

### Concept G — the Claude Code dependency lever (architectural onboarding decision)
Paper trading and the deterministic gate evaluator (Part 11) **don't need the LLM**. So a trader could
onboard and paper-trade on a **headless deterministic runtime with no Anthropic account**, adding
Claude Code only to unlock the agentic/discretionary *live* loop. This dramatically lowers the initial
barrier and is worth an explicit decision (how much of onboarding assumes Claude Code).

### Concept H — opt-in AI/dev tooling (agentmemory + graphify), pluggable or bring-your-own
The maintainer's preferred authoring setup is **[agentmemory]** (persistent file/graph memory for the
coding agent) + **[graphify]** (a code knowledge graph queried during development). Cherrypick offers
these as a **first-class opt-in**, not a hidden assumption and never a runtime requirement:
- **Onboarding surfaces it as a choice.** `cherrypick tooling init` (or a wizard prompt) offers three
  paths: **(a)** install/configure the reference agentmemory + graphify setup the maintainer uses (writes
  the hooks/`.claude` config, generates the initial graph); **(b)** **bring-your-own** — point Cherrypick
  at a different memory/graph provider behind the same thin interface; or **(c)** **none** — skip entirely.
  A fresh clone works with zero tooling; opting in is purely additive.
- **Strictly a development/authoring aid — never on a decision path.** agentmemory is an MCP and graphify
  is an external CLI, so both are barred from the deterministic paper engine, the loop's entry/stop/logging
  path, and the watchdog/notify reliability path (Part 2 invariant 2; Part 13.1 §7). They assist the human
  + the *discretionary agentic live* loop (which already depends on Claude Code, Concept G) only. This keeps
  the walk-away reliability guarantee free of any AI-tooling or network failure mode.
- **Pluggable by interface, not by vendor.** Cherrypick depends on a small `memory`/`codegraph` capability
  contract (recall/save; query/update), with agentmemory + graphify as the default adapters. This is why a
  user can swap in their own or run none — and why a module's internal memory-backend choice (e.g. MEIC
  having moved its own internal memory off agentmemory) stays independent of what Cherrypick offers at the
  onboarding layer. Both are **gitignored/local-only** (like MEIC's existing `graphify-out/`), so they never
  land in a fresh checkout unless the user opts in.

### Decisions (from clarifying questions)
- **Opt-in AI/dev tooling = agentmemory + graphify as the reference setup (Concept H)**, offered at
  onboarding with bring-your-own and none as equal options; kept off every deterministic/loop/watchdog path.
- **v1 distribution = pipx** (`pipx install cherrypick`), targeting technical traders first; standalone
  signed binary / desktop app deferred to a later version once the wizard flow proves out.
- **Paper runs headless; Claude Code optional for live.** A trader installs, connects, and paper-trades
  with **no Anthropic account** on the deterministic engine (Part 11); Claude Code is added only to
  unlock the agentic/discretionary **live** loop. This is the lowest-barrier path to first value and
  reinforces paper-as-default (Concept C).

### Distribution options (trade-offs)
- **pipx** — fastest to ship; still needs Python. Good v1.
- **Standalone signed binary** (PyInstaller/Nuitka) — no Python; per-OS artifacts; **code-signing /
  notarization** required (macOS Gatekeeper, Windows SmartScreen). Good v2.
- **Docker/compose** — bundles Python + Dolt + streamer; but Docker is itself a barrier and
  browser-OAuth/keyring in a container is awkward. Best reserved for the Dolt-heavy earnings data.
- **Desktop app** (Tauri) wrapping wizard + dashboard — friendliest for common traders; largest effort.

### Downstream benefits / risks
**Benefits:** collapses the biggest barrier (OAuth) into a wizard; `doctor` gives self-diagnosis;
paper-default + live-gate keeps new users safe; one installer across modules.
**Risks / open items:**
- **A pre-registered Cherrypick OAuth client** implies Cherrypick owns an app registration (rotation,
  rate limits, tastytrade ToS for a distributed OAuth app). Localhost-redirect + PKCE keeps user tokens
  on-device (Cherrypick never sees them) — **but only if tastytrade supports that flow** (verify).
- Standalone binaries need signing/notarization (cost + process).
- Wizards that install schedulers/services hit OS security prompts (UAC/launchd).
- **Regulatory/liability:** distributing an auto-trading tool to non-developers needs prominent
  not-financial-advice / use-at-own-risk disclaimers and the deliberate live-trading confirmation.
- Auto-update of a tool that places live orders is risky — pin versions, gate updates behind `doctor`.

**Roadmap fit:** this is a **Phase 8 — onboarding** track (after `cherrypick-core` + config schema
exist, since the wizards write validated config and reuse core auth/broker). `doctor` and the config
wizard can land early against the current repos as a preview.

---

## Part 13 — Critical instructions to inherit (review of both projects)

Reviewed both `CLAUDE.md` files. Three buckets: **shared guardrails** (present in both → promote
suite-wide, and make *enforceable* via `cherrypick-core` helpers, not just documented), **project
criticals that generalize** into suite principles, and **strategy-specific criticals that must stay
module-local** (do NOT hoist).

### 13.1 Shared guardrails — present in BOTH, promote verbatim to the suite
These are near-identical in both files; they become the Cherrypick base `CLAUDE.md` guardrail block and,
where possible, are backed by a shared helper so they can't be violated by accident:
1. **Runs on any machine/OS** — never hardcode absolute paths or machine-specific details; build paths
   from `Path(__file__)`/env/config; verify before committing. *(Enforce with an `import-linter`/CI
   check for literal paths.)*
2. **DO NOT WRITE CODE IN `CLAUDE.md`** — build/reference/guidelines only; no code, changelogs, or task
   trackers; scratchpad goes under `.tmp/` and is deleted.
3. **Never log/display account numbers — mask to last 4** (`****1234`). *(Back with the
   `cherrypick-core/logging` masking helper — Part 9 H — so masking is automatic.)*
4. **Never save working files/tests to repo root** — use `/src`, `/tests`, `/docs`, `/config`.
5. **Docs & commits from a human developer's perspective** — never include AI/co-author attribution in
   commit messages.
6. **Credentials in the OS keyring only** — never in files, env vars, or logs. *(Owned by
   `cherrypick-core/auth`.)*
7. **AI/dev tooling is opt-in and optional** — `graphify` (code knowledge graph) and `agentmemory`
   (agent memory) are the maintainer's reference setup, offered at onboarding (Part 12 Concept H) with
   bring-your-own or none as equal options; confirm availability before use and skip silently if absent.
   Never a runtime dependency — barred from the deterministic paper engine, the loop decision path, and
   the watchdog/notify path (see §13.2 no-MCP-on-loop-path).

### 13.2 Project criticals that GENERALIZE into suite principles
- **(MEIC) No MCP/network dependency on the loop's entry/stop/logging path** → suite invariant:
  loop-path code is an in-process library, never a service/MCP (already Part 2 invariant 2). The
  silent-stall history is the reason; carry the reasoning, not just the rule.
- **(MEIC) `enable_live_trading` gate defaults OFF; live-order tools require it true** → suite-wide
  safety gate; ties directly to onboarding's type-to-confirm live gate (Part 12 Concept C).
- **(MEIC) Hard stops are non-negotiable and checked first; judgment only above the hard-stop floor**
  → this *is* the shared deterministic gate-evaluator design (Part 11): one tested hard-stop floor,
  discretion layered above it only in live.
- **(MEIC) Safety-critical deadlines fail closed and escalate to CRITICAL on failure** (the
  assignment-risk escalation) → generalized by the Part 8 deadline watchdog (per-settlement-type
  authority). The specific 15:30/15:45/16:00 stays in the MEIC module.
- **(Earnings) Defined-risk only; undefined-risk/naked deliberately removed** (unmonitored blowout
  risk) → **DECIDED: not a suite-wide mandate.** Risk policy stays per-module — each module sets its
  own (EarningsAgent keeps defined-risk-only; MEIC keeps its own). The suite provides the *tools* to
  bound risk (the optional governor, defined-risk sizing helpers) but does not force a defined-risk
  guarantee across every module. Listed here as an explicitly-considered-and-declined hoist so the
  decision is on record.
- **(Earnings) Engine-vs-strategy split; add strategies without touching the shared engine** → the
  suite's core architecture standard: `cherrypick-core` is strategy-agnostic; module logic is isolated
  and pluggable (Part 2, Part 9 A).
- **(Earnings) Paper vs live data hard isolation** — separate DB/file/CLI, no shared connection; paper
  NLV is a simulated capital basis, never the real broker balance → promote to a suite-wide critical
  instruction, owned by the Part 11 paper framework.
- **(Earnings) Always check a statistic's `sample_size`** before trusting it → suite analytics standard;
  the Part 10 calibration advisor already gates on sample-vs-target.
- **(Earnings) Single source of truth per concern; do not duplicate** → the founding motivation of this
  whole consolidation.
- **(Earnings) Broker connection is still required in paper mode** — paper sources live quotes/chains;
  only order submission is skipped → important contract for the Part 11 paper framework: paper is
  *not* offline.

### 13.3 Strategy-specific criticals — keep MODULE-LOCAL (do NOT hoist)
- **MEIC: no profit-target close / do not add `profit_target_pct`** — an MEIC strategy rule, not a suite
  rule (ORB and other modules legitimately use profit targets).
- **MEIC: 0DTE-expiration hard stop, delta/OTM/credit floors, GEX placement, settlement-type EOD
  dispatch, force-close clock** — 0DTE-specific.
- **Earnings: tiering, IV/RV, winrate/DoltHub, overnight open→next-open hold** — earnings-specific.
These live in each module's own `CLAUDE.md`/config, layered on top of the shared base block.

### 13.4 Divergences to PARAMETERIZE, not unify (a caution)
The two projects deliberately differ; the suite must not force one model:
- **Persistent streamer daemon (MEIC) vs on-demand, no-daemon (Earnings).**
- **Thread-local session (MEIC, for the daemon's dual loops) vs process-global (Earnings).**
- **Intraday monitored 120s loop (MEIC) vs unmonitored overnight hold (Earnings).**
`cherrypick-core` exposes these as flags/interfaces; it never bakes in one project's choice.

### 13.5 Mechanism — make guardrails enforceable, not just documented
`cherrypick-core` ships (a) a **base `CLAUDE.md` guardrail block** each module includes/extends, and
(b) the **helpers that make the rules automatic**: keyring-only credential access, an account-number
masking log filter, `Path(__file__)`-relative path utilities, and the config-schema validator (Part 9).
Documentation states the rule; the helper prevents the violation.

---

## Part 16 — Testing strategy (research)

**What already exists (build on, don't replace).** Both modules have real pytest suites: **MEIC**
`tests/` — `test_gex_math`, `test_paper_engine`, `test_risk_profiles`, `test_streamer_cache`,
`test_credentials`, `test_session`, `test_db`, `test_notify`, `test_tt`, `test_dashboard`; **Earnings**
`tests/` — per-strategy tests, `test_costs`, `test_scanner`, `test_rank_strategies`,
`test_strategy_test_runner`, `test_db_paper`, `test_sizing`, `test_strategy_metrics`, plus `conftest.py`.
Strong per-module unit culture. **Gaps:** (1) **no CI** in either repo; (2) no cross-module **contract**
tests at the seams Cherrypick depends on; (3) no **reliability/failure-injection** tests; (4) Cherrypick
had none until this build.

> **[SUPERSEDED — gaps (1) and (4) are closed.]** CI runs all seven packages on every push (~1,955
> tests). Gap (2) was closed differently than proposed here: rather than contract tests at the CLI
> seams, coverage is enforced by `tests/test_schema_registry.py`, which fails if a schema is wired
> into some surfaces but not others. Gap (3), **reliability/failure-injection**, is still open — and
> the tier-2 argument below for why it is the highest-value tier for this project specifically still
> stands.

**Guiding principle.** Correctness lives in the deterministic cores (pure functions over snapshots:
`paper.py`, `gex`, the gate evaluators, `first_json`, `timeutil`), so that's where tests concentrate;
broker/scheduler/OS boundaries are **mocked**, and anything that touches the live broker/Dolt/Task
Scheduler runs only in an **opt-in lane**, never default CI. This mirrors the architecture and the prime
directive (the walk-away reliability path must be *provably* correct, not just exercised by hand).

**The pyramid (bottom = most tests):**
1. **Unit — pure cores (fast, no I/O, the bulk).** Gate evaluators, synthetic-fill paper engine, fee/cost
   model, GEX math (already `test_gex_math` → reuse as the Part 15 golden-master), profile-merge,
   calendar, `first_json`, `timeutil` (inject the clock), config resolution. Property: same snapshot →
   same decision.
2. **Reliability / failure-injection (highest value for THIS project).** Drive the watchdog state machine
   with fakes (fake `Notifier`, injected clock, fake task-registry, temp state file) and assert: missing
   task → CRITICAL + notify; in-session stale DB → WARN; dead streamer → restart attempted + WARN; Dolt
   down → WARN; entry-SLA missed → CRITICAL; and the notify **de-dup** (no re-notify inside the window),
   **re-notify** (after `renotify_minutes`), and **recovery** (one INFO on return to OK). The manual
   delete-task→CRITICAL→notify test run during Stage 0 becomes an automated test. Notifier: the **log
   floor is always written even when a push channel raises**.
3. **Contract tests — the seams between Cherrypick and sibling CLIs.** Cherrypick couples to modules by
   JSON: `paper_loop.py --status/--once`, `streamer.py --status`, `strategy_test_runner run_closes`,
   `tt.py get_connection_status/get_gex`. Golden-schema tests assert the exact shape Cherrypick parses —
   the `streamer --status` "Extra data" bug hit during Stage 0 is precisely this class. Live in each
   module's suite so a module-side change that would break the orchestrator fails at the source.
4. **Integration / smoke (opt-in, marked).** `install → watchdog → uninstall` against a throwaway task
   namespace (`Cherrypick-Test-*`); broker/Dolt-touching runs in a nightly/manual lane only.

**Tooling & mechanics.**
- **pytest** throughout; shared `conftest.py` fixtures — temp SQLite paper DB, injected/frozen clock,
  captured JSON snapshots as golden files, fake broker/scheduler.
- **Determinism:** no wall-clock in logic (`timeutil.now_et` already isolates it → inject in tests); seed
  randomness; freeze snapshots; no network in the unit/reliability lanes.
- **Markers & lanes:** `unit` (default), `live` (broker/Dolt), `windows` (schtasks). CI runs `-m "not
  live"`; the `windows` lane runs on a Windows runner.
- **Static analysis as tests — the invariant guards.** **import-linter** contracts encode the two
  architectural invariants as *enforced* rules: (a) `cherrypick-core` imports nothing from any consumer
  `src/`; (b) no MCP/network import on the loop/watchdog decision path. **ruff** + **mypy** for lint/types.
  These catch invariant drift no unit test would.
- **CI (fills the #1 gap):** GitHub Actions — Linux job (unit + reliability + lint + type + import-linter)
  on every push; a Windows job for `windows`/contract tests; `live` excluded (or a manual self-hosted
  nightly holding the keyring). Merges gate on the non-live lane.
- **Coverage:** high on pure cores + the reliability state machine; thin subprocess shims are covered by
  contract/integration, not chased for line coverage.

**Roadmap fit.** Cherrypick gets a **starter `tests/` now** (this build): `first_json`, `timeutil`, the
watchdog notification state machine, and the notifier log-floor guarantee — no broker, no network.
Cross-module **contract tests** land with the `cherrypick-core` extraction (Phases 1–4, when the seams are
formalized). **import-linter + ruff + mypy + CI** are **Phase 6c (Part 9 standards)** — promoting the
invariants from *documented* to *enforced*, starting with the two above.
