# The "Crazy Ivan" dip-sold put — a paper-module proposal

***SHELVED 2026-09-30, kept for reference.** One /NQ contract is about $600k of notional (about $80k
lost per contract on a 25% gap), too large to live trade, and a paper module was not wanted on its
own. The micro contracts can't run the strategy (no roll target past ~8 weeks), and QQQ has no
overnight session. The research, the example and the streaming design below stand as written; the
broker's chain endpoints are recorded by `scripts/probe_futures_option_chain.py`.*

*Drafted 2026-09-30. Nothing here is built. **Scope: E-mini Nasdaq-100 (/NQ) futures options only.**
QQQ, TQQQ and the micro (/MNQ) options were considered and left out (see "Why /NQ"). The module name
`ivan` is a working title; the suite names modules after the structure (flies, calendars, curve), and
`dipput` would follow that.*

## The trade, simply

Sell one far-out-of-the-money put on Nasdaq-100 futures, but only once the market has already
dropped. Pick an expiry about two months out and a strike about 10% below the market (roughly a
12-delta put), collect the premium, and buy it back once a quarter of that premium has decayed —
usually in about two weeks. If the market falls to within a hair of the strike, don't take the loss:
buy the put back and sell a new one further down and further out in time for about the same money
("roll down and out"), and keep doing that until it can be closed at a profit.

The bet is that fear makes puts expensive right after a drop, that a 10% cushion is rarely reached
in two weeks, and that a position small enough to survive the bad case can always be rolled until the
market recovers. The cost is the shape of the payoff: many small wins and, now and then, one loss
many times their size — which rolling defers rather than removes.

**Where it comes from.** Rob and Maria Helmick ("Math Makes Money" on YouTube), described in full on
Theta Profits' *My Trade* segment recorded 2026-09-14. This is their market and their parameters:

| | Rob's way (`control`) | Maria's way (`near18`) |
|---|---|---|
| Expiry | 45–60 DTE | 21–45 DTE |
| Strike | ~12 delta, round strikes only (the 250s, 500s, 750s, 1000s) | ~18 delta |
| Entry trigger | NQ down ≥ 300 pts (≈ 1.0%) | down ≥ 500 pts (≈ 1.7%) |
| Take profit | 25% of the credit | ~50% |
| Defence | roll down and out when NQ is within 250 pts (≈ 0.85%) of the strike | same |
| Sizing | ≤ 20–25% of buying power; 1–3 contracts per $100k | same |

The thresholds are carried as **percentages**. A point threshold loosens as the index rises: 300
points is about 1% at today's ~30,000, but it was about 1.5% when NQ was near 20,000.

Their "Crazy Ivan" proper is the entry tactic. Before bed they leave a ladder of resting sell orders
priced well above the current mid. A fill at 03:39 means the market has just dived, so the order
doubles as the alarm. NQ options trade nearly 23 hours a day, so the ladder can be modelled here
(Phase 2).

Their record (self-reported): $500k to ~$1.1M in 11 months, 320 trades, about four losses, all
during a strong Nasdaq run. Whether a rolled trade counts as a loss is not stated, and that is the
first thing this module exists to settle.

## A worked example (fictitious)

/NQ, `control` arm, one contract ($20 a point). Prices are Black-76 at plausible implied
volatilities, not quotes. IV ≈ 0.25 reproduces the source's own example (a 12-delta put 46 days out
at ~178 points). Margin is the module's SPAN-like proxy (below), calibrated so that example reads
~$17k, as the source says. Fees use a round illustrative $2.50 per contract per fill. Slippage is
one tick (0.25 pt = $5) per fill. Money follows the suite layout: signed cash flows, whole-position
dollars.

**Day 0, 03:39 ET.** Yesterday's settlement was 30,000.00. Overnight, NQ trades 29,640.00, down
1.20%, and the `control` trigger (−1.0%) fires. The entry expiry is the listed one nearest 52 DTE.
From the put band it has subscribed for that expiry, the module's own Black-76 puts the 26,500 put
(a multiple of 250) at delta −0.116, bid 172.00 / ask 173.50.

- Sell 1 NQ 26,500P at mid **172.75 cr** → **+$3,455.00**.
- Modelled margin: **$15,754**.
- Take-profit order: buy back at ≤ 129.50 (75% of 172.75, to the tick).

**Path A — the usual ending.** Day 2, NQ is back at 29,910 and the put has crossed 129.50. Buy to
close at 129.50 db → −$2,590.00. Gross **+$865.00**, fees $5.00, slippage $10.00, net **+$850.00** in
two days. This is the trade the videos show.

