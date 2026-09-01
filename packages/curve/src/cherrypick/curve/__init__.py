"""cherrypick-curve — the VIX term-structure roll-yield paper module.

Harvests VXX's persistent contango decay with defined-risk call credit spreads (short call
~30-delta, long wing at a declared width), gated by a daily VIX/VIX3M regime read. Paper-only,
credential-free, a pure stream-cache consumer in the calendars/pmcc posture — no broker, no
keyring, no live path. Three books isolate one variable each: `control` (the regime-gated harvest,
with a regime-flip hard exit), `noflip` (control's entry, minus the flip rule), `hook` (only the
rare two-day-confirmed deep-backwardation entry, control's exit rules). The daily regime series
(ratio, classification, hook flag) is written every session, traded or not — the module's second
product, for `overview` and `advisor` to consume later.
"""
