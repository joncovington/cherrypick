# EarningsFlyAgent

An autonomous options trading agent running a short-volatility **iron fly** strategy around earnings announcements, built on [Claude Code](https://claude.ai/code). Candidates are found by this project's own internal scanner (`src/scanner.py`) against the rules in [`docs/screening-criteria.md`](docs/screening-criteria.md) — term structure and expected move computed live from tastytrade option chains, plus IV/RV ratio from DoltHub. Only Tier 1 candidates are eligible for automatic entry; that tier also needs a historical winrate backtest not yet implemented, so every candidate currently caps at Tier 2 (logged, not auto-traded). Unlike a continuously-managed intraday strategy, this agent opens positions once before the close and closes them once after the next morning's open — there is no active management step in between by design.

**Status**: scaffold, with the DoltHub-backed pieces of the scanner implemented and tested live. `src/tt.py` and `src/db.py` are still stubs (`NotImplementedError` on live broker calls); `src/scanner.py`'s `get_calendar` and `get_iv_rv` commands work end-to-end against a real DoltHub clone, `get_candidates` (which ties everything together) is not implemented yet. See `CLAUDE.md` for the full operating design.

## Setup

```bash
cp config.example.json config.json   # then edit config.json
python src/db.py init_db
pip install mysql-connector-python

# Earnings calendar + IV/RV data (DoltHub, free, no API key).
# Clone both repos into a common parent directory so one `dolt sql-server`
# serves both as separate databases on the same port (verified live 2026-07-06).
mkdir dolt-data && cd dolt-data
dolt clone post-no-preference/earnings
dolt clone post-no-preference/options
dolt sql-server --data-dir .   # leave running in a separate terminal
```

## Project structure

```
EarningsFlyAgent/
├── CLAUDE.md                # Agent operational brain (loaded every loop iteration)
├── config.example.json      # Config template — copy to config.json
├── src/
│   ├── scanner.py           # Internal candidate scanner — term structure, expected move
│   ├── tt.py                # tastytrade CLI — quotes, chains, order execution
│   └── db.py                # SQLite CLI helper
├── docs/                    # (empty — add setup/operating/strategy docs as it grows)
├── .claude/
│   └── commands/            # (empty — add slash commands as the loop is implemented)
├── data/                    # Created at first run (gitignored)
│   └── earnings_trades.db
└── logs/                    # Created at first run (gitignored)
```

## Disclaimer

This software is provided for **educational and informational purposes only**. It is not financial advice. Options trading involves substantial risk of loss. You are solely responsible for all trading decisions and any resulting gains or losses.
