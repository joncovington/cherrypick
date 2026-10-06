# Intraday agent for flies: plan

**Status: design, not built (2026-10-05). It runs beside live from day one, in shadow.** The goal is stranding fewer legged entries. This plan
tests whether a model reading the session can beat a fixed rule at the two decisions that cause or
cap a strand. The suite prefers deterministic solutions (root CLAUDE.md), and leaning away from
that is a choice to write down. This document is that record.

## Why

Flies' losses are almost entirely stranded verticals, not costs (analysis of 2026-10-05).

| SPX, current era | Completed | Stranded verticals | Net per position |
|---|---|---|---|
| Paper `control` | 182, +$15,886 | 45, −$11,431 | +$19.63 |
| Live `control` | 52, +$3,704 | 16, −$3,753 | about −$0.71 |

**How a strand happens:**
- A completion order rests at the entry.
- On a trend day the price never comes back to it.
- The vertical then rides to settlement at full width, about −$250.

**Why a fixed rule falls short:**
- The 20-point trend-from-open gate (`engine._classify_trend`) cuts strands from 20% to 15% of
  positions (`advised:no-entry-on-up-trend`).
- It can't tell a trend that will last from a morning that will reverse. In meic's paper put
  spreads on up mornings, 3 of 14 days reversed, and their losing entries were opened within a few
  points of the high.
- A pullback rule on entries did not help.

So the question for the agent is whether it can read what price alone does not show: the call
wall moving, GEX draining, VIX1D rising while SPX is still up, breadth fading.

## The two decisions, and nothing else

1. **`trend_gate`, `on` or `off`.** `on` blocks new legged entries on the trend's losing side, as
   the rule does. `off` means the trend has failed, so entries resume.
2. **`close_stranded`, a list of position ids.** These are stranded verticals to close now rather
   than ride to settlement.

**What the agent cannot do:**
- It does not choose strikes, size or prices.
- It does not open anything.
- It does not touch a completed fly.

Everything else stays in `engine.py`.

## Shape

| Part | Where | Deterministic? |
|---|---|---|
| Fact pack: session spot path vs open, high and low; GEX readings; VIX/VIX1D/VIX9D; breadth; open positions and their marks; today's entries | `cherrypick.flies.intraday_pack`, a pure function over the stream cache, the GEX recorder's store and the paper ledger | yes |
| The model call | `scripts/flies_intraday_agent.py`, the `claude` CLI with the advisor's tool fence (no tools, no MCP, stdin only) | no |
| Validation: the schema, ids that exist, an expiry, and a reason of 40 words or fewer | `cherrypick.flies.intraday_advice` | yes |
| The decision artifact: bounded, expires after 10 minutes | `state/flies-intraday-advice.json`, `core.advice` conventions | — |
| Applying it | the paper loop, on one `advised:` arm only | yes |

If the artifact is missing, stale or invalid, the arm falls back to the fixed rule for that tick.
A failure costs that tick's advice and never a fill, the same posture as `advisor_checkpoint.py`.

**Cadence:** the agent runs only when there is something to decide:
- while a stranded vertical is open;
- while spot is within ±10 points of the trend band.

That is about 10–30 calls a session.

## The arms (one variable each)

- **`control`:** unchanged.
- **`trend-rule`:** control plus `refuse_completion_against_trend`, the existing drift gate. It
  refuses an entry whose completion needs the day to reverse a drift beyond `regime_trend_points`
  (20) from the open. It reads the drift fresh every tick, so it releases by itself once the day
  falls back inside the band. That is the reversal clause as built, simpler than the one first
  sketched here (fall back below +10, or net GEX turning negative). Its close rule
  (`close_tags.rule_wants_close`) fires on a stranded vertical that is through its short but not yet
  past its wing, on a day committed against it.
- **`intraday-agent`:** the same entry rules, with the gate switched on or off per tick by the
  agent's fresh decision (`paper_loop.tick_config`), and the closes it names. With no fresh decision
  (none yet, expired, inadmissible) it is `trend-rule`'s gate exactly. It is a plain arm, not an
  `advised:` book: the agent is the arm's variable, not advice applied on top of another arm.

**Closes are tagged, not executed** (`close_tags.py`). Paper flies has no intraday close: the
pre-close exit was removed in August, and settlement treats every position as open until the print.
So, as the hedge overlay does, a close is recorded on the row at the decision tick: its natural
debit, its mid and spot. The position settles as always. `run.py close-tags --arm <arm>`
(`analytics.close_tag_result`) values the arm as if every tagged close had been taken, at natural
and at the 2x haircut. A later completion is forgone, as a real close would forgo it.

