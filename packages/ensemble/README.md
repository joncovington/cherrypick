# cherrypick-ensemble

> **EXPERIMENTAL, paper-only, and off by default.** There is no paper loop yet; this is the engine.

A rules engine for short-dated option arms. An arm is configuration; `engine.decide` is the one
deterministic decision the backtest (research item 75) and, later, the paper loop both make. See
[CLAUDE.md](CLAUDE.md) for the arm schema, the readings the engine fixes, the money layout and what the
paper loop will need.

```bash
pip install -e ../core -e ../technicals -e .[dev]
python -m pytest
```
