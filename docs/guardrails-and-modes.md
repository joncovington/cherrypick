# Guardrails & modes

The safety model. These are not style preferences — several are **load-bearing invariants** with
incident history behind them. If you extend the suite, preserve them.

## Paper vs. live

- **Paper (the default — and what the automation runs).** The scheduler, the watchdog/self-healing, the
  reporting, and all variance testing operate on paper: live market data in, simulated fills out, **none
  of your money**. The orchestrator only ever invokes paper engines / paper DBs, and **never places,
  cancels, closes, or adjusts a live order** — by design it can't sit on a trading decision.
- **Live (your account, connected — but you drive it).** You link a real tastytrade account so the engines
  use *your* live market data and can be **reconciled** against your real positions. Trading for real is
  a **deliberate, manual** action you take per module — the automation never does it for you, and if you
  go there you do so **entirely at your own risk**.

Paper and live books are strictly separated: separate SQLite files, and a module's live-order tools are
gated behind `enable_live_trading: true`. Even a paper "dry-run" never calls `execute_trade` (a dry-run
performs a real margin check).

## The one live-config boundary: `connect` / `account`

The **only** live-adjacent action the orchestrator performs is onboarding *configuration*:

- `connect --module <m>` runs the module's **own** hidden-input credential tool for the OAuth bearer
  secrets — the orchestrator never sees or stores `client_secret` / `refresh_token`.
- `account --module <m>` selects **which account** a module trades in when live, writing that account's
  `ACCOUNT_NUMBER` into the module's keyring (service = its `keyring_service`).

The boundary is strict: it still **never** places/cancels/closes/adjusts an order, never flips
`enable_live_trading`, never runs a module's live engine, and never edits a module's code/config files.
Account writes are human-confirmed. `reconcile` honors the designation — a designated live account is
*expected* to hold positions (not flagged as drift).

## Load-bearing invariants

**No network / service / AI dependency on the reliability path.** The watchdog → notify path uses only
the stdlib + the OS shell — no MCP, no HTTP client, no AI tooling — so it has no new failure mode. A
**34-hour silent stall** (2026-07-01, from an external streamer dependency) is why this rule exists. The
modules' loop decisions depend only on their local tools + their instructions, for the same reason.

- The AI **EOD insight** does not violate this: it's opt-in, feature-detected, and runs on a **separate**
  scheduled task strictly **off** the watchdog/paper path, best-effort. The deterministic `eod-analysis`
  remains the guaranteed artifact.

**Read surfaces read files, never the broker.** `report`/`calibrate`/`dashboard`/EOD reports read paper
DBs (SQLite read-only), watchdog state, logs, and report files. The static dashboard render reads the
watchdog **heartbeat** for health rather than re-running `doctor`. The few broker-touching cards
(`/api/system`, `/api/reconcile`, module iframes) live **only** on the served path and never on the static
regen.

**The watchdog's only trading-adjacent action is benign, non-trading remediation** — restart a dead
streamer or a dead managed service. It never places, cancels, or closes an order.

**Account numbers are masked** to the last 4 digits (`****1234`) anywhere they surface in logs or output;
only the write to the keyring uses the full number.

**Credentials in the OS keyring only** — broker OAuth tokens (in the modules) and Slack/Discord webhooks
(in the orchestrator) live in the OS keyring, never in files, env vars, or logs.

**Portable paths, disciplined layout.** Never hardcode absolute paths, usernames, hostnames (except
`127.0.0.1`/`localhost`), or drive letters — derive from `Path(__file__)`, an env var, or config. Runtime
files live under `~/.cherrypick`, not the checkout; scratch work goes in a gitignored `.tmp/`.

**Best-effort side calls never break the reliability path.** The watchdog tick fires `trade_notifier.run`
and `dashboard.render` inside `try/except`; a push/render hiccup must not fail the health check. Preserve
this pattern for any tick-time work.

## Strategy-level risk rules

- **Earnings is defined-risk only.** Naked/undefined-risk strategies were removed — an unmonitored
  overnight naked short can blow out arbitrarily. Max loss is known at entry for every strategy.
- **MEIC has no profit target.** A condor exits only by a per-side stop, a time/event force-close, or
  cash-settled expiration. Don't add a `profit_target_pct` (ORB keeps its own, separately).
- **Correlation risk is not currently guarded** in either engine. Trading correlated underlyings (MEIC:
  SPX + XSP move together; Earnings: same-sector/same-date names) can silently multiply exposure. Do not
  configure correlated combinations until a guard exists.

## Disclaimer

For **educational and research purposes only** — **not financial, investment, or trading advice.** Options
trading involves substantial risk of loss; paper-trading results do not reflect real-world performance.
The project defaults to paper and never places live orders on its own; any live-trading use is entirely
at your own risk. See the [README disclaimer](../README.md#disclaimer) and the [LICENSE](../LICENSE).
