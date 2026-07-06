# EarningsFlyAgent

An autonomous options trading agent running a short-volatility **iron fly** strategy around earnings announcements, built on [Claude Code](https://claude.ai/code). Candidates come from an external scanner ([EarningsEdgeDetection](https://github.com/Jayesh-Chhabra/EarningsEdgeDetection)) that screens for IV term-structure inversion, IV/RV ratio, and historical winrate; only its Tier 1 recommendations are eligible for automatic entry. Unlike a continuously-managed intraday strategy, this agent opens positions once before the close and closes them once after the next morning's open — there is no active management step in between by design.

**Status**: scaffold only. `src/tt.py`, `src/db.py`, and `src/scanner_bridge.py` are stubs (`NotImplementedError`) defining the intended CLI surface — see `CLAUDE.md` for the full operating design.

## Setup

```bash
cp config.example.json config.json   # then edit config.json
python src/db.py init_db
```

## Project structure

```
EarningsFlyAgent/
├── CLAUDE.md                # Agent operational brain (loaded every loop iteration)
├── config.example.json      # Config template — copy to config.json
├── src/
│   ├── scanner_bridge.py    # Ingests EarningsEdgeDetection scanner output
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
