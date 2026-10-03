# cherrypick-overview — Operational Instructions

> Operating contract for the suite's **pre-open morning market overview**. Incident narratives:
> [docs/history.md](docs/history.md). Suite-wide context: the root
> [documentation index](../../docs/README.md).

One question before each open: **what does the market look like this morning, measured by data this
suite already produces.** The morning sibling of `packages/review` — one versioned fact pack per
session, a mechanical render, a narrative written outside every package — with every number
auditable because every number is ours.

**Read-only over everything it touches** (posture: root file): the stream cache, the GEX engine's
regime history, the daily market files in `~/.cherrypick/data/market-files/` (fetched by
`scripts/fetch_market_files.py`), and MEIC's `market_context` as a VIX fallback only. Writes only
`~/.cherrypick/data/overview`. Breadth is declared through `state/stream_requests/overview.json`.

## The artifact is the product

```
~/.cherrypick/data/overview/morning-<date>.json     the fact pack — the only thing any surface reads
~/.cherrypick/data/overview/morning-<date>.md       mechanical render of that pack
~/.cherrypick/data/overview/morning-<date>.note.md  the narrative, beside the facts, never inside them
```

The render, the console's Reports page (Morning tab) and the narrative all read the same JSON.
**Nothing downstream re-derives**: phase, gate verdicts and strongest/weakest sectors are computed
once, here.

## Rules the fact pack enforces

- **`null` is not zero; pre-open honesty is provenance.** Every reading carries a `basis` — `live`
  (fresh within two hours) or `prior` (the last completed session's confirmed value, naming the
  session) — and every surface renders prior as prior. At 08:30 the gamma levels are the prior
  session's last recording and say so.
- **The phase is mechanical.** GREEN/YELLOW/RED comes from five declared gates in `gates.py`
  (vol-curve contango, VVIX under its stress line, spot vs. our gamma flip, inside the wall band,
  calm prior tape), each Met/Not-met/Unknown with its measured value printed. **Missing data can
  never produce RED and always blocks GREEN.** Thresholds are constants in git, on purpose — a
  config threshold is a knob turned mid-experiment; one in code has a commit explaining it. The
  "risk monitor" macro theme belongs to the narrative, labelled interpretation, and never feeds the phase.
- **A declared gate that can never be measured is worse than four gates.** Verify each gate can
  actually reach Met/Not-met pre-open (`calm_tape` sat "not measured" on every pack for weeks
  because it looked up today's not-yet-existing row).
- **Proxies are labelled proxies.** Gold on GLD, the Russell 2000's cash read on IWM (no RUT index
  in the stream), crude's deployment-score read on USO (real /CL, /BZ futures ride the pre-market
  tape since 2026-09-27), HYG/TLT for credit and the eleven sector ETFs for breadth. No surface
  prints a proxy as the index or spot.

## The daily market files

Readings with no good stream source come from files a script fetches each evening: Cboe's SKEW,
VIX, VVIX and VXN histories, Treasury's par yield curve, release calendars (BEA always; FRED's
CPI, jobs and PPI once a FRED key is stored; each FRED fetch is also folded into
`fred_history.json`, which never drops a date, seeded once with `fetch_market_files.py fred-history
--since`; Census's economic-indicators calendar likewise into `census.json`, and Michigan's next-release note into `umich.json`), and OCC's daily option volume by underlying with
Nasdaq Trader's symbol directory. `files.py` holds the parsers and the fetcher imports
them, so a file is validated on arrival by the code that reads it, and a download that parses to
less than the file on disk is refused. **Every reader takes values strictly before the session.**

- **SKEW's percentile comes from Cboe's file, not the stream.** Without the file it says
  `no_cboe_file`, never "too few closes" (which promises a gap that fills itself). VXN is the file's
  latest close, labelled as such.
