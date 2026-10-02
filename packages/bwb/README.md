# cherrypick-bwb

> **⚠️ Experimental, educational software — not financial advice.** cherrypick is a prototype for
> learning about and researching options strategies. Its live-trading paths place **real,
> irreversible orders** at your own risk; options trading involves substantial risk of loss and is
> not suitable for all investors. Paper results are simulated and do not represent actual trading.
> Provided "as is", without warranty. This module has a live-trading path (armed per day by `/live-bwb-start`,
> off by default) that places real orders through your broker account. **Read [DISCLAIMER.md](../../DISCLAIMER.md) before use.**

bwb: a module that lays a **daily-laddered SPX put broken-wing butterfly** at the
expected move for a net credit, ~7 DTE, held to expiry — a new one every session, so ~5-7
positions ride concurrently per book at steady state. Four books trade the IDENTICAL base
structure; the only variable is whether/when a reversal-triggered put credit spread add-on fires,
turning the fly into a 1-3-2: `control` (never), `delta` (raw |delta| touch), `bounce` (confirmed
pullback off a peak), `flip` (a gamma-flip reclaim). A base install runs only `control`: the example
config switches `delta`, `bounce` and `flip` off with `enabled: false`, and a fifth, call-side `wall`
book is opt-in. Turn any of them on in `~/.cherrypick/config/bwb.json`. SPX is cash-settled and European-style — no
assignment machinery, no dividend calendar, the cleanest settlement model in the suite.

See [CLAUDE.md](CLAUDE.md) for the experiment design, the honesty rules, the trigger-tick
substrate that makes a read-side threshold replay possible later, and the layout.

Paper by default. Since 2026-09-18 one arm (`live.arm`, `control` in the example) can trade
**live**. Live trading is experimental, off by default and at your own risk. Nothing trades until your
own config sets `live.enabled` and fills in `live.gate0_confirmed`, and then only on a day armed by
`/live-bwb-start`, which shows the disclaimer verbatim and requires an explicit YES; while armed, the
orchestrator's supervisor runs the `bwb-live` job. It enters the same structure as its paper twin,
at most one a day, as a limit order walked down to a cost-derived floor, under a worst-case margin
cap, with an optional mark-drawdown breaker that blocks new entries only, and with no closing orders (SPX
cash-settles, and the ledger settles on an official print or not at all). The live ledger is `data/bwb/live_trades.db`, a separate file; the paper books
are untouched.

```bash
pip install -e ../core -e .[dev]
python -m cherrypick.bwb.paper_loop --status
python run.py worksheet
python run.py fires
python -m pytest
```

Config: with no config of its own, bwb runs from the shipped `config.example.json`. Your own goes in
`~/.cherrypick/config/bwb.json` (or a package-local `config.json`, or a file named by `BWB_CONFIG`).
The example's `_note` keys are the design document.
