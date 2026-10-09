# Operations — startup, supervision, and the daily checklist

How the suite runs day to day: what is installed, what starts when, what "healthy" looks like at
09:00 ET, and which warnings are noise. Everything here was verified against the running suite on
2026-07-29 (the fork cutover); each table cites its source so it can be re-verified rather than
trusted.

## The model, in one paragraph

**There is no daily startup.** `python run.py install` (from `packages/orchestrator`; the root
installer `install.cmd`/`install.sh` runs it as its last step) is a one-time act: it registers **one** OS scheduled task and starts the long-running daemons, and from then on the
**supervisor** fires every job all day (each job still self-gates on trading hours — an out-of-hours
tick is a clean no-op). `install` is idempotent and safe to re-run at any time (it re-anchors at the
current checkout's paths, restarts nothing that is healthy, and deletes any legacy per-job scheduled
tasks). Daily "startup" is therefore **verification**, not launching — the checklist below.

## Inventory — the anchor task and the supervisor's jobs

Since the **2026-08-09 supervisor cutover**, the OS scheduler holds exactly ONE cherrypick entry — a
Windows scheduled task, or on macOS and Linux a tagged line in the user's crontab (that backend is
newer and less proven on a real host than the Windows one):

| Task | Trigger | Runs | Purpose |
|---|---|---|---|
| `cherrypick-supervisor` | every **2 min** | `pythonw <orch>/run.py ensure-supervisor` | Probe: is the supervisor daemon alive (fresh `state/supervisor.last.json` + live PID)? Start it detached if not; after 3 consecutive failed probes, raise one CRITICAL (`supervisor.down`) — the alerting floor of last resort. |

The interpreter in that line is whichever Python ran `install` — the root installer's `.venv`, for a
normal install — and every job below runs under it.

Everything below is a **supervisor job**, derived from `~/.cherrypick/config.json` on every daemon
pass (`orchestrator/jobspec.py::derive_jobs` — the job table is a projection of config, no separate
registration step) and recorded per-run in `state/supervisor-jobs.json`. Cadences are ET wall-clock,
DST-correct, and no longer bound by the OS scheduler's 1-minute floor. A job whose module or feature is
switched off, or needs a capability the machine lacks (`dolt` for earnings and technicals, `claude` for
the advisor and the narratives), is still derived but **disabled, with the reason** — which `status`
shows and which is healthy. "Default" means what `config.example.json` ships.

**Supervision and notification**

| Job | Schedule (ET) | Runs | Default | Notes |
|---|---|---|---|---|
| `watchdog` | every **10 min** | `run.py watchdog` | on | overlap-guarded by the supervisor |
| `streamer-health` | every **60 s**, 09:00–16:00, trading days | `run.py streamer-health` | on | replaces `cherrypick-preopen` with whole-session coverage — a silent streamer is caught within ~5 min all day |
| `trade-notify` | every **30 s** | `run.py notify-trades` | on | fills reach the trade channels in ~30 s — which are `["log"]` until you add a push channel |
| `console` | **resident** | the console's own `run.py` | on | the read surface on 5070; restarted on death and on a stale `state/console.heartbeat` |
| `status-digest` | every **60 min**, 10:00–16:10, trading days | `run.py notify-status` | off | hourly suite-status card to Discord (`status_digest.enabled`); a webhook push, so its own job and never a watchdog-tick call |
| `status-digest-close` | daily **16:35**, trading days | `run.py notify-status --close` | off | the day's CLOSE card after the 0DTE books settle and the 16:30 review-provisional build |
| `desk-notify` | every **60 s** | `run.py notify-desk` | off | cards each manual-desk order; reads the desk's journal as a file and never imports the desk |
| `flies-payoff-post` | every **10 min**, 16:25–19:00, trading days | `scripts/flies_payoff_post.py --mode live --mode paper` | off | the settled flies payoff chart to Discord, once per ledger per session; captured from the console, so it needs the console up |
| `flies-payoff-intraday` | every **60 min**, 10:01–15:30, trading days | `scripts/flies_payoff_post.py --intraday --mode live --mode paper` | off | the same chart while the book is open, captioned INTRADAY with each arm's open flies, net cash and worst case at expiry; once per ledger per ET hour, standing down once a ledger settles (`flies_payoff_post.intraday.enabled`) |
| `guard-mutants` | daily **08:50**, trading days — **skips when nothing changed** | `scripts/guard_mutants.py --if-changed` | on | re-runs each guard's test clean (must pass) and under a mutant that breaks it in memory (must fail on an assertion); a guard that can no longer fail is a WARNING. Runs only when the checkout differs from the last passing run. Writes `state/guard-mutants.last.json` |

