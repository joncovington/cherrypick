# The market-regime recorder — a proposal

*Drafted 2026-08-23. **Phase 1 landed the same day**: `market_regime_history` + `daily_closes` in
the gex recorder (`packages/gex/src/cherrypick/gex/regime.py`, 60s cadence), the recorder's own
quote-only legs declaration with `history_days: 270`, and the `cherrypick.core.regime.regime_at`
join helper — all four guards below verified by breaking them on purpose.*

***First live session 2026-08-24: the series works.** 352 rows over the first 16 minutes, 22/22
readings printing, **zero refusals**, one sample per minute from the opening bell; `daily_closes`
backfilled 15,963 closes across 17 symbols back to 2021-02-14; and `regime_at` joins live, deriving
every ratio (VIX/VIX3M 0.857, VVIX/VIX 5.58, RSP/SPY 0.291, HYG/LQD 0.750, GLD/SPY 0.561, sector
dispersion 2.91%) alongside SPX's GEX row.*

***The entitlement probe ran the same morning and the reading list is now FROZEN — results below
("What the 2026-08-24 probe settled"). Still open**: adding the three admitted readings, the /VX
roll helper, Tier 2 chain math, and the fact-pack migration once the series has accumulated. When
the rest ships, the landing note belongs in `docs/history/` per that folder's convention, and this
file becomes the frozen record.*

## What the 2026-08-24 probe settled

One RTH session, three sub-probes, all throwaway (a temporary `regimeprobe` request file, deleted
after; a scratch script using the shared credential read-only — no orders, nothing recorded).

| Candidate | Verdict | Evidence |
|---|---|---|
| **SKEW** | **ADMIT** — one `READINGS` line | Printed 143.9 to `stream_trades` via the ordinary legs path, 31s fresh — identical mechanics to VIX |
| **VIX9D** | **ADMIT** — one `READINGS` line | Printed 14.53 the same way; front-of-curve vol, and VIX9D/VIX is a read-side ratio |
| **/VX term structure** | **ADMITTED — built 2026-08-24** as readings `vx1`/`vx2` | The whole curve prints: `/VXU26:XCBF` 17.55, `/VXV26` 19.20, `/VXX26` 19.87, `/VXZ26` 20.10 against spot VIX 15.97 |
| **/ZN (10Y note)** | **ADMITTED — built 2026-08-24** as reading `zn1` | Printed to `stream_trades` AND `stream_quotes` through the legs path, 27s fresh, both Sep and Dec contracts |
| **Market internals** (`$TICK`, `$TRIN`, `$CPC`, `$TICKI`, `$ADD`, `.NY` variants) | **REFUSE — not entitled** | Seven symbol variants subscribed as TimeAndSale for 25s: **not one print**. Not a symbology guess — see below |
| **MOVE** | Already refused (2026-08-23 research) | Institutional ICE feed only; TLT and HYG/LQD stay the reachable proxies |

Four findings worth keeping, because each corrects something this plan previously assumed:

- **The internals verdict is trustworthy, and cost nothing to establish.** This plan said they were
  "untestable without a producer change" — wrong. `cherrypick.core.dxfeed.collect_events` is
  generic over the event class and the SDK exports `TimeAndSale`, so entitlement was testable on
  demand in one script. The producer change would only ever have been needed to RECORD them
  continuously, never to ask whether we may. Ask the cheap question first.
- **The futures MIC is `XCBF`, not `XCFE`.** The first probe declared `/VXU26:XCFE` and saw
  nothing, which would have read as "CFE not entitled" — the wrong conclusion from a wrong guess.
  The authoritative answer came from the instruments endpoint (`Future.get(..., product_codes=…)`
  returns `streamer_symbol` per contract). **Any futures reading must take its symbol from that
  endpoint rather than assembling one**, which is also how the roll helper should work.
- **Futures need no producer change at all.** The legs path passed exchange-suffixed symbols
  through unmangled and both `/ZN` contracts landed in the cache within a poll. A futures reading
  is an ordinary `READINGS` entry plus a monthly-rolling declaration.
- **Indices publish Trade events, not Quote.** SKEW/VIX9D/VIX/VIX1D all printed as Trade and none
  as Quote — confirming the recorder's existing choice to read `stream_trades` for cash legs, and
  a caution against "fixing" that to `stream_quotes` later.

