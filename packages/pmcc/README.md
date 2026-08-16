# cherrypick-pmcc

PMCC-99: a paper-only module trading deep-ITM covered calls on leveraged ETFs (TNA, TQQQ, UPRO).
Buy the deepest ~99-delta call at ~21 DTE as a stock substitute, sell an ITM call at ~9 DTE; the
short's intrinsic is the downside buffer, its time value the profit; close both legs when that time
value is exhausted. Three books isolate one variable each: `control` (as taught), `keltner` (entry
timed to a Keltner-channel pullback-and-reversal), `roll` (rolls the short on a breach instead of
holding).

See [CLAUDE.md](CLAUDE.md) for the experiment design, the honesty rules (early assignment is
measured, not modelled — the paper result is an upper bound), and the layout.

```bash
pip install -e ../core -e .[dev]
python -m cherrypick.pmcc.paper_loop --status
python run.py worksheet
python -m pytest
```

Config: `config.example.json` → `config.json` (or `~/.cherrypick/config/pmcc.json`). The example's
`_note` keys are the design document.
