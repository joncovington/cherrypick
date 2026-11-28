# cherrypick-ladder

> **EXPERIMENTAL and off by default.** The paper loop is the module's normal mode. It also has a live
> path (gates, a daily arm and an order path) that ships in dry-run and off: with `live.dry_run` true,
> the shipped value, it reads the broker and journals the orders it would send, and places nothing. To
> turn the paper loop on, copy `config.example.json` to `~/.cherrypick/config/ladder.json` and set
> `modules.ladder.enabled` in the suite's config; the orchestrator then runs it as the `ladder-paper`
> job. Or run it by hand with the commands below.

ladder buys a leveraged index fund in lots when it falls 7% and sells each lot on its own at +4% of
what it paid. It starts with TQQQ and four arms: `session` (the Discord group's rule: each session
steps down 7% at a time from the previous close), `chain` (item 26's: each further lot 7% under the
last one bought, across sessions), `once` (item 26's base: one lot a session, 7% under the previous
close) and `decay` (item 27's fix for long holds: the target falls to breakeven after a month). Item 26 failed
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

## Live

Off until you set `live.enabled` and write the `live.gate0_confirmed` attestation by hand, and sending
nothing until you also set `live.dry_run` to `false`. `live_loop --status` lists every gate with its
verdict (no broker call). Arming is one day at a time: `live_loop --arm --confirm YES`, the
`/live-ladder-start` command, or, with `live_arming` on in `console.json`, the switch on the Live page.
The supervisor's `ladder-live` job (from the suite config's `modules.ladder.live`) runs `live_loop --once`
every minute while today's arm record is valid, and not otherwise. The ladder buys only
with settled cash in a cash account. The Config page cannot change `live.enabled`, `live.dry_run`, the
attestation, the arm or any limit. [CLAUDE.md](CLAUDE.md) has the gates, limits, tick order and ledger.

## Commands

```bash
python -m cherrypick.ladder.paper_loop --once          # one tick
python -m cherrypick.ladder.paper_loop --interval 60   # the resident loop, until session_end
python -m cherrypick.ladder.paper_loop --status        # one JSON health object
python run.py status | lots [--arm A] [--open|--closed] | summary [--arm A] | equity [--arm A]
python run.py shadow [--arm A] [--since YYYY-MM-DD]     # each armed day's live dry-run buys against paper's arm
python -m cherrypick.ladder.live_loop --status         # the live gates, the arm, dry-run or live
python -m cherrypick.ladder.live_loop --once           # one live tick; sends nothing while live.dry_run is true
python -m pytest
```
