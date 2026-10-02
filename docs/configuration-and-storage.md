# Configuration & storage

Where settings live, how paths resolve, and what each part of the suite reads and writes.

## The managed home

All runtime state lives under one per-user home, resolved by `cherrypick.core.home` and relocatable
wholesale with **`$CHERRYPICK_HOME`**. Nothing runtime lands in a source checkout.

```
~/.cherrypick/
  config.json                     # orchestrator config (modules, jobs, notify, capabilities, …)
  config/meic.json                # MEIC engine config (home-first; else in-repo config.json)
  config/meic.risk.json           # MEIC's arm registry (else $MEIC_RISK_CONFIG / the shipped example)
  config/earnings.json            # Earnings engine config
  config/flies.json               # Flies engine config
  config/bwb.json                 # BWB engine config
  config/calendars.json           # Calendars engine config (experimental)
  config/pmcc.json                # PMCC-99 engine config (experimental)
  config/curve.json               # Curve engine config (experimental)
  config/gex.json                 # GEX engine config
  config/streamer.json            # standalone streamer config
  config/console.json             # console UI config
  config/desk.json                # manual desk config (experimental; not installed by default)
  data/marketdata/stream_cache.db # the canonical shared DXLink stream cache (quotes/greeks/OI) —
                                  #   written ONLY by the standalone streamer, read by every module
  data/meic/paper_trades.db       # MEIC paper ledger (ic_trades)   ← orchestrator reads this
  data/meic/meic_trades.db        # MEIC live ledger (read only by `report --live`)
  data/earnings/paper_trades.db   # Earnings paper ledger (trades)  ← orchestrator reads this
  data/earnings/earnings_trades.db# Earnings live ledger
  data/earnings/{earnings,options,stocks}/  # the optional Dolt clones (the `dolt` capability)
  data/flies/paper_trades.db      # Flies paper ledger (fly_positions / fly_books)
  data/flies/live_trades.db       # Flies live ledger (the live pilot writes here; armed per day)
  data/bwb/paper_trades.db        # BWB paper ledger (bwb_positions)
  data/bwb/live_trades.db         # BWB live ledger (same schema as its paper file; armed per day; never read by a paper surface)
  data/calendars/paper_trades.db  # Calendars paper ledger (dc_positions / dc_legs / dc_marks)
  data/pmcc/paper_trades.db       # PMCC-99 paper ledger (pmcc_positions / pmcc_legs / pmcc_marks)
  data/curve/paper_trades.db      # Curve paper ledger (curve_positions)
  data/gex/gex_history.db         # GEX spot trail + regime history + the suite-level market-regime
                                  #   series (market_regime_history / daily_closes; read via
                                  #   cherrypick.core.regime — see docs/regime-recorder-plan.md)
  data/technicals/eod.db          # technicals' end-of-day store (bars, splits, dividends, IV)
  data/overview/                  # morning-<day>.json fact packs + renders + notes
  data/advisor/                   # advisor.db, fact packs, checkpoints
  logs/                           # suite logs
  data/review/                    # eod-<day>.json fact sets + renders + notes
  logs/meic/  logs/earnings/  logs/flies/  logs/gex/  logs/streamer/   # per-module logs + EOD reports
  logs/archive/<YYYY-MM>/         # monthly zipped reports + rotated logs (one zip per scope)
  state/                          # watchdog state + heartbeats + advice/ + halt-live.flag (when set)
  state/stream_requests/          # per-module streamer subscription requests (each module writes its own)
  state/config-backups/           # the previous version of a config, kept before every write
```

## Config model

One config file per package, all machine-local and gitignored (only the `config.example.json`
templates are tracked). The orchestrator's is the only one that describes other packages; the rest
configure their own engine and nothing else:

