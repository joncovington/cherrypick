# Discord

> **Stub: to be expanded.** This page is a placeholder so the guides can link to it. It holds only the
> essentials until the full Discord guide is written.

The suite posts to Discord through webhooks. A webhook URL is a secret: anyone holding it can post to
your channel, so it lives in the operating system's keyring and never in a config file.

- **`discord`**: the suite's own channel, for alerts, digests and notifications. Store it with
  `python run.py secrets-set --channel discord` (or the guided `python run.py connect`).
- **`discord_reporting`**: a separate reporting channel that no alert ever lands in. External reports
  post to it when an entry says `"post": "reporting"`
  ([external reports](external-reports.md)). Store it with
  `python run.py secrets-set --channel discord_reporting`.

`python run.py secrets-status` shows which are stored (never the URLs). Everything the suite sends is
recorded, with the webhook's name and not its URL, in `data/outbound/`; `python run.py sent` reads it
back.
