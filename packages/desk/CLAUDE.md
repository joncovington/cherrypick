# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

> ⚠️ **EXPERIMENTAL.** Least-exercised code in the repo, no meaningful track record, no paper mode, and
> every submitted order is irreversible. It is not the suite's only live-order path — MEIC, earnings,
> and flies each have one behind `enable_live_trading` — but it is the only *discretionary* one.
> Changes here are changes to the one path where real money moves — hold them to the invariants below
> rather than to convenience, and prefer failing closed over any behaviour you are unsure of. The
> user-facing warnings live in [README.md](README.md).

**cherrypick-desk** is the suite's *manual trading desk*: a foreground, human-initiated CLI for
placing discretionary orders on the real broker account. It exists because the alternative that grew
up in its absence was worse — temporarily flipping a module's `enable_live_trading` (a guarded,
plan-gated flag) for the duration of one order, then flipping it back. That works, but it means the
flag stops being a safety control, and every toggle is a window in which any other process reading
that config could also trade live.

The desk replaces that with an authorization path of its own: **its own config, its own PIN, and a
per-order ticket** — none of which touch any module's flags. Enabling the desk never enables an
automated loop; enabling a loop never enables the desk.

It is **not** a strategy module. It has no loop, no schedule, no paper ledger, and no opinion about
what to trade. It takes an order you already decided on and applies gates to it.

## Commands

```bash
# Fresh clone: install packages/core first (see the root CLAUDE.md).
pip install -e packages/desk

cherrypick-desk status                      # config, PIN presence, halt flag, today's tallies
cherrypick-desk pin-set                     # prompts without echo; --pin for non-interactive
cherrypick-desk analyze --order '<json>'    # OFFLINE structure + worst case. No broker, no state
cherrypick-desk propose --order '<json>'    # gates + broker preflight -> ticket + confirmation code
cherrypick-desk confirm --ticket <id> --code <code> --pin <pin>   # re-check everything, submit
cherrypick-desk orders                      # working orders on the resolved account -- read-only, no PIN
cherrypick-desk cancel --order-id <id> --pin <pin>   # pull a resting order
cherrypick-desk purge                       # drop expired pending tickets

# Tests (pytest; markers: unit [default lane], live)
python -m pytest                            # default: -m 'not live' -q
ruff check .
```

`--order` takes the same dict shape `cherrypick.core.broker.build_order` consumes, or `-` to read
JSON from stdin. `CHERRYPICK_DESK_PIN` is honored so a PIN never has to enter shell history.

## Architecture

**Three layers, deliberately separable.** The security story is layered because the layers have
genuinely different strengths, and conflating them would oversell the weak one:

