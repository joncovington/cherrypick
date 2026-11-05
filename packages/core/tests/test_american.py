"""core.american: the bivariate normal against identities that hold exactly, and the Bjerksund-Stensland
2002 value against an independent Cox-Ross-Rubinstein tree with early exercise, plus the bounds every
American value must keep (never under intrinsic, never under the European, equal to the European call
when there is no dividend). The tree is written here, not imported, so the test is not the code
checking itself."""

import math

import pytest

from cherrypick.core import american, bs


def tree(spot, strike, years, vol, right, rate, div, steps=2000):
    """CRR binomial value with early exercise at every node."""
    dt = years / steps
    u = math.exp(vol * math.sqrt(dt))
    d = 1.0 / u
    p = (math.exp((rate - div) * dt) - d) / (u - d)
    disc = math.exp(-rate * dt)
    sign = 1.0 if right == "C" else -1.0
    values = [max(0.0, sign * (spot * u**j * d ** (steps - j) - strike)) for j in range(steps + 1)]
    for i in range(steps - 1, -1, -1):
        for j in range(i + 1):
            cont = disc * (p * values[j + 1] + (1.0 - p) * values[j])
            values[j] = max(cont, sign * (spot * u**j * d ** (i - j) - strike))
    return values[0]


@pytest.mark.parametrize("rho", [-0.99, -0.95, -0.8, -0.5, -0.2, 0.0, 0.2, 0.5, 0.8, 0.95, 0.99])
def test_the_bivariate_normal_at_the_origin_and_with_no_correlation(rho):
    # P(X<=0, Y<=0) = 1/4 + asin(rho) / (2 pi), exactly.
    assert american.bvn_cdf(0.0, 0.0, rho) == pytest.approx(0.25 + math.asin(rho) / (2 * math.pi), abs=1e-7)


@pytest.mark.parametrize("x, y", [(-1.0, 0.5), (0.3, 1.7), (2.0, -0.4)])
def test_the_bivariate_normal_factors_with_no_correlation_and_is_symmetric(x, y):
    nx, ny = bs._n(x), bs._n(y)
    assert american.bvn_cdf(x, y, 0.0) == pytest.approx(nx * ny, abs=1e-9)
    for rho in (-0.9, -0.3, 0.6, 0.97):
        assert american.bvn_cdf(x, y, rho) == pytest.approx(american.bvn_cdf(y, x, rho), abs=1e-9)
        # Inclusion-exclusion: P(X<=x, Y<=y) + P(X<=x, Y>y) = P(X<=x), and P(X<=x, Y>y) = P(X<=x, -Y<-y).
        assert american.bvn_cdf(x, y, rho) + american.bvn_cdf(x, -y, -rho) == pytest.approx(nx, abs=1e-7)


CASES = [
    # spot, strike, years, vol, right, rate, div
    (100, 100, 0.5, 0.30, "P", 0.05, 0.00),  # an at-the-money put: early exercise worth something
    (90, 100, 1.0, 0.25, "P", 0.06, 0.00),  # in the money
    (110, 100, 0.25, 0.40, "P", 0.04, 0.02),
    (100, 95, 0.5, 0.30, "C", 0.04, 0.06),  # a call on a name yielding more than the rate
    (42, 40, 0.75, 0.35, "C", 0.04, 0.08),
    (50, 56, 0.12, 0.29, "P", 0.04, 0.00),  # a short-dated wheel put through its strike
]


@pytest.mark.parametrize("case", CASES)
def test_the_value_is_close_to_a_fine_binomial_tree(case):
    s, k, t, v, right, r, q = case
    got = american.price(s, k, t, v, right, r, q)
    want = tree(s, k, t, v, right, r, q)
    assert got == pytest.approx(want, rel=0.01, abs=0.02)


@pytest.mark.parametrize("case", CASES)
def test_never_under_intrinsic_or_the_european_value(case):
    s, k, t, v, right, r, q = case
    got = american.price(s, k, t, v, right, r, q)
    assert got >= bs.intrinsic(s, k, right) - 1e-9
    assert got >= bs.price(s, k, t, v, right, r, q) - 1e-9


def test_a_call_with_no_dividend_is_the_european_call_exactly():
    assert american.price(100, 95, 0.5, 0.3, "C", 0.04, 0.0) == bs.price(100, 95, 0.5, 0.3, "C", 0.04, 0.0)


def test_a_deep_put_is_worth_more_than_its_european_value():
    # Interest on the strike makes early exercise worth something: the European undervalues it.
    assert (
        american.price(60, 100, 1.0, 0.2, "P", 0.06, 0.0) > bs.price(60, 100, 1.0, 0.2, "P", 0.06, 0.0) + 0.5
    )


@pytest.mark.parametrize("vol", [0.0001, 0.001, 0.01, 0.03, 0.08])
@pytest.mark.parametrize(
    "spot, strike, years", [(5100, 5000, 0.12), (66.6, 70, 0.04), (50, 56, 0.5), (900, 400, 2.0)]
)
def test_a_low_vol_never_overflows_and_keeps_the_bounds(vol, spot, strike, years):
    # The implied-vol search probes vols this low; beta runs past a hundred there.
    for right in ("P", "C"):
        got = american.price(spot, strike, years, vol, right, 0.04, 0.03)
        assert math.isfinite(got)
        assert got >= bs.intrinsic(spot, strike, right) - 1e-9
        assert got >= bs.price(spot, strike, years, vol, right, 0.04, 0.03) - 1e-9


def test_no_time_or_no_vol_is_intrinsic():
    assert american.price(90, 100, 0.0, 0.3, "P", 0.05) == 10.0
    assert american.price(90, 100, 0.5, 0.0, "C", 0.05) == 0.0


def test_the_implied_vol_reprices_the_mid_and_says_when_none_can():
    s, k, t, r, q = 100, 105, 0.4, 0.04, 0.01
    mid = american.price(s, k, t, 0.27, "P", r, q)
    iv = american.implied_vol(s, k, t, mid, "P", r, q)
    assert iv == pytest.approx(0.27, abs=1e-4)
    assert american.price(s, k, t, iv, "P", r, q) == pytest.approx(mid, abs=1e-6)
    assert american.implied_vol(s, k, t, 4.99, "P", r, q) == 0.0001  # under intrinsic: the floor
    assert american.implied_vol(s, k, t, 500.0, "P", r, q) is None
    assert american.implied_vol(s, k, 0.0, 1.0, "P", r, q) is None
