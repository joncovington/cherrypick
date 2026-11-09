# cherrypick-flies

**Retired 2026-10-19, off by default.** The module ships switched off and stays in the tree so it can be
reinstated; the supervisor schedules none of its jobs while `modules.flies.enabled` is false.

> **Experimental, educational software, not financial advice.** cherrypick is a prototype for learning
> about and researching options strategies. Its live-trading paths place **real, irreversible orders** at
> your own risk; options trading involves substantial risk of loss and is not suitable for all investors.
> Paper results are simulated and do not represent actual trading. Provided "as is", without warranty. This
> module has a live-trading path (armed per day by `/live-flies-start`, off by default) that places real
> orders through your broker account. **Read [DISCLAIMER.md](../../DISCLAIMER.md) before use.**

## What it did

Flies traded 0DTE net-credit butterflies on same-day-expiring SPX options, nicknamed the "profit forest".
It ran as **paper trading** (simulated, no real money) with a small, tightly bounded **live pilot** behind
its own gate and a per-day arm. It is one module in the cherrypick suite; see the
[suite README](../../README.md) for how it fits beside the others.

A long butterfly's payoff at expiration is between $0 and its maximum width and can never go negative. If
you can get into one **for a net credit** (paid more up front than the position could ever cost back), the
worst case at expiration is still a profit. Several at different strikes put green zones across a band of
strikes, with a peak at each one: a forest of profit zones on a floor above zero.

No one sells a butterfly for a net credit directly, since that would be free money. So the module builds the
credit itself, and, more importantly, **measures whether either way survives real-world trading costs**
(commissions, fees and bid/ask slippage). That measurement is what the module is for.

**Legged entry** sells a defined-risk credit spread, then buys the spread that completes it into a full
butterfly for a smaller debit. What is left is a butterfly held for the difference, and that credit is the
position's guaranteed floor. The completing leg gets cheaper as the market moves one way (a put-side fly
benefits from a **rising** market, a call-side fly from a **falling** one). If the completing leg never gets
cheap enough, you are left holding the original credit spread, an ordinary defined-risk trade, and that
outcome is tracked separately, because it is the common one, not an edge case.

**Outright entry** buys an already-cheap butterfly, funded by premium the day's trading already collected.
It spends part of an existing floor rather than creating one, so its safety holds only at the level of the
whole day's book. It is off: the shipped `entry_modes` is `["legged"]`.

