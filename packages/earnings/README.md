# EarningsAgent

An autonomous options trading agent running earnings-announcement strategies. Six strategies
are implemented today — **iron fly**, **iron condor**, **expected-move butterfly**,
**double calendar**, **short strangle**, and **jade lizard** — spanning defined-risk credit
spreads, a debit calendar spread, a debit butterfly, and two genuinely undefined-risk
strategies gated to paper trading by default. The project is split into a strategy-agnostic
engine (`src/scanner.py`: earnings calendar lookup, average volume, IV/RV ratio, historical
winrate, liquidity gates, candidate ranking/position selection) and per-strategy modules
under `src/strategies/` that hold each strategy's own thresholds, tiering, and order
construction — a new strategy plugs in without touching the shared engine or colliding with
another strategy's config. A top-level orchestrator (`src/rank_strategies.py`) evaluates all
six against every symbol on the day's earnings calendar and picks the single best-ranked
strategy per symbol, so entries aren't decided strategy-by-strategy in isolation. Screening
rules live in [`docs/screening-criteria.md`](docs/screening-criteria.md) — only Tier 1
candidates are eligible for automatic entry; check `sample_size` on any winrate result before
trusting it, since historical chain coverage is limited (see the screening doc). Positions
open once before the close, get an early profit-target/stop-loss/delta-stop check right after
the market reopens the next morning, and are unconditionally closed by the close window if
nothing already closed them — there is no continuous intraday management in between by
design (except `double_calendar`, which spans multiple weeks and gets its own management
step). See **How it works** below for the full pipeline.

**Status**: every planned piece is implemented and tested — the shared engine and all six
strategy modules (screening against real, live data, cross-strategy ranking, cap/correlation-
aware position selection), broker CLI (`src/tt.py`, real OAuth2 session verified against a
live account), persistence (`src/db.py`, a strategy-agnostic trade lifecycle and scan-audit
log), and a full **paper-trading simulation** (`src/db_paper.py`, a wholly separate database
and CLI — see [`docs/paper-trading.md`](docs/paper-trading.md)) that never touches the live
account's orders or buying power. Paper vs. live is a single config switch
(`enable_live_trading`, default `false`): the same loop definition in `CLAUDE.md` handles
both, with no separate paper-trading loop to keep in sync. Live testing along the way caught
and fixed several real bugs, detailed in `docs/screening-criteria.md`: a swallowed-exception
path that misreported missing credentials as a DXLink timeout, a `KeyError` on an
expected-failure response shape, a term-structure **sign-convention bug** that would have
rejected exactly the candidates the strategy wants, and a threshold-check gap in
`apply_tiering`. Also worth knowing: a real order built by `strategies/iron_fly.py get_order`
was confirmed valid by tastytrade's own preflight (rejected only on account buying power, not
order structure) — which is exactly why paper trading never calls `execute_trade`, even in
dry-run, to avoid coupling simulated fills to the real account's financial state. See
`CLAUDE.md` for the full operating design.

## How it works

**1. Finding earnings symbols.** The candidate universe is rebuilt every entry-window tick,
not cached from the morning: `scanner.fetch_entry_window_calendar()` queries the DoltHub
earnings calendar for today (keeping only `After market close` reports — a same-day BMO
report already happened this morning and must not be re-entered) and for tomorrow (keeping
only `Before market open` reports, still ahead of us this afternoon), and merges the two.

**2. Reviewing and selecting a strategy.** Every symbol on that merged calendar is screened
against all six strategies before anything is entered. `rank_strategies.evaluate_symbol()`
computes the shared signals once per symbol (average volume, IV/RV ratio, historical
winrate), then runs each strategy's own criteria fetch and tiering, producing a tier
(Tier 1 / Tier 2 / Near Miss / Reject) and a composite score
(`|edge signal| × IV/RV ratio × shrunk winrate`, where the edge signal is term structure for
five strategies or skew for the expected-move butterfly, which has no back-month to compare
against) per strategy. The single best-ranked Tier 1/2 strategy wins each symbol — never two
strategies on the same name, since one earnings surprise would hit both positions at once
rather than diversify anything. Symbols are then ranked against each other by their winning
strategy's score, and `scanner.select_positions()` applies `max_concurrent_earnings_positions`
and `correlation_block_list` across that combined, cross-symbol list.

