# Console history

Dated stories behind the rules in [`../CLAUDE.md`](../CLAUDE.md). The rules live there; this file
keeps how each was found. See also `parity.md` (what survived the dashboards' deletion) and
`layout-before-frame.md` (the console as it stood before the module frame).

## Replacing and retiring surfaces

- **2026-08-12** — the console replaced the suite dashboard, the MEIC/flies/GEX dashboards, the
  earnings strategy dashboard and scout's web app, which were deleted. `pre-console-only` is the tag
  that still has them.
- **2026-08-31** — the research surfaces inherited from scout in the 08-12 port (watchlist, screener,
  builder, payoff/POP, staged dry-run tickets) were retired, leaving a read surface for the trading
  modules only with no path that touches an order.

## `withReadOnlyDb` defects (the reason for `readOnlyDb`, 2026-08-26)

Two real defects, both of which looked healthy:

- `/api/flies/meta` returned `{arms: [], dates: [], symbols: []}` with no log line, from one bad column
  in a UNION. `readFliesMeta` was the first reader migrated to `readOnlyDb`.
- A day resolver that named a journal table an older ledger lacks read as "no latest session", so a tab
  meant to show one day answered for every day in its era — 289 rows beside a 34-position day, both
  correctly labelled and irreconcilable. The `sqlite_master` guard covered the missing table; what
  remained was schema drift inside a present one (`fly_positions` without `trade_date`). From
  2026-08-26 a thrown resolve scopes to an empty day, and throws are recorded and surfaced on
  `/api/health`.

`withReadOnlyDb` itself was left unchanged (~65 call sites are written against it).

## The Advisor apply banner (2026-08-25)

It used to show tomorrow's artifact beside **today's** decision — two sessions that can never agree —
so on 2026-08-25 meic and earnings sat in it reading "written" next to "advice_disabled" with no
warning, and the card is collapsed by default so the closed head was all anyone saw. It became two
columns (queued for next session; did THIS session's artifact land) with the failure count on the head.

## Mirrors and the checks that pinned them

- **pmcc (2026-08-17).** `readers/pmcc.ts` re-implements queries `analytics.py` has but the CLI does
  not expose (per-cycle legs, rolls, attempts/events); a subprocess per request at a 15s refetch was
  not what that layer was built for. When it landed, the headline was checked against `run.py
  headline`, and a drawn Keltner band against the gate's stamped measures — which caught an off-by-one
  bar before it shipped. That half retired with pmcc's 2026-08-23 redesign (one symbol, one book, no
  Keltner gate; see `packages/pmcc/CLAUDE.md`).
- **MEIC (2026-08-26).** meic had no `run.py` to compare against until then. `meic-mirror.test.ts`
  failed on its first run and the page was wrong: `RESOLVED` was a deny-list
  (`NOT IN ('cancelled','pending','partial_entry')`), the ledger holds none of those, so still-open
  rows were admitted, contributing fees with no gross — every arm reported down by exactly what it had
  paid so far. `readMeicAnalytics` already guarded this in SQL; `readMeicPerformance` did not. Both
  now filter `pnl IS NOT NULL`.
- **Flies profit forest (2026-08-09 → 2026-09-12).** From the day it landed the TypeScript port priced
  a `long_vertical` (debit_first's opening trade) as the mirror of a short vertical, a full wing width
  from where `fly.position_pnl` puts it. No test touched the port, so every stranded debit-first
  vertical and every debit-first fly's pre-completion window was drawn wrong for a month (27 stranded
  rows on 2026-09-12; one real row off by $250 at its centre). The floor was never wrong — both sides
  bottom the kind at zero — which is why the max-possible-loss tile never gave it away. The same
  check then found the port computing the floor on its 120-point display grid instead of
  `fly._scan_prices` (a point one cent either side of every strike, padded a strike span): the
  assignment-fee step past a strike was invisible (worst understated by up to $5 per event on an
  unsettled day) and band edges landed up to a grid step off. 2026-09-11 control was a "locked" book:
  two stranded verticals whose loss four adjacent 5-wide flies cancel exactly at every price.

## Module frame migration (2026-09-22 → 2026-09-25)

Pages moved one at a time — flies first, the seven trading modules, then the suite surfaces (GEX,
Live, Reports, Advisor, Config) — with the lightbox (a dialog portalled over the Overview) shipping
beside the frame until the last one moved; then it, its carousel ring and its CSS were removed. GEX
moved as a reparent only: chart, `OI vs vol` default, spot trail and walls unchanged. The old
`LightboxFrame` keyed its whole scroll body on the slide id with the route fade on it, so every tab
change unmounted the module and faded it back from opacity 0; the same mistake recurred one level up
on 2026-09-22 when `Shell` keyed its outlet on the full pathname. Dense tables were `DetailSheet`
overlays from 2026-09-22 until 2026-09-24, when every place a reader can go became a page. The
`dragPct > 30` tint that lived in three files did not survive the sweep.

## Advisor per module (2026-09-12) and per experiment (2026-09-17)

Before 2026-09-12 a module's "is my A/B working" was answered only by expanding one collapsed card per
experiment, whose at-a-glance signals were a status chip, an `underpowered` chip every active
experiment carries, and a session count; the per-module advisor slide replaced that.

Until 2026-09-17 each module ran one experiment at a time as one book, `advised:<base>`; experiments on
a base reused the tag in turn and were told apart only by the `experiment_id` stamp (the 2026-09-16 fix
made the paired card one pair per stamp). Prefix stripping had been the rule in four files
(`pairs.ts`, `adviceDecl.ts`, `experimentGuide.ts`, the flies forest) before `experimentIndex.ts`. On
the day it landed MEIC's `advised:control` held 2,079 unstamped rows beside 421 stamped with one
experiment — history, not that experiment's.

## The Live page (2026-09-17)

The first cut used a synchronous spawn for the broker account, and the first browser check found the
whole page on skeletons for the broker's 10–20s round-trip; the bridge became non-blocking. The
recorder also samples the frozen pre-open spot, which drew a flat line ramping into the open — hence
clipping to regular hours.

On 2026-09-30 the page gained return on session peak risk (per session and per period tile) and the live
figures of both flies study tabs (completion and performance), and began showing the last settled session until the next one opens
— overnight it had read the new calendar day, which is empty. Carrying the tab's live-vs-paper panel
found it hard-coded to arm `'gex'`: the pilot moved to `control` on 2026-09-18, so the panel and its
abort rule had measured only the four July–August sessions. It now reads `live.arm`; the abort rule
armed at 30 live entries, and the gap was 1.3pp against a 15pp limit.

The same day the shared performance slide was found ignoring the paper/live toggle on flies, MEIC
and earnings: it always read `paper_trades.db`, so a live-badged page showed the paper book. It now
reads each module's live ledger in live mode, with no advised pairs (they exist only on paper).

Also 2026-09-30: flies' performance slide swapped its always-empty return on capital for return on
peak risk, and the session page's "worst case at expiry" — which summed open positions only, so it
read $0 once a book settled — became "daily peak risk", held until the next session. Paper records
no exposure, so the peak is replayed from positions; the first replay priced completed flies off
their settled `fees` and overstated two live sessions by $1.88 and $6.89 against the recorded
peaks, until final states took their recorded `floor_dollars`.

The Overview's order-alert daemon chip followed the morning of 2026-09-30, when the daemon started
at arming (01:43 ET), stopped heartbeating at 02:33 with nothing in its log, and was found only
because someone asked whether live flies was armed.

That evening MEIC's session page was read as a live trade on an unarmed day. It was the live
ledger's last trade, from 2026-06-30, under a "latest session" label that named no date. Paper had the
same fault the other way round: every arm was refused all day, so "latest session" (`MAX(trade_date)`
over `ic_trades`) fell back to the 29th's 538 trades, beside the 30th's 3,128 refusals, which the
attempts card resolves off its own table. `resolveMeicSession` now takes the loop's last run
(`daily_summary`, `loop_log`, `entry_attempts`, `ic_trades`) for every MEIC card, pmcc's
`resolvePmccSession` fix for the same shape, and the header and picker name the date.

## Trade table standard rollout (2026-09-24 → 2026-09-25)

Flies first (2026-09-24), then meic, bwb, earnings, calendars, pmcc and curve on 2026-09-25 — all seven
trading modules on the frame and the standard that day, with the suite surfaces joining so the flies
live-pilot tile links to `/live/today`. For MEIC, deriving entry from the rounded `net_credit` instead
of the two side credits showed a one-cent exit on 1,798 worthless expiries. bwb was the first module on
the charged-slippage model. The date picker (2026-09-26) replaced two native date inputs that made you
type MM/DD/YYYY.
