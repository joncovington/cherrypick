# cherrypick-automations

⚠️ **EXPERIMENTAL.** A rules engine for short-dated option arms, declared as research item 75 (eight SPX
and GLD arms from a third-party portfolio, read over all history in one pass). **Paper-only,
credential-free, no live path** (posture: root file). The package is the engine and its paper loop: a
position is a ledger row (`~/.cherrypick/data/automations/paper_trades.db`), never an order. Off by default.

**The old name resolves.** The package was `packages/ensemble`. `cherrypick.ensemble.*` still imports, as
the same module objects (`src/cherrypick/ensemble/__init__.py`), so the finished item 75 scripts and a
suite config's old argv run unchanged. `config/ensemble.json`, `data/ensemble/` and the `ENSEMBLE_CONFIG`
and `ENSEMBLE_DB_PATH` variables are read while only the old one exists (`core.home.RENAMED`); the
orchestrator reads `modules.ensemble` as `modules.automations` (`config.resolve_renamed`). The ledger's
tables and its `ensemble_trade` schema keep their names: they are stored data.

## What it is

An **arm** is configuration (`arms.py`): the underlying, the expiry rule, the legs and how each strike is
placed, the entry trigger, the weekdays, the event skips, the filter gates, the exits and the size. A new
arm is a new entry (or a machine override read by `arms.load_arms`); an unknown key is refused with its
path. A new kind of filter is one function in `gates.REGISTRY`, a new strike rule one branch in
`strikes.pick`.

**Automation groups.** An arm names its `group`, a set of like automations switched as one through the
config's `groups` block (`{"ensemble": {"enabled": false}}`). `config.load_arms` switches off every arm
of a group that is off, so none of them enters; what they hold is still managed and settled, because a
position carries its own exits. A group no arm belongs to is kept only switched off (one being set up),
and is refused while some arm names a group the block does not: that is the misspelling
(`"ensembel": off` beside arms in `ensemble`) which would leave those arms trading. The shipped
eight are the `ensemble` group, and each carries its research `register` label, item 75's verdict
(`passed`, `failed`, `no_verdict`), written from the register and checked against its forward list
(`tests/test_groups.py`), never read from a result file. The loop's `--status` shows each group with its
arms and verdicts. The four item 75 failed (`iron-fly-1000`, `iron-fly-1dte-1330`,
`gld-call-spread-20d`, `orb-put-spread`) are off in the example config.

