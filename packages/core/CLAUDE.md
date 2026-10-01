# cherrypick-core

Shared library for the suite, consumed by every package as `cherrypick.core.*`. See
[README.md](README.md) for the design invariants and layout; [CUTOVER.md](CUTOVER.md) is the frozen
record of the original submodule cutover.

**This monorepo is the source of truth.** Core was landed from the standalone `cherrypick-core` repo
by `git subtree add` (history preserved); that repo is archived read-only and there is nothing to keep
in sync. The one out-of-repo consumer, `tastytrade-mcp`, pins the standalone repo's last SHA or
vendors a copy — it gets no in-place update.

## Stay import-self-contained

**The core imports nothing from a consumer.** Everything a consumer supplies is injected or
parameterized, never reached back into; a new core module imports only `cherrypick.core.*` and the
standard library. This keeps `git subtree split --prefix=packages/core` a byte-identical reproduction
of the standalone repo — the escape hatch if core ever has to be split out again.

**Do not use that split as the check — it segfaults on this machine** (exit 139 under Git Bash, git
2.44.0.windows.1, at commits predating any change), so a failure there says nothing about yours. Check
the invariant directly:

```bash
test -f packages/core/cherrypick/__init__.py && echo VIOLATION || echo "PEP 420 intact"
grep -rn "cherrypick\.\(meic\|flies\|calendars\|pmcc\|gex\|earnings\|orchestrator\|streamer\|console\|review\|advisor\|desk\|overview\)"   packages/core/cherrypick/ --include=*.py    # must print nothing
```

If the split must ever run for real, run it where `git subtree` works.

## Layout stays flat, not src-layout

`packages/core/cherrypick/` sits directly under the package root, unlike the other packages, on
purpose: it matches the standalone repo so the split needs no path rewriting, and `tests/conftest.py`
bootstraps `parents[1]` onto `sys.path`, which depends on it. **`cherrypick/` must never gain an
`__init__.py`** — it is a PEP 420 namespace package that composes with every module's
`cherrypick.<module>` under one import root, and an `__init__.py` would break that for every consumer
at once. Accepted cost: `tests/` would be a top-level importable name if `packages/core` ever landed
directly on `sys.path`; nothing does that (core is always reached as an installed distribution).

## The module map

One line each, no signatures — the docstring at the top of each module is the reference; this is the
index that tells you which one to open.