| Config | Owned by | Sets |
|---|---|---|
| `~/.cherrypick/config.json` | Orchestrator | `capabilities` (`claude`, `dolt` — see below); which modules are enabled + their `path` and `live_db`; the per-module `paper` block (`paper_db`, `trade_schema`, tick argv and cadence, entry/exit times) and `calibration`; the top-level `streamer` (the standalone producer), `services` (background daemons like the gex recorder) and `console`; `watchdog`, `eval_activity`, `trade_notify`, `desk_notify`, `status_digest`, `flies_payoff_post`, `notify`; `review`, `morning` (the overview), `technicals`, `market_report`; `data_epoch`, `log_archive`, `backup`, `config_backup`; `advisor`, `reconcile`, `symbol_watch`; timezone. |
| `~/.cherrypick/config/meic.json` | MEIC | `symbols`, delta/VIX bands, wing widths, credit floors, entry/exit windows, stop policy, regime thresholds, cash-settled set, deploy-limit pct. |
| `~/.cherrypick/config/meic.risk.json` | MEIC | The arm registry (`active_profile`, `profiles`). Read from `$MEIC_RISK_CONFIG` if set, else this file, else the shipped control-only `packages/meic/config.risk.example.json`; a machine's arms are its own configuration, never the repo's. |
| `~/.cherrypick/config/earnings.json` | Earnings | `available_capital_paper_mode`, position caps, entry/close windows, correlation block list, liquidity gates, per-strategy tuning, named profiles. |
| `~/.cherrypick/config/flies.json` | Flies | `symbols`, wing/increment scaling, entry gates and floors, the experiment `arms`, and the `live` block for the narrow live pilot (armed per day via `/live-flies-start`, one arm / one symbol, sized by the `live.max_open_margin_dollars` cap, self-disarming at `live.disarm_time`). |
| `~/.cherrypick/config/bwb.json` | BWB | The ladder's structure and strikes, the books and their add-on triggers, the opt-in call-wall book, `advice` bounds, and the `live` block for the narrow live path (armed per day via `/live-bwb-start`). |
| `~/.cherrypick/config/calendars.json` | Calendars | Symbols (`SPY` since 2026-08-15), OCC roots, settlement style per symbol, the declared ex-dividend calendar, books, `advice` bounds. EXPERIMENTAL; the module is off in the orchestrator config by default. |
| `~/.cherrypick/config/pmcc.json` | PMCC-99 | Symbols, the long/short delta and DTE windows, the declared ex-dividend spans, books, `advice` bounds. EXPERIMENTAL; off by default. |
| `~/.cherrypick/config/curve.json` | Curve | The VXX spread construction, the VIX/VIX3M regime thresholds, books, `advice` bounds. EXPERIMENTAL; off by default. |
| `~/.cherrypick/config/gex.json` | GEX | `symbols`, the shared stream-cache source path, serve host/port, history DB path. |
| *(orchestrator `morning`, `technicals` blocks)* | Overview, Technicals | Neither has a config file of its own: the orchestrator's `morning` block schedules the overview's fact pack (and its optional narrative), and `technicals` schedules the end-of-day landing and report. Technicals reads the Dolt clones the earnings module's `paper.dolt_service` serves. |
| `~/.cherrypick/config/streamer.json` | Streamer | Broker session settings and the stream-cache path it writes; the symbol set is not configured here — it is the union of every module's `state/stream_requests/` file. |
| `~/.cherrypick/config/console.json` | Console | Serve host/port (`127.0.0.1:5070`) and which modules' read models to surface. No credential of its own — it reads the shared suite entry and never writes one. |
| `~/.cherrypick/config/desk.json` | Desk | Its own authorization for discretionary live orders — which module's keyring service to borrow a session from (`broker_keyring_service`), the allowed accounts, and the policy gates (defined-risk requirement, per-order cap). It stores no broker secrets; the PIN is kept only as a salted verifier. Deliberately independent of every module's `enable_live_trading`. |

**Resolution rules:**
- A module `path` in the orchestrator config is resolved **relative to the config file's directory** /
  the source anchor (e.g. `../meic`) — never hardcode absolute paths.
- A module's config is resolved **home-first** by its `paths.py` (`~/.cherrypick/config/<engine>.json`),
  falling back to the in-repo `config.json` until an explicit `migrate-home`.
- A module without a home config runs from its shipped `config.example.json`; every example declares
  only the `control` arm.
- Env overrides (mainly for tests / a machine escape hatch): `CHERRYPICK_HOME` relocates everything;
  `MEIC_DATA_DIR` / `EARNINGS_DATA_DIR` and `MEIC_LOGS_DIR` / `EARNINGS_LOGS_DIR` relocate a single
  module's data/logs; `MEIC_DB_PATH` points db.py at a specific DB (used by the paper engine);
  `MEIC_RISK_CONFIG` points MEIC at an arm registry.

### Capabilities

Two optional dependencies gate features, recorded in `config.json` as
`"capabilities": {"claude": …, "dolt": …}`. Only a literal `true` counts; an absent key is `false`.

