# cherrypick-weeklyput

> **EXPERIMENTAL, paper-only, and off by default.** To run it, set `modules.weeklyput.enabled` to
> `true` in `~/.cherrypick/config.json`, and the supervisor ticks it through the session.

weeklyput sells the index put nearest 10 delta, seven days before each weekly expiry that is not a
standard monthly, on SPX, SPY, QQQ and IWM, and holds it to expiry. It is the arm that passed
research item 57; item 69 declares it for paper. One arm, `unhedged`.

Its console page is `/weeklyput`. See [CLAUDE.md](CLAUDE.md) for the rule, what paper adds to it,
the money layout and the stream needs.

```bash
pip install -e ../core -e .[dev]
python -m cherrypick.weeklyput.paper_loop --status
python run.py cycles
python -m pytest
```

Config: the machine's copy is `~/.cherrypick/config/weeklyput.json`. Without it, the module runs
off `config.example.json`, whose `_note` keys document every setting.

## Commands

```bash
python -m cherrypick.weeklyput.paper_loop --once        # one gated tick
python -m cherrypick.weeklyput.paper_loop --interval 60 # the in-session resident loop
python -m cherrypick.weeklyput.paper_loop --status      # one JSON health object (watchdog contract)
python -m cherrypick.weeklyput.paper_loop --settle --date 2026-11-13 --symbol SPY --price 650.12
python run.py status | positions | cycles | summary | marks --position ID | calendar --start D --end D
python ../../scripts/weeklyput_item69.py track          # paper against item 57's replay (local result)
python -m pytest
```
