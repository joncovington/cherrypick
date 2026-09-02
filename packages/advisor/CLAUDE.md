# cherrypick-advisor — Operational Instructions

> Operating contract for the suite's **AI advisor machinery**. Suite-wide context is in the root
> [documentation index](../../docs/README.md).

This package exists so an AI can influence the paper books **without any AI running inside a suite
package**. It holds every deterministic part of that arrangement: the fact packs the model reads,
the parse-and-validate of what it replies, the experiment lifecycle, and the nightly issuing of
bounded advice artifacts through `cherrypick.core.advice`.

## The fence

**This package never invokes AI, holds no API key, and opens no socket.** The one AI touchpoint is
`scripts/advisor_checkpoint.py`, outside every package — the same fence that holds
`scripts/eod_narrative.py`, for the same reason: `packages/*` is what the trading loops import, so a
script the scheduler runs can never be imported by a loop. Deleting the script costs the advice and
nothing else; the loops keep running on baseline, which is exactly what `core.advice` guarantees
when advice is absent.

The model gets one channel in and one channel out. In: a fact pack on **stdin**, with tools denied.
Out: strict JSON, parsed here, validated here against the module's declared bounds.

## It can never touch a live account

The advisor reads live facts — they are context a competent observer would want, and hiding them
produces worse advice about the paper books that shadow them. It cannot act on them:

- Live databases and live config keys are opened **read-only and only in `factpack.py`**. Every
  other module in `src/` is proven free of them by a source scan.
- The only loop-facing output this package can produce is a paper advice artifact at
  `state/advice/<module>-<session>.json`, and each module's consumer applies it to a **synthetic
  `advised:<base>` book beside its control** — never to the control, never to a live loop.
- Nothing here writes a module config, `config.risk.json`, or any module's database. New-profile
  and new-strategy ideas come out as `creative` proposals with ready-to-paste specs; a human
  applies them or does not.

## Paper arms are the dry run

There is no replay engine and there will not be one. A proposal's test is a **next-session paper arm
beside its control**, scored as sessions accrue through `cherrypick.core.profiles.compare_profiles`
and `qualify_readings` — the same qualification the suite uses for every other promotion decision.
That is why the default experiment length is 15 sessions: shorter, and an expiring experiment cannot
satisfy the promotion gate (min 14 days, min sample 20), so its verdict would be structurally
`underpowered`.

## An experiment outlives its advice artifact

Advice is single-session by contract — never sticky, always expiring. An experiment is not: it lives
in `advisor.db` with a base profile, a param overlay, an expiry and a journal, and every evening the
deterministic `enact` step **re-issues** the next session's artifact for each active experiment,
re-validated against the module's *current* bounds. So a human who tightens bounds tonight has
tightened them by tomorrow morning, without touching the experiment; and the loops still only ever
see one validated, expiring artifact through their existing read-once consumers.

`enact` runs **unconditionally** in the deep slot, even when the AI call failed. An AI outage must
never truncate an active A/B sample — that would corrupt the measurement, which is worse than
getting no advice.

## A session counts when a loop applied it, not when an artifact was written

`sessions_run` used to increment at issue time. Issuing an artifact is not evidence that a loop read
it, and the counter could not tell the two apart — so an experiment could spend its whole length on
sessions that bought it nothing and still be scored as though they had.

That is the 2026-08-25 incident. Five artifacts went out in one batch with zero rejections; three
were applied and two were not, and the two were meic and earnings — the modules whose experiments
had their most informative session available. meic's control filled 215 entries; earnings broke a
thirteen-session drought with four iron_condors. Both loops recorded `advice_disabled` against live,
valid artifacts, and both experiments recorded the session as spent. Earnings carries a
kill-at-session-6 rule, so on the old counter "the parameter produced nothing" and "the parameter
was never applied to a session that had trades" would have concluded identically.

So `enactment.py` reconciles the two sides, and the evening pass scores the session that just ended
before it issues the next one:

* **enacted** — the loop's recorded decision matches the artifact's admitted params. A reject-all
  artifact counts: the bounds refused it, which is a real outcome the experiment paid for.
