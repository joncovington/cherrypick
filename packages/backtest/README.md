# cherrypick-backtest

Backtests of the suite's own rules over licensed vendor history. Read [CLAUDE.md](CLAUDE.md) first:
the vendor's licence decides where data and results may go.

## Setting it up

1. **Get the API key.** Sign in at <https://www.thetadata.net>, open the account dashboard, and copy
   the API key. The account needs a subscription that covers what you will fetch (Options Standard:
   intraday quotes, open interest, IV and first-order greeks from 2016).
2. **Store it in the keyring** (never in a file):

   ```
   python -m cherrypick.backtest key set
   ```

   If the key is already in the `THETADATA_API_KEY` environment variable, `key import-env` copies it
   once; then remove the variable, since the suite reads the keyring only.
3. **Write the config.** Copy `config.example.json` to `~/.cherrypick/config/backtest.json` and set
   `"enabled": true`. Unknown keys are refused, so a typo fails loudly.
4. **Check it:** `python -m cherrypick.backtest status` shows the config, whether the key is present
   (never the key), the store and the licence clock.
5. **Install the vendor library** (only the fetch needs it): `pip install -e "packages/backtest[thetadata]"`.

## Fetching

```
python -m cherrypick.backtest fetch --from 2026-10-13 --to 2026-10-16              # 15 s, the recorder era
python -m cherrypick.backtest fetch --cadence 60 --from 2022-05-16 --to 2026-10-16 # history, 1 minute
python -m cherrypick.backtest coverage [--cadence 60]                              # complete and refused
```

The fetch is the package's only network path. It skips sessions already complete (`--refetch` to
redo one), records every refusal with its reason, and asks for two requests per session, two
sessions at a time (the Standard tier allows four in flight). Each cadence is its own store file.
The vendor does not serve sub-minute history before 2026, so history is fetched at 1 minute.

**Run one client at a time.** The account has one session; a second login with the same key (a
probe, the Theta Terminal) ends the fetch's, and the fetch stops rather than recording refusals.

## Validating and running

```
python -m cherrypick.backtest validate [--cadence 60]          # against the recorder; unlocks history
python -m cherrypick.backtest run flies --arms range-high --window validation
python -m cherrypick.backtest run flies --arms range-high --window design --cadence 60
```

History (`design`, `holdout`) stays locked until validation unlocks it for that cadence. Every design
evaluation is recorded as a trial, and the holdout is spent once per spec (`--spend-holdout`).
Reports go to `~/.cherrypick/data/backtest/results/` and nowhere else.

## Connection

| Setting | Default | Meaning |
|---|---|---|
| `vendor.transport` | `python` | Talk to the vendor directly through its Python library; no terminal needed. |
| `vendor.terminal.host` / `port` | `127.0.0.1` / `25503` | Only for `transport: terminal`: a local Theta Terminal (REST v3). |
| `vendor.max_concurrent_requests` | `4` | The account-wide cap for the tier (Free 1, Value 2, Standard 4, Pro 8). |
| `vendor.credential` | `cherrypick-thetadata` / `api_key` | The keyring service and entry holding the key. |

## Ending a subscription

Set `vendor.license.terminated_on` to the end date. The licence requires everything fetched to be
deleted within 30 days of it:

```
python -m cherrypick.backtest purge            # dry run: lists what would go
python -m cherrypick.backtest purge --apply    # removes the vendor data
```

`--derived` also removes results computed from the data. The nightly backup never copies the vendor
data, so nothing survives the purge there.
