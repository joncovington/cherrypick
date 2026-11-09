# cherrypick-pmcc

**Retired 2026-11-08; off by default.** The owner switched its entries off (`defaults.wind_down`, #974, stops
entries and closes what is open; the module goes off once flat). The code, ledgers and docs are kept. To
reinstate: set `modules.pmcc.enabled` to true in the suite config (the **Modules & symbols** section of the
console's Config page, or `~/.cherrypick/config.json`), `enabled` to true on the `shield`, `shield_hold` and
`shield_guard` arms in `pmcc.json`, and `defaults.wind_down` to false or remove it. It is paper-only either
way.

PMCC: a paper-only module that holds a deep-ITM call and sells a shorter call against it, on XSP,
QQQ, GLD, IWM, SLV and SMH and the stocks AMZN, TSLA, AMD, NVDA and PLTR. Since 2026-10-06 the
trade is two arms, each its own portfolio:

- **`shield`** and **`shield_hold`** follow a published held-long design ([credited](../../docs/strategy-sources.md)): hold a ~1-year call near
  0.90 delta (the broker's, 0.88-0.92) and sell a 0.70-delta weekly call against it, rolled every
  week until the long reaches 45 DTE. `shield` also rolls early once the short has decayed or been
  breached; `shield_hold` holds each short to Friday. `shield_guard` (from 2026-10-26) holds like
  `shield_hold` but rolls an in-the-money short once its extrinsic is nearly gone, the point where a
  real short risks early assignment.
- A short spanning an ex-dividend date or, on a stock, an earnings announcement is refused.

A config must declare the shield arms to run them; the shipped example does, with each switched off. `control` (a ~21-DTE
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
