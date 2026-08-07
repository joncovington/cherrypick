# Setup

**What this covers:** getting MEIC installed and connected to your tastytrade account for the
first time — the one-time setup you do before running any paper or live session. Part of the
[MEIC module](../README.md) in the cherrypick suite.

## Requirements

- **Python 3.11+**
- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** — Anthropic's CLI coding assistant. The agent's decision loop and all the `/`-commands in this guide (`/setup`, `/meic-start`, `/paper-start`, `/serve-dashboard`, etc.) run inside Claude Code, driven by the operating instructions in `CLAUDE.md` and the skills in `.claude/commands/`. Install it with `npm install -g @anthropic-ai/claude-code`, then start it from the project directory by running `claude`.
- **tastytrade account** — live account or developer sandbox (tastytrade does not offer paper trading; the developer sandbox is a separate environment for testing without real capital)

There is no separate MCP server to install — `cherrypick/meic/tt.py` talks to tastytrade directly via the official Python SDK (OAuth2).

> **How you run it:** the plain `python …` commands below run in a normal terminal. The `/`-prefixed commands (like `/meic-start`) are Claude Code skills — type them at the Claude Code prompt after running `claude` in the project folder. Everything Claude Code does is also doable by hand with the underlying `python -m cherrypick.meic.<module>` commands; the skills just orchestrate them.

---

## Installation

### 1. Clone the suite and enter this package

`cherrypick-meic` is the MEIC module of the [cherrypick](../../../README.md) monorepo. Clone the
suite, then work from `packages/meic`:

```bash
git clone https://github.com/joncovington/cherrypick.git
cd cherrypick/packages/meic
```

### 2. Install Python dependencies

```bash
pip install -e ../core              # the shared cherrypick.core library — install this first, or
                                     # every import cherrypick.core... fails (calendar, fee lookups)
pip install -e .                    # installs tastytrade, keyring, pytz, flask (from pyproject.toml)
pip install pytest pytest-asyncio   # optional — only needed to run the test suite
```

### 3. Set tastytrade credentials

Store OAuth2 credentials (`client_secret`, `refresh_token`) in the OS keyring — never in files or environment variables:

```bash
python -m cherrypick.meic.tt secrets_set
```

This prompts for each credential with hidden input and preserves existing values if you press Enter without typing. `account_number` is optional; the SDK discovers accounts automatically if omitted. See `/setup` for a guided walkthrough, including connection verification.