* **carried** — an artifact was issued, the loop recorded no NEW decision, and the params it
  admitted are nonetheless in force: frozen onto positions an earlier session opened and this module
  still holds. Costs the experiment nothing either — a session it never re-decided bought no new
  evidence — but it is not a defect and is reported silently.
* **not_enacted** — an artifact was issued and the loop's record disagrees with it or is absent.
  It costs the experiment nothing, because it bought it nothing.
* **no_artifact** — nothing was issued; nothing to reconcile.

**`carried` exists because the first three states assumed every module decides every session, and
half of them do not** (added 2026-08-27). calendars enters once a WEEK and earnings only when a name
reports; both spend most sessions with nothing to decide while the advice they already applied stays
stamped on their open rows and governs them every tick — `calendars.management.effective_params`
reads that stamp back deliberately, so advice lapsing mid-week can never hand an open position to
rules nobody chose. Scored as `not_enacted`, calendars alone raised the watchdog's WARN four days in
five, forever, and a check that cries wolf 80% of the time cannot catch the case it was built for.

Two properties worth not breaking:

- **The discriminator is the module's own schema, not a list.** A module that freezes
  `advice_params` onto its position rows CAN carry; one that does not CANNOT. meic and flies are
  flat overnight and stamp nothing, so `not_enacted` keeps its full force there — if either quietly
  stopped recording decisions it is still reported. A blanket "a missing decision is sometimes fine"
  rule would have thrown that away.
- **Carry is only ever claimed for the CURRENT session.** The evidence is the ledger's OPEN rows,
  which describe now and prove nothing about last week; an artifact whose params happen to match
  what is open today would otherwise be scored `carried` for every past session it was issued for,
  on evidence that post-dates them. A past session that cannot be proved keeps the conservative
  verdict and stays out of the count, erring the same direction `recount`'s `unknown` bucket does.

**Exits of pinned positions carry too (2026-09-01).** earnings closed 13 advised condors at 09:45 —
every one managed under the frozen params the artifact admitted — then held nothing open, so the
open-row read alone raised the WARN hourly from 10:31 until the 15:35 entry pass recorded a
decision. A session whose only advised activity so far is closing positions dated to THIS session,
each stamped with params covering the artifact's, is `carried`: the advice governed every decision
the module actually made. The close date is discovered from the schema (`closed_session`, else
epoch `closed_at` read with 'localtime'); an undated close proves nothing and does not carry. This
cannot weaken the 2026-08-25 case — a module reaches an exit-only morning with no decision only
when no entry pass has run, and the moment a recorded decision disagrees, the params comparison
wins. Exit carry stays current-session-only like open carry, deliberately: for counting the
distinction is moot (neither `carried` nor `not_enacted` advances the counter).

The live read this rests on is `factpack.carried_advice_params` — in `factpack.py` because every
live read of another package's ledger is fenced there by contract. `enactment` forms the verdict;
factpack only reports what the rows say.

Counting is idempotent (the evening pass is re-runnable by design) and attributed by the experiment
id stamped on the artifact, so a session issued under one experiment and scored after it was
replaced lands on the one that paid for it. `advice_enacted` rides on every slot's pack, not just
the evening one, so a dropped artifact is visible at 10am rather than in the verdict that scores it.

History is re-derivable because the fact packs are write-once and already snapshot each module's
`advice_active`. `recount` reads them. Where a session has neither a pack nor a surviving decision
file, nothing is provable and it is reported `unknown` and **kept** in the count — dropping it would
shorten an experiment on the strength of missing evidence, the same error in the other direction.

## Verdicts are computed, not written

`verdicts.py` computes the comparison deterministically (ledger readers → `compare_profiles` →
`qualify_readings`). The model only *recommends* over those numbers, and its recommendation is
stored beside them, never instead of them. A verdict that fires below the qualification thresholds
is labeled `underpowered` — never silently passed or failed.

## The advisor tunes only its own experiments

Structurally: the only thing it can emit is an `advised:*` overlay. A `tune` proposal naming a
control arm, a human-configured profile, or an unknown id is rejected `not_an_advisor_experiment`.