**Enrolment** (`enrolment.py`). An automation taken to paper has a `start`, the first session the paper loop may enter it (a backtest ignores it). A group's `start` is the boundary its members join at, unless a member's own start is later. Each of its five blocks (structure, entry, gates, exit, size) is labelled for what a backtest judged: a register label's `blocks` records each block's hash as the study ran it, and a block is `backtested` while its hash matches, `changed` once it does not, and `not_backtested` without one. The shipped eight carry item 75's hashes. A `forward` rule (measure, field, min_obs, kill_z, drift_z, optionally per, reference, beside) is declared with the automation; the loop's `--status` lists it as a register-shaped row (`forward`), with the starts and a one-line label per automation (`backtested`), and the orchestrator's `forward-checks` reads those rows beside the register's, so the paper results become evidence without a separate declaration. An automation with a block not backtested also gets `backtest_suggestions` in the status (`enrolment.suggestion`): what its blocks read (the chain at its expiry class, `monthly` for a rule with `monthly` set, and the entry's cadence: `1d` for a daily trigger or a time entry whose window holds the end-of-day chains' 15:59 tick by the engine's own window test, else `1m`; the greeks for a leg placed by delta, the spot trail for an intraday trigger), whether our stores cover each over the last year (only stores the driver can replay, `enrolment.replays`: a same-day chain from a store in the recorder's schema, a later expiry from end-of-day chains, an entry inside the session on a later expiry from the full set's intraday chain stores, asked at `INTRADAY_CADENCE`, five minutes, or `why` none holds it) up to the last finished session, a store up to `sources.LAG` sessions behind covering through its last one, where the window then ends (`core.sources.coverage`: our own first, a licensed store only where they fall short and `licence.allows_new_work` says yes, by subprocess to `cherrypick.backtest status`, fail-closed; such a need and its suggestion are marked `licensed`), and where not, the nearest source's span and why it falls short, or `why` no store is a candidate; a need for every expiry that monthlies would cover carries `monthly` with that store. The suggestion also carries `windows` (`enrolment.windows`): for each store the driver can replay the chain from, the last year it holds and everything it holds, each ending where the store stops and carrying its density (`thin` below the registry's dense mark, a store under `OFFER_DENSITY` of its span left out) and the registry's words for what it holds; a licensed store only while the licence allows new work and only for a window that starts before every one of our own; a window inside one already offered is dropped. A Builder run names one by its `key`. The suggestion also says whether the underlying is one of the full set's own seven (`full_set`), so the Builder can offer to add another. The suite says a backtest can run; it never runs one.

**The recording request.** Enrolling an automation is what asks for its chain: from its start date, the stream request (`stream_request.py`) carries its symbol and, from `stream_lead_minutes` before its entry window until the window closes, the one expiry its rule names, which the streamer records into the cache like any other module's. No separate recorder is involved. The request is priced against the subscription budget before it is written (`streamrequests.fits`, with this module's own file replaced by the candidate). When it would put the producer over `configured_budget`, it drops its entry expiries and widened windows and keeps every expiry a position holds, so that day's entries refuse for want of quotes and the feed is never pushed past its ceiling. The status's `stream_budget` gives the total, the budget, and what was shed.

**The nightly replay** (`record.compare_day`, `cli replay-day`, job `automations-replay`). Each session's recorded decision ticks are run again through `paper.decide_all`, the call the backtest driver makes, on the snapshots paper kept. Any decision (entered, first failing check) or fill (expiry, count, legs, prices, slippage, fees) that differs is listed, and an arm decided on one side only counts as a difference. The command exits 1 on any difference.

**The Builder's form and "Today, live"** (`builderform.py`). `cli builder-schema` is every choice the form offers, read from the tables the engine validates against (the blocks, entry kinds and trigger forms, expiry kinds, strike rules with their match modes, gates with their parameters from each gate function's signature and the stage each reads, exits, limits, sizes, core's signal kinds, event labels), so nothing is spelled twice. It also carries `credit_rules`, the credit-trade exits in the engine's own keys, which the Builder offers as an exit starting set. `cli builder-today` (JSON on stdin: a name, or a name and an unsaved automation resolved as a save would) decides it once, now, as paper would: `feed.snapshot_for` over the stream cache and the paper ledger opened read-only, then `paper.decide_all` on a copy switched on. It returns every check, the entry and legs or the first failing check, and writes nothing: no decision, register row, replay record or order. `cli builder-payoff` (the same stdin) is the structure for the Builder's shape and chain example: `engine.structure` places the legs as `decide` does (`engine.place_legs`, one placement for both) without the timing, limits, event skips, gates or size, prices them at the mid, and `builderform.payoff` adds the P&L at expiry for one contract (`pricing.payoff_curve`, exact between strikes), the breakevens and the chain around the legs and the spot (`chain_slice`). It reads live quotes when `feed.snapshot_for` gives a snapshot now, else the last session our own recorder holds for a same-day expiry, at the first stored minute of the entry window (`recorded_structure`, `driver.snapshot_at`); a licensed store is never read (`test_builderpayoff.py`, shown to fail), and a later expiry is placed on live quotes only. Every answer also carries the generic shape (`sketch.py`, pure, from the rule alone): each leg at its distance from the spot in points, or at its delta or multiple where it is not placed in points (`exact` false: order true, spacing not), and the payoff at those positions with a nominal premium, labelled in words, never dollars. Chain rows and placed legs carry their `offset` from the listed strike nearest the spot. The answer names its source; it writes nothing.

**Legs.** A leg is written in the Builder's words: `action` (`BTO`, `STO`), `qty` (its contracts, 1 to `arms.MAX_LEG_QUANTITY`) and `side` (`put`, `call`); the shipped automations are written so, and the Builder's editor (`drafts.editable`) and every save (`drafts.draft`) convert to it. The older spelling, `side` as the signed contracts and `right` as `C`/`P`, is read for good (configs, backups, replay records, ledgers), and a leg mixing the two is refused (`arms.internal_leg`). `arms.load_arms` reads every leg into the older form, which is what the engine, the ledger, the replay record and the block hash see: so an automation hashes the same in either spelling and converting one moves no backtest label (`tests/test_leg_spelling.py`, shown to fail). `cli legs-convert` lists this machine's automations still stored in the older spelling, each with its legs written, and proves every automation loads the same after; the orchestrator writes them (`run.py automations-legs-convert --apply`). Below, `side` is the loaded form's: the leg's contracts per structure, signed (+ bought, − sold, never 0, at most `arms.MAX_LEG_QUANTITY` either way); pricing and the payoff already multiply by it, and fees and verdict slippage count contracts (`pricing.contracts`), so a 1-2-1 fly entered as three legs is the four-leg fly to the cent (`tests/test_leg_quantity.py`, each guard shown to fail). The settlement charge stays per leg finishing in the money, which is per symbol, as the broker charges it. Each leg's `name` is its own (`arms.validate` refuses a repeat; `ensemble_legs` keys a position's legs by it). The Builder names a leg it adds by an ID (L1, L2, …), as the strategies do; the shipped automations keep their names.

**Named expiries** (#1318). An automation's `expiry` is its near expiry. A leg with `"expiry": "far"` (either spelling; near is the default and never stored, so a one-expiry automation's legs and hash are as they were) sits on `far_expiry`: any `expiry` rule, resolved after the near one, or `{"kind": "after_near", "days": N, "monthly": bool}`. `engine.decide` resolves both (`resolve_far_expiry`; none listed is `no_far_expiry`) and places each leg on its own chain (`place_legs`; `same_as` and `from_leg` read an earlier leg's strike whatever its expiry). Every read of a leg's quote goes through `Chain.for_leg`, which gives the near chain or the leg's own expiry's (`held_chain`, `Chain.with_others`), and never another expiry's: a far leg with no quote is no reading (`tests/test_named_expiries.py`, shown to fail). The check (`arms._far_problems`) requires: at least one near leg; every far leg bought; and `exit.close_before_expiry_min`. A bought far leg is worth at least its intrinsic value, so the max loss read at the near expiry with far legs at intrinsic is a true bound, and the max profit is None (`pricing.payoff_extremes`, `value_bounds`). A far leg is never read past the band of strikes held. Held to the near expiry unclosed, a position settles there with each far leg at its intrinsic value and the settlement charge on near legs only. The ledger keeps a far leg's expiry (`ensemble_legs.expiration`, null on a near leg). The stream request asks for both expiries on entry (`feed.expiries_for`) and while held (`db.open_expirations`). A held position's quotes are grouped by expiry (`feed.by_expiry`). The replay record carries a far leg's expiry fifth. The structure block's hash includes `far_expiry` (absent on a one-expiry automation, so its hash is as it was). A backtest of a far leg also needs the far expiry's chain (`enrolment.needs`, `"far": true`: at the close from the same end-of-day source, inside the session from the full set's intraday chain stores); with an entry inside the session the run is named for that far chain's store (`enrolment.run_chain`) and reads the intraday chain history, the same-day store beside it, never the recorder-schema store alone, which holds no later expiry. The driver reads a held position off every expiry its legs sit on (`driver.held_expiries`). The Builder's payoff is drawn at the near expiry with each far leg's time value by a model, labelled so: the sketch values a far leg by the normal model at one drawing unit (`sketch.far_value`, `modeled`, top "peak, modeled"), and the placed example by Black-Scholes at the far leg's own vol at entry, the quote's or implied from its mid (`builderform.far_model`, `modeled`: the peak and each far leg's vol and days left; a leg whose vol cannot be read is held at intrinsic), drawn to two of the far leg's standard deviations either side (`FAR_SDS`) so the tent's breakevens show. The modeled peak is never a bound; the max loss stays the engine's (`tests/test_named_expiries.py`, each guard shown to fail).

**The strategy selector** (`strategies.py`, pure; `cli builder-strategy`, JSON on stdin: strategy, settings). A named structure and a few settings (short or long, put or call, DTE and which expiries, how the first strikes are placed and the target, the width, a broken wing's far wing, a ratio) in; its legs (L1, L2, …, written spelling) by the engine's own strike rules (`delta`, `spot` by points or percent, `std_dev`, `from_leg`, `same_as`) and an expiry rule (`at_least` the DTE, monthlies where asked; `dte` 0 for the same day) out, never a trading rule of its own. A butterfly is long when its wings are bought. `builderform.strategy_answer` adds a whole automation for the Builder (`STRATEGY_DEFAULTS`, and the credit-trade exits for one sold for a credit, the 21-day close only from `CLOSE_AT_DTE_FROM` days out). A calendar (sold near, bought far at the same strike, at the money by default) and a diagonal (the far leg `width` points further out) write `far_expiry` (`after_near` `far_days`, monthlies where asked), and the automation closes `CLOSE_BEFORE_NEAR_MIN` minutes before the near expiry. Covered is listed `available: false` with why (stock legs). `tests/test_strategies.py` loads every strategy at every enum setting through `config.load_arms`. The schema carries the list as `strategy`.

**The entry presets** (`entrypresets.py`, pure; `cli builder-entry`, JSON on stdin: entry, settings, filters `[{filter, settings}]`). One entry preset (a fixed time, near the close, an RSI(2) pullback above the 200-day average, down days in an uptrend, overbought in a downtrend, a moving-average cross, an opening-range breakout, a gap fill, a set number of sessions after an event, Custom) writes the engine's own `entry`; any number of filters (a VIX band, vol-index rank, implied over realized, a calm open, a quiet day, an uptrend) stack on it, each one or two ordinary gates, all of which must pass. The daily-signal presets read today's bar as it stands (`daily` and `developing`) from their time to 15:59. Each preset names the strategies it suits (`fits`: a strategy and any settings it must have, sold or on puts), which the Builder marks on its preset list; `suits` says it in words. A starting point, never a tested edge. `builderform.entry_answer` names a setting it cannot build from, by filter. `tests/test_entrypresets.py` loads every entry at every enum setting, and with every filter, through `config.load_arms`. The schema carries both lists as `entry_presets`.

**The Builder's words** (`words.py`, pure data). Each choice the schema offers (entry kinds, trigger forms, signals and their firing rules, the indicators a signal reads, gates, exits, strike rules and match modes, expiry rules, sizes and their progression, limits, strategies) has a label, a list group, a sentence on what it does and the line the form draws, with a `{key}` slot for each control (`{range}` a gate's min and max together, `{symbol}` the underlying in a strategy's line). Keys the line leaves out each get a phrase under "More options", and an enum's or a flag's value is worded where its own spelling would not read. A key the engine requires but the line puts under More options has a `start` value the form writes there, visible and editable (a signal's `seed`, true as the presets write it). The page lays its controls in these lines, so it words nothing itself. The words also give each filter stage its tag (`STAGES`), the exit groups the form gives a row each (`EXIT_ROWS`, the rest under More options), and the exits a trade can start from (`EXIT_STARTS`: the credit-trade rules, or held to expiry). The schema carries them as `words`, and the indicators with their keys as `sources`. The payoff answer says whether the rule enters for a credit or a debit (`entry_kind`, `core.structures.entry_kind`), so the form offers only the stop the trade can fire. `tests/test_words.py` holds them to the schema both ways: every choice offered is worded, every slot is a key that choice takes, every key has a place, every required key is in the sentence or has a start, and every flag and underscored option is worded. Each kind of gap is shown to fail there.

**Backtest references** (`references.py`, `cli reference-check`). The config's `references` block,
`{automation: run}`, names the Builder run (`builder-<automation>-<YYMMDDHHMM>`) the operator pinned
as an automation's backtest reference, which its page sets paper against. It sits apart from the
automations on purpose: nothing the paper loop reads comes from it, so a pin moves no block hash,
label or replay (`tests/test_references.py`). `check` refuses a pin to an automation that does not
exist or a run not named for it; the orchestrator confirms the run exists and writes the block. `cli
builder` lists each automation's pinned run as `reference`.

**Entries** (`entries.py`, pure; `cli entries [--name N --run-reasons JSON]`). The session register
cut into the steps of `engine.decide` in its order: `data` (an input not there: the feed, the loop's
record, a store a backtest reads), `trigger`, `limits` (the limits, the expiry and the event skips),
`gates` (market gates), `structure` (strikes, pricing, and gates the engine runs after the legs),
`size`; a reason no step names counts under `due`. Each step's `reached` is the due sessions that
got to it and `stopped` those whose last try ended there, by cause; `most_lost` the step that lost
the most (the earlier on a tie). A session counts where its last tick stopped, not the furthest any
tick got. A Builder run's `summary.reasons` are the driver's last reasons in the same words, so
`--run-reasons` cuts the run by the same steps and `causes` sets the two side by side. Read-only;
no ledger reads as no sessions.

**The sessions ahead** (`ahead.py`, pure; `cli ahead --name N [--start]`). The automation's dated rules for `core.ahead`: its weekdays, its `skip_events` labels and its exits' `avoid_events` (on the session before), answered by its own event calendar, so a label with no dates there reads unknown as it does at entry; its filter gates in the Builder's words (`words.GATES`). Only an avoided event's eve keeps a record: a skipped day holds no trade. The record is the automation's per-session nets from `core.compare` over the era. Reads only.

**Retired** (`arms`: `retired`, `drafts.retire`, `cli retire-check`). An automation's `retired` is false
or the date it was retired. A retired automation is off whatever `enabled` says (`arms.load_arms`), its
state is `retired` (`groupedit`), the Groups check refuses to switch it on, and a Builder save keeps
`retired` as stored. `drafts.retire` gives the keys to write: retiring sets the date and `enabled`
false, reinstating sets false and leaves `enabled` false, so it trades only once switched on.
What it holds is still managed to its exits.

**A group's budget** (`config.groups`, `budgets.py`). A budget is `allocation`, `max_risk` and
`shares`. Once a group has run (switched on with no start, or its start come), the Groups check
(`groupedit.check`) never rewrites the budget it ran on: a different one is a dated change in
`budget_changes` (`[{"from": session, **budget}]`, the budget whole, dates rising) from the next
session, replacing one already waiting; the same budget as today's drops a waiting one.
An automation moved out of a group takes its share with it: `check` drops it from that group's
budget, today's and every dated change alike, since the loop refuses a share of an outsider.
`config.groups(config, today)` gives each group the budget it carries that day, so the loop sizes and
caps on the right one. `budgets.suggested` is a group's suggested allocation: the most its members not
retired can have at risk at once (a `max_risk` size times `max_open`, or the structure's widest
outcome before premium times contracts times `max_open`, counted only when it does not depend on
where a delta or percent leg lands: `sketch.legs_at` at two drawing units must agree), None with why
when one member has no bound. The groups view carries it, each member's `shape` and each group's
`budget` (today, a change waiting, whether a change is dated).

**Groups as a unit** (`groupread.py`, pure; `cli groups-read`). Each group's members' closed trades
stacked (`analytics`' line with its running net), the net and drawdown as shares of the
allocation each trade's session carried (`config.budget_at`), added, never compounded, the most `capital` open at once against its `max_risk` and each member's against its
share (a position counts from its entry session through its exit session, so the figure is an upper
bound at a session's grain), the sessions missed with `group_risk` by which cap bound (the one with
less room in the check's own reading), and `core.portfolio` over the members' net per session. The
automations in no group are one line beside them. Membership is the config's now. Read-only.

**The Builder's check** (`drafts.py`, `cli draft`, JSON on stdin). By name alone it returns the automation as an editor starts from it (the shipped entry with this machine's changes laid over it, before groups stamp anything). With an `automation`, it lays the edit over the config and resolves every automation again (`config.load_arms`), so a save is refused for exactly what would stop the loop, and returns the entry to write: off (`enabled: false`), named by its key, and with its `register` label kept as stored, never taken from the editor (a copy under a new name carries none, so its blocks read `not_backtested`). It writes nothing; the orchestrator's config editor writes the entry (`automationedit`).

**Sizing** (`sizing.py`). A size is a count, a risk budget (`max_risk`), or a percent of an allocation
(`allocation_pct`, capped by `max_dollars` and `max_contracts`), and any of them can carry a
`progression`: the count steps by a set number of contracts, or doubles, after each loss (or each win)
in the arm's current run of closed trades, between a stated floor and ceiling. A trade's outcome is its
`net_pnl` at the verdict fill. A group can set an `allocation` (what an `allocation_pct` size reads when
it states none), a `max_risk` on its members' open risk at once, and `shares`, each member's percent of
both. `config.apply_groups` puts each member's slice on it as `budget`; an arm never writes one. Risk is
the maximum loss at the mid, whole-position dollars, the paper ledger's `capital`. A group cap cuts the
count to fit and refuses the entry (`group_risk`) when not one fits; a size's `minimum` never lifts it
over the cap. The driver's book and the paper ledger give the engine the same two readings, each open
position's risk and each arm's closed results in close order.

**Safeguards are stated, never defaulted.** Every arm's `limits` names `max_open` and `per_day`; the
engine reads them with no fallback, so an arm cannot run under a cap it did not state. `per_day` is 1:
the entry register keeps one row per arm and day. A replay record from before `per_day` existed runs
under what its arms ran under, one a day (`record.as_they_ran`).

**Gates run through `core.entrychecks`.** Each gate is a core condition (`gates.CONDITIONS`, core's
registry extended, its parameters read from the gate function's signature), so `arms.validate` refuses a
misspelt gate parameter at load and a gate's result is recorded as every module's are. The engine still
runs the gates in the arm's own order, market gates before the legs and structure gates after, so a
decision's first failure and its checks are what they were (item 75 reproduces identical). `CORE_STAGE`
labels each gate with core's stage by what it reads, for the builder and `core.trace`.

**A `trigger` entry** (`{"kind": "trigger", "trigger": ..., "bar_minutes": N}`) is `core.entrychecks`'
trigger: a `core.signals` firing on the trail cut into N-minute bars, or a fixed time. The shipped arms
keep their `time` and `orb` entries: the opening-range entry fires on the minute its spot crosses, a core
signal at a bar's close a minute later, so moving them would be a measurement change. An **event signal**
(on, before or after an event date, at a time) reads the event calendar's dates (`engine.events_for`):
a label whose stretch around the session is not wholly known is left out, so core refuses the signal
and the entry reads `event_dates_unknown`, never quiet.

**A daily signal** (a trigger with `"daily": true`, entry or exit, no `bar_minutes`) reads completed
daily bars only: the snapshot carries the symbol's daily bars before the session (`Snapshot.daily`, only
when an arm reads them, `arms.reads_daily`; the stream request then asks for `DAILY_HISTORY` sessions),
and core acts today, from the window's open, on a firing on the previous trading day's close. With `"developing": true` (and `within` null) it reads today's bar as it stands instead (`entrychecks.developing_bar`: the session's open, the trail's high and low so far, the spot now), and is due at each minute in the window when the signal fires on it. Bars
that stop before that day read `stale_bars`. The item 75 backtest driver carries no daily bars, so a
daily signal is backtested only once the automation driver exists (#1201).

**Library signals.** A trigger's signal (or an `only_when` entry) can be a reference to the signal
library, `{"library": name, "version": hash}` (`core.signallib`). `config.load_arms` swaps it for
that version's spec before validating, reading the library file only when an arm holds a reference;
one it cannot answer stops the load. The version is always named, so moving a paper automation to a
new version is a config edit, a measurement change made at a boundary.

**Volume.** A signal that reads bar volume (a volume-weighted VWAP, relative volume, a volume ratio;
`core.signals.needs_volume`) names the symbol whose volume weighs the bars: `volume` on the trigger
entry or the signal exit (`"volume": "SPY"` for SPX, which has none of its own). Without it the arm is
refused at load, as is a `volume` on a signal that reads none, a daily one, or relative volume over
more than `PRIOR_SESSIONS` sessions. Paper records that symbol's spot and the feed's day volume each
minute (`ensemble_trail.day_volume`, and the stream request asks for it); a minute's volume is the
counter's rise to the next recorded minute (`db.minute_volumes`), absent where a minute was not
sampled or the counter fell. The driver takes the same minutes from the recorded one-minute bars.
`core.signals.with_volume` puts them on the bars for both; a bar missing any minute has no volume, and
the trigger reads `no_volume` rather than a zero. A VWAP with equal weights reads no volume.

**One firing rule.** An intraday signal reads the earlier sessions too, as a signal study over the whole
history does: a seeded indicator is warm at the open, arming carries across the close, and a gap or a
prior-session break has the prior session. So for an arm that reads one (`arms.reads_prior_trail`) the
snapshot carries up to `PRIOR_SESSIONS` (5) earlier sessions' minute trail (`Snapshot.prior`; paper
reads its recorded trail, `feed.prior_trails`, and a day the loop did not run is absent), and the engine
builds bars over them and today. `tests/test_one_firing_rule.py` holds that a study, core's trigger and
the engine ticking minute by minute fire on the same minute.

**A `signal` exit** (`exit.signal`: `{"trigger": ..., "bar_minutes": N}`, the entry trigger's shape)
closes a position when its signal fires on the underlying, inside its window. `manage` checks it after
the touch and the profit target and before the time exit. On the entry day only a firing after the
entry minute counts, and core takes the day's first firing in the window, so a signal that first fired
before the entry does not exit that day. The backtest driver and the paper loop both run it through
`manage`, so a signal exit is backtested exactly as it trades. Paper manages a position on a snapshot
that carries what its exit reads (`feed.position_snapshot`): today's recorded trail, the earlier
sessions or the daily bars, the event calendar, and each leg's delta for a delta limit.

**The other exits**, checked in this order with the touch first: `avoid_events` (a forced close
`before_close_min`, default 60, before the close on the session before a listed event; a position
carried into the event day closes at once, one entered on it is not closed for it, and a day the
calendar cannot answer holds), the stops (`stop_credit_multiple`, a credit entry's loss at m times its
credit, never on a debit; `stop_debit_pct`, a debit entry's loss at p% of its debit, never on a credit;
`stop_dollars`), `trailing_stop` (`{"dollars": d, "activate_dollars": a}`: once the open P&L has reached
a, a fall of d from its best; or `{"pct": p, "activate_pct": a}`, the same with each a percent of the
trade's max profit, which never fires on a structure without one), the profit targets (`profit_target_pct` of max profit, `profit_dollars`),
`delta_limit` (the structure's delta per unit either way; a leg without a delta is no reading, never
zero), the signal, `close_at_dte` (calendar days to expiry, from the first tick of that session, never
on the entry day), then minutes before expiry. Dollar rules read the open P&L at the mid, whole-position
dollars. `max_spread: x` holds every exit while the structure's bid/ask is wider than $x a share, and a
mark read through it does not move the trailing stop's best; a close held to expiry settles. The best
open P&L is the position's (`Position.peak_pnl`): the driver keeps the object, paper keeps
`ensemble_positions.peak_pnl`. On end-of-day chains every exit is read once a session, at 15:59.

`engine.decide(arm, snapshot)` is the one decision: pure and deterministic, it returns the entry (legs,
quantity, prices at mid, natural and working, the slippage for each fill kind, the opening fee) or the
first failing check, with every check it read. `manage` says whether an open position should close now,
`close_position` and `settle_position` return the trade. The backtest driver and the paper loop differ
only in how they **build** the `Snapshot` (`snapshot.build`, store rows against stream-cache rows of the same
shape), so `tests/test_paths_agree.py` holds that a recorded snapshot replays to the same decision.

**The automation driver** (`driver.py`, over `history.StoreHistory`) is the backtest path for any arm:
every stored minute it manages open positions and then decides the arms in their entry window through
`paper.decide_all`, each on a snapshot `snapshot.build` makes from the stored day as `feed.snapshot_for`
makes one from the stream cache, and settles what is open at its expiry's close. A snapshot carries what a decision reads of the book, not its history: each arm's entries today and its latest before (`recent_entries`), and closed results only for an arm whose size has a progression (`results_for`); copying the whole book every minute made a long run quadratic. `StoreHistory` reads
any file in the gex recorder's schema (our recorder, the vendor's 0DTE stores) through
`core.quotehistory` and `core.regime`, never a reader of its own. `tests/test_driver.py` holds a day run
through the paper loop and through the driver to the same trade, to the cent. Choosing the store and
writing the result is the backtest package's `scripts/automation_backtest.py`. Item 75's own driver
stays as its study left it.

**Three kinds of history.** `StoreHistory` is sampled through the session (same-day expiries).
`DailyChainHistory` is end-of-day chains (Dolt, the vendor's store): one tick a session at 15:59, the
last minute paper acts in, positions held across sessions and settled at their expiry's close (on the
next session read, if the run skips the expiry day). An arm whose entry window does not hold that tick
counts `needs_intraday` for the session, never moved to the close; a session the source holds nothing
for counts `no_data`. A close that does not price on the tick waits for the next session's, and at
expiry the position settles instead: an end-of-day source has no later minute.
`IntradayChainHistory` is later expiries through the session, from the full set's intraday chain
stores (`<SYM>-near-1m.db` for 1-7 days out, `<SYM>-mid-5m.db` for 8-90), with the same-day store for
the day's own expiry and spot. A later expiry's quotes at a minute are its latest bar at or before
it, never older than its store's interval (a five-minute bar up to four minutes old). Where the
same-day store does not hold the session, the ticks and the spot are the nearest held expiry's
bars and its parity forward (`core.chaindelta.forward_from_parity`, a forward one to seven days out,
a declared limit), and closes and settlement come from the daily bars passed in.

## The rules and their resolutions

The arms are item 75's in our own words; the declaration is the authority. Where the source's text left a
reading open, the engine fixes one and says so here, because each is a measurement choice:

- **Standard-deviation move** is the move from the previous close over one day's sigma, taken from the
  previous close's VIX (`indicators.daily_sigma`).
- **GEX ratio** is `(call + put) / min(call, |put|)` over the window of strikes (`gates` docstring); the
  source does not define it.
- **Intraday RSI(14) and Stoch(14,3,3)** read completed five-minute bars of the spot trail. The slow
  stochastic's smoothed %K is the line compared with a level.
- **Gap** is the open against the previous close; **change** is spot against the previous close; **open
  change** is spot against today's open (the first spot at or after 09:30 on a replay, as paper takes it).
- **IV gates** read the underlying's Cboe vol index (`core.volindex`: VIX for SPX, GVZ for GLD), its close
  before the session, as the signal study's expected move does: `vol_index_rank` and
  `vol_index_percentile` against its `lookback` closes before that (252 by default), `vol_index_minus_rv`
  against the underlying's realized vol over `n` returns. An underlying with no index, or a history
  shorter than the lookback, fails the gate. Core's `iv_rank` conditions rank a chain's live ATM IV
  against a history of the same reading, which no module keeps yet.
- **Reward/risk** is maximum profit over maximum loss at the structure's mid; **bid/ask** is the whole
  structure's width (the sum of the legs' spreads).
- **"Up to $X"** sizes to the largest whole count whose maximum loss at the mid fits, never fewer than one
  (`size.minimum`); a fixed count is taken as stated. "X% of the allocation" is the same with the budget
  at X% of it.
- **A strike "$0.01 above the previous close"** is the listed strike nearest that price. "Or lower" is the
  highest strike at or below. A target that is not listed is no trade, except where the rule says nearest.
- **Match modes.** Every strike rule that aims at a price or an option value says how it meets the listed
  strikes (`match`: exact, nearest, above, below; `strikes` docstring). The rules that predate it keep
  their defaults; a newer rule must state one. The GEX picks and `same_as` place the strike itself.
- **Expected move and standard deviation** come from the at-the-money straddle's mid on the arm's expiry,
  one standard deviation being that straddle over sqrt(2/pi). They need quotes only, no model greeks,
  so they read alike on every source.
- **The entry's level** is the spot at the minute the entry fired (the breakout minute, the trigger's
  firing, a time entry's tick), from the trail; a daily trigger has no such minute and is refused.
- **"Exactly N market days"** is the next N trading days; **~20 days** is the listed expiry within three
  calendar days of the target, the nearest first. **At least N** is the nearest listed expiry N or more
  calendar days out; **between N and M** the nearest in that window (or the farthest, `prefer: last`).
- **Opening-range entry** evaluates once, at the first minute after the range whose spot is above its high
  (`single_shot`), as the bot's tree tags the breakout and does not look again.
- **Touch exits** read the spot against the entry spot: a negative `pct` or `points` is a fall.
- **Event labels** with a calendar rule are computed (`events.py`); the rest are data. A date a label has
  no data for passes its gate and is recorded `unknown`, never read as quiet.
- **Sold legs need a bid.** A structure whose sold leg has none is `unpriced` and skipped, as everywhere
  in the suite (`core.legbars`).
- **A mid the structure cannot be worth is no price.** A structure's value lies within its payoff at
  expiry (`pricing.value_bounds`: 0 to the width on a $10 fly, within half a cent). An entry priced
  outside it reads `unrealistic_price`; an open position's mark outside it is no reading, so no exit
  fires on it and the trailing stop's best holds; a close outside it is held and tried again on the
  next tick, settling at expiry if none comes.
- **A far wing beyond the recorded band is a range, not a gap.** A store holds a band of strikes
  around the spot, so on a day the market runs an open position's far wing can leave it
  (`engine._beyond_band`: a leg with no quote further out than the farthest strike held on its side).
  Such a leg is worth from 0 to that farthest strike's ask. `manage` reads the position at both ends
  and an exit is due only when it is due on both (the trailing stop's best moves on the lower); the
  close fills at the costly end, a sold far leg bought back at the cap and a bought one sold for 0.
  A leg missing inside the band is still no reading.

## Money

`pricing.py`, following `docs/conventions/money-layout.md`: gross is mid to mid (or mid to the settlement
intrinsic), fees (`core.fees`), settlement and slippage are separate columns, and net is reported at three
fills: **verdict** (the suite's slippage model, `fees.slippage_dollars`), **stress** (halfway from mid to the
natural fill) and **mid** (context). Per-share prices are signed so a credit is `+`.

## Dependencies

`cherrypick-core` only, its indicators (`core.indicators`) and entry checks (`core.entrychecks`) included. No
broker, no network, no streamer: the loop reads the stream cache and never writes it.

## The paper loop

`python -m cherrypick.automations.paper_loop --once` (the orchestrator ticks it every 60 s while
`modules.automations.enabled`). It takes any arm `arms.load_arms` returns: nothing in the loop is written for
an arm, only for what an arm reads (`feed.reads`).

- **Config** (`config.example.json`, copied to `~/.cherrypick/config/automations.json`): `automations` (its old
  name `arms` resolves) laid over the shipped arms (a new name is a new arm), `groups` (each group's switch), `symbols` (the OCC root and the stream window per underlying),
  `defaults` (quote and spot age, mark interval, stream lead, trail grace), `events.path`. A misspelt arm
  key stops the loop with its path. A resident loop reads the file at start; the orchestrator's `--once`
  reads it each tick.
- **Snapshot** (`feed.py`, `provider.py`): quote rows, spot, daily bars and the posted close come from the
  stream cache only (read-only), in the shape the backtest's store rows have, and go through
  `snapshot.build`. A quote older than `max_quote_age_seconds`, a crossed one or a chain the cache does not
  hold is a refusal named on the arm, never a guess. Greeks and open interest are read only for an arm that
  places a strike by delta or reads GEX; the VIX only for an arm that reads it.
- **The spot trail.** The cache keeps only the last print, so the loop records one sample a minute per
  symbol (`ensemble_trail`) and builds the opening range and the five-minute bars from that. A trail that
  began more than `trail_grace_minutes` after the open (a loop started at noon) refuses every arm that reads
  it for the day (`trail_incomplete`) rather than feed it a partial morning. The opening-range entry is
  single-shot on the first minute above the range, so a tick that misses that minute does not trade it.
- **Decisions.** `paper.decide_all` over the arms of the day whose entry is due, stacked in arm-name order
  so a later arm sees an earlier arm's entry (`no_position_today`), as the backtest stacks them. Each
  decision is a journal row (`ensemble_decisions`, the engine's first failing check and the value it read)
  and each arm-day a register row (`ensemble_entries`: entered, pending, or missed with the last reason).
  **Nothing fills after the entry window.**
- **The replay record.** Every decision tick writes one line to
  `~/.cherrypick/data/automations/replay/<session>.jsonl`: the compact snapshot, the arms as they stood and the
  decisions. `python run.py replay <file>` runs them again through `paper.replay` and lists where the
  decisions differ, which is how paper is compared with the backtest's path. Event labels are read from the
  current event file on replay.
- **Fills.** Entries and closes at the mid with the suite's slippage model (`core.fees`) as its own cost,
  fees from `core.fees`, per `docs/conventions/money-layout.md`. `net_pnl` is the verdict fill; `net_stress`
  and `net_mid` are kept beside it.
- **Exits** are the arm's, read from the rules the position was entered under (`exit_json`): every exit
  above is checked every tick and closes at the legs' quotes (a close whose
  legs do not price is retried); otherwise the position is held to the expiry's **posted close**
  (`stream_summary`, usually the next morning), never a last trade, and settled at intrinsic. A GLD position
  held to settlement is settled at intrinsic with the engine's cash-settlement charge, as the backtest does;
  a physically delivered ETF leg is not modelled.
- **Stream request** (`stream_request.py`): the arms' symbols with `history_days` for the longest moving
  average, VIX when an arm reads it, `expirations` and a `window_hints` strike window for an arm only from
  `stream_lead_minutes` before its entry window to two minutes after it, and every open position's legs
  through a ledger `leg_source`.
- **Events** (`events.load_calendar`): the file `packages/backtest/scripts/event_history_item75.py` writes.
  Without coverage for a date a data label passes its gate and is recorded `unknown`.

`ensemble_positions` holds the whole money layout on the row: `entry_value` + `exit_value` = `gross_pnl`;
`gross_pnl` - `fees` (open plus close) - `settlement` - `slippage` = `net_pnl`. `exit_reason` is `touch`,
`event`, `stop`, `trailing_stop`, `profit_target`, `delta`, `signal`, `dte`, `time` or `settlement`.

Entries are measured against item 75's replay, as weeklyput's are against item 57's. Measurement-affecting
changes (a window, a gate, an arm's rule, the fill) go to a declared boundary with a `measurement_breaks`
row.

## Tests

`python -m pytest` from this directory. `tests/market.py` invents Black-Scholes quotes and
`tests/cachebuilder.py` writes them into a stream cache built through the real DDL; each gate is shown
firing and failing; a new gate or loop guard is shown to fail by breaking its invariant on purpose
(working-rules).