| Module | What it owns |
|---|---|
| `home` | The one resolver for the per-user cherrypick home; every path derives from it. Also `heartbeat_path()` and `halt_flag_path()` — the suite's live kill switch, written by the orchestrator and polled by every live loop, none of which may import each other. |
| `db` | SQLite connection mechanics + additive migrations, including the shared read-only opener. |
| `logs` | One line format for every module log. |
| `jsonio` | `write_json_atomic`: the one write-then-rename for JSON artifacts a reader must never see half-written (advice, stream requests, regime cuts, market regime, the advisor pack, the review fact set). `indent`/`default` are its only knobs because they are the only differences that change the bytes. |
| `calendar` | The suite's single source of trading days, holidays and early closes (`session_close_hhmm`: 13:00 on July 3, the day after Thanksgiving and Christmas Eve when each trades, else 16:00). |
| `fees` | The tastytrade cost model. Every "net" figure in the suite goes through it. |
| `auth` | Keyring credentials + a lazy OAuth session, parameterized per consumer. `CredentialStore.designated_account()` is the one reading of the live account (unset and unreadable are both None). The session factory stamps `User-Agent: cherrypick/<version>` on the HTTP client every token mint and refresh uses, as tastytrade requires. |
| `broker` | Account resolution, option-chain helpers, and the live write path with its governor. `place_order` and `replace_order` share one `_preflight_then_submit` path (governor included); `tests/test_broker.py` scans every package for a `dry_run=False` outside this module. `serialize` is the one SDK-object flattener and the seam's default. |
| `execution` | The one live broker ADAPTER and the fill primitives every live loop shares: `Broker` holds one session/account on one process-wide event loop and re-checks the module's injected `live_gates` on every live submit, failing closed; `order_id_of`/`fill_state` read a placement result and a status row; `orphans` is broker truth against ledger belief; `watch` is the cache-gated fill-watch loop. **Every live submission carries an `external_identifier`, and an uncertain outcome is recovered by it** — the broker does not deduplicate retries, so after a raised submit `Broker.place` reads `broker.orders_today` (terminal orders included) and returns a found order as `recovered: True` rather than a failure a caller would retry into a duplicate. **Absent and unreadable are different answers**: if that read-back fails the outcome is `uncertain: True` and the adapter HOLDS, refusing every later live submission until a read succeeds; an order found placed but recorded nowhere is named and refused until `acknowledge()`. A dry run is never held. The meic and earnings CLIs, which call the seam directly, do the same recovery (tastytrade's idempotency-and-retries guide). Cancel-and-replace re-fetches the order and branches on its status, and never places a second order over one it could not cancel. Modules keep their spec builders, gates, ledgers and loops. The desk is deliberately not built on it; flies' burst watcher and the orchestrator's desk notifier keep their own fill-watch, and `watch` exists so the next module copies neither. Fenced off from the advisor beside `broker`. |
| `risk` | Account-level risk primitives. Fail-closed and opt-in. |
| `live` | The per-day arm record and dead-man's switch a live loop runs under: the filename convention the supervisor mirrors, the record's contents, the two disarm reasons, the supervisor-heartbeat read, and the rule that under a supervisor arming is a record write and nothing else. Also the quarter-end live rule's one reason string and arm-time warning (`QUARTER_END_REASON`, `quarter_end_warning`). |
| `settlement` | American physical settlement's share arithmetic (calendars/pmcc) and the official index close (`official_index_close`: tastytrade → Yahoo → Barchart, with `OFFICIAL_SOURCES` saying which answers a cash-settled ledger may settle on). |
| `spreadbook` | The ledger writes calendars, pmcc and curve share over their `LedgerStore`: the traded close, share disposal, exit-cost accumulation and finalization (`SpreadBook`), plus the exit spread gate (`exit_spread_blocks`). Entry and `settle_expiring_legs` stay in the modules because they genuinely differ; bwb keeps its own four-leg writer. |
| `entry` | Entry-permission rules MEIC and flies must apply identically: cadence and the leg-sign rule. |
| `structures` | Pure option-structure formulas earnings and calendars must agree on (the straddle-based expected move), and the nickel tick rounding every live order builder uses (`tick_floor`/`tick_ceil`). |
| `streamer` | The generic persistent DXLink streaming engine; `packages/streamer` is the daemon around it. |
| `streamcache` | The shared stream-cache schema and its SQLite helpers — the producer/reader contract. |
| `streamrequests` | The subscription registry: how a module declares its symbols, plus the union read the streamer subscribes from and the orchestrator checks staleness against. |
| `dxfeed` | On-demand DXLink event collectors, for a snapshot rather than a stream. |
| `gex` | The GEX engine: a pure function over an option-chain snapshot. Copying it once let the math drift ~75×. |
| `profiles` | The named risk-profile registry and merge engine — how a partial override becomes an effective config. |
| `metrics` | The shared calibration metric bundle. Its CLI (`python -m cherrypick.core.metrics read`) groups a stamped advised row under `<tag>@<experiment_id>` — display only; `profile` on the record is untouched. |
| `advice` | Bounded, expiring, deterministically-validated parameter advice; see the next section. |
| `regimecuts` | The regime-cuts artifact contract: per book × regime dimension × bucket, era-scoped by a module's `measurement_breaks`. `era_bounds` (latest book-wide break on or before the session starts the era; a later book starts at its own break; future-dated breaks listed and ignored; `partial_session` never bounds), `assemble` (`thin` on every cell under three sessions, deterministic order), `write_artifact` (dated file always, latest copy only for a newer session; `write_json_atomic` is re-exported from `jsonio`). Robustness stamps from the writer's per-session `session_nets` (never published): `fragile` + `robustness` per cell (one session ≥ `CONCENTRATED_SHARE` of flow, or dropping any session flips the sign; seeded session bootstrap), `paired` per dimension (same-session exact sign test — a bucket that wins pooled but not paired is a kind of day, not a kind of entry), `history` from prior snapshots, and a document-level `multiplicity` count. Modules keep their own `by_regime`; readers recompute nothing. Exists because a seven-session cell, and later an eleven-session cell that was half one day, each looked like a finding. |
| `ledgers` | Per-schema readers for every module's ledger — the one home for net, cost, capital and session rules. `concentration` says how much of a module net rests on one arm and whether removing it flips the sign (if so, it measures that arm, not the module). Every closed record carries `experiment_id` (None on older ledgers). |
| `regime` | The one at-or-before, staleness-bounded join against the recorded market-regime series (gex's history DB). Derived ratios are computed at read time, never stored. |
| `viz` | A declarative dashboard-section contract plus one generic renderer. |

**The bar for putting something here is that two packages would otherwise disagree** — on what a fee
is, what a trading day is, what "net" means. A helper only one package uses belongs in that package.

## The `advised:<experiment>` mechanism

`cherrypick.core.advice` is the contract behind the AI advisor's one loop back into the trading
modules. `packages/advisor` reads a module's facts up to four times a trading day and, with a
validated proposal, writes a **bounded, expiring paper-advice artifact** naming one or more
experiments. Each consuming module opens a synthetic **`advised:<experiment name>` book** — a full
paper position stream planned from the same entry as `control`, running BESIDE it under the proposed
parameters. The advisor can influence a paper result; it never touches a control book, an order, or
the live path. Both the orchestrator and the module loop validate through this same code.

- **Off by default, twice over.** The suite must schedule the advisor (`scripts/advisor_checkpoint.py`)
  and the module must declare `advice.enabled` plus its own `advice.bounds` — closed ranges the
  proposal is validated against. A bound over a parameter the module doesn't read, or a range that
  can't move the book from control, is a spent experiment slot, not a safe default.
- **Every module's `advice` block is its own**, never inherited from another module's bounds.
- **One book per experiment.** The artifact and decision carry an `experiments` list; each validated
  entry opens its own slug-safe `advised:<experiment name>` book (`advised_tag`, `slug`). An artifact
  or decision from before 2026-09-17 reads as one legacy `advised:<base>` entry, and modules still
  read that tag by name for history.
- **`advised_books(decision)`** is what every consumer opens one book per; **`stamp_for(book,
  decision)`** is the one rule that puts `experiment_id` on an advised row and never on control's
  (`experiment_for` resolves it per book; older artifacts fall back to parsing the advisor stamp).
- **`session_decision` is the read-once rule, and a baseline decision is never persisted** — a process
  reaching the day with an advice-less config must not fix that day for the loop that runs after it
  (meic and earnings each once lost their most informative session that way).

A module's own experiment names, `advice.bounds` and book naming live in that module's CLAUDE.md.

## Commands

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

## Guardrails

`auth` and `broker` are where account numbers and credentials flow, and every package imports this
one, so a slip here surfaces everywhere: mask account numbers to `****1234` in logs, docs and commit
messages without exception.
