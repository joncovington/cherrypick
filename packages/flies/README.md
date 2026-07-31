# cherrypick-flies

**What this module does:** flies trades a strategy called a 0DTE net-credit butterfly on
same-day-expiring SPX/XSP options — nicknamed the "profit forest." It runs as **paper trading**
(simulated, no real money) by default, with a small, tightly-controlled **live pilot** now
authorized for real-money testing. It's one module in the cherrypick suite — see the
[suite README](../../README.md) for how it fits alongside the other strategies (MEIC, earnings,
GEX). You don't need to touch any code to use it: everything below is run from a terminal
command line, and a few steps can be done by asking Claude directly (noted where relevant).

## The idea in plain terms

A long butterfly spread has a very simple payoff at expiration: it's worth somewhere between
$0 and its maximum width, and it can never go negative. If you can get into one **for a net
credit** — meaning you're paid more up front than the position could ever cost you back — then
the worst case at expiration is still a profit. Put several of these on at different strike
prices in a session and, on a profit-and-loss chart, you get green (profitable) zones across a
band of strikes with a peak at each one — a "forest" of profit zones sitting on a floor above
zero.

The catch: no one will sell you a butterfly for a net credit directly — that would be free
money, and free money doesn't trade. So this module builds the credit itself, in one of two
ways, and — more importantly — **measures whether either way survives real-world trading costs**
(commissions, fees, and the slippage from bid/ask spreads). That measurement, not the trade
idea itself, is what this module is really for.

## Quick start

```bash
git submodule update --init --recursive     # pulls in the shared cherrypick-core library
cp config.example.json config.json
python -m pytest                             # confirm everything checks out

# paper trading (simulated) — requires the suite's shared streamer to be running
python src/paper_loop.py --once             # run one pass across every strategy variant
python src/paper_loop.py --interval 300     # keep running until the market close
python src/paper_loop.py --settle           # book the day's results after the closing bell
python src/paper_loop.py --status           # what's open, what's settled, right now

# or feed it a saved snapshot file instead of a live data feed
python run.py once --snapshot snapshot.json

# monitoring and review
python src/dashboard.py --port 8803 --open   # opens a browser dashboard: Today / History / Performance
python run.py section --json                 # the compact summary card shown on the suite-wide dashboard
python src/paper_loop.py --eod-reports       # regenerates the day's end-of-day report
```

The paper-trading database lives at `~/.cherrypick/data/flies/paper_trades.db` (override with
the `FLIES_DB_PATH` environment variable if you need a different location).

**Live (real-money) trading** is a separate, much more guarded path — see
"[Live trading](#live-trading)" below. It is normally started with the `/live-flies-start` command
in Claude rather than by hand.

## Where its prices come from

This module doesn't run its own market-data connection. Instead it reads real-time-ish quotes
from a shared local cache (`stream_cache.db`) that one dedicated data process (the suite's
"streamer") maintains for every module in the suite — flies just reads it, the same way the GEX
dashboard does. That data process has to be running and subscribed to the symbols you've
configured, or flies has nothing to look at.

If a quote is stale (older than 120 seconds by default), crossed (bid above ask — a bad quote),
or the underlying's price is simply missing, this module refuses to trade on it rather than
guess. It also records data-quality notes on every check, so a quiet trading day can be told
apart from a day where the data feed itself was thin or broken.

## The two ways it builds a credit

**Legged entry** — sell a defined-risk credit spread for a credit, then buy the spread that
completes it into a full butterfly for a smaller debit. What's left is a butterfly held for the
difference — the net credit — and that credit is the position's guaranteed floor. The
completing leg gets cheaper as the market moves in a particular direction depending on which
side of the trade you're on (a put-side fly benefits from a **rising** market; a call-side fly
from a **falling** one). If the completing leg never gets cheap enough to buy, you're simply
left holding the original credit spread — an ordinary, fully-defined-risk trade — and that
outcome is tracked and reported separately, because it's expected to be the common one, not an
edge case.

**Outright entry** — buy a already-cheap butterfly outright (capped at a small debit, 50 cents by
default), funded by premium the day's trading has already collected. This doesn't create a new
floor of its own; it spends part of an existing one, so its safety is judged at the level of the
whole day's book, not the individual position, and only holds up within the price range the
funding trades cover. *(As of the 2026-07-27 configuration update, this mode is switched off —
see "Status" below.)*

## The four strategy variants ("arms")

The module runs several parallel copies of the strategy side by side, each changing exactly one
variable, so results from one variant can be compared cleanly against a baseline rather than a
mix of confounded changes:

| Variant | Picks its center strike by... | What it's testing |
|---|---|---|
| `gex` | the strike with the strongest dealer positioning (net gamma exposure) near the current price | whether dealer hedging really does "pin" price near that strike |
| `time_window` | the at-the-money strike, but only trades inside specific windows of the day | whether time-of-day matters |
| `control` | the at-the-money strike, all day | the plain baseline every other variant is measured against |

Each variant keeps a completely separate ledger, so one lucky trade in one variant can't make
another variant look better than it is.

## What it reports

