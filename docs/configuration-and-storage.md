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
  config/flies.json               # Flies engine config
  config/gex.json                 # GEX dashboard config
  config/streamer.json            # standalone streamer config
  config/console.json             # unified console UI config
  config/desk.json                # manual trading desk config (own credential + PIN)
  data/marketdata/stream_cache.db # the canonical shared DXLink stream cache (quotes/greeks/OI) —
                                  #   written ONLY by the standalone streamer, read by every module
  data/meic/paper_trades.db       # MEIC paper ledger (ic_trades)   ← orchestrator reads this
  data/meic/meic_trades.db        # MEIC live ledger (read only by `report --live`)
  data/earnings/paper_trades.db   # Earnings paper ledger (trades)  ← orchestrator reads this
  data/earnings/earnings_trades.db# Earnings live ledger
  data/flies/paper_trades.db      # Flies paper ledger (fly_positions / fly_books)
  data/flies/live_trades.db       # Flies live ledger (the live pilot writes here; armed per day)
  data/calendars/paper_trades.db  # Calendars paper ledger (dc_positions / dc_legs / dc_marks)
  data/pmcc/paper_trades.db       # PMCC-99 paper ledger (pmcc_positions / pmcc_legs / pmcc_marks)
  data/gex/gex_history.db         # GEX spot trail + regime history
  logs/                           # suite logs
  data/review/                    # eod-<day>.json fact sets + renders + notes
  logs/meic/  logs/earnings/  logs/flies/  logs/gex/  logs/streamer/   # per-module logs + EOD reports
  logs/archive/<YYYY-MM>/         # monthly zipped reports + rotated logs (one zip per scope)
  state/                          # watchdog state + heartbeats + advice/ + halt-live.flag (when set)
  state/stream_requests/          # per-module streamer subscription requests (each module writes its own)
  dashboard.html                  # static dashboard render
