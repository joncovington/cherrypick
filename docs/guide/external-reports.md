# External reports

An external report is a small page of your own: a title, a few tables and some notes, written by a
script you control and shown in the console. The suite ships no report and runs none until you add
one. It does not fetch anything for you. Your script does that, under your name and your rules, and
the suite only schedules it, checks what it wrote and draws it.

## What you see

Open **Reports > external** in the console (`/reports/external`).

- **Overview.** One row per report file: its title, the source label you gave it, when it was last
  updated (`as_of`), and a badge, **fresh** or **stale**. A report is stale when it is older than the
  hours you set for it. A file that fails the rules below is listed too, marked **invalid**, with every
  reason.
- **One tab per report.** The tab strip along the top of the page has an *overview* tab and one tab for
  each file in the folder. A new file adds a tab and a removed file takes it away; there is nothing to
  configure. A report's tab shows its title, the stale badge when it applies, the source and how long
  ago it was updated, the one-line summary, and then each section as its own card, titled with the
  section's heading. A table section is a table; a text section is plain text. An empty cell shows a
  dash, never a zero.
- **An invalid file** gets a tab too. It lists what is wrong and shows nothing else from the file, so a
  half-written report is never drawn as if it were whole.
- **A report that is not there.** A link to a report that does not exist says "No such report" and
  points back to the overview.
- **Nothing yet.** With no files at all, the overview says so and points here.

The console only reads. It never runs your script.

## The file

A report is one JSON file at `~/.cherrypick/data/external/<id>.json` (the folder is under
`$CHERRYPICK_HOME` if you set it). The file's name without `.json` is its id, and the `id` inside must
match it. The rules live in one file, `packages/core/cherrypick/core/schemas/external_report.schema.json`,
which both the Python check and the console read, so they cannot disagree.

| Field | Required | What it is |
|---|---|---|
| `schema` | yes | The number `1`. |
| `id` | yes | Lowercase letters, digits and hyphens, at most 64 characters, the same as the file name. |
| `title` | yes | The report's name, up to 120 characters. |
| `source` | yes | A label you write for where the figures come from, up to 120 characters. The suite never interprets it. |
| `as_of` | yes | When the figures are as of, in ISO 8601 **with a UTC offset**, for example `2026-11-09T16:30:00-05:00` or `2026-11-09T21:30:00Z`. |
| `stale_after_hours` | yes | A number above 0 (and at most 8760). The report shows as stale once `as_of` is older than this. |
| `summary` | no | One line, up to 200 characters. |
| `sections` | yes | One to 20 sections, each either a table or text (below). |

A **table section** is `{"heading": "...", "table": {"columns": ["a", "b"], "rows": [["x", 1], ["y", null]]}}`:
up to 12 columns, up to 500 rows, every row as wide as the columns, and each cell a string (up to 200
characters), a number or `null`. A **text section** is `{"heading": "...", "text": "..."}`, up to 5,000
characters. A heading is 1 to 120 characters and is the title of the section's card. The whole file may
not be over 512 KiB; a bigger one is refused without being read, so a script that runs away cannot
freeze the console. Unknown fields are not allowed.

## Check a file

```
python run.py external-check ~/.cherrypick/data/external/vix-history.json
```

It prints what the console will draw: the title, the source, the `as_of`, whether the report is fresh
or stale, and each section's heading with its row count. If the file is not valid it prints every
reason instead, for example `invalid: sections[0].table.rows[3]: 1 cells for 2 columns`, and exits 1
(0 when valid). Use it while you write a script, and first whenever a report looks wrong.

## An example, step by step

`scripts/examples/external_report_example.py` fetches the free daily history file Cboe publishes for
the VIX (`https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv`), keeps the last 30
sessions, and writes `data/external/vix-history.json`. Read it first: it is short, and it is the shape
to copy.

