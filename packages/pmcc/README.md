# cherrypick-pmcc

PMCC-99: a paper-only module trading deep-ITM covered calls on TQQQ. Buy an 85-90-delta call at
~21 DTE as a stock substitute, sell the ATM call nearest spot at ~7 DTE (no yield floor, either
side of spot); hold to the short's own expiration, then close both legs together. Single `control`
book plus the advisor's `advised:control` twin, where the old early-tv-exit rule survives as a
tunable A/B against the new hold-to-expiry default.

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