**Module loops**

| Job | Schedule (ET) | Runs | Default | Notes |
|---|---|---|---|---|
| `meic-paper` | every **60 s** | `-m cherrypick.meic.paper_loop --once` | on | cadence is `modules.meic.paper.tick_interval_seconds` |
| `meic-regime-cuts` | daily **16:40**, trading days | `-m cherrypick.meic.regime_cuts --write` | on | the nightly regime-cuts artifact the console and the advisor read |
| `flies-paper` | **resident** `--interval 15`, 09:30–16:00, trading days | `-m cherrypick.flies.paper_loop --interval 15` | on | supervised for death and for 120 s of log silence. 15 s ticks are a journaled measurement break (completion rates are not comparable across 2026-08-09) |
| `flies-paper-offsession` | every 60 s **outside** 09:30–16:00 | `…flies.paper_loop --once` | on | keeps 16:20 settlement, retry-until-settled and the hourly idle heartbeat |
| `flies-regime-cuts` | daily **16:40**, trading days | `-m cherrypick.flies.cli regime-cuts --write` | on | as MEIC's |
| `flies-fee-reconcile` | daily **09:15**, trading days | `-m cherrypick.flies.fee_reconcile --symbol SPX` | on | reconciles recorded fees against the schedule |
| `flies-live` | every 60 s — **enabled only while the arm record is valid for today** | `-m cherrypick.flies.live_loop --once --live` | off (unarmed) | armed by `/live-flies-start` writing `state/flies-live-arm.json` (fresh YES each day); self-disarms by deleting the record at `live.disarm_time` (17:00 ET) or on a stale date; the watchdog backstops with the halt flag if the record survives |
| `bwb-paper` | every **60 s** | `-m cherrypick.bwb.paper_loop --once` | on | the daily ladder: one entry a session, every open position marked each tick |
| `bwb-fee-reconcile` | daily **09:20**, trading days | `-m cherrypick.bwb.fee_reconcile --symbol SPX` | on | as flies' |
| `bwb-live` | every 60 s — **enabled only while the arm record is valid for today** | `-m cherrypick.bwb.live_loop --once --live` | off (unarmed) | armed by `/live-bwb-start` writing `state/bwb-live-arm.json`; the same self-disarm and watchdog backstop as flies, through `cherrypick.core.live`. Each tick confirms fills, walks resting orders, settles on an official print when due, and makes one entry attempt in the window |
| `earnings-paper` | every **60 s** | `-m cherrypick.earnings.paper_loop once` | on with `dolt` | the managed loop since 2026-08-12: the phase comes from the clock (forward scan ~06:30, marking from the open, management in the execution window, the forced-sampling entry scan from 15:35, the EOD write) |
| `earnings-dolt` | every **5 min** | `run.py ensure-dolt` | on with `dolt` | keep-alive: starts `dolt sql-server` only when 3306 is down and `dolt-server` is not held |
| `earnings-dolt-pull` | daily **05:30** | `scripts/refresh_dolt_data.py` | on with `dolt` | pulls the `earnings`/`options`/`stocks` clones up to date |
| `symbol-watch` | daily **06:30** | `run.py run-earnings-symbol-watch` | off | the console's Earnings "Upcoming" preview |
| `calendars-paper` | **resident** `--interval 30`, 09:30–16:00, trading days | `-m cherrypick.calendars.paper_loop --interval 30` | **off** (experimental) | weekly SPY double calendars: entry self-gates to the entry day (Monday, or Tuesday after a Monday holiday) and every open leg is marked each tick — the exit study's substrate, so a cadence change is a journaled measurement break |
| `calendars-paper-offsession` | every 60 s **outside** 09:30–16:00 | `…calendars.paper_loop --once` | **off** (experimental) | owns the 16:20 settlement of expiring legs: cash for SPX-style underlyings, **physical** for SPY (an ITM short delivers shares, held to the next session's disposal), retry-until-settled off a staleness-gated spot read |
| `pmcc-paper` | every **60 s** | `-m cherrypick.pmcc.paper_loop --once` | **off** (experimental) | enters whenever the (symbol, book) slot is free, marks every open leg (the assignment-exposure telemetry reads that path), holds to the short's expiry, and settles expiring legs (an ITM short delivers shares covered the next session) |
| `curve-paper` | every **60 s** | `-m cherrypick.curve.paper_loop --once` | **off** (experimental) | VXX call credit spreads; records the daily VIX/VIX3M regime classification every session, traded or not |

**Reports, data and the AI**

| Job | Schedule (ET) | Runs | Default | Notes |
|---|---|---|---|---|
| `review-provisional` | daily **16:30**, trading days | `run.py review --provisional` | on | see below |
| `review-final` | daily **10:15**, trading days | `run.py review --final` | on | finalises the prior session and re-runs reconciliation |
| `review-narrative` | daily **10:45**, trading days | `scripts/eod_narrative.py` | off, needs `claude` | the agent-written note, beside the facts, never inside them |
| `market-files` / `market-files-retry` | daily **18:45** / **07:45**, trading days | `scripts/fetch_market_files.py fetch` | on | the files the morning pack reads, OCC's option volume included |
| `earnings-moves` | daily **18:40**, trading days | `scripts/fetch_earnings_moves.py` | on | |
| `fetch-headlines` | daily **08:45**, trading days | `scripts/fetch_headlines.py` | on | |
| `futures-contracts` | daily **08:45**, trading days | `scripts/refresh_futures_contracts.py` | on | |
| `morning-factpack` | daily **08:30**, trading days | `run.py morning` | on | the overview's fact pack and GREEN/YELLOW/RED phase |
| `morning-narrative` | daily **09:00**, trading days | `scripts/morning_narrative.py` | off, needs `claude` | |
| `technicals-index-bars` | daily **06:00** | `scripts/fetch_index_bars.py` | on with `dolt` + earnings | |
| `technicals-land` | daily **06:15** | `-m cherrypick.technicals land` | on with `dolt` + earnings | lands the end-of-day store from the Dolt clones |
| `technicals-report` | daily **06:30** | `-m cherrypick.technicals report` | on with `dolt` + earnings | disabled with `technicals-land` |
| `technicals-iv-rank` | daily **18:50**, trading days | `scripts/fetch_iv_rank.py` | on with `dolt` + earnings | |
| `technicals-dividends` | daily **19:30** | `scripts/fetch_dividends.py` | on with `dolt` + earnings | |
| `report-edition` / `-retry`, `report-charts` | daily 07:00 / 08:15, 17:15, trading days | `scripts/fetch_vendor_edition.py` | off | `market_report.collector`; needs both vendor keys set |
| `universe-measure-1` / `-2`, `universe-daily`, `universe-watchlist` | daily 11:00 / 14:00, 18:00, 18:30, trading days | `scripts/build_stock_universe.py` | off | `market_report.universe` / `universe_watchlist` |
| `advisor-deep` | daily **17:00**, trading days | `scripts/advisor_checkpoint.py --slot deep` | off, needs `claude` | the post-close run, after `review-provisional`, on the strong model. It also ISSUES the next session's advice, even when the AI call failed. Catchup 300 min |
| `advisor-<slot>` | as configured — **none by default** | `scripts/advisor_checkpoint.py --slot <s>` | off | optional light intraday checkpoints, declared in `advisor.checkpoints` |
| `reconcile` | daily **16:30** | `run.py reconcile --scheduled` | off | worth turning on once anything trades live |
| `suite-backup` | daily **01:30** | `run.py backup` | on | one zip at `backup.dest`; `doctor` warns past 36 h |
| `config-backup` | every `config_backup.interval_minutes` (15) | `run.py config-backup` | **off** (opt in) | commits the config files the home repo's allow-list tracks and pushes to its remote; `doctor` warns when on but not set up or failing |
| `log-archive` | monthly, day 1 @ **03:30** | `run.py archive` | on | catchup 7 days (idempotent, finished months only) |

Missed-fire policy after sleep/hibernate: interval jobs fire once immediately and resume cadence
(never a burst); daily/monthly jobs fire inside their catchup window, else record `missed` and skip.

**End-of-day reporting is two ordinary supervisor jobs** since 2026-08-13: `review-provisional`
(16:30 ET) and `review-final` (10:15 the next morning), trading days only. They run the review
package through `run.py review`, which reads every module's ledger read-only and writes only into
`~/.cherrypick/data/review`. The provisional pass captures the 0DTE modules complete with earnings
still carrying overnight; the final pass closes that session out once earnings has settled, and
re-runs reconciliation. Failures are WARNING, never CRITICAL — a bad pass costs a report, not a
trade.

**The AI advisor** is off by default twice over (`advisor.enabled`, and each module's own `advice`
bounds) and needs the `claude` capability. Its deep run after the close builds a deterministic fact
pack with `python -m cherrypick.advisor factpack`, pipes it to `claude -p` with every acting tool
denied, and hands the reply back to the package to validate against the bounds each module declared.
Admitted proposals run as paper A/B experiments — an `advised:<experiment>` arm beside its un-advised
control — and the deep slot's final step re-issues tomorrow's advice artifact for every active
experiment. That step runs **unconditionally**, after a timeout, a parse failure or a missing
`claude`: an AI outage must never truncate an active A/B sample. Failures are WARNING, never
CRITICAL, and name the manual re-run command. Nothing here can reach a live account. The console's
Advisor page is the read surface. Since 2026-09-15 a model `kill` verdict is actioned on admission
(`advisor.kill_on_verdict`); the console button is for stopping one the model has not.

**Rollback** (documented for one transition window): `git tag pre-supervisor` marks the last
schtasks-driven commit. To roll back: `run.py uninstall` (new code), check out the tag, `run.py
install` (old code re-registers every per-job task; the module `--install-task` helpers exist
there, though they were removed from current code on 2026-10-08). The old and new mechanisms never coexist — each `install` deletes the other's registrations.

## Inventory — daemons (not tasks)

Two classes of long-running process, both launched detached-and-headless by `install` and thereafter
policed by the watchdog:

| Daemon | What | Supervision | Contract |
|---|---|---|---|
| **streamer** (`packages/streamer`, top-level `streamer` config block) | The suite's single producer: writes `~/.cherrypick/data/marketdata/stream_cache.db`, streaming the **union** of every `state/stream_requests/<module>.json` | Session-gated **09:15–16:00 ET**; restarted on *silence*, not just death (`stale_restart_seconds: 240` — the 2026-07-20 stall was a live-but-quiet socket) | `status_argv`/`start_argv`/`stop_argv` = `run.py --status` / `run.py` / `run.py --stop`. **`run.py uninstall` deliberately leaves it running** (`cli.py`); `run.py stop --all` stops it |
| **services** (top-level `services` block) — `gex-recorder`, on by default | The GEX spot-trail recorder: samples every symbol's spot ~15 s so the trail has no gaps whether or not anyone is looking at it, and records the GEX and market-regime history | Every watchdog tick (not session-gated) | Same argv contract; `status_argv` must print `{"running": bool}`. `uninstall` **stops** services (they are the orchestrator's own daemons) |

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

