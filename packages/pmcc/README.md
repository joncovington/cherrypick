# cherrypick-pmcc

> **EXPERIMENTAL, and off by default.** This module ships `enabled: false` in the orchestrator's
> `config.example.json`, so a fresh install does not run it. To turn it on, use the **Modules &
> symbols** section of the console's Config page, or set `modules.pmcc.enabled` to `true` in
> `~/.cherrypick/config.json`. It is paper-only either way.

PMCC-99: a paper-only module trading deep-ITM covered calls on TQQQ and XSP. Buy an 85-90-delta
call at ~21 DTE as a stock substitute, sell the call nearest spot at ~7 DTE (no yield floor, either
side of spot); hold to the short's own expiration, then close both legs together. TQQQ settles
physically and XSP in cash, so their results are not interchangeable. There is a single `control`
book, so a base install runs just that. Beside it sit the advisor's `advised:<experiment name>`
twins (one per concurrent advisor experiment since 2026-09-17; a single `advised:control` before),
where the old early-tv-exit rule survives as a tunable A/B against the new hold-to-expiry default.
The advised twins appear only when the advisor and this module's `advice` block are both switched
on.

See [CLAUDE.md](CLAUDE.md) for the experiment design, the honesty rules (early assignment on TQQQ is
measured, not modelled — the paper result is an upper bound), and the layout.

```bash
pip install -e ../core -e .[dev]
python -m cherrypick.pmcc.paper_loop --status
python run.py worksheet
python -m pytest
```

Config: the machine's copy is `~/.cherrypick/config/pmcc.json`; a git-ignored in-repo `config.json`
is still read, and with neither the module runs off `config.example.json`. The example's `_note`
keys are the design document.