**Path B — the ending this module is built to measure.**

| Day | NQ | Event | Cash flow | Margin |
|---|---|---|---|---|
| 0, 03:39 | 29,640 | Sell 26,500P (52 DTE) at 172.75 cr | +$3,455.00 | $15,754 |
| 5 | 26,900 | Within 2 × 0.85% of the strike (≤ 26,950.50): the module asks the producer for the ~110-DTE expiry's chain and subscribes a put band for it. **Pre-roll warm-up, no producer restart** | — | — |
| 6, 10:05 | 26,700 | Within 0.85% of 26,500 (≤ 26,725.25): **roll.** Buy 26,500P at 1,288.75 db | −$25,775.00 | $30,209 before (1.9× entry) |
| 6, 10:05 | 26,700 | …and sell 25,000P (110 DTE) at 1,386.00 cr. This is the lowest round strike that still pays a credit; 24,750 pays 1,285.50, a debit. Net roll 97.25 cr | +$27,720.00 | $27,500 after |
| 9 | 27,150 | 25,000P crosses 75% of 1,386.00: take profit, buy at 1,039.50 db | −$20,790.00 | — |

| | Gross | |
|---|---|---|
| Leg 1 (26,500P) | −$22,320.00 | closed `rolled` |
| Leg 2 (25,000P) | +$6,930.00 | closed `traded` at take-profit |
| **Chain** | **−$15,390.00** | fees $10.00, slippage $20.00 → **net −$15,420.00** |

Both legs "worked" by the rules: the strike was defended, and the last leg closed at its 25%
target. Counted per leg — or by "did the final close make money" — that is a win. Counted as one
position from first sale to final close, it lost about 4.5× the original premium. **The module scores
the chain.**

The rules don't say which credit the take-profit is measured against after a roll. That is
declared as an arm parameter (`tp_basis`), not guessed. On a `chain` basis the 25,000P would have
been held until the whole chain was up 25% of the *first* credit (a mark ≤ 226.75). On this path
that happens around day 29 with NQ near 28,850, for a chain gross of about +$865. The cost is twenty
more days of exposure at $25–27k of margin. The difference between the two bases is a measurement,
not a preference.

## What it measures

1. **Does the dip trigger earn its keep?** `control` against `nodip`: identical except that `nodip`
   enters at 15:45 ET on any session with room, trigger or not. Compared per chain and per
   margin-day, since `nodip` will simply trade more often.
2. **The true win rate.** Chain-level against leg-level, with the roll count beside every chain. The
   gap between the two is the figure the source can't show.
3. **Margin expansion.** Peak modelled margin ÷ entry margin per chain, and the book-level peak
   against `max_bp_fraction`. The source says survival rests on this.
4. **Deferred loss.** The mark-to-market of open rolled chains, reported on its own line, never folded
   into realised P&L.
5. **Premium at the trigger.** Every CME trade date records the 12-delta strike, mid and IV for each
   arm's entry expiry at 15:45 ET, and again when a trigger fires (the curve pattern: recorded traded
   or not). That gives the "fear premium" a distribution rather than an anecdote.
6. **When triggers fire.** Overnight versus regular hours, because the source's fills land at 3–5 a.m.
   and a regular-hours-only version of this trade would be a different trade.

**How often the trigger fires.** There is no NQ daily history locally, so this uses QQQ as a proxy
(the same index). Measured from the Dolt daily bars, 2011-01-04 to 2026-09-29 (3,956 sessions,
split-adjusted; the session low against the prior close):

| Trigger | Sessions per year | Share of sessions | Last 5 years | 2026 YTD | Episodes per year |
|---|---|---|---|---|---|
| −1.0% (`control`) | 71 | 28% | 88 | 64 | 13 |
| −1.7% (`near18`) | 34 | 13.5% | 45 | 34 | 11 |

- An episode is a trigger day with none in the five sessions before it.
- QQQ's figures cover regular hours only. NQ trades about 23 hours against the prior settlement, so
  its counts will be at least this high. The first months of `ivan_daily` will give the real figure.
- 47% of −1% days close back above the threshold. The trigger is an intraday low, not a close.
- The frequency varies widely by year: 27 sessions in 2017, 145 in 2022.
- Six triggers a month at 1% doesn't match Rob's "we sit on our hands for a month". His
  point-denominated rule was stricter in percentage terms for most of the last few years.