All loopback-only. Each port's source of truth is the code or config named in its row — this table
restates them, so treat a disagreement as a bug in the table (`tools/check_docs.py` checks both
directions).

| Port | Surface |
|---|---|
| 5070 | **Console** — the suite's one read surface (`packages/console/server/src/config.ts`, overridable via `config/console.json`). Kept up by the supervisor as an always-on resident job |
| 8804 | **Settings editor** — `run.py settings` (`settings_serve.py`); the suite's only config-writing surface, foreground-only and never scheduled |
| 7699 | MEIC REST sidecar (optional, off) |
| 3306 | Dolt SQL server (earnings data; the `earnings-dolt` supervisor job keeps it alive) |

Two servers, one read and one write. Until 2026-08-12 there were seven: a suite dashboard on 8787,
per-module dashboards on 5050/5051, 5052 and 5055 (+5056), scout on 5057, and three iframe embed
ports at 8801–8803. All deleted — the console covers them, and anything still wanted from them is in
the `pre-console-only` tag.

## Dependency order and the clock

The one **hard pre-open deadline**: the streamer must be alive **before 09:30**, because
`streamer/src/cherrypick/streamer/orb.py` accumulates the opening range strictly inside **09:30–09:35 ET** and silently
persists nothing if it wasn't running through that window. Everything else self-gates or degrades.

