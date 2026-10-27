# Job progress, the queue tab, and arm cards — plan

Agreed 2026-10-26. Three console surfaces that share one complaint: long work and the arms it measures
are hard to read from the console.

- **Progress.** A ThetaData chain fetch shows a meter. Validation and replay runs show nothing until
  they finish: the first MEIC validation (queue task 3, started 2026-10-26 21:34 ET) ran for an hour
  with an empty log and no way to tell how far it had got.
- **The queue tab.** One eleven-column table grouped by study, newest first, with every finished row
  shown forever. The running and next tasks are buried, and most columns are detail.
- **Arms.** flies and MEIC have an experiment guide (what each arm changes, when it started, whether
  it is retired). Every other module, and the Backtest page, shows bare arm names.

None of it is measurement-affecting: nothing here changes what a loop decides or records. Each phase
is its own PR and lands when it is ready. Order is 1, 2, 3.

## 1. Progress, written by the job itself

### The problem in the code today

- `validate` (`backtest/validate.py`) runs the module's replay check two or three times (recorder,
  vendor, thinned) as `subprocess.run(capture_output=True)`. The child's stderr is held until it
  exits, and `validate` prints nothing of its own.
- `run` (`backtest/runner.py`) logs `"{day} ok"` but never the total, and not refused days.
- Progress for chain fetches is computed by `queue-list` starting one `chain-progress` subprocess per
  unfinished row on every list: the console's 30 s poll pays for that each time.
- A run started by hand is invisible to the queue and the console.

### The protocol

One small JSON file per job, written by the job, read by anyone.

- **`cherrypick.core.progress`.** A `Reporter` that writes `state/progress/<job>.json` atomically
  (`jsonio.write_json_atomic`), throttled to one write a second. The fields are:
  - `job`, `pid`, `started_at`, `updated_at`;
  - `phases`: a list of `{name, done, total, unit, current}`, outermost first, so a nested job says
    "vendor replay 2/3 · session 3/4 · 2026-10-15";
  - `note`, and a final `state` (`running`, `done`, `failed`) written on exit.
- **Where the file goes.** The queue sets `CHERRYPICK_PROGRESS_FILE=state/progress/q<id>.json` when
  it starts a task. A child process inherits the variable and reports into the same file under its
  own phase. A job started by hand with no variable set writes `state/progress/<module>-<verb>-<pid>.json`,
  so it is still found.
- **Reading it.** `queue-list` reads the files rather than spawning anything: the per-row subprocess
  goes. The view derives:
  - the rate and ETA, from `done` against `started_at` and the last few updates;
  - **stalled**, when `updated_at` is older than a threshold while the pid is alive;
  - **orphaned**, when the pid is dead and no final state was written.
- **Retention.** Finished files older than 30 days are deleted by the queue's own pass.

### Who reports

| Job | Phases |
|---|---|
| `backtest validate` | `layers` (quotes, profile per session), then `decisions` (one per source: recorder, vendor, thinned) |
| meic `replay check`, flies `replay-check` | `sessions` (per day, in the loop that already exists: meic `replay._check`, flies `cmd_replay_check`) |
| `backtest run` | `sessions` (done of `len(todo)`, refused counted) |
| `fetch-chains`, `fetch-flow` | `requests` (the counts `chain-progress` already computes, now written as they happen) |
| `script` tasks | opt-in: one `Reporter` call per unit of work |

`validate` also stops capturing its child's stderr and passes it through, so the queue log shows the
replay's own lines as they happen.

### Checks before it is trusted

- A test per reporting job that runs it on a tiny fixture and reads the file mid-run and at the end.
- A test that a dead pid with no final state reads as orphaned, and a stale `updated_at` as stalled.
- The per-row subprocess in `queue-list` removed, shown by a test that counts no spawns.

## 2. The queue tab

### Layout

Three sections instead of one table:

1. **Now.** Running tasks first, each with its meter and phase line; then queued tasks in the order
   the runner will take them, each with why it waits (blackout, needs ThetaData, not before, after
   another task). Hand-started runs found through their progress files appear here, marked "by hand".
2. **Recent.** Finished in the last **30 days**, newest first.
3. **History.** Collapsed, grouped by study, a count per study.

A header line sums it: "1 running · 2 queued · next: item19/fetch-resume after the blackout".

### Rows

Five columns: `study/step`, a kind badge, state with the meter, a one-line result, and a relative
time. Everything else moves into an expansion, opened by clicking the row (`HistoryTable`'s
`expanded` prop, as the calendars and pmcc history tabs already use):

- the full arguments, not-before, stop-by, needs, runs and exit code;
- the result, laid out as fields rather than one line;
- the output path;
- the last 20 lines of the task's log (a new read-only route; the log is already on disk).

Filters: status chips (running, queued, failed, done) and a study picker, remembered per viewer.

## 3. Arm cards

### What exists

`server/src/readers/experimentGuide.ts` already builds, for flies and MEIC:

- the arm's notes, from its config `_note` keys;
- what makes it different from the base arm and its siblings, computed from config;
- first and last session from the ledger, and whether it is enabled, retired or removed;
- measurement breaks.

`ExperimentGuide.tsx` renders it in the flies and MEIC lightboxes.

### The plan

- **Every module.** Extend the guide to curve, bwb, calendars, pmcc, contango and earnings. Their
  configs keep arm blocks under `books` / `profiles` / `arms`; `core.config.registry`'s spellings
  apply. Both shapes of `measurement_breaks` are read, as `readers/integrity.ts` already does.
- **Three short fields** in each arm's config block, beside `_note`:
  - `_summary`: one line, plain English, what the arm is for;
  - `_since`: the date the arm started measuring;
  - `_retired`: the date it stopped, when it has.

  Underscore keys are documentation, so none of them changes a measurement. pmcc's `from` is read as
  `_since` where present. Each module's example config carries them; the deployed configs get the
  same lines.
- **One card, everywhere an arm is named.** The module pages, the Backtest arms table, and the
  run-card arm picker (a summary on hover, the full card on click). An `advised:<name>` arm links to
  its advisor experiment through `readers/experimentIndex.ts`, which already resolves the tag.
- **The card shows:** the summary; the settings that differ from control; since and retired dates
  with the status; the evidence so far (sessions and trades); and any measurement breaks that apply.

### A guard

A test, driven off each module's own example config, that fails when an enabled arm has no
`_summary`. Shown to fail by deleting one.

### Writing the summaries

The `_summary` lines for existing arms are drafted from each arm's `_note` and its module docs and
put up for review in the phase 3 PR, with the source of each line named there.

## Order and size

1. Progress protocol, with `validate`, the replay checks and `run` reporting. Medium; mostly Python,
   plus the queue bridge's reader.
2. Queue tab. Medium; console only, plus the log-tail route.
3. Arm cards. Medium-large; one reader generalised, one component reused, config lines in seven
   modules.
