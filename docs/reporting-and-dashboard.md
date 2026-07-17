# Reporting & dashboard (the read side)

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
        └── dashboard ───►  dashboard.html (static)  or  a live server (--serve)
```

`calibrate` sits alongside `report`, reading the same paper DBs to advise on risk-profile promotion.
`logrotate` (`archive`) sweeps finished months into `logs/archive/`.

## `report` — unified paper P&L

`report.run(cfg, session=…)` reads each enabled module's paper DB through the per-schema adapter
(`meic_ic` / `earnings`), normalizes every closed trade to `{profile, symbol, strategy, gross_pnl, cost,
net_pnl, session}`, and summarizes:

- **Suite total** and **per-module** rows.
- **Per-profile** breakdown (grouping by the trade's attribution tag via
  `cherrypick.core.profiles.compare_profiles`).
- Both **`win_rate`** (on net P&L) and **`gross_win_rate`** (on gross) — the gap shows how many trades
  have edge *before* costs but not *after* (the signal at 1-contract sizing, where cost dominates).

`--session`/`--date` scopes to one settlement day; omit it for the cumulative view. This one function is
the single source of truth the digest, dashboard EOD card, and calibration all cite, so they can never
disagree for the same day.

## The two per-module EOD reports (deterministic)

Each module writes **two** files per session, at its settlement pass, **code-generated with no
agent/LLM/network** (so they run unattended on the reliability-adjacent daemon):

| File | Content |
|---|---|
| `paper-eod-<day>.md` | Terse metrics: per-profile table (trades, win rate, net P&L, expectancy, profit factor, max drawdown), exits-by-reason, per-symbol P&L. |
| `eod-analysis-<day>.md` | Conversational **7-section** read: (1) executive snapshot, (2) position-level detail, (3) trade activity log, (4) risk metrics, (5) market context, (6) tax/accounting notes *(informational)*, (7) notes/journal with heuristic recommendations. Reads like prose but is rule-based templating, not synthesis. |

Both reconcile with `report`'s numbers for the same day. Regenerate on demand:

- MEIC: `python src/paper_loop.py --eod-report [--date <d>]` (writes both) or `--eod-analysis` (analysis only).
- Earnings: `python src/strategy_test_runner.py eod_report [--date <d>]` or `eod_analysis`.

A small **market-context snapshot** (VIX / VIX1D / per-symbol IV rank for MEIC; overnight VIX for
earnings) is captured on the loop write path — stdlib/DB-only — so the market-context section is real.

## The suite digest

`cherrypick eod-digest` writes `logs/eod-digest-<day>.md`: a conversational **Snapshot** (which module
carried the day, cost drag as a share of gross, and the gross-vs-net win-rate gap — "costs flipped ~N
trades from win to loss"; on an all-red day it names the least-bad and worst instead of a "carrier"), the
suite total, a per-module table, and links to each module's `paper-eod` and `eod-analysis`. It cites
`report`'s numbers rather than re-summing the DBs, so it can't drift. Scheduled daily as
`cherrypick-eod-digest` (via `notify-eod`, which also pushes a one-line summary), on by default.

## The AI EOD insight (opt-in)

`cherrypick eod-insight` (`orchestrator/eod_insight.py`) is the one place AI is invoked in the product,
and it's deliberately fenced:

- **Feature-detected + opt-in.** Runs only if `eod_insight.enabled` is true **and** Claude Code
  (`claude`) is on PATH; otherwise it skips silently. The deterministic `eod-analysis` stays the source
  of record.
- **Files in, text out, no dangerous tools.** It pipes the day's deterministic reports (each module's
  `eod-analysis` + `paper-eod`, plus the suite digest) to `claude -p` in headless mode with
  `--disallowed-tools Bash Edit Write NotebookEdit WebFetch WebSearch Task` — so the agent can't run
  commands, edit/write files, or reach the network. The **orchestrator** writes the output file; the
  agent never gets filesystem/broker access.
- **Off the reliability path.** A separate daily `cherrypick-eod-insight` task (registered only when
  enabled), best-effort, never on the watchdog/paper loop.

Output: `logs/eod-insight-<day>.md` — a genuine cross-module narrative (the "why", trends, concrete
paper-tuning recommendations), clearly labelled AI-generated and not advice. Enable with
`"eod_insight": {"enabled": true}` and re-run `install`. See
[guardrails-and-modes.md](guardrails-and-modes.md) for why this satisfies the no-AI-on-the-reliability-path
invariant.

## The dashboard

`dashboard.py` renders one status page composing all of the above plus a log tail and health.

- **Static** (`cherrypick dashboard`): writes `~/.cherrypick/dashboard.html`, regenerated on each
  watchdog tick. Reads the **watchdog heartbeat** (`state/watchdog.last.json`) for health rather than
  re-running `doctor`, so it stays fast and offline and never touches the broker.
- **Live** (`cherrypick dashboard --serve`): a loopback-only server (default `127.0.0.1:8787`) that
  rebuilds the same page fresh per request. Adds a few broker-touching cards that exist **only** on the
  served path — a `/api/system` doctor card, a `/api/reconcile` card, and module dashboard **iframes**
  (`/embed/<id>`, PAPER mode forced) — plus polled section cards (e.g. the live GEX view).

### The EOD card's report links

The EOD card lists, per module, the terse **metrics** report and the conversational **analysis**, plus
the suite **digest** and (when present) the **AI insight**. On the live server these are clickable links
that open the report in a new tab via `/eod-report`, rendered as **styled HTML** (a small dependency-free
markdown→HTML converter: headings, pipe tables, bold/italic/code, nested bullets, themed to match the
dashboard). On the static file they're plain `✓` existence markers — a file has no server behind it to
open. Route shapes:

| Link | Route |
|---|---|
| module metrics | `/eod-report?module=<m>&session=<day>` |
| module analysis | `/eod-report?module=<m>&kind=analysis&session=<day>` |
| suite digest | `/eod-report?suite=1&session=<day>` |
| AI insight | `/eod-report?insight=1&session=<day>` |

## End-of-month log/report rotation

`cherrypick archive` (`orchestrator/logrotate.py`) bundles each **finished** month's dated reports
(paper-eod / eod-analysis / eod-digest / eod-insight / live eod) and rotated log backups (`*.log.N`) into
`logs/archive/<YYYY-MM>/<scope>.zip` — one zip per scope (the suite logs root + each module dir) — then
removes the originals once the zip verifies (`testzip()`). It is idempotent and safe: it never touches the
current month or an active `.log`, and a re-run (or a run after a missed month) converges. Registered as a
monthly `cherrypick-log-archive` task (on by default). `--dry-run` previews; `--month YYYY-MM` scopes to
one month.
