# cherrypick-advisor — Operational Instructions

> Operating contract for the suite's **AI advisor machinery**. Suite-wide context is in the root
> [documentation index](../../docs/README.md). The incidents and measurements behind the rules below
> are in [docs/history.md](docs/history.md).

This package lets an AI influence the paper books **without any AI running inside a suite package**.
It holds every deterministic part: the fact packs the model reads, the parse-and-validate of its
reply, the experiment lifecycle, and the nightly issuing of bounded advice artifacts through
`cherrypick.core.advice`.

## The fence

**This package never invokes AI, holds no API key, and opens no socket.** The one AI touchpoint is
`scripts/advisor_checkpoint.py`, outside every package (the `eod_narrative.py` fence): a script the
scheduler runs can never be imported by a loop. Deleting it costs the advice and nothing else; the
loops run on baseline, which is what `core.advice` guarantees when advice is absent.

One channel in, one out. In: a fact pack on **stdin**, with **no tools at all** — `--tools ""`,
`--strict-mcp-config` with an empty server list, no skills; the test shim records the argv so this is
asserted, not assumed. Out: strict JSON, parsed and validated here against the module's declared
bounds. The exact model id that answered is recorded (`checkpoints.model_id`) beside the floating
config alias.

## It can never touch a live account

The advisor reads live facts (context a competent observer would want) but cannot act on them:

- Live databases and live config keys are opened **read-only and only in `factpack.py`**; every other
  module in `src/` is proven free of them by a source scan.
- The only loop-facing output is a paper advice artifact at `state/advice/<module>-<session>.json`,
  which each module's consumer applies to a **synthetic `advised:<experiment name>` book beside its
  control** — never to the control, never to a live loop.
- Nothing here writes a module config, `config.risk.json`, or any module's database. New-arm and
  new-strategy ideas come out as `creative` proposals with ready-to-paste specs; a human applies them
  or not.
- **It tunes only its own experiments.** A `tune` proposal naming a control arm, a human-configured
  arm, or an unknown id is rejected `not_an_advisor_experiment`.

## Experiments

**Paper arms are the dry run.** There is no replay engine and there will not be one. A proposal's
test is a next-session paper arm beside its control, scored through
`cherrypick.core.profiles.compare_profiles` and `qualify_readings`. Default length is **15 sessions**
because the promotion gate needs min 14 days and min sample 20; shorter is structurally
`underpowered`.

**An experiment outlives its artifact.** Advice is single-session by contract — never sticky, always
expiring. The experiment lives in `advisor.db` (base arm, param overlay, expiry, journal), and every
evening the deterministic `enact` step **re-issues** the next session's artifact for each active
experiment, re-validated against the module's *current* bounds — so tightening bounds tonight takes
effect tomorrow. `enact` runs **unconditionally** in the deep slot, even when the AI call failed: an
outage must never truncate an active A/B sample. `enact` isolates each module (one module's failure
cannot stop the others'), and the validator rejects a malformed bounds rule with a reason rather than
raising.

**One book per experiment, any number at once.** The artifact carries an `experiments` entry per
active experiment, each validated on its own, each naming its own book via the row's `tag` (fixed at
admission, unique per module, `-2` suffixed on a duplicate name; a nameless experiment keeps the
legacy `advised:<base>`). `max_experiments_per_module` is **null = unlimited**; set a number to cap,
and over-cap specs are admitted `queued` and activate FIFO. The `enactment` table is keyed
`(session, module, experiment_id)`, `''` for a module-session with no artifact. Before 2026-09-17 the
cap was one per module by construction; the retag moved historical rows onto their experiment's tag.

**Rows are stamped with their experiment.** The artifact, the session decision and every advised row
a module writes carry `experiment_id` (`core.advice.stamp_for`, never on a control row).
`verdicts.reading_pair` takes `end` (the concluding session, inclusive) and `experiment_id`, keeping
on the advised side only rows stamped with this experiment or unstamped, so a closing verdict cannot
pool a successor's rows. The console pairs each stamped experiment separately
(`<tag>@<experiment_id>` in `core.metrics`) and shows pre-stamp rows as one pair flagged unstamped.

**Frozen params persist on open rows.** bwb, curve, pmcc, calendars and earnings keep applying
admitted params to an OPEN advised row after the artifact expires (exit continuity), and one test per
module pins it beside the validator's refusal of the same artifact for a new entry. bwb and curve
plan their advised twin from the base book its tag names.

