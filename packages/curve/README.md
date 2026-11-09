# cherrypick-curve

**Retired; off by default.** The module ships switched off and stays in the tree so it can be reinstated.
Research item 37 (research/edge-hypotheses-plan.md) tested its rule at real quotes, and every arm failed
after costs. To reinstate: set `modules.curve.enabled` to true in the suite config (the **Modules & symbols**
section of the console's Config page, or `~/.cherrypick/config.json`) and `enabled` to true on the arm or
arms you want (`control`, `noflip`, `near`) in `~/.cherrypick/config/curve.json`; `advice.enabled` is
separate and stays off. It is paper-only either way: there is no live path.

curve harvests the VIX term-structure roll yield with VXX call credit spreads (short call ~30-delta, long
wing a declared width higher, $2 wide), gated by a daily VIX/VIX3M regime read. Each arm differs from
`control` (contango-gated entry; profit-take, a regime-flip hard exit or `close_dte`) by one variable:
`noflip` is control's entry exactly, minus the flip exit; `near` is control's rules on a 0.40-delta short.
The daily ratio and regime series is recorded every session, traded or not, and is the module's second
product. The surviving expression of the same signal is `contango` (shares, no option costs).

See [CLAUDE.md](CLAUDE.md) for the experiment design, the honesty rules (early assignment and VXX reverse
splits are measured, never modelled, so the paper result is an upper bound), and the layout.

```bash
pip install -e ../core -e .[dev]
python -m cherrypick.curve.paper_loop --status
python run.py worksheet
python run.py regime-history
python -m pytest
```

Config: the machine's copy is `~/.cherrypick/config/curve.json`; a git-ignored in-repo `config.json` is
still read, and with neither the module runs off `config.example.json`. The example's `_note` keys are the
design document.

## Commands

```bash
python -m cherrypick.curve.paper_loop --once        # one gated tick (what the supervisor spawns off-session)
python -m cherrypick.curve.paper_loop --interval 60 # the in-session resident loop
python -m cherrypick.curve.paper_loop --status      # one JSON health object (watchdog contract)
python -m cherrypick.curve.paper_loop --settle --date 2026-09-18 --price 42.10  # official print
python run.py status                                # open positions + target expiration + today's regime
python run.py regime                                # the stored daily regime series
python run.py worksheet                             # the live per-position worksheet
python run.py exposure                              # the early-assignment-exposure telemetry
python run.py headline                              # closed results per arm
python run.py equity                                # each arm's daily marked equity and tear sheet
python run.py regime-history                        # the VIX/VIX3M signal replay over stored history
python -m pytest                                    # temp CHERRYPICK_HOME; no broker, no streamer needed
ruff check . && ruff format .                       # line-length 110
```
