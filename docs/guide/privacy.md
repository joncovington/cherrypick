# Privacy

cherrypick runs on your own computer. Your trades, results, settings and logs stay on it. This page lists
what does leave the machine, what never does, where your passwords are kept, and how to remove everything.

## What leaves the machine

Nothing here is sent to the project's authors. The suite has no account, no telemetry and no analytics.
These are the only outside connections it makes.

### Always, as part of normal use

- **Your broker, tastytrade.** The suite signs in with your login to stream prices and to read your
  positions, balances and orders. Live loops send orders here, and only here. This is the one place your
  account details go.
- **The GitHub release check.** The console asks GitHub which release is newest, at most every six hours,
  so the status bar can show a "new version available" chip. The request carries none of your data: a
  generic name and nothing else. To turn it off, set `"update_check": false` in
  `~/.cherrypick/config/console.json`.
- **Free public data.** Scheduled fetches pull public files and quotes: Cboe volatility-index history,
  Treasury and Federal Reserve rates, SEC filings data, Yahoo for prices, adjusted history and, where
  Nasdaq's earnings calendar does not answer, the time of day of an earnings report, exchange
  and clearing-house lists, a few public headline feeds, and similar. Each is an ordinary download. The
  requests name the symbol or file wanted and nothing about you.

### Only if you set them up

- **Discord, Slack or Telegram.** The suite posts only to a webhook (or, for Telegram, a bot) that you
  store yourself. With none stored, nothing is posted. What it posts is results and alerts: a day's
  review, a status digest, a warning that a loop stalled. Account numbers in them are masked. Every
  message sent is recorded, under the webhook's name and never its address, in `~/.cherrypick/data/outbound/`.
  Read it back with:

  ```bash
  python packages/orchestrator/run.py sent
  ```

  Treat a Discord channel as public unless you know who is in it. See [discord.md](discord.md).
- **Claude Code, for the advisor and the written narratives.** These are off by default and need the
  Claude Code command-line tool on your machine (`python packages/orchestrator/run.py capabilities` shows
  whether it was found). When you switch one on, the suite hands Claude Code a summary of the day's
  numbers (the day's results, not your credentials, which it cannot reach) and takes the written reply
  back. That request goes to Anthropic under your own Claude Code account and its terms. The morning
  narrative may also look things up on the web for the economic calendar. The advisor and narratives only
  ever propose or write words: they cannot place an order.
- **GitHub issues, from the end-of-day review.** If you set `review.file_issues` in `~/.cherrypick/config.json`, and
  the `gh` command is installed and signed in, the review files its recommendations as issues on the
  repository `gh` points at. It is off by default.
- **DoltHub.** The earnings and technicals data are public datasets, pulled from DoltHub into a local
  copy. Only the pull travels; nothing of yours is sent.
- **ThetaData.** If you subscribe for backtests, the suite fetches quote history from them with your own
  key.
- **A remote for your settings.** The opt-in config backup keeps a git history of `config.json` and
  `config/*.json`. Only if you give it a remote and turn pushing on does anything leave the machine. Those
  files hold settings, never passwords, but they do hold your choices; use a private remote.
- **Your own external reports.** A script you write to feed the console, or to post to Discord, sends
  whatever you write it to send. See [external-reports.md](external-reports.md).

## What stays local

Everything the suite keeps is under one folder, `~/.cherrypick` (your home folder's `.cherrypick`, or the
place `CHERRYPICK_HOME` points to). That includes:

- your configuration, including which modules and arms are on;
- every ledger, paper and live, and the stream cache;
- the review packs, narratives, research results and backtest history;
- logs, state files and the halt flag;
- the outbound record of what was posted.

The console and the settings editor listen on `127.0.0.1` only, so only that computer can open them. They
have no login. Do not expose either to a network. Anyone who could reach the console could read your
positions and use its Config page, including the halt control.

## Credentials

Your broker login and your webhooks live in your operating system's keyring: Windows Credential Manager,
macOS Keychain, or the Linux Secret Service. They are never in a config file, an environment variable or a
log. Even the suite's own status screens only say whether a secret is present or missing, never its value.

Enter them with the guided wizard, or one at a time:

```bash
python packages/orchestrator/run.py connect
python packages/orchestrator/run.py secrets-set --channel discord
python packages/orchestrator/run.py secrets-status
```

Input is hidden as you type. `secrets-status` shows which are stored without showing them. Remove one
with `secrets-delete --channel <name>`. Do not paste a login or a webhook into a config file, a chat or
an issue; anyone holding a webhook address can post to your channel.

## Account numbers

Wherever an account number appears, in the console, in a log, in a Discord post or in an error, it is
masked to its last four digits, as `****0000`. Only the write into the keyring uses the whole number.

When you report a bug or ask for help, mask your own account digits, positions and figures the same way
before you paste them anywhere public.

## Wiping everything

There are three separate things to remove. Do them in this order.

1. **Stop the suite.** Close or flatten anything you hold at the broker first, because once the suite is
   stopped nothing is managing it. Then double-click `uninstall.cmd` on Windows, or run `./uninstall.sh` on
   macOS and Linux. This stops the scheduler, the supervisor, the streamer and the console. It keeps your
   data and settings on purpose, so installing again picks up where you left off.
2. **Delete the data.** Delete the `~/.cherrypick` folder (or the folder `CHERRYPICK_HOME` names), then the
   checkout folder itself. That removes every ledger, log, setting and the outbound record.
3. **Delete the keyring entries.** These are not in `~/.cherrypick`, so deleting the folder does not remove
   them. The suite uses these keyring service names:
   - `cherrypick-broker`: the shared broker login;
   - `cherrypick-broker.<name>`: each further broker login you connected with `connect --login <name>`;
   - `cherrypick-notify`: Discord, Slack and Telegram webhooks;
   - `meicagent` and `earningsagent`: a module's own login, if you ever entered one for a module (also
     `fliesagent` and `bwbagent` for the retired modules);
   - `cherrypick-thetadata`: the ThetaData key, if you set one.

   In Windows Credential Manager, open the Windows credentials list and remove the entries that name those
   services. On macOS, search Keychain Access for `cherrypick`. On Linux, use your keyring app (such as
   Seahorse). Or, for the broker login, in a terminal:

   ```bash
   python -c "import keyring; [keyring.delete_password('cherrypick-broker', 'production:' + k) for k in ('client_secret', 'refresh_token', 'account_number')]"
   ```

   A further login is removed with `python -m cherrypick.core.auth remove --login <name>`, also before
   you delete the checkout.

   Webhooks are removed with `secrets-delete --channel discord` (and `slack`, `telegram`,
   `discord_reporting`), before you delete the checkout, because the command lives there.

Deleting a stored login from the keyring removes your copy of it; it does not revoke it at tastytrade. If
you want the suite's access to stop everywhere, also revoke it in your tastytrade account.

## The licence rule for ThetaData results

Backtests use quote history licensed from ThetaData for one person's personal use. The terms forbid
passing the data on, and anything computed from it counts. So:

- The data, and every result, table, chart or headline number derived from it, stays in `~/.cherrypick`.
- It does not go in a public issue, a pull request, a commit, a chat, a screenshot or a shared page.
  Reading a verdict ("this idea failed after costs") to a friend is fine. Posting the figures behind it
  is not.
- The licensed-data feature is off by default, and the Backtest page is hidden while it is off.
- The local backup skips this data. If your subscription ends, the terms require you to delete what you
  fetched; the Data tab lists every store so you can see what there is to remove.
