# cherrypick — User Guide

> **⚠️ Experimental, educational software — not financial advice.** cherrypick is a prototype for
> learning about and researching options strategies. Its live-trading paths place **real,
> irreversible orders** at your own risk; options trading involves substantial risk of loss and is
> not suitable for all investors. Paper results are simulated and do not represent actual trading.
> Provided "as is", without warranty. **Read [DISCLAIMER.md](../DISCLAIMER.md) before use.**

A practical walkthrough for options traders. If you can follow a strategy checklist and run a few
commands, you can operate cherrypick. If you have not installed it yet and would rather not touch a
terminal at all, start with [QUICKSTART.md](../QUICKSTART.md).

---

## What cherrypick is (in one minute)

cherrypick lets you **test options strategies on paper, automatically.** You choose the strategies and
symbols, turn it on, and leave it running. During market hours it runs the strategies in simulation,
records every would-be trade (with modelled fills and costs), and monitors itself, notifying you if
anything stops working. Later you open the console or a report to review how your strategies would have
performed.

Its distinguishing feature is **variance testing**: you can run many parameter variations of a strategy
in parallel and compare which entry rules actually add edge — see
[Variance testing with arms](#variance-testing-with-arms) below.

### The strategy modules

| Module | What it trades | Out of the box |
|---|---|---|
| **MEIC** | 0DTE **multiple-entry iron condors** on index products (SPX in the shipped example). Strike selection scaled to volatility, credit floors and OTM buffers, regime gates (VIX, VIX1D, ATR, dealer gamma / GEX), per-side stops. | On |
| **Earnings** | **Defined-risk earnings plays** (iron fly, double calendar, iron condor, ATM calendar, directional credit spread, broken-wing butterfly), held overnight around a company's report. Needs [Dolt](#optional-extras). | On if Dolt is set up |
| **Flies** | 0DTE **net-credit butterflies** on SPX/XSP: whether the manufactured credit survives real costs, arm by arm. Built so a negative answer is a usable result. | On |
| **BWB** | A daily-laddered SPX **put broken-wing butterfly**, ~7 DTE, held to expiry; its arms differ only in whether and when a reversal-triggered add-on fires. | On |
| **Calendars** | Weekly **SPY double calendars**, an exit-parameter experiment. **EXPERIMENTAL.** | Off |
| **PMCC** | Deep-ITM **covered calls** on TQQQ. **EXPERIMENTAL.** | Off |
| **Curve** | **VXX call credit spreads** gated by a daily VIX/VIX3M regime read. **EXPERIMENTAL.** | Off |

The experimental modules are newer and less tested; switch one on deliberately if you want it (the
console's **Config** page, or `modules.<name>.enabled` in `~/.cherrypick/config.json`).

### Around them

- **GEX** — a self-hosted **gamma-exposure** view (call/put walls, the gamma-flip point) built from the
  market data you are already streaming, and a gate MEIC can use.
- **Morning overview** — a pre-open fact pack with a mechanical GREEN/YELLOW/RED phase.
- **End-of-day review** — one deterministic fact set per session across every module.
- **Technicals** — the market report's end-of-day store and technical engines. Needs Dolt.
- **The advisor** — paper A/B experiments proposed by an AI model and judged against the same control.
  Needs Claude Code, and is off by default.
- **The console** — the web page at <http://127.0.0.1:5070> where you look at all of it.

By default everything runs in **paper mode**: simulated fills, none of your money. Live trading is off
everywhere; see [Paper and live modes](#paper-and-live-modes).

---

## What you need

- A **[tastytrade](https://tastytrade.com) account** (the market data comes from tastytrade).
- A **computer that stays on** during the market sessions you want to capture. **Windows is
  recommended**: the scheduler and self-healing are most proven there. macOS and Linux work, through a
  crontab entry that is newer and less proven.
- **Python 3.11 or newer** and **Node.js 22 or newer**. The installer checks both and tells you the
  command to install whichever is missing.

### Optional extras

- **[Dolt](https://github.com/dolthub/dolt)** — a free database that serves the historical earnings,
  options and stock data the **Earnings** module and the **Technicals** report read. Several GB to
  download. Without it, those two are switched off and hidden.
- **[Claude Code](https://docs.claude.com/en/docs/claude-code)** — needed only for the AI advisor and
  the written end-of-day and morning narratives. Without it, those are switched off and hidden.

The installer detects both and records what it found as **capabilities** in your config; run
`python run.py capabilities` any time to see the current answer.

---

## Installing it

Use the one-command installer: double-click **`install.cmd`** on Windows, or run **`./install.sh`** on
macOS and Linux. [QUICKSTART.md](../QUICKSTART.md) walks through it in plain language, and
[INSTALL.md](../INSTALL.md) says exactly what each step does and lists the options. In short, it asks
you to accept the disclaimer, creates a Python environment in the checkout (`.venv`), installs every
package (except the experimental desk), builds the console, writes your config, offers the optional
Dolt download, asks for your tastytrade login, and starts the suite.

Running the installer again is always safe — it never overwrites your settings — and it is how you add
Dolt or Claude Code later.

**Commands on this page** are shown as `python run.py <cmd>`, run from `packages/orchestrator` with the
installer's Python: `..\..\.venv\Scripts\python run.py <cmd>` on Windows, `../../.venv/bin/python run.py
<cmd>` elsewhere (or activate `.venv` first). The suite runs on whichever Python ran `install`, so keep
using that one.

---

## First-time setup

The installer has already done the essentials. These are the things you may want to adjust.

**1. Your settings.** `~/.cherrypick/config.json` is where you say **which modules run, on what
schedule, and how you want to be alerted**. It lives under your user home, not the repo. Edit it in the
console's **Config** page, in the settings editor (`python run.py settings`, a local web page), or in
any text editor — every key is documented inline in `packages/orchestrator/config.example.json`.

**2. Your tastytrade login.** The installer stored it in your operating system's secure keyring (never
in a file). To change it, choose which account the suite would use *if* you ever enable live trading,
or add Discord/Slack alerts, run the wizard:

```bash
python run.py connect
```

There is exactly **one** shared login the whole suite reads, so there is a single place to rotate it.
The **console** only ever reads: at start-up it probes the token's scope, and a read-only token (the
right choice for paper mode) disables its write-oriented functions.

**3. Strategy details.** The fine-grained rules — symbols, target deltas, credit floors, entry
windows, arms — live in each module's own config under `~/.cherrypick/config/` (`meic.json`,
`flies.json`, `earnings.json`, …). A module without one runs from its shipped `config.example.json`;
copy that to `~/.cherrypick/config/<module>.json` to change anything. MEIC's arms are a separate file,
`~/.cherrypick/config/meic.risk.json` — see below. Each module's own docs explain every setting; start
with the symbols and leave the rest at their defaults.

**4. Confirm you're ready:**

```bash
python run.py doctor
```

A green/red checklist: Python, your settings, the broker connection, the data feed, the databases, the
capabilities, and (for earnings) Dolt. Green means you are good to go.

---

## Turning it on and off

The installer turns it on with `python run.py install`. That registers **one** background task (a
Windows scheduled task, or a crontab entry) which keeps a **supervisor** running, including after a
restart. The supervisor derives every job from your config on each pass:

- each enabled module's paper loop, during the session;
- a **watchdog** every ten minutes, and a tighter watch on the market-data streamer;
- a **fill-notifier** that pushes new paper trades to you;
- the morning overview, the end-of-day review, nightly backups and log archiving;
- the console itself.

The full inventory — what runs when, and what "healthy" looks like each morning — is the
[operations runbook](operations.md).

To stop everything, double-click **`uninstall.cmd`** (or run **`./uninstall.sh`**). It removes the
background task, stops the supervisor, then stops the streamer, the console and the Dolt server — a full
stop. Your recorded data, settings and login are kept, so running the installer again picks up where you
left off.

---

## Variance testing with arms

This is what most distinguishes cherrypick from a single-strategy paper tool. An **arm** is one
configured variant of a strategy, run as its own portfolio — control and treatment, in the clinical
sense. Arms change one parameter each: short-strike delta, credit floor, stop policy, a regime gate,
entry timing, wing width, buying-power cap or symbol. They all trade **the same live market snapshots in
parallel**, and every recorded trade is tagged with the arm that opened it, so `report` breaks results
down per arm. (Older modules call an arm a *profile*, *risk profile* or *book*; those spellings keep
working.)

The value is in controlled comparison: clone the `control` arm, change **one** parameter, and you
measure that idea's effect in isolation rather than confounding several changes at once.

**A base install runs only `control` in every module**, and any other arm is your machine's own
configuration. BWB, calendars and curve build comparison books into the module (bwb's add-on triggers,
calendars' `path`, curve's `noflip` and `hook`) that run unless a config sets that book's `enabled` to
`false`; their shipped examples do exactly that, so you switch one on in your own config. For MEIC,
the arm registry is read
from `$MEIC_RISK_CONFIG` if set, else `~/.cherrypick/config/meic.risk.json`, else the shipped
control-only `packages/meic/config.risk.example.json` — copy the example into your home and add arms
there. The advisor, when you enable it, adds its own `advised:<experiment>` arms beside the control.

Read the outcomes two ways. `report` shows **gross P&L** (did the entry select good setups?) beside
**net** (did it survive commissions and slippage?), because at small size costs can turn a real entry
edge into a net loss. And `calibrate` reports when an arm has met a documented threshold — enough
sessions, a sustained win rate, a sufficient sample — with readings such as the per-session Sharpe and
the Probabilistic Sharpe Ratio. Calibration is advisory only; it never changes your risk settings. See
[risk profiles](../packages/meic/docs/risk-profiles.md) and
[paper experiments](../packages/meic/docs/paper-experiments.md) for the complete method.

---

## Reviewing your results

| Where | What you get |
|---|---|
| the **console** at <http://127.0.0.1:5070> | Everything in your browser: overall status, each module's trades and P&L, fee drag, the **GEX** view, the morning overview and the end-of-day review, and health checks. The supervisor keeps it running — there is nothing to start. Modules and features that are switched off, or missing a capability, are hidden; experimental modules carry an *experimental* chip. |
| `python run.py report` | Win rate with **gross and net** P&L across modules and arms. Add `--eod` (today) or `--date YYYY-MM-DD` for one session. |
| `python run.py calibrate` | Whether an arm has collected enough evidence to consider a step up (advisory only). |
| `python -m cherrypick.review build` | The end-of-day review for one session, saved under `~/.cherrypick/data/review/` (`eod-<day>.json` and its render, `eod-<day>.md`). It runs automatically twice a day, so you rarely need it by hand. |

The end-of-day review is **scheduled automatically**: a provisional pass after the close and a final
one the next morning, once the overnight earnings plays have settled. To turn it off, set
`"review": {"enabled": false}` in `~/.cherrypick/config.json`.

---

## Staying informed

**Push notifications are off by default.** Out of the box every alert goes only to the suite's log,
and the console shows its own on-screen trade toasts, which are always on and need no setting. To be
told away from the console, add channels in `~/.cherrypick/config.json`: `notify.channels` (warnings)
and `notify.trade_channels` (fills) accept **`log`** (always kept), **`desktop`**, **`discord`** and
**`slack`**. You will then get:

- a **notification when a new paper trade fills**, and
- a **warning if something stalls** (the data feed going quiet mid-session, say), so a silent gap
  does not go unnoticed.

Discord and Slack need a webhook, stored in the keyring rather than the config file:

```bash
python run.py secrets-set --channel discord      # store a webhook in the keyring (paste when prompted)
python run.py notify-test                        # check that alerts actually reach you
```

---

## Everyday commands (cheat sheet)

| Command | Purpose |
|---|---|
| `python run.py doctor` | Green/red readiness check (add `--fast` to skip the broker check). |
| `python run.py status` | What the supervisor is running, and when things last ran or run next. |
| `python run.py capabilities` | Which optional features this machine can carry (Dolt, Claude Code). |
| `python run.py report` | Paper P&L summary. |
| the **console** at <http://127.0.0.1:5070> | Everything in your browser (kept running for you). |
| `python run.py reconcile` | Safety check: confirms your **real** brokerage account has no unexpected open positions. |
| `python run.py account` | See or choose which account the suite would use if run live. |
| `python run.py install` / `uninstall` | Turn the background task on / off (`uninstall.cmd` / `uninstall.sh` is the full stop). |
| `python run.py notify-test` | Send yourself a test alert. |

---

## Paper and live modes

- **Paper (the default).** The supervisor, the self-healing monitor, the reporting and all the variance
  testing operate on paper: live market data in, simulated fills out, none of your money. The
  orchestrator itself never places, cancels or closes a live order.
- **Live (off by default, and opened per module by you).** `connect` links your real tastytrade account
  so the engines use your live market data and `reconcile` can check your real positions (read-only).
  Trading for real is a deliberate, manual step you take per module. Once you take it, though, that
  module's loop trades on its own — see the warning below.

Paper and live books are kept strictly separate, and credentials stay in your OS keyring. The flies and
bwb live pilots add an extra safeguard: each has to be armed with a fresh, explicit confirmation every
trading day, and the arm is valid for that day only, so one day's confirmation never carries into the
next.

> ### ⚠️ If you are thinking about trading live
>
> **Know every way an order can be placed.** There are five: **MEIC**, **earnings**, **flies** and
> **bwb** each have a live loop behind their own `enable_live_trading` gate, and the **desk** is the
> manual one. The four loops trade on their own once started, without asking again: flies' and bwb's
> are supervisor jobs that run for as long as that day's arm is valid, and MEIC's and earnings' run in
> an agent session you start (`/meic-start`, `/earnings-start`). The orchestrator never places an order
> itself, but "the automation won't trade for me" stops being true the moment one of those gates is
> open.
>
> **`packages/desk` is an EXPERIMENTAL prototype for educational purposes only**, not installed or
> enabled by default. Read [its documentation](../packages/desk/README.md) in full before going near
> it.
>
> **Nothing in this suite has been validated as profitable.** The paper experiments exist because the
> answer is not known yet, and a good number of the recorded results so far are negative. Simulated
> fills are optimistic by construction — the suite's own measurements show live fills costing far more
> than the model assumed — so paper results are not a forecast of live ones.
>
> If you enable any live capability you do so entirely at your own risk; read
> [DISCLAIMER.md](../DISCLAIMER.md) again first.

## How your account is protected

- **Paper unless you open a gate.** With every live gate closed (the default), the scheduled jobs run
  in simulation and only read market data: they do **not** place, cancel, close or adjust real orders,
  and nothing on the schedule switches a module to live. Opening a gate is a manual action you take
  yourself (see above).
- **A real-account safety check.** `reconcile` looks at your actual brokerage account(s) and flags
  anything that is not flat. It is read-only and never trades.
- **Credentials stay secure.** Your tastytrade login is kept in your OS keyring, and account numbers
  are always masked (shown as `****1234`) wherever they appear.
- **The safety monitor never trades.** The most it does on its own is restart a stalled data feed or a
  dead background service — never anything order-related.

---

## Troubleshooting

- **Start with `python run.py doctor`.** It pinpoints most problems (broker not connected, data feed
  down, a database missing, a recorded capability the machine no longer has, Dolt not running for
  earnings).
- **A module or page is missing from the console.** It is switched off, or needs a capability this
  machine does not have (`python run.py capabilities` says which). Install the missing piece, run the
  installer again, and switch the module on.
- **"Not much is happening."** Outside market hours, or when volatility and credit gates are not met,
  the engines correctly sit on their hands. Check `status` to confirm the supervisor is running.
- **No alerts arriving?** Run `notify-test`; if desktop/Discord do not show up, re-check the `notify`
  channels in `~/.cherrypick/config.json` and (for Discord/Slack) that you stored the webhook with
  `secrets-set`.
- **Laptop keeps sleeping.** `packages/orchestrator/tools/setup-walkaway-durability.ps1` keeps a
  Windows machine awake and running scheduled tasks while you are away.

---

## Disclaimer

For **educational and research purposes only** — **not financial or trading advice.** Options trading
involves substantial risk of loss; paper results do not reflect real-world performance. The full terms
are in [DISCLAIMER.md](../DISCLAIMER.md) and the [LICENSE](../LICENSE).
