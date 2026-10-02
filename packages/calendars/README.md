# cherrypick-calendars

> **EXPERIMENTAL, and off by default.** This module ships `enabled: false` in the orchestrator's
> `config.example.json`, so a fresh install does not run it. To turn it on, use the **Modules &
> symbols** section of the console's Config page, or set `modules.calendars.enabled` to `true` in
> `~/.cherrypick/config.json`. It is paper-only either way, and a base install runs only the
> `control` book (see below).

Weekly SPY double calendars (SPX until 2026-08-15), paper-only, built as a **forward
exit-parameter experiment**.

The trade: every Monday (Tuesday after a Monday holiday) from 10:00 ET, buy one put calendar at the
expected-move-down strike and one call calendar at the expected-move-up strike — short legs expiring
that week's Friday (4DTE), long legs the following Monday (7DTE). The expected move comes from the
front ATM straddle (`cherrypick.core.structures`); the strike from the **intersection** of both
chains' strikes, because different expirations list different increments. Holiday weeks produce
tagged variants (`dc_3_6`, `dc_4_8`) that are never pooled with the ordinary `dc_4_7`. SPY settles
physically, and that is modelled; a week containing a declared SPY ex-dividend date is skipped.

The question: **which exit rule makes this worth anything, net of costs?** Nobody has tested the
exits, so the module doesn't guess — it measures:

- a `control` book closes every leg in Friday's 15:45–15:55 exit window (no stops, no targets, no
  weekend hold);
- a `path` book holds every leg to its expiry and records a per-tick mark path all week. It is
  **off on a base install** (`books.path.enabled: false` in the example config); switch it on in
  `~/.cherrypick/config/calendars.json`, because the replay below needs its recorded path;
- `exit_policies.py` replays profit targets, stops, short-strike touch, exit-timing variants and
  both long dispositions over that recorded path — exactly paired, priced through the same fee and
  slippage stack, and validated to the cent against the control book's real results on every run;
- optional `advised:<experiment name>` books (off unless the advisor and this module's `advice`
  block are both switched on) run the AI advisor's admitted exit params (frozen per
  row at entry) as real books beside the control — one book per concurrent advisor experiment
  since 2026-09-17 (`advised:control` before).

Paper-only and credential-free: a pure read-only consumer of the suite's shared stream cache, whose
4DTE/7DTE chains are served via the streamer's `expirations` request field. There is no live path.

## Quick start

```bash
pip install -e ../core && pip install -e .
python -m cherrypick.calendars.paper_loop --status   # health JSON
python run.py status                                 # open positions + the week plan
python run.py policies                               # the derived exit-policy table
```

The suite runs it unattended: the orchestrator's supervisor drives
`python -m cherrypick.calendars.paper_loop` as a 30-second resident loop in session plus a
once-a-minute off-session tick (see `modules.calendars` in the orchestrator's
`config.example.json`). Operating contract and design rationale: [CLAUDE.md](CLAUDE.md); every
config key is documented inline in [config.example.json](config.example.json).
