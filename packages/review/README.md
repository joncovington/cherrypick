# cherrypick-review

The suite's cross-module **end-of-day review**. Each session it builds one versioned, deterministic
**fact set** answering what the suite did today, whether that was what was expected, and what should
change — for every module with a ledger reader, together, so modules can be compared.

- **Read-only over every other package.** It reads each module's ledger through
  `cherrypick.core.ledgers` (the one home for per-schema net, cost and session rules) and writes only
  to its own store. It never touches a module's database or an order.
- **No credentials, no network, no AI.** Every figure is computed from recorded rows. The narrative
  is written outside the package by `scripts/eod_narrative.py`, which reads the fact set and nothing
  else, so a failed narrative costs a note and never a report.
- **Arms are kept apart.** Each module's arms are a paired comparison, so the fact set reports them
  side by side and never only as a module total.

## Where it writes

`~/.cherrypick/data/review/` (override with `REVIEW_DATA_DIR`):

- `eod-<date>.json` — the fact set, and the only thing any surface reads;
- `eod-<date>.md` — the human render of those facts;
- `eod-<date>.note.md` — the narrative, beside the facts and never inside them (only when the
  narrative is switched on).

The console's Review page reads the same JSON. Logs go to `~/.cherrypick/logs/review/`.

## Commands

```bash
python -m cherrypick.review build [--session YYYY-MM-DD] [--final]   # build + render one session
python -m cherrypick.review backfill [--since YYYY-MM-DD]            # every session with activity (final)
python -m cherrypick.review render [--session YYYY-MM-DD]            # re-render one session's markdown
python -m cherrypick.review reconcile [--since YYYY-MM-DD]           # re-count totals with independent SQL
```

`build` defaults to today and writes a `provisional` set; `build --final` defaults to the prior trading
day, because earnings settles overnight and session D is finalised on D+1.

## How the suite runs it

You don't normally run it by hand. The orchestrator's supervisor has two daily jobs, on trading days
only: `review-provisional` (16:30 ET, `run.py review`) and `review-final` (10:15 ET the next morning,
`run.py review --final`, which also runs `reconcile`). Both times, and the switch, are in the `review`
block of `~/.cherrypick/config.json`. A third job, `review-narrative`, runs the narrative script; it
is off by default (`review.narrative`) and needs the `claude` capability.

Operating contract and the rules the fact set enforces: [CLAUDE.md](CLAUDE.md). Incidents behind
those rules: [docs/history.md](docs/history.md).
