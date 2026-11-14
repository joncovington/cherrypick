# cherrypick-ladder

> **EXPERIMENTAL, paper-only and off by default.** To turn it on, copy `config.example.json` to
> `~/.cherrypick/config/ladder.json` and set `modules.ladder.enabled` in the suite's config; the
> orchestrator then runs the paper loop as the `ladder-paper` job. Or run it by hand with the
> commands below.

ladder buys a leveraged index fund in lots when it falls 7% and sells each lot on its own at +4% of
what it paid. It starts with TQQQ and three arms: `chain` (the Discord group's rule: each further lot
7% under the last), `once` (item 26's base: one lot a session, 7% under the previous close) and
`decay` (item 27's fix for long holds: the target falls to breakeven after a month). Item 26 failed
its history test; this runs it in paper to watch it at real quotes.

See [CLAUDE.md](CLAUDE.md) for the rule, what paper adds to it, the money layout and the stream needs.

```bash
pip install -e ../core -e .[dev]
python -m cherrypick.ladder.paper_loop --status
python run.py summary
python -m pytest
```

Config: the machine's copy is `~/.cherrypick/config/ladder.json`. Without it, the module runs off
`config.example.json`, whose `_note` keys document every setting.

## Commands

```bash
python -m cherrypick.ladder.paper_loop --once          # one tick
python -m cherrypick.ladder.paper_loop --interval 60   # the resident loop, until session_end
python -m cherrypick.ladder.paper_loop --status        # one JSON health object
python run.py status | lots [--arm A] [--open|--closed] | summary [--arm A]
python -m pytest
```
