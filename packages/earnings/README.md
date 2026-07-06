# EarningsFlyAgent

An autonomous options trading agent running a short-volatility **iron fly** strategy around earnings announcements, built on [Claude Code](https://claude.ai/code). Candidates are found by this project's own internal scanner (`src/scanner.py`) against the rules in [`docs/screening-criteria.md`](docs/screening-criteria.md) — term structure and expected move computed live from tastytrade option chains, plus IV/RV ratio and a historical winrate backtest from DoltHub. Only Tier 1 candidates are eligible for automatic entry — check `sample_size` on any winrate result before trusting it, since historical chain coverage is limited (see the screening doc). Unlike a continuously-managed intraday strategy, this agent opens positions once before the close and closes them once after the next morning's open — there is no active management step in between by design.

**Status**: every planned piece is implemented and tested — scanner (all screening criteria against real, live data, plus candidate ranking and cap/correlation-aware position selection), broker CLI (`src/tt.py`, real OAuth2 session verified against a live account), persistence (`src/db.py`, full open/close trade lifecycle and scan-audit logging), and a full **paper-trading simulation** (`src/db_paper.py`, a wholly separate database and CLI, plus `/loop /paper-trade-tick` for unattended earnings-season simulation — see [`docs/paper-trading.md`](docs/paper-trading.md)) that never touches the live account's orders or buying power. Live testing along the way caught and fixed several real bugs, detailed in `docs/screening-criteria.md`: a swallowed-exception path that misreported missing credentials as a DXLink timeout, a `KeyError` on an expected-failure response shape, a term-structure **sign-convention bug** that would have rejected exactly the candidates the strategy wants, and a threshold-check gap in `apply_tiering`. Also worth knowing: a real order built by `scanner.py get_order` was confirmed valid by tastytrade's own preflight (rejected only on account buying power, not order structure) — which is exactly why paper trading never calls `execute_trade`, even in dry-run, to avoid coupling simulated fills to the real account's financial state. See `CLAUDE.md` for the full live-trading operating design. **Not yet built**: `CLAUDE.md`'s own live-trading Loop Steps have no `/loop`-driven automation running them yet (only the paper-trading tick does).

## Setup

```bash
cp config.example.json config.json   # then edit config.json
python src/db.py init_db
pip install mysql-connector-python tastytrade keyring
python src/tt.py secrets_set   # store tastytrade OAuth client secret + refresh token in the OS keyring

# Earnings calendar, IV/RV, and winrate-backtest data (DoltHub, free, no API key).
# Clone all three repos into a common parent directory so one `dolt sql-server`
# serves them as separate databases on the same port (verified live 2026-07-06).
mkdir dolt-data && cd dolt-data
dolt clone post-no-preference/earnings
dolt clone post-no-preference/options
dolt clone post-no-preference/stocks
dolt sql-server --data-dir .   # leave running in a separate terminal
```

## Project structure

```
EarningsFlyAgent/
├── CLAUDE.md                # Agent operational brain (loaded every loop iteration)
├── config.example.json      # Config template — copy to config.json
├── src/
│   ├── scanner.py           # Internal candidate scanner — term structure, IV/RV, winrate, tiering, ranking, order-building
│   ├── tt.py                # tastytrade CLI — OAuth2 session, quotes, chains, order execution
│   ├── session.py           # Cached tastytrade OAuth session
│   ├── credentials.py       # OS-keyring credential storage
│   ├── db.py                # SQLite CLI helper — real trade lifecycle, scan audit log
│   └── db_paper.py          # SQLite CLI helper — PAPER trade lifecycle (separate DB, never mixed with real trades)
├── docs/
│   ├── screening-criteria.md  # Source of truth for every screening threshold
│   └── paper-trading.md       # Paper-trading simulation design
├── .claude/
│   └── commands/
│       └── paper-trade-tick.md  # /loop-driven paper-trading iteration
├── data/                    # Created at first run (gitignored)
│   ├── earnings_trades.db
│   └── paper_trades.db      # Wholly separate from earnings_trades.db
└── logs/                    # Created at first run (gitignored)
```

## Disclaimer

This software is provided for **educational and informational purposes only**. It is not financial advice. Options trading involves substantial risk of loss. You are solely responsible for all trading decisions and any resulting gains or losses.
