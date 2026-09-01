"""cherrypick-pmcc — the PMCC-99 paper module.

Deep-ITM covered calls on TQQQ (American, physical-settlement ETF option) and XSP (European,
cash-settled Mini-SPX index option), both in the single `control` book: buy an 85-90-delta call at
~21 DTE as a stock substitute, sell the ATM call nearest spot at ~7 DTE, hold to the short's own
expiration, then close both legs together. Paper-only, credential-free, a pure consumer of the
suite's shared stream cache. Ledger schema: `pmcc_99`.
"""
