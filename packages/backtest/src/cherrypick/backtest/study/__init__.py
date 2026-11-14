"""The study kit: the pieces study scripts share, as plain functions and small types (#1068).

A study script under `packages/backtest/scripts/` calls these; nothing here is a framework to
subclass. The kit holds data access, calendars, chain maths and (later) the declared checks, never a
trading rule: where a module exists, a study drives the module's own code.

The kit's constraints (#1068's design comment), in short:

- **Copied, never moved.** Each piece is copied from a script whose results are recorded, and a test
  pins it to that script's copy. Finished studies keep their own code, so no recorded result moves.
  A script moves onto the kit only when a logged correction touches it.
- **No defaults for anything a declaration fixes** (entry tenor and band, deltas, test bars): those
  are required arguments.
- **Costs come from the suite** (`core.fees`, core's slippage), never from here.
- **Deterministic**, and tested harder than any one study (property tests on synthetic chains).

Modules: `calendars` (monthly expiries, entry sessions) and `chains` (one expiry's end-of-day chain:
its parity forward, Black-76 deltas at r = 0, out-of-the-money strikes, nearest strike by delta).
"""
