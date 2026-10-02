# cherrypick-desk

> **⚠️ EXPERIMENTAL PROTOTYPE — FOR EDUCATIONAL PURPOSES ONLY — NOT FINANCIAL ADVICE.** This
> package places **REAL, irreversible orders with real money** on a live brokerage account. It is
> not installed by default, is off behind two separate gates, and has no paper mode. Options
> trading carries substantial risk of loss and is not suitable for all investors. The software can
> fail; its caps and PIN reduce accidents but do not make trading safe, and anything running as your
> OS user can change them. **You alone are responsible for every order and every loss.** Provided
> "as is", without warranty. **Read [DISCLAIMER.md](../../DISCLAIMER.md) before going any further.**

**EXPERIMENTAL.** An experimental prototype for educational purposes only, and the suite's manual
trading desk: a foreground CLI for placing discretionary orders on a real broker account. It is
**off by default**, behind two separate gates. With both open it places **real, irreversible
orders**. There is no paper mode, no undo and no simulation path.

It is the newest and least-exercised package in the suite, and nothing in this suite has been shown
to be profitable. Read this whole file before you turn it on. If you use it, you do so at your own
risk, and nothing here is financial advice. The repository's disclaimer applies in full.

## Why it exists

Before the desk, placing one discretionary order meant temporarily flipping a module's
`enable_live_trading` and flipping it back. That works, but it turns the flag into a convenience
rather than a safety control, and while it is flipped any other process reading that config can
also trade live.

The desk has its own config, its own PIN and a per-order ticket. **Enabling the desk never enables
an automated loop, and enabling a loop never enables the desk.** MEIC, earnings, flies and bwb each
have their own live loop behind their own gate; this is the only *discretionary* path.

## Turning it on

The suite installer leaves the desk out. To include it, run the installer with `-WithDesk` (Windows,
`install.ps1`) or `--with-desk` (`install.sh`), or install it by hand as below.

Both gates must be open. Either one alone does nothing.

1. **The config gate.** `~/.cherrypick/config/desk.json` must contain `"enabled": true` (a literal
   JSON `true`; `"yes"`, `1` and `"true"` all read as off), plus at least one account in
   `allowed_accounts` and one symbol in `allowed_underlyings`.
2. **The environment gate.** `CHERRYPICK_DESK_EXPERIMENTAL=1` must be exported in the shell that
   runs the command.

With either gate closed, `propose`, `confirm`, `cancel` and `orders` exit non-zero before touching
the network or the keyring, and say why. `status`, `analyze`, `purge` and the PIN commands keep
working. The desk never falls back to `config.example.json`.

The desk stores no broker secrets of its own. It borrows a module's keyring session, named by
`broker_keyring_service` (`meicagent` by default), so that module's credentials must already be set
up. Borrowing the credentials does not borrow that module's permissions: every desk gate still
applies.

```bash
pip install -e packages/core && pip install -e packages/desk

cp packages/desk/config.example.json ~/.cherrypick/config/desk.json
$EDITOR ~/.cherrypick/config/desk.json     # enabled, allowed_accounts, allowed_underlyings
cherrypick-desk pin-set                    # interactive, at least 10 characters
export CHERRYPICK_DESK_EXPERIMENTAL=1

cherrypick-desk status                     # shows both gates and anything refused in desk.json
```

## Placing an order

```bash
# 1. Inspect it offline first: no broker, no state written, nothing to undo.
cherrypick-desk analyze --order '{
  "price": 0.90, "price_effect": "debit",
  "legs": [
    {"instrument_type":"Equity Option","symbol":"XYZ   260807C00085000","action":"buy to open","quantity":1},
    {"instrument_type":"Equity Option","symbol":"XYZ   260807C00091000","action":"sell to open","quantity":2},
    {"instrument_type":"Equity Option","symbol":"XYZ   260807C00097000","action":"buy to open","quantity":1}
  ]}'
# -> max_loss 90.0, max_gain 510.0, breakevens [85.9, 96.1]

# 2. Run the gates and the broker's own preflight. Returns a ticket and a confirmation code.
cherrypick-desk propose --order '<same json>'

# 3. Confirm. Prompts for the PIN, re-runs every gate against current state, then submits.
cherrypick-desk confirm --ticket <ticket_id> --code <code>
```

`--order` accepts `-` to read JSON from stdin. The accepted shape is deliberately narrow: equity
options on one underlying, a Day Limit order with a positive price, whole-number quantities, and no
fields beyond `legs`, `price`, `price_effect`, `order_type` and `time_in_force`. Stop orders are
refused.

