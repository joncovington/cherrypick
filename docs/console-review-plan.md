# Console review: every page, from the user's seat

*Drafted 2026-10-30, after the Backtest rebuild (docs/studies-board-plan.md). Read surfaces only:
nothing here changes a measurement, a gate or an arm, so each batch lands when it is ready. Built
from a tab-by-tab inventory of the source and the live API on 2026-10-30.*

## The tests

Each page and tab was asked four things (the user's, 2026-10-29):

1. **Needed or noise?** Would a person open it, and for what question?
2. **Clear?** Would a short card with one small set of figures beat a row with too many columns?
3. **Portfolio goals?** Does it move the user toward theirs: premium income from short puts,
   covered calls and credit spreads on the 30-60 DTE monthly, inside their delta and theta bands?
4. **Automated trades?** Or does it show how an automated loop is working: entering, filling,
   stopping, earning against paper and control, anything wrong today?

A tab that answers none of these is developer detail. It can stay, but not in the user's path.

## What the console is today

19 pages, about 180 tabs. By page: Flies 21 (retired 2026-10-19), MEIC 15, BWB 12, PMCC 12,
Calendars 12, Curve 12, Contango 11, Earnings 8, Live 8, Screeners 8, Config 7, Flow 6, System 6,
Compare 5, GEX 4, Backtest 4, Charts 3, Reports 2, Advisor 1 (four views inside it).

Five patterns account for most of the noise:

- **Two "today" pages in every module.** `summary` and `session` show the same net, open
  positions, costs and arms. In BWB they disagree: summary reads 10-23 while session reads 10-29.
- **The same evidence on several pages.** Arm-against-control tables sit on every module's `arms`
  tab and on Compare. Advisor tabs sit on five module pages and on the Advisor page while the advisor
  is paused. Fee drag appears in three places on MEIC alone.
- **Wide tables where a card would do.** Twenty-odd tables run past eight columns. The widest:
  Screeners skate (20+), Compare arms (about 20), Charts setups (18), module histories (13-18),
  short puts and covered calls (15-16), Live positions (14).
- **Silent staleness.** A module's summary shows "the latest traded day" (curve 10-07, PMCC 10-16,
  calendars and contango 10-19) beside a session dated today, which reads as no data. Flow shows
  10-28 as its latest session with no word that the 10-29 capture was rejected by the integrity
  check (two spread rows did not reconcile; the notification went out, the page says nothing).
- **Tested status said in some places, missing in others.** Earnings `strategies`, the skate, the
  signal board, credit spreads and fair value say plainly whether their rule is a tested edge. PMCC,
  Calendars, Curve and Contango never say their strategy was studied and closed, and the short-put
  and covered-call lists still read as a ranking although their note says the rule has no tested
  edge.

## Page by page

Verdicts: **keep**, **trim** (fewer columns or cards), **merge** (into the named tab), **move**,
**drop**. Only the tabs that change are listed under each page.

### Live (8 tabs) — the landing page, and the right one

The most on-goal page in the console: `positions` (what needs action) and `portfolio` (delta and
theta bands, guidance, hedge, income candidates) are the user's goals in two tabs.

| Tab | Verdict |
|---|---|
| today | keep; drop the retired flies row; gross, fees and settlement into one net with the split on hover |
| positions | trim: action-first card (trade, action, net, reason); targets, stop and time exit into the expanded row |
| portfolio | keep; the 12-column income-candidates table becomes "top three that fit your room", the rest behind an expand |
| wheel, pmcc | merge into one "income positions" tab, hidden while both are empty (both are empty today); the name `pmcc` clashes with the paper PMCC module |
| flies | drop from the rail (retired); its record stays on the Flies page |
| meic | keep (the one live loop); fold orphaned and held orders into today's "needs a person" list |
| execution | keep; into simple view (pages.md: it is detail-only because the simple list predates it, not by a choice); a three-figure card: fill rate, slippage against mid, overrides |

**Gaps:** no premium-income pace (collected this week and month against a target); delta and
theta bands shown for now only, never over time; no single "what to do today" list (actions sit on
positions, candidates inside portfolio, setups on Charts, lists on Screeners); no view of the user's
short strikes against the GEX walls.

