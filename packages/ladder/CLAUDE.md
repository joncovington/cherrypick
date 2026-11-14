# cherrypick-ladder

⚠️ **EXPERIMENTAL.** The share dip ladder in paper, on leveraged index funds (TQQQ first): buy a lot
7% down, sell each lot on its own at +4% of its cost, no stop, no time limit. **Paper-only,
credential-free, no live path** (posture: root file). Ledger: `~/.cherrypick/data/ladder/paper_trades.db`.
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
    empty book resets it. The group's posted SOXL fills of 2026-11-13 are this rule.
- **Target rules:** `fixed` 104% of cost; `decay` (item 27) 104% to day 30, breakeven after the sale's
  fees from day 31, 95% of cost from day 121, in calendar days from the buy session.
- **Rounding:** a buy level rounds down to the cent and a target up, so a rounded limit never fills
  on worse terms than the rule's own number.
- **Size:** whole shares, never more than `lot_dollars`; a lot under `min_shares` is refused.
- **Caps:** `max_open_lots` per arm per fund, `max_open_lots_total` per arm across funds. Each arm
  keeps its own book.

## What paper adds, and says

1. **Fills are resting limits read off one quote a minute.** A buy fills at its level when the ask
   is at or under it; a sell fills at its target when the bid is at or over it. The fill is the
   limit price, so slippage is zero by construction, and a touch shorter than a tick is missed.
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
`net_pnl`. Fees are `core.fees.stock_trade_fee`: none on a buy, the SEC fee and FINRA TAF on a sale.
Settlement and slippage are zero. `exit_reason` is `target_gain`, `target_breakeven` or
`target_floor`.

## Stream needs (`stream_request.py`)

Each fund as an underlying (Trade and Summary) and as a leg (Quote), `nearest_window` false: no option
chain is read. A fund removed from the config stays requested until its last lot sells.

## Layout

`rules.py` (levels, targets, size, money, pure), `provider.py` (read-only cache views), `db.py`
(ledger), `paper_loop.py` (the tick), `stream_request.py`, `analytics.py` (the forward read),
`cli.py` (status, lots, summary).

## Commands

Commands to run the package, its tests and its lint are in [README.md](README.md#commands).

`paper_loop --force` acts outside the hours, for testing only.

## Boundaries

Measurement-affecting changes (the drop, the gain, the hours, the fill test, the caps, the arms) go
to a declared boundary with a `measurement_breaks` row.
