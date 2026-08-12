# Reporting & the console (the read side)

Everything the suite produces for you to *look at*. All of it is **read-only and file-only** — it reads
paper DBs (SQLite read-only), watchdog state, logs, and report files, never the broker or the network
(the one exception is the opt-in AI insight, which calls Claude Code — still off the reliability path).

## The reporting stack, layer by layer

```
report.run(session=day)  ── unified cross-module P&L (gross/net, per profile)
        │
        ├── eod_digest ──►  logs/eod-digest-<day>.md   (suite roll-up + snapshot + links)
        │                        cites report's numbers, so it can't drift
        │
        ├── each module writes (at settlement, deterministically):
        │       logs/<mod>/paper-eod-<day>.md       terse metrics tables
        │       logs/<mod>/eod-analysis-<day>.md    conversational 7-section read
        │
        ├── eod_insight ─►  logs/eod-insight-<day>.md  (opt-in AI synthesis over the above)
        │
        └── console ─────►  the reactive UI at 127.0.0.1:5070 (packages/console)
                                 reads these files and every module's DB directly
```

`calibrate` sits alongside `report`, reading the same paper DBs to produce per-profile qualification
readings. It compares a **champion** (the currently-live profile) against every other observed tag as a
challenger; where a module's tags are parallel experiment arms rather than a risk sequence, it declares
no champion and reports each arm on its own. The older fixed-ladder "promote to the next rung" model was
retired 2026-08-01 — it produced a meaningless recommendation for parallel arms.
`logrotate` (`archive`) sweeps finished months into `logs/archive/`.

## `report` — unified paper P&L

`report.run(cfg, session=…)` reads each enabled module's paper DB through the per-schema adapter
(`meic_ic`, `earnings`, `fly_book`), normalizes every closed trade to `{profile, symbol, strategy,
gross_pnl, cost, net_pnl, session}`, and summarizes:

- **Suite total** and **per-module** rows.
- **Per-profile** breakdown (grouping by the trade's attribution tag via
  `cherrypick.core.profiles.compare_profiles`).
- Both **`win_rate`** (on net P&L) and **`gross_win_rate`** (on gross) — the gap shows how many trades
  have edge *before* costs but not *after* (the signal at 1-contract sizing, where cost dominates).

On the CLI: `--date YYYY-MM-DD` scopes to one settlement day and `--eod` scopes to today's; omit both
for the cumulative view. (There is no `--session` flag — that is the `report.run` keyword, not a CLI
option.) `--live` switches to a **separate** reader (`report.live_run`) over the modules' live ledgers;
it is a different function by design, so `calibrate` — which goes through `report.run` — can never see a
live trade even by accident. Both are files-only and never touch the broker.

This one function is the single source of truth the digest, dashboard EOD card, and calibration all
cite, so they can never disagree for the same day.

## The two per-module EOD reports (deterministic)

Each module writes **two** files per session, at its settlement pass, **code-generated with no
agent/LLM/network** (so they run unattended on the reliability-adjacent daemon):

| File | Content |
|---|---|
| `paper-eod-<day>.md` | Terse metrics: per-profile table (trades, win rate, net P&L, expectancy, profit factor, max drawdown), exits-by-reason, per-symbol P&L. |
| `eod-analysis-<day>.md` | Conversational **7-section** read: (1) executive snapshot, (2) position-level detail, (3) trade activity log, (4) risk metrics, (5) market context, (6) tax/accounting notes *(informational)*, (7) notes/journal with heuristic recommendations. Reads like prose but is rule-based templating, not synthesis. |

Both reconcile with `report`'s numbers for the same day. Regenerate on demand:

- MEIC: `python -m cherrypick.meic.paper_loop --eod-report [--date <d>]` (writes both) or `--eod-analysis` (analysis only).
- Earnings: `python -m cherrypick.earnings.strat_test_harness eod_report [--date <d>]` or `eod_analysis`.

A small **market-context snapshot** (VIX / VIX1D / per-symbol IV rank for MEIC; overnight VIX for
earnings) is captured on the loop write path — stdlib/DB-only — so the market-context section is real.

## The suite digest