### Reports (2 tabs)

| Tab | Verdict |
|---|---|
| morning | trim: keep deployment score, gates, vol term, calendar, levels; hot options and the technicals cards become one-line links to Flow and Charts (both exist in full there) |
| eod | keep; the user's daily per-module summary |

### Charts (3 tabs)

| Tab | Verdict |
|---|---|
| intraday | keep (cheap); not on-goal |
| technicals | keep; the drill-down every other page links to |
| setups | trim: 18 columns to about 8 (symbol, setup, side, status, move, 1M, RS, support/resistance), the rest in the expanded row |

### Options flow (6 tabs → 3)

`today` already summarises the other five. Keep `today` and `derived flow`; merge birdseye,
trades, spreads and vol/OI into one `tables` tab. Say on `today` when the latest capture was
rejected and why. In derived flow, Score and S·C·P·O are debug columns: behind the expand.

### GEX (4 tabs → 2)

Keep `gex` and `iv skew`; `volume` becomes skew's second chart. `history` is recorder health
(QQQ last written 07-31, XSP 10-23): its integrity strip moves to System, its 77-row snapshot table
becomes a small net-GEX-through-the-day chart on `gex`.

### Screeners (8 tabs → 6)

The closest page to the user's goal after Live.

| Tab | Verdict |
|---|---|
| short puts, covered calls | merge into one `income lists` tab with a put/call toggle (same component, same columns); trim 15-16 columns to about 8 (symbol, expiry, strike, Δ, premium, annual, distance, flags as chips); put "not a tested edge" in the tab head, not a footnote |
| credit spreads | trim the main table to about 8; the width table stays in the expanded row |
| skate | becomes a lens on the income list (band strike and best expiry per day) rather than a 20-column table; it repeats the short-put idea |
| earnings | keep as a calendar; drop direction, strength, kind and spread (the rule has no edge, which the tab already says) |
| earnings record | move: a one-card summary here, the 681-row table to Earnings `strategies` as its evidence |
| signal board | keep (four columns, concise); it is what credit spreads' setups column reads |
| fair value | keep as a lookup |

### Modules

**Live-capable modules keep their own pages; experimental ones shrink.** Every module gets the
same five-part shape: **today** (summary and session merged, refusals as a card), **open**,
**closed**, **performance**, **guide**. Evidence and study tabs stay for detail view. Two rules from
`docs/conventions/money-layout.md` bound the trims: open and closed stay different tables, and a
trade row's money group (entry, exit, gross, each cost, net) is never split, so a wide history is
trimmed on its descriptive columns (strikes into one structure cell, date and time into one).

Every module's today tab carries **one status line**: live or paper, and the strategy's tested
status in words with a link to the study (from the hypothesis register where one exists). The
missing ones today: PMCC (the roll study lost), Contango (the gate buys drawdown, not return), Curve
(costs take about half of each credit), Calendars (no verdict stated).

| Module | Changes |
|---|---|
| **MEIC** (15) | today = session + summary, with refusals from attempts; `session` into simple view (it is the daily page and is missing); calibration into performance; fee drag only on costs; regime cuts and the by-weekday/wing/delta cuts to Compare slices; divergence dropped; **add a live-against-paper page** (flies and bwb have one, MEIC does not, and MEIC is the live loop) |
| **BWB** (12) | today = session + summary (and the 10-23/10-29 mismatch goes with it); decisions and arms into today; positions trimmed from 14 columns; entry vol to one regime card; live-against-paper stays (empty until live trades) and today says live has not traded yet |
| **Earnings** (8) | today = session + summary; screening's forward scan goes (Screeners owns the calendar), its rejection funnel stays; `strategies` is the model the other modules should copy, with a short card above its 11-column table; `decisions` renamed `management log` (that is what it is) |
| **Flies** (21, retired) | one record page: final result, completion, costs, live-against-paper, the live pilot's closed trades, why it was retired and when; the payload-heavy timeline (1.3 MB), attempts (1.2 MB) and agent tabs go; the idle loop pill and "latest day" picker read as broken and go too |
| **PMCC, Calendars, Curve, Contango** (11-12 each) | under one **Experiments** page with a section each (status line, today, open, closed, performance), or each cut to the five-part shape; `decisions` (empty today on three of them), `analysis` (2-8 records) and `arms` (duplicates Compare) go; Calendars' plan stays (its one distinct page); Curve's and Contango's regime pages are the same VIX/VIX3M ratio, drawn once on a market-context card |

