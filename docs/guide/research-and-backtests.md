# Research and backtests

The suite does not take a strategy on trust. An idea is written down as a test, run once, and recorded
whatever it finds. This page explains how that works and how to read it: the research register, a declared
test, the research queue, the backtest package, and the reports the console draws from them.

## The licence rule

**Vendor data, and anything derived from it, stays under `~/.cherrypick`. It never goes into the repo,
a pull request, an issue, a screenshot or a post.**

Backtests run on historical option quotes from ThetaData, under a personal-use licence. The licence
forbids passing the data on, and requires it to be deleted within 30 days of the subscription ending. A
result computed from the data counts as the data. That covers a table, a chart, a headline number or a
screenshot of the Backtest page.

- Keep results and reports on your machine.
- A write-up that leaves your machine says what a test decided (it passed or failed) and where the local
  result file is. It never says what the data showed.
- The repo, its issues and its pull requests are public. Do not paste backtest output into any of them,
  including to ask for help.

The Backtest page repeats a licensed-data note at the top of every tab for the same reason. This guide has
no screenshot of it.

## The research register

`research/edge-hypotheses-plan.md` is the register of research items: each idea stated as the edge it would
need, what would show it wrong, the test, and the verdict. Each item is a numbered heading. Many concern
the retired modules. `research/hypotheses.json` holds the same items in a form a program can check, one row
per item with its status, its rule and a pointer to its section. The register holds no licensed figures: a
row names a verdict and the local path of a result file, never a number from the data.

Check the register with:

```
python packages/orchestrator/run.py hypotheses
```

It prints whether every row is well formed and every pointer names a real heading, any study in your queue
or result folders with no row, the next free item number, and any logged rule change that is due a
re-read. It exits non-zero on a problem. To see rejected look-alikes before declaring a new idea, give it
a proposal:

```
python packages/orchestrator/run.py hypotheses --similar '{"setup": "down-day", "structure": "put-bwb"}'
```

A rejected look-alike is named in the declaration, so a retest is a choice that is said out loud. (In
Windows PowerShell, write each inner double quote as `\"`, or run the command from Command Prompt.)

Two more commands read the register:

- `python packages/orchestrator/run.py hypotheses-board` prints each item with its verdict, its queue rows
  and its result files. It is what the Backtest page's `studies` tab draws.
- `python packages/orchestrator/run.py study-report --study item69` reads one study's result files as a
  report. Both read licensed result files, so what they print stays on your machine.

## A declared test

A test is **declared before it is run.** The declaration fixes four things, and none can be changed after
the data is read, because a rule changed after the fact is no longer a test:

1. **The rule:** what is measured, and how a trade is built.
2. **The windows:** the data it may use. A design window is where the rule is set and looked at. A
   holdout is data held back and read **once**, to check that the result was not fitted to the design
   window.
3. **The pass test:** the statistic and the bar, in words and numbers, and that it must hold in both
   halves of time. Larger studies add that it holds in both halves of the names.
4. **The sample:** how many sessions or trades it needs, so that "no result" is read as "too few to see an
   edge larger than X" and not as "no edge".

The declaration also says which rival explanations could produce the same result, and the check that rules
each out. A result that passes is then re-read with its parameter's neighbours (is it a plateau or a lone
peak), at higher costs, and with its drawdown range. Those are reported beside a pass. They are never used
to choose the setting again.

The verdict is one of passed, failed, no verdict (too little data), or observation. A study that fails is
kept. The register's negative results are the point of it: most ideas that look good do not survive
costs, and the register is where that is written down. Nothing is built from an item until
it has passed.

## The research queue

A long study can mean hours of data fetching. The research queue runs such tasks one at a time, from rows
in a table, so a task does not die with your terminal and never collides with the nightly fetch. The
supervisor's `research-queue` job takes the next ready row every minute.

A row is named `study/step`, such as `item69/study`. Rows run in priority order, oldest first among equals.
A row can wait on another (`--after`), for a time (`--not-before`), or for a free vendor session. A fetch
that reaches its stop time or the nightly window stops and is queued again to resume.

| Command | What it does |
| --- | --- |
| `python packages/orchestrator/run.py queue-list` | The rows, newest first, with why each queued row is waiting and where a finished row's result is. `--study item69` narrows it. |
| `python packages/orchestrator/run.py queue-priority --task-id 12 --priority 5` | Reorders a queued row. A higher number runs first. |
| `python packages/orchestrator/run.py queue-cancel --task-id 12` | Cancels a queued row. |
| `python packages/orchestrator/run.py queue-run` | What the job runs: ready rows one after another until none is ready. You rarely run it by hand. |
| `python packages/orchestrator/run.py queue-add --study item69 --task script ...` | Adds a row. `--task-args` is a JSON object whose keys depend on the kind. |

A row can only be one of a short allow-list of kinds: the ThetaData fetches, a `validate` run, or a
`script` that is a file already committed to the repo. It is never a command you type, because the
supervisor runs with your credentials in reach. That is why `queue-add` is mostly used by a developer, or
with Claude Code, alongside a study script. The full list of kinds and their arguments is in
[orchestrator-cli.md](../orchestrator-cli.md).

## The backtest package

`packages/backtest` replays the suite's own rules over years of ThetaData history, so a question can be
answered in weeks instead of waiting for paper arms to gather sessions. It never trades and never touches a
live loop. It reads a local store and reproduces exactly.

### Setting it up

You need a ThetaData subscription that covers what you will fetch (Options Standard: intraday quotes and
greeks from 2016).

1. **Store your API key in the keyring**, never in a file:

   ```
   python -m cherrypick.backtest key set
   ```

   If the key is already in the `THETADATA_API_KEY` environment variable, `python -m cherrypick.backtest key
   import-env` copies it once. Then remove the variable. `key status` says whether a key is present, and
   never shows it.