With one entry per arm per trade date, `control` enters repeatedly through a clustered selloff until
the margin cap stops it. So the effective sample for the `control`/`nodip` comparison is the episode
count, about a dozen a year, not the trigger count. The suite holds no NQ option history for a
backtest. Plan on at least a year of paper before reading the A/B.

## Arms

| Arm | DTE | Delta | Trigger | Take profit | `tp_basis` |
|---|---|---|---|---|---|
| `control` | 45–60 | 12 | −1.0% vs prior settlement | 25% | `leg` |
| `nodip` | 45–60 | 12 | none, 15:45 ET entry | 25% | `leg` |
| `near18` | 21–45 | 18 | −1.7% | 50% | `leg` |

**Shared defaults:**

- **Strikes:** multiples of 250 only (`strike_multiple`, the source's liquidity rule).
- **Entry expiry:** the listed expiry nearest the arm's target DTE within its span, drawn from Friday
  weeklies (`QN1`–`QN4`), end-of-month (`QNE`) and quarterlies (`NQ`). The Monday–Thursday weeklies
  never list that far out. The root is recorded with every leg.
- **Roll trigger:** NQ ≤ strike × 1.0085, with pre-roll warm-up at twice that distance.
- **Roll target:** the nearest listed end-of-month or quarterly expiry at least 60 days beyond the
  current one, at the lowest round strike whose mid pays at least the buy-back.
- **Terminal rule:** `max_rolls: 3` and `max_roll_dte: 180`. If no credit roll exists within those
  bounds, the chain is closed and booked.
- **Sizing:** one contract per entry, and one new chain per arm per CME trade date.
- **Capital:** `paper_capital: 500000` (the source's starting account) and `max_bp_fraction: 0.25` of
  it in modelled margin. That is about seven fresh positions, or half that once margin doubles.

The terminal rule is **ours, not the source's**. Their answer is "keep rolling", and a measurement
needs an end. Its frequency is reported, so if it never fires it costs nothing.

`control` and `nodip` share one entry expiry; `near18` needs a second. Both are cheap here (below).
A `ladder` arm is Phase 2.

## Why /NQ

- **It is the only market where the trade runs as specified.** On 2026-09-30 NQ listed 24 expiries
  out to 457 DTE: Friday weeklies, end-of-month series, and quarterlies through 2027-12. The roll
  target always exists.
- **The overnight half exists.** Triggers and the ladder can fire at 3 a.m., as the source's do.
- **/MNQ can't run it.** Since 2026-07-13 CME lists only financially settled micro options (roots
  `MN1A`–`MN5E`), on a cycle of two weeks of Monday–Thursday expiries plus eight Fridays. That is
  about 56 DTE at most, so the 46 → 108 DTE roll has nowhere to go. The physically settled micro
  series are being delisted, the last quarterly on 2026-12-18.
- **QQQ and TQQQ are out of scope.** QQQ options trade only in regular hours, so the ladder and the
  overnight triggers don't exist there. TQQQ's −3% days are QQQ's −1% days (1,105 of ~1,120 since
  2011), with three times the volatility and leveraged decay on top.

`ivan` and pmcc are both long Nasdaq delta and short volatility. Their drawdowns will arrive on the
same days, and the review should read them together.

**Size, honestly.** One NQ contract is about $600k of notional. The worked example's bad path lost
$15k on a $3.5k premium, and a 25% gap down with no chance to roll would cost about $80k per
contract (29,640 → 22,230 against the 26,500 strike). The paper module can measure that tail. This plan has no live path, and the posture would
need its own decision before anything here informed one.

## The streaming subscription boundary

The producer is shared, so whatever `ivan` declares, every module pays for in reconnect cost and
restart risk. The futures design turns out much cheaper than an equity one would be. It declares **no
`symbols` and no `window_hints`**, so it never restarts the producer, not even at registration.

### Today

The facts the design starts from:
- **Budget:** `budget_status(default_strike_count=30)` on the live registry (2026-09-30) estimates
  **9,752 against the 12,000 watchdog budget**; the producer reports 8,824 subscribed.
- **Restarts:** the watchdog recycles the producer whenever `symbols` or `window_hints` grow.
  `expirations`, `legs` and `leg_sources` are re-read live with no restart.
- **Futures as legs:** futures already stream as quote-only `legs`. `/NQZ26:XCME` is declared by
  overview, and the producer keeps it live overnight. A non-option leg gets Trade, Quote and
  Summary; an option leg (leading `.`) gets Quote and Greeks.
