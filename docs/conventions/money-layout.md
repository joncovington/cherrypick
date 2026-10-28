# Trade histories and reports: one money layout

Moved from the root CLAUDE.md.

Every surface that shows a trade — console table, review fact set, report, notification — states money
the same way, so modules can be compared. Flies is the reference implementation;
`packages/console/CLAUDE.md` holds the console specifics.

- **"P&L" means net, always.** net = gross − fees − settlement (− slippage, where charged as a cost). A
  gross figure is labelled gross.
- **Signed cash flow.** A credit received is `+`, a debit paid is `−`, on every entry and exit.
- **Whole-position dollars** (price × multiplier × quantity). The one per-share figure is the net
  price, and it says so (`1.25 cr` / `0.40 db`).
- **Rows add up.** entry + exit = gross; gross − every cost column = net. A surface derives what a
  ledger lacks so the identities hold, and never shows two figures that cannot be reconciled.
- **Costs are separate.** Trading fees (commissions, exchange) and settlement (exercise, assignment,
  delivery) are different columns. Where a fee total includes settlement, it is subtracted once; where
  the split was never recorded, the total stays in fees and settlement reads `n/r` — never a zero.
- **Slippage is its own column and says which model it is.** Where the modelled fill already concedes
  it (flies, meic) it is inside gross — shown as a measure, never subtracted again. Where fills are at
  mid and slippage is charged as a cost (bwb, calendars, pmcc, curve, earnings) it is its own cost
  column, subtracted once, out of fees. A figure subtracted in one module and not another is never
  shown under one unqualified name.
- **Open and closed are different tables.** Open carries the entry side (and a mark, where recorded);
  history carries the exit, how it ended (`closed` / `settled` / `expired` / `assigned`), gross, costs
  and net. Cancelled and voided rows are in neither.
- **Fee drag** is fees ÷ premium collected, named that way.

This is a presentation rule. Recording a missing cost component in a ledger is not
measurement-affecting — it changes what a row *says*, not what the module did — so it lands
immediately, with a dry-run-by-default backfill where history can be recovered.
