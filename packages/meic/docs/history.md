# MEIC history

Dated narrative behind the rules in [`../CLAUDE.md`](../CLAUDE.md). The rules live there; this file
keeps how each was found, with the numbers. For the experiment designs themselves see
[paper-experiments.md](paper-experiments.md).

## Operations

- **2026-06-30** — XSP: $4.00 gross credit, $4.96 fees, net −$0.97. The reason for the fee-adjusted
  credit floor. On 2026-07-06 the standalone `min_credit` / flat $2.00 fee constant and per-symbol
  dollar floors were retired once the fee check became width-aware; the old flat $1.00 SPX floor had
  blocked narrow low-IV SPX credits the new rule admits.
- **2026-07-01** — the DXLink streamer stalled silently for 34+ hours with nothing on the decision path
  noticing; the origin of the `stale_warning` handling in Step 4a and the loop-dependency warning. The
  same day's EOD report flagged ORB as unauditable, which produced the `orb_evaluated` log row.
- **2026-07-02** — the opening range was missed entirely because no loop iteration landed inside
  09:30–09:35; capture moved into the streamer (`_track_orb`, later `packages/streamer/orb.py`).
- **2026-07-21** — the standalone `packages/streamer` became the producer; this module's streamer
  remains as rollback. The cache moved from `data/meic` to `data/marketdata`
  (`docs/history/streamer-package-plan.md` at the repo root).
- **2026-08-09** — supervisor cutover: `meic-paper` runs every 60 s under the orchestrator daemon,
  replacing the module-registered 2-minute `cherrypick-meic-paper-loop` schtasks task.
- **2026-08-14** — `packages/advisor` replaced the retired `cherrypick advise` as the advice producer,
  issuing the same artifact through the same `cherrypick.core.advice` contract; the consumer here
  needed no change.
- **2026-08-20** — the SPX/XSP same-index correlation lint landed in the orchestrator's tests.
- **Early live testing** — a fixed 0.18 delta target on a low-VIX day traded one hard-stop failure (OTM
  distance) for another (credit floor) with no delta clearing both; hence the VIX-banded delta scale.
- **Fixed-point thresholds** — the old fixed 30-point ATR pause over-blocked SPX and never fired for
  QQQ/IWM, and the fixed 35-point quarterly range only made sense at SPX's price; both became
  percentages.

## Live loop (2026-09-17 / 09-18)

- Until 2026-09-17 an accepted placement was recorded as a fill at the submitted limit — an entry
  became an open row the moment the broker accepted it. Fill confirmation, confirmed exits and the
  three re-pricing choices landed that day. The submission seam replaced an adapter that shelled out to
  `tt.py execute_trade` and scraped stdout. `adjust_order` used to call the SDK's replace directly.
  The `ENABLE_LIVE_TRADING` environment fallback was removed the same day.
- `stream_request.py`'s docstring said the live loop did not exist; it did, never imported that file,
  and a live IC's legs survived only while they sat inside the ATM window. `meic-live.json` fixed it.
- 2026-09-18: the halt-flag path, designated account, tick rounding and serialiser moved to core. The
  hand-rolled halt path skipped `$VAR` expansion, so a `CHERRYPICK_HOME` the orchestrator understood
  could point this loop at a file nobody touched.

## Advice

- Through 2026-09-16 the `advised:<base>` tag named a book and every experiment on that base reused it
  in turn; from 2026-09-17 the tag names the experiment (`_advice_profiles` builds one profile per
  entry `cherrypick.core.advice.advised_books` returns), so two experiments on control run as two
  twins of the same control.
- **2026-08-26** — `entry_price_strategy` was removed from `advice.bounds`: only the agent-driven live
  path in `.claude/commands/` reads it. This is the `sign` arm's verdict (3,036 blocked attempts, zero
  fills, 100% decision-agreement with control) reachable by configuration.
