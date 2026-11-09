---
name: external-report
description: Create or change an external report for the cherrypick console - a script that writes one JSON file under ~/.cherrypick/data/external/ which the console shows as its own tab (and can post to Discord). Use when someone asks to add a report, table or feed of their own to the console, to bring outside data into the suite, or to style or fix an existing external report.
---

# Making an external report

An external report is how a user brings their own data into the console without a new page: a script
writes one JSON file, the console draws it under Reports > external as a tab of its own, and the
supervisor can run the script on a schedule and post it to Discord. It is source-neutral by design:
the suite never names where the figures come from, the user's `source` label does.

Read these before writing anything, in this order. They are the source of truth; this file only says
how to use them well.

1. `docs/guide/external-reports.md` - the user guide: the file, every styling hint with an example,
   checking, scheduling, posting and the rules.
2. `packages/core/cherrypick/core/schemas/external_report.schema.json` - the contract. When this
   file and the guide disagree, the schema wins; say so to the user.
3. `scripts/examples/external_report_example.py` - the shape to copy: fetch, a pure shaping function,
   an atomic write.
4. `packages/core/tests/fixtures/external_report_cases.json` - valid and invalid examples of every
   field, which both validators run.

## When to use one, and when not

Use one for data that is theirs to use, shown as tables or short text, refreshed by a script. Do not
use one to change what a trading module does, to place or suggest orders, or for anything that needs
charts beyond the bars the schema offers. If the user wants a real console page, say that is a change to
`packages/console` and not an external report.

## The workflow

1. **Agree the report with the user**: its title, its `source` label in their words, what each table
   shows, how stale is too stale (`stale_after_hours`), and whether it should post to Discord.
2. **Write the script under the suite home**, for example `~/.cherrypick/scripts/<id>.py`, never in
   this repository. A scheduled script outside the home is refused. Copy the example's structure: the
   network step alone, a pure function that turns the fetched data into the report (so it can be tested
   with no network), and an atomic write (a temporary file, then a rename) to
   `~/.cherrypick/data/external/<id>.json`, honouring `CHERRYPICK_HOME` through the suite's home
   helper.
3. **Write a small test** of the shaping function with a fixed input and check the output passes the
   validator in `cherrypick.core.external_report`.
4. **Run it once by hand, then `python packages/orchestrator/run.py external-check <file>`.** Fix
   every reason it prints. Its `hints:` line says which styling it found; check that matches what you
   meant to use.
5. **Schedule it** by adding an entry to `external_reports` in `~/.cherrypick/config.json`: `id`,
   `script`, `at_et`, `trading_days_only`, optional `args`, `timeout_minutes`, `retries` and
   `retry_delay_minutes`, `writes_report` (false for a step that only fetches) and `post`. Back the
   config up first. Then `python packages/orchestrator/run.py supervise --restart`, because the
   supervisor reads its jobs once.
6. **Posting**: `post` is `reporting` (the `discord_reporting` webhook), `notify` (the suite's notify
   webhook) or null. The suite posts a text card once per new `as_of`. A script that posts for itself
   must use null so nothing is posted twice. Webhooks are stored with
   `python packages/orchestrator/run.py secrets-set --channel <channel>`, never in a file.

## Making it read well

The hints are optional; a plain table is valid. Use them to make a table read like the console's own,
and only where they carry meaning. Names below are the schema's; the guide shows each one.

- **Format every numeric column.** Money as `money`, counts as `compact` or `int`, rates as `percent`
  (the number is the percent: 56, not 0.56), scores and changes as `signed`. Numbers right-aligned is
  the default; leave it.
- **Colour only what has a direction.** `tone: sign` on a signed column, `pos`/`neg` on a cell that
  says up or down, bought or sold. `warn` for flags that need attention, `muted` for what is secondary,
  `strong` for the one thing each row is about. If every cell is coloured, nothing is.
- **Put qualifiers in a suffix**, not a second column: a contract with its kind ("outright", "sweep")
  as a muted suffix keeps the row short.
- **Bars for comparison, stacks for proportion.** A `bar` with a fixed `scale` when the figure has
  natural bounds (a score from -100 to 100) so it means the same every day; without a scale it is
  relative to the column. `track` and `own_column` when the number and the bar both matter. A `stack`
  when a total splits into parts; label every segment.
- **Explain every tag.** If a cell shows an abbreviation or flag, add a `key` entry for it. Say how a
  number is made in the `footnote`, in one line.
- **Emphasise sparingly**: `numbered` when rank matters, row `emphasis` for today against later, never
  for decoration.
- `show_source_foot: false` only when the user asks; the source line is how a reader knows where the
  figures came from.

Do not put text in a numeric column, format a column that holds text, or invent a tone or format the
schema does not list; `external-check` will refuse it, and that is correct.

## Rules

- Check a site's terms before writing a script that scrapes it, and tell the user what they say. If it
  needs a login, the credential goes in the OS keyring, never in the script, a file or an argument.
- Never republish someone else's content to a shared Discord channel; a report the user keeps to
  themselves is a different thing from a post to others.
- Never put an account number, a real position or anything from the user's broker account in a report
  meant for a shared channel. Mask an account as `****1234`.
- Nothing about the report goes in this repository: the script, its output and its settings live
  under `~/.cherrypick`.

## Done means

- `external-check` passes on the real output, with the hints you intended.
- The report shows on its own tab under Reports > external, fresh, and looks right in both light and
  dark mode.
- If scheduled, `python packages/orchestrator/run.py status` lists `external-<id>`, and the first run's
  log is clean.
- If it posts, a dry run (or the first post) went to the channel the user chose, once.

Tell the user what you checked and what you did not.
