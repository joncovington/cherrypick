# cherrypick-curve

**Retired 2026-11-01; off by default.** Research item 37 (docs/edge-hypotheses-plan.md) tested the module's
own rule at real quotes, and every arm failed after costs. To reinstate: set `modules.curve.enabled` to true
in the suite config (the **Modules & symbols** section of the console's Config page, or
`~/.cherrypick/config.json`) and `enabled` to true on the arm or arms you want (`control`, `noflip`, `near`)
in `curve.json`; `advice.enabled` is separate and stays off. It is paper-only either way.

curve: a paper-only module harvesting the VIX term-structure roll yield with VXX call credit
spreads (short call ~30-delta, long wing a declared width higher), gated by a daily VIX/VIX3M
regime read. Each arm is one variable from `control` (contango-gated entry; profit-take, a
regime-flip hard exit or close_dte): `noflip` (control's entry exactly, minus the flip exit),
`near` (control's rules on a 0.40-delta short, from 2026-10-06) and `hook` (the rare
two-day-confirmed backwardation entry; retired 2026-10-15, never traded). `noflip` and `near` are
**off in the example config** (`enabled: false` under `books`, as is `control` since the retirement, each with a
`_retired` note); switch the ones you want on in `~/.cherrypick/config/curve.json` after reinstating. Spreads are $2 wide from 2026-10-06. The daily ratio/regime/hook series
is recorded every session, traded or not — the module's second product.

See [CLAUDE.md](CLAUDE.md) for the experiment design, the honesty rules (early assignment and VXX
reverse splits are measured, never modelled — the paper result is an upper bound), and the layout.

```bash
pip install -e ../core -e .[dev]
python -m cherrypick.curve.paper_loop --status
python run.py worksheet
python run.py regime-history
python -m pytest
```

Config: the machine's copy is `~/.cherrypick/config/curve.json`; a git-ignored in-repo
`config.json` is still read, and with neither the module runs off `config.example.json`. The
example's `_note` keys are the design document.
