# cherrypick-ladder

⚠️ **EXPERIMENTAL.** The share dip ladder in paper, on leveraged index funds (TQQQ first): buy a lot
7% down, sell each lot on its own at +4% of its cost, no stop, no time limit. **Paper-only
in practice: the live path is gates, an arm and an order path that ships in dry-run and off** (see Live;
posture: root file). Ledgers: `~/.cherrypick/data/ladder/paper_trades.db` and, for live,
`live_trades.db` beside it.
Off by default: the orchestrator runs `--once` every 60 s on trading days as the `ladder-paper` job once
`modules.ladder.enabled` is set (trade schema `ladder_lot`); the loop gates its own hours.

## Why it exists

A Discord group trades this rule by hand on 3x funds. Item 26 (`research/edge-hypotheses-plan.md`)
judged it on daily bars and it **failed**: a small edge over random entry, lots held for years in a
long fall, and buy-and-hold beating it on index funds. Item 27's `decay` target **passed** as a fix
for the long holds. The user chose to run it in paper regardless, to watch it trade at real quotes.
Nothing here says the rule has an edge.

## The rule (`rules.py`, pure)

- **Reference close:** the previous session's posted close from `stream_summary` (that session's
  `day_close`, or today's `prev_day_close`), never a last trade. No posted close, no buy.
