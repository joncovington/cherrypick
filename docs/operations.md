# Operations — startup, supervision, and the daily checklist

How the suite runs day to day: what is installed, what starts when, what "healthy" looks like at
09:00 ET, and which warnings are noise. Everything here was verified against the running suite on
2026-07-29 (the fork cutover); each table cites its source so it can be re-verified rather than
trusted.

## The model, in one paragraph

**There is no daily startup.** `python run.py install` (from `packages/orchestrator`) is a one-time
act: it registers OS scheduled tasks and starts two long-running daemons, and from then on every task
fires all day and self-gates on trading hours — an out-of-hours tick is a clean no-op. `install` is
idempotent and safe to re-run at any time (it overwrites task registrations at the current checkout's
paths and only starts a daemon that is down). Daily "startup" is therefore **verification**, not
launching — the checklist below.

## Inventory — OS scheduled tasks

All registrations bake in the absolute path of the checkout that ran `install`, which is what makes a
given checkout "the live instance." Sources: `cli.py` (`cmd_install`), `config.example.json`, each
module's `paper_loop.py --install-task`, and `schtasks /Query /V` on the live box.

| Task | Trigger | Runs | Registered by |
|---|---|---|---|
| `cherrypick-meic-paper-loop` | every **2 min** | `pythonw -m cherrypick.meic.paper_loop --once` | **the MEIC module itself** (`install` shells `-m cherrypick.meic.paper_loop --install-task`; the cadence is a module constant, not config) |
| `cherrypick-flies-paper-loop` | every **1 min** | `pythonw -m cherrypick.flies.paper_loop --once` | **the flies module itself** (same pattern; 2 → 1 min on 2026-07-29 — the completion gate's cadence sensitivity is documented at the constant in `cherrypick/flies/paper_loop.py`) |
| `cherrypick-flies-live-loop` | every **1 min** — **per-day, usually absent** | `pythonw -m cherrypick.flies.live_loop --once --live` | **the flies module itself**, via `/live-flies-start` (fresh YES each day). SELF-DISARMS at `live.disarm_time` (17:00 ET) or on a stale arm stamp; the watchdog backstops by setting the halt flag if it survives. Absent = normal (disarmed); mid-session absence while `--status` says armed-for-today is the CRITICAL the watchdog raises. Spawns short-lived `--watch-fills` burst watchers while orders are pending. Settle: provisional at 16:20 from the last streamed trade; confirm with `python -m cherrypick.flies.live_loop --settle --price <official>`. Stop: `/live-flies-start --stop`, or the halt flag (`state/halt-live.flag`). |
| `cherrypick-earnings-paper-entry` | daily **15:45 ET** | `pythonw <orch>/run.py run-earnings-entry` | orchestrator |
| `cherrypick-earnings-paper-exit` | daily **09:45 ET** | `pythonw <orch>/run.py run-earnings-exit` | orchestrator |
| `cherrypick-earnings-dolt` | every **5 min** | `pythonw <orch>/run.py ensure-dolt` (keep-alive: starts `dolt sql-server` only when 3306 is down) | orchestrator (`paper.dolt_service`) |
| `cherrypick-watchdog` | every **10 min** | `pythonw <orch>/run.py watchdog` | orchestrator |
| `cherrypick-trade-notify` | every **2 min** | `pythonw <orch>/run.py notify-trades` | orchestrator |
| `cherrypick-follow-notify` | every **5 min** — **opt-in, off by default** (`follow_feed.enabled`) | `pythonw <orch>/run.py notify-follow` | orchestrator, only when enabled |
| `cherrypick-log-archive` | monthly, day 1 @ **03:30** local | `pythonw <orch>/run.py archive` | orchestrator |
| `cherrypick-reconcile` | daily 16:30 ET — **opt-in, off by default** (`reconcile.schedule.enabled`; a phase-5 posture for live operation) | `run.py reconcile --scheduled` | orchestrator, only when enabled |

**Two tasks are deliberately absent** — do not read their absence in `status` as a fault:
`cherrypick-eod-digest` and `cherrypick-eod-insight` are no longer fixed-time tasks. Both are
**event-driven**: the watchdog fires them (detached, off the reliability path) once every installed
module has written its `paper-eod-<day>.md`, with `eod_digest.deadline` (16:45 ET) as the backstop.
`install` actively deletes any stale fixed-time registration of either (`cli.py`).

## Inventory — daemons (not tasks)

Two classes of long-running process, both launched detached-and-headless by `install` and thereafter
policed by the watchdog:

| Daemon | What | Supervision | Contract |
|---|---|---|---|
| **streamer** (`packages/streamer`, top-level `streamer` config block) | The suite's single producer: writes `~/.cherrypick/data/marketdata/stream_cache.db`, streaming the **union** of every `state/stream_requests/<module>.json` | Session-gated **09:15–16:00 ET**; restarted on *silence*, not just death (`stale_restart_seconds: 240` — the 2026-07-20 stall was a live-but-quiet socket) | `status_argv`/`start_argv`/`stop_argv` = `run.py --status` / `run.py` / `run.py --stop`. **`uninstall` deliberately leaves it running** (`cli.py`) |
| **services** (top-level `services` block) — currently `gex-recorder` | The GEX spot-trail recorder: samples every symbol's spot ~15 s so the trail has no gaps whether or not a dashboard is open | Every watchdog tick (not session-gated) | Same argv contract; `status_argv` must print `{"running": bool}`. `uninstall` **stops** services (they are the orchestrator's own daemons) |

