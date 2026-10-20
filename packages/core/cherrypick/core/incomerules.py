"""The income screens' strike rule: one place for the numbers the Screeners lists and manage share.

Decided 2026-10-19 from two history studies on the local Dolt chains (52 names, 2020-26, at the
working fill, net of costs):

- **Short put: 16 delta on the cycle monthly** (45-60 days out, else 30-60). Higher delta earned more
  only by carrying more market exposure; measured beyond the delta-equivalent stock position every
  delta was near zero, 16 the least bad, and the 30-day recipe worse at every delta.
- **Covered call: 16 delta on the monthly 30-45 days out**, else the cycle monthly. The overlay lost
  to holding the shares on most names, worse at higher delta and on 45-60 days.

Neither is an edge: both are the least-bad cells of studies that found none, and wait on the
confirmation run against full vendor chains. Readers say so wherever they show a list.

`packages/technicals` `incomelists.py` builds the lists from these; `packages/manage`'s ruleset
defaults to them (and keeps its own keys, so an account can differ).
"""

from __future__ import annotations

SHORT_PUT_DELTA = 0.16
COVERED_CALL_DELTA = 0.16
# How far the picked strike's delta may sit from its target and still be the rule's trade: the
# history replay (`scripts/premium_cycle_replay.py`) took no entry that missed by more, so a list row
# outside it would be a trade the study never measured (a $3 stock whose nearest quoted call is
# 0.43 delta, 2026-10-19).
DELTA_TOLERANCE = 0.05
# The rate the deltas are solved at: the screen rules' 4% (technicals `screeners.py`), manage's
# `pricing.rate`.
RATE = 0.04
