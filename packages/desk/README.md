# cherrypick-desk

> ## ⚠️ EXPERIMENTAL — this places real orders with real money
>
> This is the newest and least-exercised package in the suite, and it has no meaningful track record.
> Every order it submits is **irreversible**: there is no undo, no paper mode, and no simulation path —
> `--live` here means live.
>
> It is not the only live-order path in the suite (MEIC, earnings, and flies each have one behind their
> own `enable_live_trading` gate), but it is the only **discretionary** one — the others are loops that
> act on a schedule, this one acts because you typed a confirmation.
>
> Read this file in full before using it. Then:
>
> - **Start at the smallest size that could possibly matter**, and stay there until you have your own
>   evidence that the desk behaves the way you expect.
> - **Never use it with capital you cannot afford to lose outright.** Options can lose their entire
>   value, and the defined-risk requirement below bounds a *position's* loss, not your judgment.
> - **Check the ticket every time.** The confirmation step is the last thing standing between a typo
>   and a filled order; the gates in `policy.py` are what constrain everything else.
> - **Nothing in this suite is validated as profitable.** The rest of cherrypick is a paper experiment
>   whose results are, so far, substantially negative. The desk does not change that.
>
> The suite as a whole is for **education and research** and is **not financial advice**. If you use
> this, you do so entirely at your own risk — see the [disclaimer](../../README.md#disclaimer).

The suite's **manual trading desk** — a foreground CLI for placing discretionary orders on the real
broker account, authorized entirely on its own terms.

It exists to replace a habit that had grown up in its absence: temporarily flipping a module's
`enable_live_trading` (a guarded, plan-gated flag) for the length of one order, then flipping it
back. That works, but it means the flag has stopped being a safety control, and every toggle opens a
window in which any other process reading that config could also trade live.

The desk has its own config, its own PIN, and a per-order ticket. **Enabling the desk never enables
an automated loop, and enabling a loop never enables the desk.**

## Quick start

```bash
pip install -e packages/core && pip install -e packages/desk

cp packages/desk/config.example.json ~/.cherrypick/config/desk.json
$EDITOR ~/.cherrypick/config/desk.json     # set enabled + allowed_accounts
cherrypick-desk pin-set                    # prompts without echo

cherrypick-desk status
```

## Placing an order

```bash
# 1. Inspect it offline first — no broker, no state written, nothing to undo.
cherrypick-desk analyze --order '{
  "price": 1.10, "price_effect": "debit",
  "legs": [
    {"instrument_type":"Equity Option","symbol":"XYZ   260807C00085000","action":"buy to open","quantity":1},
    {"instrument_type":"Equity Option","symbol":"XYZ   260807C00091000","action":"sell to open","quantity":2},
    {"instrument_type":"Equity Option","symbol":"XYZ   260807C00097000","action":"buy to open","quantity":1}
  ]}'
# -> max_loss 110.0, max_gain 490.0, breakevens [86.1, 95.9]

# 2. Run the gates + the broker's own preflight. Returns a ticket and a confirmation code.
cherrypick-desk propose --order '<same json>'
# -> ticket_id "9f2c1a04", confirmation_code "K7MQP3", expires in 180s

# 3. Confirm. Re-runs every gate against CURRENT state, then submits.
CHERRYPICK_DESK_PIN='...' cherrypick-desk confirm --ticket 9f2c1a04 --code K7MQP3
```

`--order` accepts `-` to read JSON from stdin. `CHERRYPICK_DESK_PIN` keeps the PIN out of shell
history.

## What stops a bad order

**Policy gates** (pure, total, and the part that binds no matter who is asking):

| Gate | Default |
| --- | --- |
| `desk.enabled` | off |
| Account allowlist (last-4) | empty — refuses everything |
| Suite halt flag (`state/halt-live.flag`) | vetoes everything when present |
| Defined risk required | on, configurable |
| Per-order worst case | $500 |
| Daily order / risk caps | off unless set |
| Broker preflight | must be error-free |

Worst case comes from the **expiry payoff diagram**, not a strategy-name whitelist — so ratios,
broken wings, and structures nobody named are all scored correctly. Two situations make it
uncomputable and are reported distinctly: an unbounded upside tail, and a calendar/diagonal (where a
single-expiry diagram is the wrong model). Both refuse by default.

**Closing orders are exempt from the risk gates** (never from the halt flag or the allowlist). An
order that only closes *removes* exposure; a cap that blocks it is the cap misfiring — which is
exactly what an account-level deploy governor did to a risk-reducing close, and the reason this
package exists.

**Ticket + PIN** (the human checkpoint): the confirmation code is a fingerprint of the order, so it
can only ever ratify the exact order that produced it; single-use, expiring, and tamper-evident. The
PIN lives in the OS keyring as a salted PBKDF2 verifier — never stored, logged, or journaled raw.

**Audit journal**: append-only JSONL at `state/desk/journal.jsonl`, recording refusals as faithfully
as submissions, with account numbers masked.

### An honest limit

An agent that runs `propose` can read the confirmation code off its own output. The code proves
*this exact order was reviewed* — not *a human reviewed it*. The PIN raises that bar and gives
non-repudiation, but an agent handed a PIN has seen it. **The policy gates are what actually
constrain that case**, which is why they are pure, total, and tested to fail closed. Don't rely on
the ticket alone as a human gate.

## Development

```bash
python -m pytest      # default lane: -m 'not live'
ruff check .
```

See `CLAUDE.md` for the architecture and the invariants (including the isolation tests that assert
no automated package can import this one).