- **Entry rules** (item 26's):
  - `once`: at most one lot a session, at 93% of the reference close.
  - `chain`: with no lot open, the first lot at 93% of the reference close; then each further lot at
    93% of the last lot bought, the same session or later. A sold lot does not move the anchor; an
    empty book resets it.
  - `session`: every session starts again from the reference close, whatever older lots the arm
    holds: its first lot at 93% of the close, its n-th at 93%^n. Each level buys once a session,
    even after its lot sells. The group's posted SOXL fills (2026-11-13 and 2026-11-19) are this
    rule; on 11-19 `chain`, anchored on an older lot, missed theirs.
- **Target rules:** `fixed` 104% of cost; `decay` (item 27) 104% to day 30, breakeven after the sale's
  fees from day 31, 95% of cost from day 121, in calendar days from the buy session.
- **Rounding:** a buy level rounds down to the cent and a target up, so a rounded limit never fills
  on worse terms than the rule's own number.
- **Size:** whole shares, never more than `lot_dollars`; a lot under `min_shares` is refused.
- **Caps:** `max_open_lots` per arm per fund, `max_open_lots_total` per arm across funds. Each arm
  keeps its own book.

## What paper adds, and says

1. **Fills are resting limits read off one quote a minute.** A buy fills when the ask
   is at or under its level, a sell when the bid is at or over its target. The fill is the limit
   when the price crossed it since the previous tick (the order was resting as the market moved
   through it), and the market (ask or bid) when the market was already through it: a gap into the
   session, or an order placed under water. A lot is sized at its level and costs its fill; its target is worked from the fill.
   Slippage is zero by construction, and a touch shorter than a tick is missed.
2. **A lot can be bought and sold in the same session, and a sold lot frees its slot at once**, as a
   live account's would. Item 26's daily replay allowed neither; the group's own fills do both.
3. **Hours are tastytrade's equity day, 08:15 to 20:00 ET**, but the stream cache only holds quotes
   while the streamer runs (08:45 to about 20:00). Nothing fills overnight, even on a fund the broker
   trades overnight.
4. **The group's 18-day "trade hospital"** is counted, never acted on: `summary` reports lots sold
   within 18 days and open lots older than 18. The group writes covered calls on those; at lot sizes
   under 100 shares that is not possible, and nothing here does it.
5. **The group's first lot comes from a chart signal** bought at the next open; that signal is
   unknown, so paper uses item 26's rule for every lot.

## The money layout

A position is one lot. Signed cash flow, whole-position dollars: `entry_value` (the purchase, −) +
`exit_value` (the sale, +) = `gross_pnl`; `gross_pnl` − `fees` − `settlement` − `slippage` =
`net_pnl`. Fees are `core.fees.stock_trade_fee`: the clearing fee on the buy and the sale, the SEC fee and FINRA TAF on the sale.
Settlement and slippage are zero. `exit_reason` is `target_gain`, `target_breakeven` or
`target_floor`.

## Stream needs (`stream_request.py`)

Each fund as an underlying (Trade and Summary) and as a leg (Quote), `nearest_window` false: no option
chain is read. A fund removed from the config stays requested until its last lot sells.

## Layout

`rules.py` (levels, targets, size, money, pure), `provider.py` (read-only cache views), `db.py`
(ledger), `paper_loop.py` (the tick), `stream_request.py`, `analytics.py` (the forward read),
`cli.py` (status, lots, summary), `live_gates.py`, `live_loop.py` and `credentials.py` (the live gates,
the arm and the commands; none imports anything that trades), `orders.py` (the pure order planner and
the executor over an injected broker), `live_db.py` (the live ledger), `live_tick.py` (the broker
adapter, the reconcile and the tick: the only module that imports the broker seam), `live_checks.py` (pure: the
split entries and the positions comparison).

## Commands

Commands to run the package, its tests and its lint are in [README.md](README.md#commands).

`paper_loop --force` acts outside the hours, for testing only.

## Live (gates, arm, order layer, tick and ledger; ships in dry-run and off; #1337)

`live_gates.py` (pure: a list of named gates, each with a verdict and a reason), `live_loop.py` (the
commands), `credentials.py` (the designated account's presence), `orders.py` (the order planner and
executor), `live_db.py` (the live ledger) and `live_tick.py` (the broker adapter, the reconcile and the
tick). `live_tick.py` is the only module that imports the broker seam, and it builds the adapter inside
the tick, never at import; a source scan in the tests holds every other ladder file to no trading
import (`orders.py` may import the one pure id reader). The `live` block in the config ships off and
`live.dry_run` ships true.

- **Gates:** `live.enabled`; `live.gate0_confirmed` (a human attestation, who and when); the suite's
  `modules.ladder.enabled`; a valid arm record for today; a designated account; `live.arm` naming an
  enabled paper arm; `live.symbols` non-empty and inside `names`; valid limits. The halt flag is
  reported but blocks new buys only.
- **Limits** (validated by the `live.limits` gate, enforced by the order layer): `lot_dollars`,
  `max_open_lots` per fund, `max_open_lots_total`, `max_open_lots_per_group` over `correlated_groups` (the
  shipped group is `nasdaq-semis`), `max_open_dollars` (open lots plus resting buys), `drawdown_breaker_frac`
  (blocks new buys only) and `hospital_alert_days` (an alert, never an action).
- **Arm:** `live_loop --arm --confirm YES` prints `DISCLAIMER.md` and writes the day's record only on
  exactly `YES` while the first three gates hold; `--install-task` is the same without the prompt
  (`/live-ladder-start`). Both need a running supervisor, as every module's arm does. `--disarm` removes
  the record. The record is good for its date; `live.disarm_at` (20:00 ET) ends the day.
- **`--status`** is one JSON object: every gate, the masked account, whether it is armed today, `mode`
  (`dry-run` or `live`), `held` (what an uncertain submit is holding) and `checks` (the last tick's
  positions cross-check, declared splits and dry-run intents). It makes no broker call and
  `ready` reflects the gates only. **`--once`** runs one tick (below).
- There is no `live.task_name`, so the supervisor derives no `ladder-live` job and a tick is run by
  hand. The supervisor job comes with a later step.
- **Dry-run (`live.dry_run`, guarded)** is true unless it is exactly `false`. The tick still reads the
  broker, but the executor logs the orders it would place or cancel into the decisions journal
  (`mode = dry_run`, the order's client id in the reason, the shares, limit and time in force in the
  detail) and calls the broker for nothing, so a shadow week can be checked against paper's lots. An
  intent is journaled when it is new or its terms changed since the last row for that client id;
  `--status` lists every standing intent. The adapter refuses a live placement while the flag is true, whatever the caller passed.
- **The tick (`live_loop --once`)**, in this order: the dead-man's switch (an arm record dated another
  day is removed before any broker read); the gates that block everything (any unmet and nothing is
  read, no adapter built); the orphan sweep; the splits; the reconcile; then, only inside
  `session_start` to `disarm_at` and armed, the positions cross-check, the plan inputs, `orders.plan`
  and the executor. Unarmed, or outside those hours, it stops after the reconcile.
  - **Splits** have no feed. The suite's split data is not fit for a live tick (Dolt's split table is
    refreshed by hand and holds splits the prices never made and misses real ones; the public history
    is fetched per symbol by a script; the broker has no split endpoint; a package may not import a
    script), so the source is the person's: `live.splits`, a list of
    `{"fund", "ex_date", "to", "for"}` (`to` new shares for `for` old ones). Before the reconcile, each
    open lot bought before an entry's ex-date is restated once: shares by the factor (rounded down),
    cost and levels by its inverse, money columns untouched. Each application is recorded with the lot
    in one transaction (`ladder_lot_splits`), so it never repeats, and journaled (`mode = split`). The
    planner then sees new terms: a resting sell at the old ones is cancelled and the new one placed on
    the next run. A reverse split that leaves less than one share is alerted. The reference close
    (the previous session's, which the cache may hold on either side of the split) is restated by the
    factor only when that lands nearer the fund's bid than the raw close does; with no bid it is
    unknown and the fund's buys wait. An entry the tick cannot read blocks every fund's new buys.
  - **Positions cross-check**, after the reconcile and before the plan, in dry-run too. The broker's
    equity shares in each fund are compared with the ledger's open live shares in it, so only the
    ladder's own shares count and options and other instruments are ignored. Broker below ledger is a
    shortfall and stops that fund's new buys. Broker above ledger is the person's own shares: reported
    (`mode = positions`), never adopted, no reason to stop. A mismatch whose ratio is a whole-number
    split (2:1 through 20:1, or the reverse) is reported as a suspected split and stops the fund's new
    buys too, but nothing is restated: only a `live.splits` entry restates. An unreadable positions
    read (core's `Broker.positions` returns None, never an empty list) stops every fund's new buys.
    Stopped means the fund's resting buys are cancelled and none placed; resting sells are never
    touched. Each stop is an `alert` journal row, and `--status` shows the last tick's reason under
    `checks`, with the dry-run orders it would send. A fill or sell landing between the reconcile and
    the positions read shows as a one-tick mismatch.
  - **Reconcile first.** It reads the broker's word on every order the ledger holds open and adopts any
    `ldr.` order the ledger lacks (a crash between a place and its ledger row), so a fill the ledger
    has not seen reaches the planner as a lot, not as a resting buy it would cancel or re-place. A
    filled buy becomes a lot at the broker's fill price; a filled sell closes it with realized money. A
    read that fails ends the tick before the plan (`reconcile_incomplete`). A lot whose sell order
    ended with only part of its shares sold stays open, is alerted, and is planned nothing until a
    person looks.
  - **Orphans** are working orders carrying one of our client ids (`ldr.`) that the ledger never
    recorded. Another module's order in the same account, or a hand order, is not ours and is never
    counted or touched. A sweep that cannot read the broker is unknown, not zero, and ends the tick.
  - **Disarm.** At `disarm_at` the tick runs once more while the arm still counts: the planner cancels
    every resting buy, a second reconcile-and-plan pass rests the sell of a buy that filled as it was
    cancelled, and only then the loop disarms itself. A sell is never cancelled by the disarm, the
    halt flag or the breaker.
  - **The adapter (`BrokerAdapter`)** is `core.execution.Broker` with the ladder's credentials, the
    designated account and the gates injected, and a hold file (`live_held.json`) that outlives the
    process, so an uncertain submit blocks the next minute's tick too. It re-reads the gates on every
    placement: a gate that blocks everything stops any order, the halt flag stops one that opens a
    position, and a gate flipped mid-tick bites on the next order.
- **The live ledger (`live_trades.db`)** is a separate file from paper's and is never resolved from
  `LADDER_DB_PATH`. It has paper's tables, so every ladder reader reads it, plus `ladder_orders` (one
  row per order: client id, broker order id, side, fund, lot, shares, limit, state, fill, fees, whether
  adopted) and these columns on `ladder_positions`: the masked account, both order ids and client ids,
  each side's fees and where they came from. A lot is keyed `<arm>:<fund>:<session>:<n>`, the id paper
  uses, taken from the buy's client id so it is stable. Money is the layout above with the broker's
  fills: `entry_value` + `exit_value` = `gross_pnl`; `gross_pnl` − `fees` = `net_pnl`; settlement and
  slippage zero. Fees start as the model's (`core.fees.stock_trade_fee`, `fees_source = modeled`) and are
  replaced by the broker's own transaction rows for that order when they are readable
  (`fees_source = broker`). An account number is never stored.
- **Notices.** The ladder never talks to a channel. The orchestrator's trade notifier reads the live
  ledger (`modules.ladder.live.notify_trades`, on in the example, with `live_db` declared and no
  `task_name`, so it derives no job): each live lot's buy and sale, labelled LIVE with the desktop toast,
  the sale as net with fees in their own figure and where they came from; and the journal rows a person
  has to read, each once: `alert` rows, a `split` applied, a `positions` surplus, a `refused` live
  submission or cancel (with the gate, hold or broker error that stopped it), and the two `blocked`
  rows that stop every buy (`drawdown_breaker`, invalid limits). `dry_run`, `order` and every other
  `blocked` row are not announced; the orders themselves come from `live-fill-alerts`, which names
  an order `ladder` by its published ids or its `ldr.` client id. `record_decision` collapses a session
  day's identical rows into one with a count, so a breaker held for a hundred ticks is one row and one
  notice; a standing condition (a hospital lot) is announced again the next session day. The notifier
  matches on these rows' `mode`, `symbol` and `reason` and cannot import this package; the tests pin
  them (`test_live_splits.py`, "what the notifier reads"), so changing one means changing the notifier.
- **The console's Live tab** (`/ladder/live`) reads `--status` (the gate list, `mode`, `ready`, `held`,
  `checks`, the masked `account`) and `live_trades.db` (the lot, order and journal columns above), read
  only, and has no control. It reads the gates through this command and never re-derives them, so
  changing the `--status` document's keys means changing `ladderLiveBridge.ts`, whose test runs the real
  command. The suite-wide Live page lists the ladder from the same ledger and the arm record.
