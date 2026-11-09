# The flies intraday agent

An optional model that reads the session and makes two decisions that cause or cap a stranded legged
vertical. It is off by default, and the module is retired, so it runs only after flies is reinstated and
`intraday_agent.enabled` is set. The suite prefers deterministic solutions (root CLAUDE.md); the model
lives outside the package, in `scripts/flies_intraday_agent.py`, and a failure costs one tick's advice,
never a fill. It is a research instrument: its stored packs exist so a plain rule can be fitted from what
it noticed.

## Why it exists

Flies' losses are almost entirely stranded verticals, not costs. A completion order rests at the entry;
on a trend day price never returns to it, and the vertical rides to settlement at full width. The fixed
20-point trend-from-open gate (`engine._classify_trend`) cuts strands but cannot tell a trend that will
last from a morning that will reverse. The question for the agent is whether it can read what price
alone does not show: the call wall moving, GEX draining, VIX1D rising while SPX is still up, breadth
fading.

## The two decisions, and nothing else

1. **`trend_gate`, `on` or `off`.** `on` blocks new legged entries on the trend's losing side, as the
   rule does. `off` means the trend has failed, so entries resume.
2. **`close_stranded`, a list of position ids** to close now rather than ride to settlement.

It does not choose strikes, size or prices, opens nothing, and never touches a completed fly.

## Shape

| Part | Where | Deterministic? |
|---|---|---|
| Fact pack: session spot path against open, high and low; GEX readings; VIX/VIX1D/VIX9D; breadth; open positions and their marks; today's entries | `cherrypick.flies.intraday_pack`, a pure function over the stream cache, the GEX recorder's store and the ledger | yes |
| The model call | `scripts/flies_intraday_agent.py`: the `claude` CLI with the advisor's tool fence (no tools, no MCP, stdin only) | no |
| Validation: the schema, ids that exist, an expiry, a reason of 40 words or fewer | `cherrypick.flies.intraday_advice` | yes |
| The decision artifact, expiring after `decision_ttl_minutes` | `state/flies-intraday-advice.json`, `core.advice` conventions | n/a |
| Applying it | the paper loop on the agent arm; the live loop per the day's mode | yes |

A missing, stale or invalid artifact means the arm falls back to the fixed rule for that tick. The agent
runs only when there is something to decide: while a stranded vertical is open, or while spot is within
`trigger_band_points` of the trend band. The pack carries no dates, and holds only what was recorded by
its instant; a test pins that (the replay is worth only its look-ahead test).

Every pack is stored with its decision, reason and, once known, its outcome (`data/flies/intraday_agent/
<session>.jsonl`), on paper, in the replay and in the live shadow alike. Each record carries
`pack_blocks` (`intraday_pack.completeness`), which names the fields a recording could not feed; results
from packs with different completeness are never pooled as if alike.

## The arms (one variable each)

- **`control`:** unchanged.
- **`trend-rule`:** control plus `refuse_completion_against_trend`, which refuses an entry whose
  completion needs the day to reverse a drift beyond `regime_trend_points` (20) from the open. It reads
  the drift fresh each tick, so it releases when the day falls back inside the band. Its close rule
  (`close_tags.rule_wants_close`) fires on a stranded vertical through its short but not yet past its
  wing, on a day committed against it.
- **`intraday-agent`:** the same entry rules, with the gate switched per tick by the agent's fresh
  decision (`paper_loop.tick_config`), and the closes it names. With no fresh decision it is
  `trend-rule`'s gate exactly. It is a plain arm, not an `advised:` book: the agent is the arm's
  variable.

**Closes are tagged, not executed** (`close_tags.py`). Flies has no intraday close, and settlement
treats every position as open until the print. A close is recorded on the row at the decision tick: its
natural debit, its mid and spot. The position settles as always. `run.py close-tags --arm <arm>`
(`analytics.close_tag_result`) values the arm as if every tagged close had been taken, at natural and at
twice the spread. `run.py close-tag-tracker` (`analytics.close_tag_tracker`) does it for every tagging
arm in both ledgers, classes each tag by how the position really ended (`stranded`, `completed`,
`expired_otm`) and checks each paper tag that completed against live's same vertical that day
(`live_twin`). A later completion is forgone, as a real close would forgo it.

