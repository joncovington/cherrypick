# cherrypick-bwb

**Retired; off by default.** The module ships switched off and stays in the tree so it can be reinstated.
Its history found no arm that wins after costs (research/history-studies-2026-10.md). To reinstate: set `modules.bwb.enabled` to true in the suite config and
`enabled` to true on the `control` arm (and on `bounce` or `flip` if wanted) in `bwb.json`; the supervisor
derives the bwb jobs from the module switch. Live trading stays behind its own gate and per-day arm
(`/live-bwb-start` and `--arm` refuse while the module is off).

> **⚠️ Experimental, educational software — not financial advice.** cherrypick is a prototype for
> learning about and researching options strategies. Its live-trading paths place **real,
> irreversible orders** at your own risk; options trading involves substantial risk of loss and is
> not suitable for all investors. Paper results are simulated and do not represent actual trading.
> Provided "as is", without warranty. This module has a live-trading path (armed per day by `/live-bwb-start`,
> off by default) that places real orders through your broker account. **Read [DISCLAIMER.md](../../DISCLAIMER.md) before use.**

bwb: a module that lays a **daily-laddered SPX put broken-wing butterfly** at the
expected move for a net credit, ~7 DTE, held to expiry — a new one every session, so ~5-7
positions ride concurrently per arm at steady state. The arms trade the IDENTICAL base
structure; the only variable is whether/when a reversal-triggered put credit spread add-on fires,
turning the fly into a 1-3-2: `control` (never), `bounce` (confirmed pullback off a peak), `flip` (a
gamma-flip reclaim). The code also keeps the retired `delta` (raw |delta| touch) and call-side `wall`
arms, so their rows stay readable. The example config ships every arm off (`enabled: false`, each with
a `_retired` note); once reinstated, turn `control`, and `bounce` or `flip` if wanted, on in
`~/.cherrypick/config/bwb.json`. SPX is cash-settled and European-style — no
assignment machinery, no dividend calendar, the cleanest settlement model in the suite.

See [CLAUDE.md](CLAUDE.md) for the experiment design, the honesty rules, the trigger-tick
substrate that makes a read-side threshold replay possible later, and the layout.

Paper by default. One arm (`live.arm`, `control` in the example) can trade **live**. Live trading is experimental, off by default and at your own risk. Nothing trades until your
own config sets `live.enabled` and fills in `live.gate0_confirmed`, and then only on a day armed by
`/live-bwb-start`, which shows the disclaimer verbatim and requires an explicit YES (without Claude Code:
`python -m cherrypick.bwb.live_loop --arm --confirm YES`, which prints `DISCLAIMER.md` and arms nothing
unless the word is exactly `YES`; `--readiness` and `--status` first, `--disarm` to stop; `--install-task`
is the same arm without the prompt, for the slash command, and both refuse while `modules.bwb.enabled` is
false); while armed, the orchestrator's supervisor runs the `bwb-live` job. It enters the same structure as its paper twin,
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

## Commands

```bash
python -m cherrypick.bwb.paper_loop --once        # one gated tick
python -m cherrypick.bwb.paper_loop --interval 60 # the in-session resident loop
python -m cherrypick.bwb.paper_loop --status      # one JSON health object (watchdog contract)
python -m cherrypick.bwb.paper_loop --settle --date 2026-09-18 --price 6400.10  # official print
python run.py status | worksheet | fires          # positions + expiration / worksheet / fire counts
python run.py iv-premium [--windows]            # implied at entry vs realised to expiry, per window
python run.py live-gap | entry-context           # live arm vs paper per session; entry vol per position (read-only)
python -m pytest                                  # temp CHERRYPICK_HOME; no broker, no streamer
ruff check . && ruff format .                     # line-length 110

python -m cherrypick.bwb.live_loop --once         # LIVE dry-run smoke: preflights, places nothing
python -m cherrypick.bwb.live_loop --status       # armed_for / pending / orphans / breaker / broker_held
python -m cherrypick.bwb.live_loop --settle --price 6400.10 --date 2026-09-18   # official print
python -m cherrypick.bwb.fee_reconcile            # settled live rows vs broker transactions
```