**Calendar exit.** An active experiment concludes as `stalled` after twice its length in calendar
sessions (verdict computed, `underpowered` on its face), and the queued one activates — otherwise a
module that stopped recording decisions holds its slot forever.

## A session counts when a loop applied it

`sessions_run` advances on what the loop recorded, not on an artifact being written (the 2026-08-25
incident: two of five artifacts were ignored and both experiments would have counted the session as
spent). `enactment.py` reconciles, and the evening pass scores the session that ended before issuing
the next:

- **enacted** — the loop's recorded decision matches the artifact's admitted params. A reject-all
  artifact counts: the bounds refusing it is a real outcome.
- **carried** — issued, no NEW decision, but the admitted params are demonstrably in force. Costs the
  experiment nothing and is reported silently. Three routes, each discovered from the module's schema:
  - *open rows* — params frozen onto positions this module still holds (calendars enters weekly,
    earnings only when a name reports; `calendars.management.effective_params` reads the stamp back
    so lapsing advice never hands an open position to rules nobody chose);
  - *exits* — the session's only advised activity is closing positions dated to THIS session, each
    stamped with params covering the artifact's (close date from `closed_session`, else epoch
    `closed_at` read with 'localtime'; an undated close proves nothing);
  - *scanning* — the loop applied the params but its scan accepted no candidate, so the advice had
    nothing to decide. Discovered from a `scan_log` table with an `outcome` column
    (`factpack.candidates_accepted`), so it reaches earnings and nothing that keeps no scan. **A
    declared break for the counter on 2026-09-16: do not pool earnings `sessions_run` across it.**
- **not_enacted** — issued, and the loop's record disagrees or is absent. Costs the experiment
  nothing, because it bought nothing.
- **no_artifact** — nothing issued; nothing to reconcile.

Properties not to break:

- **The discriminator is the module's own schema, not a list.** A module that freezes
  `advice_params` onto position rows can carry; one that does not cannot. meic and flies are flat
  overnight and stamp nothing, so `not_enacted` keeps its full force there.
- **Carry is only ever claimed for the CURRENT session.** Open rows describe now and prove nothing
  about last week; an unprovable past session keeps the conservative verdict. When a recorded
  decision disagrees with the artifact, the params comparison wins.
- The live read is `factpack.carried_advice_params` (every live read of another package's ledger is
  fenced in `factpack.py`); `enactment` forms the verdict.
- Counting is idempotent (the evening pass is re-runnable) and attributed by the experiment id on the
  artifact. The counter and its `counted` journal row commit together; `kill` writes verdict and
  status in one statement.
- `advice_enacted` rides on every pack. The mid-session check is deterministic: the orchestrator's
  `_check_advice_enactment` runs `python -m cherrypick.advisor enactment` by subprocess between
  10:30 and 16:30.
- `recount` re-derives history from the write-once fact packs (which snapshot `advice_active`). A
  session with neither pack nor surviving decision file is `unknown` and **kept** in the count —
  dropping it shortens an experiment on missing evidence.

## Verdicts are computed, not written

`verdicts.py` computes the comparison deterministically (ledger readers → `compare_profiles` →
`qualify_readings`), always with **the module's own qualification rule**, never the library default.
The model only *recommends*; its recommendation is stored beside the numbers, never instead of them.
A verdict below the qualification thresholds is labelled `underpowered` — never silently passed or
failed. Stored verdicts are **recomputed every time a recommendation is attached**.

**A `kill` recommendation is actioned; `keep` and `promote` are recorded** (`advisor.kill_on_verdict`,
on by default; off restores record-only). Admitting a kill takes the same path as a human `kill`:
status `killed`, verdict stored with the model's block, the queue moves up, and the next session's
artifact is **re-issued on the spot** for the successor — or **retracted** when nothing succeeds, so
the loop runs baseline. Retraction touches only an artifact stamped with this advisor's tag. A CLI or
console `kill` re-issues the same way, so a kill after the 17:00 pass leaves no stale artifact.

## Checkpoints and admission