- **No futures chains:** the producer has **no futures-option chain path**. `_fetch_full_chain` calls
  the equity `get_option_chain`.

### Who serves the chain: the producer, chain-only

The producer gets one new request key, e.g. `future_option_chains: {"NQ": ["2026-11-20", …]}`:
product code to the expiries a module needs. For each, it **fetches and writes the chain, and
subscribes nothing.**

- **The fetch.** `get_future_option_chain(product)`, the flat endpoint, which took 0.49s for NQ's
  8,760 options. Every `FutureOption` already carries the fields `streamcache.write_chain` reads, so
  rows land in `stream_chain` as they are. Fetches run on the same cooldown as extra expirations
  (900s), plus immediately when a requested date is missing.
- **Settlement comes from the product catalogue**, `FutureOptionProduct.cash_settled` per option
  root, not from the option object. Every NQ and MNQ option reports `settlement_type: "Future"`,
  even the cash-settled micro series, so the option object can't be trusted for this.
- **The underlying futures are resolved** for the served dates (one `Future.get` call), and each row
  carries its `underlying_streamer_symbol`. Never assemble a futures symbol by hand. Each expiry
  sits on its own future: every expiry after mid-December is on `/NQH7`, and on 2026-12-18 two roots
  on different futures expire together.
- **Restart-free:** the key belongs to the half the producer re-reads live. A test pins it out of
  `subscription_snapshot`.
- **Health:** chain failures surface the way equity ones do, as `stream_symbol_health` rows under
  `NQ@<date>` and in `--status`.

Why the producer, and not a separate chain cache:
- **One broker caller.** It already holds the session, fetches equity chains, retries, and reports
  chain health to the watchdog.
- **One cache writer, one read surface.** A separate fetcher would be a second broker caller with
  its own freshness problem, and modules would read two sources.
- **Futures complexity stays in the module.** What makes futures awkward for the producer is
  windows: centring on the right contract, keeping roots apart, a 1-point re-centre threshold on a
  30,000 index. A chain-only path has no window. The module picks the expiry, the root and the band.

### What the module declares

- **`symbols: []`**, **`window_hints: {}`** — always, enforced by a test.
- **`future_option_chains`** — each arm's entry expiry, and a roll-target expiry only while some
  chain is in its pre-roll band.
