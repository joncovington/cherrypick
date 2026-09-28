# cherrypick-overview — Operational Instructions

> Operating contract for the suite's **pre-open morning market overview**. Suite-wide context is in
> the root [documentation index](../../docs/README.md).

This package answers one question before each open: **what does the market look like this morning,
measured by data this suite already produces.** It is the morning sibling of `packages/review` —
one versioned fact pack per session, a mechanical render, and a narrative written outside every
package — modelled on the daily "Market Overview" research-report format, but with every number
auditable because every number is ours.

**It is read-only over everything it touches.** It reads the shared stream cache (the streamer is
that cache's single producer), the GEX engine's regime history, the daily market files in
`~/.cherrypick/data/market-files/` (fetched by `scripts/fetch_market_files.py`, outside the
package), and — as a VIX fallback only — MEIC's `market_context` table. It writes only into its own home (`~/.cherrypick/data/overview`).
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
- **Proxies are labeled proxies.** Gold rides on GLD and the Russell 2000's cash read on IWM (the
  stream delivers nothing for the RUT index), and no surface prints either as the index or spot
  price. Crude has real futures in the pre-market tape (/CL, /BZ) since 2026-09-27; USO stays as
  the labelled proxy the deployment score was built on. The credit signal's HYG/TLT
  and the breadth signal's eleven sector ETFs carry the same label for the same reason.

## The daily market files

