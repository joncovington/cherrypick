# Operations: the 2026-07-29 cutover and the gaps since fixed

> **Status: frozen record, 2026-11-08.** Moved out of [../operations.md](../operations.md), which
> describes the suite as it runs now. Nothing here is current instruction.

## Rollback note (supervisor transition window)

**Rollback** (documented for one transition window): `git tag pre-supervisor` marks the last
schtasks-driven commit. To roll back: `run.py uninstall` (new code), check out the tag, `run.py
install` (old code re-registers every per-job task; the module `--install-task` helpers exist
there, though they were removed from current code on 2026-10-08). The old and new mechanisms never coexist — each `install` deletes the other's registrations.

## The 2026-07-29 cutover

The previous production checkout was retired and this fork became the live instance — the shadow-mode
plan (task prefixes, port offsets, side-by-side A/B) was superseded by direct inheritance of
`~/.cherrypick` with `CHERRYPICK_HOME` unset. What the cutover did, for the record:

- `install` run from the fork checkout — same task names, commands re-baked at fork paths.
- Home configs updated to the fork's shape; stale pre-monorepo `repo` keys removed so a path miss can
  never clone retired code; **`data_epoch.date = 2026-07-29`** set (calibrate uses only post-epoch
  sessions; production's last partial session was 07-28); the hardened promotion checks
  (`min_return_on_capital: 0.02`, `require_slippage_survival: true`) enabled in MEIC's
  `calibration.rule` — pending since phase 2.
- `state/stream_requests/meic.json` corrected (it was hand-written at the 2026-07-21 streamer cutover:
  seven retired symbols, leg query against the live ledger) and the MEIC writer added so it can never
  go stale again; streamer restarted once so the XSP/QQQ underlying set bound.
- Two live defects caught in verification: a gex-recorder surviving from the retired checkout
  ("already running" at install — stop it so the relaunch runs current code), and the streamer's
  credential chain missing the shared `cherrypick-broker` fallback after the keyring migration
  (fixed; regression-tested).
- The paper `drawdown` alert remains **deliberately disabled** in the home config (paper drawdown
  noise is unwanted; live drawdown is the live module's concern) — note a forced-sampling study arm's
  volume would trip any floors sized for ladder volume anyway.


## Known gaps that were fixed

These were struck through in the runbook's "Known gaps" list once fixed.

- ~~**Thin pre-open margin.**~~ **Fixed 2026-08-06** ([#64](https://github.com/joncovington/cherrypick/issues/64)),
  **superseded 2026-08-09**: the dedicated `cherrypick-preopen` task (streamer liveness every 2 min,
  09:00–09:35) became the supervisor's `streamer-health` job — same check (`_check_streamer_health`
  reused, never copied; no heartbeat write; stops at the door on a non-trading day), every 60 s
  across the whole 09:00–16:00 session. The original reasoning survives intact: the full watchdog
  tick still never speeds up to protect the streamer — the check just has its own cadence now that
  per-job cadences are free.
- ~~**`doctor` coverage holes.**~~ **Mostly fixed 2026-08-06** ([#65](https://github.com/joncovington/cherrypick/issues/65)).
  `doctor` now checks every orchestrator-owned task — `trade-notify`, `log-archive`, `reconcile`
  and `preopen` — resolving each name through the same settings helper `install` uses,
  so a config-driven rename can't desync the check. Opted-out-and-absent reports as healthy; enabled-
  but-missing, and disabled-but-still-registered, both warn. **Still open:** nothing cross-checks that
  `stream_requests/*.json` cover the symbols the modules actually trade — that half is better done
  alongside [#62](https://github.com/joncovington/cherrypick/issues/62), which introduces the notion of
  a request the producer has not served.
- ~~**`holidays_loaded=0`.**~~ **Fixed 2026-08-05.** `timeutil.load_holidays` was scanning MEIC's
  config for `nyse_holidays_<year>` keys that had been retired when the calendar moved into
  `cherrypick.core` — so the scan matched nothing and every caller, the watchdog included, ran with an
  empty set and treated market holidays as ordinary sessions. It now reads
  `cherrypick.core.calendar.nyse_holidays` (this year and next; `doctor` shows 20 rather than 0), and
  degrades to weekday-only gating if that lookup ever fails.
