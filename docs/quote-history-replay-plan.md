# Replaying rules against recorded quotes — flies and MEIC

*Drafted 2026-10-12. **The recording landed the same day** (gex PRs #172–#174): the gex recorder keeps
SPX's 0DTE chain every tick (~15 s), and `cherrypick.core.quotehistory` reads it back. **First
recorded session: 2026-10-13.** Nothing below that line is built yet. Amended the same evening: the
storage made compact and held strikes added before the first session, and six gaps in this plan
named (*Before Phase 1*).*

## Why

Until now no rule a module never ran could be priced honestly after the fact. The stream cache keeps
only the latest quote per leg; the ledgers record a market only for positions a module actually held.
Three questions on 2026-10-12 hit that wall:

- **Flies' live credit floor.** The estimate had to borrow paper's fills for the trades live never
  placed, and its gain rested on two trend days.
- **Restack on live.** The replay of one live session could only *estimate* the restack's credit,
  and could only infer from the spot path whether it would have completed.
- **MEIC's profit side and stop fills.** Its max favourable excursion is `None`, because "the cache
  keeps no quote history", and a derived stop is priced at its threshold rather than where a real
  stop would have filled. (This bullet first said the stop curve was censored. It is not: see
  *Corrections* below.)

## The data

| | |
|---|---|
| Tables | `gex_quote_samples` (one row per symbol and tick) + `gex_quotes` (one per side and strike of a sample) in `~/.cherrypick/data/gex/gex_history.db`; read only through `core.quotehistory` |
| Written by | `service.record_quotes`, every gex recorder tick in RTH |
| Content | 20 strikes each side of spot (~±100 SPX points), both rights: bid, ask, mid, quote age, delta; plus every strike of the chain a module declares it holds (`held_strikes`) |
| Quotes kept | only those `core.streamcache.usable_quote` accepts at 120 s; a missing row is a missing quote |
| Spot | not stored again; `core.quotehistory.session` joins `gex_spot_history` at or before each sample, within 60 s |
| Switch | the gex config's `quote_history` block, **off by default**; on for SPX on the trading machine |
| Size | ~80 quotes a tick, ~8 MB a session (7.9 MB measured on a synthetic 1,560-tick session; the first layout was 15.3 MB), ~2 GB a year |

What it covers: every flies structure (ATM centres; 15-delta debit-first and bwb wings fit inside
±100), and every MEIC SPX condor since 09-01 (13,462 trades: all legs within 85 points of spot at
entry; the threatened side stays inside the window as spot moves). A leg that drifts past ±100 on a
big trend day is still recorded while a module declares it held (flies' and MEIC's open legs are,
through their `leg_sources`). What it can miss: a strike past the window nobody holds, and touches
shorter than one tick.

**What it does not cover.** SPX's nearest expiration only. bwb (SPX ~7 DTE), calendars and pmcc
(multi-week), and MEIC on XSP, NDX or RUT get **nothing**: the recorder's `quote_history.symbols` is
SPX, and even on SPX it keeps only the chain the GEX profile reads (the nearest expiration with live
greeks). A replay of any of those needs the recorder extended first, which is a size decision (each
symbol is another ~8 MB a session), not a code one.

## Phase 0 — check the recording (2026-10-13)

About four samples a minute, gaps around 15 s, ~80 rows each, spot joined on most samples, no
recorder warnings. A one-off check is scheduled for 10:07 ET.

## Before Phase 1 — gaps found in review (2026-10-12)

A review the evening before the first session found the plan below under-specified in six places.
The first is already fixed. Each of the others is a precondition of Phase 1, not a refinement of it.

**1. Flies stamped the wall clock — fixed (#178).** `book.process_snapshot` wrote `_now()` beside a
decision made on the snapshot's `now_min`, so a replayed entry carried the hour the replay ran, its
latency measured the replay's runtime, and the cadence clock paced a past session against today.
`flies.clock.pinned(when)` now sets the instant a replayed tick stands for, and `process_snapshot` and
`settle_book` take an optional `now`. Phase 1's first validation check is still that a replayed
entry's time is its sample's.

**2. What the snapshot needs that the recording lacks, and the rule for each.** Flies' paper
snapshot (`provider.build_snapshot`) carries more than quotes. Each field is either rebuilt from a
recorded table by a declared rule, or the replay refuses the tick; it never approximates silently.
- *Per-strike IV, gamma and OI* are not in the quote history. `gex_profile_history` has them per
  side, every ~5 minutes. Rule: the profile row at or before the sample, used only within 360 s (one
  5-minute cadence plus a minute's slack); older is a refusal (`profile_stale`), as flies' own
  `max_gex_input_age_seconds` refuses a stale live surface.
- *The GEX surface and walls* are rebuilt from that same profile row through `core.gex`, the one
  engine, under the same staleness rule. They therefore move in 5-minute steps where paper's moved
  every tick: a declared difference, measured during validation (entries the `gex` gates admitted
  or refused differently), never assumed small. `recorded_gex` (the `wall-clear` arm's input) is
  already a `core.regime.gex_at` read and replays exactly.
- *The ATM straddle* (`vol-floor`, `low_vol_refusal`) is two quote mids: recorded.
- *The selector* is pure over the snapshot plus a model frozen for the session. A past session
  replays only if that session's model file is kept; check that `selector-fit` keeps dated copies,
  and if it does not, add them before the selector is replayed.
- *The intraday pack* (`intraday_pack.build_pack`) already reads the gex history at or before
  `as_of` and replays exactly. The agent's reply does not (a model), so the `intraday-agent` arm is
  never re-run; its recorded decisions are re-priced.
- *Quote age and cadence.* The recorder kept quotes no older than 120 s at its own tick and stores
  each one's age; a replay applies paper's `max_quote_age_seconds` to that recorded age. The
  recorder's tick and paper's are separate ~15 s clocks, so a replayed tick can sit up to ~15 s from
  the paper tick it is compared with; "within one tick" below means exactly that.

**3. A statistics plan, declared before any number is read.**
- *Validation tolerance* (proposed here, fixed before the first comparison runs): entries matching on time within one tick, centre and side; credit within
  one price tick ($0.05); completion within one tick; per-session net within $25 or 5% of the
  session's gross, whichever is larger, on at least 9 of 10 sessions. A session outside tolerance is
  explained, not averaged away.
- *Power:* the intraday comparison's own arithmetic (`packages/flies/docs/intraday-agent-plan.md`:
  paired, one-sided 5%, 80% power, correlation 0.78, $385 a session) needs **about 42 sessions to
  see $100 a session**, 19 for $150. A replay of fewer sessions is a mechanics check, not a finding.
- *Grids declared up front.* Each question names its grid (credit floors, restack thresholds, start
  times) before the run, counts every cell, and corrects for the count (the `core.stats` module
  being added alongside this; Bonferroni or Holm over the declared family, as
  `core.rangefeatures` did). A cell that wins only uncorrected is reported as such.

**4. Coverage is SPX 0DTE only** (see *What it does not cover* above): bwb, calendars, pmcc, and
MEIC on XSP, NDX or RUT get nothing from this recording.

**5. Validate against the live ledger first, or alongside paper.** The questions this exists for
(the credit floor, restack, the start time) are live questions, and the live ledger is the one whose
fills the broker confirmed. Paper is the easier match (same tick rules, no fill model), so it is
done too, but the live-rules replay is validated against live `control`'s rows from the first
session, not after ten paper sessions.

**6. Operations** (below): a replay reads a copy or relies on WAL, and the file has a roll policy.

## Phase 1 — the flies replay

**Shape.** Rebuild each recorded tick as the snapshot the paper loop would have built, and feed it
to flies' own code: `book.process_snapshot` against a scratch ledger per arm. There is no second copy
of the rules. Fields beyond quotes are enriched from what is already recorded:
- GEX surface and walls from `gex_profile_history` / `gex_regime_history` (5-minute rows);
- the session's open, high and low from the spot trail;
- the vol complex from `market_regime_history`;
- the event calendar from `core.events`.

**Validation, before any number is used** (the suite's rule: a guard has to be shown to fail):
- **Paper:** replayed `control` against paper `control`'s real ledger, session by session for at
  least 10 sessions. Entries must match on time (within one tick), centre and side. Credits must
  match within one price tick. Completions and their timing must agree, and per-session net must
  agree within a declared tolerance.
- **Prove it can fail:** break a rule on purpose (spacing off, one rule changed) and confirm the
  comparison reports it.
- **Live rules:** the same replay driven by `live_loop`'s admission: the $1,000 cap via
  `exposure_at`, the 10:15 start, the resting completion limit, and the `fill_model` measurements
  that turn a touch into a fill. Validated against live `control`'s real ledger: entries per
  session, completions, and the cap refusals in `fly_decisions`.

**First uses, once validated:**
- the live credit floor;
- restack under live rules;
- the live start time and the cap size;
- which sources could join the selector;
- the intraday agent's tagged closes, re-priced at other times;
- any paper arm judged over sessions recorded since it was declared.

## Phase 2 — the MEIC replay

**Shape.** The same foundation: `core.quotehistory`, the same enrichment, MEIC's own entry and
stop-management functions, and a scratch ledger per arm.

**Validation:**
- **Stops:** every arm that really stopped must be reproduced to the cent. This is MEIC's existing
  `analytics.validate_stop_derivation` rule.
- **Entries:** `control`'s entries must match its ledger, on the same tolerances as flies.

**What it unlocks:**
- **Gap-through stop fills.** A stop fills at the first recorded cost past its trigger, instead of
  at the trigger itself (currently flagged as optimistic). With MFE below, this is what MEIC truly
  needs the quote history for.
- **Profit-side exits.** Max favourable excursion becomes measurable, and with it profit targets,
  time stops, and hold-to-expiry against early exits.
- **Entry variants MEIC never ran.** Short delta, wing width, OTM floors, entry windows and
  spacing, priced on strikes never traded.
- **The advisor's `advised:*` experiments,** checked over recorded sessions instead of forward only.

**Corrections (2026-10-12).** This phase first promised "an uncensored stop curve". MEIC already
has one: its risk-book `control` runs with `per_side_stop_management: false`
(`config.risk.example.json`), holding every side to settlement, so its recorded maximum-cost path is
never cut short by a real stop, and `stop_policies` already derives each stop threshold's outcome
from it (whether a threshold fired is exact; `validate_against_control` checks the arithmetic). What
that path cannot give is WHERE a fired stop would have filled between ticks (priced at the
threshold today, optimistic by the gap-through) and the favourable side (MFE, `None`). Those two are
the quote history's contribution to MEIC; looser stop ratios are not.

**Order.** After the flies replay validates, because the shared parts (snapshot rebuilding,
enrichment, the validation harness) are proved there first. Where flies and MEIC would otherwise
disagree, the shared code moves to `cherrypick.core`, following core's own bar.

## Operations

- **Reading while the recorder writes.** `gex_history.db` is WAL from 2026-10-12, so a read-only
  connection (`core.db.connect_ro`, which `core.quotehistory` uses) never blocks the recorder and is
  never blocked by it. A long replay should still read a **copy**: one `sqlite3` backup (or `VACUUM
  INTO`) at the start gives it a fixed input it can be re-run against, which a live file is not. A
  bare file copy of a WAL database without its `-wal` misses the newest writes; use the backup API.
- **Growth.** ~8 MB a session for SPX, ~2 GB a year, on a file that also holds the regime series.
  Nothing prunes it.
- **Monthly roll** (to build before the file passes ~1 GB, i.e. by about February 2027): after
  each month's last session, copy that month's samples and quotes into
  `gex_quotes_YYYY-MM.db` beside it (backup API, verified by row count), then delete them from the
  live file. `core.quotehistory.session` then looks in the month's file when the live one has no
  rows for the date. The regime, profile and spot tables are not rolled.

## Rules that hold throughout

- **Read-side only.** A replay never writes a module's ledger or touches a broker. Building it is
  not measurement-affecting.
- **A rule a replay supports still lands at a declared boundary,** and on live through the live
  plan, the same as any other change.
- **Out of sample means sessions after the hypothesis was declared.** A rule found by searching
  recorded sessions is judged on later ones, never on the sessions it was found on.
- **The paper loop keeps running.** It is the replay's reference, it runs the model-driven arms
  (`intraday-agent`, `selector`) in real time, and live's `live_vs_paper` abort rule reads it.
  Moving rule-based paper arms to replay is a separate decision, taken only after validation.

## Timeline

| When | What |
|---|---|
| 2026-10-13 | First recorded session; Phase 0 check |
| from 10-13 | Phase 1 built while data accrues |
| ~10-26 (10 sessions) | Phase 1 validation, live-rules and paper side by side (gap 5) |
| after Phase 1 validates | Phase 2 (MEIC) |
| ~14 sessions out of sample | First tuning reads (credit floor, restack, start time), against their declared grids; an effect of ~$100 a session needs ~42 (gap 3) |