- **`policy.py` — the gates.** Pure, no I/O. Takes the already-read world (config, halt-flag
  presence, resolved account, today's journal tally) and returns *every* unmet gate. This is the
  half that binds regardless of who is asking, and the half a mistaken automation cannot talk past.
- **`ticket.py` + `pin.py` — the human checkpoint.** Two-phase propose→confirm where the code is a
  **fingerprint of the order** (change the account, a strike, the price, or a size and the code
  changes), single-use, expiring, tamper-evident; plus a keyring-held PIN stored only as a salted
  PBKDF2 verifier.
- **`journal.py` — blast radius.** Append-only JSONL of every decision, refusals included.

**Risk comes from the payoff diagram, never a strategy-name whitelist** (`order.py`). The diagram is
piecewise-linear with kinks only at strikes, so its minimum is exact at `S=0`, at a strike, or at
infinity — and infinity is caught by a slope test. A name-based check ("is this an iron condor?")
passes a mislabeled order and refuses a legitimate structure nobody named; the payoff does not.

**Two things make a worst case uncomputable**, and they are reported distinctly because they read
completely differently to a human: `unbounded` (net short the upside tail) and `multi_expiry` (a
calendar/diagonal — the far leg still carries time value at the near expiry, which no single-expiry
diagram can know). Both surface as `max_loss=None`, which callers **must** treat as "worse than any
cap", never as "no risk".

**Closing orders are classified and exempted on purpose.** An order whose every leg is "to close"
*removes* exposure, so the risk cap and the defined-risk requirement do not apply to it (the halt
flag and the account allowlist still do). This is the concrete failure that motivated the package: a
naive account-level deploy governor refused a risk-*reducing* BKNG close because it only knew "more
buying power consumed = bad". A roll (`mixed`) is held to the opening bar — it establishes new legs.

**`cancel` is exempt from the halt flag too, for the same reason, one step earlier in an order's
life.** `policy.evaluate_management` (not `evaluate`) gates it: `desk.enabled` and the account
allowlist still apply, but there is no `halt_present` parameter to check at all, because pulling a
resting order only reduces exposure — a halt that trapped an account inside a stale working order
would be the safety flag misfiring in the direction it exists to prevent. **There is no `replace`
command.** Repricing an order in place would need its own answer to "does the new spec still mean
the same position", which `propose`/`confirm` already answer for every order they see; `cancel` then
a fresh `propose`/`confirm` gets a repriced working order by composing two primitives this package
already has to get right, rather than adding a third authorization path with its own risk surface.

## Invariants (do not violate — the reasons are load-bearing)

- **The desk never reads or writes any module's config.** Not `enable_live_trading`, not
  `account_deploy_limit_pct`, not `gate0_confirmed`. Its authorization is its own. Enforced by
  `tests/test_isolation.py` (AST scan, so prose explaining the rule doesn't trip it).
- **No automated package may import `cherrypick.desk`.** The submit path must stay unreachable from
  scheduled, unattended code — a loop that imported it could call the submit helper on its own
  schedule with no human and no ticket. Enforced by the same test across every automated package.
- **`live=True` appears in exactly one place** (`cli.py`), so there is a single auditable line where
  real money can move — mirroring `core.broker`'s own "a live order is placed on exactly one path".
- **Fail-closed everywhere.** A missing config, a corrupt config, an unreachable broker, a damaged
  keyring entry, an unparseable order, and an unreadable ticket all *refuse*. Absent config keys land
  on disabled/no-accounts/defined-risk-required/$500. An explicit `null` cap is a deliberate choice
  and is distinguished from an absent key, which cannot disable the cap.
- **The desk stores no broker secrets.** It borrows an existing module's keyring service for the
  OAuth session (`broker_keyring_service`). Borrowing credentials is not borrowing permissions.
- **The PIN is never stored, logged, echoed, or journaled.** Only a salted PBKDF2 verifier goes in
  the keyring. The journal records the order *fingerprint*, never the confirmation code.
- **Account numbers are masked to `****1234`** in every output, refusal message, and journal line
  (suite-wide rule). A refusal naming a disallowed account must not leak the number.
- **Never scheduled.** This CLI is foreground and human-initiated. It must never be registered as an
  OS task or invoked from the watchdog.

### The honest limit — state it, don't paper over it

An agent that runs `propose` can read the confirmation code off its own output, so the code proves
*this exact order was reviewed*, not *a human reviewed it*. The PIN raises that bar (a process never
given it cannot submit) and gives non-repudiation in the journal — but an agent handed a PIN has
seen it. **The gates in `policy.py` are what actually constrain that case**, which is why they are
pure, total, and tested to fail closed. Do not restructure the docs to imply the ticket alone is a
human gate; it isn't, and someone will rely on it.

## Gotchas

- **`max_loss=None` means "uncomputable", not "zero".** Every consumer must branch on it explicitly.
  `RiskProfile.unbounded` is the readable form.
- **`spreads` is the GCD of leg quantities**, because a net price is quoted *per spread*: a 2/-4/2
  butterfly at 1.10 costs 220, not 110. Getting this wrong halves every risk number.
- **Leg order must not affect the fingerprint** — the broker echoes legs back in its own order, and
  a fingerprint sensitive to ordering would fail legitimate confirmations. `ticket.canonical` sorts.
- **A failed confirmation still spends the ticket.** Otherwise a wrong code could be retried against
  a live ticket; instead the human re-proposes, which re-runs every gate against current state.

## Suite-wide guardrails

Inherited from the root `CLAUDE.md` and every sibling package: instruction files hold no code;
account numbers masked to `****1234`; portable paths only (never hardcode `C:\Users\...`); secrets in
the OS keyring only; human-voice docs and commits with no AI attribution; deterministic solutions
preferred over AI/agentic ones (root file) — sharply so here, since this is the discretionary LIVE
order path and a ticket must mean exactly what it says. Scratch work goes in a gitignored `.tmp/`,
never the repo root.