## The pack has a budget, and it is now enforced against the real pack (2026-08-26)

**Measurement break for the advisor: proposals either side of 2026-08-26 were made on different
evidence.** The journal the model reads is tapered from this date, so a thread it could previously
re-read in full now reaches it as a title beyond two sessions, and an aged non-critical flag reaches
it as a 120-character stub. Nothing about the ledgers changed — this is a change to the advisor's
INPUT, and its output should be read with that in mind.

Both taper passes — the age taper and the severity taper that corrects it — land on **this same
date**, deliberately, per the suite's rule that measurement-affecting changes batch to a declared
boundary. The second was written the same session the first was measured; landing it a week later
would have split one input change into two breaks and left a stretch of evidence comparable to
neither side.

The deep pack had grown from 250KB (2026-08-17) to **731KB** (2026-08-26), about +65KB a session,
against a stated ceiling of 150KB. Nothing caught it because `tests/test_factpack.py` measures a
seeded fixture — a pack nobody reads — so the check was green the whole way.

Where it was: `advisor_journal` at 466KB of the 690KB, being 46 checkpoints and 76 proposals carried
verbatim. The ten-session window was never the problem; the prose per session was. A creative
proposal runs ~7.7KB.

What changed:

- **The journal tapers by age.** The most recent `JOURNAL_FULL_SESSIONS` (2) keep full payloads and
  observations; older entries keep identity — title, module, kind, fate, reason. That is what "do
  not re-propose what was dismissed" actually needs.
- **Flags taper by SEVERITY, and the first attempt at this cut the wrong half.** Flags were
  originally exempt at every age, on the reasoning that a flag is a standing caveat about a module
  and must not age out of view. That is right about `critical` and wrong about the rest, and it went
  unmeasured: flags were **97.6KB of the checkpoints section's 120KB**, while observations — the
  half the taper did cut — were 12.3KB. Twelve `critical` flags cost 8.3KB; 113 `warn` and 97 `info`
  cost 86.5KB. So `critical` is now carried verbatim at any age and the rest keep module, severity
  and a 120-character stub past the window. Two things were measured before the fix was written:
  the flags are **not** repetition (222 instances are 222 distinct texts, so the dedup fix drafted
  first would have saved nothing), and the per-flag elision marker was itself ~13KB of the same
  sentence repeated, so the rule is stated once on `_taper` instead.
- **Concluded experiments are carried once**, by `experiments_full.concluded`, not also by the
  journal. The same seven were appearing twice in two shapes.
- **`pending_proposals` is deep-slot-only elided**, since the journal already carries this session
  in full there. The light slots keep it: they have no journal, and compounding earlier slots is
  the whole reason it exists.
- **`cmd_factpack` returns a `budget` block** that `scripts/advisor_checkpoint.py` already reads.
  Reported, never fatal: a size check must not cost a session its advice.
- Measured the way `store.write_json` serialises — indent=2 — because that is the file handed to
  the model. A compact measure understates it by about a quarter.

Result: **731KB → 472KB → 425KB deep.** Still 2.1x the ceiling, and that is recorded rather than
papered over.

### The budget is an ATTENTION budget (correction, 2026-08-26)

The ceilings were originally derived from token targets and the warning was worded like a resource
overrun. That framing was wrong, and it is probably why nobody acted on the warning for nine
sessions: **neither thing a size limit usually protects was ever at risk.** Both were checked rather
than assumed. The deep pack is ~130–160k tokens against a **1M-token context window** — about 15%,
so it could quadruple and still fit. And at list rates the schedule costs on the order of **$1 a
day** — the deep slot dominates it, and the light slots that were dropped the same day were about
$0.20 of that.

What a 425KB pack actually costs is that a finding inside it goes unread, which is the same failure
as not recording it. `pack_size`'s warning says so now, and says "cut the largest section; do not
raise the ceiling."

**Prompt caching does not apply as invoked, and was checked before being ruled out.** `cache_control`
is a Messages API request parameter; the checkpoint shells out to `claude -p` with the pack on
stdin. Each run is a fresh process with no prior turn, and caching is strict prefix-match while the
pack changes every slot. There was a structural opportunity while several light slots shared a day's
stable prefix, but harvesting it meant reordering the pack stable-first and moving onto the SDK,
which hands `advisor_checkpoint.py` an API key — not worth a network credential on the one path the
fence keeps thin. **It is moot as of the deep-only schedule below**: one run a day has no prefix to
share with anything.