**The roll helper — built 2026-08-24.** `scripts/refresh_futures_contracts.py` resolves the product
codes through the instruments endpoint and writes `state/futures_contracts.json`; the recorder reads
that map and samples `vx1`/`vx2`/`zn1`. Four properties, each with a reason:

- **It lives in `scripts/`, outside every package.** Contract resolution needs the broker; the
  recorder is credential-free and network-free and stays that way. Same fence as the narratives — a
  spawned process whose failure costs a refresh and nothing else. A `futures-contracts` supervisor
  job runs it at 08:45 ET on trading days.
- **The reading name is stable and the row carries the contract.** `vx1` persists across a roll
  while the row's `symbol` records `/VXU26:XCBF` — so the roll is visible in the data, and no row is
  ever a blended constant-maturity value. This is what the long-row shape was for.
- **A stale map (>5 days) drops the futures readings entirely.** Sampling a rolled-off contract
  would leave a plausible-looking series that is quietly wrong; a gap is legible. Verified by
  breaking the guard.
- **`/ZN` takes the ACTIVE month, `/VX` takes consecutive expirations.** They differ on purpose: the
  VX roll yield is a relationship between adjacent contracts, while a Treasury future's liquidity
  leaves ahead of first notice — on 2026-08-24 September still traded while December was active, and
  "nearest" would have recorded the illiquid tail of an expiring contract as the rates market.

## The finding this comes from

An audit of regime capture across the trading modules (2026-08-23) asked one question three ways:
does each module record the market regime at entry, at exit, and at each management decision?

| Module | Entry | Exit | Management | Where it lives |
|---|---|---|---|---|
| meic | yes — gamma flip, GEX net/spot/vol, IVR, VIX1D ratio, ATR, skew, trend | no dedicated columns | yes — `market_context` + `iteration_regime` every tick, refusals included | meic's own store, joinable by date |
| flies | yes — `entry_*` regime columns | yes — `completion_*` twins on the same row | re-read fresh each phase | on-row; no join needed |
| bwb | yes — gamma flip + basis + spot, every tick | same row at close | yes — trigger latch persists the firing tick's reading | on-row |
| curve | `entry_regime` string on the trade row | **no** | reads the day's regime to decide flips, doesn't write it back | separate daily `curve_regime` table, joinable by date only |
| calendars | IV/term-structure only — no VIX, GEX, breadth | no | no | — |
| pmcc | no | no | no | — |
| earnings | no | no | no | — |

Two more findings frame the problem. First, `overview` computes the richest single regime snapshot
in the suite — VIX/VIX3M/VVIX, gamma flip and walls, sector breadth — once per session, and
**nothing else reads it**: a grep across review, advisor, and every trading module found zero
consumers. Second, the advisor's fact pack builds its regime block by scraping meic's private
`market_context` table plus `gex_regime_history` — a session-wide sidecar assembled from another
module's internals, which works only because meic happens to record what the advisor happens to
need.

So three modules capture regime thoroughly, one captures entry only, and three capture nothing —
and every future question of the form "how did entries under backwardation do?" is answerable for
meic and unanswerable for pmcc, not because anyone decided that, but because regime capture grew
per-module.

## What already exists, and what the gap actually is

The suite already runs half of the answer. The gex recorder (`packages/gex`, `run.py record`) is an
always-on, credential-free daemon that reads the shared stream cache read-only and writes its own
history database. Since the `gex_regime_history` table was added it records, for every offered
symbol on a ~5-minute cadence all session long: spot, net GEX, net GEX volume, zero-gamma (the
flip), call wall, put wall. Its own schema comment states the purpose — *"what did GEX look like
when that trade was accepted?"* — and the advisor already reads it. **The dealer-positioning half
of the regime is a joinable suite-level time series today.**

What has no suite-level time series is everything else:

