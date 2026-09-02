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
pip install -e ../core                       # the shared cherrypick.core library, install first
pip install -e ".[dev]"
cp config.example.json config.json
python -m pytest                             # confirm everything checks out

# paper trading (simulated) — requires the suite's shared streamer to be running
python -m cherrypick.flies.paper_loop --once             # run one pass across every strategy variant
python -m cherrypick.flies.paper_loop --interval 300     # keep running until the market close
python -m cherrypick.flies.paper_loop --settle           # book the day's results after the closing bell
python -m cherrypick.flies.paper_loop --status           # what's open, what's settled, right now

# or feed it a saved snapshot file instead of a live data feed
python run.py once --snapshot snapshot.json

# monitoring and review
# The read surface is the console: http://127.0.0.1:5070/flies -- Today / History / Performance,
# the profit forest, the session timeline and the decision journal. This module's own dashboard and
# its suite-dashboard card were retired 2026-08-12; every read still goes through analytics.py.
python run.py regime                         # results grouped by the market regime each trade entered into
python -m cherrypick.review build --session <date>        # the suite review (all modules, one place)
```

`regime` reports coverage first — how much of the book carries each tag, and whether a tag ever took
more than one value — because a table split on a tag that never varied looks like a result and isn't.
Pass `--dimension gex|vol|skew|time` for one dimension, or `--bucket-edges 0.4,0.6` to re-cut the
recorded measurement at different thresholds without re-running any sessions.

The paper-trading database lives at `~/.cherrypick/data/flies/paper_trades.db` (override with
the `FLIES_DB_PATH` environment variable if you need a different location).

**Live (real-money) trading** is a separate, much more guarded path — see
"[Live trading](#live-trading)" below. It is normally started with the `/live-flies-start` command
in Claude rather than by hand.

## Where its prices come from

This module doesn't run its own market-data connection. Instead it reads real-time-ish quotes
from a shared local cache (`stream_cache.db`) that one dedicated data process (the suite's
"streamer") maintains for every module in the suite — flies just reads it, the same way the GEX
console does. That data process has to be running and subscribed to the symbols you've
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

## The strategy variants ("arms")

The module runs several parallel copies of the strategy side by side, each changing exactly one
variable from the `control` baseline, so results from one variant can be compared cleanly rather
than a mix of confounded changes. `gex` picks its centre by dealer gamma positioning, `time_window`
by trading only inside specific windows, and `width-2`..`width-5`/`width-10` sweep the wing width
(in strike increments, so the sweep means the same thing regardless of the traded symbol's own
strike spacing) — those
change WHERE or WHEN a position is centred. `debit-first`, `iron`, and `bwb` instead change HOW the
net credit is manufactured in the first place (buying the debit leg first, completing with an iron
butterfly, or entering a broken-wing butterfly whole and rolling it in) — see
[`CLAUDE.md`](CLAUDE.md)'s "The arms" section for the current, complete list and what each one is
actually testing; it's the canonical source so this file doesn't drift out of sync with it.

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

## Monitoring

The console's flies page, `http://127.0.0.1:5070/flies` (`/console` opens it). Today tiles, the
profit forest, the session timeline, the decision journal, positions with their post-fee floors, arm
divergence, history and performance. Read-only and loopback-only, and the supervisor keeps it
running. This module's own dashboard on 5052 was retired on 2026-08-12.

## What it costs

Every number reported is already net of realistic commissions/fees plus a modeled slippage
haircut (the same cost model MEIC and earnings use). A legged butterfly pays two full fee
stacks against a credit that's often only $35–105, so costs here aren't a rounding error — they
are a central part of what this module is measuring. That includes the $5-per-ITM-strike
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