1. It downloads the CSV (the only place anything touches the network; the suite's packages never do).
2. `shape_report` turns the last sessions into one table (date, open, high, low, close, change, newest
   first), a one-line summary and a text note. It is a pure function of the CSV text and the clock, so
   it is tested without the network.
3. It writes the file atomically (a temporary file, then a rename) so the console never reads half a
   report.

To use it, copy it under your suite home, then add an entry to `config.json`:

```
cp scripts/examples/external_report_example.py ~/.cherrypick/scripts/
cd ~/.cherrypick/scripts
python external_report_example.py
cd -
python packages/orchestrator/run.py external-check ~/.cherrypick/data/external/vix-history.json
```

## Scheduling it

Add entries to `external_reports` in `~/.cherrypick/config.json`. Each entry becomes one daily job named
`external-<id>`, run by the supervisor with the suite's Python, logged like the other jobs, caught up
if the machine was asleep (inside four hours), and listed in `python run.py status`.

```
"external_reports": [
  {"id": "vix-history", "script": "scripts/external_report_example.py", "at_et": "17:30",
   "trading_days_only": true, "post": null, "enabled": true}
]
```

| Key | Meaning |
|---|---|
| `id` | Lowercase letters, digits and hyphens. Names the job (`external-<id>`) and the report file. Unique. |
| `script` | A `.py` file, as a path under the suite home (`~/.cherrypick`) or relative to it. **A path that resolves anywhere outside the home is refused**: the job is listed off with the reason, `python run.py doctor` says so, and nothing runs. |
| `at_et` | `"HH:MM"`, New York time. |
| `trading_days_only` | Default `true`. |
| `post` | `null` (default), `"reporting"` or `"notify"`: see Discord below. |
| `enabled` | Default `true`. `false` keeps the entry and lists the job off. |
| `args` | Optional list of strings passed to the script. |
| `timeout_minutes` | Optional, 1 to 180, default 15: how long the script may run. |
| `writes_report` | Default `true`: after the script, the suite checks `data/external/<id>.json` and fails the job if it is invalid. Set `false` for a step that does something else (a capture, a score) and only has to exit cleanly. |

**Scripts that post for themselves.** Some scripts need more than a report: they take their own
pictures, post their own messages and run in several steps through the day. List one entry per step,
all with the same `script` and different `args` and times, set `"post": null` so the suite does not
post a second time, and set `writes_report` to `true` only on the step that writes a report. A step's
exit code is its verdict: a non-zero exit fails the job. For a script to take pictures of its own
report, each section is a card whose title is its heading, at `/reports/external/<id>`, which
`packages/console/tools/ui-check.mjs --route reports/external/<id> --card "<heading>"` can capture.

`python run.py supervise --restart` makes a running supervisor read a changed config.

## Posting to Discord

With `"post": "reporting"` or `"post": "notify"`, after a successful run the suite posts a **text**
card: the title, the source, the `as_of`, the summary, and the first table cut to fit Discord's limits,
ending "...and N more rows" when rows were left out. `reporting` is the generic reporting webhook (the
keyring entry `discord_reporting`); `notify` is the suite's own Discord webhook. Each is a choice, never
a fallback: if the one you chose is not stored, nothing is posted and the log says why. A report is
posted **once per `as_of`**: running the job again, or restarting the machine, never repeats a post for
the same `as_of`. An invalid report posts nothing and the job fails with the reason. Setting up the
webhooks is in [Discord](discord.md).

## The rules

- **Keep your scripts and what they write under `~/.cherrypick`.** The suite refuses to schedule a
  script anywhere else.
- **Check a site's terms before you fetch from it.** Fetching a public file for your own use is one
  thing; scraping a page that forbids it is another. A script that logs in to a site is yours to answer
  for, not the suite's.
- **Logins live in the operating system's keyring, never in the script.** A script that needs a
  password should read it from the keyring (`keyring.get_password`), so it is never written in a file or
  a commit.
- **Do not republish someone else's content to a shared channel.** A Discord post is a copy. Post your
  own figures, or what you have the right to share, and keep a private channel for the rest.
- **Reports are records, not signals.** Nothing in the suite trades from an external report.

## Troubleshooting

| What you see | What to do |
|---|---|
| The tab says **invalid** | Run `python run.py external-check <file>`; it lists every reason. Fix the script, run it again. |
| The report is **stale** | The script has not run, or has failed. `python run.py status` shows the `external-<id>` job and its last run; its log is in `~/.cherrypick/logs/`. A stale badge is the console telling you the job is not keeping up. |
| The job is listed **off** | The reason is in `python run.py status` and in `python run.py doctor`: a script outside the home, a malformed id or time, a bad `post`, or `enabled: false`. |
| `doctor` warns about a `quikoptions` block | That integration was removed in v0.12.0 and the block is ignored. Delete it. |
| Nothing posted to Discord | The webhook you chose is not stored (`python run.py secrets-status`), the report was invalid, or this `as_of` was already posted. |
| A new file does not appear | The file must be `data/external/<id>.json` directly in that folder and end in `.json`. The page refreshes every minute. |
