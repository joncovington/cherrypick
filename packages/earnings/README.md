# EarningsFlyAgent

An autonomous options trading agent running a short-volatility **iron fly** strategy around earnings announcements, built on [Claude Code](https://claude.ai/code). Candidates are found by this project's own internal scanner (`src/scanner.py`) against the rules in [`docs/screening-criteria.md`](docs/screening-criteria.md) — term structure and expected move computed live from tastytrade option chains, plus IV/RV ratio and a historical winrate backtest from DoltHub. Only Tier 1 candidates are eligible for automatic entry — check `sample_size` on any winrate result before trusting it, since historical chain coverage is limited (see the screening doc). Unlike a continuously-managed intraday strategy, this agent opens positions once before the close and closes them once after the next morning's open — there is no active management step in between by design.

**Status**: every screening criterion (#1–#10) is implemented and tested against real, live data — the scanner is functionally complete. `src/tt.py` is a real tastytrade CLI (OAuth2 session via the official SDK, OS-keyring credentials, quotes/chains/greeks/open-interest/orders) adapted from MEICAgent's proven pattern, verified against a live session with real credentials (`get_connection_status` confirms 2 real accounts). `src/db.py` remains a stub — the one piece not yet built. Live testing caught and fixed four real bugs along the way: (1) a swallowed-exception path in `tt.py` that misreported a missing-credentials error as a DXLink timeout; (2) `scanner.py` assumed `tt.py`'s response always had a `price` key, crashing on an expected-failure response shape; (3) a term-structure **sign-convention bug** that would have rejected exactly the candidates the strategy wants; (4) `apply_tiering` only checked OI/ATM-delta/expiration-window for missing data, never against their actual thresholds — see `docs/screening-criteria.md` for the full account, including a real finding about small-cap option-cycle liquidity worth reading before trusting a live scan's rejections at face value. See `CLAUDE.md` for the full operating design.

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
│   ├── scanner.py           # Internal candidate scanner — term structure, IV/RV, winrate, tiering
│   ├── tt.py                # tastytrade CLI — OAuth2 session, quotes, chains, order execution
│   ├── session.py           # Cached tastytrade OAuth session
│   ├── credentials.py       # OS-keyring credential storage
│   └── db.py                # SQLite CLI helper (stub)
├── docs/
│   └── screening-criteria.md  # Source of truth for every screening threshold
├── .claude/
│   └── commands/            # (empty — add slash commands as the loop is implemented)
├── data/                    # Created at first run (gitignored)
│   └── earnings_trades.db
└── logs/                    # Created at first run (gitignored)
```

## Disclaimer

This software is provided for **educational and informational purposes only**. It is not financial advice. Options trading involves substantial risk of loss. You are solely responsible for all trading decisions and any resulting gains or losses.