`meic-sidecar` (the 127.0.0.1:7699 REST poller) is a declared-but-disabled third; enable only for
MEIC's live/interactive loop fast-path.

**Both are also recycled when their config changes.** A daemon reads config once, at launch, so
an edit afterwards reaches the file and never the process — and no liveness check can see it, because
nothing is wrong with the process. (A `gex-recorder` up since 07-19 kept writing a frozen spot into
the trail for days after `source.stream_cache_db` moved off the retired meic cache.) `install` stamps
a hash of each service's effective config — its own config file *and* its `services[]` entry — into
`state/service-<id>.launch.json`, and every watchdog tick compares. A moved hash means stop-then-start
so the process re-reads; the stamp only advances if the restart actually succeeded, so a failed
recycle is retried rather than forgotten. Recycling is gated on `auto_restart`: a service the
orchestrator may not restart is only reported as stale, never touched. A service with no stamp yet
(started by hand, or predating this) is **adopted, not restarted** — with nothing to compare against,
staleness is unknowable, and the first tick simply records what it is running so the *next* change is
caught. Set `config_name` on a service whose config file is named for neither its checkout directory
nor its id.

The streamer gets the same treatment, with two differences that follow from its restart path. The
recycle is reached **only from the healthy branch**, so the stall path always wins — a silent streamer
is restarted for silence, and that restart stamps the new config anyway. And it honours the same
`settling` window the stall path uses: a streamer restarted seconds ago has not resubscribed yet, and
recycling it again is how a restart loop starts. Producers stamp under their watchdog finding label
(`streamer`, `<module>.streamer`), so during a cutover the standalone producer and a module's own
streamer keep separate stamps instead of recycling each other every tick.

