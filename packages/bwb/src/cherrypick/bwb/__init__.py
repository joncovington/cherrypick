"""cherrypick-bwb — the SPX daily-laddered put broken-wing butterfly paper module.

Every session at a fixed tick (default 10:00 ET), one put broken-wing butterfly on SPX is entered
at the expected move for a net credit, ~7 DTE, held to expiry — a new one every session, so ~5-7
positions ride concurrently per book at steady state. Paper-only, credential-free, a pure
stream-cache consumer in the calendars/pmcc/curve posture. No live path.

Four books trade the IDENTICAL base structure; the only variable is whether/when a reversal-
triggered put credit spread add-on fires, turning the fly into a 1-3-2:

- `control` — never adds on, the BWB rides alone to expiry.
- `delta` — the near wing's |delta| touches `delta_trigger` (raw proximity).
- `bounce` — the near wing's peak |delta| since entry reached `delta_trigger` AND has since pulled
  back to `delta_trigger - bounce_pullback` (a confirmed reversal, not just a touch).
- `flip` — spot has traded below `gamma_flip` since entry AND reclaimed above it with a buffer.

SPX is cash-settled and European-style: no assignment machinery, no dividend calendar, no physical
settlement decomposition — the cleanest settlement model in the suite. Ledger schema: **`bwb_132`**.

See CLAUDE.md for the full plan, the honesty rules, and the trigger-tick substrate that makes a
read-side threshold replay (`replay.py`, a fast-follow) possible later.
"""