Credentials are stored via the [`keyring`](https://github.com/jaraco/keyring) library, which uses the OS secure store automatically — **Keychain** on macOS, **Credential Manager / DPAPI** on Windows, and the **Secret Service** (GNOME Keyring / KWallet) on a Linux desktop. All three encrypt credentials at rest and unlock them with your login session, which is the appropriate bar for a long-lived broker refresh token. Nothing is ever written to a file or environment variable, and `credentials.py` deliberately fails with `CredentialError("No keyring backend available.")` rather than falling back to anything insecure.

#### Headless / server credentials (Linux without a desktop)

A server or VPS with no desktop session has no Secret Service running, so `secrets_set` will fail with *"No keyring backend available."* You have two secure ways to run there — pick based on how production-grade the box is. **Do not** fall back to a plaintext `.env`, a config-file secret, or a shell environment variable: the refresh token is persistent broker access and plaintext at rest fails any reasonable security review.

**Option A — encrypted file backend (`keyrings.cryptfile`), simplest.** This is a drop-in `keyring` backend, so `secrets_set` and the agent use it with **no code change** — credentials live in an AES-encrypted file unlocked by a passphrase you enter at session start:

```bash
pip install keyrings.cryptfile
# Select it as the active backend (persists in keyring's config):
python -c "import keyring, keyrings.cryptfile.cryptfile as c; keyring.set_keyring(c.CryptFileKeyring())"
python -m cherrypick.meic.tt secrets_set          # prompts for the encryption passphrase, then each secret
```

You'll be prompted for the passphrase again the first time the agent reads a secret in a new session. To avoid an interactive prompt in a fully unattended daemon, supply it via the backend's `KEYRING_CRYPTFILE_PASSWORD` environment variable set only in the daemon's own environment (not in a committed file) — this keeps the passphrase out of the repo and off disk while accepting that it lives in the process environment for the session.

**Option B — systemd or a cloud secret manager, production-grade.** On a systemd-managed host, deliver the secrets with [`systemd-creds` / `LoadCredential=`](https://systemd.io/CREDENTIALS/) (TPM-backed encryption, exposed to the process via tmpfs). For a cloud deployment, pull them at startup from HashiCorp Vault, AWS Secrets Manager, or GCP Secret Manager (IAM-scoped, audited, rotatable) and hand them to `set_secret` in-process. Both keep the secrets off the box's persistent disk entirely and are the recommended path for a real server.

### 4. Configure the agent

Copy the example config and edit it with your settings:

```bash
cp config.example.json config.json
```

> **Config location.** This in-repo `config.json` is the standalone-checkout path. Under the orchestrated
> cherrypick suite the config resolves **home-first** to `~/.cherrypick/config/meic.json` (see
> [`cherrypick/meic/paths.py`](../src/cherrypick/meic/paths.py)); `cherrypick migrate-home` moves an in-repo config up to the home. The
> keys are identical either way — edit whichever `paths.config_path()` resolves to.

Key fields to update in your config:

| Field | Description |
|---|---|
| `symbols` | List of underlyings to trade concurrently, e.g. `["XSP", "SPX"]` — any index or equity symbols tastytrade supports, such as equity index options (`XSP`, `SPX`, `NDX`, `RUT`) or futures options (`/MES`, `/ES`, `/MNQ`, `/NQ`). Every symbol must list daily-expiring (0DTE) option chains; most single-name equities don't. Non-cash-settled symbols should be left out of `cash_settled_symbols` so a missed force-close is escalated as an assignment-risk failure. All symbols share one account-wide risk budget (buying power, `max_concurrent_ics`, `max_entries_per_day`) — there's no per-symbol cap, and correlated symbols aren't exposure-limited against each other yet. The single-symbol `symbol` key is still accepted as a deprecated alias for `["symbol"]` if `symbols` is omitted. |
| `delta_target` | Short strike delta target (default `0.18`) |
| `max_wing_width` | Upper bound (points) on spread width; the agent decides the actual wing width per entry rather than picking from a fixed list |
| `quantity` | Number of contracts per IC leg |
| `max_entries_per_day` | Hard cap on entries (`-1` = no cap, rely on the agent's judgment + buying power) |
| `entry_window_start` | Earliest time to enter new ICs in HH:MM ET (default `"10:00"`) |
| `separate_spread_entry` | Order structure: `false` = 4-leg combo (default), `true` = separate 2-leg spreads, `"auto"` = agent decides per-iteration based on IV rank, session, and open IC count |
| `entry_price_strategy` | Limit price strategy: `"mid"` = streaming mid price with a spread-width gate and fallback to natural bid, `"natural_bid"` = always submit at natural bid (safest), `"ioc_step"` = try IOC orders above natural bid then fall back, `"day_improve"` = Day limit above natural bid with cancel-replace, `"auto"` (default) = agent decides per-iteration by session/spread-width/IV rank |
| `ioc_step_increments` | Amounts above natural bid to attempt as IOC orders (e.g. `[0.02, 0.01]`). Used when `entry_price_strategy` is `"ioc_step"` or `"auto"` selects it |
| `ioc_step_wait_seconds` | Seconds to wait per IOC attempt before stepping to the next increment (default `10`) |
| `day_improve_amount` | Amount above natural bid to submit as a Day limit when using `"day_improve"` (default `0.03`) |
| `day_improve_wait_seconds` | Seconds to wait before canceling the Day improve order and resubmitting at natural bid (default `60`) |

### 5. Initialize the database

```bash
python -m cherrypick.meic.db init_db
```

This creates `meic_trades.db` (SQLite, WAL mode) in the **data home** — `~/.cherrypick/data/meic/` by
default, shared with the orchestrator so both read the same files. Override the location with the
`MEIC_DATA_DIR` environment variable (tests point it at a tmp path). The path is resolved centrally by
[`cherrypick/meic/paths.py`](../src/cherrypick/meic/paths.py), which also resolves the **logs home** — `~/.cherrypick/logs/meic/` by
default (override with `MEIC_LOGS_DIR`, or `CHERRYPICK_HOME` for the whole suite) and the **config home**
— `~/.cherrypick/config/meic.json` (home-first, else the in-repo `config.json`). Nothing runtime stays in
the package. Safe to run multiple times.

### 6. Start the streamer daemon (recommended)

Since the 2026-07-21 producer cutover, the **standalone streamer** (`packages/streamer`, a sibling
package) is the suite's single writer of the shared stream cache — run it from the monorepo root:

```bash
python ../streamer/run.py
```

**Do not run `python -m cherrypick.meic.streamer` alongside it** — two producers means two DXLink
writers on one cache and one account; MEIC's own streamer module is a disabled rollback path, only
for a box deliberately configured with `modules.meic.streamer.enabled: true`. Either producer
maintains a persistent DXLink WebSocket so quote/greeks/chain reads are served from cache instead of
a cold connection each time. `/meic-start` and `/paper-start` both check/start it automatically as
their first step (see `.claude/commands/meic-start.md`) — neither launches a dashboard.

### 7. Enable live trading (when ready)

Tastytrade operations go direct via the Python SDK in `tt.py` — there is no MCP server involved. Live order submission is gated by a single `config.json` key, checked by `tt.py`'s `_live_trading_enabled()` before any `execute_trade`/`adjust_order`/`close_position` call:

```json
{
  "enable_live_trading": false
}
```

By default this is `false`, so `execute_trade`/`adjust_order` calls without `--live` always dry-run regardless of this setting, and calls with `--live` are rejected outright. To go live, set `enable_live_trading` to `true` in `config.json` — no restart of the running session is required, since `tt.py` reads `config.json` fresh on every invocation.

---

## Running the tests

The test suite operates on temp SQLite databases and stubbed cache data — no tastytrade connection or credentials required.

```bash
pytest
```