Every module that consumes the stream declares its needs in `~/.cherrypick/state/stream_requests/`:
flies and gex have always regenerated their files on every run, and MEIC gained its writer on
2026-07-29 (`meic/src/cherrypick/meic/stream_request.py` — symbols plus a `leg_sources` query against the paper
ledger, so open positions' option legs stay subscribed after spot walks the ATM window away).

## Inventory — ports

All loopback-only. Sources: each module's own default, the orchestrator embed config, and
`config.example.json`.

| Port | Surface |
|---|---|
| 8787 | Suite dashboard — `run.py dashboard --serve` (`dashboard.serve`) |
| 8801 | MEIC dashboard as a suite embed (`dashboard.embeds`, PAPER mode forced) |
| 8802 | gex full dashboard as a suite embed |
| 8803 | flies dashboard (both standalone default and as the suite embed) |
| 5050 / 5051 | MEIC dashboard run directly: live / paper (`python -m cherrypick.meic.dashboard`) |
| 5055 (+5056) | gex standalone serve (`serve.port`; WebSocket push defaults to port+1) |
| 7699 | MEIC REST sidecar (optional, off) |
| 3306 | Dolt SQL server (earnings data; `cherrypick-earnings-dolt` keeps it alive) |

## Dependency order and the clock

The one **hard pre-open deadline**: the streamer must be alive **before 09:30**, because
`streamer/src/cherrypick/streamer/orb.py` accumulates the opening range strictly inside **09:30–09:35 ET** and silently
persists nothing if it wasn't running through that window. Everything else self-gates or degrades.

Order: keyring credentials + stream-request files (standing) → **streamer alive by ~09:20** →
self-gating loops (fire all day; act when in-session) → earnings exit 09:45 / entry 15:45 → watchdog
supervising from 09:15.

Time constants, each with its source:

| When (ET) | What | Source |
|---|---|---|
| 09:15 | `NEAR_OPEN` — watchdog begins streamer/freshness supervision | `orchestrator/timeutil.py` |
| 09:30 | `MARKET_OPEN` | `orchestrator/timeutil.py` |
| 09:30–09:35 | ORB window — streamer must be up throughout | `streamer/src/cherrypick/streamer/orb.py` |
| 09:30–16:05 | MEIC loop acts (the +5 min runs the 16:00 settlement pass) | `meic/src/cherrypick/meic/paper_loop.py` |
| 10:00–14:30 | MEIC paper IC entries (`entry_window_start/_end`; the 09:30 paper override was removed 2026-07-29 — 10:00 is the intended start) | `~/.cherrypick/config/meic.json` |
| 09:30–16:00 | flies loop RTH; hard `no_entry_before: 10:00`; settle 16:20 | `flies/src/cherrypick/flies/paper_loop.py`, flies config |
| 09:45 / 15:45 | earnings exit / entry runs; entry SLA goes CRITICAL if it hasn't run by 16:20 | orchestrator config + watchdog |
| 16:00 | `MARKET_CLOSE`; cash-settled MEIC positions settle | `orchestrator/timeutil.py` |
| 16:45 | EOD digest deadline backstop (event-fired earlier when every module's paper-eod exists) | `eod_digest.deadline` |

## The 09:00 ET checklist

Five commands; what "good" looks like is quoted from real runs (2026-07-29).

1. **`python run.py doctor`** (from `packages/orchestrator`) — expect `Result: ALL GREEN`. Read the
   `clock/tz` line (`trading_day=True` on a session day) and `onboarding` (each module's credential
   source + masked account). A failing state names its finding — e.g. before install it shows
   `not installed (run: cherrypick install)` per module and a dead streamer.
2. **`python run.py status`** — every task `Scheduled Task State: Enabled` **with a future
   `Next Run Time`**. This is the check `doctor` does not make: a task can be registered but disabled
   or stuck in the past. (`Last Result: 267011` on a daily task just means "hasn't run yet today.")
3. **`python packages/streamer/run.py --status`** — require **both** `"running": true **and** a small
   `oldest_event_age_s`** during market hours. Liveness alone is insufficient — the whole lesson of
   the 2026-07-01 34-hour stall is a socket that is connected and silent. Caveat observed at cutover:
   right after a restart the status row can still show the *previous* run's `connected_since` /
   `last_event_at` until the new connection populates, so read it a minute after any restart.
4. **Dolt**: reachable on 3306 *and serving* `earnings`, `options`, `stocks` — `doctor` checks the
   databases, not just the port (a server rooted at the wrong data dir answers happily and serves
   nothing).
5. **`~/.cherrypick/state/watchdog.last.json`** — recent (≤ interval) with `"overall": "OK"`.

## Normal vs. real warnings

Expected — do not chase:

- `eod-digest` / `eod-insight` missing from `status`: by design (event-driven, see above).
- Freshness "not checked" and streamer WARNs **outside** 09:15–16:00 ET: supervision is
  session-gated; an overnight streamer with `stale_warning: true` and a huge event age is idle, not
  broken.
- **The pre-open stale WARN, ~09:15–09:32** — *confirmed in `watchdog.log`* (2026-07-23, -24, -27):
  freshness checking goes live at 09:15 but the paper loops first write after 09:30, so a tick landing
  ~09:24 raises "MEIC/Flies paper data is stale — no paper write in ~1039 min," which self-recovers on
  the first post-09:30 tick. Noise, unless it persists past ~09:35.
- Flies iterations refused with `no_fresh_quotes` in bursts: the provider refusing to price on stale
  data is the design working; a barren stretch reads from `fly_snapshots`, not silence.

Real — act:

- A streamer stale WARN **during** market hours that repeats across ticks (auto-restart should clear
  one; repeated restarts mean the feed or auth is broken — check `logs/streamer/streamer.log`; on
  2026-07-29 the signature `Missing credentials: client_secret, refresh_token` meant a keyring
  service-chain gap).
- `earnings entry SLA` CRITICAL after 16:20: the day's entries did not run.
- `watchdog.last.json` older than ~20 min: the watchdog task itself is dead — nothing else is being
  supervised.

## Known gaps (documented, deliberately not fixed)

Each open gap below is tracked as an issue — the entry here is the operator-facing description, the issue carries the anchors and the suggested direction. A gap with no issue is either fixed (struck through, with what it actually was) or has not been verified recently enough to file.

- **Underlyings bind at streamer start.** ([#62](https://github.com/joncovington/cherrypick/issues/62)) The per-module stream-request writers keep the files
  current, but a *new* underlying only reaches the wire on one streamer restart
  (`streamer/src/cherrypick/streamer/daemon.py`). A symbols change = one restart, stated here so it isn't rediscovered.
- **MEIC's gates fail open when the streamer is down.** ([#63](https://github.com/joncovington/cherrypick/issues/63)) The paper loop does not crash: GEX, ATR, and
  intraday-range return unavailable and their gates silently deactivate (`meic/GATES.md`;
  `meic/src/cherrypick/meic/tt.py`). It keeps trading with safety gates off. The ATR gate additionally needs **5
  complete prior sessions** of `stream_summary`, so a multi-day outage disarms it for a week with no
  error surfaced.
- **Thin pre-open margin.** ([#64](https://github.com/joncovington/cherrypick/issues/64)) Streamer supervision starts at 09:15 and the watchdog interval is 10
  minutes, so the first supervising tick can land ~09:25 — as little as 5 minutes before the
  unrecoverable ORB window. A streamer that died overnight is unsupervised until then. (Mitigation:
  the 09:00 checklist above.)
- **`doctor` coverage holes.** ([#65](https://github.com/joncovington/cherrypick/issues/65)) It verifies the watchdog and module paper tasks but not
  `cherrypick-trade-notify`, `-log-archive`, or `-reconcile`; and nothing cross-checks that
  `stream_requests/*.json` cover the symbols the modules actually trade (mitigated since 2026-07-29
  by every module regenerating its own file).
- ~~**`holidays_loaded=0`.**~~ **Fixed 2026-08-05.** `timeutil.load_holidays` was scanning MEIC's
  config for `nyse_holidays_<year>` keys that had been retired when the calendar moved into
  `cherrypick.core` — so the scan matched nothing and every caller, the watchdog included, ran with an
  empty set and treated market holidays as ordinary sessions. It now reads
  `cherrypick.core.calendar.nyse_holidays` (this year and next; `doctor` shows 20 rather than 0), and
  degrades to weekday-only gating if that lookup ever fails.

## The 2026-07-29 cutover (record)

Production (`~/Claude/cherrypick`) was retired and this fork became the live instance — the shadow-mode
plan (task prefixes, port offsets, side-by-side A/B) was superseded by direct inheritance of
`~/.cherrypick` with `CHERRYPICK_HOME` unset. What the cutover did, for the record:

- `install` run from the fork checkout — same task names, commands re-baked at fork paths.
- Home configs updated to the fork's shape; stale pre-monorepo `repo` keys removed so a path miss can
  never clone retired code; **`data_epoch.date = 2026-07-29`** set (calibrate uses only post-epoch
  sessions; production's last partial session was 07-28); the hardened promotion checks
  (`min_return_on_capital: 0.02`, `require_slippage_survival: true`) enabled in MEIC's
  `calibration.rule` — pending since phase 2.
- `state/stream_requests/meic.json` corrected (it was hand-written at the 2026-07-21 streamer cutover:
  seven retired symbols, leg query against the live ledger) and the MEIC writer added so it can never
  go stale again; streamer restarted once so the XSP/QQQ underlying set bound.
- Two live defects caught in verification: a gex-recorder surviving from the retired checkout
  ("already running" at install — stop it so the relaunch runs current code), and the streamer's
  credential chain missing the shared `cherrypick-broker` fallback after the keyring migration
  (fixed; regression-tested).
- The paper `drawdown` alert remains **deliberately disabled** in the home config (paper drawdown
  noise is unwanted; live drawdown is the live module's concern) — note a forced-sampling study arm's
  volume would trip any floors sized for ladder volume anyway.
