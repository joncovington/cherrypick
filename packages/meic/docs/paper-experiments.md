**What this covers:** the history and design of MEIC's paper-trading studies — the current
four-stream forward test (below), and the retired GEX and wing-width studies it supersedes. More
of an internal design record than a how-to guide — see [paper-trading.md](paper-trading.md) for
the general paper-trading system and [risk-profiles.md](risk-profiles.md) for the risk-profile
ladder (now disabled, kept for history and for live `/set-risk-profile`). Part of the
[MEIC module](../README.md) in the cherrypick suite.

## The forward test (2026-08-07) — four streams, one breakeven identity

**Why this exists.** MEIC is not broken by gates or throughput — it sits just below its own
breakeven identity. Under the per-side buy-back design an IC has three outcomes: both sides expire
(+credit), one side stops (a scratch, ≈ −fees), both stop (≈ −credit). Expected value is
`credit × [P(clean) − P(double)] − fees`, so the strategy pays exactly when

```
P(both expire clean)  −  P(both stop)   >   fees / credit
```

Measured over 290 ICs / 11 sessions (2026-07-10 → 08-04, pre-cutover `era='book'` rows):
`10.0% − 4.8% = 5.2% < 5.5%` — **margin −0.4%**. And it is worse than that headline number: the 29
"clean" both-expired ICs averaged **−$2.80**, not the ~+$108 a clean win should pay, because ITM
cash settlements were hiding inside the nominal "expired" bucket (see `analytics.expired_detail`,
which now splits `expired` into `expired_otm`/`expired_itm`/`expired_unknown` everywhere).

