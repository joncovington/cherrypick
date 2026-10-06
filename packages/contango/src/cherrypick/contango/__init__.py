"""cherrypick-contango -- the VIX term-structure regime switch, held in shares.

Holds SVXY (-0.5x short VIX futures) while VIX/VIX3M says contango and a T-bill ETF (SHV) while it
does not; one decision per session, ten minutes before the close, off the shared stream cache.
Paper-only, credential-free, no live path. Arms differ only in the two thresholds of the switch:
`control` (in below 0.97, out at 0.97) and `flipexit` (in below 0.97, out only once the ratio
reaches 1.0, holding through the band between).

It is the scalable expression of the thesis curve trades in VXX options: curve's edge is real but
its costs are half the credit at ten contracts of capacity; a share switch costs a few basis points
a side at any size an account here could run.
"""
