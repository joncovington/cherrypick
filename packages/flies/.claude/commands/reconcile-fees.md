Manually run (or check the status of) the flies LIVE ledger's broker fee reconciliation.

This runs automatically every trading morning as part of `live_loop.py --once --live` (before the
settlement gate) — this command is for manual/ad-hoc use: confirming yesterday's numbers on demand,
or catching up after several missed sessions.

1. Run the reconciliation (this is a single command — it checks what's pending locally, then, only
if anything is, fetches real broker transactions and reconciles it; GET-only, no order is placed,
cancelled, or modified):
python -m cherrypick.flies.fee_reconcile --symbol XSP --lookback-days 10

If `pending` comes back empty, every settled session in that window was already reconciled —
nothing more to do.

2. Otherwise, review the `results` array in the output:
`reconciled` position ids had their `net`/`fees`/`gross_pnl`/`pnl` recomputed from real broker cash
flow (original modeled values preserved in `modeled_*` columns); `unmatched` ids couldn't be tied to
broker transactions confidently and were left untouched — investigate those manually (a missing
`entry_order_id`/`completion_order_id`, or an order placed under a different symbol/date than
expected).

3. To reconcile one specific session instead of everything pending:
python -m cherrypick.flies.fee_reconcile --symbol XSP --date YYYY-MM-DD

4. Any `variance` entry with `|delta| > $1.00` is logged as a WARN in `flies_live.log` (see
`live_loop.log_file()`) — worth a look even after auto-correction, since a large gap can also mean a
matching bug (wrong strikes derived, wrong order ids stored) rather than just the modeled
assignment-fee estimate being off.