- The schedule is **`advisor-deep` at 17:00 and nothing else.** Light slots cannot issue anything
  (only the deep slot's `enact` writes artifacts) and, measured over 36 of them, produced almost
  nothing actionable.
- A slot whose model call produced no reply is a **failed checkpoint row** (`checkpoint-failed`), and
  stays re-runnable.
- `admit` carries the freeze itself (`--force` to override), because the console reaches it directly;
  re-admitting the same reply resolves to the same experiment rather than queuing a duplicate.

## The fact pack

**The budget is an ATTENTION budget.** The deep pack (~130–160k tokens) fits a 1M context and costs
about $1 a day; what an oversized pack costs is a finding inside it going unread. The rule is **"cut
the largest section; do not raise the ceiling."** Ceilings: light 48,000, deep 200,000.
`test_the_ceilings_are_pinned_so_a_raise_is_a_deliberate_act` fails on any move, and
`test_the_deep_ceiling_stays_below_the_pack_it_is_meant_to_constrain` keeps the bar under the real
pack so it keeps reporting over-budget (the deep pack is still ~1.8x its ceiling, recorded rather than
papered over). Measure against the real pack, as `store.write_json` serialises it (indent=2) — a
seeded fixture stayed green while the real pack tripled.

What is in and out, deliberately:

- **Journal tapers by age**: the last `JOURNAL_FULL_SESSIONS` (2) keep full payloads; older entries
  keep identity (title, module, kind, fate, reason).
- **Flags taper by severity**: `critical` verbatim at any age; the rest keep module, severity and a
  120-character stub past the window. The elision rule is stated once on `_taper`, not per flag.
- **Concluded experiments appear once**, in `experiments_full.concluded`. `experiments_full` carries
  identity, the overlay, a 240-character prose stub and ONE compacted verdict per experiment (fresh
  for an active one, the stored final body for a concluded one).
- **`pending_proposals`** is elided in the deep slot only (the journal carries the session there).
- **`arm_readings` is NOT cut**, retired arms included: a retired arm's reading is evidence.
- **`cmd_factpack` returns a `budget` block**, reported and never fatal: a size check must not cost a
  session its advice.
- **"Could not measure" is never zero.** `store.rows` records every refused query and the pack lists
  them as `query_errors`; `settled_with_no_price_today` and `control_fired` read `null` when their
  query was refused; an unreadable module config reads `null` with `config_read: false`, never "live
  trading off".
- **Regime is read at the close**: clamped to the calendar's RTH close (`clock.rth_close_iso`, 13:00
  on a half day), so a pack rebuilt for a past date describes that date. MEIC's regime is a
  distribution (`regime_session`: bucket counts over 09:30–close ticks, `post_close_ticks` counted
  and excluded, the gex bucket re-derived from the sign flag where the stored tag reads `unknown`).
- Prompt caching does not apply (`claude -p`, a fresh process per run, one run a day); adopting it
  would hand the script an API key.

**`regime_cuts`** (deep-only; flies and MEIC) carries each module's nightly regime-cuts artifact
thinned, era-scoped by the module's `measurement_breaks` journal (rules in `cherrypick.core.regimecuts`),
read from `data/<module>/regime_cuts.json`. Absent or an unreadable `cut_version` is `{"_absent": ...}`;
another session's artifact carries `_stale`. Thinning rules: books under three sessions collapse to
`_thin_books`; dimensions under 50% coverage or degenerate are dropped and named under `_dropped`;
cross-tabs keep the six largest non-thin cells; every cell is ONE string, sessions first; **a thin
cell carries no P&L at all** (a hand-cut two-dimension read once made a one-cell effect look
predictive). Single-dimension cells may carry a trailing ` fragile`; per-module `paired` (p < 0.10)
and `sign_changed` lists are capped at `REGIME_CUTS_LIST_MAX`, strongest first; `multiplicity` is
carried verbatim; `underpowered` appears only when true. flies' `gate_replay` rides here as one string
per replayable gate rule, and its additive book keys (`completion_latency_min`, `miss_gap`) are
copied when set — `cut_version` stays 1 because every reader uses `.get`. The leg-level
`refuse_completion_against_trend` gate stays out of `advice.bounds` on purpose.
`test_factpack.py` bounds a synthetic far case under **72 KB**; it measures 71.9 KB, so **the next
addition to this section cuts something first**. Raise that guard only with a fresh measurement of the
real section beside it.

**Measurement breaks for the advisor's input** (proposals either side were made on different
evidence, and are not pooled): **2026-08-26** (journal/flag taper and deep-only schedule, batched as
one), **2026-09-14** (compacted `experiments_full`), **2026-09-22** (first deep pack reading
`regime_cuts`), **2026-09-28** (robustness stamps and `gate_replay`, at the first deep checkpoint
reading them). Plus the 2026-09-16 counter break for earnings above.