- **2026-08-26** — `min_call_otm_pct` was added as a bound. With `min_iv_rank` at 0.0 the IV floor was
  provably inert, yet `call_otm_below_floor` still refused 29 of `advised:control`'s entries — the
  difference between "the regime gate is the sole remaining constraint" and "there are two", which is
  what that experiment's kill rule turned on.

## Read-side CLI (2026-08-26)

meic was the last module to get a `run.py` CLI. `analytics.py` had carried ~20 read functions nobody
could invoke without writing Python, so two questions the advisor asked repeatedly (`settlement-audit`
five times, `gex-gate` four) went unanswered while the code to answer them existed. The console's
`meic-mirror.test.ts` became possible and caught a real divergence on its first run: the console counted
still-open positions in per-arm P&L, reporting each arm down by exactly the fees it had paid so far.
The regime-cuts artifact landed 2026-09-19; its robustness stamps 2026-09-28, when a read that pooled
gross by hand came out $55k off before it was noticed — hence the net-of-fees pin.

## The paper registry

- **2026-07-18** — 15 symbol/wing/credit cells were retired for pinning a *symbol* into their identity,
  which collided with the (arm × symbol) portfolio model; recoverable from git history. The mechanism
  they used (`symbols`, `wing_widths_by_symbol` + `wing_selection`, `stagger_entries`,
  `short_delta_target`) is what every study arm since has used without the `symbols` pin.
- The (arm × symbol) portfolio model exists because one shared budget starved the last symbol
  processed: IWM, 1,313 iterations, zero fills.
- **2026-08-07 → 2026-08-20: the four-stream forward test.** `control` (the deployed policy and the
  reference book; also champion of the champion/challenger surface until it retired on 08-20), `open`
  (every study gate off, no per-side stop, `overlap_scope: "none"`, full per-side path recording — the
  permissive superset every gate variant and derived stop policy was answered from), and
  `width-5`/`width-10` (wing width pinned, paired on the same ticks). Derived stop policies
  (`stop-none`/`stop-0.75-net`/`stop-2.0-side`/`strike-touch`) came from `open`'s paths. Also retired
  by then with written verdicts: the four-tier ladder (for paper), the GEX study pair
  (`gex-open`/`gex-blocked`, superseded by `open`'s own regime tagging) and the original four-way
  wing-width study.
- **2026-08-11** — `min_seconds_between_entries` and `overlap_scope: "sign"` landed in code but were not
  adopted: `control` was then pinned by test to equal `config.json`'s defaults, and
  `open`/`width-5`/`width-10` to share `overlap_scope: "none"`; making `open` sign-ruled would have
  destroyed what it was for. The sign rule was first adopted by the `bp-*` arms on 2026-09-28.
- **2026-08-14 / 08-15** — `control`/`control-drift` carried a stricter `min_iv_rank` than
  `open`/`width-5`/`width-10`, so on 2026-08-14 control went completely dark (0 of 297) while the
  looser arms traded — the reason for `analytics.control_fired` bucketing. On 2026-08-15 the whole stop
  curve was made derivable (`analytics.stop_grid`), turning a 15-session bounded stop experiment into a
  confirmation rather than a search.
- **2026-08-21 — advisor-era cutover.** sign/control-drift retired (zero fills, 100% decision-agreement
  with control); width-5/width-10 retired (ten sessions with control dark on every one, so the paired
  reading never existed — insufficient to separate, not falsified; width moved to the
  `wing_width_points` bound). That day's EOD amendment made `control` the permissive substrate
  (formerly `open`) and retired the gated ex-control as `control-gated`, whose IV floor and default
  negative-GEX gate never produced a fill; its questions moved to the bounds `min_iv_rank` and
  `regime_gex_block_negative`. Era day-1 rows were re-stamped to the new names
  (`meic_control_redefinition` break).
- **2026-09-24 — derived stop pricing reworked.** Before, a fired derived side was priced at its running
  MAXIMUM (on average 2.1× credit on the never-stopping control), which penalised every stop policy by
  ~$1.3M. Real stops fill a median 6% past the trigger (p90 24%) over 3,902 stops. The missing opening
  fee had flipped width-5's answer. `validate_stop_derivation` reproduced 4,817 of 4,817 real stops to
  the cent.
- **2026-09-28 — `live-shadow`.** Live's top level differs from `control` on sixteen settings:
  `min_iv_rank` 0.3, the negative-GEX block (on by `paper.py`'s code default, since `config.json`
  never sets it), OTM floors 0.0035/0.003, the VIX / VIX1D-ratio / ATR pauses, per-side stops, a 14:30
  entry end, `overlap_scope` `shorts`, late-entry bias and a 200/day target. The IV floor plus the GEX
  block is the retired `control-gated` book, dark with 0 fills in the sample era. `arm_added` break
  2026-09-29.
- **2026-09-28 — `bp-5k`/`bp-10k`/`bp-25k`.** Built on a replay of control's 26 era sessions (sign rule,
  window, held to expiry): ~6 / 10 / 22 ICs a day, +$9.9k / +$18.8k / +$52.7k, worst days −$2.3k /
  −$5.0k / −$10.5k. At a flat 15-minute spacing the window admits ~18 entries, so the $25k cap never
  bound. The same replay scored every stop rule on the same entries: holding to expiry won on total at
  every size, stops only shrank the worst day; unspaced, a cap fills in the first minutes on one market
  and loses ~100% of itself on the worst day. `arm_added` break 2026-09-29.

