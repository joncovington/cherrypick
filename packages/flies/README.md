# cherrypick-flies

Paper module for 0DTE net-credit butterflies on SPX — the "profit forest". Part of the
[cherrypick suite](../../README.md).

A long symmetric butterfly pays `max(0, W - |S - K|)` at expiry: bounded to `[0, W]`, never negative.
Hold one for a **net credit** and its worst case at expiry is a profit. Hold several at different
strikes and the risk graph is green across a band with a peak at each centre — a forest of profit zones
standing on a positive floor.

The catch is that you cannot buy such a fly; the credit has to be manufactured. This module implements
both ways it is actually done, and measures whether either survives costs.

## Quick start

```bash
git submodule update --init --recursive     # src/_core (cherrypick-core)
cp config.example.json config.json
python -m pytest

# live session (requires MEIC's streamer to be running)
python src/paper_loop.py --once             # one iteration across every arm
python src/paper_loop.py --interval 300     # run until the close
python src/paper_loop.py --settle           # cash-settle after the bell
python src/paper_loop.py --status

# or drive it from a snapshot file, no streamer needed
python run.py once --snapshot snapshot.json

# monitoring and review
python src/dashboard.py --port 8803 --open   # Today / History / Performance
python run.py section --json                 # the compact card for the suite dashboard
python src/paper_loop.py --eod-reports       # rewrite the day's EOD markdown
```

The paper database lives at `~/.cherrypick/data/flies/paper_trades.db` (override with `FLIES_DB_PATH`).

## Data source

No streamer of its own — `src/provider.py` reads MEIC's `stream_cache.db` **read-only**, the same
piggyback path `cherrypick-gex` uses. MEIC's streamer must be running and subscribed to the symbols
you configure. Stale quotes (>120s by default), crossed quotes, and missing spot are refused rather
than traded on, and every snapshot records `quote_stats` so a thin-data day is distinguishable from a
no-signal day.

## Entry modes

**`legged`** — sell a credit spread for `C`, then buy the completing spread for `D < C - fee_buffer`.
You are left holding a butterfly for `C - D` of net credit, and that credit is the position's floor.
The direction inverts by side: a put fly's completing spread cheapens as spot **rises**, a call fly's
as it **falls**. When the completion never comes, the credit spread simply settles as an ordinary
defined-risk vertical — a branch reported separately, because it is expected to be the common one.

**`outright`** — buy a cheap fly (`max_fly_debit`, 0.50 by default) using premium the book already took
in. This spends a floor rather than creating one, so its safety is book-level and bounded by the
funding spreads' wings.

## Arms

| arm | centres on | purpose |
|---|---|---|
| `gex` | max positive per-strike net GEX near spot | the pin hypothesis |
| `time_window` | ATM, inside configured windows | which time of day works |
| `control` | ATM, one fixed midday entry | the baseline that makes the other two falsifiable |

Each arm keeps its own book. Nothing is shared, so one lucky structure cannot paper over an arm that
does not work.

## What it reports

- **Per position** — `floor_dollars` after fees, and whether the position is genuinely risk-free.
- **Per book** — credit collected, debits paid, fees, and the **price band** over which the floor
  holds, plus `unbounded_below` when it does not hold everywhere.
- **Per session** — `completion_rate`, `risk_free_rate`, `pin_rate`.

`completion_rate` is the number to watch. If legged entries rarely complete, this strategy is short
verticals wearing a costume.

## Monitoring

The dashboard (loopback only, read-only, no CDN) has three views:

- **Today** — the payoff curve, which *is* the profit forest: green across the band where the book
  profits, red outside it, a dashed line at each fly's centre. Plus open positions with their post-fee
  floors, the book floor with the band it holds over, and the decision journal.
- **History** — filterable trade log, per-arm and per-entry-mode comparison, entry-window breakdown,
  fee drag, daily P&L heatmap.
- **Performance** — P&L over daily/weekly/monthly, completion rate and latency, arm divergence.

**The decision journal** answers "why didn't we trade today?" Repeated refusals collapse into counted
runs, so a quiet session reads as a handful of rows that explain themselves. It also separates *the
market gave us nothing* (`credit_below_floor`) from *we had no data* (`missing_leg_quotes`) — which look
the same in a P&L of zero and mean completely different things.

At the close the module writes `paper-eod-<day>.md` and `eod-analysis-<day>.md` into
`~/.cherrypick/logs/flies/`, which the suite digest and EOD insight pick up by filename convention.
Both lead with completion rate and the floor after fees rather than P&L — over a handful of 0DTE
sessions the P&L is mostly noise, and leading with it would invite the wrong conclusion in either
direction.

## Costs

Every figure is net of the shared `cherrypick.core.fees` schedule plus a 0.125-of-spread slippage
haircut — the same fill model as MEIC and earnings. A legged fly pays two fee stacks against a credit
that may only be $35–105, so costs are not a rounding error here; they are the experiment.

## Status

Engine, accounting, database, snapshot provider, session driver, and the orchestrator `fly_book`
wiring are complete and tested. **It has never run a live session** — every test uses a seeded cache —
so the orchestrator entry ships disabled. Enable it deliberately and watch the first day.

Settlement defaults to the last streamed trade, which approximates the official print. Pass `--price`
for a book whose result matters.

## Tests

```bash
python -m pytest        # 151 tests
python -m ruff check .
```

`tests/test_books.py` replays three real tastytrade order chains. Those are the most valuable tests
here: they check the accounting against something that actually happened rather than against a model
we also wrote.