**The agent is judged against `trend-rule`, not just against `control`.** If it cannot beat a rule
built from the inputs it sees, the rule wins: it costs nothing and fails the same way twice.

## Configuration, and the daily yes on live

**Config** (`~/.cherrypick/config/flies.json`, an `intraday_agent` block, off by default):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | The master switch. Off: the agent never runs, and the live start never offers it. |
| `model` | `"opus"` | The model alias, travelling on argv as the advisor's does. Never hardcoded. |
| `paper` | `true` | Whether the paper arm `intraday-agent` acts. Paper needs no daily yes. |
| `live_mode_max` | `"shadow"` | The most the live start may offer: `off`, `shadow`, `gates` or `gates_and_closures`. A ceiling, not a choice: the per-day selection picks within it, and the qualification below has to allow it too. |
| `trigger_band_points` | `10` | Run only within this distance of the trend band, or while a stranded vertical is open. |
| `decision_ttl_minutes` | `10` | How long one decision is valid before the arm falls back to the rule. |
| `max_calls_per_session` | `100` | A hard cap per session, so a stuck trigger cannot run up a bill. Raised from 40 on 2026-10-06, before the arms' first session: a vertical is open most of the day, so at a 4-minute gap a session asks about 95 times, and 40 ran out by about 12:20 on all 29 replayed sessions. |

**On live the agent is chosen per day, never by config alone.** `/live-flies-start` gains one step.
After the live YES, and only when `enabled` is true, it asks a second, separate question with the
AskUserQuestion tool: a selection of the modes allowed today.

| Option | What the agent may do on live |
|---|---|
| **No agent today** | nothing (always offered) |
| **Shadow** | records decisions against the live arm and acts on nothing |
| **Gates** | may block live entries on the trend's losing side; never places an order |
| **Gates and closures** | may also close stranded live verticals: model-chosen live orders |

**A mode is offered only when both of these allow it:**
- `live_mode_max` in config;
- the qualification file (below).

So the dialogue can never offer more than config, and config can never reach past the evidence. The
question shows, for each locked mode, what it is still waiting for.

The selection is written onto the same per-day arm record (`state/flies-live-arm.json`):
`intraday_agent: {mode, model, confirmed_at}`. The record self-disarms at `live.disarm_time` like
everything on it, so tomorrow needs a fresh yes.

**Where the yes is checked:**
- The agent script reads the arm record and runs for the live arm only when the yes is on it.
- The live loop reads a live decision only in the mode the record names.
- A "No", no record, or a disarm leaves live exactly as it is without the agent.
- `--stop` clears the agent with everything else.

The `--status` readout before the question shows the config block and today's agent state, so the
yes is informed, the same rule as the live YES.

**As built (step 4, 2026-10-06):**
- `python -m cherrypick.flies.live_loop --agent-mode <mode>` writes the selection. It refuses, and
  writes nothing, unless today's arm record exists and the mode is in
  `intraday_eval.offered_live_modes`. Those are the qualification file's `offered_modes` (off and
  shadow while there is no file), narrowed again by `live_mode_max` as config stands now.
- `--status` carries an `intraday_agent` block: the config, `mode_today`, `offered_modes` and each
  criterion with its value.
- **There is no live closing-order path.** `intraday_advice.LIVE_CLOSES_BUILT` is false, so
  `gates_and_closures` is never offered, and a record naming it runs as `gates`. On live, the
  agent's named closes are tagged at live natural on the live rows (close_tags.py), exactly as on
  paper. That is how the shadow's closes are scored against real live quotes. Building the order
  path is its own change, and it flips that constant.
- The live loop reads the live decision once a tick (`intraday_advice.live_tick`). In `shadow` it
  changes nothing. From `gates` up, a fresh decision sets the live arm's trend gate. With no fresh
  decision, the arm keeps its own declared gate.
- Every live entry carries `agent_mode`, `agent_gate` and `agent_would_refuse`: whether the
  decision in force would have refused it, by the gate's own test (`completion_opposes_drift`).
- The supervisor job `flies-intraday-agent-live` runs the agent on the live arm each minute of the
  session. It exits at once unless today's record names a mode.

**How the shadow is scored** (`intraday_eval.shadow_scoring`):
- Per live session run in `shadow`, the gate's live effect is minus the settled net of the entries
  it would have refused.
