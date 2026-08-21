# cherrypick-overview — Operational Instructions

> Operating contract for the suite's **pre-open morning market overview**. Suite-wide context is in
> the root [documentation index](../../docs/README.md).

This package answers one question before each open: **what does the market look like this morning,
measured by data this suite already produces.** It is the morning sibling of `packages/review` —
one versioned fact pack per session, a mechanical render, and a narrative written outside every
package — modelled on the daily "Market Overview" research-report format, but with every number
auditable because every number is ours.

**It is read-only over everything it touches.** It reads the shared stream cache (the streamer is
that cache's single producer), the GEX engine's regime history, and — as a VIX fallback only —
MEIC's `market_context` table. It writes only into its own home (`~/.cherrypick/data/overview`).
No broker credentials, no network, no chains: a pure stream-cache consumer in the calendars/pmcc
posture. Its market breadth (VIX/VIX3M/VVIX, the eleven sector ETFs, USO/GLD as labeled commodity
proxies) is declared through `state/stream_requests/overview.json` like any module's symbols; the
streamer serves the union.

## The artifact is the product

```
~/.cherrypick/data/overview/morning-<date>.json     the fact pack — the only thing any surface reads
~/.cherrypick/data/overview/morning-<date>.md       mechanical render of that pack
~/.cherrypick/data/overview/morning-<date>.note.md  the narrative, beside the facts, never inside them
```

The markdown render, the console's Reports page (Morning tab, beside the EOD review from
`packages/review`) and the narrative all read the same JSON, so they cannot disagree. Nothing downstream re-derives: the phase, the gate verdicts and the
strongest/weakest sectors are computed once, in this package, and displayed everywhere else.

## Rules the fact pack enforces

- **`null` is not zero, and pre-open honesty is provenance.** Every reading carries a `basis` —
  `live` (a quote fresh within two hours) or `prior` (the last completed session's confirmed value,
  with the session it belongs to) — and every surface renders prior values as prior. At 08:30 the
  gamma levels are the prior session's last confirmed recording and say so, exactly as the
  reference reports label their own pre-open dashboards.
- **The phase is mechanical; the editorial lives elsewhere.** GREEN/YELLOW/RED comes from five
  declared gates in `gates.py` (vol-curve contango, VVIX under its stress line, spot vs. our own
  gamma flip, inside the wall band, calm prior tape), each Met/Not-met/Unknown with its measured
  value printed. Missing data can never produce RED and always blocks GREEN. The thresholds are
  constants versioned in git, on purpose — a threshold in config is a knob someone turns
  mid-experiment; one in code has a commit explaining it. The free-form "risk monitor" — whatever
  macro theme is currently live — belongs to the narrative and is labeled interpretation; it never
  feeds the phase.
- **Proxies are labeled proxies.** The streamer has no futures path, so crude and gold ride on USO
  and GLD, and no surface ever prints them as a WTI or gold spot price. The credit signal's HYG/TLT
  and the breadth signal's eleven sector ETFs carry the same label for the same reason.

## The deployment score is a measurement, not a gate

`score.py` blends five macro signals into a 0–100 `deployment` block in the pack — VIX percentile
against its trailing year, the VIX/VIX3M ratio, sector breadth against 200-day SMAs, an HYG/TLT
z-score, and VIX's 20-session rate of change. It is **record-only, and the block says so on
itself**: it feeds no gate, no phase and no sizing, and nothing in the suite reads it to decide
anything. The five-gate phase remains the operative morning verdict. The point of writing it down
first is that weeks of scores can be held against outcomes before anyone is allowed to act on one,
and the phase and the score are free to disagree in the meantime — that disagreement is data.

Three properties worth not breaking:

- **A signal nobody could measure is UNKNOWN, never a default.** The blend renormalizes its
  declared weights over what it actually measured and records that it did; under four measured
  signals it refuses to produce a score at all, because two readings do not summarize a market.
- **The declared weights sum to 0.90 on purpose.** The missing tenth is the deferred
  factor-crowding signal's seat — it needs ~100 single-name daily histories the streamer has no
  reason to carry yet — kept visible rather than quietly redistributed, so adding it later does not
  silently reweight the other five.
- **The credit signal reads a ratio, and a ratio moves opposite a spread.** High yield falling
  against Treasuries is stress, and it pushes HYG/TLT *down*, so the stressed end of that z-score is
  negative. Copying the spread convention's endpoints inverts the signal; the constants name which
  end is which.

## What this package may ask the producer for

**Its breadth is quote-only, and quote-only means `legs`, not `symbols`.** In the streamer's
contract a `symbols` entry is an UNDERLYING — spot, an ATM window, GEX, and an option-chain fetch
repeated every subscription poll. Declaring the eleven sector ETFs plus VIX/VVIX/GLD/USO/HYG/TLT
there had the producer maintaining 0DTE chains for sixteen symbols nothing in the suite reads,
pushing it to ~20,000 subscriptions; it crash-looped on a locked cache and every trading module's
quotes went stale behind it, during market hours, on 2026-08-17. Only SPX belongs in `symbols`
here, and only because half the suite already streams it.