## What stops a bad order

| Gate | Default |
| --- | --- |
| `enabled` in desk.json | `false` (only a literal `true` opens it) |
| `CHERRYPICK_DESK_EXPERIMENTAL=1` | unset |
| `allowed_accounts` (last 4 digits) | empty, which refuses everything |
| `allowed_underlyings` | empty, which refuses everything |
| Suite halt flag (`state/halt-live.flag`) | vetoes every order while present |
| `require_defined_risk` | `true` (only a literal `false` turns it off) |
| `max_order_risk_dollars` (worst case at expiry) | $100 |
| `max_order_buying_power_dollars` (broker preflight) | $100 |
| `max_spreads_per_order` | 1 |
| `max_orders_per_day` (ET trading date) | 2 |
| `max_daily_risk_dollars` (ET trading date) | $200 |
| `ticket_ttl_seconds` | 120, clamped to 30–300 |
| Broker preflight | must be error-free and report a buying-power change |

**Caps fail closed.** Every cap must be a finite number of zero or more. There is no "no cap"
setting: `null`, a string, a negative, `NaN` or `Infinity` refuses the *whole* config, and
`status` lists what was wrong.

**Worst case comes from the expiry payoff diagram**, not a strategy-name whitelist, so ratios,
broken wings and unnamed structures are all scored. An unbounded upside tail or a multi-expiry
order (a calendar or diagonal) has no computable worst case and is refused.

**Only a pure cover is exempt from the risk gates.** An order whose every leg is "buy to close" can
only remove exposure, so the caps and the defined-risk rule skip it. The halt flag and both
allowlists still apply. "Sell to close" is not exempt: selling the long wing of an iron condor
leaves a naked short, and the desk scores the order it is given, not the position behind it.

**Daily caps count attempts**, not just fills. A submit that raised may still have reached the
broker, so placed, failed and uncertain submissions all count. Refused and merely proposed orders
cost nothing.

## The PIN

- Read from an interactive terminal with `getpass`, and nowhere else. There is no `--pin` flag and
  no PIN environment variable, and a non-interactive caller is refused.
- At least 10 characters. Stored only as a salted PBKDF2-SHA256 verifier (600,000 iterations) in
  the OS keyring.
- Changing or clearing it needs the current PIN.
- Five wrong PINs within an hour lock it for an hour. The counter lives in the keyring next to the
  verifier.
- A wrong PIN spends the ticket. So do a wrong code, a refused gate and a failed submit; you
  re-propose, which re-runs everything against the current state.
- PIN changes and lockouts are journaled. The PIN itself never is.

## Submitting

`confirm` holds a lock across the cap check and the submit, so two confirmations cannot both spend
the same daily budget. Before an order leaves, a `submitting` line is written to the journal and
synced, and if that write fails the order is not sent. Every order carries the identifier
`desk-<ticket>`. If the submit raises, the desk reads today's orders back and looks for that
identifier rather than retrying. Found means placed, absent means failed, and a failed read-back is
reported as **uncertain**: check `cherrypick-desk orders` or the broker before doing anything else.

Tickets hold only the masked account. The full number is resolved from the broker at confirm. The
confirmation code is an HMAC of the order under a key kept in the keyring, and is never written to
disk. All output passes through the suite's account redactor.

The audit journal is append-only JSONL at `state/desk/journal.jsonl`. It records refusals as
faithfully as submissions, with account numbers masked.

## Honest limits

Read these as plainly as they are written.

- **Anything running as your OS user can defeat the desk's own controls.** It can edit `desk.json`,
  delete the halt flag, export the environment variable, read and rewrite the desk's keyring entries
  (the PIN verifier, the lockout counter, the ticket key), and use the borrowed broker credentials
  directly without going through the desk at all. The PIN and the caps protect against accidents and
  against agents that were never given the PIN. They do not protect against a compromised user
  account.
- **The confirmation code proves which order was reviewed, not that a person reviewed it.** Whatever
  ran `propose` can read the code off its own output.
- **An agent that has been handed the PIN has the PIN.** "Interactive terminal only" keeps the PIN
  out of flags, environment and pipes, but a process that drives a pseudo-terminal can still type
  it.
- **The caps bound one order and one day, not your judgement.** A defined-risk position can still
  lose its whole worst case.

## Development

```bash
python -m pytest      # default lane: -m 'not live'
ruff check .
```

See `CLAUDE.md` for the architecture and the invariants, including the isolation test that checks
no other package imports or launches this one.