- The paper effect is the agent arm less control on the same session: the same gate, applied.
- A session where neither moved is no evidence and is not counted.
- `live_shadow` passes on at least 5 counted sessions with at least 80% agreeing in sign.
- `shadow_closes_live` passes on at least 10 shadow closes tagged at live natural that still save
  money.
- Both thresholds are choices made here (the plan named the bar, not the numbers), recorded as
  constants beside the others.

## Unlocking gates and closures: how much evidence

The agent is not trained: no weights change, and it reads each pack fresh. What it needs before
acting on live is **evidence**: enough sessions to show it beats `trend-rule` by more than luck.

**From flies' own paper `control`** (SPX, era from 2026-08-21, measured 2026-10-05):
- 30 sessions, +$149 mean and a $385 standard deviation per session.
- 23 of the 30 had a stranded vertical, costing about $497 on each.
- Paired arms correlate at about 0.78 (control against `no-entry-on-up-trend`, 11 shared sessions).

**Sessions to detect an agent edge D over `trend-rule`** (paired, one-sided 5%, 80% power, at 0.78):

| D, per session | about 200 | about 150 | about 100 | about 50 |
|---|---|---|---|---|
| sessions | 11 | 19 | 42 | 170 |

$100 is the working guess: about half the strand cost, less the good entries a gate also blocks.

**The qualification file** (`data/flies/intraday_agent_qualification.json`) is written by a
deterministic evaluation over the replay, the paper arms and the live shadow, never by the agent
(`intraday_eval.py`; the paper loop rewrites it after each settlement, and `run.py agent-eval
--write` on demand). The console's `/flies/agent` tab reads it, and step 4's live selection will
read the same `offered_modes`, so the page and the dialogue cannot disagree. A criterion the suite
cannot score yet (the live shadow's sign agreement, its re-priced closes) is `pass: null`, and null
never unlocks. It unlocks:

- **`gates`:**
  - at least 20 sessions with the agent's decisions recorded (the replay counts once its look-ahead
    test passes);
  - the agent arm beats `trend-rule` on net per session at one-sided 95%, and strands no more often;
  - at least 5 of those sessions from the live shadow, agreeing in sign with paper.
- **`gates_and_closures`:** everything above, plus:
  - at least 60 stranded-vertical episodes with a close decision;
  - the closes' edge survives the 2x spread haircut;
  - the shadow's would-be live closes, re-priced at natural against real live quotes
    (`fly_order_path`), are still positive.

**The thresholds are re-derived** from the data at each evaluation (the 0.78 rests on 11 sessions).
The file records the numbers it judged on, so an unlock can always be checked.

**Expected timing:** the replay supplies about 30 sessions now. With about a month of forward
sessions, gates could plausibly qualify. Closures need about two months.

## Paper fills against live fills

Paper and live fill differently. The design answers this in three ways.

**1. The comparison is paired, inside one fill regime.** All three arms run in paper on the same
ticks and the same fill model. Most fill bias is shared and cancels in the difference. The claim
being tested is "the agent's arm beats the rule's arm", not "this is what it makes live".

**2. Each decision depends on fills differently.**
- **`trend_gate` involves no fill.** It only stops entries, so its effect is in which positions
  exist, and that transfers to live as it is.
- **`close_stranded` is a closing order in a running market**, the most fill-sensitive thing here,
  and there is no live history of closing a stranded vertical: the pre-close exit was removed in
  August. So paper prices every close at the **natural price** (pay the ask on the short, take the
  bid on the long), not mid less a concession. It also records the four leg quotes at the
  decision, so the close can be re-priced later under any model.
- **The result is reported twice:** at natural, and at natural plus a 2× spread haircut. An edge
  that only survives at the first is not one.

**3. Live runs from day one, in shadow, beside the paper arms.** Everything starts together rather
than in sequence:
1. **Live shadow (day one).** The agent runs against the live arm with the same cadence and inputs.
   Its decisions are recorded, never acted on. Live's real outcomes (completed or stranded,
   `fly_live_orders`, `fly_order_path`) score it, so paper and live decisions on the same session
   can be compared from the first day.
2. **Live gate (an option from day one, otherwise after the evidence).** `trend_gate` may act on
   live: it only blocks entries, so its worst failure is a missed fly and it never places an order.
   It still falls back to the fixed rule on a missing or stale decision, and the live loop's own
   gates (arming, the buying-power cap, the halt flag) sit in front of it unchanged.
