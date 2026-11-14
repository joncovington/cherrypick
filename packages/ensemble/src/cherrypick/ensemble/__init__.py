"""cherrypick-ensemble -- a rules engine for short-dated option arms.

An arm is configuration (`arms.ARMS`): the underlying, the expiry rule, the legs and how each strike is
placed, the entry trigger, the weekdays, the event skips, the filter gates, the exits and the size.
`engine.decide` is the one deterministic decision function; the backtest driver
(`packages/backtest/scripts/ensemble_item75.py`) and the paper loop call the same one on a `Snapshot`
built by `snapshot.build`. Paper-only, credential-free, no live path. EXPERIMENTAL.
"""
