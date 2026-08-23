# cherrypick-bwb

bwb: a paper-only module that lays a **daily-laddered SPX put broken-wing butterfly** at the
expected move for a net credit, ~7 DTE, held to expiry — a new one every session, so ~5-7
positions ride concurrently per book at steady state. Four books trade the IDENTICAL base
structure; the only variable is whether/when a reversal-triggered put credit spread add-on fires,
turning the fly into a 1-3-2: `control` (never), `delta` (raw |delta| touch), `bounce` (confirmed
pullback off a peak), `flip` (a gamma-flip reclaim). SPX is cash-settled and European-style — no
assignment machinery, no dividend calendar, the cleanest settlement model in the suite.

See [CLAUDE.md](CLAUDE.md) for the experiment design, the honesty rules, the trigger-tick
substrate that makes a read-side threshold replay possible later, and the layout.

```bash
pip install -e ../core -e .[dev]
python -m cherrypick.bwb.paper_loop --status
python run.py worksheet
python run.py fires
python -m pytest
```

Config: `config.example.json` → `config.json` (or `~/.cherrypick/config/bwb.json`). The example's
`_note` keys are the design document.
