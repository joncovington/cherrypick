"""cherrypick-weeklyput -- item 57's unhedged arm in paper: the index 10-delta put, 7 days out.

On each weekly expiry that is not a standard monthly, sell the put nearest 10 delta (Black, on the
parity forward at zero rate) on SPX (SPXW), SPY, QQQ and IWM in the last minutes of the entry
session, seven calendar days before the expiry, and hold it to the expiry's posted close. No
hedge, no stop, no exit. Paper-only, credential-free, no live path: a stream-cache consumer.
EXPERIMENTAL.
"""
