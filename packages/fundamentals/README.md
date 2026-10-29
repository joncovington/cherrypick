# cherrypick-fundamentals

Company fundamentals for the suite's fair-value model: each company's own SEC filings, Dolt's annual
statements and revenue consensus, and Aswath Damodaran's public cost-of-capital data, landed into one
store and turned into a daily fair-value file (`python -m cherrypick.fundamentals valuation`). The
Screeners page's fair-value tab and the technicals income lists read it.

The value is a model estimate, never a price, and was tested 2018-26 with no edge as a way to pick
names. See [CLAUDE.md](CLAUDE.md) for the package's rules and
[docs/fair-value-model-plan.md](../../docs/fair-value-model-plan.md) for the method.