`cherrypick eod-digest` writes `logs/eod-digest-<day>.md`: a conversational **Snapshot** (which module
carried the day, cost drag as a share of gross, and the gross-vs-net win-rate gap — "costs flipped ~N
trades from win to loss"; on an all-red day it names the least-bad and worst instead of a "carrier"), the
suite total, a per-module table, and links to each module's `paper-eod` and `eod-analysis`. It cites
`report`'s numbers rather than re-summing the DBs, so it can't drift. On by default and **event-driven,
not scheduled**: the watchdog fires `notify-eod` (which writes the digest and pushes a one-line summary)
as a detached subprocess once every installed module has written its `paper-eod-<day>.md`, with
`eod_digest.deadline` as the backstop so a late or flat module can't skip the day. Detached because the
push is a network call and the watchdog tick must stay stdlib-and-OS-shell only.

## Trade notifications (intraday)

The `trade-notify` supervisor job (`orchestrator/trade_notifier.py`) is the intraday counterpart to the
digest: it reads each module's paper DB **read-only, files and no broker**, finds trades that opened,
had a wing stopped, or closed since the last check, and pushes them. Each event is one-shot, tracked by
an id watermark rather than deduped — and on first activation the watermark is seeded to the current DB
state, so switching it on never backfills your existing trades as a burst.

Push goes to `notify.trade_channels` (default `log` + `discord`) rather than every channel, so frequent
paper fills don't spam desktop toasts. Per module, it is opt-in via `paper.notify_trades`; a module's
**live** ledger is a separate opt-in (`live.notify_trades`) and its pushes carry a LIVE prefix and a
desktop toast, because real money warrants one where paper deliberately doesn't.

**MEIC runs several parallel arms, so per-trade pushes can get loud.** `notify.trade_summary.mode`
decides how its events reach you:

- **`per-trade`** (default) — one push per entry, stop, and exit. `trade_summary.profile_prefixes`
  routes only the arms whose `risk_profile` starts with a listed prefix into the digest instead, which
  is how a high-volume study arm is kept quiet while the everyday book stays per-trade.
- **`summary`** — every MEIC trade accumulates into a periodic per-symbol digest, pushed every
  `interval_minutes`; `profile_prefixes` is ignored. A quiet window pushes nothing rather than an empty
  heartbeat, and wing stops fold into the eventual exit line rather than firing mid-trade.

A digest line reads `MEIC digest 13:45 ET — SPX: 30 entries (open×10 width-10×10 width-5×10) · 2 exits
net +$48 · day 7 trades net +$61`, with a matching Discord card. Arms are **counted, not listed** — a
30-entry window would otherwise repeat the same three labels ten times each.

## The AI EOD insight (opt-in)

`cherrypick eod-insight` (`orchestrator/eod_insight.py`) is the one place AI is invoked in the product,
and it's deliberately fenced:

- **Feature-detected + opt-in.** Runs only if `eod_insight.enabled` is true **and** Claude Code
  (`claude`) is on PATH; otherwise it skips silently. The deterministic `eod-analysis` stays the source
  of record.
- **Files in, text out, no dangerous tools.** It pipes the day's deterministic reports (each module's
  `eod-analysis` + `paper-eod`, plus the suite digest) to `claude -p` in headless mode with
  `--disallowed-tools Bash Edit Write NotebookEdit WebFetch Task` — so the agent can't run commands or
  edit/write files. The **orchestrator** writes the output file; the agent never gets filesystem or
  broker access.
- **⚠️ It does reach the network by default.** `eod_insight.research_events` defaults to **true**, and
  when it is on the run is granted **`WebSearch`** (bounded by `--max-turns 8`) so the debrief can
  research upcoming macro and earnings events. That is the single deliberate exception to "no network".
  Set `"research_events": false` to move WebSearch onto the disallowed list and make the run fully
  offline. This does not touch the reliability-path invariant — the call is opt-in, detached, and
  best-effort — but it is a real outbound call, and this page previously claimed the opposite.
- **Off the reliability path.** Fired by the watchdog on the same module-completion event as the
  digest and **launched detached**, so the `claude` call runs in that child and never in the watchdog
  process. Best-effort, never on the paper loop.

Output: `logs/eod-insight-<day>.md` — a genuine cross-module narrative (the "why", trends, concrete
paper-tuning recommendations), clearly labelled AI-generated and not advice. Enable with
`"eod_insight": {"enabled": true}` — it takes effect on the next watchdog tick, with no install step. See
[guardrails-and-modes.md](guardrails-and-modes.md) for why this satisfies the no-AI-on-the-reliability-path
invariant.

## The console

The suite's **one read surface**: `packages/console`, a Node + TypeScript server and a React SPA on
**127.0.0.1:5070**, composing every module's read models (overview/watchdog, MEIC, flies, earnings,
GEX) plus the research and screening surfaces. It reads module SQLite stores with `readonly: true` and their
JSON state as files; its only writable store is `~/.cherrypick/data/console/`.

**The supervisor keeps it up** as an always-on resident job — no clock window and no trading-day
gate, since a read surface you can only open during RTH cannot be used to read the session that just
ended. It is restarted if it dies and if it *wedges*: the server rewrites
`state/console.heartbeat` every ~15s and a stale mtime is the wedge signal, which is what a
process-liveness check would miss on a Node event loop. `/console` opens it and diagnoses it when it
is down; its log is `logs/console/console.log`.

The EOD reports above are surfaced in-page, rendered as markdown from an allowlisted set of files.

**What this replaced, on 2026-08-12.** A static `dashboard.html` regenerated on every watchdog tick,
a served version of the same page on 8787 with broker-touching cards and module-dashboard iframes,
and each module's own dashboard (MEIC 5050/5051, flies 5052, GEX 5055, scout 5057). All deleted;
`pre-console-only` is the tag that still has them. Two consequences worth knowing:

- The watchdog no longer renders anything on its tick, which takes that work off the reliability path
  for good.
- The served page's **live ops card** — the halt flag, per-module live gates, and the reconcile
  panel — has no console equivalent. It was deliberately never ported because it is broker-touching,
  and it is the one real gap the deletion accepted. `liveops.py` and `reconcile.py` both survive;
  `cherrypick reconcile` still runs on demand and as its daily job.

## End-of-month log/report rotation

`cherrypick archive` (`orchestrator/logrotate.py`) bundles each **finished** month's dated reports
(paper-eod / eod-analysis / eod-digest / eod-insight / live eod) and rotated log backups (`*.log.N`) into
`logs/archive/<YYYY-MM>/<scope>.zip` — one zip per scope (the suite logs root + each module dir) — then
removes the originals once the zip verifies (`testzip()`). It is idempotent and safe: it never touches the
current month or an active `.log`, and a re-run (or a run after a missed month) converges. Registered as a
monthly `cherrypick-log-archive` task (on by default). `--dry-run` previews; `--month YYYY-MM` scopes to
one month.
