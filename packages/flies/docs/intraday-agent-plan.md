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
- **`trend-rule`:** the fixed rule with a reversal clause. The gate goes off once spot falls back
  below open + 10 after being past +20, or once net GEX turns negative while spot is still above the
  open. Stranded verticals close when the gate goes off.
- **`advised:intraday-agent`:** the same entry rules as `trend-rule`, but the agent decides both
  calls.

**The agent is judged against `trend-rule`, not just against `control`.** If it cannot beat a rule
built from the inputs it sees, the rule wins: it costs nothing and fails the same way twice.

## Configuration, and the daily yes on live

**Config** (`~/.cherrypick/config/flies.json`, an `intraday_agent` block, off by default):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | The master switch. Off: the agent never runs, and the live start never offers it. |
| `model` | `"sonnet"` | The model alias, travelling on argv as the advisor's does. Never hardcoded. |
| `paper` | `true` | Whether the paper arm `advised:intraday-agent` acts. Paper needs no daily yes. |
| `live_mode` | `"shadow"` | What the agent may do on live when chosen: `off` or `shadow`. `gate` is reserved and refused until a written decision adds it. |
| `trigger_band_points` | `10` | Run only within this distance of the trend band, or while a stranded vertical is open. |
| `decision_ttl_minutes` | `10` | How long one decision is valid before the arm falls back to the rule. |
| `max_calls_per_session` | `40` | A hard cap per session, so a stuck trigger cannot run up a bill. |

**On live the agent is chosen per day, never by config alone.** `/live-flies-start` gains one step.
After the live YES, and only when `enabled` is true and `live_mode` is not `off`, it asks a second,
separate question with the AskUserQuestion tool, exactly two options:

- **"YES: run the intraday agent in shadow against today's live arm (Sonnet, about $1-2 today)"**
- **"No, not today"**

The answer is written onto the same per-day arm record (`state/flies-live-arm.json`):
`intraday_agent: {mode, model, confirmed_at}`. The record self-disarms at `live.disarm_time` like
everything on it, so tomorrow needs a fresh yes.

**Where the yes is checked:**
- The agent script reads the arm record and runs for the live arm only when the yes is on it.
- The live loop reads a live decision only in the mode the record names.
- A "No", no record, or a disarm leaves live exactly as it is without the agent.
- `--stop` clears the agent with everything else.

The `--status` readout before the question shows the config block and today's agent state, so the
yes is informed, the same rule as the live YES.

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

**The replay scores both decisions:**
- For the gate: which entries would not have been taken, valued from the ledger's settled outcome.
- For the closes: the close cost at the recorded marks, priced at natural.

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

**Running cost:**
- At 10–30 calls a session (21 sessions a month), Sonnet costs about **$15–45 a month**.
- A 5-minute cadence would be about $113.
- The replay over 30 sessions is about 900 calls, about **$60 once**.
- These are API-equivalent costs. On a subscription they draw on usage limits instead.

For scale: live strands about 8 verticals a month at about $250 each.

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

## Order of work

Days, not months, and nothing waits on anything it does not need:

1. **The fact pack and its look-ahead test.** Deterministic, and the one prerequisite of everything
   below.
2. **In parallel, from the first session after it lands:**
   - the agent script, the validation and the artifact;
   - the paper arms `trend-rule` and `advised:intraday-agent`, acting;
   - the live shadow, recording.
3. **Also in parallel:** the historical replay of `trend-rule` and the agent over the ~30 recorded
   sessions (about $60).
4. **When the replay and the first forward sessions agree:** the live gate (if not already on from
   day one), and later the closes.

## Decided (2026-10-05)

- **Day one on live: shadow only.** The agent records decisions against the live arm and acts on
  nothing live. The paper `advised:intraday-agent` arm acts. The live gate waits for the replay and
  the first forward sessions to agree.
- **Model: Sonnet.** The alias lives in config (`intraday_agent.model`), never in code, as the
  advisor's does.
- **Configurable, and chosen per day on live.** The `intraday_agent` block is off by default, and on
  live the agent runs only after a separate yes in `/live-flies-start`, recorded on that day's arm
  record (above).

## Open decisions

- **The 10-minute expiry, and the ±10-point band that triggers a call.** Both are first guesses,
  to be set from the replay.
- **Whether `trend-rule` also lands on the live arm** if it beats `control` in the replay, whatever
  the agent does.