## The schedule is deep-only (2026-08-26)

`advisor-deep` at 17:00 is the whole schedule. Seven light intraday checkpoints were cut to one on
08-21 and to none on 08-26, on this package's own record rather than on preference.

Light slots **cannot issue anything** — the only loop-facing output is the artifact `enact` writes
in the deep slot, for the next session — and empirically they did not draft much either. Across 36
light checkpoints: **4 proposals, all `creative`** (the kind a human pastes or ignores, which no
code path acts on), **zero** `experiment_spec`/`tune`/`verdict`, and **one** critical flag, which
that evening's deep slot re-derived more precisely off the settled numbers rather than a mid-session
mark. `midday`, the last one standing, produced **zero proposals across its entire history**. Deep,
over the same four sessions, produced 30 proposals and 4 criticals.

They also cost the deep slot directly: light checkpoints were ~43KB, about **10% of the deep pack**,
which is more than the flag taper above saved.

The one thing they were nominally for — `advice_enacted` visible at 10am rather than in the evening
verdict — is deterministic, and no light checkpoint ever caught one. It is now the orchestrator's
`_check_advice_enactment`, which invokes `python -m cherrypick.advisor enactment` by subprocess
between 10:30 and 16:30. That is strictly better than a model checking it: it is free, it reports
the same way twice, and it fires on the exact incident (2026-08-25) that motivated the verb.

**This is a measurement break on the same declared 2026-08-26 boundary as the taper above.** The
deep slot no longer reads a day's intraday observations as compounding context, so its input changed
again — batched deliberately rather than landed as a second break a week later.

**The ceilings moved once, deliberately: light 32,000 → 48,000, deep 120,000 → 200,000.**
`test_the_ceilings_are_pinned_so_a_raise_is_a_deliberate_act` failed when they moved, which is what
made it deliberate rather than quiet. Its companion,
`test_the_deep_ceiling_stays_below_the_pack_it_is_meant_to_constrain`, is the anti-drift half: a
ceiling raised until it clears the artifact reports success forever, so the deep bar must stay under
the real pack and keep reporting over-budget.

The light move was made against measured evidence, not to clear the artifact. Across 35 stored light
packs the median is 28.6KB and the **maximum is 42,707** — the old bar was breached by honest packs,
because the ~8k-token light target predated the suite having seven modules. (An earlier note here
claimed a 108KB light pack. That number was a midday pack rebuilt at end of day, with a full day of
`pending_proposals` compounded into it — not a pack any slot ever sent.) Light bulk is `paper`
(~23KB), the module facts themselves, which is what the pack exists to carry.

`arm_readings` looks like an easy 14KB — meic carries 17 arms of which 15 are retired. It is
deliberately NOT cut: the advisor cited retired arms this week, and the twelve-session gate
retrospective rests entirely on them. A retired arm's reading is evidence, not dead weight.

## The module list is derived, not restated (2026-08-26)

`bounds._BASE_KEY` is the source of truth for which modules the advisor may act on. `MODULES` is
`tuple(_BASE_KEY)`, `enactment.MODULES` derives from it, and `factpack.MODULES` now does too — it
was a separate literal until 2026-08-26, which is how the package came to hold three hand-kept
module lists that disagreed.

**bwb and curve were missing from all of them.** Both consume advice through the same
`core.advice.session_decision` every other module uses, both declare an `advice` block, bwb declares
**twelve bounds** and the suite config already had `advisor.modules.bwb.enabled: true` — and no
artifact has ever been written for either, because the advisor's own map did not list them. The
module sat reading for advice that could not arrive, and the config granted the advisor a module no
code path could act on.

