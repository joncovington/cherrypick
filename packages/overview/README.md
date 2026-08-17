# cherrypick-overview

The suite's pre-open **morning market overview**: one deterministic fact pack per session
(`~/.cherrypick/data/overview/morning-<date>.json`), a mechanical markdown render, and — outside
this package, behind the suite's AI fence — an agent-written morning note beside them.

Every reading comes from data the suite already produces: the shared stream cache (index and vol
levels, sector board, USO/GLD commodity proxies) and the GEX engine's own gamma flip and walls.
A mechanical GREEN/YELLOW/RED phase is computed from five declared gates; missing data can never
produce RED and always blocks GREEN. Pre-open values are labeled with their provenance — a prior
session's confirmed close is never passed off as a live quote.

See `CLAUDE.md` for the operating contract, `python -m cherrypick.overview --help` for the CLI,
and the console's Morning page for the rendered surface.
