# Onboarding redesign — one login, one command

**Status: decided 2026-07-28; steps 1, 2, and 4 implemented** (shared service + CLI in
core.auth, all three module stores read through it, suite-wide designation via `account`
without `--module`). The wizard (3), status panel (5), and config defaults (6) remain.

**Decisions on the open questions:** (1) the wizard **migrates** existing module-service
secrets into the shared service — copies deleted so one rotation point remains; the migrate
command still refuses to silently clobber a *different* shared value (reported as a
conflict, `--overwrite` is deliberate). (2) Webhooks are **opt-in** (Enter skips).
(3) `doctor` goes **yellow, not red**, on missing credentials in a paper-only setup.

**Status: proposed.** A plan for collapsing the suite's secrets-and-account workflow into
something an average user can complete in one sitting without knowing the package layout.

## The problem, as actually lived

Setting up broker access today for a three-module suite means:

1. `cd packages/meic && python src/tt.py secrets_set` — enter client_secret + refresh_token.
2. `cd packages/earnings && python src/tt.py secrets_set` — enter the **same two values again**
   (same brokerage login, different keyring service).
3. `cd packages/flies && python src/credentials.py secrets_set` — the same two values a
   **third** time, via a third, differently-named tool.
4. Hand-edit the machine-local `config.json` to add `keyring_service` / `broker_tool` keys
   the example config knows but an existing config doesn't.
5. `python run.py account --module meic --set <last4>` — designate the live account.
6. Repeat step 5 per module, even when the login has exactly one account.
7. Separately: `python run.py secrets-set --channel slack` for webhooks.

Seven steps, four CLIs, three identical secret entries, one manual JSON edit — to express two
facts: *this is my tastytrade login* and *this is the account the suite may use*. Every step
exists for a defensible reason (module independence, per-module blast radius, delegation so
the orchestrator never touches bearer secrets), but the composition is expert-only.

## Design principles (what must survive the redesign)

- **Secrets live in the OS keyring only** — never files, env vars, argv, or logs; input hidden.
- **The orchestrator process never sees a bearer secret.** Entry happens in a child process
  with the tty inherited; only keyring *status* (present/absent) crosses back.
- **Account designation stays human-confirmed** — it is the suite's one live-config action.
- **Modules keep working standalone.** A module cloned without the orchestrator must still be
  able to set and read its own credentials.
- **Per-module override survives.** If a module ever needs distinct credentials (a second
  login, a sandbox account), the per-module service must still win.

## Piece 1 — the shared-credential model (the structural fix)

The root cause of the triple entry is that each module owns a keyring service
(`meicagent`, `earningsagent`, `fliesagent`) holding **identical values**. The fix uses
machinery that already exists: `CredentialStore` already supports read-through fallback
(`legacy_service_names`, built for MEIC's pre-rename `tastytrade-mcp` entries).

- `cherrypick.core.auth` gains a well-known shared service: `SHARED_SERVICE =
  "cherrypick-broker"`, holding the same three keys (client_secret, refresh_token,
  account_number).
- Every module's store appends it to the fallback chain:
  `CredentialStore("meicagent", legacy_service_names=("tastytrade-mcp", SHARED_SERVICE))`.
  Reads resolve module-service-first, then shared. **Writes keep going to the module
  service**, exactly as today — so a per-module override is simply "set it, and it wins",
  and rotation of an override doesn't touch anyone else.
- A tiny core CLI (`python -m cherrypick.core.auth setup`) writes the shared service with
  hidden input. It is core code run as a child process, so the orchestrator-never-sees-
  secrets property is preserved by the same mechanism `connect` uses today.

Result: enter the login once, every module authenticates. Nothing existing breaks — modules
with per-service secrets already set keep reading their own.

**Account designation follows the same shape.** `account_number` in the shared service is
the suite-wide default; a per-module designation overrides it. `reconcile`'s designated-set
union and the Live Ops card pick this up with no change beyond reading through the same
fallback. For the common case — one login, one account, whole suite — designation becomes
one confirmation instead of one per module.

## Piece 2 — the wizard (`cherrypick connect`, no `--module`)

Today `connect` is per-module. With no `--module` it becomes the suite wizard:

    $ python run.py connect

    [1/4] tastytrade login (stored once, shared by every module; input hidden)
          client_secret:  ········
          refresh_token:  ········
    [2/4] verifying connection…        connected (1 account)
    [3/4] live-trading account designation (suite-wide default)
          1) ****2375  Traditional IRA
          Designate ****2375 as the suite's live account? [y/N]: y
          note: this is an IRA — confirm your options approval level covers defined-risk spreads.
    [4/4] notifications (optional, Enter to skip)
          Slack webhook URL: <skipped>

    Done. Per-module status:
      meic      credentials: own service        account: ****2375 (own)      connected
      earnings  credentials: shared             account: ****2375 (shared)   connected
      flies     credentials: own service        account: ****2375 (shared)   connected