- **`legs`**:
  - the underlying futures for the served expiries, read from the chain rows (3 subscriptions
    each);
  - the **put band**: for each served expiry, puts that are multiples of 250 from about 6 to 20
    delta (by the module's own Black-76 off the future's mid and the band's last mids), plus a margin
    of strikes. That is about 15–30 strikes × 2 = **30–60 subscriptions**. The roll band reaches
    20–25% below the future at coarser listed steps; about 15–20 strikes, ~40 subscriptions.
  - The band is rewritten only when the 12-delta estimate leaves its middle half (hysteresis).
  - Strike lists grow as expiry approaches, so the band re-reads the chain rather than caching it.
- **`leg_sources`** — `SELECT streamer_symbol FROM ivan_legs … WHERE status = 'open'`, 2 per open
  leg.

**Delta is computed, not streamed.** The module prices the band with Black-76 from each put's mid and
its own underlying future's mid. That is a pure function, and it doesn't depend on a Greeks
entitlement for futures options that nobody has verified. The streamed Greeks, where they arrive, are
recorded beside it as a cross-check.

### What that adds

| State | Added | Suite estimate |
|---|---|---|
| Idle (chains served, no positions) | ~6 (two futures) + 2 entry bands ≈ 125 | ~9,880 |
| A chain in pre-roll | + ~40 | ~9,920 |
| Each open chain | + 2 | — |

That's about a twentieth of an equity-window design on QQQ (~2,300 at peak), with no window and no
restart. The one gap: **`estimate_subscriptions` counts windows only**, so the whole of `ivan`'s cost
is invisible to the budget check. Counting `legs` (2 per option leg, 3 per cash leg) is a Phase 0
change.

### Overnight

The producer already runs and streams overnight. What isn't overnight is everything watching it:

- **Producer supervision.** The watchdog's streamer-health job runs 09:00–16:00, and off-session it
  never restarts. A producer that dies at 20:00 stays dead until morning, and with it every overnight
  trigger and ladder fill. The health window must extend across the CME session (Sunday 18:00 ET to
  Friday 17:00 ET, less the daily 17:00–18:00 halt) whenever a module declares that need. It should be
  declared by the module, not hand-kept.
- **Module scheduling.** `jobspec.in_window` can't express a window that crosses midnight, and
  `trading_days_only` is the NYSE calendar, which has no Sunday 18:00 open. The simplest correct
  shape is a resident job with no clock window, gating itself in a pure `clock.py` on the CME session
  and a declared CME holiday list. Heartbeat silence supervision already works around the clock.
- **Trade dates.** CME's trade date rolls at 18:00 ET. `ivan_daily` is keyed by CME trade date, from
  `clock.py`.
  - The producer keys `stream_summary` by ET calendar date, and its COALESCE upsert would hold the
    prior session's values through the evening. So the module **doesn't read session values from
    `stream_summary`**.
  - The trigger's reference is the prior settlement, taken from the feed itself:
    `stream_trades.last − stream_trades.change` on the front future. Phase 0 checks that `change`
    resets at 18:00.
- **Futures roll.** The trigger reads the active-month future (`futures_contracts.json`). Each leg is
  priced off its own underlying, from the chain rows.

### Guards (each shown to fail)

- `ivan`'s request file has empty `symbols` and `window_hints`. Declare one, and the test fails.
- `future_option_chains` is absent from `subscription_snapshot`. Add it there, and the restart test
  fails.
- **The correlation lint is blind to futures legs.** It reads `symbols` only, which `ivan` leaves
  empty. Extend it to classify futures legs by product (`/NQ…` → nasdaq100), and prove it by
  declaring a second Nasdaq vehicle.
- Every band symbol belongs to a served expiry and an allowed root, and the band is no wider than
  `max_band_strikes`.
- A root that the product catalogue doesn't declare is refused at entry (`unknown_settlement`), the
  pmcc rule.
- `budget_status` counts `ivan`'s declared legs once Phase 0 lands.

## Module shape (mirrors pmcc/curve)

- **Pure:**
  - `engine.py`: arm definitions, `merged_params`, Black-76 (price, IV, delta), entry-expiry and
    root selection, delta-to-strike selection over the band, roll-target search, the margin proxy,
    settlement.
  - `management.py`: take-profit, pre-roll, roll, terminal rule, `assignment_exposed`.
  - `trigger.py`: drawdown versus prior settlement, latched per arm per CME trade date.
  - `clock.py`: the CME session, trade date, halt and declared holidays.
- **Read/write:**
  - `provider.py`: read-only cache snapshots that refuse rather than guess (`no_fresh_quotes`,
    `no_band_quotes`, `no_entry_chain`, `no_underlying_quote`). Chains are read by product,
    expiration and option root from `stream_chain`. That is not the OCC-root `chain_for_expiration`.
  - `book.py` on `core.spreadbook.SpreadBook`.
  - `db.py` on `core.ledgerstore`.
  - `stream_request.py`: chain requests, futures and band legs, leg source, best-effort.
  - `paper_loop.py`, `analytics.py`, `cli.py`/`run.py`.
- **Loop order per tick:**
  1. Heartbeat.
  2. CME-session gate.
  3. Record the daily row.
  4. Settle and dispose.
  5. Marks.
  6. Management (roll / take profit).
  7. Triggers and entries.
  8. Rewrite the stream request.
  9. `record_iteration`.

**The margin proxy.** tastytrade margins futures options with CME SPAN, which can't be reproduced
locally. The module computes a SPAN-like scenario charge: reprice with Black-76 at ±⅓, ⅔ and 1 of an
8% price scan crossed with ±25% IV, plus ±2× scans at 35% weight, and take the worst loss.
Calibrated to the source's figure (a 12-delta 46-DTE put at ~$17k), it reads $15,754 at the
example's entry. The scan parameters live in config. It is labelled **modelled, not the broker's
number** everywhere it appears. The expansion ratio is the measurement, not the dollar figure.

**Ledger.** One position per chain, rolls inside it (pmcc's legacy roll pattern):
- `ivan_positions`: `position_id = "NQ:<arm>:<trade_date>:<n>"`, `roll_count`, `tp_basis`.
- `ivan_legs`: `short_put_1..n`, with option root, underlying future, signed
  `entry_credit`/`exit_debit` in points and dollars, and `close_kind` ∈
  `traded|rolled|expired|assigned`.
- `ivan_marks`: per tick × open leg, with the underlying's mid, Black-76 IV and delta, streamed
  Greeks when present, modelled margin (so expansion is a query) and `assignment_exposed`.
- `ivan_futures`: a future delivered by assignment and its disposal. This is pmcc's
  `pmcc_assignments`, for a contract instead of shares.
- `ivan_daily`: one row per CME trade date. The prior settlement, the session low drawdown, and per
  arm whether the trigger fired, when, and whether in regular hours or overnight. Also the 12-delta
  strike, mid and IV per entry expiry at 15:45 and at trigger.
- `ivan_management_events`: every verdict, including blocked ones. A roll carries old/new
  root/strike/expiry/net in `detail_json`.
- Also `ivan_decisions`, `ivan_entry_attempts`, `ivan_snapshots`, `ivan_loop_iterations`,
  `ivan_band`.
- Money: fills at mid, with slippage as its own cost column (one tick per fill by default; the
  bwb/pmcc/curve model). Fees come from a futures schedule `core.fees` doesn't have yet (Phase 0),
  and settlement is split out.

**`core.ledgers` reader:**
- `capital` = the largest strike × $20 held in the chain, less credits collected. That is the true
  worst case of a naked put, and it is large on purpose.
- `max_profit` = credits collected.
- Peak modelled margin is reported beside these, never as `capital`. Return on margin is a read-side
  figure.

**Settlement.** NQ options are American. All NQ roots are physically settled per the catalogue.
- Weekly and end-of-month (PM) options exercise into the quarterly future. A short put left in the
  money is assigned a **long future at the strike**. It is marked from that future's quote and
  disposed of at the next session's disposition time (the calendars pattern).
- Quarterly options (AM) exercise into the expiring future, which itself cash-settles to the
  special opening quotation, so in effect they settle to cash.
- The roll and terminal rules should make either rare. Its frequency is reported.
- Early exercise is **measured, never modelled**: marks with extrinsic under the threshold are
  flagged, and the result is an upper bound.

**Registration** — the full list the new package must join, by hand:
- `.github/workflows/ci.yml` matrix.
- The orchestrator `modules.ivan` block (a resident job without a clock window; see "Overnight") and
  `advisor.modules.ivan`.
- `schemas.SCHEMAS` and the four readers `test_schema_registry` forces.
- `configedit` `GUARDED["ivan"]["/live/enabled"]` (paper-only, but the guard is uniform),
  `_MODULE_TARGETS` and `_EXAMPLE_REL`.
- `core/ledgers` `READERS`/`OPEN_READERS`.
- Review `MODULES`/`HEALTH_READERS`/`EXPECTED_READERS`.
- The advisor's bounds, verdicts and factpack maps.
- Console server, shared and web registrations, plus the headline mirror test.
- The `check_docs.py` mentions (root `CLAUDE.md`, `README.md`, `docs/architecture.md`,
  `docs/README.md`).

Dev-install and ci-local discover the package on their own.

## Phases

0. **Infrastructure.** Each item is guarded by a test that is shown to fail.
   1. **An entitlement probe.** A temporary request file declaring three NQ put legs and `/NQH27`.
      Watch `stream_quotes` (and `stream_greeks`) fill in regular hours and again overnight, then
      remove the file. In the same run, record `stream_trades.change` across 18:00 ET to confirm it
      resets to the new settlement. This is the 2026-08-24 method. It adds a few subscriptions with no
      restart, but it touches the live producer, so it needs a go-ahead.
   2. **The producer's chain-only futures path** (`future_option_chains`), with the product catalogue
      and underlying resolution as above.
   3. **Count `legs`** in `estimate_subscriptions`.
   4. **Overnight supervision:** streamer health across the CME session when declared, and a
      resident job shape for a self-gating module.
   5. **A futures fee schedule** in `core.fees`, with values from the broker's published schedule.
   6. **The correlation lint** learns futures legs.
1. **The NQ paper module** with `control`, `nodip` and `near18`, running the CME session, with the
   daily record, the ledger including delivered futures, and the registrations above.
2. **The `ladder` arm** — the "Crazy Ivan" proper. At 16:00 ET it rests paper offers at +10/20/30%
   over the 12-delta mid on that named contract, filled when the bid reaches them at any tick
   through the night. This is the source's 03:39 fill, measured.

## Open questions

- The name: `ivan`, or `dipput` to match the suite's convention?
- Is the margin proxy good enough, or should the expansion figures wait for a way to read the
  broker's own number? That would need an account-scoped call, which this credential-free module
  must not make.
- Where should the CME holiday calendar come from? A declared list in config, like pmcc's dividend
  table, with a lapse refusal?
- The entitlement probe in Phase 0.1 touches the live producer. Run it now, or after the
  producer-side work?