- **Per position** — the post-fee floor in dollars, and whether that position is genuinely
  risk-free (a positive credit before fees does not automatically mean a safe position after
  them).
- **Per day's book** — total credit collected, debits paid, fees, and the price range over which
  the floor actually holds (flagged when it doesn't hold everywhere).
- **Per session** — completion rate (how often a legged entry actually became a full butterfly),
  risk-free rate, and "pin rate" (how often price settled near a chosen center).

**Completion rate is the number to watch above all others.** If legged entries rarely complete
into a full butterfly, this strategy is really just selling credit spreads with extra steps —
and full defined-risk exposure, not a bounded floor.

## Monitoring dashboard

Start it with `python src/dashboard.py --port 8803 --open`, or ask Claude to run
`/serve-dashboard --flies`. It's read-only and only reachable from your own computer (no
outside network access), and has three views:

- **Today** — the day's profit-and-loss curve across strike prices (green where the book
  profits, red where it doesn't, with a dashed line marking each position's center), the open
  positions and their floors, the book-wide floor and the price band it holds over, and a
  running "decision journal."
- **History** — a filterable trade log, comparisons across strategy variants and entry types, a
  breakdown by time of day, the fee drag on results, and a daily profit/loss calendar.
- **Performance** — profit and loss over time (daily/weekly/monthly), completion rate and how
  long completions take, and how often the strategy variants disagree with each other.

**The decision journal** answers "why didn't we trade today?" Repeated identical reasons
collapse into a single counted line, so a quiet day still reads as a short, explainable list
rather than a wall of repetition — and it separately tells apart *the market simply didn't offer
a good enough price* from *we had no usable data to work with*, which look identical in the P&L
but call for very different responses.

At the market close the module writes two files into `~/.cherrypick/logs/flies/`: a plain
end-of-day report and a deeper analysis file. Both lead with the completion rate and the
post-fee floor rather than the day's raw profit or loss — over a handful of same-day-expiry
sessions, raw P&L is mostly noise, and leading with it invites the wrong conclusion either way.

## What it costs

Every number reported is already net of realistic commissions/fees plus a modeled slippage
haircut (the same cost model MEIC and earnings use). A legged butterfly pays two full fee
stacks against a credit that's often only $35–105, so costs here aren't a rounding error — they
are a central part of what this module is measuring. That includes the $5/contract
exercise-assignment fee tastytrade charges overnight on any leg that finishes ITM — see
[docs/faq.md](docs/faq.md) for why that cost (not fee size on its own) rules out trading
SPY, or futures options on /ES or /MES, instead of SPX/XSP.

## Live trading

Paper trading (above) is simulated — no real orders, no real money. **A small, tightly-bounded
live pilot is now authorized** on top of it: real orders, real money, one specific strategy
variant (`gex`), one contract at a time, at most one open position at a time. It exists to
surface real trading-mechanics issues (order fills, timing, cancellations) before any larger
commitment, not to prove the strategy works — the statistical bar for that is defined, and not
yet cleared, in [docs/live-trading-plan.md](docs/live-trading-plan.md).

**To start a live session, use the `/live-flies-start` command in Claude.** It will show you
the current state (any open positions, pending orders, and safety-flag status) and require an
explicit, freshly-typed "YES" before arming anything — a previous day's confirmation never
carries over. Live trading also **automatically turns itself off every evening** (17:00 ET by
default); starting it again the next trading day requires running the command again. To stop a
live session early, run `/live-flies-start --stop`, or ask Claude to set the suite-wide halt
flag, which stops new trades within about a minute without touching anything already open.

Full detail on the live pilot's rules, safety gates, and rollout plan lives in
[docs/live-trading-plan.md](docs/live-trading-plan.md).

## Status

Paper trading is complete, tested, and has run real sessions. **Five sessions in (2026-07-20
through 2026-07-24), the honest headline result is:** every completed butterfly made money (an
average of about +$110, with a floor of +$52 — the guaranteed-profit design doing exactly what
it promises), but the days where a legged entry never completed lost money on average (about
-$208 each), and there were more of those misses than completions. The strategy needs roughly a
65% completion rate to break even overall; the observed rate over that window was about 57.5%.
Since then, a few settings were adjusted based on that data (see
[docs/live-trading-plan.md](docs/live-trading-plan.md) for the reasoning), and the `gex`
variant specifically has performed much better under the new settings — though still on too
small a sample to call it proven.

The live pilot described above began 2026-07-30, as a deliberate, explicitly logged exception
to that statistical bar — see [docs/live-trading-plan.md](docs/live-trading-plan.md) for exactly
why, and what has to happen before the pilot can be expanded.

Settlement (the final price used to close out a day's positions) defaults to the last streamed
trade price, which closely approximates but isn't identical to the official closing print. For
any day's result that matters, re-run settlement with the official print: `--settle --price
<official price>`.

## Tests

```bash
python -m pytest        # the full test suite
python -m ruff check .  # style/lint check
```

`tests/test_books.py` replays three real historical order sequences from an actual brokerage
account. Those are the most valuable tests in the suite: they check the accounting against
something that actually happened, not against a model this same module also wrote.