Some readings have no good stream source, so they come from files a script fetches each evening:
Cboe's SKEW, VIX, VVIX and VXN histories, Treasury's par yield curve, and the release calendars
(BEA always; FRED's CPI, jobs and PPI dates once a FRED key is stored). `files.py` holds the
parsers, and the fetcher imports them, so a file is validated on arrival by the code that reads it
and a download that parses to less than the file on disk is refused. Every reader takes values
**strictly before** the session: pre-open, a close dated today has not happened.

- **SKEW's percentile comes from Cboe's file, not the stream.** The stream's SKEW backfill returned
  five scattered rows in 270 days, so the row said "no daily series" from the start. With the file
  it ranks; without the file it says `no_cboe_file`, never "too few closes", which would promise a
  gap that fills by itself. VXN has no stream leg at all: its value is the file's latest close,
  labelled as that.
- **`moves`**: the weekly expected move (VIX / √52, and in SPX points) beside SPX's 10-session
  realized volatility, both record-only.
- **`yields`**: the last curve before the session, with the 2s10s and 3m10y spreads and each
  tenor's one-day change in basis points.
- **`calendar.releases`**: the next seven days' scheduled releases, with ET times where the source
  gives them. No consensus estimates: no free source has them.

- **`vol_regime.risk_reversal_25d_30d`**: the 30-day constant-maturity 25-delta risk reversal from
  Cboe's delayed SPX chain, reduced to one row a session by the fetcher. Near-the-money skew, never
  shown as SKEW.

## The pre-market tape

`premarket` carries /ES /NQ /YM /RTY /CL /BZ against their prior settle, and NDX, DJX and IWM's
prior-session moves. Three rules, each learned on the first live run (2026-09-27):

- **The contract comes from `state/futures_contracts.json`, never assembled here**, and a map older
  than five days is no futures legs at all — the gex recorder's rule, for the same reason.
- **A leg only gets daily rows if it declares `history_days`.** The producer writes live Summary
  rows for underlyings alone; a quote-only leg's rows come solely from the connect-time candle
  backfill. All nine new legs printed within seconds and none had a settle until the request
  declared 30 days for them.
- **The change is measured against a settle, never a trade.** For a live contract with no settle
  on file, the last trade is the live print itself, and a change against it reads a confident
  0.00%. It is unmeasured instead.

The same run found an older fault in the prior-close reader: with the prior session's row missing,
it took the newest row it could find, and IWM's Friday (+0.11%) printed as -1.83% against a close
two sessions back. The base is now only the print's own session row or the one exactly before it.

- **`calendar.earnings`**: the next seven days' announcements among the stocks the market report
  covers, each with the move its post-event straddle implies (`scripts/fetch_earnings_moves.py`,
  priced at the close; the suite's one definition, 0.85 x the ATM straddle, on the first expiration
  the market can trade the print on -- the earnings module's own rule, copied and pinned equal by a
  test). A file more than four days older than the session is refused, not shown as this week's.

None of these feed a gate or the deployment score. They are fact-pack v4 additions; a v3 pack
still renders.

## The deployment score is a measurement, not a gate

`score.py` blends five macro signals into a 0–100 `deployment` block in the pack — VIX percentile
against its trailing year, the VIX/VIX3M ratio, sector breadth against 200-day SMAs, an HYG/TLT
z-score, and VIX's 20-session rate of change. It is **record-only, and the block says so on
itself**: it feeds no gate, no phase and no sizing, and nothing in the suite reads it to decide
anything. The five-gate phase remains the operative morning verdict. The point of writing it down
first is that weeks of scores can be held against outcomes before anyone is allowed to act on one,
and the phase and the score are free to disagree in the meantime — that disagreement is data.

Four properties worth not breaking:

- **A declared gate that can never be measured is worse than four gates.** `calm_tape` (prior
  session within 1.5%) reported "not measured" on EVERY stored pack from the module's first
  session until 2026-08-27 — the phase was a five-gate verdict that only four gates could ever
  join, sitting permanently at `MIN_MEASURED_FOR_GREEN` with no margin. The cause was that this
  package runs pre-open, where today's `stream_summary` row does not exist yet, while the prior
  close's base was looked up on the CALENDAR date of the last trade — which for an overnight print
  is today. Both halves are fixed: the base falls back to the newest completed session's row, and
  the print is dated to the session it belongs to rather than the date it was stamped.

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

One consequence worth knowing, and it has been wrong in both directions: **which events a leg is
served has twice been the thing that silently starved this package.** Cash legs were Quote-only, so
the index readings — which publish Trade and never Quote — had no price at all and the whole panel
froze on 2026-08-17; Trade was added for them then, which is why those readings say `live` today.
Summary was still underlyings-only until 2026-08-25, so `day_close` never landed for any leg and
`daily_closes` stopped accumulating on 2026-08-14 — the series every percentile, SMA and z-score on
this page is built from. Both are fixed. The lesson worth keeping is that a leg is not simply "a
symbol with fewer events": each event type it is denied removes a specific, non-obvious capability,
and the loss shows up somewhere far from the subscription that caused it.

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
nothing else. **One documented deviation:** WebSearch/WebFetch stay allowed. The calendar no
longer needs them — the pack carries the week's releases and earnings since v4, and the prompt
forbids looking it up — but why a stock moved does: the headline file holds titles, not reasons.
The fence holds where it matters — no Bash, no Edit, no Write, so the agent can only ever return
prose, and market numbers must come from its inputs alone.

Those inputs are three deterministic artifacts: the pack; the technicals report for the last
session BEFORE the pack's (movers, breadth, stages, rotation, leaders — the console's pairing
rule, pinned by a test that fails at `<=`); and the morning's headlines from
`scripts/fetch_headlines.py` (title, source and time from the Fed, CNBC, MarketWatch and WSJ RSS
feeds; no article bodies). A missing one reaches the prompt as null and never stops the note. The
prompt asks for a mover's number from the report and its reason only as reported, and for an
unexplained mover to be named as unexplained.

## Scheduling

Two supervisor jobs (see `cfgmod.morning_settings`; config block `morning`): `morning-factpack`
(08:30 ET, on by default) runs `python run.py morning` → `python -m cherrypick.overview build`;
`morning-narrative` (09:00 ET, off by default, tag `ai`) runs the script, and `fetch-headlines`
(08:45 ET, on by default, `morning.headlines`) fetches what it reads. All trading-days-only
with a deliberately tight 90-minute catch-up — a pre-open pack caught up at 11:00 describes a
market that already opened.

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> ⚠️ Suite-wide guardrails apply — see root `CLAUDE.md` (no code in this file; masked accounts;
> portable paths; human-voice docs/commits). The fact pack is deterministic; the narrative is not,
> and is fenced accordingly — see "The narrative lives outside every package" above.

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