- **The vol complex.** VIX, VIX3M, VVIX, VIX1D and their ratios exist intraday only inside meic's
  store (scoped to meic's session), as one row per day in `curve_regime`, or as overview's single
  pre-open reading. There is no answer anywhere to "what was VIX at 13:42."
- **Breadth and cross-asset.** Sector readings exist only in overview's once-per-session snapshot.
- **Per-symbol IV.** ATM IV is computed transiently wherever a module needs it and stored nowhere
  as a series — which means IV rank, one of the most-used conditioners in options work, cannot
  exist until an ATM-IV history starts accumulating.

## The design

One sentence: extend the gex recorder with a market-regime companion table sampled the same way
the spot trail already is, add a permanent daily-closes table, and give the read side one shared
join helper in `cherrypick.core` — so any module's entries, exits, and decisions can be
regime-tagged by timestamp join, with zero changes to any decision path.

### The recorder

The gex recorder gains a second sampling duty alongside `record_spots`: every 1–5 minutes during
RTH (cadence to be fixed at implementation; the spot trail's 15s is finer than regime needs), read
the declared regime symbols from the stream cache and write one row per reading into a new
`market_regime_history` table in the recorder's own history database.

Rules the row must obey, each inherited from a lesson already paid for elsewhere in the suite:

- **Store the measure, never just a bucket.** Continuous values only — the raw quote, the raw
  ratio. Buckets are a read-side cut (`bucket_edges`), recalibratable forever; a stored bucket is
  frozen at whatever threshold looked right the week it shipped. This is meic's `gex_net_at_entry`
  rationale and flies' "store the measure" rule, verbatim.
- **Basis-stamped and RTH-gated from day one.** Every reading carries its quote timestamp, and a
  stale or frozen quote writes a row marked unusable with the refusal reason — never the last
  value the feed happened to freeze on. This is `curve_regime`'s posture, adopted after the
  advisor's GEX-counts lesson (2026-08-21): an overnight-frozen recorder double-weights whatever
  sign the session ended on.
- **A hole is a marked row, not a gap.** A tick where the feed refused writes an unusable row, so
  a stretch of thin data reads afterwards as "the feed was thin" and a stretch of no rows at all
  reads as "the recorder was down" — the flies `fly_snapshots` distinction, which is what made
  the 2026-07-20 outage legible as an ops failure rather than a quiet market.
- **The recorder writes only its own database.** The stream cache stays read-only under it, per
  the gex package's standing invariant.

### What gets sampled, in tiers by cost

**Tier 1 — plain quote samples.** Each is one row of arithmetic over quotes the streamer can carry
as quote-only legs:

| Reading | Regime it captures |
|---|---|
| VIX, VIX3M, VIX/VIX3M | the term-structure regime (curve's gate, intraday for the first time) |
| VIX9D, VIX9D/VIX | front-of-curve vol — event-week pricing (FOMC/CPI) sharper than VIX/VIX3M |
| VIX1D | the 0DTE modules' natural vol read |
| VVIX, VVIX/VIX | vol-of-vol — whether the vol market itself is nervous |
| SKEW | tail-risk pricing, independent of ATM vol level |
| RSP/SPY | breadth via equal-weight vs cap-weight — narrow leadership vs broad participation |
| HYG/LQD | credit risk appetite, which often leads equity vol turns |
| TLT | duration/rates; the bond-equity correlation state |
| GLD, USO | the overview commodity pair (labeled proxies, added 2026-08-23): gold+TLT read together split risk-off into fear vs inflation/dollar regimes; oil is its own vol driver. ETFs deliberately, not /GC//CL futures — during RTH they track at beta ~1, and the futures would buy contract-roll machinery plus an unproven entitlement for hours the RTH-gated sampler never records. Revisit the carrier only if sampling ever extends overnight |
| sector ETF dispersion | rotation days vs monolithic days (cross-sectional spread of the moves) |

**Tier 2 — chain math over greeks/OI already streamed**, per offered symbol:

- ATM IV — the direct per-symbol vol reading, and the series IV rank falls out of later for free.
- Expected move (the ATM straddle) — calendars stores it at entry; as a series it is joinable by
  everyone.
- 25-delta risk reversal — the suite-wide form of flies' `skew_bucket`.
- Put/call OI ratio and near-spot gamma concentration. Concentration must use the windowed
  top-strikes form: flies' whole-chain version came back degenerate 60/60 before being windowed,
  and re-deriving that lesson would be re-paying for it.

**Futures quotes — researched 2026-08-23, probe-gated like the internals.** tastytrade's DXLink
carries futures as ordinary streamed symbols (`/VXU26:XCFE`-style streamer symbols, handed back by
the instruments endpoints), and the suite's streamer already has the mechanism: quote-only `legs`
in a stream request are plain streamer-symbol strings subscribed as Quote/Trade events, exactly
how VIX and VIX3M ride today. No new credential appears anywhere — the producer holds the only
session, as ever. Two readings would earn their place:

- **/VX front and second month** — the *actual* VIX futures term structure: VX1/VX2 contango and
  the VIX−VX1 basis are the object curve harvests through the VXX proxy, and a direct read of the
  roll yield rather than the VIX/VIX3M shadow of it. The front-month symbol is deterministically
  computable from the calendar (VX settlement is pinned to SPX opex; month code and year are
  string assembly), so the recorder can roll its declaration monthly the same way curve computes
  its target expiration — no per-day API lookup.
- **/ZN (or /ZB)** — the rates regime read that trades nearly 23 hours, and — since MOVE itself
  is unreachable (below) — the raw material for a poor-man's bond-vol read: a /ZN trail in the
  recorder's store makes treasury realized vol a Tier-3 derivation over data we own.

Discipline the rows must carry: record the raw quote **per contract, with the contract identity
on the row** — never a pre-blended "constant maturity" value. Roll days step; a blend is a
read-side computation over identified contracts, recalibratable forever, while a stored blend
freezes one roll convention. This is "store the measure" applied to futures.

Both wait on the same empirical probe as the internals: whether the retail api-quote-token is
entitled to CFE and CBOT futures data is undocumented (community SDK usage suggests yes — /ES
subscriptions are routine in tastyware examples — and the 2024-era "equities only" complaints
predate the current token and are contradicted by our own SPX/greeks streaming today), and
whether the streamer's legs path passes an exchange-suffixed symbol through unmangled is untested.
One RTH probe answers both.

**Tier 3 — not recorded, retained.** Realized vol, intraday chop-vs-trend (directional efficiency),
and trend-from-open are pure functions over the spot trail the recorder already keeps — they need
no new recording, only the read-side functions. Distance-from-moving-averages and gap statistics
need a durable daily-close history: a small permanent `daily_closes` table (symbol, date, close),
appended once per session, replaces the current situation where overview reads a close history it
does not own and flies documents `stream_summary` retention as "a window that will close." That
one table retroactively unlocks the entire moving-average/percentile family for every date after
it starts.

### The subscription

The recorder declares its own file under `state/stream_requests/` for every Tier-1 symbol, as
quote-only `legs` — never `symbols`, which would have the producer maintain option chains for
tickers nothing reads (the overview 2026-08-17 incident, cited by curve's plan for the same
choice). Today VIX/VIX3M stream only because curve declares them and the sector ETFs only because
overview does; the regime series must not go dark because some other module's declaration changed.
This follows the suite's guard rule of driving coverage off what a module itself declares.

### The read side

One helper in `cherrypick.core` — call it `regime_at(ts, symbol=None)` — returns the nearest
`market_regime_history` and `gex_regime_history` rows within a declared staleness bound, or an
explicit "unmeasured" refusal beyond it. Everything joins through it: review can tag every
module's entries and exits, the console can draw regime context under any timeline, and the
advisor's fact pack migrates from scraping meic's `market_context` to reading the canonical
series. Three consumers doing the nearest-row join by hand is how three subtly different staleness
rules would be born.

### What is deliberately not recorded

- **Calendar context** — days to OPEX, FOMC proximity, 0DTE-heavy days. A pure function of the
  date is re-derivable forever; recording it stores nothing the date doesn't already hold. It
  belongs in a read-side function beside `regime_at`.
- **MOVE (bond vol)** — researched 2026-08-23 and settled as unreachable: ICE publishes it
  near-real-time only over the ICE Consolidated Feed / Global Index Feed, an institutional
  entitlement tastytrade's retail DXLink does not carry. Every free intraday source is a scrape of
  a delayed web quote — a network dependency of exactly the kind the suite leans away from. TLT
  and HYG/LQD (already Tier 1) are the reachable proxies for the rates/credit-vol regime; if MOVE
  itself is ever wanted, that is a new data dependency to write down deliberately, not a symbol to
  add.
- **Market internals** ($TICK-family, advance/decline, VOLD) — researched 2026-08-23 and settled
  as *plausible but unproven*, so still excluded from the reading list until probed. dxFeed (the
  feed behind tastytrade's DXLink) does publish a Market Indicators family — 400+ symbols
  covering TICK, TRIN, advance/decline and put/call ratios per exchange and index (`$TICK`,
  `$TRIN`, `$CPC`, …) — and tastytrade's own help center documents market indicators on its
  platform, so the data exists on the other side of the wire. Two things keep it out of Tier 1:
  whether tastytrade's API token is *entitled* to those symbols is undocumented, and the
  indicators are delivered as **TimeAndSale events, not Quote events** — the suite's streamer
  subscribes Quote/Greeks/Summary, so carrying them means a contained producer change, not just a
  declared symbol. The verification is a one-off RTH probe: subscribe the candidate symbols as
  TimeAndSale over the existing session and see what prints. If it prints, internals (and the
  equity put/call ratio, a regime read in its own right) join Tier 1 with their own tier note; a
  declared symbol that never prints would otherwise be indistinguishable from a dead feed.

## Why this shape and not per-module stamping

The alternative is the flies pattern everywhere: each module stamps regime columns onto its own
rows at entry and exit. That is the higher-fidelity answer — the row records exactly what the
decision saw — and the modules whose *gates* read regime (meic, bwb, flies) already have it and
keep it. But extending it to calendars, pmcc, earnings, and curve's exits means touching seven
loops, and for paper modules whose regime use is purely read-side telemetry, a ≤5-minute join is
fine. The join approach covers every module — including refusal rows, management events, and
modules not yet written — for the cost of one recorder change.

The two are not in tension. The series is telemetry; nothing gates on it. Any module that later
wants a regime-conditioned *gate* must read its own snapshot on its own decision path and stamp
its own row, exactly as meic and bwb do today — a gate reading a 5-minute-old sample from another
process's database would be a new and worse pattern.

## Properties worth stating

- **Not measurement-affecting.** No decision path changes and no recorded number changes meaning —
  the change only records more. Under the suite's batching rule it can land immediately, no
  declared boundary needed.
- **No backfill exists, so sequencing matters more than completeness.** Flies documented this
  hard: regime data has no backfill path, and every session before the recorder starts is
  regime-blind forever. Land the recorder and the subscription first, even before any consumer
  reads a row. A month of unconsumed series is an asset; a month of design discussion is a month
  of blind sessions.
- **Single writer, own store.** The recorder writes its own history database and nothing else —
  the same shape as the streamer's single-producer rule, and enforceable the same way.

## Guards, each to be shown to fail

Per the house rule that a guard has to be shown to fail before it counts:

1. **Frozen-quote refusal** — feed the sampler a quote older than the staleness bound and assert
   the row lands unusable with the refusal, not with the stale value.
2. **Declaration coverage** — assert every Tier-1 reading's symbol appears in the recorder's own
   stream-request file, driven off the recorder's declared reading list rather than a hand-kept
   test constant, so adding a reading without its subscription fails the build.
3. **Stale-writer columns** — the flies `stale_writer_columns` pattern: if the history database
   carries columns the running checkout cannot fill, log it at recorder start. A stale checkout
   silently writing NULLs cost flies a full session's regime data on 2026-08-05.
4. **Join staleness** — assert `regime_at` refuses beyond its bound rather than returning the
   nearest row regardless of distance.

## Open questions

- **Does this broaden the gex package's charter too far?** The recorder is the natural host — it
  is already the suite's always-on sampler with its own history store — but "GEX engine and
  recorder" becomes "GEX engine and market recorder." The alternative is a sibling recorder
  package sharing the daemon pattern. Leaning toward extending gex: one daemon, one store, one
  supervision entry, and the console already reads its database. Decide at implementation and
  update the gex CLAUDE.md either way.
- **Cadence.** 1 minute costs ~390 rows/session/table and matches the finest module tick; 5
  minutes matches `gex_regime_history`. Either is cheap; pick one and stamp it in the schema note.
- ~~The entitlement probe~~ — **ran 2026-08-24; see "What the 2026-08-24 probe settled" above.
  The reading list is frozen: SKEW, VIX9D, /VX and /ZN admitted; internals refused as not
  entitled.**
- **Retention** — the tables are permanent (the point of the exercise); confirm the monthly
  archiving job leaves the gex history database alone.