```

## Config model

One config file per package, all machine-local and gitignored (only the `config.example.json`
templates are tracked). The orchestrator's is the only one that describes other packages; the rest
configure their own engine and nothing else:

| Config | Owned by | Sets |
|---|---|---|
| `~/.cherrypick/config.json` | Orchestrator | Which modules are enabled + their `path` and `live_db`; the per-module `paper` block (`paper_db`, `trade_schema`, task names, entry/exit times) and `calibration`; the top-level `streamer` (the standalone producer) and `services` (background daemons like the gex recorder); `watchdog`, `trade_notify`, `eval_activity`, `notify`, `review`, `data_epoch`, `log_archive`, `reconcile`, `dashboard`; timezone. |
| `~/.cherrypick/config/meic.json` | MEIC | `symbols`, delta/VIX bands, wing widths, credit floors, entry/exit windows, stop policy, regime thresholds, cash-settled set, deploy-limit pct (risk profiles live in the repo's `config.risk.json`). |
| `~/.cherrypick/config/earnings.json` | Earnings | `available_capital_paper_mode`, position caps, entry/close windows, correlation block list, liquidity gates, per-strategy tuning, named profiles. |
| `~/.cherrypick/config/flies.json` | Flies | `symbols`, wing/increment scaling, entry gates and floors, the experiment `arms`, and the `live` block for the narrow live pilot (armed per day via `/live-flies-start`, one arm / one symbol / one incomplete position, self-disarming at `live.disarm_time`). |
| `~/.cherrypick/config/gex.json` | GEX | `symbols`, the shared stream-cache source path, serve host/port, history DB path. |
| `~/.cherrypick/config/streamer.json` | Streamer | Broker session settings and the stream-cache path it writes; the symbol set is not configured here — it is the union of every module's `state/stream_requests/` file. |
| `~/.cherrypick/config/console.json` | Console | Serve host/port (`127.0.0.1:5070`) and which modules' read models to surface. No credential of its own — it reads the shared suite entry and never writes one. |
| `~/.cherrypick/config/desk.json` | Desk | Its own authorization for discretionary live orders — which module's keyring service to borrow a session from (`broker_keyring_service`), the allowed accounts, and the policy gates (defined-risk requirement, per-order cap). It stores no broker secrets; the PIN is kept only as a salted verifier. Deliberately independent of every module's `enable_live_trading`. |

**Resolution rules:**
- A module `path` in the orchestrator config is resolved **relative to the config file's directory** /
  the source anchor (e.g. `../meic`) — never hardcode absolute paths.
- A module's config is resolved **home-first** by its `paths.py` (`~/.cherrypick/config/<engine>.json`),
  falling back to the in-repo `config.json` until an explicit `migrate-home`.
- Env overrides (mainly for tests / a machine escape hatch): `CHERRYPICK_HOME` relocates everything;
  `MEIC_DATA_DIR` / `EARNINGS_DATA_DIR` and `MEIC_LOGS_DIR` / `EARNINGS_LOGS_DIR` relocate a single
  module's data/logs; `MEIC_DB_PATH` points db.py at a specific DB (used by the paper engine).

### Orchestrator scheduling knobs

Since the 2026-08-09 supervisor cutover the OS scheduler holds **exactly one** cherrypick entry
(`cherrypick-supervisor`). Everything below is a **supervisor job**, re-derived from this config on
every supervisor pass by `orchestrator/jobspec.py` — so changing a cadence is a config edit that takes
effect on the next pass, with no `install` step and no scheduled task to register. The job ids are what
`cherrypick status` lists.

"Default" below means what `config.example.json` ships; your own `config.json` may differ.

| Block / source | Enabled by default | Supervisor job |
|---|---|---|
| `watchdog` | on | `watchdog` |
| `streamer` | on (in-session liveness probe) | `streamer-health` |
| `trade_notify` | on | `trade-notify` |
| module `paper`, `tick_interval_seconds` ≥ 60 | on | `<module>-paper` (short-lived tick) |
| module `paper`, `tick_interval_seconds` < 60 | on | `<module>-paper` (the module's own resident `--interval` loop, in-session only, restarted on death and on `silence_seconds` of log silence) plus `<module>-paper-offsession` (60 s ticks outside the session, so settlement and retries keep their shape) |
| module `paper` (kind `cherrypick_scheduled`) | on, entry 15:45 / exit 09:45 ET | `<module>-entry`, `<module>-exit` |
| module `paper` (kind `self_healing`) | on, every `tick_interval_seconds` | `<module>-paper` (earnings uses this since 2026-08-12) |
| `paper.dolt_service` | on | `<module>-dolt` (keep-alive) |
| module `live` | **off** until armed | `<module>-live` |
| `review` | **on** | `review-provisional` 16:30 ET, `review-final` 10:15 next morning, trading days only |
| `advise` | **off twice** (suite + per-module), event-driven with the digest | *no job* (same event) |
| `symbol_watch` | **off**, daily 06:30 when enabled | `symbol-watch` |
| `log_archive` | **on**, day 1 @ 03:30 | `log-archive` (monthly) |
| `reconcile.schedule` | **off** by default, daily 16:30 when enabled — worth turning on once any module trades live, since it diffs the live ledger against the broker | `reconcile` |

Cadences are deliberately not restated here — each job's interval is the value of its own config key
(`watchdog.interval_minutes`, `trade_notify.interval_seconds`, a module's `paper.tick_interval_seconds`,
and so on), and a table that repeats them is a second place for them to go stale. Read the current
values from `packages/orchestrator/config.example.json`, which annotates every one. The complete
verified job inventory with commands is in [operations.md](operations.md).

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

## The settings surface

`cherrypick settings` (loopback `:8804`, see [guardrails-and-modes.md](guardrails-and-modes.md) for the
security posture) is a local web editor for every config file above, plus a keyring secrets manager. It
is the suite's one config-writing *engine* outside `init`'s never-clobbers scaffold, so its write paths
are conservative by design:

- **Field edits never re-serialize the file.** Every config here documents itself in its own data
  (`_note`/`_comment` strings, `*_header` section markers, a deliberate key order) — a normal
  load→`json.dumps`→save round trip would silently erase all of it. Instead, editing one value locates
  its exact byte span by JSON pointer and splices in the new JSON encoding, so a one-field edit is a
  one-line diff and everything else in the file is untouched. A "Raw" tab is available for edits the
  form view doesn't expose; it writes the client's text verbatim after validation.
- **Every write is backed up first.** A timestamped copy of the previous file goes to
  `state/config-backups/<target>.<timestamp>.json` before the new version replaces it (atomic
  tmp-then-`os.replace`, same idiom as the dashboard renderer).
- **Guarded fields are read-only, in both directions.** `enable_live_trading` (meic/earnings),
  flies' `live.enabled` and `live.gate0_confirmed` (a human attestation string), and the live
  loss/deploy-limit fields cannot be changed from this surface — arming or de-risking live trading
  stays on its existing deliberate path (`/live-flies-start`, hand-editing the live gates with the plan
  doc open). The UI shows each locked field with a pointer to where it's actually changed; a direct API
  call to the same pointer is refused server-side too.
- **`--organize [target] [--apply]`** reorders a live config's top-level keys to match its
  `config.example.json`'s section order — inserting the example's `*_header` markers, appending any
  keys the example doesn't know about at the end, and changing no value. Dry-run by default; the applied
  write goes through the same backup/atomic path as any other save. This is what brought every shipped
  config (and this repo's `config.example.json` files) into the section layout above.

### The console's Config page

The console's Config page is a second **front-end** to that same engine, not a second engine. It
reaches it as a subprocess (`python -m cherrypick.orchestrator.configcli` — one JSON request on
stdin, one JSON response on stdout), so every property above holds there unchanged: the guarded
fields are refused identically, each save is one backup and one atomic write, and a file that moved
under the page comes back as a conflict rather than a clobber. The console holds no splicing or
guard logic of its own, deliberately — a second copy of a live-safety rule is one that can drift.

Two things are different, both about scope rather than mechanism. The page offers an **allow-list**
of fields rather than the whole document — the settings that change between sessions (experiment
arms and risk profiles, module enablement and symbols, entry windows and cadences, alert routing),
because the rest are decided once and a page that offers everything equally makes the rare edit as
easy to reach as the routine one. And it surfaces the **suite halt flag** as its headline control,
with asymmetric friction: setting it is one click, clearing it takes a typed `RESUME LIVE`
confirmation. Clearing it arms nothing on its own — every per-module gate still applies, and flies
still needs its per-day arm record.

## Report & log files

Deterministic per-session outputs (see [reporting-and-dashboard.md](reporting-and-dashboard.md)):
`data/review/eod-<day>.json` and its renders. Rotating `.log` files use size-based rotation (`*.log.N`); the
monthly `archive` task zips finished-month reports + rotated logs into `logs/archive/<YYYY-MM>/`.

## Credentials

Every secret lives in the **OS keyring** (Windows Credential Manager/DPAPI, macOS Keychain, Linux Secret
Service) — never in files, env vars, or logs. Broker OAuth tokens are stored under each module's
`keyring_service`; Slack/Discord webhooks under the orchestrator (`secrets-set`). The standalone
follow-feed-notifier's entries (`discord_follow_webhook`, `lossdog_client`) share the same
`cherrypick-notify` service name for historical reasons but are managed only by that repo's own
CLI. See [guardrails-and-modes.md](guardrails-and-modes.md).

> **Moved out (2026-08-21):** the tastylive Follow Feed and Lossdog VIP feed notifiers — code,
> settings, card rendering, scheduling — live in the standalone `follow-feed-notifier` repo
> (`~/Claude/follow-feed-notifier`), scheduled by the OS Task Scheduler, reading the same keyring
> entries it always did (service `cherrypick-notify`). See that repo's README for setup, filters,
> and the Lossdog token capture steps. Nothing in this suite polls either feed any more.
