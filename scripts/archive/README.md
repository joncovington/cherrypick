# Archived one-shot scripts

Migrations and backfills that have done their job on the machine this suite runs on. They are kept,
still runnable, for an install that has not been through them yet, and as the record of what each
one changed. Each is dry-run by default and refuses a second application.

| Script | What it did | Evidence it ran here |
|---|---|---|
| `retag_advised_books.py` | Moved advised rows onto their per-experiment books | ran 2026-09-17 (`packages/core/tests/test_arm_column_names.py`) |
| `bwb_backfill_settlement_fees.py` | `settlement_fees` on closed bwb rows | `paper_trades.db.bak-pre-settlement-fees-20260925-002546` |
| `meic_backfill_settlement_fees.py` | the same for meic | `meic_trades.db.bak-pre-settlement-fees-20260925-000954` |
| `positions_backfill_settlement_fees.py` | the same for calendars, pmcc and curve | `.bak-pre-settlement-fees-20260925-061132` beside each ledger |
| `earnings_backfill_settlement_fees.py` | the same for earnings paper | every closed earnings paper row carries `settlement_fees` |
| `earnings_resettle_expiry_fees.py` | re-costed earnings' expiry exits | `paper_trades.db.expiry-fee-resettled.json` (2026-09-25) |
| `meic_resettle_expiry_fees.py` | re-costed meic's expiry exits | `paper_trades.db.expiry-fee-resettled.json` (2026-09-24) |

Not archived: `arm_column_migrate.py` and `arm_column_preflight.py`. The ledgers' pre-rename
refusal (`core.db.refuse_pre_rename`, meic's `db.py`) tells an older install to run them, so they
stay where that message points. The flies scripts belong to the flies module and are left to it.
