# cherrypick-overview

The suite's pre-open **morning market overview**: one deterministic fact pack per session
(`~/.cherrypick/data/overview/morning-<date>.json`), a mechanical markdown render, and — outside
this package, behind the suite's AI fence — an agent-written morning note beside them.

No paywalled data is involved. Most readings come from data the suite already produces: the shared
stream cache (index and vol levels, sector board, USO/GLD commodity proxies, from the broker feed)
and the GEX recorder's gamma flip and walls. Beside them sit free public files that
`scripts/fetch_market_files.py` fetches outside the package into `~/.cherrypick/data/market-files/`:
Cboe's index histories and delayed SPX chain, Treasury's yield curve and BEA's release calendar
(FRED's too, only once a FRED key is stored). The week's earnings, with implied moves, come from
`scripts/fetch_earnings_moves.py`, which reads the technicals store and so needs the `dolt`
capability; without it that section is empty and says why.

A mechanical GREEN/YELLOW/RED phase is computed from five declared gates; missing data can never
produce RED and always blocks GREEN. Pre-open values are labelled with their provenance — a prior
session's confirmed close is never passed off as a live quote.

Beside the phase, and deliberately separate from it, the pack records a 0–100 **deployment score**
blended from five macro signals (VIX percentile, vol term structure, sector breadth, an HYG/TLT
credit proxy, VIX rate of change). It is record-only: it gates nothing and sizes nothing, and exists
so the number can be measured against outcomes before anyone acts on it.

`python -m cherrypick.overview score-history` recomputes that score across stored history and
reports what its zones would have separated — no look-ahead, and reported as an SPX benchmark
rather than as suite P&L, since no trade was taken on any of those sessions.

The supervisor builds the pack at 08:30 ET on trading days (`morning-factpack`; times and switches
are in the `morning` block of `~/.cherrypick/config.json`). The note is written by
`scripts/morning_narrative.py` from the pack, the technicals report and public RSS headlines; it is
off by default (`morning.narrative`) and needs the `claude` capability.

See `CLAUDE.md` for the operating contract, `python -m cherrypick.overview --help` for the CLI,
and the console's Reports page (Morning tab) for the rendered surface.
