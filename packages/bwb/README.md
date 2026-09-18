# cherrypick-bwb

bwb: a module that lays a **daily-laddered SPX put broken-wing butterfly** at the
expected move for a net credit, ~7 DTE, held to expiry — a new one every session, so ~5-7
positions ride concurrently per book at steady state. Four books trade the IDENTICAL base
structure; the only variable is whether/when a reversal-triggered put credit spread add-on fires,
turning the fly into a 1-3-2: `control` (never), `delta` (raw |delta| touch), `bounce` (confirmed
pullback off a peak), `flip` (a gamma-flip reclaim). SPX is cash-settled and European-style — no
assignment machinery, no dividend calendar, the cleanest settlement model in the suite.

See [CLAUDE.md](CLAUDE.md) for the experiment design, the honesty rules, the trigger-tick
substrate that makes a read-side threshold replay possible later, and the layout.

Paper by default. Since 2026-09-18 one arm can trade **live**, armed per day by `/live-bwb-start`
with a literal YES: the same structure as its paper twin, as a limit order walked down to a
cost-derived floor, under a worst-case margin cap, with no closing orders (SPX cash-settles on the
official print). The live ledger is `data/bwb/live_trades.db`, a separate file; the paper books
are untouched.

```bash
pip install -e ../core -e .[dev]
python -m cherrypick.bwb.paper_loop --status
python run.py worksheet
python run.py fires
python -m pytest
```

Config: `config.example.json` → `config.json` (or `~/.cherrypick/config/bwb.json`). The example's
`_note` keys are the design document.