- **`moves`**: weekly expected move (VIX / √52, and in SPX points) beside SPX's 10-session realised
  vol, record-only. **`yields`**: the last curve before the session, 2s10s and 3m10y, and one-day
  changes in bp. **`calendar.releases`**: the next seven days with ET times where given; no
  consensus estimates (no free source). **`vol_regime.risk_reversal_25d_30d`**: the 30-day 25-delta
  risk reversal from Cboe's delayed SPX chain, one row a session — never shown as SKEW.
- **`calendar.earnings`**: the next seven days' announcements among the market report's stocks, each
  with its implied move (`scripts/fetch_earnings_moves.py`: 0.85 x the ATM straddle on the first
  expiration that can trade the print — the earnings module's rule, copied and pinned equal by a
  test). A file more than four days older than the session is refused.
- **`hot_options`** (`occ.py`): OCC's cleared option volume for the newest session before the pack,
  ranked the way the Hot Options Report ranks it — the five index products (VIX, SPY, SPX, IWM, QQQ),
  then the top ten single-name equities and top five funds, each with call/put split, the customer
  share of sides and volume against up to 20 prior sessions. Record-only.
  - **OCC states both sides of every trade**, so contracts are its quantities halved (VIX 2026-09-30:
    1,070,156 in the file, 535,078 at Cboe). The store keeps the sides; halving is on read.
  - **OCC publishes a session late that evening** (10-01's file appeared between 23:12 and 23:22
    ET), after the 18:45 fetch, so the 07:45 retry is what lands it before the pack. The block names
    its session and counts `lag_sessions` behind the pack's prior one; more than three behind is
    refused (`stale_occ_file`).
  - **Stock or fund is Nasdaq Trader's `ETF` flag**, never guessed: no directory leaves equities
    unranked (`no_listings_file`), and an unlisted name ranking in the top 25 (a cash index like
    XSP) is named in `unclassified`.

## The pre-market tape

`premarket` carries /ES /NQ /YM /RTY /CL /BZ /GC against their prior settle, plus NDX, DJX and IWM's
prior-session moves.

- **The contract comes from `state/futures_contracts.json`, never assembled here**; a map older than
  five days means no futures legs at all (the gex recorder's rule).
- **A leg only gets daily rows if it declares `history_days`** — a quote-only leg's rows come solely
  from the connect-time candle backfill.
- **The change is measured against a settle, never a trade.** No settle on file → unmeasured, never
  a confident 0.00% against the live print.
- **The prior-close base is only the print's own session row or the one exactly before it** — never
  "the newest row found".

None of the files or tape readings feed a gate or the deployment score. They are fact-pack v4
additions; a v3 pack still renders.

## The deployment score is a measurement, not a gate

`score.py` blends five signals into a 0–100 `deployment` block: VIX percentile over its trailing
year, VIX/VIX3M, sector breadth against 200-day SMAs, an HYG/TLT z-score, and VIX's 20-session rate
of change. **Record-only, and the block says so**: no gate, phase or sizing reads it. The five-gate
phase remains the operative verdict; weeks of scores are held against outcomes before anyone may act
on one, and disagreement between the two is data.

- **An unmeasured signal is UNKNOWN, never a default.** The blend renormalises over what it measured
  and records that it did; under four measured signals it refuses to score.
- **Declared weights sum to 0.90 on purpose.** The missing tenth is the deferred factor-crowding
  signal's seat (needs ~100 single-name histories), kept visible so adding it later does not
  silently reweight the others.
- **The credit signal reads a ratio, which moves opposite a spread**: stress pushes HYG/TLT *down*,
  so the stressed end of that z-score is negative. The constants name which end is which.

## What this package may ask the producer for

- **Quote-only breadth means `legs`, not `symbols`.** A `symbols` entry is an UNDERLYING (spot, ATM
  window, GEX, a chain fetch every poll). Declaring breadth there once pushed the producer to ~20,000
  subscriptions and crash-looped it, staling every module's quotes in market hours. Only SPX belongs
  in `symbols` here, because half the suite already streams it.
- **A history request is a load decision.** `history_days` is 270 — enough for everything the live
  score reads (a 252-session year, 200-day SMAs). 1000 never finished (each reconnect restarted the
  backfill). Raise it only deliberately, outside market hours, watching the producer. The read-side
  backtest reads whatever rows the cache holds, independent of the request.
- **Each event type a leg is denied removes a specific capability**, and the loss shows up far from
  the subscription that caused it: index readings need Trade (they never publish Quote); `day_close`
  needs Summary. Both have silently starved this package before.
- **The history trap** (`facts._close_history`): in `stream_summary`, `day_close` belongs to its own
  row's session and `prev_day_close` to the session *before*; today's row is read for its
  `prev_day_close` (the freshest settle) but never appears in the series.

## The narrative lives outside every package

`scripts/morning_narrative.py` writes `morning-<day>.note.md` — the `scripts/eod_narrative.py` fence:
no loop can import it, no package gains an API key or network dependency, and deleting it costs a
note. **One documented deviation: WebSearch/WebFetch stay allowed**, because why a stock moved is not
in the headline file (titles only); the calendar needs no lookup and the prompt forbids it. The fence
holds where it matters — no Bash, Edit or Write, so the agent can only return prose, and market
numbers come from its inputs alone.

Inputs are three deterministic artifacts: the pack; the technicals report for the last session
BEFORE the pack's (the console's pairing rule, pinned by a test that fails at `<=`); and the
morning's headlines from `scripts/fetch_headlines.py` (title, source, time from Fed, CNBC,
MarketWatch and WSJ RSS; no bodies). A missing input reaches the prompt as null and never stops the
note. A mover's number comes from the report, its reason only as reported; an unexplained mover is
named as unexplained.

