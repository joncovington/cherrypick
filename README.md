# cherrypick

**Test your options strategies on autopilot — without babysitting a screen.**

cherrypick runs your trading strategies in **paper mode** on a schedule, records every simulated trade,
and keeps watch so you don't have to. Walk away, and if anything stops working it pings you (desktop,
Discord, or Slack). Come back whenever you like to see how your strategies would have done — real fills,
real costs, real market conditions, **none of your real money.**

![CI](https://img.shields.io/github/actions/workflow/status/joncovington/cherrypick/ci.yml?branch=main)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## What it does

- **Trades on paper, automatically.** Two strategy engines come built in:
  - **MEIC** — 0DTE multiple-entry iron condors on indices/ETFs (SPX, XSP, QQQ, IWM, …).
  - **Earnings** — defined-risk earnings plays (iron flies, calendars, condors, and more).
- **Records everything** to a local database so you can review win rate, P&L (net of commissions), and
  per-profile performance any time.
- **Watches itself.** A built-in monitor checks that data is flowing during market hours and restarts a
  stalled data feed on its own — and **alerts you** if something needs attention.
- **Never touches your live account.** It only ever runs the simulated (paper) engines.

## Quick Start

> **You'll need:** a [tastytrade](https://tastytrade.com) account, a computer that stays on during market
> hours (Windows recommended), Python 3.11+, and a few minutes in a terminal.

```bash
# 1. Get the project
git clone --recurse-submodules https://github.com/joncovington/cherrypick.git
cd cherrypick/packages/umbrella
pip install -e ".[dev]"

# 2. Set your preferences (symbols, strategies, alert channels)
cp config.example.json config.json     # open in any editor and adjust

# 3. Link your tastytrade account (credentials are stored securely, never in a file)
python run.py connect --module meic

# 4. Make sure everything's ready
python run.py doctor                     # a simple green/red checklist

# 5. Turn it on — it now runs on its own, on a schedule
python run.py install
```

That's it. From here it collects data hands-off. To stop it later: `python run.py uninstall`.

## Checking your results

```bash
python run.py report              # win rate + P&L across strategies and risk profiles
python run.py dashboard --serve   # a live dashboard in your browser
python run.py calibrate           # advice on when a risk profile has "earned" a step up
```

## Staying in the loop

Set your alert channels in `config.json` (`log`, `desktop`, `discord`, `slack`). You'll get a heads-up
when a new paper trade fills, and a warning if the system ever stalls — so you can walk away with
confidence. Test it any time with `python run.py notify-test`.

## Good to know

- **Paper by default, always.** cherrypick never places, cancels, or closes a live order on its own.
- **Your data stays yours.** Trades and credentials live on your machine (credentials in your operating
  system's secure keyring — never in a plain file).
- **Set-and-forget.** Once installed, it runs on a schedule and recovers from common hiccups by itself.
- **Runs on your computer**, not a cloud service — so the machine needs to stay awake during the sessions
  you want to capture. (There's a helper to keep a laptop from sleeping mid-session.)

📖 **New here?** The [User Guide](docs/PROJECT.md) walks through setup, settings, daily use, and
troubleshooting in plain language.

## Disclaimer

**For educational and research purposes only.** This software is provided as-is for learning about
market-data collection, paper-trading workflows, and automation. It is **not financial, investment, or
trading advice**, and nothing here is a recommendation to buy or sell any security.

- Trading options and other securities involves **substantial risk of loss** and is not suitable for
  everyone. Paper-trading results do not guarantee — and rarely reflect — real-world performance.
- The project **defaults to paper trading** and never places live orders on its own. If you enable or
  extend any live-trading capability, **you do so entirely at your own risk**.
- The authors and contributors accept **no liability** for any financial loss, data loss, or damages
  arising from use of this software (see the warranty disclaimer in the [LICENSE](LICENSE)).
- This project is **independent** and is not affiliated with, endorsed by, or sponsored by tastytrade,
  DoltHub, or any broker or data provider.

Do your own research and consult a licensed financial professional before making any investment decision.

## License

[MIT](LICENSE) © 2026 Jon Covington
