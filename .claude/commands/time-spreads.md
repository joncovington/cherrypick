---
description: The time-spread scanner — today's calendars and diagonals, sized to the account; read-only, no tested edge
argument-hint: [--scan] | [--force] | [--name SYMBOL]
---

Show the **time-spread scanner**'s day (docs/time-spread-scanner-plan.md): calendars and diagonals on
the tradable names whose front week is priced rich to the back month, and the ones that fit the
account. It reads the account read-only and places nothing. **No tested edge**: the rows are ordered
by the front/back IV ratio, an untested hypothesis (research item 54). Say so every time.

## Route on `$ARGUMENTS`

- **No argument:** read the day's evaluation (`~/.cherrypick/data/manage/eval-<today>.json`,
  `accounts[].portfolio.time_spreads`) and, for the unsized list, the scan's own file
  (`~/.cherrypick/data/time-spreads/<today>.json`, `structures`). If today's scan file is missing,
  say so and offer `--scan`.
- **`--scan`:** run the scan now, then the full snapshot that sizes it:
  `python packages/orchestrator/run.py time-spreads`. It refuses outside regular hours.
- **`--force`:** the same, outside regular hours (after-hours quotes say little about a spread's
  real width; say so): `python packages/orchestrator/run.py time-spreads --force`.
- **`--name SYMBOL`:** the rows for that one name only, from the files above.

## What to show

1. One line: when the scan ran, how many names were read, how many passed, the shortlist size, and
   anything it left out (`dropped`), plus whether bullish rows were held back for the book's delta.
2. **The rows that fit**, as a short table: name, structure, debit at mid and natural (per share),
   theta a day, net delta, the modelled profit at the short's expiry flat / +5% / −10%, the price stop
   band, and the quantity with its binding cap. Flag a name or sector the book already holds.
3. For any row the user asks about, the levels it would be managed by: breakeven band, price stop
   (close if the stock closes beyond it), take profit, value stop, roll-by date. These are what the
   positions page will apply once the trade is on.
4. The ones too big for the account, briefly, with the cap that stops each.

Every number is modelled at mid from the scan's quotes: tell the user to re-quote before sending.
Never place, stage or suggest submitting an order from here; the user enters trades themselves. Never
copy the account's positions, the scan's rows or any figure from these files into the repo, a commit,
a PR or an issue.