Two consequences worth keeping straight. A module in `_BASE_KEY` still needs its own `advice.enabled`
to be true before anything happens — curve is listed and disabled, which is a state that reports
itself, where being absent is not. And a module in `_BASE_KEY` **must** have a `factpack`
section: without one the pack can reconcile its enactment while carrying no facts about it, and the
model would be asked to design an experiment for a module it cannot see. `tests/test_factpack.py`
pins both directions.

## The cap is one per module, by construction

Each module's consumer builds exactly **one** `advised:<base>` book from the day's artifact, so one
active experiment per module is what the consumers can express. Over-cap specs are admitted as
`queued` and activate FIFO when a slot frees. More than one per module is a documented future
consumer extension, not a config knob that silently does nothing.

---
CRITICAL_GUARDRAIL: DO NOT WRITE CODE IN THIS FILE
---

> ⚠️ This file is strictly for build commands, tech-stack reference, and project guidelines:
> - **No code here** — no Python, no scripts, no logic, and no scratchpad content, changelogs, or task trackers.
> - **Mask account numbers** to the last 4 digits (`****1234`) anywhere they surface.
> - **Portable paths only** — never hardcode absolute paths, usernames, hostnames, or drive letters.
> - **Human-voice docs & commits** — never add AI/co-author attribution to commit messages.
> - **No AI, no network, no broker.** No `tastytrade`, `keyring`, `requests`, `socket` or
>   `cherrypick.core.auth`/`broker` import may appear in `src/` — enforced by a source scan
>   (`tests/test_guardrails.py`), not by prose.
>   **This stays a hard ban even though the suite-wide rule is now a PREFERENCE** for deterministic
>   over AI/agentic solutions. The difference is what this package is: the deterministic half of the
>   AI advisor — the fact packs a model reads, and the validation of what it replies. If a model
>   could be reached from in here, the validating side and the validated side would be the same
>   process, and the validation would stop meaning anything. The fence is the product.
> - **Writes are confined** to `data/advisor/**` and `state/advice/*.json` — enforced by a
>   file-tree snapshot test around a full factpack→admit→enact run.

## Tool Reference

| Command | Purpose |
|---|---|
| `python -m cherrypick.advisor init-db` | Create/migrate `data/advisor/advisor.db`. Idempotent. |
| `python -m cherrypick.advisor factpack --slot {open,am1,am2,midday,pm1,pm2,close,deep} [--session D]` | Build one deterministic fact pack and print its path. |
| `python -m cherrypick.advisor admit --slot S [--session D] --raw <path>` | Parse a raw model reply, validate every proposal against module bounds, record admissions and rejections. |
| `python -m cherrypick.advisor enact [--session D]` | Issue the next session's advice artifact for every active experiment. Runs nightly, unconditionally. |
| `python -m cherrypick.advisor enactment [--session D]` | Did each module apply the artifact issued for a session? The reconciliation, per module, with the reason when it did not. |
| `python -m cherrypick.advisor recount [--apply]` | Re-derive `sessions_run` for every active experiment from what the loops actually recorded. Read-only without `--apply`: it rewrites the denominator every verdict is judged against. |
| `python -m cherrypick.advisor verdicts [--session D]` | Compute deterministic verdicts for expiring experiments. |
| `python -m cherrypick.advisor status [--session D]` | What the advisor thinks is true right now: checkpoints, experiments, apply status per module. |
| `python -m cherrypick.advisor kill <experiment_id>` | Stop an experiment tonight. Journaled; a queued experiment activates in its place. |
| `python -m cherrypick.advisor dismiss <proposal_id>` | Mark a proposal dismissed by the user. Fed back to the model so it stops re-proposing it. |

The console's two write actions (kill, dismiss) invoke exactly these verbs as a subprocess — the
console holds no advisor logic, the same shape as its Config page.

## Where the shared rules live

- `cherrypick.core.advice` — the one validator. Both the producer (here) and every loop-side
  consumer call it, so a disagreement between the two sides is impossible by construction.
- `cherrypick.core.ledgers` — per-schema net/risk/session rules for `meic_ic`, `fly_book`,
  `earnings`. Do not add another implementation; that module's docstring records what happened the
  first three times.
- `cherrypick.core.profiles` — `compare_profiles` and `qualify_readings`, the suite's one
  arm-comparison and promotion gate.
