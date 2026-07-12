# cherrypick — User Guide

A friendly walkthrough for options traders. If you can follow a strategy checklist and copy-paste a few
commands, you can run cherrypick.

---

## What cherrypick is (in one minute)

cherrypick lets you **test options strategies on paper, automatically.** You pick the strategies and
symbols, turn it on, and walk away. On a schedule during market hours it runs the strategies in
simulation, records every would-be trade (with realistic fills and commissions), and keeps an eye on
itself — pinging you if anything stops working. Later you open a report or dashboard to see how your
strategies would have performed.

It comes with two strategy engines:

- **MEIC** — 0DTE **multiple-entry iron condors** on cash/ETF index products (SPX, XSP, QQQ, IWM, and
  similar). It scales strike selection to volatility (VIX bands), respects credit floors and OTM
  buffers, manages per-side stops, and force-closes or lets positions settle as appropriate.
- **Earnings** — **defined-risk earnings plays** (iron fly, double calendar, iron condor, ATM calendar,
  directional credit spread, broken-wing butterfly, reverse fly). It opens once before the close and
  closes once after the next open, sized to a simulated capital base.

Everything is **paper** — cherrypick never places, cancels, or closes a real order on its own.

---

## What you need

- A **[tastytrade](https://tastytrade.com) account** (the data and pricing come from tastytrade).
- A **computer that stays on** during the market sessions you want to capture. **Windows is
  recommended** (it uses Windows Task Scheduler to run on a schedule; a Linux/Mac option exists but is
  less battle-tested).
- **Python 3.11 or newer** and **git** installed.
- For the **Earnings** engine only: a local install of **[Dolt](https://github.com/dolthub/dolt)** (a
  free database it uses for historical earnings/options data). MEIC doesn't need it.

---

## Installing it

```bash
# Download the project (the --recurse-submodules part pulls a shared library it needs)
git clone --recurse-submodules https://github.com/joncovington/cherrypick.git
cd cherrypick

# Install the orchestrator
cd packages/umbrella
pip install -e ".[dev]"
```

The two strategy engines live under `packages/meic` and `packages/earnings`. You can install each the
same way if you plan to run both (`pip install -e ".[dev]"` in `packages/meic`; the earnings engine uses
`pip install -r requirements.txt`).

---

## First-time setup

**1. Adjust your settings.** Copy the template and open it in any text editor:

```bash
cp config.example.json config.json
```

`config.json` is where you say **which strategies to run, on what schedule, and how you want to be
alerted**. It's kept only on your machine.

**2. Link your tastytrade account.** cherrypick stores your credentials in your operating system's
secure keyring (never in a file), and lets you pick which account it uses:

```bash
python run.py connect --module meic        # do the same for earnings if you'll run it
```

**3. Choose your strategy details.** The fine-grained trading rules — which symbols, target deltas,
credit floors, entry windows, risk profiles — live in each engine's own config:

- MEIC: `packages/meic/config.json` (e.g. the `symbols` list, `min_iv_rank`, entry/exit windows, and the
  conservative → very-aggressive **risk profiles**).
- Earnings: `packages/earnings/config/config.json` (position caps, entry/close windows, per-strategy
  tuning).

Each engine's own docs explain every setting in detail — start with the symbols and risk profile, and
leave the rest at their sensible defaults.

**4. Confirm you're ready:**

```bash
python run.py doctor
```

This prints a simple green/red checklist — Python, your settings, your broker connection, the data feed,
the databases, and (for earnings) Dolt. Green means you're good to go.

---

## Turning it on

```bash
python run.py install
```

That registers cherrypick to run on a schedule and starts the data feed. From now on it works on its
own:

- **MEIC** evaluates entries every couple of minutes during the session.
- **Earnings** opens plays before the close and closes them after the next open, on daily timers.
- A **monitor** checks in every few minutes and a **fill-notifier** pushes new paper trades to you
  quickly.

To pause everything: `python run.py uninstall`. Your recorded data and settings are untouched — you can
turn it back on any time.

---

## Reviewing your results

| Command | What you get |
|---|---|
| `python run.py report` | Win rate and P&L (net of commissions) across strategies and risk profiles. |
| `python run.py dashboard --serve` | A live dashboard in your browser: overall status, per-strategy P&L, recent activity, and health checks. |
| `python run.py dashboard` | The same as a single self-contained web page you can open or share. |
| `python run.py calibrate` | Advice on whether a risk profile has collected enough good results to consider stepping up (advisory only — it never changes anything). |

---

## Staying informed

Set your alert channels in `config.json` under `notify` — any of **`log`** (always on), **`desktop`**,
**`discord`**, and **`slack`**. You'll get:

- a **ping when a new paper trade fills**, and
- a **warning if something stalls** (e.g. the data feed goes quiet mid-session) so you're never
  surprised by a silent gap.

Test that alerts actually reach you:

```bash
python run.py notify-test
```

To set up a Discord or Slack webhook (stored securely, not in a file):

```bash
python run.py secrets-set --channel discord      # paste your webhook URL when prompted
```

---

## Everyday commands (cheat sheet)

| Command | Purpose |
|---|---|
| `python run.py doctor` | Green/red readiness check (add `--fast` to skip the broker check). |
| `python run.py status` | Shows the schedule and when things last ran / run next. |
| `python run.py report` | Paper P&L summary. |
| `python run.py dashboard --serve` | Live browser dashboard. |
| `python run.py reconcile` | Safety check: confirms your **real** brokerage account has no unexpected open positions. |
| `python run.py account --module meic` | See / choose which account a strategy would use if run live. |
| `python run.py install` / `uninstall` | Turn the schedule on / off. |
| `python run.py notify-test` | Send yourself a test alert. |

---

## How your account is protected

- **Paper only.** cherrypick runs simulation engines and reads market data. It does **not** place,
  cancel, close, or adjust real orders, and it does not flip on live trading.
- **A real-account safety check.** `reconcile` looks at your actual brokerage account(s) and flags
  anything that isn't flat — a guard against surprises. It's read-only and never trades.
- **Credentials stay secure.** Your tastytrade login is kept in your OS keyring, and account numbers are
  always masked (shown as `****1234`) anywhere they appear.
- **The safety monitor never trades.** The most it will do on its own is restart a stalled data feed —
  never anything order-related.

---

## Troubleshooting

- **Start with `python run.py doctor`.** It pinpoints most problems (broker not connected, data feed
  down, a database missing, Dolt not running for earnings).
- **"Not much is happening."** Outside market hours, or when volatility/credit gates aren't met, the
  engines correctly sit on their hands — that's normal. Check `status` to confirm the schedule is active.
- **No alerts arriving?** Run `notify-test`; if desktop/Discord don't show up, re-check the `notify`
  channels in `config.json` and (for Discord/Slack) that you stored the webhook with `secrets-set`.
- **Laptop keeps sleeping.** There's a helper, `tools/setup-walkaway-durability.ps1`, to keep a Windows
  machine awake and running scheduled tasks while you're away.

---

## Disclaimer

For **educational and research purposes only** — **not financial or trading advice.** Options trading
involves substantial risk of loss; paper results do not reflect real-world performance. See the
[Disclaimer in the README](../README.md#disclaimer) and the [LICENSE](../LICENSE) for the full terms.