| Capability | Present when | Gates |
|---|---|---|
| `dolt` | The `dolt` binary runs **and** the `earnings`, `options` and `stocks` clones exist in the directory the earnings Dolt server serves (`modules.earnings.paper.dolt_service.data_dir`, default `~/.cherrypick/data/earnings`). | The earnings module; the technicals jobs. |
| `claude` | `claude --version` runs (the Claude Code CLI). | The advisor; the end-of-day and morning narratives. |

A module or feature runs only when its own switch is on **and** every capability it needs is recorded
true; otherwise its jobs are derived disabled with the reason, and the console hides it. Detection
never switches anything on. `run.py capabilities` prints the resolved view, `--detect --write` probes
and records (the installer runs it), and `--cap name=true|false` records one by hand; `doctor` warns
when the record and the machine disagree.

### Notifications

**Push channels are off by default.** The template's `notify.channels`, `notify.trade_channels`,
`desk_notify.channels` and `status_digest.channels` are all `["log"]`, and so are the code fallbacks.
Desktop, Slack and Discord are opt-in: add the channel to the list, and for Slack or Discord store the
webhook with `run.py secrets-set --channel slack|discord`. The `log` channel is always kept as the
floor. The console's on-screen trade toasts are always on and need no setting.

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
| module `paper` (kind `cherrypick_scheduled`) | legacy shape, entry 15:45 / exit 09:45 ET | `<module>-entry`, `<module>-exit` |
| module `paper` (kind `self_healing`) | on, every `tick_interval_seconds` | `<module>-paper` (earnings uses this since 2026-08-12) |
| `paper.dolt_service` | on with the `dolt` capability | `<module>-dolt` (keep-alive), plus `earnings-dolt-pull` (05:30 ET, refreshes the clones) |
| module `live` | **off** until armed | `<module>-live` |
| module `paper.regime_cuts_at` | on (flies, MEIC) | `<module>-regime-cuts` 16:40 ET |
| `console` | **on** | `console` (resident) |
| `review` | **on** | `review-provisional` 16:30 ET, `review-final` 10:15 next morning, trading days only; `review-narrative` **off** (needs `claude`) |
| `morning` | **on** | `morning-factpack` 08:30 ET, the market files (`market-files`, `market-files-retry`, `earnings-moves`, `fetch-headlines`); `morning-narrative` **off** (needs `claude`) |
| `technicals` | on in config, but needs `dolt` **and** the earnings module | `technicals-land`, `technicals-report`, `technicals-dividends`, `technicals-index-bars`, `technicals-iv-rank` |
| `market_report` | **off** (`collector`, `universe`) | `report-edition`, `report-edition-retry`, `report-charts`, `universe-*` |
| `advisor` | **off twice** (suite + per-module `advice` bounds), and needs `claude` | `advisor-deep` (17:00 ET) |
| `status_digest` | **off** | `status-digest` (hourly), `status-digest-close` 16:35 ET |
| `desk_notify` | **off** | `desk-notify` |
| `flies_payoff_post` | **off** | `flies-payoff-post` |
| `symbol_watch` | **off**, daily 06:30 when enabled | `symbol-watch` |
| `backup` | **on**, daily 01:30 | `suite-backup` |
| `config_backup` | **off** (opt in; `run.py config-backup --init --enable`, the installer, or the Config page) | `config-backup` |
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
arm. Companion tables: `ic_spread_legs` (per-side exits), `daily_summary`, `loop_log`, and
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
  flies' and bwb's `live.enabled` and `live.gate0_confirmed` (a human attestation string), bwb's
  `live.arm`, floor, caps and breakers, and every live loss/deploy-limit field cannot be changed from
  this surface — arming or de-risking live trading stays on its existing deliberate path
  (`/live-flies-start`, `/live-bwb-start`, hand-editing the live gates with the plan doc open). The UI shows each locked field with a pointer to where it's actually changed; a direct API
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
Service) — never in files, env vars, or logs. The broker OAuth tokens are one shared entry,
`cherrypick-broker`, which the installer and `connect` write; a module's own `keyring_service` is an
optional per-module override layered over it. Slack/Discord webhooks live under the orchestrator's
`cherrypick-notify` service (`secrets-set`). See [guardrails-and-modes.md](guardrails-and-modes.md).

> **Moved out (2026-08-21):** the tastylive Follow Feed and Lossdog VIP feed notifiers live in a
> separate repository and are no longer part of this suite. Nothing here polls either feed any more;
> their keyring entries happen to share the `cherrypick-notify` service name.
