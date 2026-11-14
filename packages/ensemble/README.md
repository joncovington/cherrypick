# cherrypick-ensemble

> **EXPERIMENTAL, paper-only, and off by default.** A position is a ledger row; there is no order path.

A rules engine for short-dated option arms, and the paper loop that runs it. An arm is configuration;
`engine.decide` is the one deterministic decision the backtest (research item 75) and the paper loop both
make, and the loop takes any arm `arms.load_arms` returns. See [CLAUDE.md](CLAUDE.md) for the arm schema,
the readings the engine fixes, the money layout and the loop.

## Commands

```bash
pip install -e ../core -e ../technicals -e .[dev]
python -m pytest
ruff format . && ruff check .

python run.py status        # today's arms and windows, open positions, today's session rows
python run.py positions     # every position, the money laid out to add up
python run.py decisions --date 2026-11-16    # the gate that blocked each refused arm
python run.py summary       # per arm and combined: trades, net, drawdown
python run.py replay ~/.cherrypick/data/ensemble/replay/2026-11-16.jsonl

python -m cherrypick.ensemble.paper_loop --once      # what the orchestrator ticks
python -m cherrypick.ensemble.paper_loop --status
```

Copy `config.example.json` to `~/.cherrypick/config/ensemble.json` to change an arm, add one or point at
another event file.