3. **Live closes (only after the evidence).** `close_stranded` places a live order chosen by a
   model, the first AI output that would cause a live trade in this suite. That takes the paper
   edge surviving the 2x haircut, the live shadow agreeing, and an explicit written decision.

Each rung is a declared boundary with a `measurement_breaks` row.

The fill-realism work (`docs/fill-model.md`) already narrows the gap. Since 2026-10-05, paper
completions pay the live limit. And 43 of the first 48 live completions filled better than paper's
modelled debit. Neither changes the plan. Both are why the comparison is paired rather than absolute.

## Answering in weeks: the historical replay

The fact pack is a pure function over stored data, so the agent can be run over past sessions
before any live session is spent on it.

**The data:**
- GEX regime readings for 48 sessions (from 2026-07-29);
- minute market readings for 30 (from 2026-08-24);
- spot for 76;
- flies' paper ledger and marks for the current era (from 2026-08-21).

**Safeguards:**
- The model's knowledge ends in June 2026, so it cannot know what those sessions did. Dates are
  still stripped from the pack.
- The pack at time T holds only what was recorded by T. A test checks this, the same rule as
  `core.rangefeatures`' look-ahead guard: the replay is only worth its look-ahead test.

**The replay scores the gate, and only the gate** (as built 2026-10-06, `intraday_replay.py`,
`scripts/flies_intraday_replay.py`, Opus only by the user's choice):
- For the gate: which entries would not have been taken, valued from the ledger's settled outcome.
  Each `control` entry stamped its drift and the direction it needed, so the gate's refusal is
  exact. A decision counts only once made, and only within its expiry (mutant
  `flies-replay-decision-lookahead`).
- **Not the closes.** The plan assumed recorded marks to price a close at natural. Paper never
  recorded intraday quotes (marks are live-only), so a past close cannot be priced honestly. The
  model's close decisions are kept with their packs, and closes are valued by the forward arms only.
- The packs show `control`'s positions, the arm being scored. The agent arm's own book did not exist.
- Replayed sessions count toward the gate criteria in the qualification, marked `replay`, and never
  replace a forward session (mutant `flies-replay-keeps-forward`).
- Paced and resumable on the Max plan: `--max-calls` per run, `--dry-run` to count, and a session
  counts once it has run to the bell.

`trend-rule` gets the same replay, for free. That makes it a paired, already-out-of-sample read
over about 30 sessions, and the deciding evidence for whether the forward paper arm is worth
building at all.

## Cost

Measured on 2026-10-05 with one fenced call per model on a representative 4 KB snapshot:

| Model | Per call | Latency | Note |
|---|---|---|---|
| Sonnet 5 | $0.069 | 12 s | reasons from the data (named the call wall move, the tick, GEX draining) |
| Haiku 4.5 | $0.042 | 43 s | wrote 4,100 tokens to answer in four lines; its reason was not in the data |

About 9,000 tokens of each call are the CLI's own overhead, so a larger pack barely moves the price.

**With the real pack (measured 2026-10-06 through `run_tick`):** $0.11-0.125 a call, 5-16 s, on
recorded packs of about 1,700 tokens. That is above the probe's $0.069: the probe's pack was smaller.

**Running cost** (the per-month figures below are at the probe's price; at the real price the
event-driven cadence is about **$25-80 a month**, and the replay about **$100**):
- At 10–30 calls a session (21 sessions a month), Sonnet costs about **$15–45 a month**.
- A 5-minute cadence would be about $113.
- The replay over 30 sessions is about 900 calls, about **$60 once**.
- These are API-equivalent costs. On a subscription they draw on usage limits instead.

For scale: live strands about 8 verticals a month at about $250 each.

**As configured from 2026-10-06 (Opus, about $0.17 a call, a 4-minute gap, cap 100):** the
estimates above assumed calls only near the trend band. The open-vertical trigger fires whenever a
vertical is open, which is most of the session: 1,020 of 1,160 calls in the replay's dry run.
- So a session asks about 95 times, about **$16 a session** and **$340 a month** for the paper arm,
  and as much again on each live-shadow day.
- The replay, Opus only, is 2,604 calls by its dry run (**about $440 once**). It runs in paced batches
  (`scripts/flies_intraday_replay.py --max-calls`), because on the Max plan these are usage limits,
  not a bill.

## What counts as a result

Each figure is per session, against `trend-rule` on the same sessions.

**What is measured:**
- stranded verticals;
- net per position;
- daily marked equity drawdown;
- closes priced at natural and at the 2× haircut.

**The bar:** the agent arm beats `trend-rule` on net per position at the 2× haircut **and** strands
no more often, over at least 20 sessions that crossed the trend band. A tie goes to the rule.

The replay counts toward the 20 sessions only if its look-ahead test passes.

## Every pack is kept, and the end goal is a rule

**Every pack the agent reads is stored** with its decision, its reason and, once known, its outcome:
whether a blocked entry would have stranded or completed, and whether a close beat riding to
settlement. This happens on paper, in the replay and in the live shadow alike
(`data/flies/intraday_agent/<session>.jsonl`).

**The agent is a research instrument; the goal is a deterministic rule.**
1. The agent's reasons on its correct calls name candidate features: the call wall moving since the
   open, net GEX's change, VIX1D against its open.
2. Those features become measurable columns over the stored packs.
3. A plain rule is fitted on them (thresholds or a small tree, the way the 20-point trend band was
   fitted), on the earlier sessions only.
4. It is tested on the later sessions beside `control`, `trend-rule` and the agent.
5. The simplest arm that is not measurably worse wins. If the derived rule keeps most of the agent's
   edge, it replaces the agent: it costs nothing, it is reproducible, and it replays over any month.
   If it does not, the gap is what the agent's judgement is worth, and that decides whether to keep it.

**The fit/test split is declared before the fit.** About 40 sessions of evidence is small, and a rule
fitted and scored on the same days flatters itself.

**Paper does not depend on live.**
- The paper arm, the stored packs and the replay run every session the module runs, armed or not.
- Only the unlock bar's "5+ live-shadow sessions" needs armed days.

## Order of work

Days, not months, and nothing waits on anything it does not need:

1. **The fact pack and its look-ahead test.** Deterministic, and the one prerequisite of everything
   below.
2. **In parallel, from the first session after it lands:**
   - the agent script, the validation and the artifact;
   - the paper arms `trend-rule` and `intraday-agent`, acting (from 2026-10-06; the supervisor's
     `flies-intraday-agent` job runs the agent each minute of the session, within its own budget);
   - the live shadow, recording.
   - **Built (2026-10-06):** the selection, the live job, gates mode and the shadow's scoring
     (above). Live closing orders are not.
3. **Also in parallel:** the historical replay of `trend-rule` and the agent over the ~30 recorded
   sessions, **once per model** (Opus and Sonnet, about $255 together). Each is scored against
   `trend-rule` on the same sessions, and the cheaper model is kept unless the dearer one wins by
   more than its cost.
4. **When the replay and the first forward sessions agree:** the live gate (if not already on from
   day one), and later the closes.

## Decided (2026-10-05)

- **Day one on live: shadow only.** The agent records decisions against the live arm and acts on
  nothing live. The paper `intraday-agent` arm acts. The live gate waits for the replay and
  the first forward sessions to agree.
- **Model: Opus 5.5** (changed from Sonnet on 2026-10-06). The alias lives in config
  (`intraday_agent.model`), never in code, as the advisor's does. On the first two recorded moments
  run through `run_tick`, Opus got the economics of a close right where Sonnet did not:
  - On 2026-09-21 12:40 Sonnet closed verticals already 2-9x past their wing, which locks in a loss
    that is already full. Opus closed only the one 0.12 points through its short.
  - On 2026-08-28 13:00 Opus turned the gate off ("chop not trend") and declined a pointless close.

  Opus costs $0.17 a call (11-13 s) against Sonnet's $0.11-0.125: about $36-110 a month
  event-driven. Two moments are not evidence, so **the replay runs both models** over the same
  sessions and scores each against `trend-rule`. The model is chosen on that result.
- **Configurable, and chosen per day on live.** The `intraday_agent` block is off by default. On live
  the agent's mode (none, shadow, gates, gates and closures) is a separate selection in
  `/live-flies-start`, recorded on that day's arm record. Only the modes that config allows and the
  qualification has unlocked are offered.
- **The written exception (user decision, 2026-10-05).** `gates_and_closures` lets a model's output
  place live orders, an exception to the suite's rule that AI stays out of live order paths. It is
  allowed only through the qualification above, only when selected on the day, and only within the
  live loop's own gates (arming, the buying-power cap, the halt flag).

## Open decisions

- **The 10-minute expiry, and the ±10-point band that triggers a call.** Both are first guesses,
  to be set from the replay.
- **Whether `trend-rule` also lands on the live arm** if it beats `control` in the replay, whatever
  the agent does.