**A history request is a load decision, not a preference.** `history_days` at 1000 across sixteen
symbols did not merely cost more than 270 — it never finished, because each reconnect restarted the
backfill from the top, so the producer spent its life re-fetching four years of candles instead of
serving quotes. 270 covers everything the live score reads (a 252-session year, the 200-day SMAs
inside it). Raise it only deliberately, outside market hours, watching the producer while it lands.
The read-side backtest is deliberately decoupled: it reads whatever rows the cache actually holds,
so a generous history helps it without the request having to ask for one.

One consequence worth knowing: legs are served Quote and Summary events but **not** Trade events, and
`facts._live_quote` reads `stream_trades`. So the breadth readings render on their prior-session
basis rather than a live pre-open tick. That is honest (the pack labels every basis) and costs
nothing the pre-open report actually needs, but it is why those readings say `prior` on a session
when the producer is plainly up.

The history behind the percentiles, SMAs and z-scores comes from `stream_summary` via the request
file's `history_days` field — the streamer backfills a deficit once from DXLink daily candles, so
the series exists on day one instead of accruing over a year of sessions. Reading that table has one
trap `facts._close_history` exists to handle: `day_close` belongs to its own row's session, while
`prev_day_close` belongs to the session *before* its row, and today's row is read for its
`prev_day_close` (the freshest settle there is) but never appears in the series.

## The narrative lives outside every package

`scripts/morning_narrative.py` writes `morning-<day>.note.md` beside the pack — the same fence as
`scripts/eod_narrative.py`, for the same reasons: a script the scheduler runs cannot be imported by
a loop, no package gains an API key or a network dependency, and deleting it costs a note and
nothing else. **One documented deviation:** WebSearch/WebFetch stay allowed, because the macro
calendar (CPI/PPI times, notable earnings) has no deterministic source in the suite and is fetched
at render time instead of curated. The fence holds where it matters — no Bash, no Edit, no Write,
so the agent can only ever return prose, and market numbers must come from the pack alone.

## Scheduling

Two supervisor jobs (see `cfgmod.morning_settings`; config block `morning`): `morning-factpack`
(08:30 ET, on by default) runs `python run.py morning` → `python -m cherrypick.overview build`;
`morning-narrative` (09:00 ET, off by default, tag `ai`) runs the script. Both trading-days-only
with a deliberately tight 90-minute catch-up — a pre-open pack caught up at 11:00 describes a
market that already opened.

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> ⚠️ This file is strictly for build commands, tech-stack reference, and project guidelines:
> - **No code here** — no Python, no scripts, no logic, and no scratchpad content, changelogs, or task trackers.
> - **Mask account numbers** to the last 4 digits (`****1234`) anywhere they surface.
> - **Portable paths only** — never hardcode absolute paths, usernames, hostnames, or drive letters.
> - **Human-voice docs & commits** — never add AI/co-author attribution to commit messages.
> - **The fact pack is deterministic; the narrative is not, and is fenced accordingly.** Every
>   reading here is computed from stored data. The narrative is deliberately generated *outside*
>   this package by a scheduled agent reading the fact pack, so this package acquires no API key and
>   no network dependency, and a failed narrative can never damage a report. That containment is the
>   pattern the root file's preference points at.

## Tool Reference

| Command | Purpose |
|---|---|
| `python -m cherrypick.overview build [--session YYYY-MM-DD]` | Build and write one session's fact pack and render. Defaults to today's ET trading day. Also refreshes the stream request, best-effort. |
| `python -m cherrypick.overview render [--session YYYY-MM-DD]` | Re-render one session's markdown from its pack. |
| `python -m cherrypick.overview score-history [--session YYYY-MM-DD]` | Recompute the deployment score across stored history and report what its zones would have separated, into `score-history.json`. Read-only research over the cache — it schedules nothing and decides nothing. A session's zone comes from the score computed the session BEFORE it, so the overlay cannot look ahead, and the forward return is SPX's own next-session move: a benchmark for whether the zones separate regimes at all, **not** suite P&L, since no trade was taken on any of those sessions. Read `score_distribution` first — a score that puts nearly every session in one zone is a constant, not a signal. |
| `python -m cherrypick.overview request` | (Re)write `state/stream_requests/overview.json` without building anything. |
| `python scripts/morning_narrative.py [--session] [--force] [--dry-run]` | Write the narrative beside the pack (run from the repo root; not part of this package). |

## Where the shared rules live

Paths resolve through `cherrypick.core.home`; the trading calendar (holidays, FOMC, witching) is
`cherrypick.core.calendar`; the stream cache contract is `cherrypick.core.streamcache` and the
request contract `cherrypick.core.streamrequests`. The GEX numbers this package displays are
computed by `packages/gex` with the math in `cherrypick.core.gex` — this package reads the recorded
history and computes none of it.
