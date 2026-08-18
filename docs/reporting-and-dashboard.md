# Reporting & the console (the read side)

Everything the suite produces for you to *look at*. All of it is **read-only and file-only** — it reads
paper DBs (SQLite read-only), watchdog state, logs, and report files, never the broker and never the
network. No AI runs inside any package: the EOD narrative is written outside them by a scheduled agent
reading the fact set below.

## The reporting stack, layer by layer

```
cherrypick.core.ledgers  ── the per-schema readers (meic_ic / fly_book / earnings):
        │                     one home for the net, cost, capital and session rules
        │
        ├── report.run(session=day) ── unified cross-module P&L (gross/net, per profile)
        │
        └── packages/review ────────►  data/review/eod-<day>.json   THE FACT SET
                    │                        one versioned record per session, and the
                    │                        only thing any read surface reads
                    │
                    ├──►  eod-<day>.md        human render of those facts
                    ├──►  eod-<day>.note.md   the narrative, written beside them by a
                    │                          scheduled agent — never inside them
                    └──►  console at 127.0.0.1:5070 (packages/console)
```

**Nothing downstream re-derives.** That is the whole point of the fact set: the render, the console
page and the narrative read the same artifact, so they cannot hold different opinions about a session.
The arrangement this replaced had six report families and two normalisation layers, and they had
already drifted — the orchestrator read flies from `fly_positions` while the console's hand-copied
TypeScript port read `fly_books`.

**Retired 2026-08-13**, all replaced by the above: each module's `paper-eod-<day>.md` and
`eod-analysis-<day>.md`, the suite `eod-digest`, the opt-in `eod-insight` AI synthesis, and `advise`.

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

This one function is the single source of truth the review's fact set, the console, and calibration all
cite, so they can never disagree for the same day.

## The suite review (`packages/review`)

One versioned fact set per session across MEIC, flies and earnings, plus the renders of it. This
replaced the six per-module and suite report families on 2026-08-13.

| File | Content |
|---|---|
| `data/review/eod-<day>.json` | The facts. Per module: health (did the loop tick, entry attempts, gates), results, return on risk, expected-vs-observed against that module's own model, the per-arm split, and sample (raw n, effective n, measurement breaks). |
| `data/review/eod-<day>.md` | Human render: what needs attention first, then what each module did, the arms, expected against observed, and the trend. |
| `data/review/eod-<day>.note.md` | The narrative, written beside the facts by a scheduled agent — never inside them, so a failed or missing note cannot damage the record. |

Four properties the shape enforces, each of them a mistake this suite has already made:

- **`None` is not zero.** A field with no recorded value is null. Averaging "not recorded" as zero is
  what once made a cost model look 90% cheaper than it was.
- **Effective sample beside raw n.** Trades sharing a symbol and session share one market event —
  673 MEIC trades in a session are one day, not 673 observations.
- **Measurement breaks travel with the numbers**, and a trend never crosses one. A module that does
  not track breaks reports `null` rather than `[]`, so no trend can quietly assume continuity.
- **The arms are never collapsed.** MEIC's `open`/`width-5`/`width-10` and flies' arms run against the
  same underlying on the same sessions — a paired comparison, and the reason those arms exist.

**Provisional then final.** MEIC and flies are 0DTE and complete at the close; earnings opens before
it and settles the next morning. So the 16:30 pass writes a provisional set with earnings as carried
overnight risk, and the 10:15 pass next morning finalises it. The narrative only ever runs on a final
set, which is what lets it be written once and frozen.

**Reconciliation is not optional.** `python -m cherrypick.review reconcile` re-counts every module
with independent SQL — a different route to the same question than the readers took — and reports
deltas rather than merely failing. It caught a real scope bug on its first run.

Commands: `build [--session] [--final]`, `backfill [--since]`, `render [--session]`,
`reconcile [--since]`. Two supervisor jobs run the first of these daily.

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

The digest only fires **intraday on a trading day** (09:15–17:00 ET, the tail running past the bell so
the day's closing exits still make that day's roll-up). MEIC only trades the session, so an evening or
weekend push would repeat figures the last in-session digest already carried. A flush that comes due
outside that window is *held*, not dropped: the pending batch stays in state and goes out as one digest
on the next tick inside the window.

A digest line reads `MEIC digest 13:45 ET — SPX: 30 entries (open×10 width-10×10 width-5×10) · 2 exits
net +$48 · day 7 trades net +$61`, with a matching Discord card. Arms are **counted, not listed** — a
30-entry window would otherwise repeat the same three labels ten times each.

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
(the review's dated fact sets and renders) and rotated log backups (`*.log.N`) into
`logs/archive/<YYYY-MM>/<scope>.zip` — one zip per scope (the suite logs root + each module dir) — then
removes the originals once the zip verifies (`testzip()`). It is idempotent and safe: it never touches the
current month or an active `.log`, and a re-run (or a run after a missed month) converges. Registered as a
monthly `cherrypick-log-archive` task (on by default). `--dry-run` previews; `--month YYYY-MM` scopes to
one month.
