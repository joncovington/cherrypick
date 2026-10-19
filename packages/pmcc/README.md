# cherrypick-pmcc

> **EXPERIMENTAL, and off by default.** This module ships `enabled: false` in the orchestrator's
> `config.example.json`, so a fresh install does not run it. To turn it on, use the **Modules &
> symbols** section of the console's Config page, or set `modules.pmcc.enabled` to `true` in
> `~/.cherrypick/config.json`. It is paper-only either way.

PMCC: a paper-only module that holds a deep-ITM call and sells a shorter call against it, on XSP,
QQQ, GLD, IWM, SLV and SMH and the stocks AMZN, TSLA, AMD, NVDA and PLTR. Since 2026-10-06 the
trade is two arms, each its own portfolio:

- **`shield`** and **`shield_hold`** follow Tom King's "Income Shield": hold a ~1-year call near
  0.90 delta (the broker's, 0.88-0.92) and sell a 0.70-delta weekly call against it, rolled every
  week until the long reaches 45 DTE. `shield` also rolls early once the short has decayed or been
  breached; `shield_hold` holds each short to Friday.
- A short spanning an ex-dividend date or, on a stock, an earnings announcement is refused.

A config must declare the shield arms to run them; the shipped example does. `control` (a ~21-DTE
long re-bought each cycle) was retired on 2026-10-06 and its advisor twins with it; pmcc's advice is
off.

XSP settles in cash and every other symbol physically, so each symbol is read as its own population. A
held-long position closes about ten months after entry, so judge the shield arms by their
marked-to-market weekly readout (the console's "weekly by arm" and tracker tab), not by closed
results. Why these arms and symbols: [docs/shield-study.md](docs/shield-study.md). The experiment
design, the honesty rules (early assignment on the physical symbols is measured, not modelled, so their paper
results are an upper bound) and the layout are in [CLAUDE.md](CLAUDE.md).

```bash
pip install -e ../core -e .[dev]
python -m cherrypick.pmcc.paper_loop --status
python run.py tracker-index
python -m pytest
```

Config: the machine's copy is `~/.cherrypick/config/pmcc.json`; a git-ignored in-repo `config.json`
is still read, and with neither the module runs off `config.example.json`. The example's `_note`
keys are the design document, and it ships the full roster.
