# EarningsAgent

EarningsAgent is a trading bot that specializes in one thing: options trades around
company earnings announcements. Earnings reports are famous for causing implied volatility
to spike beforehand and then collapse right after the news is out — this project is built to
find good candidates for that pattern, pick the right options strategy for each one, place
the trade, watch it, and close it out, night after night, without a human clicking anything.

It knows six different strategies, ranging from simple defined-risk spreads to a couple of
higher-risk, no-safety-net trades that are only allowed to run in paper (simulated) mode
unless you deliberately turn them loose. Every night, it looks at the whole list of
companies reporting earnings, screens each one against all six strategies, and picks
whichever single strategy looks best for each stock — rather than committing to one
strategy ahead of time and hoping it fits.

Everything below explains how it actually works, in plain terms, followed by the setup
steps if you want to run it yourself.

## The six strategies

- **Iron fly** — sell an at-the-money straddle, buy wings to cap the risk. The original
  strategy this project was built around.
- **Iron condor** — same shape as the iron fly, but the strikes you sell are further out
  (at the edge of the expected move) instead of dead-center.
- **Expected-move butterfly** — a debit trade: buy one at-the-money option, sell two further
  out, buy one more beyond that. Which side (calls or puts) it uses depends on which side's
  options look richer.
- **Double calendar** — sells a near-term option and buys a longer-dated one at the same
  strike, on both the call and put side. Profits when the near-term option's value decays
  faster than the long-term one's.
- **Short strangle** — sell an out-of-the-money call and put with nothing protecting you.
  Real, uncapped risk — this one only trades automatically in paper mode by default.
- **Jade lizard** — sell a put and a call spread together, sized so there's technically no
  risk if the stock rallies. The downside on the put is still open-ended, though, so it gets
  the same paper-mode-only treatment as the short strangle.

## How a night actually goes

**1. It finds tonight's (and tomorrow morning's) earnings reports.** Every time it wakes up
during the trading window, it re-checks the earnings calendar fresh — companies reporting
after today's close, plus companies reporting before tomorrow's open. It never trades on a
stale list.

**2. It screens every stock against all six strategies.** For each company reporting, it
pulls the numbers that matter (trading volume, how rich the options are relative to how much
the stock actually tends to move, and a track record of past earnings reactions), then checks
each of the six strategies against that stock, one by one. Each strategy gets a tier — good
candidate, borderline, or reject — and a score. Whichever single strategy scores best for that
stock is the one that gets used; the same stock is never traded with two strategies at once,
since one earnings surprise would hit both trades identically instead of spreading the risk
around.

**3. It ranks the whole night's opportunities against each other**, not just within one
stock, and picks the best handful — capped by how many positions it's allowed to hold at
once and by rules against piling into correlated names on the same night.

**4. Before it actually places a trade, it double-checks everything.** Prices move between
the afternoon scan and the moment it's ready to enter, so it re-verifies the trade still
makes sense with fresh numbers, checks the position won't risk too much of the account, and
only then builds and places the order. The two riskier strategies get an extra hard stop
here too — they simply won't fire in a live account unless you've explicitly said it's okay.

**5. Once a trade is on, it watches for a reason to close it early.** Right after the market
reopens the next morning, it checks whether the trade has already hit a profit target or a
stop-loss, and closes it right then if so. Most of these positions are meant to be held
overnight, not managed all day — the double calendar is the one exception, since it runs for
weeks rather than one night, so it gets checked throughout the trading day instead.

**6. Whatever's still open gets closed no matter what**, once the morning "close window"
arrives — win, lose, or draw. The idea is that the edge in these trades comes from the
overnight move itself; there's nothing to gain by holding longer once the market has settled.

**7. Every decision gets written down.** Not just the trades it made — every stock it looked
at, every strategy it considered for that stock, and the reason each one passed or failed.
That way a quiet night (nothing worth trading) and a broken screening rule (something's wrong)
never look the same in hindsight.

## Paper trading vs. real money

This whole thing runs in a fully simulated "paper trading" mode by default — it never touches
your real account, never places a real order, and keeps a completely separate results ledger
from live trading. Flipping one setting (`enable_live_trading` in the config) switches it over
to placing real trades through your tastytrade account. The two modes share the exact same
decision-making logic; nothing about how it picks trades changes based on which one you're in,
so paper results are a genuine preview of what live trading would do, not a toy version.

## Getting it running yourself

You'll need a tastytrade account (for live quotes and, if you want it, real order
placement) and a local clone of some free earnings/options datasets from DoltHub.

```bash
cp config.example.json config.json   # then edit config.json to taste
python src/db.py init_db
pip install mysql-connector-python tastytrade keyring
python src/tt.py secrets_set   # stores your tastytrade credentials in your OS's secure keyring

# Earnings calendar, IV/RV, and historical winrate data — free, no API key needed.
mkdir dolt-data && cd dolt-data
dolt clone post-no-preference/earnings
dolt clone post-no-preference/options
dolt clone post-no-preference/stocks
dolt sql-server --data-dir .   # leave this running in its own terminal window
```

Once that's done, `CLAUDE.md` has the full step-by-step operating logic the agent follows
every time it runs.

### Starting it for the day

Inside a Claude Code session opened in this project, just run:

```
/run-today
```

That's it — it reads `CLAUDE.md`, starts the loop from wherever the market is right now, and
keeps itself running through the rest of today's session (checking on positions, watching for
entry windows, handling the next close window) without you needing to restart it partway
through the day. It won't start a second one if a loop is already running against the same
trade database — if you're not sure whether one's already going, check for a live process
holding `.claude/scheduled_tasks.lock` before starting another.

## What's in here

```
EarningsAgent/
├── CLAUDE.md                # The agent's full playbook — read every time it runs
├── config.example.json      # Copy this to config.json and fill in your own settings
├── src/
│   ├── scanner.py           # Shared brains: calendar lookup, volume/IV checks, ranking
│   ├── rank_strategies.py   # Screens every stock against all six strategies, picks the best fit
│   ├── strategies/          # One file per strategy — its own rules and order-building logic
│   │   ├── iron_fly.py
│   │   ├── iron_condor.py
│   │   ├── expected_move_butterfly.py
│   │   ├── double_calendar.py
│   │   ├── short_strangle.py
│   │   └── jade_lizard.py
│   ├── tt.py                # Talks to tastytrade — quotes, option chains, placing orders
│   ├── session.py           # Keeps the tastytrade login session cached
│   ├── credentials.py       # Reads/writes your credentials from the OS keyring — never plaintext
│   ├── db.py                # Keeps a record of real trades
│   └── db_paper.py          # Keeps a completely separate record of paper trades
├── docs/
│   ├── screening-criteria.md  # The exact thresholds each strategy screens on, and why
│   └── paper-trading.md       # How paper mode is kept honest and separate from real trading
├── dolt-data/                # Your local earnings/options data clones (not checked in)
├── data/                    # Trade history databases, created the first time you run it
└── logs/                    # Run logs
```

## License

MIT — see [`LICENSE`](LICENSE).

## Disclaimer

This software is provided for **educational and informational purposes only**. It is not
financial advice. Options trading involves substantial risk of loss. You are solely
responsible for all trading decisions and any resulting gains or losses.