- **The planner (`orders.plan`)** is pure: it takes the live block, the arm's rule, each fund's
  reference close, the open lots, the broker's working orders, the day's fills per fund, the funds'
  marks and the time, and returns place, cancel and keep actions per fund. Unarmed it returns nothing.
  - Buys rest at `rules.buy_level`'s levels (the call paper makes) from `session_start`, whole shares
    sized at the level from `lot_dollars`, as extended-hours day limits (`Ext`). Every resting buy is
    cancelled at `disarm_at`. A buy the broker ended the same day is not re-placed that session.
  - Each open lot has one `GTC Ext` limit sell at its target, placed as soon as the lot is known. A
    changed target (`decay`'s days 31 and 121) or a split changes the order's terms: the old order is
    cancelled and the new one placed on the next run, once the broker no longer lists the old one.
  - Caps hold before every buy, a resting buy counting as if filled: lots per fund, in all and per
    correlated group, and `max_open_dollars` over open cost plus resting buys. A fund with no
    reference close places nothing and its resting buys are left alone.
  - The drawdown breaker compares the open lots' unrealized result with `drawdown_breaker_frac` of the
    budget, which is `max_open_dollars`. A lot with no mark trips it. The breaker, the halt flag and
    `disarm_at` cancel resting buys and place none; none of them touches a sell.
  - A lot open past `hospital_alert_days` is an alert, as is a sell for no known lot or a split that
    leaves no whole share. Orders whose client id is not ours are never touched.
  - Splits come in as a factor per fund (new shares per old); the planner restates the lots it is
    given and the reference close and prices every order at the restated terms. The live tick restates
    the ledger itself and passes no factor for lots. A fund in `block_buys` (the positions
    cross-check) takes no new buy and loses its resting buys; its sells are planned as ever.
  - Client ids carry the order's terms, so the same state plans the same ids and the broker's working
    list alone says what is resting. A sell the broker ended unfilled is re-placed with a retry suffix,
    three times at most.
- **The executor (`orders.Executor`)** applies a plan through a broker object handed to it, cancels
  first. Dry-run logs what it would send and calls the broker for nothing. A place whose outcome is
  uncertain stops the remaining places that run. It refuses an unarmed plan.

## Boundaries

Measurement-affecting changes (the drop, the gain, the hours, the fill test, the caps, the arms) go
to a declared boundary with a `measurement_breaks` row.
