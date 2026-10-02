# cherrypick-curve

> **EXPERIMENTAL, and off by default.** This module ships `enabled: false` in the orchestrator's
> `config.example.json`, so a fresh install does not run it. To turn it on, use the **Modules &
> symbols** section of the console's Config page, or set `modules.curve.enabled` to `true` in
> `~/.cherrypick/config.json`. It is paper-only either way, and a base install runs only the
> `control` book (see below).

curve: a paper-only module harvesting the VIX term-structure roll yield with VXX call credit
spreads (short call ~30-delta, long wing a declared width higher), gated by a daily VIX/VIX3M
regime read. Three books isolate one variable each: `control` (contango-gated entry, profit-take
or a regime-flip hard exit or close_dte), `noflip` (control's entry exactly — its exit is
control's minus the flip rule), `hook` (only the rare two-day-confirmed deep-backwardation entry).
`noflip` and `hook` are **off on a base install** (`enabled: false` under `books` in the example
config); switch either on in `~/.cherrypick/config/curve.json`. The daily ratio/regime/hook series
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