**The agent is judged against `trend-rule`, not just `control`.** If it cannot beat a rule built from
the inputs it sees, the rule wins: it costs nothing and fails the same way twice. The bar is net per
position at the 2x haircut and no more strands, over at least 20 sessions that crossed the trend band; a
tie goes to the rule.

## Configuration (`intraday_agent` block, off by default)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | The master switch. Off: the agent never runs and the live start never offers it. |
| `model` | `"opus"` | The model alias, passed on argv as the advisor's is. Never hardcoded. |
| `paper` | `true` | Whether the paper arm `intraday-agent` acts. Paper needs no daily yes. |
| `live_mode_max` | `"shadow"` | The most the live start may offer: `off`, `shadow`, `gates` or `gates_and_closures`. A ceiling: the day's selection picks within it, and the qualification has to allow it too. |
| `trigger_band_points` | `10` | Run only within this distance of the trend band, or while a stranded vertical is open. |
| `decision_ttl_minutes` | `10` | How long one decision is valid before the arm falls back to the rule. |
| `max_calls_per_session` | `100` | A hard cap, so a stuck trigger cannot run up a bill. A vertical is open most of the day, so at a 4-minute gap a session asks about 95 times. |

A call costs roughly $0.11 to $0.17 depending on the model, mostly the CLI's own overhead, so a larger
pack barely moves the price.

## On live: chosen per day, never by config alone

`/live-flies-start` asks a second, separate question after the live YES, only when `enabled` is true:

| Option | What the agent may do on live |
|---|---|
| No agent today | nothing (always offered) |
| Shadow | records decisions against the live arm and acts on nothing |
| Gates | may block live entries on the trend's losing side; never places an order |
| Gates and closures | may also close stranded live verticals (not built, see below) |

A mode is offered only when both `live_mode_max` and the qualification file allow it, so the dialogue
never offers more than config, and config never reaches past the evidence. The selection is written to the
day's arm record (`state/flies-live-arm.json`) as `intraday_agent: {mode, model, confirmed_at}` and goes
with the record when it self-disarms at `live.disarm_time`, so tomorrow needs a fresh yes. A "No", no
record or a disarm leaves live exactly as it is without the agent; `--stop` clears the agent with
everything else.

- `python -m cherrypick.flies.live_loop --agent-mode <mode>` writes the selection. It refuses, and writes
  nothing, unless today's arm record exists and the mode is in `intraday_eval.offered_live_modes` (the
  qualification's `offered_modes`, only off and shadow while there is no file, narrowed by
  `live_mode_max`).
- `--status` carries an `intraday_agent` block: the config, `mode_today`, `offered_modes` and each
  criterion with its value.
- **There is no live closing-order path.** `intraday_advice.LIVE_CLOSES_BUILT` is false, so
  `gates_and_closures` is never offered and a record naming it runs as `gates`. The agent's named closes
  are tagged at live natural on the live rows, exactly as on paper, which scores them against real quotes.
  Building the order path is its own change, and flips that constant. Letting a model's output place a
  live order would be an exception to the suite's rule that AI stays out of live order paths; it would
  need the qualification, the day's selection, and the live loop's own gates in front of it (arming, the
  buying-power cap, the halt flag).
- The live loop reads the live decision once a tick (`intraday_advice.live_tick`). In `shadow` it changes
  nothing. From `gates` up, a fresh decision sets the live arm's trend gate; with none, the arm keeps its
  declared gate. Every live entry carries `agent_mode`, `agent_gate` and `agent_would_refuse`.
- The supervisor job `flies-intraday-agent-live` runs the agent on the live arm each minute of the
  session, and exits at once unless today's record names a mode. `flies-intraday-agent` runs the paper
  arm.

