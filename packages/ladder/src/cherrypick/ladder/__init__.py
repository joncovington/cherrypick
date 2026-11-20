"""cherrypick-ladder -- the share dip ladder in paper: buy a lot 7% down, sell each lot at +4%.

Item 26's rule as the Discord group trades it, on leveraged index funds (TQQQ first): buy a lot when
the fund trades 7% under its previous close (or, for `chain`, 7% under the last lot bought), sell
each lot on its own at +4% of its cost, no stop. Item 27's `decay` target is a third arm.
A stream-cache consumer that places no order. Its live path is gates and a per-day arm only (no
order path yet). EXPERIMENTAL.
"""