## The module list is derived, not restated

`bounds._BASE_KEY` is the source of truth. `MODULES`, `enactment.MODULES`, `factpack.MODULES` and
`settings.DEFAULTS["modules"]` all derive from it — hand-kept copies once silently left bwb and curve
with no advice at all. A module in `_BASE_KEY` still needs its own `advice.enabled` (curve is listed
and disabled, which reports itself), and **must** have a `factpack` section, or the model is asked to
design experiments for a module it cannot see. `tests/test_factpack.py` pins both directions.

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> ⚠️ Suite-wide guardrails apply — see root CLAUDE.md. On top of those:
> - **No AI, no network, no broker.** No `tastytrade`, `keyring`, `requests`, `socket`,
>   `cherrypick.core.auth`/`broker`, `core.dxfeed`, `core.streamer` or `core.streamrequests` import
>   may appear in `src/` — enforced by a source scan (`tests/test_guardrails.py`), and
>   `packages/core/tests/test_no_model_client_in_packages.py` scans every package's source and
>   declared dependencies for an AI client. **This stays a hard ban even though the suite-wide rule is
>   a preference**: if a model could be reached from in here, the validating side and the validated
>   side would be the same process, and the validation would stop meaning anything. The fence is the
>   product.
> - **Writes are confined** to `data/advisor/**` and `state/advice/*.json` — enforced by a file-tree
>   snapshot test around a full factpack→admit→enact run.

## Tool Reference

| Command | Purpose |
|---|---|
| `python -m cherrypick.advisor init-db` | Create/migrate `data/advisor/advisor.db`. Idempotent. |
| `python -m cherrypick.advisor factpack --slot {open,am1,am2,midday,pm1,pm2,close,deep} [--session D]` | Build one deterministic fact pack and print its path. |
| `python -m cherrypick.advisor admit --slot S [--session D] --raw <path> [--force]` | Parse a raw model reply, validate every proposal against module bounds, record admissions and rejections. |
| `python -m cherrypick.advisor checkpoint-failed --slot S [--session D] --error <text> [--model A] [--model-id M]` | Record a slot whose model call produced no reply as a failed checkpoint row. |
| `python -m cherrypick.advisor enact [--session D]` | Issue the next session's advice artifact for every active experiment. Runs nightly, unconditionally. |
| `python -m cherrypick.advisor enactment [--session D]` | Did each module apply the artifact issued for a session? Per module, with the reason when it did not. |
| `python -m cherrypick.advisor recount [--apply]` | Re-derive `sessions_run` for every active experiment from what the loops recorded. Read-only without `--apply`: it rewrites the denominator every verdict is judged against. |
| `python -m cherrypick.advisor verdicts [--session D]` | Compute deterministic verdicts for expiring experiments. |
| `python -m cherrypick.advisor status [--session D]` | Checkpoints, experiments, apply status per module. |
| `python -m cherrypick.advisor kill <experiment_id>` | Stop an experiment now. Journaled; a queued experiment activates and the next artifact is re-issued for it (retracted when nothing is queued). A model `kill` verdict runs this same path. |
| `python -m cherrypick.advisor dismiss <proposal_id>` | Mark a proposal dismissed. Fed back to the model so it stops re-proposing it. |

The console's two write actions (kill, dismiss) invoke exactly these verbs as a subprocess — the
console holds no advisor logic.

## Where the shared rules live

- `cherrypick.core.advice` — the one validator, called by the producer (here) and every loop-side
  consumer, so the two sides cannot disagree. Also `params_map`, `stamp_for` and `disabled_reason`
  (which `bounds.resolve` uses). Within this package the artifact literal is built in one place,
  `experiments.artifact_for`, for both `check_params` and `enact._issue`.
- `cherrypick.core.ledgers` — per-schema net/risk/session rules. Do not add another implementation;
  its docstring records what happened the first three times.
- `cherrypick.core.profiles` — `compare_profiles` and `qualify_readings`, the suite's one
  arm-comparison and promotion gate.
- Deliberately NOT folded, because the difference is the point: the dotted-param split (`bounds`
  takes the first dot for earnings' strategy prefix, `enactment` the last for the stamped leaf) and
  the two atomic-write helpers in two packages.