## Unlocking gates and closures: the qualification

The agent is not trained; it reads each pack fresh. What it needs before acting on live is evidence. The
qualification file (`data/flies/intraday_agent_qualification.json`) is written by a deterministic
evaluation over the replay, the paper arms and the live shadow, never by the agent
(`intraday_eval.py`; the paper loop rewrites it after each settlement, `run.py agent-eval --write` does
it on demand). The console's `/flies/agent` tab and the live selection read the same `offered_modes`, so
the page and the dialogue cannot disagree. A criterion the suite cannot score yet is `pass: null`, and
null never unlocks. The file records the numbers it judged on, so an unlock can be checked.

- **`gates`:** at least 20 sessions with the agent's decisions recorded (the replay counts once its
  look-ahead test passes); the agent arm beats `trend-rule` on net per session at one-sided 95% and
  strands no more often; at least 5 of those sessions from the live shadow, agreeing in sign with paper.
- **`gates_and_closures`:** everything above, plus at least 60 stranded-vertical episodes with a close
  decision; the closes' edge survives the 2x spread haircut; the shadow's would-be live closes, re-priced
  at natural against real live quotes (`fly_order_path`), are still positive.

How the shadow is scored (`intraday_eval.shadow_scoring`): per live session run in `shadow`, the gate's
live effect is minus the settled net of the entries it would have refused; the paper effect is the agent
arm less control on the same session. A session where neither moved is not counted. `live_shadow` passes
on at least 5 counted sessions with at least 80% agreeing in sign; `shadow_closes_live` passes on at
least 10 shadow closes tagged at live natural that still save money. Both thresholds are choices recorded
as constants beside the others.

## Paper fills against live fills

Paper and live fill differently, so the comparison is paired and inside one fill regime: all arms run on
paper on the same ticks and fill model, most fill bias cancels in the difference, and the claim tested is
"the agent's arm beats the rule's arm", not what it makes live. `trend_gate` involves no fill (it only
stops entries). `close_stranded` is a closing order in a running market, the most fill-sensitive thing
here, with no live history, so paper prices every close at natural (ask on the short, bid on the long) and
records the four leg quotes at the decision so it can be re-priced under any model. The result is reported
twice, at natural and at natural plus a 2x spread haircut; an edge that survives only the first is not one.
Each rung of live exposure (shadow, gates, closes) is a declared boundary with a `measurement_breaks` row.

## The historical replay

`intraday_replay.py` and `scripts/flies_intraday_replay.py` run the agent over past sessions, so evidence
accrues without spending live sessions. It scores the gate only, valued from the ledger's settled
outcomes: each `control` entry stamped its drift and the direction it needed, so the gate's refusal is
exact, and a decision counts only once made and within its expiry. It does not score closes, because
paper recorded no intraday quotes; the model's close decisions are kept with their packs and closes are
valued by the forward arms. Replayed sessions count toward the gate criteria marked `replay` and never
replace a forward session. `--targeted` asks once, the minute before each entry the rule refuses (the
only place the agent can differ); `--max-calls` paces a run and `--dry-run` counts calls. A replay is
scored on paper control's recorded outcomes, so it overstates what a refused entry would have earned live
(paper fills completions the live loop never makes): read it as a paper result, and let the live shadow
settle it. The quote history the GEX recorder keeps (`core.quotehistory`) lets a later replay price
closes for sessions recorded after it began.

## From agent to rule

The agent's reasons on its correct calls name candidate features (the call wall moving since the open,
net GEX's change, VIX1D against its open). Those become measurable columns over the stored packs; a plain
rule is fitted on the earlier sessions only, tested on the later ones beside `control`, `trend-rule` and
the agent, with the fit/test split declared before the fit. The simplest arm that is not measurably worse
wins: if the derived rule keeps most of the agent's edge it replaces the agent, because it costs nothing
and replays over any month.
