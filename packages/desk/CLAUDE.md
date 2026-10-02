# CLAUDE.md

> **⚠️ EXPERIMENTAL PROTOTYPE — FOR EDUCATIONAL PURPOSES ONLY — NOT FINANCIAL ADVICE.** This
> package places **REAL, irreversible orders with real money** on a live brokerage account. It is
> not installed by default, is off behind two separate gates, and has no paper mode. Options
> trading carries substantial risk of loss and is not suitable for all investors. The software can
> fail; its caps and PIN reduce accidents but do not make trading safe, and anything running as your
> OS user can change them. **You alone are responsible for every order and every loss.** Provided
> "as is", without warranty. **Read [DISCLAIMER.md](../../DISCLAIMER.md) before going any further.**

## What this is

**EXPERIMENTAL.** An experimental prototype for educational purposes only, and the least-exercised
code in the repo. It has no track record and no paper mode, and every submitted order is
irreversible. It is the suite's only *discretionary* live-order path (the strategy modules have their
own, behind `enable_live_trading`). Hold changes here to the invariants below rather than to
convenience, and fail closed on any behaviour you are unsure of. User-facing warnings and limits are
in [README.md](README.md).

**cherrypick-desk** is the *manual trading desk*: a foreground, human-initiated CLI for discretionary
orders on the real broker account. It replaces temporarily flipping a module's `enable_live_trading`
for one order. Its authorization is **its own config, its own PIN, and a per-order ticket**, none of
which touch a module's flags: enabling the desk never enables a loop, and enabling a loop never
enables the desk.

It is **not** a strategy module: no loop, no schedule, no paper ledger, no opinion about what to trade.

## Two gates, both required

`desk.json` must hold `"enabled": true` (checked with `is True`) **and** the environment must carry
`CHERRYPICK_DESK_EXPERIMENTAL=1`. With either closed, `propose`, `confirm`, `cancel` and `orders` exit
non-zero (code 2) from `cli._gated_config()` before any network or keyring access, printing the fixed
disabled message plus the disclaimer line. Keep that check the first statement of every
broker-touching command. There is no fallback to `config.example.json`.

## Commands

```bash
# Fresh clone: install packages/core first (see the root CLAUDE.md).
pip install -e packages/desk

cherrypick-desk status                      # gates, config errors, PIN/lockout, halt flag, today's tallies
cherrypick-desk pin-set                     # interactive only; needs the current PIN once one is set
cherrypick-desk pin-clear                   # interactive only; needs the current PIN
cherrypick-desk analyze --order '<json>'    # OFFLINE structure + worst case. No broker, no state
cherrypick-desk propose --order '<json>'    # gates + broker preflight -> ticket + confirmation code
cherrypick-desk confirm --ticket <id> --code <code>   # PIN prompt, re-check everything, submit
cherrypick-desk orders                      # working orders -- read-only, no PIN, still gated
cherrypick-desk cancel --order-id <id>      # PIN prompt, pull a resting order
cherrypick-desk purge                       # drop expired pending and claimed tickets

# Tests (pytest; markers: unit [default lane], live)
python -m pytest                            # default: -m 'not live' -q
ruff check .
```

`tests/conftest.py` points `CHERRYPICK_HOME` at a temp dir and replaces the keyring, the broker
session and the terminal for every test. Keep it that way: no test may reach the real keyring or a
broker.

## Architecture

**Three layers, deliberately separable** — they have different strengths, and conflating them would
oversell the weak one:

- **`policy.py` — the gates.** Pure, no I/O: config, halt flag, resolved account, today's tally and
  the broker preflight in, *every* unmet gate out. This half binds whoever is asking.
- **`ticket.py` + `pin.py` — the human checkpoint.** Propose→confirm where the code is an HMAC of the
  canonical order (every field, exact decimals) under a keyring secret; single-use, expiring, sealed;
  plus a keyring PIN stored only as a PBKDF2 verifier, with a keyring-held lockout counter.
  `keystore.py` is the one seam to the desk's own keyring entries.
- **`journal.py` — blast radius.** Append-only JSONL of every decision, refusals included.

**Config fails closed** (`config.resolve`). Any cap that is not a finite number ≥ 0 — `null`, a
string, `NaN`, a negative — refuses the *whole* file: every field drops to its safe default and
`config_errors` says why. Defaults: $100 per order, $100 buying power per order, 1 spread per order,
2 orders and $200 per ET day, ticket TTL 120 s clamped to 30–300, no accounts, no underlyings.
`policy._cap` refuses a junk cap even in an unresolved dict.

