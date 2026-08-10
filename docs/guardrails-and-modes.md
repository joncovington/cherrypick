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

Paper and live books are strictly separated: separate SQLite files. Even a paper "dry-run" never calls
`execute_trade` (a dry-run performs a real margin check).

### The four live-order paths, and what actually gates each

Know all four before opening any of them. **They do not share one gate**, and "gated behind
`enable_live_trading`" — which earlier versions of this page said — is true of only two:

| Path | Code | What gates it |
|---|---|---|
| **MEIC** | `meic/live_loop.py`, `live_orders.py` | `enable_live_trading` in MEIC's config, plus its own daily-loss breaker and the suite halt flag. Inert by default and never installed by the orchestrator, but it is a full live loop. |
| **Earnings** | `earnings/tt.py execute_trade --live` | `enable_live_trading` in the earnings config. |
| **Flies** | `flies/live_loop.py`, `live_orders.py` | **Not** `enable_live_trading`. A separate `live.enabled` **and** `live.gate0_confirmed` attestation, **and** a per-day arm record written by `/live-flies-start`, **and** a designated account, **and** the halt flag. Self-disarms every evening. |
| **Desk** | `packages/desk` | **Never reads `enable_live_trading` at all** — deliberately. Its own config `enabled`, an account allowlist, a PIN, a per-order ticket you confirm, and its own `policy.py` gates. ⚠️ Experimental. |

The first three are **loops**: once the gate is open they act on their own schedule without asking
again. The desk is the only discretionary one — it acts because you typed a confirmation.

**What "the orchestrator never places an order" does and doesn't mean.** The orchestrator process
genuinely never calls a place-order path. But its supervisor derives a `<module>-live` job for *any*
module that declares `live.task_name`, and spawns that module's live loop on an interval while it is
armed — so it is the thing that launches the process that trades. Today only flies configures that key;
the mechanism is not flies-specific.

**The flies pilot is the most tightly bounded of the three loops** — one arm, one symbol, re-armed by
hand each trading day. Note the precise concurrency rule: at most one *incomplete* position at a time.
An open short vertical always blocks a new entry; a **completed** fly blocks only while its floor is
negative, so several completed flies can be open at once. See
[`packages/flies/docs/live-trading-plan.md`](../packages/flies/docs/live-trading-plan.md) for the
complete rulebook.

Every other guardrail on this page — masked accounts, keyring-only credentials, no AI/network on a
decision path — applies to all four paths in full.

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

## The second live-config boundary: `settings`

`cherrypick settings` (loopback `:8804`) is the suite's second narrow live-config exception, and its
only mutating HTTP surface — every dashboard here is GET-only. Two things make it safe to run:

- **Guarded live-trading fields are read-only, both ways.** `enable_live_trading`, flies'
  `live.enabled`/`live.gate0_confirmed`, and the live loss/deploy-limit fields are locked in the UI and
  refused server-side on both write paths (a field-level edit and a raw-text save). This surface can
  arm nothing and disarm nothing — the deliberate paths above (`/live-flies-start`, hand-editing a gate
  with the plan doc open) stay the only way to touch them.
- **A secret transits the process once, then is gone.** Unlike `connect`, which never lets the
  orchestrator see a bearer secret at all, a settings POST body necessarily does pass through this
  process — the trade-off for a browser-based secrets UI. It goes straight to
  `CredentialStore.set_secret` / the webhook store and is dropped: never logged, never written to a
  file, never echoed back. Every GET response carries only `secrets_status()` booleans, webhook
  set/not-set strings, and masked account numbers — the same values-never-cross-the-wire contract as
  `connect`/`account`, just enforced per-request instead of by keeping the orchestrator out of the loop.

Because it's a mutating local server, loopback binding alone isn't the whole story: every route checks
the `Host` header (defeats DNS rebinding from a page open in your browser), and every POST additionally
requires the per-session CSRF token baked into the page — the server sends no CORS headers, so a
cross-origin fetch can't reach it regardless. See
[configuration-and-storage.md](configuration-and-storage.md#the-settings-surface) for the config-write
mechanics (byte-offset splicing, backups, `--organize`).

## Load-bearing invariants

**No network / service / AI dependency on the reliability path.** The watchdog → notify path uses only
the stdlib + the OS shell — no MCP, no HTTP client, no AI tooling — so it has no new failure mode. A
**34-hour silent stall** (2026-07-01, from an external streamer dependency) is why this rule exists. The
modules' loop decisions depend only on their local tools + their instructions, for the same reason.

- The AI **EOD insight** does not violate this: it's opt-in, feature-detected, and the watchdog fires it
  **detached** on the module-completion event, strictly **off** the watchdog/paper path, best-effort. The
  deterministic `eod-analysis` remains the guaranteed artifact.
- **That insight run does make an outbound call, by default.** `eod_insight.research_events` defaults to
  true, which grants the agent `WebSearch` (bounded turns) to research upcoming events. It is the one
  sanctioned network exception in the suite, and it sits entirely off the reliability path — but it is
  real, so do not read "no network" as covering it. `"research_events": false` makes the run offline.

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

**The shared-credential model** (see [onboarding-redesign.md](history/onboarding-redesign.md)): the tastytrade
login lives once in the shared `cherrypick-broker` keyring service, which every module's store reads
*through* as a fallback; a module's own service, when set, always wins — that's the override and
per-module rotation layer, and it's what `connect --module` writes. The suite-wide account designation
follows the same shape (shared default, per-module override). Secrets are still only ever typed into
module/core child processes with the tty inherited — the orchestrator process never sees a bearer
secret; the status surfaces (doctor's onboarding line, the Live Ops card) show presence and *source*
(own/shared/missing), never values.

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
