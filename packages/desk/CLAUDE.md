# CLAUDE.md

## What this is

> ⚠️ **EXPERIMENTAL.** Least-exercised code in the repo, no track record, no paper mode, and every
> submitted order is irreversible. It is the suite's only *discretionary* live-order path (the
> strategy modules have their own, behind `enable_live_trading`). Hold changes here to the invariants
> below rather than to convenience, and fail closed on any behaviour you are unsure of. User-facing
> warnings: [README.md](README.md).

**cherrypick-desk** is the *manual trading desk*: a foreground, human-initiated CLI for discretionary
orders on the real broker account. It replaces temporarily flipping a module's `enable_live_trading`
for one order — which stops that flag being a safety control and opens a window in which any process
reading that config could trade live. Its authorization is **its own config, its own PIN, and a
per-order ticket**, none of which touch a module's flags: enabling the desk never enables a loop, and
enabling a loop never enables the desk.

It is **not** a strategy module: no loop, no schedule, no paper ledger, no opinion about what to trade.
It takes an order you already decided on and applies gates to it.

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

`--order` takes the dict shape `cherrypick.core.broker.build_order` consumes, or `-` for JSON on
stdin. `CHERRYPICK_DESK_PIN` is honoured so a PIN never has to enter shell history.

## Architecture

**Three layers, deliberately separable** — they have different strengths, and conflating them would
oversell the weak one:

- **`policy.py` — the gates.** Pure, no I/O: takes the already-read world (config, halt-flag presence,
  resolved account, today's journal tally) and returns *every* unmet gate. This half binds whoever is
  asking; a mistaken automation cannot talk past it.
- **`ticket.py` + `pin.py` — the human checkpoint.** Two-phase propose→confirm where the code is a
  **fingerprint of the order** (change the account, a strike, the price or a size and it changes),
  single-use, expiring, tamper-evident; plus a keyring PIN stored only as a salted PBKDF2 verifier.
- **`journal.py` — blast radius.** Append-only JSONL of every decision, refusals included.

**Risk comes from the payoff diagram, never a strategy-name whitelist** (`order.py`). The diagram is
piecewise-linear with kinks at strikes, so its minimum is exact at `S=0`, at a strike, or at infinity
(caught by a slope test). A name check passes a mislabelled order and refuses a legitimate one.

**Two things make a worst case uncomputable, reported distinctly**: `unbounded` (net short the upside
tail) and `multi_expiry` (a calendar/diagonal, whose far leg still has time value at the near expiry).
Both surface as `max_loss=None`, which callers **must** treat as worse than any cap, never as no risk.

**Closing orders are exempt from the risk cap and the defined-risk requirement** — an all-"to close"
order *removes* exposure (a naive deploy governor once refused a risk-reducing close). The halt flag
and the account allowlist still apply. A roll (`mixed`) establishes new legs and is held to the
opening bar.

**`cancel` is exempt from the halt flag too.** `policy.evaluate_management` (not `evaluate`) gates it:
`desk.enabled` and the account allowlist apply, but there is no `halt_present` parameter at all — a
halt that trapped an account inside a stale working order would misfire in the direction it exists to
prevent. **There is no `replace` command**: cancel then a fresh `propose`/`confirm` composes two
primitives that must already be right, instead of adding a third authorization path.

## Invariants (do not violate — the reasons are load-bearing)

- **The desk never reads or writes any module's config** — not `enable_live_trading`, not
  `account_deploy_limit_pct`, not `gate0_confirmed`. Enforced by `tests/test_isolation.py` (AST scan,
  so prose explaining the rule doesn't trip it).
- **No automated package may import `cherrypick.desk`**, so the submit path stays unreachable from
  unattended code. Enforced by the same test across every automated package.
- **`live=True` appears in exactly one place** (`cli.py`): one auditable line where real money moves.
- **Fail-closed everywhere.** A missing or corrupt config, an unreachable broker, a damaged keyring
  entry, an unparseable order and an unreadable ticket all *refuse*. Absent config keys land on
  disabled / no accounts / defined-risk required / $500. An explicit `null` cap is a deliberate choice;
  an absent key cannot disable the cap.
- **The desk stores no broker secrets.** It borrows a module's keyring service for the OAuth session
  (`broker_keyring_service`). Borrowing credentials is not borrowing permissions. Secrets live in the
  OS keyring only.
- **The PIN is never stored, logged, echoed or journaled** — only its salted PBKDF2 verifier, in the
  keyring. The journal records the order *fingerprint*, never the confirmation code.
- **A refusal naming a disallowed account must not leak the number** — masking applies to every
  output, refusal message and journal line.
- **Never scheduled.** Never registered as an OS task, never invoked from the watchdog.

### The honest limit — state it, don't paper over it

An agent that runs `propose` can read the confirmation code off its own output, so the code proves
*this exact order was reviewed*, not *a human reviewed it*. The PIN raises the bar (a process never
given it cannot submit) and gives non-repudiation, but an agent handed a PIN has seen it. **The gates
in `policy.py` are what actually constrain that case**, which is why they are pure, total and tested to
fail closed. Never restructure the docs to imply the ticket alone is a human gate.

## Gotchas

- **`max_loss=None` means "uncomputable", not "zero".** Branch on it explicitly;
  `RiskProfile.unbounded` is the readable form.
- **`spreads` is the GCD of leg quantities**, because a net price is quoted *per spread*: a 2/-4/2
  butterfly at 1.10 costs 220, not 110.
- **Leg order must not affect the fingerprint** — the broker echoes legs in its own order.
  `ticket.canonical` sorts.
- **A failed confirmation still spends the ticket**, so a wrong code cannot be retried; the human
  re-proposes, which re-runs every gate against current state.

## Guardrails

The deterministic-over-AI preference applies sharply here: a ticket must mean exactly what it says.
Scratch work goes in a gitignored `.tmp/`, never the repo root.
