# cherrypick-contango

> **EXPERIMENTAL, paper-only.** It is not yet registered with the orchestrator, so nothing runs it
> on a schedule.

contango holds SVXY while VIX/VIX3M is in contango and SHV (T-bills) while it isn't. It decides
once a session, ten minutes before the close. Its two arms differ only in where they get out:
`control` exits at 0.97 and `flipexit` at 1.0.

See [CLAUDE.md](CLAUDE.md) for the replay numbers (the gate buys drawdown, not return), the
honesty rules and the money layout.

```bash
pip install -e ../core -e .[dev]
python -m cherrypick.contango.paper_loop --status
python run.py nav
python ../../scripts/contango_replay.py            # the rule over 2018-03 onward
python -m pytest
```

Config: the machine's copy is `~/.cherrypick/config/contango.json`. Without it, the module runs
off `config.example.json`, whose `_note` keys document every setting.
