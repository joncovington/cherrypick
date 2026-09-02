# cherrypick-curve

curve: a paper-only module harvesting the VIX term-structure roll yield with VXX call credit
spreads (short call ~30-delta, long wing a declared width higher), gated by a daily VIX/VIX3M
regime read. Three books isolate one variable each: `control` (contango-gated entry, profit-take
or a regime-flip hard exit or close_dte), `noflip` (control's entry exactly — its exit is
control's minus the flip rule), `hook` (only the rare two-day-confirmed deep-backwardation entry).
The daily ratio/regime/hook series is recorded every session, traded or not — the module's second
product.

See [CLAUDE.md](CLAUDE.md) for the experiment design, the honesty rules (early assignment and VXX
reverse splits are measured, never modelled — the paper result is an upper bound), and the layout.

```bash
pip install -e ../core -e .[dev]
python -m cherrypick.curve.paper_loop --status
python run.py worksheet
python run.py regime-history
python -m pytest
```

Config: `config.example.json` → `config.json` (or `~/.cherrypick/config/curve.json`). The
example's `_note` keys are the design document.