Order: keyring credentials + stream-request files (standing) → **streamer alive by ~09:20** →
self-gating loops (fire all day; act when in-session) → earnings management in the morning, entry scan
from 15:35 → watchdog supervising from 09:15.

Time constants, each with its source:

| When (ET) | What | Source |
|---|---|---|
| 09:15 | `NEAR_OPEN` — watchdog begins streamer/freshness supervision | `orchestrator/timeutil.py` |
| 09:30 | `MARKET_OPEN` | `orchestrator/timeutil.py` |
| 09:30–09:35 | ORB window — streamer must be up throughout | `streamer/src/cherrypick/streamer/orb.py` |
| 09:30–16:05 | MEIC loop acts (the +5 min runs the 16:00 settlement pass) | `meic/src/cherrypick/meic/paper_loop.py` |
| 10:00–14:30 | MEIC paper IC entries (`entry_window_start/_end`; the 09:30 paper override was removed 2026-07-29 — 10:00 is the intended start) | `~/.cherrypick/config/meic.json` |
| 09:30–16:00 | flies loop RTH; hard `no_entry_before: 10:00`; settle 16:20 | `flies/src/cherrypick/flies/paper_loop.py`, flies config |
| 08:30 | morning fact pack (`morning-factpack`) | `morning.factpack_at` |
| 15:35 | earnings forced-sampling entry scan starts (`entry_scan_at`); the watchdog's entry SLA (`entry_time` 15:45 + `entry_sla_grace_minutes` 35) goes CRITICAL if no entry ran by 16:20 | earnings config + orchestrator config + watchdog |
| 16:00 | `MARKET_CLOSE`; cash-settled MEIC positions settle | `orchestrator/timeutil.py` |
| 16:30 | suite review, provisional pass (0DTE complete; earnings still overnight) | `review.provisional_at` |
| 10:15 (next day) | suite review, final pass for the prior session + reconciliation | `review.final_at` |

