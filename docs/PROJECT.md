# cherrypick — User Guide

A practical walkthrough for options traders. If you can follow a strategy checklist and run a few
commands, you can operate cherrypick.

---

## What cherrypick is (in one minute)

cherrypick lets you **test options strategies on paper, automatically.** You choose the strategies and
symbols, turn it on, and leave it running. On a schedule during market hours it runs the strategies in
simulation, records every would-be trade (with realistic fills and costs), and monitors itself, notifying
you if anything stops working. Later you open a report or dashboard to review how your strategies would have
performed.

Its distinguishing feature is **variance testing**: you can run many parameter variations of a strategy in
parallel and compare which entry rules actually add edge — see
[Risk-profile variance testing](#risk-profile-variance-testing) below.

It comes with four modules:

- **MEIC** — 0DTE **multiple-entry iron condors** on cash/ETF index products (currently XSP + QQQ).
  It scales strike selection to volatility (VIX bands), respects credit floors and OTM
  buffers, applies regime gates (VIX, VIX1D, ATR, and dealer gamma / GEX), manages per-side stops, and
  force-closes or lets positions settle as appropriate.
- **Earnings** — **defined-risk earnings plays** (iron fly, double calendar, iron condor, ATM calendar,
  directional credit spread, broken-wing butterfly). It opens once before the close and
  closes once after the next open, sized to a simulated capital base.
- **Flies** — 0DTE **net-credit butterflies** ("profit forest"): it measures whether the strategy
  makes money net of costs, arm-by-arm, and is built so a negative answer is a usable result. Paper
  by default, with a small, explicitly-armed live pilot also available (see
  [`packages/flies/docs/live-trading-plan.md`](../packages/flies/docs/live-trading-plan.md)).
- **GEX** — a self-hosted read-only **gamma-exposure dashboard** over the shared market-data stream.

By default everything runs in **paper mode** — the automation never places, cancels, or closes a real
order on its own. You can also connect a real tastytrade account for live market data and a read-only
reconciliation check, and (for flies) a deliberately narrow, manually-armed live pilot; see
[Paper and live modes](#paper-and-live-modes).

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
# Download the project
git clone https://github.com/joncovington/cherrypick.git
cd cherrypick

# Install the shared core library first, then the orchestrator
# (scripts/dev-install.ps1 or .sh does this step, and every package's, in one command)
pip install -e packages/core
cd packages/orchestrator
pip install -e ".[dev]"
```

The modules live under `packages/meic`, `packages/earnings`, `packages/flies`, and `packages/gex`
(plus the standalone market-data streamer in `packages/streamer`). The trading engines are installed the
same way if you plan to run them (`pip install -e ".[dev]"` in `packages/meic` or `packages/earnings`;
flies, gex, and the streamer need no install — the orchestrator runs them in place).

Three more surfaces are optional and set up separately when you want them: `packages/console` (the
unified web console — Node/TypeScript, so it installs with `npm`, not pip), `packages/scout`
(screening and strategy exploration), and `packages/desk` (⚠️ **experimental** — the manual trading
desk, which places real orders on your explicit per-order confirmation; read its README in full first).

---

## First-time setup

**1. Adjust your settings.** Create your config from the template and open it in any text editor:

```bash
python run.py init      # writes ~/.cherrypick/config.json from the template
```

`~/.cherrypick/config.json` is where you say **which strategies to run, on what schedule, and how you
want to be alerted**. It lives under your user home (not the repo) and is kept only on your machine.

**2. Link your tastytrade account — one command.** cherrypick stores your credentials in your
operating system's secure keyring (never in a file). Run the wizard once and every engine is
connected:

```bash
python run.py connect
```

It walks you through: your tastytrade login (entered once, input hidden, shared by every
engine), a connection check, picking which account the suite would use if you ever enable
live trading (you confirm; per-engine overrides are possible with `connect --module <name>`),
optional Slack/Discord notifications (Enter skips), and a status panel showing each engine's
setup. If you previously set credentials per engine, the wizard offers to consolidate them
into the shared login so there's one place to rotate. The **GEX** dashboard and the streamer
reuse the same keyring credentials — no separate step.

There is exactly **one** place credentials are set (this wizard) and one shared keyring entry the whole
suite reads, so there is a single point to rotate. Two surfaces limit what that shared login can *do*
rather than holding a login of their own:

- The **console** only ever reads. At boot it probes the token's scope, and a read-only token disables
  its write-oriented functions until a trade-scoped one is rotated in.
- The **desk** (⚠️ experimental — see the live-trading warning below) stores no secrets — it borrows a module's keyring session and
  adds its own **authorization**: a PIN (only a salted verifier is stored), a ticket you confirm per
  order, and its own policy gates. Borrowing credentials is not borrowing permissions, which is how it
  places a discretionary order without touching any module's live-trading switch.

**3. Choose your strategy details.** The fine-grained trading rules — which symbols, target deltas,
credit floors, entry windows, risk profiles — live in each engine's own config:

- MEIC: `~/.cherrypick/config/meic.json` (e.g. the `symbols` list, `min_iv_rank`, entry/exit windows).
  The **risk profiles** themselves live in the repo's `config.risk.json` — see below.
- Earnings: `~/.cherrypick/config/earnings.json` (position caps, entry/close windows, per-strategy
  tuning).

(These per-engine configs live under your user home too. Copy each engine's `config.example.json` into
`~/.cherrypick/config/<engine>.json`; upgrading from an older checkout, `python run.py migrate-home`
moves an existing in-repo config there for you.)

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
own — the full inventory of what got installed, what runs when, and what "healthy" looks like each
morning is the [operations runbook](operations.md):

- **MEIC** evaluates entries roughly once a minute during the session (its
  `paper.tick_interval_seconds`).
- **Earnings** opens plays before the close and closes them after the next open, on daily timers.
- A **monitor** checks in every few minutes and a **fill-notifier** pushes new paper trades to you
  quickly.

To pause everything: `python run.py uninstall`. Your recorded data and settings are untouched — you can
turn it back on any time.

---

## Risk-profile variance testing

This is what most distinguishes cherrypick from a single-strategy paper tool. A **risk profile** is a named
set of parameters — short-strike delta, credit floor, stop policy, regime gates, entry timing, wing width,
symbol. You can define as many as you like, and they all trade **the same live market snapshots in
parallel**, each as its own shadow book. Every recorded trade is tagged with the profile that opened it, so
`report` breaks results down per profile.

The value is in controlled comparison: clone a baseline profile, change **one** parameter, and you can
measure that idea's effect in isolation rather than confounding several changes at once. MEIC's live
profiles are in `packages/meic/config.risk.json`; four are enabled, and they run as a **four-stream
forward test** rather than a ladder:

- **`control`** — the baseline and the `active_profile`. Every other arm differs from it in exactly one
  thing, which is what makes any difference in the results attributable.
- **`open`** — control with the entry cap removed: does taking every qualifying setup beat being
  selective, once costs are paid?
- **`width-5` / `width-10`** — control with the wing width pinned: does a wider wing justify what it
  costs to build?

Read the outcomes two ways. `report` shows **gross P&L** (did the entry select good setups?) alongside
**net** (did it survive commissions and slippage?), because at small size costs can turn a real entry edge
into a net loss. And `calibrate` reports when a profile has met a documented threshold — enough sessions, a
sustained win rate, and a sufficient sample — to justify a step up in risk. Calibration is advisory only; it
never changes your risk settings.

Older profile names still appear in historical reports and are kept in `config.risk.json` (disabled) so
those books stay readable: the **risk ladder** (conservative → moderate → aggressive → very-aggressive)
was the everyday progression until the 2026-08-07 arms cutover replaced it with the forward test above,
and a `large-spx-*` / `small-xsp` family of fixed `(symbol, wing, credit)` cells was removed 2026-07-18.
Neither is live. See
[risk profiles](../packages/meic/docs/risk-profiles.md) and
[paper experiments](../packages/meic/docs/paper-experiments.md) for the complete method.

---

## Reviewing your results

| Command | What you get |
|---|---|
| `python run.py report` | Win rate with **gross and net** P&L (net of commissions and slippage) across strategies and risk profiles. Add `--eod` (today) or `--date YYYY-MM-DD` for one day. |
| `python run.py eod-digest` | An end-of-day write-up for one session across both tools, saved to `logs/eod-digest-<day>.md`. Runs automatically each afternoon (see below) — you rarely need to run it by hand. |
| `python run.py dashboard --serve` | A live dashboard in your browser: overall status, per-strategy P&L, a fee-drag card, a **GEX (gamma-exposure) view** (call/put walls and the gamma-flip point), recent activity, and health checks. |
| `python run.py dashboard` | The same as a single self-contained web page you can open or share. |
| `python run.py calibrate` | Advice on whether a risk profile has collected enough good results to consider stepping up (advisory only — it never changes anything). |

The end-of-day digest is **scheduled automatically when you install** and sends you a one-line summary
each afternoon. If you'd rather not get it, set `"eod_digest": {"enabled": false}` in `~/.cherrypick/config.json` and
re-run install (or uninstall).

---

## Staying informed

Set your alert channels in `~/.cherrypick/config.json` under `notify` — any of **`log`** (always on), **`desktop`**,
**`discord`**, and **`slack`**. You'll get:

- a **notification when a new paper trade fills**, and
- a **warning if something stalls** (e.g. the data feed goes quiet mid-session), so a silent gap
  doesn't go unnoticed.

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

## Paper and live modes

- **Paper (the default, and what the automation runs).** The scheduler, the self-healing monitor, the
  reporting, and all variance testing operate on paper: live market data in, simulated fills out, none of
  your money. The orchestrator never places, cancels, or closes a live order.
- **Live (your account, connected — but you drive it).** `connect` links your real tastytrade account so
  the engines use your live market data and can **reconcile** against your real positions (a read-only
  check that flags anything a paper-only suite shouldn't be holding). `account --module <engine>` shows
  which account an engine would use if run live. Trading for real is a deliberate, manual step you take per
  engine — the automation will never do it for you, and you do so entirely at your own risk (see the
  disclaimer).

Paper and live books are kept strictly separate, and credentials stay in your OS keyring. The
flies module's live pilot adds an extra safeguard on top of this: it has to be armed with a
fresh, explicit confirmation every trading day, and it turns itself off automatically each
evening — there's no way for one day's confirmation to silently carry into the next.

> ### ⚠️ If you are thinking about trading live
>
> **Know every way an order can be placed.** There are four: MEIC, earnings, and flies each have a live
> path behind their own `enable_live_trading` gate, and the desk is the manual one. The first three are
> **loops** — once you open that gate, they trade on their own schedule without asking again. The
> orchestrator never places an order itself, but "the automation won't trade for me" stops being true
> the moment one of those gates is open.
>
> **`packages/desk` is experimental.** It is the newest and least-exercised package here, it has no
> meaningful track record, there is no paper mode for it, and every order it submits is irreversible.
> Read [its documentation](../packages/desk/README.md) in full before you use it, start at the smallest
> size that could possibly matter, and never point it at money you cannot afford to lose outright.
>
> **Nothing in this suite has been validated as profitable.** The paper experiments exist because the
> answer isn't known yet, and a good number of the recorded results so far are negative. Simulated
> fills are also optimistic by construction — the suite's own measurements show live fills costing far
> more than the model assumed — so paper results are not a forecast of live ones.
>
> This is educational and research software, not financial advice. If you enable any live capability
> you do so entirely at your own risk; see the disclaimer at the end of this guide.

## How your account is protected

- **The automation is paper-only.** The scheduled engines run in simulation and read market data. They do
  **not** place, cancel, close, or adjust real orders, and nothing on the schedule ever switches to live
  trading — going live is a separate, manual action you take yourself (see above).
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
  channels in `~/.cherrypick/config.json` and (for Discord/Slack) that you stored the webhook with `secrets-set`.
- **Laptop keeps sleeping.** There's a helper, `tools/setup-walkaway-durability.ps1`, to keep a Windows
  machine awake and running scheduled tasks while you're away.

---

## Disclaimer

For **educational and research purposes only** — **not financial or trading advice.** Options trading
involves substantial risk of loss; paper results do not reflect real-world performance. See the
[Disclaimer in the README](../README.md#disclaimer) and the [LICENSE](../LICENSE) for the full terms.
