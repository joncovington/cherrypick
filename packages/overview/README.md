# cherrypick-overview

The suite's pre-open **morning market overview**: one deterministic fact pack per session
(`~/.cherrypick/data/overview/morning-<date>.json`), a mechanical markdown render, and — outside
this package, behind the suite's AI fence — an agent-written morning note beside them.

Every reading comes from data the suite already produces: the shared stream cache (index and vol
levels, sector board, USO/GLD commodity proxies) and the GEX engine's own gamma flip and walls.
A mechanical GREEN/YELLOW/RED phase is computed from five declared gates; missing data can never
produce RED and always blocks GREEN. Pre-open values are labeled with their provenance — a prior
session's confirmed close is never passed off as a live quote.

Beside the phase, and deliberately separate from it, the pack records a 0–100 **deployment score**
blended from five macro signals (VIX percentile, vol term structure, sector breadth, an HYG/TLT
credit proxy, VIX rate of change). It is record-only: it gates nothing and sizes nothing, and exists
so the number can be measured against outcomes before anyone acts on it.

`python -m cherrypick.overview score-history` recomputes that score across stored history and
reports what its zones would have separated — no look-ahead, and reported as an SPX benchmark
rather than as suite P&L, since no trade was taken on any of those sessions.

See `CLAUDE.md` for the operating contract, `python -m cherrypick.overview --help` for the CLI,
and the console's Reports page (Morning tab) for the rendered surface.
