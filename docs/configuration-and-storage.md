# Configuration & storage

Where settings live, how paths resolve, and what each part of the suite reads and writes.

## The managed home

All runtime state lives under one per-user home, resolved by `cherrypick.core.home` and relocatable
wholesale with **`$CHERRYPICK_HOME`**. Nothing runtime lands in a source checkout.

```
~/.cherrypick/
  config.json                     # orchestrator config
  config/meic.json                # MEIC engine config (home-first; else in-repo config.json)
  config/earnings.json            # Earnings engine config
  data/meic/paper_trades.db       # MEIC paper ledger (ic_trades)   ← orchestrator reads this
  data/meic/meic_trades.db        # MEIC live ledger (never touched by the orchestrator)
  data/meic/stream_cache.db       # DXLink streamer cache (quotes/greeks/OI)
  data/earnings/paper_trades.db   # Earnings paper ledger (trades)  ← orchestrator reads this
  data/earnings/earnings_trades.db# Earnings live ledger
  logs/                           # suite logs + eod-digest-<day>.md + eod-insight-<day>.md
  logs/meic/                      # MEIC logs + paper-eod / eod-analysis / (live) eod-<day>.md
  logs/earnings/                  # Earnings logs + paper-eod / eod-analysis
  logs/archive/<YYYY-MM>/         # monthly zipped reports + rotated logs (one zip per scope)
  state/                          # watchdog state + heartbeats
  dashboard.html                  # static dashboard render
```

## Config model

Three config layers, all machine-local and gitignored (only the `config.example.json` templates are
tracked):

| Config | Owned by | Sets |
|---|---|---|
| `~/.cherrypick/config.json` | Orchestrator | Which modules are enabled + their `path`; the per-module `paper` block (`paper_db`, `trade_schema`, task names, entry/exit times); `watchdog`, `trade_notify`, `notify`, `eod_digest`, `log_archive`, `eod_insight`, `reconcile`; timezone. |
| `~/.cherrypick/config/meic.json` | MEIC | `symbols`, delta/VIX bands, wing widths, credit floors, entry/exit windows, stop policy, regime thresholds, cash-settled set, risk profiles. |
| `~/.cherrypick/config/earnings.json` | Earnings | `available_capital_paper_mode`, position caps, entry/close windows, correlation block list, liquidity gates, per-strategy tuning, named profiles. |

**Resolution rules:**
- A module `path` in the orchestrator config is resolved **relative to the config file's directory** /
  the source anchor (e.g. `../meic`) — never hardcode absolute paths.
- A module's config is resolved **home-first** by its `paths.py` (`~/.cherrypick/config/<engine>.json`),
  falling back to the in-repo `config.json` until an explicit `migrate-home`.
- Env overrides (mainly for tests / a machine escape hatch): `CHERRYPICK_HOME` relocates everything;
  `MEIC_DATA_DIR` / `EARNINGS_DATA_DIR` and `MEIC_LOGS_DIR` / `EARNINGS_LOGS_DIR` relocate a single
  module's data/logs; `MEIC_DB_PATH` points db.py at a specific DB (used by the paper engine).

### Orchestrator scheduling knobs (defaults)

| Block | Default | Task |
|---|---|---|
| `watchdog` | on, ~10 min | `cherrypick-watchdog` |
| `trade_notify` | on, ~2 min | `cherrypick-trade-notify` |
| `eod_digest` | **on**, 16:15 | `cherrypick-eod-digest` (runs `notify-eod`) |
| `log_archive` | **on**, day 1 @ 03:30 | `cherrypick-log-archive` (monthly) |
| `eod_insight` | **off** | `cherrypick-eod-insight` (daily 16:20; needs Claude Code) |

Each is opt-out (or opt-in for `eod_insight`) via its `enabled` key, then re-run `install`/`uninstall`.
Full annotated examples live in `packages/orchestrator/config.example.json`.

## Databases & schemas

Each module keeps **separate paper and live SQLite databases** (same schema, wholly separate files) so
paper and live data are never queryable through one connection.

**MEIC — `ic_trades`** (one row per iron condor, PK `ic_order_id`): trade_date, entry/exit times, symbol,
put/call strikes, wing_width, put/call/net credit, quantity, greeks at entry (put/call/long deltas),
underlying price at entry, IV rank, session/skew/price-action signals, stop state, exit_reason, pnl, fees,
risk_profile. Companion tables: `ic_spread_legs` (per-side exits), `daily_summary`, `loop_log`, and
`market_context` (per-day VIX/VIX1D/per-symbol snapshot for the analysis report).

**Earnings — `trades`** (one row per position, PK order ID): strategy, symbol, expiration, legs_json,
entry_credit/exit_debit, pnl (**kept gross** — costs live separately), opened_at/closed_at, profile,
quantity, capital_at_risk (defined max loss), entry_cost/exit_cost, entry_context JSON
(iv_rv/skew/winrate), entry_iv/exit_iv (→ IV crush). Companion tables: `trade_legs`, `scan_log`,
`daily_summary`, `market_context`.

> **Two couplings the orchestrator depends on — don't change silently:** each module's **paper DB path**
> + **schema** (read through the `meic_ic` / `earnings` adapter), and its **keyring service** + live
> account designation (used by `connect`/`account`/`reconcile`). Renaming a DB or altering a schema
> breaks cross-module `report`/`calibrate`.

## Report & log files

Deterministic per-session outputs (see [reporting-and-dashboard.md](reporting-and-dashboard.md)):
`logs/<mod>/paper-eod-<day>.md`, `logs/<mod>/eod-analysis-<day>.md`, `logs/eod-digest-<day>.md`, and
(opt-in) `logs/eod-insight-<day>.md`. Rotating `.log` files use size-based rotation (`*.log.N`); the
monthly `archive` task zips finished-month reports + rotated logs into `logs/archive/<YYYY-MM>/`.

## Credentials

Every secret lives in the **OS keyring** (Windows Credential Manager/DPAPI, macOS Keychain, Linux Secret
Service) — never in files, env vars, or logs. Broker OAuth tokens are stored under each module's
`keyring_service`; Slack/Discord webhooks under the orchestrator (`secrets-set`). See
[guardrails-and-modes.md](guardrails-and-modes.md).
