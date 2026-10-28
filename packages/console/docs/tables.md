# Tables

Moved from the console CLAUDE.md. Read before changing a trade or history table.

## The trade table standard

The root money layout applied here. Flies is the reference: `readers/flies.ts` `tradeCash`/`bookCash`
derive the columns, `lib/format.ts` `fmtCash` (`+$178.75`) and `fmtPrice` (`1.25 cr`) render them.
Header order: identity and structure, qty, price, entry, exit, how it ended, gross, fees, settle, slip,
net — each money header's `title` stating its definition.

- Money is derived on the server, never in a component, so every surface of a module agrees.
- A recently added cost column is read as optional (`NULL` on an older ledger), so a missing column
  reads "not recorded" rather than `withReadOnlyDb` turning it into an empty table.
- A totals chip carries gross, fees, settlement and net over every matching row, and says how many
  rows a partially recorded measure (slippage) covers. A settlement total over any row that never
  recorded it is n/r, never a partial sum.
- Fee drag is one helper, `@console/shared`'s `feeDragPct` (trading fees, settlement out, ÷ premium
  collected; null when nothing was collected), on every surface that shows it.
- Each module on the standard has its own trade-standard test against its own fixture, shown to fail
  (the flies one by removing the subtraction and the filter):

| Module | Derivation | Test | Notes |
|---|---|---|---|
| flies | `readers/flies.ts` `tradeCash`/`bookCash` | `flies-trade-standard` | slippage inside gross |
| meic | `readers/meic.ts` `meicTradeCash`, `pages/Meic/MeicTables.tsx` | `meic-trade-standard` | entry summed from the two side credits, rounded per side as the write path does, when they sum to `net_credit` (within half a cent); otherwise `net_credit`, the fill the gross was booked on. A row with fees never recorded has net n/r, and totals over it read fees and net n/r with the recorded share |
| bwb | `readers/bwb.ts` `bwbTradeCash` | `bwb-trade-standard` | slippage charged inside `fees`; each part subtracted once; a broker-reconciled row takes none out |
| earnings | `readers/earnings.ts` `earningsTradeCash` | `earnings-trade-standard` | `entry_cost`/`exit_cost` carry slippage and (exit) settlement fee; history is closed trades across both books |
| calendars, pmcc, curve | `readers/positionCash.ts`, `components/TradeMoney.tsx` | `position-trade-standard` (via curve), `pmcc-`/`calendars-trade-standard` on fixtures from each module's `db.connect` | exit kinds add `assigned`; calendars' history is per week and arm, net null until every position (shares included) has closed, totals over finished weeks only |
| contango | `readers/contango.ts` `contangoTradeCash` | `contango-trade-standard`, on a fixture from its `db.connect` | a stint is a fund bought and sold: entry (the purchase) + exit (the sale) + distributions = gross; slippage charged as a cost; settlement never recorded, so `n/r` |

## History-table controls

A history table declares its columns once (`components/table/columns.ts`, `ColumnDef`: header,
definition tooltip, cell, kind); the controls come from that list.

- **Columns menu** (`ColumnsMenu`): a dropdown panel, not a dialog. `describe` columns can be hidden
  and reordered; `money` columns can be hidden but stay one block in the standard's order at the end,
  and `net` (`pinned`) is always shown and always last. `resolveColumns` enforces that against any
  stored layout, drops unknown ids and shows newly declared columns (`web/test/tableColumns.test.ts`).
- **Layout storage:** the prefs store under `columns:<table>` — per viewer, follows to the desktop
  shell, never in the URL.
- **Date range** (`DateRangeBar`, `useUrlDateRange`, `DatePicker.tsx`): presets plus a two-click
  picker (Monday-first, nothing after today); `?from=&to=` written with `replace`; presets end today in
  New York time. **The server applies it** (`readers/dateRange.ts`: one parser, one clause builder), so
  counts and totals describe the range. Each module bounds the date its history is ABOUT, and the bar
  names it: trade date for flies and meic, close date for bwb/pmcc/curve/earnings/contango (an open position is
  outside any range), week for calendars.
- **`HistoryTable`** (`components/table/HistoryTable.tsx`) is the card for every module but flies.
  Money and `numeric` columns right-align per column (`td.num`), since a reorderable table cannot align
  by position. `tradeMoneyColumns` holds calendars/pmcc/curve's shared columns.
- **Deliberate differences:** flies' range sits at the top of its History tab and bounds the summaries
  too, so a narrowed tab never sets one table beside cards answering for the era. MEIC's history is one
  session by default; a range replaces the header's day, and the no-range button reads `session`.
  Earnings' range bounds its history and totals only.

## Select controls: pill or box

A select that **scopes what a header or page shows** (session, arm, symbol, era, scope, the Compare
filters) wears the pill, `chip review-session-select`, as `ScopeSelect`/`EraSelect` do, so a header
row never mixes a pill with a box. A select that **sets an option inside a card** (a chart's width or
range, the pager's page size, a Config field) stays `text-input`. A scope select narrowed off its
default adds `scope-select-off-default`, which reads amber on either class. The pill is capped at
16rem: a select is as wide as its longest option, and uncapped, MEIC's arm list in the number face
pushed its header onto a third line and off a phone's edge (2026-10-18).

## Sorting a table

Sort where ranking is a useful read: money, counts, rates, dates, symbol, arm, prices, sizes. A click
cycles largest first, smallest first, the table's own order; nulls sink either way; the header is a
button (`.th-sort`). **Where the rows live decides how** (2026-10-16):

- **A server-paged table sorts on the server, never in the browser.** Sorting one fetched page would
  rank 100 rows and call it the history. `usePage().toggleSort` sends `sort`/`dir` with the page;
  the reader passes its `SortAccessors` to `pagedQuery`/`pageArray`, which order the WHOLE result
  before slicing. Each accessor reads the MAPPED row, the figure the cell shows, so a net derived in
  the reader sorts as that net; an id the reader does not name keeps the default order. The web
  table's `serverSorts` list is held to the reader's by a test (`history-sort*.test.ts`).
- **A `HistoryTable` holding every row** sorts in the browser from each `ColumnDef`'s `sortValue`,
  and only when it passes `clientSort`. With neither mode a `HistoryTable` sorts nothing, so shared
  defs (MEIC's, paged and live) cannot switch on a client sort of a server page by accident.
- **Hand-built and `DataCard` tables** use `useSort` / `sortRows` / `SortTh` (`DataCard` takes
  `{label, k}` headers and `sort`). A client-sliced table ("show more") sorts before the slice.

Leave the order alone where it is the meaning: time series and journals, pivots and heatmaps,
key/value lists, fixed category orders, legs of one position, grouped rows, a `#` that is the row's
position, top-N previews of a server ranking (Flow's today cards), and capture layouts.