**Risk comes from the payoff diagram, never a strategy-name whitelist** (`order.py`). The diagram is
piecewise-linear with kinks at strikes, so its minimum is exact at `S=0`, at a strike, or at infinity.
`analyze` also refuses anything outside a narrow shape: equity options on one underlying, Day Limit,
positive finite price, `int` quantities, allow-listed keys only, no `stop_trigger`.

**Only a pure cover is exempt from the risk gates** (`RiskProfile.covering_only`: every leg is
"buy to close"). Anything with a "sell to close" leg is scored as it stands, because selling the long
wing of a condor leaves a naked short and the desk sees only the order. Halt flag and both allowlists
still apply to a cover.

**The submit path** (`cli._confirm_claimed`): claim the ticket (spent from here), check seal and TTL,
prompt the PIN, resolve the full account from the masked one, check the code, then under
`submit.lock`: re-run the gates, re-preflight and check buying power, write `submitting` with
`journal.record_strict` (refuse if it fails), submit with `external_identifier = desk-<ticket>`. A
submit that raises is resolved by reading `orders_today` for that identifier, never by resubmitting.
`today_totals` counts `submitting`/`submitted`/`failed`/`uncertain` once per ticket on the ET date.

**`cancel` is exempt from the halt flag.** `policy.evaluate_management` has no `halt_present`
parameter at all: a halt that trapped an account inside a stale working order would misfire in the
direction it exists to prevent. **There is no `replace` command**: cancel then a fresh
`propose`/`confirm` composes two primitives that must already be right.

## Invariants (do not violate — the reasons are load-bearing)

- **The desk never reads or writes any module's config** — not `enable_live_trading`, not
  `account_deploy_limit_pct`, not `gate0_confirmed`. Enforced by `tests/test_isolation.py`.
- **No other package may import or launch `cherrypick.desk`.** The isolation test derives its list
  from `packages/*/` minus this one, plus `scripts/`, skips only `test_*.py`, and flags the package or
  script name inside subprocess/importlib-style calls. Its scanner self-tests plant offending files in
  a temp tree, so each rule is shown to fire.
- **`live=True` appears in exactly one place** (`cli._submit`).
- **The journal format is read by `orchestrator/desk_notifier.py`** as a file. It cards `submitted`
  events with an `order_id` and the masked `account`, keyed on the UTC `ts`. Keep those three fields
  and that event name; new event types are ignored by it.
- **The desk stores no broker secrets.** It borrows a module's keyring service for the OAuth session
  (`broker_keyring_service`). Borrowing credentials is not borrowing permissions.
- **The PIN is never stored, logged, echoed or journaled**, and the confirmation code is never written
  to disk. The journal records the order *fingerprint*.
- **No full account number in any output, ticket or journal line.** `cli.main` passes everything
  through `redact_accounts` and masks every account number the process saw by literal match.
- **Never scheduled.** Never registered as an OS task, never invoked from the watchdog.

### The honest limit — state it, don't paper over it

Any process running as the same OS user can edit `desk.json`, delete the halt flag, set the
environment variable, rewrite the desk's keyring entries and use the borrowed broker credentials
directly. The PIN and the caps protect against accidents and against agents that were never given the
PIN, not against a compromised user account. The confirmation code proves *this exact order was
reviewed*, not *a human reviewed it*. Never restructure the docs to imply otherwise.

## Gotchas

- **`max_loss=None` means "uncomputable", not "zero".** Branch on it explicitly.
- **`spreads` is the GCD of leg quantities**, because a net price is quoted *per spread*: a 2/-4/2
  butterfly at 1.10 costs 220 and counts as 2 against `max_spreads_per_order`.
- **Leg order must not affect the fingerprint** — `ticket.canonical` sorts.
- **Buying power is signed the SDK's way**: a negative `change_in_buying_power` is consumption. A
  preflight without the field is refused, never read as zero.
- **Every failed confirmation spends the ticket** — wrong code, wrong PIN, no TTY, refused gate.
- **Tests use 1,000 PBKDF2 iterations** (conftest) for speed; one test checks the real 600,000.

## Guardrails

The deterministic-over-AI preference applies sharply here: a ticket must mean exactly what it says.
Scratch work goes in a gitignored `.tmp/`, never the repo root.