**Arms.** The module can run several parallel copies of the strategy, each changing exactly one variable
from the `control` baseline, each with a completely separate ledger, so results compare cleanly. The shipped
example holds only `control`, switched off; every other arm is added in a machine's own config
(`~/.cherrypick/config/flies.json`). What each arm tests is in [docs/reference.md](docs/reference.md#the-arms).

Every reported number is net of realistic commissions and fees plus a modelled slippage haircut (the cost
model MEIC and earnings use). A legged butterfly pays two full fee stacks against a credit that is often
only $35 to $105, so costs are a central part of what is measured, including the $5-per-ITM-strike
settlement fee, which is also why only SPX/XSP are traded ([docs/faq.md](docs/faq.md)).

**Completion rate is the number to watch above all others.** If legged entries rarely complete, the
strategy is selling credit spreads with extra steps and full defined-risk exposure, not a bounded floor.

## What history found

Across the paper sessions the module ran, completion rate against its break-even decided the result;
stranded verticals, not costs, were the dominant loss. Every alternative way of choosing the centre,
the width or the entry window measured flat to negative against plain at-the-money `control`, the iron
completion is the same trade as the debit completion by put-call parity, and the broken-wing and OTM
debit-first constructions lost to fees or to the roll. The intraday agent's replay found that neither the
fixed trend gate nor the agent's version beat taking every entry. The reasoning behind each rule is in
[CLAUDE.md](CLAUDE.md) and [docs/reference.md](docs/reference.md); the module's own rule was tested on
real quotes as research items 21 (a resting entry limit, with and without the range-high refusal) and 40
(no strand tail: a fixed stop on an uncompleted vertical, or the whole fly bought at once). Both failed
after costs. See the register, [../../research/edge-hypotheses-plan.md](../../research/edge-hypotheses-plan.md).

## How to reinstate it

1. In the suite config set `modules.flies.enabled` to true, and in `~/.cherrypick/config/flies.json` set
   `enabled` to true on the `control` arm (copy `config.example.json` first; with no config of its own,
   flies runs from the example). The supervisor then derives the flies jobs from the module switch.
2. The extras `flies_payoff_post.enabled`, `flies_payoff_post.intraday.enabled` and
   `intraday_agent.enabled` are separate and stay off until chosen.
3. The suite's shared streamer must be running; flies reads its stream cache and declares its own symbols.
4. Live trading needs more (see "Live trading" below): its own config gate, a human attestation, a
   designated account and the per-day arm.

## Quick start

```bash
pip install -e ../core                       # the shared cherrypick.core library, install first
pip install -e ".[dev]"
python -m pytest                             # confirm everything checks out

# paper trading (simulated), requires the suite's shared streamer to be running
python -m cherrypick.flies.paper_loop --once             # run one pass across every arm
python -m cherrypick.flies.paper_loop --interval 15      # the resident loop; the supervisor already runs it, and a different interval journals a cadence break
python -m cherrypick.flies.paper_loop --settle           # book the day's results after the closing bell
python -m cherrypick.flies.paper_loop --status           # what's open, what's settled, right now

# or feed it a saved snapshot file instead of a live data feed
python run.py once --snapshot snapshot.json

# monitoring and review
python run.py regime                         # results grouped by the market regime each trade entered into
python run.py regime-cuts --write            # the same cut, per arm, written nightly for the console and advisor
python -m cherrypick.review build --session <date>        # the suite review (all modules, one place)
```

The paper-trading database is `~/.cherrypick/data/flies/paper_trades.db` (override with `FLIES_DB_PATH`).
The read surface is the console's flies page (`http://127.0.0.1:5070/flies`, which `/console` opens), with
tabs for session, the profit forest, timeline, positions, history, decisions, completion, performance,
regime cuts, intraday agent and more. It is read-only and loopback-only.

`regime` reports coverage first (how much of the book carries each tag, and whether a tag ever took more
than one value), because a table split on a tag that never varied looks like a result and is not. Pass
`--dimension gex|vol|skew|time|center_offset|trend|drift_alignment|event` for one dimension, or
`--bucket-edges 0.4,0.6` to re-cut the recorded measurement at different thresholds. `regime-cuts` builds
the same cut per arm, era-scoped to what is comparable, and writes `data/flies/regime_cuts-<session>.json`
plus a `regime_cuts.json` latest copy, which the console's "regime cuts" tab and the advisor's evening read
consume; the supervisor runs it nightly, `--session YYYY-MM-DD` re-cuts a past day and `--backfill --since
YYYY-MM-DD` fills a range.

## Where its prices come from

The module does not run its own market-data connection. It reads quotes from the shared local cache
(`stream_cache.db`) that the suite's streamer maintains, the same way the GEX console does. If a quote is
stale (older than 120 seconds by default), crossed (bid above ask) or the underlying's price is missing,
the module refuses to trade on it rather than guess, and records data-quality notes on every check, so a
quiet day can be told apart from a day the feed was thin or broken.

## What it reports

- **Per position:** the post-fee floor in dollars, and whether the position is genuinely risk-free (a
  positive credit before fees does not automatically mean a safe position after them).
- **Per day's book:** credit collected, debits paid, fees, and the price range over which the floor holds
  (flagged when it does not hold everywhere).
- **Per session:** completion rate, risk-free rate, and "pin rate" (how often price settled near a chosen
  centre).

## Live trading

Paper trading is simulated. **A small, tightly bounded live pilot exists** on top of it: experimental, off
by default, used at your own risk. Real orders, real money, one arm (`live.arm`, `control`), one symbol,
one contract per order, under a local worst-case buying-power cap (`live.max_open_margin_dollars`). Nothing
trades until your own config sets `live.enabled` and fills in `live.gate0_confirmed`. It exists to surface
real trading-mechanics issues (fills, timing, cancellations) and to measure the live completion rate, not
to prove the strategy works; the statistical bar for that is in [docs/live.md](docs/live.md).

**To start a live session, use `/live-flies-start` in Claude.** It shows the current state (open positions,
pending orders, safety flags), shows the disclaimer verbatim, and requires an explicit, fresh "YES" before
arming anything. While armed, the supervisor runs the `flies-live` job, one tick a minute. A previous day's
confirmation never carries over, and live turns itself off every evening (17:00 ET by default). To stop
early, run `/live-flies-start --stop`, or set the suite-wide halt flag, which stops new trades within about
a minute without touching anything already open.

**Without Claude Code**, arm from a terminal with `python -m cherrypick.flies.live_loop --arm --confirm
YES`. It prints `DISCLAIMER.md` first and arms nothing unless the confirmation is exactly `YES`; it also
refuses while `modules.flies.enabled` is false. `--readiness` lists any unmet gate, `--status` shows the
state, and `--disarm` disarms. `--install-task` is the same arm without the prompt, for the slash command,
which has already asked; the module-off refusal applies to it too.

Settlement in paper defaults to the last streamed trade price, which approximates but is not the official
closing print. For any day that matters, re-run with `--settle --price <official price>`. The live loop
fetches the official print itself and, if no source has posted one, settles provisionally on the last trade
and keeps retrying.

## Docs

- [CLAUDE.md](CLAUDE.md): the strategy, the honesty rules, the guardrails.
- [docs/reference.md](docs/reference.md): layout, read side, data source, arms, pacing rules.
- [docs/live.md](docs/live.md): the live path and its gates.
- [docs/intraday-agent.md](docs/intraday-agent.md): the optional model-assisted gate.
- [docs/faq.md](docs/faq.md): the strategy question by question, and why only SPX/XSP.

## Tests

```bash
python -m pytest        # the full test suite
python -m ruff check .  # style/lint check
```

`tests/test_books.py` replays three real historical order sequences from an actual brokerage account. They
are the most valuable tests in the suite: they check the accounting against something that actually
happened, not against a model this same module also wrote.
