"""An American option's value: Bjerksund and Stensland (2002), with a continuous dividend yield.

The holder of an American option may exercise early: a deep put when the interest on the strike beats
what is left of its time value, a call just before a dividend. Black-Scholes (`core.bs`) prices the
European option and so understates both. Bjerksund-Stensland 2002 is a closed-form approximation of
the early-exercise premium, two flat exercise boundaries over two time steps, close to a fine binomial
tree (a few cents on a dollars-wide option) and fast enough to draw a payoff curve with.

`div` is the continuous dividend yield; the cost of carry is `rate - div`. A call on a name paying no
dividend is never worth exercising early, and its value is the European one, exactly. A put is valued
through the put-call transformation P(S, K, T, r, b) = C(K, S, T, r - b, -b). An index option settled
in cash is European: price it with `core.bs`, not here.

`bvn_cdf` is the bivariate normal distribution the two-step boundary needs, after Genz (2004).

Every number here is a model. It bounds or compares; it is not a price anyone can trade at.
"""

from __future__ import annotations

import math

from cherrypick.core import bs

# Gauss-Legendre half sets for Genz's bivariate normal: 6, 12 and 20 points.
_W = (
    (0.1713244923791705, 0.3607615730481384, 0.4679139345726904),
    (
        0.04717533638651177,
        0.1069393259953183,
        0.1600783285433464,
        0.2031674267230659,
        0.2334925365383547,
        0.2491470458134029,
    ),
    (
        0.01761400713915212,
        0.04060142980038694,
        0.06267204833410906,
        0.08327674157670475,
        0.1019301198172404,
        0.1181945319615184,
        0.1316886384491766,
        0.1420961093183821,
        0.1491729864726037,
        0.1527533871307259,
    ),
)
_X = (
    (-0.9324695142031522, -0.6612093864662647, -0.2386191860831970),
    (
        -0.9815606342467191,
        -0.9041172563704750,
        -0.7699026741943050,
        -0.5873179542866171,
        -0.3678314989981802,
        -0.1252334085114692,
    ),
    (
        -0.9931285991850949,
        -0.9639719272779138,
        -0.9122344282513259,
        -0.8391169718222188,
        -0.7463319064601508,
        -0.6360536807265150,
        -0.5108670019508271,
        -0.3737060887154196,
        -0.2277858511416451,
        -0.07652652113349733,
    ),
)


