# cherrypick

> **⚠️ Experimental, educational software — not financial advice.** cherrypick is a prototype for
> learning about and researching options strategies. Its live-trading paths place **real,
> irreversible orders** at your own risk; options trading involves substantial risk of loss and is
> not suitable for all investors. Paper results are simulated and do not represent actual trading.
> Provided "as is", without warranty. **Read [DISCLAIMER.md](DISCLAIMER.md) before use.**

**Test many variations of an options strategy against the live market — in paper mode — to see which entry rules actually add edge.**

**Getting started:** not a programmer? Follow [QUICKSTART.md](QUICKSTART.md) — a double-click installer
on Windows, one command on a Mac. Everyone else: [INSTALL.md](INSTALL.md).

cherrypick runs your options strategies on a schedule against the live market in **paper mode**, recording
every simulated trade with modelled fills and costs. Its defining capability is **parallel variance
testing**: it runs many parameter variations of the same strategy at once, so you can measure which entry
rules add edge before committing real capital. It monitors its own data feed during market hours and
can notify you (desktop, Discord, or Slack, once you switch a channel on) if anything stalls.

![CI](https://img.shields.io/github/actions/workflow/status/joncovington/cherrypick/ci.yml?branch=main)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## What sets it apart

Most paper-trading tools replay a single strategy. cherrypick is designed to answer questions about yours.

### Variance testing with arms

Define a strategy once, then create named **arms** — variations that each change one parameter:
short-strike delta, credit floor, stop policy, a regime filter, entry timing, wing width, buying-power
cap, or symbol. Every arm trades the same live market snapshots in parallel as its own portfolio, and
every fill is tagged with the arm that opened it. (Older modules call an arm a *risk profile*, *profile*
or *book*; those spellings keep working.)

Because each arm changes one lever against a shared control, you can measure the isolated effect of a
single idea rather than confounding several at once. **A base install runs only the `control` arm in
every module**; any further arms are your own machine's configuration under `~/.cherrypick/config/`
(MEIC's in `meic.risk.json`). BWB, calendars and curve also build comparison books into the module
(bwb's add-on triggers, calendars' `path`, curve's `noflip` and `hook`); their shipped examples switch
each one off with `enabled: false`, and you switch one on in your own config. With Claude Code
installed, the optional AI advisor can run its own experiment arms beside the control, each proposed
and judged against it. The pattern answers questions like:

- whether a tighter stop protects the position or exits it prematurely (a hold-to-expiry arm vs. a stopped one);
- whether further-OTM shorts justify their thinner credit on trending days (a delta sweep across 0.10 / 0.15 / 0.25);
- whether gating on dealer gamma (GEX) avoids adverse sessions;
- whether restricting entries to the afternoon changes the outcome.

Reporting separates **gross P&L** (does the entry select good setups?) from **net** (does it survive
commissions and slippage?), so an idea that looks unprofitable after costs can still reveal a genuine entry
edge. A **calibration** report indicates when an arm has met a documented threshold — enough sessions, a
sustained win rate, a sufficient sample, and readings such as the per-session Sharpe and the
Probabilistic Sharpe Ratio — to justify a step up in risk; it never changes your risk settings
automatically.

See [risk profiles](packages/meic/docs/risk-profiles.md) and [paper experiments](packages/meic/docs/paper-experiments.md) for the full method.

### A lightweight GEX (gamma-exposure) dashboard

Dealer gamma positioning is a common input for 0DTE traders. cherrypick streams the option chain and
computes a gamma-exposure profile from open interest and greeks, presenting the **call/put walls**, the
**gamma-flip point**, and an **open-interest-vs-volume** view on the console's GEX page. It is
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

### Realistic cost modelling

Fills are modelled with a slippage allowance — conceded inside the fill price by some engines, charged
as its own cost column by others, and labelled either way — on top of the actual tastytrade
commission/exchange schedule, one shared cost model across every engine, so reported "net" figures
reflect real transaction costs. That is not a rounding detail at this size: a recorded paper trade in this
suite once collected $4.00 of credit against $4.96 of fees.

## The strategy engines

- **MEIC** — 0DTE multiple-entry iron condors on indices/ETFs (SPX in the shipped example; XSP, QQQ,
  IWM, … by configuration), with per-side stops, regime gates (VIX, VIX1D, ATR, GEX), and all the arm
  machinery above.
- **Earnings** — defined-risk earnings plays (iron flies, calendars, condors, broken-wing flies, and more),
  each sized to a fixed dollar risk, held overnight around a company's earnings report. Needs the
  optional Dolt data; without it the module is switched off and hidden.
- **Flies** — 0DTE net-credit butterflies on SPX/XSP built from two credit spreads, measuring whether the
  manufactured credit survives real trading costs. See [packages/flies](packages/flies). It is built so a
  *negative* result is usable: floors are measured after fees, and a book-level floor always carries the
  price band over which it actually holds.
- **Calendars** — ⚠️ **EXPERIMENTAL, off by default.** Weekly SPY double calendars (SPX before
  2026-08-15) entered every Monday at the expected-move strikes (shorts
  expiring Friday, longs the following Monday), built as a forward *exit-parameter experiment*: a
  mechanical control book, a permissive path book recording every tick, and a read-side replay that
  scores profit targets, stops and exit timings over the recorded path — validated against the real
  books to the cent. Paper-only and credential-free. See [packages/calendars](packages/calendars).
- **PMCC-99** — ⚠️ **EXPERIMENTAL, off by default.** Deep-ITM covered calls on TQQQ and XSP: buy an 85-90-delta ~21DTE call as a stock
  substitute, sell the ATM ~7DTE call nearest spot (no yield floor), hold to the short's own
  expiration and close both legs together. Single `control` book plus an advised A/B against the
  old early-tv-exit rule; early assignment is measured, never modelled, so paper results are an
  explicit upper bound. Paper-only and credential-free. See [packages/pmcc](packages/pmcc).
- **Curve** — ⚠️ **EXPERIMENTAL, off by default.** VXX call credit spreads harvesting the VIX term-structure roll yield, gated by a daily
  VIX/VIX3M regime read. Three books (contango-gated control, no-flip-exit, deep-backwardation hook)
  trade the identical structure and differ only in entry gate and exit rule; the daily regime
  classification is recorded every session as the module's second product. Paper-only and
  credential-free. See [packages/curve](packages/curve).
- **BWB** — a daily-laddered SPX put broken-wing butterfly entered at the expected move for a net
  credit, ~7 DTE, held to expiry. Four books trade the identical base structure and differ only in
  whether a reversal-triggered add-on fires (never / delta touch / confirmed bounce / gamma-flip
  reclaim), plus an opt-in call-side book at the GEX call wall. A narrow, per-day-armed live path
  (one arm, worst-case margin capped, no closing orders) exists alongside the paper books since
  2026-09-18. See [packages/bwb](packages/bwb).
- **Overview** — the pre-open morning market overview: one deterministic fact pack per session with a
  mechanical GREEN/YELLOW/RED phase from five declared gates; missing data can never produce RED and
  always blocks GREEN. See [packages/overview](packages/overview).
- **Technicals** — the market report's end-of-day store: adjusted daily bars and IV history for the
  stock universe and the rotation ETFs, from the local Dolt clones — so, like earnings, it needs the
  optional Dolt data. See [packages/technicals](packages/technicals).

The experimental modules are newer and less tested. Switch one on deliberately on the console's
**Config** page or under `modules.<name>.enabled` in `~/.cherrypick/config.json`; the console marks them
with an *experimental* chip.

## Where you look at the results

> **⚠️ The console is not hardened. Keep it on this computer.** It has no login and was not built
> to face a network: anyone who can reach it sees your positions and results and can use its
> Config page, including the live-trading halt switch. It listens only on `127.0.0.1` (this
> computer) by design. **Never expose it to your local network or the internet**: no port
> forwarding, reverse proxy, tunnel (ngrok and the like) or remote-access sharing of the page.
> The settings editor (`run.py settings`, port 8804) is under the same rule.

- **The console** (`packages/console`, `127.0.0.1:5070`) — the suite's one read surface: every engine's
  read models plus interactive screening, the watchlist, and a strategy builder in one app. Read-only over
  every other package's data, and kept running by the supervisor rather than started by hand. A module or
  feature that is switched off, or needs a capability this machine lacks, is hidden. It replaced the
  suite dashboard and every per-module dashboard on 2026-08-12.
- **The end-of-day review on disk** — one versioned, deterministic fact set per session covering every
  module (`packages/review`), with rendered reports built from it and (opt-in, needs Claude Code) an
  AI-written narrative over it. The fact set stays the source of record; it replaced the old per-module EOD reports on
  2026-08-13.

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
  module's own loop can place orders without asking again.** MEIC, earnings, flies, and bwb each have a live
  path behind their own `enable_live_trading` gate. Flies and bwb are the most tightly bounded of them —
  pilots that must be re-armed by hand every trading day, one arm at a time, under a worst-case
  buying-power cap; while armed, the supervisor runs their live loop as a job
  ([the flies plan](packages/flies/docs/live-trading-plan.md)).
- **The manual desk — ⚠️ EXPERIMENTAL.**
  [packages/desk](packages/desk) is an EXPERIMENTAL prototype for educational purposes only, not
  installed or enabled by default: a foreground CLI for placing one discretionary order by hand.
  **Read [its documentation](packages/desk/README.md) and the warning below before going near it.**

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
> **There are five ways real orders can be placed**, and it is worth knowing all of them before you open
> any of them: **MEIC**, **earnings**, **flies**, and **bwb** each have a live path behind their own
> `enable_live_trading` gate, and **desk** is the manual one. The first four are *loops* — once the gate
> is open, they act on their own schedule without asking again (flies' and bwb's as supervisor jobs while
> that day's arm is valid; MEIC's and earnings' in an agent session you start). The orchestrator itself
> never places an order, but "the automation won't trade for you" stops being true the moment you open
> one of those gates.
>
> **`packages/desk` is an EXPERIMENTAL prototype for educational purposes only**, not installed or
> enabled by default. It has no meaningful track record. Read [its documentation](packages/desk/README.md)
> in full before going near it.
>
> **The flies and bwb live pilots are deliberately tiny** (one arm, re-armed by hand each day, under a
> worst-case buying-power cap) because that is the responsible size for something still being measured — not because
> the constraint is arbitrary. Do not widen it to "see what happens".
>
> **Paper results do not predict live results.** Simulated fills are optimistic by construction: the
> suite's own measurements show live fills refusing at 2.9× the modelled cost, and its completion-rate
> figures are explicitly an *upper bound* on what live would achieve. If you enable any live capability,
> **you do so entirely at your own risk** — read [DISCLAIMER.md](DISCLAIMER.md).

## What's in the repo

One workspace, seventeen packages: the strategy engines above plus the pieces that feed, drive, and
read them.

| Package | What it is |
|---|---|
| [packages/orchestrator](packages/orchestrator) | The supervisor, watchdog, notifications, and the read side (report / calibrate / EOD). Drives the engines by subprocess. |
| [packages/core](packages/core) | The shared `cherrypick.core` library — calendar, fees, profiles, GEX math, broker, auth. Install it first. |
| [packages/meic](packages/meic) · [packages/earnings](packages/earnings) · [packages/flies](packages/flies) · [packages/calendars](packages/calendars) · [packages/pmcc](packages/pmcc) · [packages/curve](packages/curve) · [packages/bwb](packages/bwb) | The seven strategy engines. Calendars, pmcc and curve are EXPERIMENTAL, paper-only and off by default; bwb is paper by default with a narrow live path; earnings needs the optional Dolt data. |
| [packages/streamer](packages/streamer) | The single market-data producer. Everything else reads the cache it writes; nothing else writes it. |
| [packages/gex](packages/gex) | The GEX engine and spot-trail recorder; the console renders it. |
| [packages/console](packages/console) | The unified web console (`127.0.0.1:5070`) — every module's read models plus research and screening, in one app. Read-only. |
| [packages/overview](packages/overview) | The pre-open morning market overview: one deterministic fact pack per session with a mechanical GREEN/YELLOW/RED phase. Read-only over everything it touches. |
| [packages/technicals](packages/technicals) | The market report's end-of-day store and technical engines: adjusted daily bars and IV for the universe and rotation ETFs, from the local Dolt clones (needs the optional Dolt data). |
| [packages/review](packages/review) | The cross-module end-of-day review: one versioned fact set per session over every engine, plus the renders of it. Read-only over every other package. |
| [packages/advisor](packages/advisor) | The deterministic half of the AI advisor — fact packs, reply validation, and paper A/B experiments. Contains no AI itself; off by default, and needs Claude Code. |
| [packages/desk](packages/desk) | ⚠️ **EXPERIMENTAL** prototype for educational purposes only, not installed or enabled by default — the manual trading desk. [Read the warning](#before-you-go-anywhere-near-live-trading). |

## Requirements

| You'll need | Why |
|---|---|
| A [tastytrade](https://tastytrade.com) account | Supplies the live market data the paper engines fill against (and your real account, if you ever choose to trade live). A read-only API grant is enough for paper mode. |
| **Python 3.11+** | Every package except the console is Python. |
| **[Node.js](https://nodejs.org) 22+** | The console — the suite's one read surface — is a Node/TypeScript package. The installer adds **pnpm 11** for you. |
| A computer that stays awake during market hours | cherrypick runs on your machine, so it has to be on to capture a session. **Windows is recommended** — the scheduler and self-healing are most proven there; the macOS/Linux crontab backend is newer and less proven. |
| **[Dolt](https://github.com/dolthub/dolt)** *(optional)* | The **earnings** module and the **technicals** report read three public DoltHub datasets from a local `dolt sql-server` (`stocks` about 3 GB, `earnings` about 1.35 GB, `options` larger still). Without it, both are switched off and hidden. |
| **[Claude Code](https://docs.claude.com/en/docs/claude-code)** *(optional)* | Anthropic's agentic CLI. The AI advisor, the agent-written end-of-day and morning narratives, and the repo's slash commands (`/meic-start`, `/earnings-start`, `/console`, …) need it. Without it, those features are switched off and hidden; the paper automation runs on its own. |

Whether this machine has the two optional pieces is recorded as **capabilities** in
`~/.cherrypick/config.json` (`capabilities: {claude, dolt}`, absent means no). The installer detects
them; `python run.py capabilities` shows the resolved view, `--detect --write` probes again, and
`--cap dolt=false` overrides one by hand.

## Quick Start

### 1. Install

On **Windows**, download or clone the repository and double-click **`install.cmd`**. On **macOS or
Linux**:

```bash
git clone https://github.com/joncovington/cherrypick.git
cd cherrypick
./install.sh
```

The installer asks you to accept [DISCLAIMER.md](DISCLAIMER.md) by typing `YES`, checks Python and
Node, creates a virtual environment at `.venv` in the checkout, installs every package except the
experimental desk, builds the console, writes `~/.cherrypick/config.json`, detects capabilities, offers
the optional Dolt download, asks for your tastytrade login, starts the suite, and opens the console at
<http://127.0.0.1:5070>. It is safe to run again, never overwrites your settings, and is how you add
Dolt or Claude Code later. [QUICKSTART.md](QUICKSTART.md) is the same thing in plain language;
[INSTALL.md](INSTALL.md) lists every step and option, and the developer path (`scripts/dev-install.*`).

Every command below is `python run.py <cmd>` from `packages/orchestrator`, using the installer's
interpreter (`.venv\Scripts\python` on Windows, `.venv/bin/python` elsewhere) — the supervisor runs on
whichever Python ran `install`. A pip install also exposes them as `cherrypick <cmd>`.

### 2. Adjust your config

Every key is documented inline in `packages/orchestrator/config.example.json`. The console's **Config**
page covers the everyday switches (which modules run, the live-trading halt); the settings editor covers
every config file in the suite:

```bash
python run.py settings      # local web editor for every config file, loopback :8804
```

The settings editor backs up before every write, preserves the comments and key order in your file, and
renders the live-trading gate fields **read-only** — so it can never arm or disarm live trading.

### 3. Your broker account

Credentials live in your operating system's secure keyring (Windows Credential Manager/DPAPI, macOS
Keychain, Linux Secret Service) — **never in a file, an environment variable, or a log**. One shared login
(`cherrypick-broker`) serves the whole suite, so there is a single place to rotate it. The installer
stores it; the `connect` wizard does the rest:

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
python run.py secrets-set --channel discord    # prompts without echo; also: slack
python run.py secrets-status                   # which channels are configured (prints no secrets)
```

The **console** only reads, and probes the token's scope at boot — a read-only token disables its
write-oriented functions.

### 4. Check, start and stop

```bash
python run.py doctor        # green/red readiness checklist — read-only, safe to run any time
python run.py install       # registers the one anchor task and starts the supervisor + data feed
python run.py status        # what the supervisor is running
```

The installer has already run `install`; from there it collects data hands-off. To stop it completely,
run **`uninstall.cmd`** (Windows) or **`./uninstall.sh`** (macOS/Linux): it removes the anchor task and
stops the supervisor (`run.py uninstall`), then stops the streamer, the console and everything else
still running (`run.py stop --all`), and the Dolt server. Your recorded data, settings and keyring login
are kept.

## Checking your results

```bash
python run.py report              # win rate + gross/net P&L across modules and arms
python run.py report --eod        # scope to one settlement session (--date YYYY-MM-DD for a past one)
python run.py calibrate           # advice on when an arm has "earned" a step up
python -m cherrypick.review build # today's cross-module review: fact set + render
```

To look rather than read: the **console** at <http://127.0.0.1:5070>. The supervisor keeps it running,
so there is normally nothing to start — `/console` opens it and says what is wrong when it is down.

Paper and live results are read through **separate** commands by design — `report` is paper-only and
`report --live` reads the modules' separate live ledgers, so a calibration reading can never accidentally
include live trades. If you do trade live, `python run.py reconcile` diffs what the suite believes it
holds against what your broker actually shows, and flags anything a paper-only setup shouldn't be holding.

## Staying in the loop

**Push notifications are off by default.** The template's `notify.channels` and
`notify.trade_channels` (and the desk and status-digest channel lists) are all `["log"]`, so out of the
box every alert lands only in the suite's log, and the console shows its own on-screen trade toasts,
which are always on and need no setting. Add `desktop`, `discord` or `slack` to a channel list in
`~/.cherrypick/config.json` to be told elsewhere (Discord and Slack also need their webhook stored with
`secrets-set`, above). The `log` channel always stays on as a floor, so a failed push never means a lost
record. With channels set, you are notified when a paper trade fills and warned if the system stalls, so
it can run unattended. Test any time with `python run.py notify-test`.

Trade pushes go to their own channel set (`notify.trade_channels`) so frequent paper fills don't spam the
warning channels. MEIC can trade often, so its trades can roll up into one periodic per-symbol digest
(`MEIC digest 13:45 ET — SPX: 30 entries (open×10 width-10×10 width-5×10) · 2 exits net +$48 · day 7
trades net +$61`) every `interval_minutes` instead of one push each. Arms whose names start with an entry
in `notify.trade_summary.profile_prefixes` go to the digest (the shipped list includes `control`);
setting `notify.trade_summary.mode` to `summary` sends every MEIC trade there. Other modules push each
trade.

## Where everything lives

Nothing runtime is written into your checkout (apart from the installer's `.venv`). Config, data, logs,
and reports all live under one per-user directory — relocate the whole thing by setting
`CHERRYPICK_HOME`:

```
~/.cherrypick/
  config.json                       # the orchestrator's config, including `capabilities`
  config/<module>.json              # one per module, when you override its example (meic, flies, …)
  config/meic.risk.json             # MEIC's arm registry, when you add arms beyond control
  data/marketdata/stream_cache.db   # the shared market-data cache — one writer, every module reads it
  data/<module>/paper_trades.db     # paper ledger (live ledgers are separate files)
  data/earnings/{earnings,options,stocks}/  # the optional Dolt clones
  data/review/                      # the end-of-day review: fact sets and their renders
  logs/                             # suite + per-module logs
  state/                            # supervisor job state, heartbeats, stream requests
```

Data paths in the config start with `~`, and a module's relative `path` (`../meic`) resolves against
`packages/orchestrator`, so nothing hardcodes a location on your machine. If you set
`CHERRYPICK_HOME`, a relative module `path` resolves against that directory instead, so give absolute
ones. Paper and live ledgers are separate files, never queryable through one connection.

## Good to know

- **Paper by default.** Every engine ships with live trading off, and the orchestrator that schedules
  them never places, cancels, or closes an order itself. Opening a module's live gate is on you — and
  once open, that module's loop trades within its own limits without asking again.
- **Experimental modules are off.** Calendars, PMCC-99 and curve ship switched off, and the desk is not
  installed at all unless you ask for it.
- **Your data stays yours.** Trades and credentials live on your machine (credentials in your operating
  system's secure keyring — never in a plain file).
- **Set-and-forget.** Once installed, it runs on a schedule and recovers from common hiccups by itself.
- **Runs on your computer**, not a cloud service — so the machine needs to stay awake during the sessions
  you want to capture. (`packages/orchestrator/tools/setup-walkaway-durability.ps1` keeps a Windows
  laptop from sleeping mid-session.)

📖 **New here?** [QUICKSTART.md](QUICKSTART.md) gets you running; the [User Guide](docs/PROJECT.md) walks
through settings, daily use, and troubleshooting in plain language. For the full reference —
architecture, the CLI, the reporting stack, configuration, and the safety model — see the
[documentation index](docs/README.md).

## Disclaimer

**For educational and research purposes only.** This software is provided as-is for learning about
market-data collection, paper-trading workflows, and automation. It is **not financial, investment, or
trading advice**, and nothing here is a recommendation to buy or sell any security.

- Trading options and other securities involves **substantial risk of loss** and is not suitable for
  everyone. Paper-trading results do not guarantee — and rarely reflect — real-world performance.
- The project **defaults to paper trading**: it places no live order until you open a module's live
  gate yourself, and once you do, that module trades on its own. If you enable or extend any
  live-trading capability, **you do so entirely at your own risk**.
- The authors and contributors accept **no liability** for any financial loss, data loss, or damages
  arising from use of this software (see the warranty disclaimer in the [LICENSE](LICENSE)).
- This project is **independent** and is not affiliated with, endorsed by, or sponsored by tastytrade,
  DoltHub, or any broker or data provider.

Do your own research and consult a licensed financial professional before making any investment decision.

The full, canonical text is [DISCLAIMER.md](DISCLAIMER.md); where this summary and it differ, it governs.

## Licence

[MIT](LICENSE) © 2026 Jon Covington
