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
single idea rather than confounding several at once. MEIC currently runs four such streams in parallel —
a `control` baseline plus one arm each for entry selectivity and two wing widths — and the pattern
answers questions like:

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

One supervisor daemon runs everything — your OS scheduler holds a **single** cherrypick entry, and every
job (each engine's loop, the watchdog, notifications, end-of-day reports) is derived from your config on
each pass. Changing a cadence is a config edit; there is no task to re-register.

The watchdog verifies data is actually flowing during market hours and notifies you only when something
needs you. Its remediation is deliberately limited to restarting a stalled feed or a dead background
service — it never places, cancels, or closes an order. The market-data streamer gets its own tighter
watch, because its failure window is the one that can't be recovered: a producer that is down through the
first five minutes of the session loses that day's opening range for good. It is restarted on **silence**,
not just on death — the incident behind that rule was a live-but-quiet socket that stalled for 34 hours
while every process still looked healthy.

### Realistic cost modeling

Fills are modeled at mid price minus a slippage allowance, on top of the actual tastytrade
commission/exchange schedule — one shared cost model across every engine — so reported "net" figures
reflect real transaction costs. That is not a rounding detail at this size: a recorded paper trade in this
suite once collected $4.00 of credit against $4.96 of fees.

## The strategy engines

- **MEIC** — 0DTE multiple-entry iron condors on indices/ETFs (SPX, XSP, QQQ, IWM, …), with per-side
  stops, regime gates (VIX, VIX1D, ATR, GEX), and all the risk-profile machinery above.
- **Earnings** — defined-risk earnings plays (iron flies, calendars, condors, broken-wing flies, and more),
  each sized to a fixed dollar risk, held overnight around a company's earnings report.
- **Flies** — 0DTE net-credit butterflies on SPX/XSP built from two credit spreads, measuring whether the
  manufactured credit survives real trading costs. See [packages/flies](packages/flies). It is built so a
  *negative* result is usable: floors are measured after fees, and a book-level floor always carries the
  price band over which it actually holds.

## Where you look at the results

- **The console** (`packages/console`, `127.0.0.1:5070`) — the unified web UI: every engine's read models
  plus interactive screening, the watchlist, and a strategy builder in one app. Read-only over every other
  package's data. This is where the suite is heading; the per-module dashboards still run alongside it.
- **The suite dashboard** (`run.py dashboard --serve`, `:8787`) — one status page composing health, P&L,
  and each module's own dashboard as an embed.
- **Per-module dashboards** — MEIC (`:5050`/`:5051`), flies (`:5052`), GEX (`:5055`), scout (`:5057`).
- **Reports on disk** — a deterministic end-of-day report per module, a cross-module suite digest, and
  (opt-in) an AI-written narrative over the day's deterministic reports. The deterministic files stay the
  source of record.

Every surface binds to loopback only.

## Paper & live modes

- **Paper (the default — and what the automation runs).** The scheduler, the self-healing, the reporting,
  and all the variance testing operate on paper: live market data in, simulated fills out, **none of your
  money**. The orchestrator **never places, cancels, or closes a live order** — by design it can't sit on a
  trading decision.
- **Live (off by default, and enabled per module by you).** You link your real tastytrade account with
  `connect` so the engines use *your* live market data and can **reconcile** against your real positions (a
  read-only safety check that flags anything a paper-only suite shouldn't be holding). Turning live
  trading *on* is always a deliberate, manual, per-module act — the orchestrator will never do it for you
  and never places an order itself. But note what that means: **once a module's live gate is open, that
  module's own loop can place orders without asking again.** MEIC, earnings, and flies each have a live
  path behind their own `enable_live_trading` gate. Flies is the most tightly bounded of them — a pilot
  that must be re-armed by hand every trading day, one arm and one position at a time, self-disarming each
  evening ([the plan](packages/flies/docs/live-trading-plan.md)).
- **The manual desk — ⚠️ EXPERIMENTAL.**
  [packages/desk](packages/desk) is the only *discretionary* live path: a foreground CLI you invoke
  yourself, with its own config, its own PIN, and a ticket you confirm per order. No loop, no schedule.
  It is deliberately authorized on its own so placing one order never means temporarily flipping a
  strategy module's live-trading switch — but it is new, lightly exercised, and every order it submits is
  irreversible. **See the warning below before using it.**

Credentials live in your operating system's secure keyring — never in a file — and paper and live books are
kept strictly separate.

> ### Before you go anywhere near live trading
>
> ⚠️ &nbsp;**Read this section if you are considering enabling any live capability.**
>
> **This project is for education and research, and it defaults to paper for a reason.** Nothing here has
> been validated as profitable — the paper experiments exist precisely because the answer is not yet
> known, and several of the suite's own recorded results are *negative*.
>
> **There are four ways real orders can be placed**, and it is worth knowing all of them before you open
> any of them: **MEIC**, **earnings**, and **flies** each have a live path behind their own
> `enable_live_trading` gate, and **desk** is the manual one. The first three are *loops* — once the gate
> is open, they act on their own schedule without asking again. The orchestrator itself never places an
> order, but "the automation won't trade for you" stops being true the moment you open one of those gates.
>
> **`packages/desk` is experimental.** It is the newest and least-exercised part of the suite and has no
> meaningful track record. Treat it as unproven software that submits irreversible orders. Read
> [its documentation](packages/desk/README.md) in full first, start with the smallest size that can
> possibly matter, and never run it on capital you cannot afford to lose outright.
>
> **The flies live pilot is deliberately tiny** (one arm, one symbol, one position at a time, re-armed by
> hand each day) because that is the responsible size for something still being measured — not because
> the constraint is arbitrary. Do not widen it to "see what happens".
>
> **Paper results do not predict live results.** Simulated fills are optimistic by construction: the
> suite's own measurements show live fills refusing at 2.9× the modeled cost, and its completion-rate
> figures are explicitly an *upper bound* on what live would achieve. If you enable any live capability,
> **you do so entirely at your own risk** — see the [disclaimer](#disclaimer).

## What's in the repo

One workspace, ten packages. The three strategy engines above plus the pieces that feed, drive, and
read them:

| Package | What it is |
|---|---|
| [packages/orchestrator](packages/orchestrator) | The scheduler, watchdog, notifications, and the whole read side (report / calibrate / dashboard / EOD). Drives the engines by subprocess. |
| [packages/core](packages/core) | The shared `cherrypick.core` library — calendar, fees, profiles, GEX math, broker, auth. Install it first. |
| [packages/meic](packages/meic) · [packages/earnings](packages/earnings) · [packages/flies](packages/flies) | The three strategy engines. |
| [packages/streamer](packages/streamer) | The single market-data producer. Everything else reads the cache it writes; nothing else writes it. |
| [packages/gex](packages/gex) | The standalone GEX dashboard. |
| [packages/console](packages/console) | The unified web console (`127.0.0.1:5070`) — every module's read models in one app. Read-only. |
| [packages/scout](packages/scout) | Interactive screening and strategy exploration. |
| [packages/desk](packages/desk) | ⚠️ **Experimental.** The manual trading desk — the only *discretionary* live-order path, driven by you per order. [Read the warning](#before-you-go-anywhere-near-live-trading). |

## Requirements

| You'll need | Why |
|---|---|
| A [tastytrade](https://tastytrade.com) account | Supplies the live market data the paper engines fill against (and your real account, if you ever choose to trade live). |
| **Python 3.11+** | Runs the orchestrator, both strategy engines, and the reporting. |
| **[Claude Code](https://docs.claude.com/en/docs/claude-code)** | Anthropic's agentic CLI. It drives the interactive and live-trading sessions, the slash-command workflows (`/meic-start`, `/earnings-start`, `/eod-report`), and the agent-synthesized analysis. The unattended **paper** automation runs on its own without it — but the agent-driven features need it. Installs via npm (needs [Node.js](https://nodejs.org) 18+). |
| A computer that stays awake during market hours | cherrypick runs on your machine on a schedule, so it has to be on to capture a session. **Windows is recommended** — the scheduler and self-healing are most complete there. |
| **[pnpm](https://pnpm.io)** *(console only)* | The unified web console is a Node/TypeScript package and builds with pnpm. Not needed for anything else — every other package is Python. |
| **[Dolt](https://github.com/dolthub/dolt)** *(earnings engine only)* | The earnings module reads its historical datasets from a local `dolt sql-server`. Not needed for MEIC or the GEX dashboard. |

## Quick Start

> **You'll need** the pieces listed under [Requirements](#requirements) — a tastytrade account, Python 3.11+,
> Claude Code, and a machine that stays on during market hours — plus a few minutes in a terminal.

### 0. Install Claude Code (optional)

Install [Claude Code](https://docs.claude.com/en/docs/claude-code), Anthropic's agentic CLI — it's what
drives the interactive sessions, the slash-command workflows, and the synthesized analysis reports.
**Skip it if you only want the unattended paper automation**, which runs without it:

```bash
npm install -g @anthropic-ai/claude-code   # needs Node.js 18+
claude --version                           # verify the install
```

Then run `claude` from the project directory to use the suite's slash commands (`/meic-start`,
`/earnings-start`, `/eod-report`, …). See the [Claude Code docs](https://docs.claude.com/en/docs/claude-code) for sign-in and setup.

### 1. Install

```bash
git clone https://github.com/joncovington/cherrypick.git
cd cherrypick

# Installs the shared cherrypick.core library first, then every package. Do this from the repo root.
scripts/dev-install.sh          # or: scripts\dev-install.ps1 on Windows
```

Prefer to do it by hand, or only want the orchestrator? `cherrypick.core` **must** go first — every other
package imports it, and there is no `sys.path` fallback:

```bash
pip install -e packages/core       # always first
cd packages/orchestrator
pip install -e ".[dev]"
```

MEIC and earnings install the same way (`pip install -e ".[dev]"` in their directory) if you plan to run
them. **flies, gex, and the streamer need no install** — the orchestrator runs them in place. The
**console** is the one Node package; build it only if you want the unified UI:

```bash
cd packages/console && pnpm install && pnpm build
```

All commands below are run from `packages/orchestrator` as `python run.py <cmd>`, or as `cherrypick <cmd>`
anywhere once pip-installed.

### 2. Create your config

```bash
python run.py init          # writes ~/.cherrypick/config.json from the annotated template
```

Then edit it — every key is documented inline in `packages/orchestrator/config.example.json`. Or use the
built-in editor, which is the friendlier route:

```bash
python run.py settings      # local web editor for every config file in the suite, loopback :8804
```

The settings editor is the suite's only config-writing surface. It backs up before every write, preserves
the comments and key order in your file, and renders the live-trading gate fields **read-only** — so it
can never arm or disarm live trading.

### 3. Connect your broker account

Credentials live in your operating system's secure keyring (Windows Credential Manager/DPAPI, macOS
Keychain, Linux Secret Service) — **never in a file, an environment variable, or a log**. One shared login
serves the whole suite, so there is a single place to rotate it.

```bash
python run.py connect       # the wizard: shared login, account designation, optional webhooks
```

It walks you through the tastytrade login (entered once, input hidden — the orchestrator never sees your
`client_secret` or `refresh_token`, it delegates to the credential tool), a connection check, choosing
which account the suite would use *if* you ever enable live trading, and optional Slack/Discord alerts.
If you had per-module credentials from an older setup, it offers to migrate them into the shared login so
one rotation point remains. Everything after the login is skippable with Enter.

```bash
python run.py account                 # show the designated account (masked to ****1234)
python run.py account --set 1234      # change it
python run.py connect --module meic   # only if ONE module must differ from the suite default
```

Account numbers are masked to `****1234` everywhere they surface. Designating an account is configuration
only — it never places a trade, and it does not enable live trading.

**Notification webhooks** are stored in the keyring the same way, never in your config file:

```bash
python run.py secrets-set --channel discord    # prompts without echo; also: slack, discord_follow
python run.py secrets-status                   # which channels are configured (prints no secrets)
```

Two surfaces limit what the shared login can *do* rather than holding one of their own. The **console**
only reads, and probes the token's scope at boot — a read-only token disables its write-oriented
functions. The **desk** stores no secrets at all: it borrows a module's keyring session and adds its own
authorization (a PIN kept only as a salted verifier, a per-order ticket, its own policy gates). Borrowing
credentials is not borrowing permissions.

### 4. Check and turn it on

```bash
python run.py doctor        # green/red readiness checklist — read-only, safe to run any time
python run.py install       # registers the one anchor task and starts the supervisor + data feed
```

That's it. From here it collects data hands-off. `python run.py status` shows what is running;
`python run.py uninstall` stops everything and leaves your recorded data and settings untouched.

## Checking your results

```bash
python run.py report              # win rate + gross/net P&L across strategies and risk profiles
python run.py report --eod        # scope to one settlement session (--date YYYY-MM-DD for a past one)
python run.py dashboard --serve   # the suite status page in your browser (:8787)
python run.py calibrate           # advice on when a risk profile has "earned" a step up
python run.py eod-digest          # write today's cross-module digest to a file
```

For the unified web console instead: `cd packages/console && python run.py dashboard --serve` (`:5070`).

Paper and live results are read through **separate** commands by design — `report` is paper-only and
`report --live` reads the modules' separate live ledgers, so a calibration reading can never accidentally
include live trades. If you do trade live, `python run.py reconcile` diffs what the suite believes it
holds against what your broker actually shows, and flags anything a paper-only setup shouldn't be holding.

## Staying in the loop

Set your channels in `~/.cherrypick/config.json` under `notify` (`log`, `desktop`, `discord`, `slack`).
The `log` channel is always on as a floor, so a failed push never means a lost record. You'll be notified
when a paper trade fills and warned if the system stalls, so it can run unattended. Test any time with
`python run.py notify-test`.

Trade pushes go to their own channel set (`notify.trade_channels`, default log + Discord) so frequent
paper fills don't spam desktop toasts. If an engine runs several arms at once and per-trade pushes get
noisy, switch `notify.trade_summary.mode` to `summary` — every trade then rolls up into one periodic
per-symbol digest (`MEIC digest 13:45 ET — SPX: 30 entries (open×10 width-10×10 width-5×10) · 2 exits
net +$48 · day 7 trades net +$61`) on whatever `interval_minutes` you set.

## Where everything lives

Nothing runtime is written into your checkout. Config, data, logs, and reports all live under one
per-user directory — relocate the whole thing by setting `CHERRYPICK_HOME`:

```
~/.cherrypick/
  config.json                       # the orchestrator's config
  config/<engine>.json              # one per engine (meic, earnings, flies, gex, streamer, console, …)
  data/marketdata/stream_cache.db   # the shared market-data cache — one writer, every module reads it
  data/<module>/paper_trades.db     # paper ledger, per module (live ledgers are separate files)
  logs/                             # suite + per-module logs, EOD reports, digests
  state/                            # supervisor job state, heartbeats
```

Paths inside your config resolve **relative** to the config file's own directory, so nothing hardcodes a
location on your machine. Paper and live ledgers are separate files, never queryable through one
connection.

## Good to know

- **Paper by default.** Every engine ships with live trading off, and the orchestrator that schedules
  them never places, cancels, or closes an order itself. Opening a module's live gate is on you — and
  once open, that module's loop trades within its own limits without asking again.
- **Your data stays yours.** Trades and credentials live on your machine (credentials in your operating
  system's secure keyring — never in a plain file).
- **Set-and-forget.** Once installed, it runs on a schedule and recovers from common hiccups by itself.
- **Runs on your computer**, not a cloud service — so the machine needs to stay awake during the sessions
  you want to capture. (There's a helper to keep a laptop from sleeping mid-session.)

📖 **New here?** The [User Guide](docs/PROJECT.md) walks through setup, settings, daily use, and
troubleshooting in plain language. For the full functionality reference — architecture, the CLI, the
reporting/dashboard stack, configuration, and the safety model — see the [documentation index](docs/README.md).

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
