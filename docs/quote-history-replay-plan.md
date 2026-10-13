# Replaying rules against recorded quotes — flies and MEIC

*Drafted 2026-10-12. **The recording landed the same day** (gex PRs #172–#174): the gex recorder keeps
SPX's 0DTE chain every tick (~15 s), and `cherrypick.core.quotehistory` reads it back. **First
recorded session: 2026-10-13.** Nothing below that line is built yet.*

## Why

Until now no rule a module never ran could be priced honestly after the fact. The stream cache keeps
only the latest quote per leg; the ledgers record a market only for positions a module actually held.
Three questions on 2026-10-12 hit that wall:

- **Flies' live credit floor.** The estimate had to borrow paper's fills for the trades live never
  placed, and its gain rested on two trend days.
- **Restack on live.** The replay of one live session could only *estimate* the restack's credit,
  and could only infer from the spot path whether it would have completed.
- **MEIC's stop curve.** It is censored: a side's path stops being observed once it really stops,
  so looser ratios read "never fired". Its profit side (max favourable excursion) is `None`, because
  "the cache keeps no quote history".

## The data

| | |
|---|---|
| Table | `gex_quote_history` in `~/.cherrypick/data/gex/gex_history.db` |
| Written by | `service.record_quotes`, every gex recorder tick in RTH |
| Content | 20 strikes each side of spot (~±100 SPX points), both rights: bid, ask, mid, quote age, delta |
| Quotes kept | only those `core.streamcache.usable_quote` accepts at 120 s; a missing row is a missing quote |
| Spot | not stored again; `core.quotehistory.session` joins `gex_spot_history` at or before each sample, within 60 s |
| Switch | the gex config's `quote_history` block, **off by default**; on for SPX on the trading machine |
| Size | ~80 rows a tick, ~10 MB a session, ~2.5 GB a year |

What it covers: every flies structure (ATM centres; 15-delta debit-first and bwb wings fit inside
±100), and every MEIC SPX condor since 09-01 (13,462 trades: all legs within 85 points of spot at
entry; the threatened side stays inside the window as spot moves). What it can miss: a leg that
drifts past ±100 on a big trend day, and touches shorter than one tick.

## Phase 0 — check the recording (2026-10-13)

About four samples a minute, gaps around 15 s, ~80 rows each, spot joined on most samples, no
recorder warnings. A one-off check is scheduled for 10:07 ET.

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
- **An uncensored stop curve.** Every side priced on every tick to settlement, so looser ratios
  are scored rather than dropped.
- **Gap-through stop fills.** A stop fills at the first recorded cost past its trigger, instead of
  at the trigger itself (currently flagged as optimistic).
- **Profit-side exits.** Max favourable excursion becomes measurable, and with it profit targets,
  time stops, and hold-to-expiry against early exits.
- **Entry variants MEIC never ran.** Short delta, wing width, OTM floors, entry windows and
  spacing, priced on strikes never traded.
- **The advisor's `advised:*` experiments,** checked over recorded sessions instead of forward only.

**Order.** After the flies replay validates, because the shared parts (snapshot rebuilding,
enrichment, the validation harness) are proved there first. Where flies and MEIC would otherwise
disagree, the shared code moves to `cherrypick.core`, following core's own bar.

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
| ~10-26 (10 sessions) | Phase 1 paper validation; then the live-rules validation |
| after Phase 1 validates | Phase 2 (MEIC) |
| ~14 sessions out of sample | First decision-grade tuning reads (credit floor, restack, start time) |