`connect --module <m>` keeps its current behavior for per-module overrides. Step 3 keeps the
existing selector semantics (`last4` / index) and the existing confirmation; the IRA-style
account-type note is printed from data the account listing already returns.

## Piece 3 — one status surface

Today the answer to "am I set up?" is scattered across `secrets_status` ×3, `account
--module` ×3, and `doctor`. Add one panel, shown at the end of the wizard, as a
`cherrypick doctor` section, and on the hub's Live Ops card:

    module    credentials        account            connection
    meic      own service        ****2375 (own)     ok
    earnings  shared             ****2375 (shared)  ok
    flies     missing            —                  BLOCKED: run `cherrypick connect`

Status only — present/absent and *source* (own/shared), never values. The "source" column is
what makes the override model legible instead of magical.

## Piece 4 — config self-wiring (kill the hand-edit)

The manual JSON edit exists because `keyring_service` / `broker_tool` were added to
`config.example.json` after real configs were created. Fix it at the resolver: `cfgmod`
gains a small table of **known-module defaults** —

    meic:     keyring_service meicagent,     broker_tool ["src/tt.py"]
    earnings: keyring_service earningsagent, broker_tool ["src/tt.py"]
    flies:    keyring_service fliesagent,    broker_tool ["src/broker_cli.py"]

— applied when a config omits the key, with the config always winning when present. An
existing config then needs **zero** broker keys for onboarding to work. The table lives in
one place, is data not behavior, and a genuinely new module still declares its keys in
config like today.

## What deliberately does not change

- The delegation boundary: secrets are typed into module/core child processes, never the
  orchestrator's.
- Hidden input, keyring-only storage, masking to `****1234` on every display surface.
- Human confirmation on designation writes.
- Per-module services as the override layer (and therefore per-module rotation).
- Webhook storage (`secrets-set --channel`) — it just gets a slot in the wizard.
- Nothing about live *enablement*: `enable_live_trading` / `live.enabled` / Gate 0
  attestations are config the wizard never touches.

## Migration and compatibility

- Fully additive. Existing per-module secrets keep working untouched (module service still
  reads first). No migration step is required.
- Optional dedup: the status panel can note "own service duplicates shared" so a user who
  wants one rotation point can delete the module copies at their leisure.
- Modules standalone: a module without the orchestrator can still run its own
  `secrets_set`; the shared service is a fallback, not a dependency.

## Implementation plan

| # | Work | Where | Size |
|---|---|---|---|
| 1 | `SHARED_SERVICE` + `python -m cherrypick.core.auth setup` (hidden-input CLI) + tests | core | S/M |
| 2 | Append shared fallback in each module's store (meic, earnings, flies `credentials.py`) | modules | S |
| 3 | Suite wizard: `connect` no-module flow (creds once → verify → designate → webhooks → status) | orchestrator | M |
| 4 | Shared designation default + `account --set` without `--module`; reconcile/liveops read-through | orchestrator | S/M |
| 5 | Unified status panel (wizard end, doctor section, Live Ops card column) | orchestrator | M |
| 6 | Known-module config defaults in `cfgmod` (config wins; table in one place) | orchestrator | S |
| 7 | Docs: PROJECT.md quick-start becomes "run `cherrypick connect`"; guardrails doc gains the shared/override model | docs | S |

Order: 1→2 first (invisible, fully back-compatible), then 4, then 3/5 together (the wizard
is only honest once the status panel exists), 6 and 7 last. Each step ships independently
with tests; nothing waits on anything outside this list.

## Open questions (decide before step 3)

1. **Should the wizard offer to migrate existing module-service secrets into the shared
   service** (and delete the copies), or only note the duplication? Leaning: note only —
   deleting credentials on a user's behalf violates least surprise.
2. **Webhook step in the wizard: opt-in or opt-out?** Leaning: opt-in (Enter skips), since
   notifications already default to `log` + `desktop` without any secret.
3. **Should `doctor` fail (red) on missing credentials when no module has live ambitions?**
   Leaning: yellow, not red — paper collection runs fine with credentials absent everywhere
   except earnings' scanner.
