# Mirrors and bridges

Moved from the console CLAUDE.md, which keeps the rule: mirror a query, bridge a derivation, and pin
a mirror with a test against the module itself. These are the current ones.

- `server/test/pmcc-mirror.test.ts` — open count, book set and each book's net vs `run.py headline`,
  to the cent. Compares empty against empty until pmcc opens a position under its current design, so
  treat it as armed, not as evidence.
- `server/test/meic-mirror.test.ts` — `readers/meic.ts` (the largest mirror) per-arm trades,
  sessions, gross, fees and net vs `python run.py headline --era ALL`. Resolved rows are an
  allow-list (`pnl IS NOT NULL`), never a deny-list of statuses, or open rows leak fees with no gross.
- `server/test/flies-mirror.test.ts` — `analytics/fliesPayoff.ts` is a port of `fly.py`'s payoff
  core, pinned against `fly.py`'s own fixtures (run unconditionally) and against
  `fly.position_pnl`/`position_floor`/`book_floor` over the ledger to the cent. The floor is computed
  on the module's strike-anchored scan (`scanPrices`/`bookFloor`), never the 120-point display grid.
  One thing is deliberately not equalised: which zone is "the band" on an exact peak tie is the
  module's recorded pick (`fly_books.band_low/high`), and changing that rule is a declared-boundary
  change for flies; the test tolerates a different pick only when both peaks agree to the cent. The
  forest sentence says what the floor knows and no more ("from 7659 upward", "at or below 7645",
  "locked").
- **bwb evidence is bridged** (`services/bwbEvidenceBridge.ts`): `live-gap` (the live arm against
  paper's same arm, refusals named with their gate, e.g. `live.vix9d_vix_max`), `entry-context`,
  `regime-split` and `iv-premium` (the `vol` tab). Each verb opens the ledger read-only.
- **Bridged, not mirrored:** calendars' exit-policy table and week anchors go through
  `services/calendarsBridge.ts` (memoised 5–10 min) — the policy replay is validated to the cent
  against the real books, and a TypeScript copy would be validating the wrong derivation; the
  anchors are NYSE holiday arithmetic whose structure tag keys every result.
- **Flies evidence** (`services/fliesEvidenceBridge.ts`, memoised 2 min): `run.py live-gap`
  (live vs paper paired per session, split at the `completion_rule` break; each unmatched entry
  carries the other book's reason and category, each pair its largest gap, and a "why the books
  differ" card sums them per era -- `WhyTable`, `SessionWhy`), `gate-sessions`
  (each gate arm against control over its own window from the ledger's breaks, not `ERAS`) and
  `selector-sessions` (departures and the model's procedure-v2 intervals). The module's JSON is
  passed through in its own snake_case inside a `BridgedResult`; a failed read is an error, never
  an empty table. The verbs open ledgers read-only.