`analytics.breakeven_scorecard` computes this identity per arm, per period — reproduced this
document's own manual finding exactly (`{'trades': 290, 'clean_pct': 10.0, 'double_stop_pct': 4.8,
'breakeven_bar_pct': 5.5, 'margin_pct': -0.4}`) — and is the health line in every EOD report
(`paper-eod-<day>.md`'s "Arm scorecard" section) and the dashboard's Performance view.

**The stop is the canonical convention, not a bug.** Research confirms the per-side stop keyed to
the *whole IC's* credit (not each side's own credit) is the published Chambless MEIC method,
designed so a single-side stop scratches the trade. `stop_trigger_ratio: 0.95` is the documented
"MEIC+" variant (stop $0.10 below breakeven) that turns scratch days slightly positive. But the
stop's cost is real and unpriced by the convention: of stopped sides with a recorded settlement
counterfactual, the large majority settled OTM — money paid to stop a side that would have expired
worthless anyway. That gap is what the derived stop policies below are built to measure.

### The four streams

| stream | what it is | why it exists |
|---|---|---|
| `control` | today's deployed policy incl. its 0.95×net stop, `overlap_scope: "shorts"` | the reference book **and** the champion (`calibration.champion` in the orchestrator config) — validates every derivation below |
| `open` | every study gate off, no per-side stop, `overlap_scope: "none"`, full per-side path recording (running max cost, settle values on every exit path, first strike-touch time) | the **permissive superset** — every gate variant and every stop policy is a read-side split of this one stream's own recorded rows, never a reason to run a sixth or seventh arm |
| `width-5` | SPX wing pinned to 5, otherwise identical gates to `open`, but keeps `control`'s real 0.95×net stop | the one genuinely non-derivable structural variant — wing width isn't a float you can re-derive after the fact |
| `width-10` | SPX wing pinned to 10, same design as `width-5` | paired against `width-5` on the same ticks; expected to near-duplicate `control`'s own book under `control`'s widest-first selection — `analytics.arm_divergence` reports how often the two streams actually realized different strikes, so that isn't assumed |

All four write `risk_profile = <arm name>` (there is no separate `arm` column — every existing
reader, from the orchestrator's `report`/`calibrate` to `dashboard.py`/`section.py`, already groups
on `risk_profile`). Rows carry an `era` column: `'book'` for the pre-cutover ladder-era history
above, `'sample'` for everything from the four-stream design onward — `analytics.py`'s functions
default to `era='sample'` so the two selection intensities are never silently pooled.

### Why entries and exits are both answered read-side

**Entries.** With `overlap_scope: "none"` and no position interaction, every gate variant is a
pure entry filter and a deterministic function of a float `open` already records per entry (IV
rank, ATR/spot, GEX bucket and value, skew, center offset, trend — see `regime.py`'s 8 dimensions).
So `atr-open`, `ivr-open`, `gex-mag`, and the retired `gex-open`/`gex-blocked` pair (see below) are
all answerable via `analytics.by_regime(conn, dimension, arm="open")`, immediately, rather than
after a dedicated arm collects its own 14 sessions.

**Exits.** The same logic, more strongly: a fully-marked un-stopped position's path contains every
stop policy's outcome. `open` records, per side, the running max cost, the settle value on every
exit path (not just cash-settle), and the first tick the spot crossed the short strike. From those
recorded fields, `stop_policies.derive()` computes what four policies *would have* paid, at **1×
position cost** and with exact pairing (same entries, same strikes, same credit, same session):

| policy | rule | why it's interesting |
|---|---|---|
| `stop-none` | hold to settlement | flagship candidate — external literature (Option Alpha's SPY/0DTE backtests) predicts no-stop beats every stop level tested; the tail risk is real, so its scorecard carries a held-through-ITM count and worst-loss line, not just an average |
| `stop-0.75-net` | cost ≥ 0.75 × net_credit | a clean tightening of `control`'s own basis, zero censoring |
| `stop-2.0-side` | cost ≥ 2.0 × that side's own credit | the "gave back its own premium" rule people mean when they say that — 1.0× side credit does NOT mean that (see the corrections below); for a balanced split this is ≈ `control` already, so it's a re-basing test more than a tightening |
| `strike-touch` | stop only on an actual spot breach of the short strike | strongest per-dollar support in the tracked sample — most stops fired on sides that were never breached |

This is only valid because paper has no market impact and positions are independent, and it is
**validated, not assumed**: `analytics.validate_stop_derivation` reconstructs `control`'s real
0.95×net mechanism from `open`'s recorded fields and must reproduce `control`'s realized P&L within
fill noise (confirmed exact — zero discrepancy — against 17 real production `control` rows). If a
future change makes that validation fail, every number under "Stop-policy table" in the EOD report
is void until it's fixed.

### Corrections this redesign made to earlier assumptions

A few claims from the pre-2026-08-07 planning were checked against the real ledger and turned out
wrong — recorded here so they aren't re-asserted:

| earlier claim | verified truth |
|---|---|
| `gex_positive_at_entry` = 1 on all 290 book-era rows | 1 on 78 non-null rows; 212 NULL. The dominant defect was missing GEX instrumentation (73% untagged), not a gate that never fired |
| `overlap_scope: "none"` was already a working config flip | only `"shorts"` had a branch; `"none"` fell through to the strictest path (`"all"`) — a silent throttle. Fixed by adding an explicit branch (`paper.py`) |
| the per-side stop is "mis-based by construction" | it is the canonical Chambless MEIC convention (see above) — reframed as canonical-vs-variant, not a bug |
| `1.0× side credit` giveback means "gave back its own premium" | giveback of 1.0 means **cost = 2.0× side credit** — at 1.0× the side exits at its entry price having given back nothing. `stop-2.0-side` above is the rule people actually mean |
| the retired `width-*` arms "never traded a row" | they filled on 2026-07-30/31; the rows were destroyed by a missing-columns incident during a later schema change — the same failure class `stale_writer_columns()` (`db.py`) now exists to catch |
| `per_side_stop_trigger` would be "wired" this phase | superseded by the read-side derivation above: rather than adding a fifth live-configurable trigger-basis mode, the four bases are answered from `open`'s recorded paths via `stop_policies.py`. The config key was unread by any code path — decorative — and was removed from `config.json`/`config.example.json` 2026-08-07 rather than left as a misleading no-op |

### Retired-profile disposition

| cell | disposition |
|---|---|
| `large-spx-holdtoexpiry` (2026-07-16 → 07-18, one zero-entry session) | revived in spirit — `stop-none` is now the flagship derived policy |
| `large-spx-lateonly`, `large-spx-gexmag` (same window, both zero-entry) | subsumed by `open`'s widened window and read-side regime splits |
| the 15 symbol/wing/credit cells (2026-07-13 → 07-18) | stay retired — each pinned a symbol into its identity; see the account-size study section below |
| `width-2`/`width-5`/`width-10`/`width-adaptive` (2026-07-28 → 08-05, retired without visible rows) | revived as `width-5`/`width-10` above — the "never traded" framing was corrected (see the table above) |
| `aggressive`, `very-aggressive` | disabled with the rest of the ladder 2026-08-07 (see `config.risk.json`'s `_disabled_note`s); kept for history and for a deliberate live experiment |
| `gex-open`/`gex-blocked` (2026-08-01, one session, byte-identical) | disabled 2026-08-07 — subsumed by `open`'s regime tagging, see the GEX study section below |

**The kill rule this document follows:** a stream is retired only by an explicit written verdict
here, never by silent deletion — `config.risk.json` keeps every retired profile's exact key set
with `enabled: false` and a `_disabled_note` explaining why, rather than deleting it. A stream that
enters zero trades for 5 consecutive sessions is escalated in the EOD report, not quietly dropped.

---

## Independent sampling (2026-08-01) — every profile is a sample stream

**What changed.** All profiles now run uncapped (`max_concurrent_ics: 99`), with no entry spacing
(`min_minutes_between_entries: 0`) and `overlap_scope: "shorts"` — only an exact **short-pair**
repeat is refused. The short pair is the profit zone, exactly as a butterfly's centre is, and flies
enforces the same one-structure-per-centre rule for the same reason: a second structure on the same
zone doubles the bet without adding a zone.

**Pacing is now the market's, not a clock's.** Entries are limited by how many distinct short pairs
the underlying's path makes available during the window — the model flies uses. Measured on a
simulated SPX session, samples per profile went **4 → 13**, and the two changes compound: the overlap
rule alone gives 4→8, dropping the clock alone gives 4→5, because overlap was the binding constraint.

**`stagger_entries` stays on** even with zero spacing. It is what keeps `daily_ic_trade_target` a
HARD cap and, more importantly, skips the over-target credit-floor tightening — without it, later
entries in a session would face a stricter floor than earlier ones and every comparison would
silently measure floor drift.

**Two consequences to keep in view.**

1. **The risk ladder lost its offset axis.** `max_concurrent_ics` used to fall as rungs got riskier
   (4/4/3/2), bounding book risk. With no book there is nothing to bound, so the rungs now differ
   **only** in entry quality — IV floor, credit floor, delta ceiling, OTM distance, stop. That is a
   cleaner comparison than before, but it is a real change of meaning, and `conservative` is no
   longer "the config defaults as a book".
2. **Paper models no buying power.** An uncapped stream is only honest as a *sample* stream: its P&L
   is a sum of independent samples, **not a book's P&L**, and must not be read as one. Rows before
   2026-08-01 are book-semantics and are not comparable across the switch.

**What this does and does not buy.** More samples per session improve coverage of time-of-day,
moneyness and strike space, and reduce the influence of any single entry's luck. They do **not**
proportionally raise the effective N: same-day trades share a regime, so sessions remain the unit of
independence. For the GEX study specifically the gain is smaller still — net-GEX sign was one-sided
on 3 of the 4 sessions measured (2.3%, 98.3% and 100% positive; only 07-30 saw both states), so the
blocked-vs-allowed contrast is mostly a between-day comparison regardless of intraday sample count.


> ## Retired 2026-08-07 — subsumed by `open`'s regime tagging
>
> **The `gex-open`/`gex-blocked` arm pair below has been disabled in `config.risk.json`.** The two
> arms went byte-identical over their one session of real data: `gex_positive_at_entry` never took
> the value 0 among the 78 rows that carried it at all (73% of rows were untagged, not gated), so
> the "control vs treatment" comparison never had an opportunity to diverge. The question this
> study asked survives — it's now answered read-side from the `open` stream's own recorded
> `entry_gex_bucket`/`entry_gex_value` (`regime.py`, `analytics.by_regime`), the same
> supersets-not-parallel-books design the wing-width study below was rebuilt around. See "The
> forward test (2026-08-07)" at the top of this document for the full design and
> `GATES.md`'s gate 7 for the gate-level writeup. This section is kept as the historical record of
> why a dedicated control/treatment pair was tried first and what it actually measured.

## GEX study (2026-08-01) — does the GEX gate earn what it cuts?

**The question.** `regime_gex_block_negative` refuses entry whenever net GEX is confirmed negative.
It is on by default and it is the module's single biggest brake: across 2026-07-29…31 it blocked
**400 in-window iterations** (234 QQQ, 166 XSP). Nothing establishes that the trades it removes would
have been worse than the ones it keeps. Only 20 historical trades carry GEX at all, all from one
session, and there is no backfill path — so this is forward-looking only.

**The arms.** `gex-open` (control, ungated) and `gex-blocked` (treatment, runs the live policy),
identical in **every** key except `regime_gex_block_negative`. `test_gex_study_arms_differ_in_exactly_one_key`
pins that, because an arm edited on one side and not the other silently turns the study into a
comparison of two different strategies — the failure flies recorded twice, where a wider window let
one arm out-earn control purely by trading more often.

Both are forced-sampling like the width arms (`stagger_entries`, 15-minute spacing,
`max_concurrent_ics: 99`, `daily_ic_trade_target: 24`) so the **gate is the only thing that ever
binds**, and both face an identical credit floor on identical ticks — `stagger_entries` makes the
daily target a hard cap, which also skips the over-target floor tightening that would otherwise drift
between them. Symbol-agnostic, so the `(profile × symbol)` grain supplies that axis.

**Read order** — `python -m cherrypick.meic.experiment`:

1. **The within-arm counterfactual, on `gex-open` alone.** This is the primary read. All three GEX
   gates are pure entry filters and every fill stamps the GEX state it saw, so splitting the ungated
   arm's own trades by `gex_positive_at_entry` shows exactly what each gate would have blocked, on the
   same days, with every trade informative. `block_negative`, `require_positive` and a swept
   `min_flip_distance_pct` all come out of the same rows — three answers from one arm.
2. **`gex-open` vs `gex-blocked` P&L.** Secondary. The only read that sees the path-dependent
   portfolio effect (a blocked entry frees a slot and changes what trades later), which the within-arm
   split structurally cannot.
3. **The divergence log** (`loop_log.action = 'width_arm_divergence'`, now written for `gex-` arms
   too): who sat out and why. A refused entry is an outcome, not missing data.

**Sessions are the sample, not trades.** Same-day trades share a regime. `experiment.py` reports
`sessions` beside every trade count and refuses to quote a bootstrap interval below **14 sessions**
(`PROMOTION_RULE.min_days`); the bootstrap resamples whole sessions rather than individual trades,
because with fewer than ~30 clusters per-trade inference is badly optimistic. Read at 14, decide at
20+.

**The decision.** If the blocked trades are not meaningfully worse than the allowed ones, the gate is
cutting ~40% of samples for nothing and `regime_gex_block_negative` should default to `false` — the
same standard applied to flies' pre-close ITM exit. If they are worse, the flip-distance sweep says
whether the stricter variants are worth enabling. Nothing changes the live loop automatically.


# Paper-trading experiment cells (account-size study)

> **2026-08-01 — symbols narrowed to SPX, registry narrowed to two rungs.** `symbols` is now
> `["SPX"]`, dropping XSP and QQQ. That ends the deliberate cash-vs-physical settlement pairing
> chosen on 2026-07-28 (QQQ was the physically-settled half) — accepted because no MEIC session has
> recorded a trade since 07-28, so nothing was actually accumulating on that axis. The reason for the
> move is fee drag: the flat $5-per-ITM-strike settlement fee and the per-contract commission stack
> are near-constant in dollars while credit scales with the underlying, so a $7,500 index carries them
> far better than a $750 one. `aggressive` and the four `width-*` arms were disabled the same day
> (see `config.risk.json`); only `conservative` and `moderate` still run. Eight profiles across two
> symbols was sixteen portfolios competing for the same ticks — more books than any of them was
> answering a question about.

> ## Retired 2026-07-18 — resumed 2026-07-28 as the wing-width study
>
> **The 15 experiment cells described below were removed from `config.risk.json`** because each one
> pinned a *symbol* as part of its identity (`large-spx`, `small-xsp`, …), which collided with the
> portfolio model the paper study runs on — the symbol is its own axis, one portfolio per
> **(profile × symbol)** pair. This document is kept as the reference for the per-profile mechanism
> (`symbols`, `wing_widths_by_symbol`, `wing_selection`, `stagger_entries`, `short_delta_target`,
> `regime_gex_require_positive`, `per_side_stop_management`), which is still fully supported by the
> engine; the cells themselves are recoverable from git history.
>
> **The study resumed 2026-07-28** as the wing-width study below, built the way this section always
> said it should be: symbol-agnostic branches (no `symbols` pin), the portfolio grain supplying the
> per-symbol split. See [risk-profiles.md](risk-profiles.md) for the ladder's design rationale and
> the two-axis model.

---

> ## Retired 2026-08-05 — the wing-width study never traded
>
> **The four `width-*` arms have been removed from `config.risk.json`.** They were added 2026-07-28,
> stood down 2026-08-01 with **zero rows** in `paper_trades.db`, and are now retired outright rather
> than left disabled — a definition that has never produced a datum and has no owner reads as pending
> work every time someone audits the registry, and the registry's own problem was that sixteen
> competing portfolios (8 profiles × 2 symbols) is more than can be read.
>
> Nothing was lost, because nothing was collected. The design below stays as the record of what the
> study *was*, exactly like the retired-cells section above it: if wing width becomes the question
> being asked again, re-add these arms from this text (or from git history) rather than re-deriving
> them. The symbol reduction to XSP/QQQ that this study motivated is a separate operational choice —
> see `config.json`'s `symbols`, which now trades SPX.
>
> The infrastructure it introduced outlived it and is **not** retired: the `gex_net_vol_at_entry`
> stamp, the `pin_risk_applied` flag, the arm-divergence log, and the dashboard's study-arms frame
> now serve the current four-stream design (see the top of this document).
>
> **Correction, 2026-08-07: "zero rows" was wrong.** A later audit found these arms *did* fill on
> 2026-07-30/31 — the rows were destroyed by a missing-columns incident in the same class of bug
> `db.stale_writer_columns()` now exists to catch (see the corrections table at the top of this
> document). The disposition stands regardless: the question is revived, not the specific rows,
> as `width-5`/`width-10` — a paired two-arm design instead of four candidate widths behind one
> profile, run under `overlap_scope: "none"` alongside `open` rather than the account-size study's
> throughput caps.

## The wing-width study (2026-07-28, retired 2026-08-05)

Four forced-sampling arms in `config.risk.json` — `width-2`, `width-5`, `width-10`, `width-adaptive`
— isolate wing width as its own variable, the question the retired account-size cells couldn't
answer because they bundled width into a symbol-pinned identity. All four are symbol-agnostic (no
`symbols` key), so the (profile × symbol) grain gives each arm its own book per configured symbol —
currently **XSP** (cash-settled) and **QQQ** (physically-settled), the suite's two decorrelated,
liquid 0DTE-eligible underlyings. XSP+SPY was considered and rejected: same underlying, maximum
correlation by construction, and it duplicates rather than contrasts settlement mechanics. XSP/QQQ
are still meaningfully correlated (~0.90, like any two liquid US index instruments) — mitigated by
paper-only books and per-cell caps, not by any claim of independence.

**The design, in one line:** every 15 minutes, evaluate the same market snapshot against three
width pins (2/5/10, `wing_selection: "fixed"`) and one dynamic-width policy arm
(`wing_selection: "widest"` over 2/5/10, the fee-drag-bias default) — same regime gates, same
spacing, `max_concurrent_ics: 99` so each structure is an independent sample rather than a budgeted
book. `conservative` keeps running as a **reference curve only** (different sampling semantics —
soft daily target, floor drift, late-entry bias — so it is not a controlled comparison against the
arms).

**Why forced sampling instead of the account-size cells' throughput caps.** The retired cells used
concurrency/daily caps to keep several time-cohorts open (~11 profile×symbol evaluations/iteration).
The width study instead removes the cap entirely: at up to 20 possible 15-minute ticks in the
09:30–14:30 paper window, each width gets up to ~20 independent samples/day/symbol, and every entry
faces the *same* credit floor (`stagger_entries` makes the daily target a hard cap and skips
over-target floor-tightening) — so a paired comparison across widths on the same tick is valid.

**Reading the results, in order:**
1. **True pairs first** — ticks where every fixed arm entered (from `ic_trades` entry timestamps).
   Width-proportional credit floors mean each arm self-selects its entry set, so unrestricted
   cross-arm comparison carries selection bias; paired differences on common ticks cancel the
   session's regime.
2. **Divergence second** — `loop_log` rows with `action = 'width_arm_divergence'` record which arm
   sat out a tick while a sibling entered, and why (floor / overlap / spacing). A refused entry is a
   width *outcome*, not missing data.
3. **Per-cell streams third** — each arm's own conditional performance, the deployable read (that
   arm as an actual strategy under its own gate).
4. Same-day trades are clustered — the effective N for a regime-level claim is the **day** count
   (≥14–20 sessions), not the trade count, regardless of how fast 6-session significance shows up
   per cell.

**Surfaces:**
- `python -m cherrypick.meic.db get_range_summary` — `by_profile["width-2"…]` pools each width across symbols
  (convenient, but mixes cash/physical settlement mechanics — read with that caveat); portfolios
  `width-2:XSP`, `width-2:QQQ`, … via `compare_profiles`.
- The dashboard's Performance view carries a **Study Arms** frame (named *Width Study* while this
  study ran): one cumulative-P&L line chart
  per symbol, one line per arm (`width_study` in the API payload, computed the same way as every
  other performance series — `_pnl_series` per (symbol, profile) cell).
- Every fill also stamps `gex_net_vol_at_entry` (the flow/volume-weighted GEX series) beside the
  existing OI-based `gex_net_at_entry` — the entry gate stays OI-based (matching the live loop), but
  0DTE positioning largely builds intraday where OI is blind, so the flow series lets a regime read
  ask later whether volume-GEX would have gated differently. Flip *distance* is derivable from the
  existing `gamma_flip_at_entry` + `gex_spot_at_entry` pair — no separate column needed.
- QQQ's physical-settlement path applies a modeled pin-risk penalty that scales with wing width; a
  force-closed QQQ trade that paid it is flagged (`ic_trades.pin_risk_applied`), so QQQ cells can be
  read both gross and net of that modeled cost rather than treating a width "result" there as free of
  the two uncalibrated settlement-friction constants.

The parallel-shadow paper engine (`cherrypick/meic/paper.py`, driven unattended by `cherrypick/meic/paper_loop.py`)
evaluates **every** profile in `config.risk.json` against each iteration's market snapshot, per
symbol, writing all books to `~/.cherrypick/data/meic/paper_trades.db`. Beyond the four-tier risk
ladder (conservative → very-aggressive), the registry *used to* hold **experiment cells** whose
purpose was to collect enough variation in the placed iron condors to analyze optimal risk profiles
for **small, medium, and large accounts** — by varying wings, symbols, and the minimum credit, and
by staggering entries across the day. This was paper-only; nothing here ever touched the live
account.

## What makes an experiment cell different from a ladder tier

The ladder tiers are complete presets that share one risk-appetite axis. Each experiment cell is a
**partial overlay** merged onto `config.json` that pins one `(symbol, wing, min-credit)` cell using
per-profile keys the ladder does not carry:

| Key | Meaning |
|---|---|
| `symbols` | The subset of the account's symbols this profile trades. A cell pinned to `["XSP"]` is skipped entirely for an SPX snapshot. Absent ⇒ trades all base `symbols` (the ladder's behavior). |
| `wing_widths_by_symbol` | This profile's own wing shortlist per symbol. `paper_loop` builds each symbol's candidate menu from the **union** of every profile's widths; each profile then picks from its own subset. |
| `wing_selection` | How to pick among clearing candidates: `widest` (default, fee-drag bias), `narrowest` (small-account cells), or `fixed` (the shortlist's own order). |
| `stagger_entries` | Opt-in. When true, enforces the entry window (`entry_window_start`/`_end`), a hard daily cap (`daily_ic_trade_target`), and spacing (`min_minutes_between_entries`) so entries spread across the session instead of filling in the first passing iterations. The ladder omits this and keeps the prior unstaggered behavior. |
| `min_minutes_between_entries` | Minimum spacing between this profile's entries (staggering). |

**Account-size character lives in wing width + symbol** (the dollar risk per IC), not in throttling
how many samples get collected. The cells use *throughput* caps (concurrency 4 / daily 6) so several
time-cohorts stay open at once for denser time-of-day coverage; reconstruct a strict small account
(e.g. 2 concurrent) by filtering the tagged rows in analysis.

## The retired symbol/wing/credit roster (removed 2026-07-18)

> **None of the seven profiles below exist in `config.risk.json`.** They were removed 2026-07-18 for
> pinning a *symbol* into a profile's identity, which collided with the portfolio model. The table is
> kept so historical books opened under these names stay readable, and because the per-profile
> mechanism it documents (`symbols`, `wing_widths_by_symbol` + `wing_selection`, `stagger_entries`) is
> what every study arm since has used — without the `symbols` pin. The **live** roster is the
> four-stream forward test described earlier in this document.

| Profile | Symbol | Wings | Pick | min_credit | Concurrent | Daily | Spacing | Tier / purpose |
|---|---|---|---|---|---|---|---|---|
| `small-xsp` | XSP | 2, 3 | narrowest | 0.12 | 4 | 6 | 45m | Small acct, cash-settled |
| `small-iwm` | IWM | 3, 5 | narrowest | 0.12 | 4 | 6 | 45m | Small acct, physically-settled |
| `medium-qqq` | QQQ | 5 | fixed | 0.15 | 4 | 6 | 45m | Medium, physically-settled |
| `medium-xsp-wide` | XSP | 5, 10 | widest | 0.15 | 4 | 6 | 45m | Wing-risk contrast vs `small-xsp` |
| `large-spx` | SPX | 5, 10 | widest | 0.15 | 4 | 6 | 45m | Large acct |
| `explore-spx-tightcredit` | SPX | 5 | fixed | 0.20 | 4 | 6 | 45m | Credit-floor sweep (stricter) |
| `explore-xsp-loosecredit` | XSP | 2 | fixed | 0.10 | 4 | 6 | 45m | Credit-floor sweep (looser) |

QQQ and IWM are physically settled — they exercise the paper engine's existing early
force-close + modeled friction / pin-penalty path (see `docs/paper-trading.md`); no new settlement
code was added for them.

## How staggering behaves through the day

The two caps do different jobs: **concurrency** holds several time-cohorts open at once, while the
**daily cap + spacing** govern cadence. Example — `small-xsp` (concurrent 4 / daily 6 / 45m) over the
10:00–14:30 window enters around 10:00 → 10:45 → 11:30 → 12:15 (four cohorts live), then refills at
~13:00 and ~13:45 **as earlier ICs stop out and free a slot**, up to the daily cap of 6. Because
0DTE cash-settled XSP is left to expire and only frees a slot intraday on a per-side stop, on a calm
day a cell simply holds its four cohorts; on a choppy day it rotates through more.

## Reading the results

Every book is tagged with its profile name in `ic_trades.risk_profile`, and the whole read side is
profile-name-agnostic:

- `python -m cherrypick.meic.db get_range_summary --start <d> --end <d>` groups metrics by profile.
- The daemon's deterministic EOD report (`logs/paper-eod-<day>.md`) tables every profile that
  traded, with a **Symbol** column — so each cell reads directly as an account-size / wing / credit
  comparison — and notes which configured profiles were idle.
- The live dashboard's profile selector lists whatever tags exist.

### Read the session count before the trade count (2026-08-10)

`analytics.regime_coverage` reports `sessions`, `daily_scale`, `effective_n` and `underpowered`
alongside the row counts, and `by_regime` reports sessions per bucket, because **rows are not
independent draws**. Under the independent-sampling convention above this book takes hundreds of
entries per session, so a dimension can carry a four-figure trade count and rest on two days.
Measured on 2026-08-10: 967 tagged rows across **2 sessions** (08-07 and 08-10), and `vol_realized`
— trailing 5-day ATR over spot — varied by 0.00005 within a session against 0.00154 across them,
which is an effective n of **2**, not 967.

That is why `underpowered` is keyed on the session count rather than `effective_n`: rows inside one
session share that session's market, so even a genuinely intraday dimension is bounded by how many
days it has seen. It is deliberately a separate flag from `degenerate` — the two look identical in a
bucket table and call for opposite responses. A degenerate dimension says *re-cut the float* (the
measure is stored precisely so this costs nothing); an underpowered one says *collect more sessions*,
and re-cutting it instead is fitting a boundary to a handful of days. `daily_scale` is measured from
the data rather than declared per dimension, since a dimension can be daily-scale in one period and
intraday in another.

### The regime you refused is recorded too

`iteration_regime` (see the MEIC CLAUDE.md's Database section) writes one row per iteration × symbol
regardless of whether anything filled. Every regime column on `ic_trades` is conditioned on having
entered, so on its own the ledger can only answer "which regime does this arm win in" over ticks that
already cleared every gate — the refused ticks, which are most of them, left no trace of what they
refused. With the iteration row, `gate_block`'s *which gate* and the regime row's *what the market
was* combine into the counterfactual the gates have never been measurable against.

## Validation (forward paper on tastytrade)

All cells are validated **forward** by the automated paper engine (`paper_loop.py`) against live
tastytrade data — the cells accumulate real, tagged trades day to day and surface in
`get_range_summary` / the EOD report / the dashboard.

> ⚠️ The SPX historical replay tool (`cherrypick/meic/paper_replay.py`) is **not** used: its bulk-extraction
> design is incompatible with 0DTESPX's terms of service (confirmed 2026-07-13). See
> [0dtespx-api.md](0dtespx-api.md) and the warning in [paper-trading.md](paper-trading.md). Historical
> backtesting is therefore out of scope here; the sanctioned server-side alternatives (0DTESPX
> practice sessions / strategy backtester) would require re-expressing MEIC in their order model and
> are not currently pursued.

## Load note

Per-profile symbol pinning keeps the per-iteration work modest (~11 profile×symbol evaluations, each
pinned cell touching one symbol). The staggering DB read is issued only for profiles that opt in. If
the roster is later expanded into a dense grid, batch `process_symbol`'s per-profile `get_open_trades`
shell-outs into one read per iteration before adding many more cells.