## The 09:00 ET checklist

Five commands; what "good" looks like is quoted from real runs (2026-07-29).

1. **`python run.py doctor`** (from `packages/orchestrator`) — expect `Result: ALL GREEN`. Read the
   `clock/tz` line (`trading_day=True` on a session day) and `onboarding` (each module's credential
   source + masked account). A failing state names its finding — e.g. before install it shows
   `not installed (run: cherrypick install)` per module and a dead streamer.
2. **`python run.py status`** — the `supervisor` block shows `running: true` with a small
   `heartbeat_age_seconds`, the `anchor` task exists, and every enabled job has a recent
   `last_start` (interval jobs) or a future `next_run`. This is the check `doctor` makes too
   (`supervisor` / `supervisor.anchor` / per-job rows); a job `disabled` with a reason naming a
   config opt-out is healthy. `state/supervisor.last.json` older than ~90 s means the daemon is
   down — `run.py ensure-supervisor` starts it (the anchor task does the same within 2 min).
3. **`python packages/streamer/run.py --status`** — require **both** `"running": true **and** a small
   `oldest_event_age_s`** during market hours. Liveness alone is insufficient — the whole lesson of
   the 2026-07-01 34-hour stall is a socket that is connected and silent. Caveat observed at cutover:
   right after a restart the status row can still show the *previous* run's `connected_since` /
   `last_event_at` until the new connection populates, so read it a minute after any restart.
4. **Dolt** (only with the `dolt` capability): reachable on 3306 *and serving* `earnings`, `options`,
   `stocks` — `doctor` checks the databases, not just the port (a server rooted at the wrong data dir
   answers happily and serves nothing). Without the capability, earnings and technicals are disabled
   and this step does not apply.
5. **`~/.cherrypick/state/watchdog.last.json`** — recent (≤ interval) with `"overall": "OK"`.

## Stopping and restarting one thing

By name, never by PID: `python packages/orchestrator/run.py ps` lists every job and daemon;
`restart <name>`, `stop <name>` and `start <name>` act on one
([orchestrator-cli.md](orchestrator-cli.md)). The supervisor stops the old process and confirms it
gone before starting another, so a restart never leaves two running; a stopped name is **held**
(`state/holds.json`) until `start` or the next `install`. None of these raise an alert or count as a
failure. Two things still warn on purpose: a held live loop during the session, and any hold left
over 12 hours. The watchdog also reports two running copies of any job (CRITICAL for a live loop),
whoever started them.

## Turning it all off

`python run.py uninstall` deletes the anchor task first (so nothing can restart the daemon), stops the
supervisor, removes any legacy per-job tasks and stops the orchestrator's own `services`. It
**deliberately leaves the streamer, the console and the Dolt server running** — its output lists them
under "Left running by design". A **full stop** is `uninstall` followed by `python run.py stop --all`
(the streamer, the console and any job child still recorded; refused while the supervisor or its anchor
could start things again), then the Dolt server on 3306 if the process there is `dolt`. The root
`uninstall.cmd` / `uninstall.ps1` / `uninstall.sh` do exactly that, and the `/uninstall` slash command
does it and verifies. Recorded data, configuration and the keyring login are untouched; the installer
(or `run.py install`) brings everything back.

## Normal vs. real warnings

Expected — do not chase:

- `review-provisional` / `review-final` showing as not-yet-run before their times: by design — they
  are daily jobs, trading days only, and skip weekends entirely.
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
- `watchdog.last.json` older than ~20 min: the watchdog job is not firing — check
  `state/supervisor.last.json` first (a dead supervisor stops every job at once; the anchor task +
  `ensure-supervisor` should revive it within ~2 min, and escalate `supervisor.down` if they can't).

## Known gaps (documented, deliberately not fixed)

Each open gap below is tracked as an issue — the entry here is the operator-facing description, the issue carries the anchors and the suggested direction. A gap with no issue is either fixed (struck through, with what it actually was) or has not been verified recently enough to file.

- **Underlyings bind at streamer start.** ([#62](https://github.com/joncovington/cherrypick/issues/62)) The per-module stream-request writers keep the files
  current, but a *new* underlying only reaches the wire on one streamer restart
  (`streamer/src/cherrypick/streamer/daemon.py`). A symbols change = one restart, stated here so it isn't rediscovered.
- **MEIC's gates fail open when the streamer is down** — now *visible*
  ([#63](https://github.com/joncovington/cherrypick/issues/63)). The paper loop does not crash: GEX, ATR
  and intraday-range return unavailable and their gates silently deactivate (`meic/GATES.md`;
  `meic/src/cherrypick/meic/tt.py`). It keeps trading with safety gates off, and the ATR gate needs **5
  complete prior sessions** of `stream_summary`, so a multi-day outage disarms it for a further week
  after the streamer is healthy again. **The fail-open behaviour is unchanged and deliberate** — blocking
  every entry on a missing feed would be its own outage. What changed on 2026-08-06 is that it is no
  longer invisible: `python -m cherrypick.meic.gate_health` reports which gates are armed, which have
  stood down and why, and how many sessions ATR is still missing. Read-only and file-only.
  **Remaining:** it is a command, not a console page, so it still has to be asked.
- ~~**Thin pre-open margin.**~~ **Fixed 2026-08-06** ([#64](https://github.com/joncovington/cherrypick/issues/64)),
  **superseded 2026-08-09**: the dedicated `cherrypick-preopen` task (streamer liveness every 2 min,
  09:00–09:35) became the supervisor's `streamer-health` job — same check (`_check_streamer_health`
  reused, never copied; no heartbeat write; stops at the door on a non-trading day), every 60 s
  across the whole 09:00–16:00 session. The original reasoning survives intact: the full watchdog
  tick still never speeds up to protect the streamer — the check just has its own cadence now that
  per-job cadences are free.
- ~~**`doctor` coverage holes.**~~ **Mostly fixed 2026-08-06** ([#65](https://github.com/joncovington/cherrypick/issues/65)).
  `doctor` now checks every orchestrator-owned task — `trade-notify`, `log-archive`, `reconcile`
  and `preopen` — resolving each name through the same settings helper `install` uses,
  so a config-driven rename can't desync the check. Opted-out-and-absent reports as healthy; enabled-
  but-missing, and disabled-but-still-registered, both warn. **Still open:** nothing cross-checks that
  `stream_requests/*.json` cover the symbols the modules actually trade — that half is better done
  alongside [#62](https://github.com/joncovington/cherrypick/issues/62), which introduces the notion of
  a request the producer has not served.
- ~~**`holidays_loaded=0`.**~~ **Fixed 2026-08-05.** `timeutil.load_holidays` was scanning MEIC's
  config for `nyse_holidays_<year>` keys that had been retired when the calendar moved into
  `cherrypick.core` — so the scan matched nothing and every caller, the watchdog included, ran with an
  empty set and treated market holidays as ordinary sessions. It now reads
  `cherrypick.core.calendar.nyse_holidays` (this year and next; `doctor` shows 20 rather than 0), and
  degrades to weekday-only gating if that lookup ever fails.

## The 2026-07-29 cutover (record)

The previous production checkout was retired and this fork became the live instance — the shadow-mode
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