## Scheduling

Supervisor jobs (`cfgmod.morning_settings`; config block `morning`): `morning-factpack` (08:30 ET, on
by default) runs `python run.py morning` → `python -m cherrypick.overview build`; `fetch-headlines`
(08:45 ET, on, `morning.headlines`); `morning-narrative` (09:00 ET, off by default, tag `ai`). All
trading-days-only with a deliberately tight 90-minute catch-up — a pack caught up at 11:00 describes
a market that already opened.

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> ⚠️ Suite-wide guardrails apply — see root `CLAUDE.md`. The fact pack is deterministic; the
> narrative is not, and is fenced accordingly (above).

## Tool Reference

| Command | Purpose |
|---|---|
| `python -m cherrypick.overview build [--session YYYY-MM-DD]` | Build and write one session's fact pack and render (default today's ET trading day). Also refreshes the stream request, best-effort. |
| `python -m cherrypick.overview render [--session YYYY-MM-DD]` | Re-render one session's markdown from its pack. |
| `python -m cherrypick.overview score-history [--session YYYY-MM-DD]` | Recompute the deployment score over stored history into `score-history.json`. Read-only research; schedules and decides nothing. A session's zone comes from the score the session BEFORE (no look-ahead); the forward return is SPX's next-session move — a benchmark for whether zones separate regimes, **not** suite P&L. Read `score_distribution` first: a score that puts nearly every session in one zone is a constant, not a signal. |
| `python -m cherrypick.overview request` | (Re)write `state/stream_requests/overview.json` without building anything. |
| `python scripts/morning_narrative.py [--session] [--force] [--dry-run]` | Write the narrative beside the pack (from the repo root; not part of this package). |

## Where the shared rules live

Paths: `cherrypick.core.home`; trading calendar: `cherrypick.core.calendar`; stream cache contract:
`cherrypick.core.streamcache`; request contract: `cherrypick.core.streamrequests`. GEX numbers are
computed by `packages/gex` (math in `cherrypick.core.gex`); this package reads the recorded history
and computes none of it.
