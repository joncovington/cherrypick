# The studies board: what we tested, and what we found

*Drafted 2026-10-29, after a review of the console's Backtest tabs. A read surface: nothing here
changes a measurement, a gate or an arm, so each phase lands when it is ready.*

## Why

The question a user brings to the Backtest page is "what did we test, and what did we find?". No tab
answers it today:

- **queue** says a row is `done`; it does not say the study failed. `item23/design` reads "done".
- **runs** lists 21 replays named by timestamp. None says which item it belongs to, all 21 carry
  the `provisional` chip, and the reason (validation never unlocked) is on another tab.
- **Most studies make no run at all.** Items 14-20, 22 and 25-28, the income confirmation and the
  strangle test write their own result files under `~/.cherrypick/data/backtest/results/research/`
  (or `~/.cherrypick/data/research/`). Their verdicts appear nowhere in the console.

Everything a board needs already exists; it is only scattered.

## What we already hold

| Source | Holds | Where |
|---|---|---|
| The register (B7) | one row per item: `study`, `title`, `status`, `rule`, `mechanism`, `rivals`, `pointer`; `results` and `retests` fields that are empty on every row | `docs/hypotheses.json`, checked by `cherrypick hypotheses check` |
| The plan | the declaration and a `**Result (date): verdict; decision.**` line per finished item | `docs/edge-hypotheses-plan.md` |
| Result files | a `verdict` word, and from item 21 the `window` and `run_id` | under `~/.cherrypick`, licensed or derived from licensed data |
| The research queue | each row's `study` (`item23`), step, state and result path | `cherrypick queue-list` |
| Backtest runs | the replay reports a `run_id` points at | `python -m cherrypick.backtest read overview` |

The result files use three verdict shapes, all words, never a figure to recompute:

1. a string: `"fail"` (items 21, 23, 24), `"no verdict"` (item 18);
2. per arm: `{"trail": "fail", "fixed_1030": "fail"}` (items 19, 20, 22, one file per time-half);
3. per arm with detail: `{"w14-hold": {"verdict": "no verdict", ...}}` (item 25).

## The register is the source of truth

The board lists register rows, not plan headings and not result folders. The register is in the repo
already, holds no figures, and is checked. Parsing the plan's prose would be brittle; reading result
folders would miss every item that never wrote one.

**It is behind, which the board would make visible on day one:**

- items 22, 23, 24 and 25 have no row, although the note says every field is required from item 18;
- `results` is empty on every row, so nothing links an item to its files.

## Phases

### 1. Bring the register up to date (orchestrator, docs)

- Add rows 22-25 from their declarations: 22 `failed`, 23 `failed`, 24 `failed` (its holdout re-run,
  queue row 30, confirms or not), 25 `no_verdict`.
- Fill `results` on every row from 18 on with the result files, as `~/.cherrypick/...` paths.
  Rows 1-17 fill it where a file exists; a legacy row without one stays empty.
- Add an optional `outcome`: the decision in a line, words only ("live stays at 16 delta"). It
  copies the plan's Result line after the verdict, never a figure.
- Two new checks in `cherrypick hypotheses check`, each shown to fail first:
  - a row from item 18 whose status is `passed`, `failed` or `no_verdict` names at least one result;
  - every queue study named `itemN` has a register row (catches the next 22-25).
- The declaration template gains a line: a study's result file carries a top-level `verdict` in one
  of the three shapes, plus `window` and, where there is one, `run_id`.

### 2. A board reader (orchestrator)

`cherrypick hypotheses board --json` joins, per row:

- the register row as it stands;
- each result file's verdict, normalised to `{window, arm, verdict}` rows from the three shapes. A
  file without a verdict word reads `unreadable`; nothing is recomputed;
- the `run_id` each file names;
- the item's queue rows: their state and when they last moved.

Status comes from the register. Where the queue disagrees (a `declared` row with a running task) the
board shows both, so the register is corrected by hand, never rewritten by the reader.

### 3. A `studies` tab (console)

Second, after `queue`, behind the same backtest gate (the verdicts come from licensed data).
`GET /api/backtest/studies` bridges the reader, as `researchQueueBridge.ts` does the queue.

- **Grouped by where an item stands:** in progress (a queued or running task), declared and not
  started, finished (newest first), observations.
- **Columns:** item, title, status, design verdict, holdout verdict, outcome, last activity.
- **A row opens to:** the pass test (`rule`), mechanism, rivals with their checks, the per-arm
  verdicts, and links: the plan section (on GitHub, from `pointer`), its queue rows
  (`/backtest/queue?study=itemN`) and its run (`/backtest/runs?run=`).
- A short head line: the design and holdout windows (moved off the data tab) and, while validation
  has not unlocked MEIC and flies history, one sentence saying why their runs are provisional.

### 4. Cross-links on the tabs that stay

- **queue:** a finished row shows its item's verdict beside the result path, and the study name
  links to the board row.
- **runs:** an `item` column, from the result file that names the run; the `provisional` chip says
  why and links to validation; the run report gets a plain-English head card, as slices has.

### 5. Tidy

- **data:** drop the `vendor stores` card (its two rows are the inventory's first two); the
  refused-session counts become an inventory column; the licence card keeps the licence only.
- **validation:** becomes a verdict at the top of data ("is history trusted?": the verdict, the
  failing check, what would unlock it), with today's tables behind an expand.
- **slices:** reads MEIC paper trades, not backtests. See the open questions.

## Licence

The register and this plan hold verdict words and pointers only. The board reads licensed result
files on this machine, behind the fail-closed backtest gate, and shows words, never their figures.
No fixture in a test carries a licensed figure.

## Decisions (2026-10-29)

1. **Slices moves out of Backtest.** It reads paper trades, and a reader looks for it beside paper and
   live results. A report may still come from a licensed replay, so it needs a fail-closed gate per
   tab (`FAIL_CLOSED_PAGES` is per page today); that gate lands with the move, shown to fail first.
   *Landed 2026-10-30 under Compare*, the console having no Review page: a slice report compares
   groups of one arm's trades, beside Compare's arms, risk and calendar.

## Status (2026-10-30)

Phases 1-5 have landed: the register caught up and checked (#677, #679 for item 22), the board
reader (#677), the studies tab (#678), the cross-links (#680), and the tidy (data with "is history
trusted?", one store table, slices to Compare). The trial count is next, in its own PR.
2. **The trial count is its own PR.** `trials` in the overview is empty, so the DSR on every run
   deflates by nothing. Recording trials per study fixes a number recorded wrong, so it lands
   immediately and apart from this board.
3. **Pre-register items (1-17) show status and pointer only.** Their four parts live in the plan
   sections they point to; backfilling them now would rewrite declarations after the result was
   known. Their `results` is filled where a file exists.