**3. Entering a trade.** Selection isn't authorization to trade — every selected symbol still
clears a re-verification gate using live data, not the scan-time snapshot:
naked-strategy live-mode block (`short_strangle`/`jade_lizard` never enter live without
`allow_naked_strategies` explicitly set, though paper mode always allows them) → re-verify
via `reverify_symbol()` (reruns the winning strategy's own tiering fully fresh) →
position-level risk cap (wing width minus credit, or debit paid, vs.
`max_risk_per_trade_pct` of NLV) → build the order and record it. Every strategy's
`get_order` returns the same `order.price`/`order.price_effect` shape, so `entry_credit` is
derived the same way regardless of which strategy won: positive for a credit, negative for a
debit. Paper mode stops at `db_paper.py save_trade` — no broker call at all; live mode
submits via `tt.py execute_trade --live` and records through `db.py` instead.

**4. Attaching stops.** Every open position gets one check between market open and the close
window — reacting to an overnight gap the moment the market reopens, ahead of the mechanical
close a few minutes later:

| Strategy | Profit target | Stop | Basis |
|---|---|---|---|
| Iron fly / iron condor | 50% of credit | 1.5× credit | Earnings-specific short-straddle convention |
| Expected-move butterfly | 25% of debit | 40% loss of debit | Debit-butterfly convention |
| Double calendar | 25% of debit | 100% of debit (+ 5-day time exit) | Own multi-week research |
| Short strangle / jade lizard | — | 0.45Δ on either short leg | Undefined risk — the stop *is* the risk management |

**5. Reviewing for exit.** Three layers, in firing order, with the last always unconditional:
an early profit-target/stop-loss/delta check (Step 3c) right after the open; `double_calendar`'s
own multi-week management step (Step 3b), which can close just the threatened side and leave
the rest running; and a close-window sweep (Step 3) that force-closes whatever remains,
regardless of P&L, by reading each position's own stored order legs (`legs_json`) rather than
strategy-specific strike columns — one mechanism covers every strategy's shape.

**6. Logging and analyzing.** Every decision point writes to `scan_log` — one row per
(symbol, strategy) evaluated whether or not it won, one reserved `_ranked` summary row per
symbol naming the winner and where it landed against every other candidate that day, and one
row per position at entry and at each exit decision. `get_pnl_summary` reads closed positions
back out of `trades` — win rate, average win/loss, totals by strategy — from whichever
database (paper or live) Step 0 selected. A quiet night and a broken filter look identical in
the P&L alone; they don't in the log.

## Setup

```bash
cp config.example.json config.json   # then edit config.json
python src/db.py init_db
pip install mysql-connector-python tastytrade keyring
python src/tt.py secrets_set   # store tastytrade OAuth client secret + refresh token in the OS keyring

# Earnings calendar, IV/RV, and winrate-backtest data (DoltHub, free, no API key).
# Clone all three repos into a common parent directory so one `dolt sql-server`
# serves them as separate databases on the same port.
mkdir dolt-data && cd dolt-data
dolt clone post-no-preference/earnings
dolt clone post-no-preference/options
dolt clone post-no-preference/stocks
dolt sql-server --data-dir .   # leave running in a separate terminal
```

## Project structure

```
EarningsAgent/
├── CLAUDE.md                # Agent operational brain (loaded every loop iteration)
├── config.example.json      # Config template — copy to config.json (top-level = project-wide,
│                             #   "strategies.<name>" = per-strategy tuning)
├── src/
│   ├── scanner.py           # Strategy-agnostic engine — calendar, IV/RV, winrate, volume, liquidity gates, ranking
│   ├── rank_strategies.py   # Cross-strategy orchestrator — evaluates all six per symbol, ranks symbols against each other
│   ├── strategies/
│   │   ├── iron_fly.py               # ATM short straddle + wings
│   │   ├── iron_condor.py            # Same shape, short strikes at the expected-move boundary
│   │   ├── expected_move_butterfly.py  # 1-2-1 debit butterfly, side picked by skew
│   │   ├── double_calendar.py         # Front short / back long calendar spread
│   │   ├── short_strangle.py          # Condor's short strikes, no wings (undefined risk)
│   │   └── jade_lizard.py             # Short put + riskless call spread (undefined risk on the put side)
│   ├── tt.py                # tastytrade CLI — OAuth2 session, quotes, chains, order execution
│   ├── session.py           # Cached tastytrade OAuth session
│   ├── credentials.py       # OS-keyring credential storage
│   ├── db.py                # SQLite CLI helper — real trade lifecycle, scan audit log
│   └── db_paper.py          # SQLite CLI helper — PAPER trade lifecycle (separate DB, never mixed with real trades)
├── docs/
│   ├── screening-criteria.md  # Source of truth for iron fly's screening thresholds
│   └── paper-trading.md       # Paper-trading simulation design (uses CLAUDE.md's Loop Steps directly)
├── .claude/
│   └── commands/            # (empty — no separate paper/live command; CLAUDE.md's Loop Steps cover both)
├── dolt-data/                # DoltHub clones (gitignored, machine-local, multi-GB)
├── data/                    # Created at first run (gitignored)
│   ├── earnings_trades.db   # trades/scan_log tagged by `strategy` column
│   └── paper_trades.db      # Wholly separate from earnings_trades.db
└── logs/                    # Created at first run (gitignored)
```

## License

MIT — see [`LICENSE`](LICENSE).

## Disclaimer

This software is provided for **educational and informational purposes only**. It is not financial advice. Options trading involves substantial risk of loss. You are solely responsible for all trading decisions and any resulting gains or losses.