Module `advisor` tabs leave the module pages while the advisor is paused (#560); the Advisor page
holds them.

### Compare (5 tabs)

| Tab | Verdict |
|---|---|
| arms | trim to about 8 columns (arm, trades, net, net per $100 risk, difference against control, t lower bound, verdict), paired statistics behind an expand; retired flies arms hidden by default |
| risk | merge into arms as a column set |
| calendar | keep as the drill-down |
| portfolio | give it a default arm set (one per live-capable module); it is empty on open today |
| slices | keep (moved here 2026-10-30, fails closed on the backtest feature) |

### Backtest (4 tabs)

Rebuilt 2026-10-29/30 (queue, studies, runs, data). One follow-up from this review: System's
quote-history and implied-variance evidence cards are validation evidence and move to Backtest
data's "is history trusted?" card.

### Advisor (1 tab, four views)

Paused, with no measured experiment. Until it is unpaused: a status line at the top ("paused since
10-25: why"), and the page leaves the Research rail section for a card on System.

### System (6 tabs)

| Tab | Verdict |
|---|---|
| health | keep as the landing; the per-module "live trading posture" table merges with `modules` here |
| supervisor | trim the 9-column, 114-row jobs table: jobs needing attention by default, all on a toggle; the stale-process table from environment joins it |
| modules | merge into health (it is the third per-module table) |
| data | keep freshness and stream; the storage table is empty (no store measurement recorded): fix or drop; evidence cards move to Backtest |
| environment, logs | keep as detail |

### Config (7 tabs)

`Arms & profiles` is renamed `Arms` (the suite's naming rule). `dev` holds one field: into
`timing`. `notify` is titled "Notifications & jobs", one of three meanings of "jobs" in the console;
rename it `alerts`.

### The rail

- **Modules:** live-capable first (MEIC, BWB, Earnings), then one Experiments entry (or the four
  experimental modules under a collapsed group). Flies leaves the rail; its record page stays
  reachable from Compare and Experiments.
- **Digits** `1`-`9` renumber when a module is off, so `3` moves. Bind each module to a fixed key.
- **Config** has no shortcut key; give it one.
- **Simple view** drops the "Suite" header when it holds only Config.

## Batches, in order

1. **Say what is true** (small, highest value):
   - the tested-status line on each module's today tab, and in the head of each income list
   - Flow says when the latest capture was rejected
   - a stale window is labelled "last traded 10-19", never shown as today
   - BWB says live has not traded yet
   - Compare portfolio gets a default arm set
   - the empty System storage table is fixed or removed
2. **Fold the duplicates:**
   - every module: summary + session into one today tab
   - module `advisor` and `arms` tabs leave the module pages
   - MEIC calibration into performance
   - Flow 6 tabs to 3, GEX 4 to 2
   - System `modules` into health; Config `dev` into timing
3. **Cards for wide tables:**
   - Live positions and income candidates
   - Screeners: income lists merged and trimmed, credit spreads, skate as a lens
   - Compare arms (risk merged in), Charts setups
   - module open-position tables (descriptive columns only; money groups whole)
4. **Experiments and Flies:**
   - the Experiments page (or four five-part pages)
   - the Flies record page
   - the rail regrouped; fixed module keys
5. **Fill the gaps:**
   - on Live: premium-income pace, delta and theta bands over time, and "what to do today" (actions, candidates that fit the room, expiring and earnings exposure)
   - MEIC live against paper
   - the user's short strikes against the GEX walls

Each batch is one or two PRs. Batch 1 is a day's work and changes no layout.

## Decisions for the user

1. **Experiments:** one Experiments page with a section per module, or four separate pages cut to
   five tabs each?
2. **Flies:** a one-page record, or out of the console entirely (its ledgers stay on disk)?
3. **Advisor while paused:** a card on System, or leave the page as it is?
4. **Income candidates:** stay on Live portfolio (they are sized to the user's room) or move to
   Screeners beside the lists?