2. **Write the config.** Copy `packages/backtest/config.example.json` to
   `~/.cherrypick/config/backtest.json` and set `"enabled": true`. An unknown key is refused with its
   path, so a typo fails loudly.
3. **Install the vendor library**, which only the fetch needs:
   `pip install -e "packages/backtest[thetadata]"`.
4. **Check it:** `python -m cherrypick.backtest status` shows the config, whether the key is present, the
   store and the licence clock.

### Fetching, validating and running

```
python -m cherrypick.backtest fetch --cadence 60 --from 2022-05-16 --to 2026-10-16
python -m cherrypick.backtest coverage --cadence 60
python -m cherrypick.backtest validate --cadence 60
python -m cherrypick.backtest run meic --arms control --window design --cadence 60
```

- `fetch` is the only command that uses the network. It skips sessions already complete, records every
  refusal with its reason, and is resumable. History is fetched at 1 minute (`--cadence 60`).
- **Run one vendor client at a time.** Your account has one session. A second login, such as the vendor's
  own terminal, ends the fetch's.
- `validate` compares the vendor's sessions with the suite's own recording. **History stays locked until it
  passes**, and each module (MEIC, flies) unlocks its own.
- `run` replays a module's arms. `control` always runs beside the arms you name, and each arm is paired
  with it. The windows are `validation`, `design` and `holdout`. The holdout needs `--spend-holdout` and
  is spent once per spec. A design run is recorded as a trial, so a result is judged against how many
  things were tried.
- While history is locked, `--provisional` runs the design window anyway. It is marked provisional
  everywhere, records no trial and is a look, not evidence.

To fetch each new session automatically, set `"backtest": {"fetch": true}` in `~/.cherrypick/config.json`
and run `python packages/orchestrator/run.py install` again. The `backtest-fetch` job then runs at 18:45 ET
on trading days. It holds the vendor account's session while it runs.

### Where the data and results live

Everything is under `~/.cherrypick/data/backtest/`:

- the vendor store, by default `vendor/thetadata/`, one file per dataset and cadence;
- results, runs and reports under `results/`, and nowhere else.

The vendor store can be large (tens of gigabytes). `store.dir` in `backtest.json` points it at a bigger
drive, or you can keep the whole `data/backtest` folder on one, such as `D:`, as a directory link, which
leaves every path in the config unchanged. The data directory carries a marker file. The nightly backup
skips any directory with it, and `purge` removes only such directories.

### Ending a subscription

Set `vendor.license.terminated_on` to the end date in `backtest.json`. The licence then requires the data
to be deleted within 30 days:

```
python -m cherrypick.backtest purge
python -m cherrypick.backtest purge --apply
```

The first command is a dry run that lists what would go. `purge --apply --derived` also removes results
computed from the data.

## Reading a study's report

The console's Backtest page (`/backtest`) draws all of this. It is hidden unless the licensed-data feature is
on, which needs the config enabled, the licence still running, the key in the keyring and at least one
complete session in the store. It is read-only: rows are added from the command line, never from the page.
Its tabs are `studies`, `queue`, `runs` and `data`.

### `studies`

One tile per register item, in order: in progress, declared, finished, observations. A finished tile's big
word is the register's status. It reads "2 pass, 5 fail" where the newest result's arms disagree. Chips on
a tile mark a result as **stale** (the study's replay was corrected after the result was written),
**provisional**, and its window. **This week** lists studies with a result or a finished step since Monday.

Open a tile for five tabs:

- **report:** what the figures say, what to do next with the figure behind each suggestion, the gates in
  plain words, and every arm charted by window. Every sentence comes from fixed rules in
  `study-report`, never from a model.
- **overview:** the decision and the run's figures, each arm's net a session and its running net.
- **declaration:** the measure, the sample, the pass test and the rival explanations, as written before the
  run.
- **grid:** a study that tried one parameter at several values draws each value's mean net, labelled as a
  plateau or a lone peak.
- **queue:** each result file's verdicts and the study's queue rows.

### How to read a report

- **The register's verdict decides.** If a script's own word for an arm differs, the register's outcome
  leads.
- **A pass needs both halves.** A result that holds in the first half of the window and not the second
  has not passed.
- **Read the paired difference against the baseline,** not an arm's own net. A rising market lifts every
  arm.
- **"Stale" means re-read.** The result predates a correction to how the replay worked.
- **Provisional is not evidence.** It records no trial and may rest on data that is not validated.
- **A holdout is spent once.** Do not read a second look at it as a second test.
- **Money is net,** whole-position dollars, with fees shown as a cost already taken out, as everywhere. See
  [money-layout.md](../conventions/money-layout.md).

### `queue`, `runs` and `data`

`queue` is what the supervisor is doing now: the running task with its progress, what is up next and why
each row waits, and anything that failed in the last 7 days. `runs` is one tile per replay, with a summary
of each arm against `control`, its equity line, health checks and settings. The health checks list the
warnings first, such as a result resting on its best five sessions. `data` answers "is history trusted?"
first, then the licence clock and a tile per store with what it holds and which sessions were refused.

## What to do

- Before you test an idea, check the register for a look-alike. If it has failed, the burden is on the new
  version to say what is different.
- Write the declaration first: the rule, the windows, the pass test. Commit nothing that depends on the
  result until it has passed.
- Use the Backtest page to read verdicts. Do not copy a figure out of it.
- Treat a pass as a reason to run a paper arm beside `control`, not as a reason to change a live loop. See
  [experimenting-with-arms.md](experimenting-with-arms.md).
- If your subscription ends, set the end date and purge.
