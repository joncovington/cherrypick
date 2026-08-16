"""cherrypick-pmcc — the PMCC-99 paper module.

Deep-ITM covered calls on leveraged ETFs (TNA, TQQQ, UPRO): buy the deepest ~99-delta call at ~21
DTE as a near-zero-extrinsic stock substitute, sell an ITM call at ~9 DTE whose intrinsic is the
downside buffer and whose time value is the entire profit. Paper-only, credential-free, a pure
consumer of the suite's shared stream cache. Ledger schema: `pmcc_99`.
"""