## The gex regime tag

- **2026-09-16** — `regime._classify_gex` bucketed on distance from the gamma flip alone, and the flip
  is interpolated from a zero crossing of cumulative net GEX; a window net negative end to end has no
  crossing, so every such tick tagged `unknown`: 492 of 492 entries on 09-15, at an average net of
  −21B, on a day the gate (keyed on `gex_positive`) read correctly. The tag went sign-first and rows
  began recording `gex_positive` (added on demand by `save_iteration_regime`). The advisor's pack
  re-derives over `iteration_regime` the same way; that table lacks the flag before this date, so those
  sessions still read `unknown`.
- **2026-09-28** — re-deriving only `unknown` rows had left two definitions pooled in the advisor era:
  299 control rows (467 across arms) tagged `deep_positive` on a negative flag or `negative` on a
  positive one. Every row with a sign flag is now re-derived. Control's `deep_positive` moved from
  −$37.5k/11 sessions to −$68.0k/14, and the killed `gex-gate-earns-its-keep` arm stopped showing 168
  negative-GEX trades. The advisor's re-derivation was left alone: it reads one session, and every
  session since 09-16 already carries sign-first tags.

## The settlement audit (2026-08-26)

The advisor asked five times (08-17 to 08-21), escalating to "upstream of the era's baseline", whether
the striking rate of exact full-credit capture meant the marking convention was wrong. Findings:

- **7,908 of 7,908 resolved fills reproduce exactly**, and one settlement price per (session, symbol)
  on every session. No official SPXW settlement print is stored, so the audit bounded the exposure:
  2026-08-20 — the session the negative-GEX reading rests on — moves $14,300 per point of settlement
  error ($1,430 per tenth) against a result of −129,344. `settle_underlying` and `market_context`
  agree to within about a tenth, so the convention is not load-bearing for that finding.
- **A real defect:** `_settlement_value` scored a `None` underlying at zero intrinsic — full credit, the
  most favourable outcome, on exactly the fills nobody could see. 90 rows, 2026-07-13 to 07-27, all in
  the retired ladder era and excluded by `CURRENT_ERA`. Settlement without a price has been refused
  since.
- meic was the last paper module without a `settlement_check` (flies, calendars, pmcc, curve and bwb
  had one); it was off because its `--status` reported none of the fields
  `watchdog._check_settlement` reads, and enabling the flag alone would have produced a check that
  could never fire. The orchestrator's tests now lint that combination.