def _n(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bvn_upper(h: float, k: float, r: float) -> float:
    """P(X > h, Y > k) for standard normals with correlation r (Genz's bvnu)."""
    ng = 0 if abs(r) < 0.3 else 1 if abs(r) < 0.75 else 2
    w, x = _W[ng], _X[ng]
    hk = h * k
    if abs(r) < 0.925:
        hs = (h * h + k * k) / 2.0
        asr = math.asin(r) / 2.0
        total = 0.0
        for wi, xi in zip(w, x, strict=True):
            for sign in (-1.0, 1.0):
                sn = math.sin(asr * (1.0 + sign * xi))
                total += wi * math.exp((sn * hk - hs) / (1.0 - sn * sn))
        return total * asr / (2.0 * math.pi) + _n(-h) * _n(-k)
    if r < 0:
        k, hk = -k, -hk
    bvn = 0.0
    if abs(r) < 1.0:
        # As Genz's TVPACK (Fortran BVU) writes it.
        a_s = (1.0 - r) * (1.0 + r)
        a = math.sqrt(a_s)
        b_s = (h - k) ** 2
        c = (4.0 - hk) / 8.0
        d = (12.0 - hk) / 16.0
        bvn = (
            a
            * math.exp(-(b_s / a_s + hk) / 2.0)
            * (1.0 - c * (b_s - a_s) * (1.0 - d * b_s / 5.0) / 3.0 + c * d * a_s * a_s / 5.0)
        )
        if hk > -160.0:
            b = math.sqrt(b_s)
            bvn -= (
                math.exp(-hk / 2.0)
                * math.sqrt(2.0 * math.pi)
                * _n(-b / a)
                * b
                * (1.0 - c * b_s * (1.0 - d * b_s / 5.0) / 3.0)
            )
        a /= 2.0
        for wi, xi in zip(w, x, strict=True):
            xs = (a * (xi + 1.0)) ** 2
            rs = math.sqrt(1.0 - xs)
            bvn += (
                a
                * wi
                * (
                    math.exp(-b_s / (2.0 * xs) - hk / (1.0 + rs)) / rs
                    - math.exp(-(b_s / xs + hk) / 2.0) * (1.0 + c * xs * (1.0 + d * xs))
                )
            )
            xs = a_s * (1.0 - xi) ** 2 / 4.0
            rs = math.sqrt(1.0 - xs)
            bvn += (
                a
                * wi
                * math.exp(-(b_s / xs + hk) / 2.0)
                * (math.exp(-hk * (1.0 - rs) / (2.0 * (1.0 + rs))) / rs - (1.0 + c * xs * (1.0 + d * xs)))
            )
        bvn = -bvn / (2.0 * math.pi)
    if r > 0:
        return bvn + _n(-max(h, k))
    if h >= k:
        return -bvn
    lower = _n(k) - _n(h) if h < 0 else _n(-h) - _n(-k)
    return lower - bvn


def bvn_cdf(x: float, y: float, rho: float) -> float:
    """P(X <= x, Y <= y) for standard normals with correlation `rho`."""
    return min(1.0, max(0.0, _bvn_upper(-x, -y, rho)))


def _scaled(log_scale: float, prob: float) -> float:
    """exp(log_scale) x prob, without overflow when a huge scale meets a vanishing probability."""
    if prob <= 0.0:
        return 0.0
    x = log_scale + math.log(prob)
    return math.exp(x) if x < 700.0 else math.inf


# `base` scales s**gamma as (s / base)**gamma: the caller's coefficient carries base**-gamma, so the
# powers are taken as ratios in logs. At a low vol gamma (beta) runs past a hundred, and s**beta alone
# overflows long before the product it belongs to does.


def _phi(
    s: float, t: float, gamma: float, h: float, i: float, r: float, b: float, v: float, base: float = 1.0
) -> float:
    lam = (-r + gamma * b + 0.5 * gamma * (gamma - 1.0) * v * v) * t
    d = -(math.log(s / h) + (b + (gamma - 0.5) * v * v) * t) / (v * math.sqrt(t))
    kappa = 2.0 * b / (v * v) + 2.0 * gamma - 1.0
    log0 = lam + gamma * math.log(s / base)
    return _scaled(log0, _n(d)) - _scaled(
        log0 + kappa * math.log(i / s), _n(d - 2.0 * math.log(i / s) / (v * math.sqrt(t)))
    )


def _psi(
    s: float,
    t2: float,
    gamma: float,
    h: float,
    i2: float,
    i1: float,
    t1: float,
    r: float,
    b: float,
    v: float,
    base: float = 1.0,
) -> float:
    drift = b + (gamma - 0.5) * v * v
    sq1, sq2 = v * math.sqrt(t1), v * math.sqrt(t2)
    e1 = (math.log(s / i1) + drift * t1) / sq1
    e2 = (math.log(i2 * i2 / (s * i1)) + drift * t1) / sq1
    e3 = (math.log(s / i1) - drift * t1) / sq1
    e4 = (math.log(i2 * i2 / (s * i1)) - drift * t1) / sq1
    f1 = (math.log(s / h) + drift * t2) / sq2
    f2 = (math.log(i2 * i2 / (s * h)) + drift * t2) / sq2
    f3 = (math.log(i1 * i1 / (s * h)) + drift * t2) / sq2
    f4 = (math.log(s * i1 * i1 / (h * i2 * i2)) + drift * t2) / sq2
    rho = math.sqrt(t1 / t2)
    lam = -r + gamma * b + 0.5 * gamma * (gamma - 1.0) * v * v
    kappa = 2.0 * b / (v * v) + 2.0 * gamma - 1.0
    log0 = lam * t2 + gamma * math.log(s / base)
    return (
        _scaled(log0, bvn_cdf(-e1, -f1, rho))
        - _scaled(log0 + kappa * math.log(i2 / s), bvn_cdf(-e2, -f2, rho))
        - _scaled(log0 + kappa * math.log(i1 / s), bvn_cdf(-e3, -f3, -rho))
        + _scaled(log0 + kappa * math.log(i1 / i2), bvn_cdf(-e4, -f4, -rho))
    )


def _european_call(s: float, k: float, t: float, r: float, b: float, v: float) -> float:
    return bs.price(s, k, t, v, "C", r, r - b)


def _call(s: float, k: float, t: float, r: float, b: float, v: float) -> float:
    """Bjerksund-Stensland 2002 American call, cost of carry `b`. At a vol so low the boundary's
    exponentials leave floating point (the implied-vol search's lowest probes), the value is the
    larger of exercising now and the European option: what the boundary converges to there."""
    european = _european_call(s, k, t, r, b, v)
    if b >= r:  # never worth exercising early
        return european
    try:
        value = _call_2002(s, k, t, r, b, v)
    except (OverflowError, ZeroDivisionError, ValueError):
        value = 0.0
    # Never below exercising now, nor the European option, nor nothing: an underflowed boundary can
    # otherwise read as "exercise now" out of the money.
    return max(value, s - k, european, 0.0)


def _call_2002(s: float, k: float, t: float, r: float, b: float, v: float) -> float:
    v2 = v * v
    t1 = 0.5 * (math.sqrt(5.0) - 1.0) * t
    beta = (0.5 - b / v2) + math.sqrt((b / v2 - 0.5) ** 2 + 2.0 * r / v2)
    b_inf = beta / (beta - 1.0) * k
    b0 = max(k, r / (r - b) * k)
    h1 = -(b * t1 + 2.0 * v * math.sqrt(t1)) * k * k / ((b_inf - b0) * b0)
    h2 = -(b * t + 2.0 * v * math.sqrt(t)) * k * k / ((b_inf - b0) * b0)
    i1 = b0 + (b_inf - b0) * (1.0 - math.exp(h1))
    i2 = b0 + (b_inf - b0) * (1.0 - math.exp(h2))
    if s >= i2:
        return s - k
    # alpha_j = (I_j - K) I_j**-beta; each beta term takes I_j as its base, so only ratios are raised.
    c1, c2 = i1 - k, i2 - k
    value = (
        c2 * (s / i2) ** beta
        - c2 * _phi(s, t1, beta, i2, i2, r, b, v, base=i2)
        + _phi(s, t1, 1.0, i2, i2, r, b, v)
        - _phi(s, t1, 1.0, i1, i2, r, b, v)
        - k * _phi(s, t1, 0.0, i2, i2, r, b, v)
        + k * _phi(s, t1, 0.0, i1, i2, r, b, v)
        + c1 * _phi(s, t1, beta, i1, i2, r, b, v, base=i1)
        - c1 * _psi(s, t, beta, i1, i2, i1, t1, r, b, v, base=i1)
        + _psi(s, t, 1.0, i1, i2, i1, t1, r, b, v)
        - _psi(s, t, 1.0, k, i2, i1, t1, r, b, v)
        - k * _psi(s, t, 0.0, i1, i2, i1, t1, r, b, v)
        + k * _psi(s, t, 0.0, k, i2, i1, t1, r, b, v)
    )
    # Never below exercising now, nor below the European option.
    return max(value, s - k, _european_call(s, k, t, r, b, v))


def price(
    spot: float, strike: float, years: float, vol: float, right: str, rate: float, div: float = 0.0
) -> float:
    """An American option's model value; intrinsic with no time or no vol."""
    if years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return bs.intrinsic(spot, strike, right)
    b = rate - div
    if right == "C":
        return _call(spot, strike, years, rate, b, vol)
    # Put-call transformation: P(S, K, T, r, b) = C(K, S, T, r - b, -b).
    return max(_call(strike, spot, years, rate - b, -b, vol), strike - spot)


def implied_vol(
    spot: float, strike: float, years: float, mid: float, right: str, rate: float, div: float = 0.0
) -> float | None:
    """The vol at which the American value is `mid`, by bisection; None when no vol prices it (no
    time left, or a mid above the value at 500% vol). A mid at or under intrinsic is the floor."""
    if years <= 0 or mid is None or spot <= 0:
        return None
    if mid <= bs.intrinsic(spot, strike, right) + 1e-9:
        return 0.0001
    lo, hi = 0.0001, 5.0
    if price(spot, strike, years, hi, right, rate, div) < mid:
        return None
    for _ in range(80):
        m = (lo + hi) / 2.0
        if price(spot, strike, years, m, right, rate, div) > mid:
            hi = m
        else:
            lo = m
    return (lo + hi) / 2.0
