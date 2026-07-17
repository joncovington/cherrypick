# cherrypick

**Test many variations of an options strategy against the live market — in paper mode — to see which entry rules actually add edge.**

cherrypick runs your options strategies on a schedule against the live market in **paper mode**, recording
every simulated trade with realistic fills and costs. Its defining capability is **parallel variance
testing**: it runs many parameter variations of the same strategy at once, so you can measure which entry
rules add edge before committing real capital. It monitors its own data feed during market hours and
notifies you (desktop, Discord, or Slack) if anything stalls.

![CI](https://img.shields.io/github/actions/workflow/status/joncovington/cherrypick/ci.yml?branch=main)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## What sets it apart

Most paper-trading tools replay a single strategy. cherrypick is designed to answer questions about yours.

### Risk-profile variance testing

Define a strategy once, then create named **risk profiles** — variations that each change one parameter:
short-strike delta, credit floor, stop policy, a regime filter, entry timing, wing width, or symbol. Every
profile trades the same live market snapshots in parallel as its own shadow book, and every fill is tagged
with the profile that opened it.

Because each profile changes one lever against a shared control, you can measure the isolated effect of a
single idea rather than confounding several at once — for example:

- whether a tighter stop protects the position or exits it prematurely (a hold-to-expiry profile vs. a stopped one);
- whether further-OTM shorts justify their thinner credit on trending days (a delta sweep across 0.10 / 0.15 / 0.25);
- whether gating on dealer gamma (GEX) avoids adverse sessions;
- whether restricting entries to the afternoon changes the outcome.

Reporting separates **gross P&L** (does the entry select good setups?) from **net** (does it survive
commissions and slippage?), so an idea that looks unprofitable after costs can still reveal a genuine entry
edge. A **calibration** report indicates when a profile has met a documented threshold — enough sessions, a
sustained win rate, and a sufficient sample — to justify a step up in risk; it never changes your risk
settings automatically.

See [risk profiles](packages/meic/docs/risk-profiles.md) and [paper experiments](packages/meic/docs/paper-experiments.md) for the full method.

### A lightweight GEX (gamma-exposure) dashboard

Dealer gamma positioning is a common input for 0DTE traders. cherrypick streams the option chain and
computes a gamma-exposure profile from open interest and greeks, presenting the **call/put walls**, the
**gamma-flip point**, and an **open-interest-vs-volume** view in a lightweight browser dashboard. It is
built from data you are already streaming, and it is the same GEX signal the MEIC engine can use to gate
entries — for example, requiring positive gamma with price well inside the flip.

### Automated and self-healing

A watchdog verifies that data is flowing during market hours, restarts a stalled feed automatically, and
notifies you only when something requires attention. It runs on your machine on a schedule.

### Realistic cost modeling

Fills are modeled at mid price minus a slippage allowance, on top of the actual tastytrade
commission/exchange schedule — the same cost model across both engines — so reported "net" figures reflect
real transaction costs.

## The two strategy engines

- **MEIC** — 0DTE multiple-entry iron condors on indices/ETFs (SPX, XSP, QQQ, IWM, …), with per-side
  stops, regime gates (VIX, VIX1D, ATR, GEX), and all the risk-profile machinery above.
- **Earnings** — defined-risk earnings plays (iron flies, calendars, condors, broken-wing flies, and more),
  each sized to a fixed dollar risk.

## Paper & live modes

- **Paper (the default — and what the automation runs).** The scheduler, the self-healing, the reporting,
  and all the variance testing operate on paper: live market data in, simulated fills out, **none of your
  money**. The orchestrator **never places, cancels, or closes a live order** — by design it can't sit on a
  trading decision.
- **Live (your account, connected — but you drive it).** You link your real tastytrade account with
  `connect` so the engines use *your* live market data and can **reconcile** against your real positions (a
  read-only safety check that flags anything a paper-only suite shouldn't be holding). Trading for real is a
  **deliberate, manual** action you take per module — the automation will never do it for you, and if you
  go there you do so **entirely at your own risk** (see the disclaimer).

Credentials live in your operating system's secure keyring — never in a file — and paper and live books are
kept strictly separate.

## Requirements

| You'll need | Why |
|---|---|
| A [tastytrade](https://tastytrade.com) account | Supplies the live market data the paper engines fill against (and your real account, if you ever choose to trade live). |
| **Python 3.11+** | Runs the orchestrator, both strategy engines, and the reporting. |
| **[Claude Code](https://docs.claude.com/en/docs/claude-code)** | Anthropic's agentic CLI. It drives the interactive and live-trading sessions, the slash-command workflows (`/meic-start`, `/earnings-start`, `/eod-report`), and the agent-synthesized analysis. The unattended **paper** automation runs on its own without it — but the agent-driven features need it. Installs via npm (needs [Node.js](https://nodejs.org) 18+). |
| A computer that stays awake during market hours | cherrypick runs on your machine on a schedule, so it has to be on to capture a session. **Windows is recommended** — the scheduler and self-healing are most complete there. |
| **[Dolt](https://github.com/dolthub/dolt)** *(earnings engine only)* | The earnings module reads its historical datasets from a local `dolt sql-server`. Not needed for MEIC or the GEX dashboard. |

## Quick Start

> **You'll need** the pieces listed under [Requirements](#requirements) — a tastytrade account, Python 3.11+,
> Claude Code, and a machine that stays on during market hours — plus a few minutes in a terminal.

### Install Claude Code

Install [Claude Code](https://docs.claude.com/en/docs/claude-code), Anthropic's agentic CLI — it's what
drives the interactive/live sessions, the slash-command workflows, and the synthesized analysis reports
(the unattended paper automation below runs without it):

```bash
npm install -g @anthropic-ai/claude-code   # needs Node.js 18+
claude --version                           # verify the install
```

Then run `claude` from the project directory to use the suite's slash commands (`/meic-start`,
`/earnings-start`, `/eod-report`, …). See the [Claude Code docs](https://docs.claude.com/en/docs/claude-code) for sign-in and setup.

### Set up the suite

```bash
# 1. Get the project
git clone --recurse-submodules https://github.com/joncovington/cherrypick.git
cd cherrypick/packages/orchestrator
pip install -e ".[dev]"

# 2. Set your preferences (symbols, strategies, alert channels)
python run.py init                       # creates ~/.cherrypick/config.json from the template
                                         # then open ~/.cherrypick/config.json in any editor and adjust

# 3. Link your tastytrade account for each engine you'll run (stored securely, never in a file)
python run.py connect --module meic
python run.py connect --module earnings        # only if you'll run the earnings engine
#   (the GEX dashboard reuses these same credentials — no separate step)

# 4. Make sure everything's ready
python run.py doctor                     # a simple green/red checklist

# 5. Turn it on — it now runs on its own, on a schedule
python run.py install
```

That's it. From here it collects data hands-off. To stop it later: `python run.py uninstall`.

## Checking your results

```bash
python run.py report              # win rate + gross/net P&L across strategies and risk profiles
python run.py dashboard --serve   # a live dashboard in your browser (P&L, fee drag, and the GEX view)
python run.py calibrate           # advice on when a risk profile has "earned" a step up
```

## Staying in the loop

Set your alert channels in `~/.cherrypick/config.json` (`log`, `desktop`, `discord`, `slack`). You'll be
notified when a new paper trade fills, and warned if the system stalls, so it can run unattended. Test the
channels any time with `python run.py notify-test`.

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
