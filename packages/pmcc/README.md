# cherrypick-pmcc

> **EXPERIMENTAL, and off by default.** This module ships `enabled: false` in the orchestrator's
> `config.example.json`, so a fresh install does not run it. To turn it on, use the **Modules &
> symbols** section of the console's Config page, or set `modules.pmcc.enabled` to `true` in
> `~/.cherrypick/config.json`. It is paper-only either way.

PMCC: a paper-only module that holds a deep-ITM call and sells a shorter call against it, on XSP,
QQQ, GLD, IWM and SLV. Three arms, each its own portfolio:

- **`control`** buys an 85-90-delta call at ~21 DTE, sells the call nearest spot at ~7 DTE, holds to
  the short's expiration and closes both.
- **`shield`** and **`shield_hold`** (from 2026-10-05) follow Tom King's "Income Shield": hold a
  ~1-year 0.90-0.95-delta call and sell a 0.70-delta weekly call against it, rolled every week until
  the long reaches 45 DTE. `shield` also rolls early once the short has decayed or been breached;
  `shield_hold` holds each short to Friday.

A config must declare the shield arms to run them; the shipped example does. The advisor's
`advised:<experiment name>` twins shadow control when the advisor and this module's `advice` block
are both on.

XSP settles in cash and the ETFs physically, so each symbol is read as its own population. A
held-long position closes about ten months after entry, so judge the shield arms by their
marked-to-market weekly readout (the console's "weekly by arm" and tracker tab), not by closed
results. Why these arms and symbols: [docs/shield-study.md](docs/shield-study.md). The experiment
design, the honesty rules (early assignment on the ETFs is measured, not modelled, so their paper
results are an upper bound) and the layout are in [CLAUDE.md](CLAUDE.md).

```bash
pip install -e ../core -e .[dev]
python -m cherrypick.pmcc.paper_loop --status
python run.py tracker-index
python -m pytest
```

Config: the machine's copy is `~/.cherrypick/config/pmcc.json`; a git-ignored in-repo `config.json`
is still read, and with neither the module runs off `config.example.json`. The example's `_note`
keys are the design document.
