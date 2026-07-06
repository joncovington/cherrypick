# EarningsFlyAgent

An autonomous options trading agent running a short-volatility **iron fly** strategy around earnings announcements, built on [Claude Code](https://claude.ai/code). Candidates are found by this project's own internal scanner (`src/scanner.py`), which screens for IV term-structure inversion and expected move computed live from tastytrade option chains. Only "Tier A"-qualified candidates are eligible for automatic entry; a richer historical winrate/IV-RV-ratio signal (Tier B) is planned but not yet implemented. Unlike a continuously-managed intraday strategy, this agent opens positions once before the close and closes them once after the next morning's open — there is no active management step in between by design.

**Status**: scaffold only. `src/tt.py`, `src/db.py`, and `src/scanner.py` are stubs/partial (`NotImplementedError` on live calls; the term-structure math itself is implemented as a pure function) defining the intended CLI surface — see `CLAUDE.md` for the full operating design.

## Setup

```bash
cp config.example.json config.json   # then edit config.json
python src/db.py init_db

# Earnings calendar source (DoltHub, free, no API key)
pip install mysql-connector-python
dolt clone post-no-preference/earnings && cd earnings && dolt sql-server   # leave running in a separate terminal
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
