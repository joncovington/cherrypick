"""The account as one book: beta-weighted delta, theta, the break-even move, and the bands around them.

A pure function of the trades the evaluation already built (each leg with its own solved IV) and the
snapshot's market metrics, so a past session re-evaluates to the same reading. The plan behind it is
docs/portfolio-guidance-plan.md.

Every figure is MODELLED -- Black-Scholes at each leg's own IV, dividends ignored -- and the broker's
display will differ a little (it models American exercise and dividends). What it is for:

- **Beta-weighted delta (BWD)**, in shares of the benchmark: sum of quantity x multiplier x delta x
  beta x spot / benchmark spot. The beta is the BROKER's, the one its own BWD uses, so the two can be
  set side by side.
- **Theta**, in dollars per calendar day (the broker's convention) and as a share of net liq. Stated
  twice: the whole book, and without the option legs `front_dte` days or less from expiry. The band,
  the concentration rule and the candidates read the second: a leg about to expire can carry nearly
  all of a day's theta and then none of it, which made the band swing from five times its ceiling to
  under its floor overnight (2026-10-20).
- **The break-even move.** Theta is paid for by gamma (theta = -1/2 gamma S^2 sigma^2), so a short-gamma
  book loses a day's theta on a benchmark move of sqrt(2 theta / |gamma|). Gamma is stated in benchmark
  terms -- each underlying's gamma times (beta x spot / benchmark spot)^2 -- and the move is set against
  the benchmark's implied one-day move, on the same calendar-day footing as theta.
- **A stress line**: each position repriced at its beta-scaled share of a benchmark shock, at its own
  IV with time held still.

A leg the model cannot price (unpriced, no IV, past expiry) is left out and NAMED; an underlying with
no beta has no BWD and is named. Neither is ever counted as zero. A leg expiring today is taken at its
moneyness (delta 1 or 0) with its remaining extrinsic as that day's theta.

The bands are retail heuristics with no published out-of-sample evidence, and their confidences say so.
Guidance fired here is REVIEW, naming what drives it: it never sizes, places or closes anything.
"""

from __future__ import annotations

import math
from typing import Any

from cherrypick.manage import pricing
from cherrypick.manage import rules as _rules

REVIEW = "REVIEW"
MULTIPLIER = pricing.MULTIPLIER


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _round(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


# --------------------------------------------------------------------------- per leg
def leg_greeks(leg: dict[str, Any], spot: float | None, rate: float) -> dict[str, Any] | None:
    """Position greeks for one leg (whole-position: quantity x multiplier), or None when the model
    cannot price it. `basis` says how: `model`, `expiring` (today, by moneyness) or `shares`."""
    qty = float(leg.get("quantity") or 0.0)
    right = leg.get("right")
    if right not in ("C", "P"):
        return {"delta": qty, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "vega_30": 0.0, "basis": "shares"}
    strike, dte = _f(leg.get("strike")), leg.get("dte")
    if not spot or strike is None or dte is None or dte < 0:
        return None
    size = qty * MULTIPLIER
    if dte == 0:
        if leg.get("mid") is None or leg.get("extrinsic") is None:
            return None
        itm = spot > strike if right == "C" else spot < strike
        sign = 1.0 if right == "C" else -1.0
        return {
            "delta": size * sign * (1.0 if itm else 0.0),
            "gamma": 0.0,
            "theta": -size * max(float(leg["extrinsic"]), 0.0),
            "vega": 0.0,
            "vega_30": 0.0,
            "basis": "expiring",
        }
    iv = leg.get("iv")
    g = pricing.greeks(spot, strike, float(leg["years"]), iv, right, rate) if iv else None
    if g is None:
        return None
    return {
        "delta": size * g["delta"],
        "gamma": size * g["gamma"],
        "theta": size * g["theta"],
        "vega": size * g["vega"],
        # Front-month IV moves more than back-month, so vega is weighted to a 30-day equivalent.
        "vega_30": size * g["vega"] * math.sqrt(30.0 / dte),
        "basis": "model",
    }


def _leg_value(
    leg: dict[str, Any], spot: float, rate: float, vol_shift: float = 0.0, years_ahead: float = 0.0
) -> float | None:
    """Model value of one leg at `spot`, its IV moved by `vol_shift` (absolute, 0.05 = five points)
    and `years_ahead` of time passed. A leg that has expired by then is worth its intrinsic value."""
    qty = float(leg.get("quantity") or 0.0)
    right = leg.get("right")
    if right not in ("C", "P"):
        return qty * spot
    strike, dte = _f(leg.get("strike")), leg.get("dte")
    if strike is None or dte is None or dte < 0:
        return None
    left = float(leg.get("years") or 0.0) - years_ahead
    if dte == 0 or left <= 0:
        return qty * MULTIPLIER * pricing.intrinsic(spot, strike, right)
    if not leg.get("iv"):
        return None
    vol = max(float(leg["iv"]) + vol_shift, MIN_VOL)
    return qty * MULTIPLIER * pricing.price(spot, strike, left, vol, right, rate)


# An IV shifted down past this is held here: a vol of zero prices every option at intrinsic, which
# is a statement about the market nobody made.
MIN_VOL = 0.01


def book_pnl(
    rows: list[dict[str, Any]],
    bench_spot: float | None,
    rate: float,
    move: float,
    vol_shift: float = 0.0,
    years_ahead: float = 0.0,
    term_weighted: bool = False,
) -> tuple[float, list[str]]:
    """The book's modelled P&L against today's model value when the benchmark moves by `move` (each
    name by beta x move), IVs shift by `vol_shift` and `years_ahead` pass; and the names left out
    for want of a beta. A leg the model cannot price is left out on both sides, never valued at zero.

    `term_weighted` scales each leg's shift by sqrt(30 / DTE), as the 30-day weighted vega does:
    front-month IV moves more than a LEAPS's, so a flat shift overstates what long-dated legs gain
    in a vol spike."""
    total, left_out = 0.0, []
    for r in rows:
        if r["beta"] is None or not r["spot"] or not bench_spot:
            if r["legs"]:
                left_out.append(r["underlying"])
            continue
        moved = r["spot"] * (1.0 + r["beta"] * float(move))
        for leg in r["legs"]:
            shift = vol_shift
            if term_weighted and vol_shift and (leg.get("dte") or 0) > 0:
                shift = vol_shift * math.sqrt(30.0 / leg["dte"])
            now = _leg_value(leg, r["spot"], rate)
            then = _leg_value(leg, moved, rate, shift, years_ahead)
            if now is not None and then is not None:
                total += then - now
    return total, left_out


# --------------------------------------------------------------------------- the book
def _beta_doubt(beta: float | None, corr: float | None, cfg: dict[str, Any]) -> str | None:
    """Why the broker's beta looks wrong beside its own correlation, or None.

    Over one window a regression beta and a correlation share a sign, so opposite signs mean the two
    come from different windows; a near-zero beta beside a strong correlation is the same doubt."""
    if beta is None or corr is None:
        return None
    min_corr, near_zero = float(cfg["min_abs_corr"]), float(cfg["near_zero_beta"])
    if abs(corr) >= min_corr and abs(beta) >= near_zero and (beta > 0) != (corr > 0):
        return f"beta {beta:+.2f} but three-month correlation {corr:+.2f}: opposite signs"
    if abs(beta) < near_zero and abs(corr) >= max(min_corr, 0.5):
        return f"beta {beta:+.2f} beside a three-month correlation of {corr:+.2f}"
    return None


def broker_check(
    trades: list[dict[str, Any]],
    broker: dict[str, dict] | None,
    metrics: dict[str, Any],
    bench_spot: float | None,
    rate: float,
) -> dict[str, Any]:
    """Beta-weighted delta and theta summed twice over the same legs: on our model and on the
    broker's own greeks (snapshot v9), with the same beta, spot and benchmark, so the gap is the two
    models' and nothing else. A leg only one side prices is left out of both and named, never
    counted on one side as zero. Shares are delta 1 on both sides. Nothing reads this to decide
    anything: it is the record the portfolio plan asks for before the bands are trusted.

    Legs expiring today are summed apart (`expiring`) and kept out of the totals and the names: ours
    takes them at moneyness, the broker's prices a whole day left at whatever IV the quote implies,
    and on 2026-10-21 (before the open, the morning after a report) one such pair was $374 of a
    $434 theta gap. `legs_on_broker_iv` counts the legs priced on the broker's IV (a wide quote,
    `evaluate._legs`): on those the gap is the two models' spot and formula, not their IVs."""
    if broker is None:
        return {"available": False, "reason": "snapshot carries no broker greeks (before v9)"}
    per: dict[str, dict[str, Any]] = {}
    expiring = {"delta": [0.0, 0.0], "theta": [0.0, 0.0], "legs": [], "scaled": True}
    compared, left_out, on_broker_iv = 0, [], 0
    for trade in trades:
        und = trade["underlying"]
        spot = _f(trade.get("spot"))
        beta = _f(((metrics or {}).get(und) or {}).get("beta"))
        row = per.setdefault(
            und,
            {"underlying": und, "scale": None, "delta": [0.0, 0.0], "theta": [0.0, 0.0], "legs": 0},
        )
        if beta is not None and spot and bench_spot:
            row["scale"] = beta * spot / bench_spot
        for leg in trade.get("legs") or []:
            ours = leg_greeks(leg, spot, rate) if leg.get("priced") else None
            if ours is not None and ours["basis"] == "shares":
                row["delta"][0] += ours["delta"]
                row["delta"][1] += ours["delta"]
                continue
            b = (broker or {}).get(str(leg.get("symbol") or ""))
            b_delta, b_theta = (_f(b.get("delta")), _f(b.get("theta"))) if b else (None, None)
            if ours is None or b_delta is None or b_theta is None:
                left_out.append(leg.get("label") or str(leg.get("symbol")))
                continue
            size = float(leg.get("quantity") or 0.0) * MULTIPLIER
            if ours["basis"] == "expiring":
                # Beta-weighted here, so the line is in the same benchmark shares as the totals.
                if row["scale"] is None:
                    expiring["scaled"] = False
                scale = row["scale"] or 0.0
                expiring["delta"][0] += ours["delta"] * scale
                expiring["delta"][1] += size * b_delta * scale
                expiring["theta"][0] += ours["theta"]
                expiring["theta"][1] += size * b_theta
                expiring["legs"].append(leg.get("label") or str(leg.get("symbol")))
                continue
            row["delta"][0] += ours["delta"]
            row["delta"][1] += size * b_delta
            row["theta"][0] += ours["theta"]
            row["theta"][1] += size * b_theta
            row["legs"] += 1
            compared += 1
            on_broker_iv += leg.get("iv_source") == "broker"

    names = []
    totals = {"bwd": [0.0, 0.0], "theta": [0.0, 0.0]}
    for r in per.values():
        bwd = None if r["scale"] is None else [d * r["scale"] for d in r["delta"]]
        if bwd is not None:
            totals["bwd"][0] += bwd[0]
            totals["bwd"][1] += bwd[1]
        totals["theta"][0] += r["theta"][0]
        totals["theta"][1] += r["theta"][1]
        if r["legs"]:
            names.append(
                {
                    "underlying": r["underlying"],
                    "legs": r["legs"],
                    "bwd_ours": _round(bwd[0]) if bwd else None,
                    "bwd_broker": _round(bwd[1]) if bwd else None,
                    "bwd_gap": _round(bwd[0] - bwd[1]) if bwd else None,
                    "theta_ours": _round(r["theta"][0]),
                    "theta_broker": _round(r["theta"][1]),
                    "theta_gap": _round(r["theta"][0] - r["theta"][1]),
                }
            )
    names.sort(key=lambda n: -abs(n["bwd_gap"] if n["bwd_gap"] is not None else n["theta_gap"] or 0.0))
    have_bench = bench_spot is not None
    exp_bwd = expiring["delta"] if have_bench and expiring["scaled"] else None
    return {
        "available": True,
        "legs_compared": compared,
        "legs_left_out": left_out,
        "legs_on_broker_iv": on_broker_iv,
        "expiring": {
            "legs": expiring["legs"],
            "bwd_ours": _round(exp_bwd[0]) if exp_bwd else None,
            "bwd_broker": _round(exp_bwd[1]) if exp_bwd else None,
            "bwd_gap": _round(exp_bwd[0] - exp_bwd[1]) if exp_bwd else None,
            "theta_ours": _round(expiring["theta"][0]),
            "theta_broker": _round(expiring["theta"][1]),
            "theta_gap": _round(expiring["theta"][0] - expiring["theta"][1]),
        }
        if expiring["legs"]
        else None,
        "bwd_ours": _round(totals["bwd"][0]) if have_bench else None,
        "bwd_broker": _round(totals["bwd"][1]) if have_bench else None,
        "bwd_gap": _round(totals["bwd"][0] - totals["bwd"][1]) if have_bench else None,
        "theta_ours": _round(totals["theta"][0]),
        "theta_broker": _round(totals["theta"][1]),
        "theta_gap": _round(totals["theta"][0] - totals["theta"][1]),
        "underlyings": names,
    }


def reading(
    trades: list[dict[str, Any]],
    metrics: dict[str, Any],
    benchmark: str | None,
    balances: dict[str, Any],
    account_type: dict[str, Any] | None,
    rules: dict[str, Any],
    broker_greeks: dict[str, dict] | None = None,
) -> dict[str, Any]:
    cfg = rules["portfolio"]
    rate = float(rules["pricing"]["rate"])
    front_dte = int(cfg["theta"].get("front_dte", 0))
    net_liq = _f(balances.get("net_liquidating_value"))
    bench = (metrics or {}).get(benchmark or "") or {}
    bench_spot = _f(bench.get("last"))

    unmodelled: list[str] = []
    no_beta: list[str] = []
    per: dict[str, dict[str, Any]] = {}
    leg_thetas: list[tuple[str | None, float]] = []
    trade_theta: dict[int, float] = {}
    for trade in trades:
        und = trade["underlying"]
        spot = _f(trade.get("spot"))
        m = (metrics or {}).get(und) or {}
        beta, corr = _f(m.get("beta")), _f(m.get("corr_spy_3month"))
        row = per.setdefault(
            und,
            {
                "underlying": und,
                "spot": spot,
                "beta": beta,
                "corr_spy_3month": corr,
                "beta_doubt": _beta_doubt(beta, corr, cfg["beta_doubt"]),
                "delta": 0.0,
                "gamma": 0.0,
                "theta": 0.0,
                "theta_front": 0.0,
                "vega": 0.0,
                "vega_30": 0.0,
                "bwd": None,
                "gamma_bench": None,
                "legs": [],
            },
        )
        for leg in trade.get("legs") or []:
            g = leg_greeks(leg, spot, rate) if leg.get("priced") else None
            if g is None:
                unmodelled.append(leg.get("label") or str(leg.get("symbol")))
                continue
            for key in ("delta", "gamma", "theta", "vega", "vega_30"):
                row[key] += g[key]
            if leg.get("right") in ("C", "P") and leg.get("dte") is not None and leg["dte"] <= front_dte:
                row["theta_front"] += g["theta"]
            row["legs"].append(leg)
            leg_thetas.append((leg.get("expiration"), g["theta"]))
            trade_theta[id(trade)] = trade_theta.get(id(trade), 0.0) + g["theta"]

    for row in per.values():
        if row["beta"] is not None and row["spot"] and bench_spot:
            scale = row["beta"] * row["spot"] / bench_spot
            row["bwd"] = row["delta"] * scale
            row["gamma_bench"] = row["gamma"] * scale * scale
        elif row["legs"]:
            no_beta.append(row["underlying"])

    rows = list(per.values())
    for r in rows:
        r["theta_ex_front"] = r["theta"] - r["theta_front"]
    theta = sum(r["theta"] for r in rows)
    theta_ex_front = sum(r["theta_ex_front"] for r in rows)
    bwd = sum(r["bwd"] for r in rows if r["bwd"] is not None) if bench_spot else None
    gamma_bench = sum(r["gamma_bench"] for r in rows if r["gamma_bench"] is not None) if bench_spot else None

    iv = _f(bench.get("iv_index"))
    hv_pct = _f(bench.get("hv_30_day_pct"))
    day = math.sqrt(1.0 / 365.0)
    expected = bench_spot * iv * day if bench_spot and iv else None
    realised = bench_spot * hv_pct / 100.0 * day if bench_spot and hv_pct else None
    breakeven = _breakeven(theta, gamma_bench, bench_spot)

    used = _f(balances.get("used_derivative_buying_power"))
    # Concentration's theta share is of the theta that is not about to expire, like the band.
    gross_theta = sum(abs(r["theta_ex_front"]) for r in rows)
    gross_bwd = sum(abs(r["bwd"]) for r in rows if r["bwd"] is not None)

    underlyings = []
    for r in sorted(rows, key=lambda r: -abs(r["bwd"] if r["bwd"] is not None else r["theta"])):
        underlyings.append(
            {
                "underlying": r["underlying"],
                "beta": r["beta"],
                "corr_spy_3month": r["corr_spy_3month"],
                "beta_doubt": r["beta_doubt"],
                "delta_shares": _round(r["delta"]),
                "bwd": _round(r["bwd"]),
                "theta": _round(r["theta"]),
                "theta_ex_front": _round(r["theta_ex_front"]),
                "vega_30": _round(r["vega_30"]),
                "bwd_share": _round(abs(r["bwd"]) / gross_bwd, 3)
                if r["bwd"] is not None and gross_bwd
                else None,
                "theta_share": _round(abs(r["theta_ex_front"]) / gross_theta, 3) if gross_theta else None,
            }
        )

    out: dict[str, Any] = {
        "benchmark": benchmark,
        "benchmark_spot": bench_spot,
        "complete": not unmodelled and not no_beta and bench_spot is not None,
        "unmodelled_legs": unmodelled,
        "no_beta": no_beta,
        "beta_source": "broker",
        "account_type": account_type or {"margin_or_cash": None, "type_name": None},
        "net_liq": net_liq,
        "bwd": _round(bwd),
        "bwd_dollars_per_1pct": _round(bwd * bench_spot * 0.01) if bwd is not None and bench_spot else None,
        "theta": _round(theta),
        "theta_pct_net_liq": _round(theta / net_liq, 5) if net_liq else None,
        "front_dte": front_dte,
        "theta_ex_front": _round(theta_ex_front),
        "theta_ex_front_pct_net_liq": _round(theta_ex_front / net_liq, 5) if net_liq else None,
        "gamma_bench": _round(gamma_bench, 4),
        "vega": _round(sum(r["vega"] for r in rows)),
        "vega_30": _round(sum(r["vega_30"] for r in rows)),
        "breakeven": breakeven,
        "expected_move": _round(expected),
        "expected_move_pct": _round(iv * day, 5) if iv else None,
        "realised_move": _round(realised),
        "buying_power_used": used,
        "buying_power_used_pct": _round(used / net_liq, 4) if used is not None and net_liq else None,
        "stress": _stress(rows, bench_spot, rate, cfg["stress"]["shocks"], net_liq),
        "beta_doubts": [
            {"underlying": u["underlying"], "reason": u["beta_doubt"], "bwd_share": u["bwd_share"]}
            for u in underlyings
            if u["beta_doubt"]
        ],
        "underlyings": underlyings,
        "vix": _f(((metrics or {}).get(VIX) or {}).get("last")),
        "scenarios": scenarios(rows, bench_spot, rate, cfg["scenarios"], net_liq),
        "ladder": ladder(trades, leg_thetas, trade_theta, theta),
        "broker_check": broker_check(trades, broker_greeks, metrics, bench_spot, rate),
    }
    out["bands"] = bands(out, cfg)
    out["guidance"] = guidance(out, rules)
    return out


VIX = "VIX"


def scenarios(
    rows: list[dict[str, Any]],
    bench_spot: float | None,
    rate: float,
    cfg: dict[str, Any],
    net_liq: float | None,
) -> dict[str, Any]:
    """The book repriced across benchmark moves: a matrix against IV shifts (time held still), and a
    risk graph at two horizons -- now, and the nearest expiry, when every leg expiring by then is
    worth its intrinsic value and the rest have that much less time.

    The IV shift is stated for a 30-day option and, with `term_weighted` (the default), scaled to
    each leg by sqrt(30 / DTE), so front months move more than long-dated legs. Real implied
    volatility also rises as the market falls (skew, spot-vol correlation), so the down-move/IV-up
    corner is the one to read; neither is a model of how vol moves, and the reading says which was
    used. On 2026-10-18 a flat shift showed the book GAINING $425 on +5 points, carried by its
    long-dated longs, while its front months -- what a vol spike hits -- were short."""
    moves = [float(m) for m in cfg["moves"]]
    shifts = [float(s) for s in cfg["iv_shifts"]]
    weighted = cfg.get("iv_shift_weighting") == "term"
    matrix = []
    left_out: list[str] = []
    for shift in shifts:
        cells = []
        for move in moves:
            pnl, lo = book_pnl(rows, bench_spot, rate, move, shift, 0.0, weighted)
            left_out = lo
            cells.append(round(pnl, 2))
        matrix.append({"iv_shift": shift, "pnl": cells})

    dtes = [
        leg["dte"]
        for r in rows
        for leg in r["legs"]
        if leg.get("right") in ("C", "P") and (leg.get("dte") or 0) > 0
    ]
    horizon = min(dtes) if dtes else None
    lo_g, hi_g, step = (float(v) for v in cfg["graph"])
    count = int(round((hi_g - lo_g) / step))
    grid = [round(lo_g + i * step, 4) for i in range(count + 1)]
    curves = [{"horizon_days": 0, "pnl": [round(book_pnl(rows, bench_spot, rate, m)[0], 2) for m in grid]}]
    if horizon:
        ahead = horizon / 365.0
        curves.append(
            {
                "horizon_days": horizon,
                "pnl": [round(book_pnl(rows, bench_spot, rate, m, 0.0, ahead)[0], 2) for m in grid],
            }
        )
    return {
        "moves": moves,
        "iv_shift_weighting": "term" if weighted else "flat",
        "matrix": matrix,
        "graph": {"moves": grid, "curves": curves},
        "net_liq": net_liq,
        "left_out": left_out,
    }


def ladder(
    trades: list[dict[str, Any]],
    leg_thetas: list[tuple[str | None, float]],
    trade_theta: dict[int, float],
    theta: float,
) -> dict[str, Any]:
    """What the book has expiring when: per expiry date, the trades whose nearest short (else nearest
    leg) expires then, what they can still lose (each trade's modelled downside from now), and the
    theta of the legs expiring then. Then the share of the book's theta and downside in trades with
    an earnings report before their nearest short expires."""
    by: dict[str, dict[str, Any]] = {}

    def slot(exp: str) -> dict[str, Any]:
        return by.setdefault(
            exp, {"expiration": exp, "trades": [], "downside": 0.0, "downside_unknown": 0, "theta": 0.0}
        )

    for exp, th in leg_thetas:
        if exp:
            slot(exp)["theta"] += th
    earn = {"trades": [], "theta": 0.0, "downside": 0.0}
    by_report: dict[str, dict[str, Any]] = {}
    for t in trades:
        exp = t.get("nearest_short_expiry") or t.get("nearest_expiry")
        if not exp:
            continue
        s = slot(exp)
        s["trades"].append(t.get("trade_id") or t["underlying"])
        down = _f((t.get("profile") or {}).get("downside_from_now"))
        if down is None:
            s["downside_unknown"] += 1
        else:
            s["downside"] += down
        er = t.get("earnings") or {}
        if er.get("before_nearest_short_expiry"):
            earn["trades"].append(t.get("trade_id") or t["underlying"])
            if down is not None:
                earn["downside"] += down
            earn["theta"] += trade_theta.get(id(t), 0.0)
            day = by_report.setdefault(
                str(er.get("date") or "unknown"), {"trades": [], "downside": 0.0, "downside_unknown": 0}
            )
            day["trades"].append(t.get("trade_id") or t["underlying"])
            if down is None:
                day["downside_unknown"] += 1
            else:
                day["downside"] += down
    rows = []
    for exp in sorted(by):
        s = by[exp]
        rows.append(
            {
                "expiration": exp,
                "trades": s["trades"],
                "downside": round(s["downside"], 2),
                "downside_unknown": s["downside_unknown"],
                "theta": round(s["theta"], 2),
            }
        )
    return {
        "expiries": rows,
        "earnings": {
            "trades": earn["trades"],
            "theta": round(earn["theta"], 2),
            "theta_share": round(earn["theta"] / theta, 3) if theta > 0 else None,
            "downside": round(earn["downside"], 2),
            # The same trades by report date: what each day's announcements can take at once.
            "by_date": [
                {
                    "date": d,
                    "trades": by_report[d]["trades"],
                    "downside": round(by_report[d]["downside"], 2),
                    "downside_unknown": by_report[d]["downside_unknown"],
                }
                for d in sorted(by_report)
            ],
        },
    }


def bands(r: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Each band with the reading's place in it, stated here so no surface re-derives a band or
    decides "inside" on its own. `inside` is None where there is no figure to place."""
    delta = delta_band(r, cfg["delta"])
    lo_pct, hi_pct = float(cfg["theta"]["min_pct_nlv"]), float(cfg["theta"]["max_pct_nlv"])
    pct, bwd = r["theta_ex_front_pct_net_liq"], r["bwd"]
    return {
        "delta": {
            "low": _round(delta[0]) if delta else None,
            "high": _round(delta[1]) if delta else None,
            "basis": delta_band_basis(r, cfg["delta"]),
            "value": bwd,
            "inside": (delta[0] <= bwd <= delta[1]) if delta and bwd is not None else None,
        },
        "theta": {
            "low": lo_pct,
            "high": hi_pct,
            "value": pct,
            "inside": (lo_pct <= pct <= hi_pct) if pct is not None else None,
            # `value` leaves out legs `front_dte` days or less from expiry; `raw_value` is the whole book.
            "raw_value": r["theta_pct_net_liq"],
            "front_dte": r["front_dte"],
        },
        "buying_power": _bp_band(r, cfg["buying_power"]),
    }


def bp_cap(vix: float | None, steps: list[list[Any]]) -> float | None:
    """The buying-power ceiling for this VIX: the first step whose VIX bound it is under (a null
    bound catches the rest). None without a VIX -- a cap guessed for a missing VIX would be a claim."""
    if vix is None:
        return None
    for bound, cap in steps:
        if bound is None or vix < float(bound):
            return float(cap)
    return None


def _bp_band(r: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    cap = bp_cap(r.get("vix"), cfg["vix_steps"])
    used = r.get("buying_power_used_pct")
    return {
        "low": 0.0,
        "high": cap,
        "value": used,
        "vix": r.get("vix"),
        "inside": (used <= cap) if cap is not None and used is not None else None,
    }


def _breakeven(theta: float, gamma: float | None, bench_spot: float | None) -> dict[str, Any] | None:
    """The benchmark move that wipes out a day's theta (short gamma), or that a day's theta needs
    (long gamma). None when theta and gamma do not oppose each other."""
    if gamma is None or not bench_spot or gamma == 0 or theta == 0 or (theta > 0) == (gamma > 0):
        return None
    points = math.sqrt(2.0 * abs(theta) / abs(gamma))
    return {
        "kind": "short_gamma" if gamma < 0 else "long_gamma",
        "points": round(points, 2),
        "pct": round(points / bench_spot, 5),
    }


def _stress(
    rows: list[dict[str, Any]],
    bench_spot: float | None,
    rate: float,
    shocks: list[float],
    net_liq: float | None,
) -> list[dict[str, Any]]:
    out = []
    for shock in shocks:
        total, left_out = book_pnl(rows, bench_spot, rate, shock)
        out.append(
            {
                "shock": shock,
                "pnl": round(total, 2),
                "pct_net_liq": round(total / net_liq, 4) if net_liq else None,
                "left_out": left_out,
            }
        )
    return out


# --------------------------------------------------------------------------- guidance
def _fire(rule: str, confidence: Any, reason: str, drivers: list[dict[str, Any]] | None = None) -> dict:
    return {
        "rule": rule,
        "action": REVIEW,
        "confidence": confidence,
        "reason": reason,
        "drivers": drivers or [],
    }


def _apart(value: float, bound: float, places: int, kind: str) -> tuple[str, str]:
    """`value` and the `bound` it breached, formatted (`kind` is "%" or "f", sign included where the
    caller's spec wants one) with as many places past `places` as it takes for the two to read
    differently. A theta of 0.304% above a 0.30% ceiling once read "0.30% ... above the 0.30%"."""
    sign = "+" if kind.startswith("+") else ""
    spec = kind.lstrip("+")
    for p in range(places, places + 5):
        a, b = format(value, f"{sign}.{p}{spec}"), format(bound, f"{sign}.{p}{spec}")
        if a != b:
            return a, b
    return a, b


def delta_band(reading_: dict[str, Any], cfg: dict[str, Any]) -> tuple[float, float] | None:
    """(low, high) BWD band in benchmark shares; `delta_band_basis` says which band it is."""
    band = _delta_band(reading_, cfg)
    return band[:2] if band else None


def delta_band_basis(reading_: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    band = _delta_band(reading_, cfg)
    return band[2] if band else None


def _delta_band(reading_: dict[str, Any], cfg: dict[str, Any]) -> tuple[float, float, str] | None:
    """(low, high, basis). Scaled by theta while the book collects it, and never narrower than the
    net-liq band (the BWD whose 1% move costs `fallback_pct_nlv_per_1pct` of net liq), which is also
    the band when there is no theta to scale by: a band of zero width would fire on every share, and
    before the floor a book collecting $1.63 a day got a band eight times narrower than one
    collecting nothing. The theta is the theta band's (without the legs about to expire), so a leg
    expiring tomorrow cannot widen it: on 2026-10-20 one did, to -106 to +160 shares."""
    theta = reading_.get("theta_ex_front")
    if theta is None:  # a reading from before the split
        theta = reading_["theta"]
    net_liq, spot = reading_["net_liq"], reading_["benchmark_spot"]
    by_theta = (
        (-float(cfg["short_per_theta"]) * theta, float(cfg["long_per_theta"]) * theta, "theta")
        if theta is not None and theta > 0
        else None
    )
    by_net_liq = None
    if net_liq and spot:
        half = float(cfg["fallback_pct_nlv_per_1pct"]) * net_liq / (spot * 0.01)
        by_net_liq = (-half, half, "net_liq")
    if by_theta and by_net_liq:
        # The wider of the two, whole: halves from different bands would lean neither way they mean.
        return by_theta if by_theta[1] - by_theta[0] >= by_net_liq[1] - by_net_liq[0] else by_net_liq
    return by_theta or by_net_liq


def guidance(r: dict[str, Any], rules: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = rules["portfolio"]
    if cfg.get("enabled") is False:
        return []
    fired = []

    def off(rule_id: str, underlying: str = "") -> bool:
        return _rules.disabled(rules, underlying, rule_id)

    partial = "" if r["complete"] else " (partial: some legs or betas are missing)"
    pct = r["theta_ex_front_pct_net_liq"]
    tc = cfg["theta"]
    if not off("portfolio.theta") and pct is not None:
        lo, hi = float(tc["min_pct_nlv"]), float(tc["max_pct_nlv"])
        # Which theta the band read, stated with the whole book's when the expiring legs moved it.
        stated = f"theta ${r['theta_ex_front']:,.2f}/day"
        if round(r["theta_ex_front"], 2) != round(r["theta"], 2):
            stated += (
                f" without the legs {r['front_dte']} days or less from expiry (${r['theta']:,.2f} with them)"
            )
        if pct > hi:
            # Drivers carry the theta the band read, under the `theta` figure every surface knows.
            drivers = [
                {"underlying": d["underlying"], "theta": d["theta_ex_front"]}
                for d in _drivers(r, "theta_ex_front", 1.0)
            ]
            shown, bound = _apart(pct, hi, 2, "%")
            fired.append(
                _fire(
                    "portfolio.theta",
                    tc["confidence"],
                    f"{stated} is {shown} of net liq, above the {bound} ceiling: "
                    f"the book is selling more gamma than the band allows{partial}",
                    drivers,
                )
            )
        elif pct < lo:
            shown, bound = _apart(pct, lo, 2, "%")
            fired.append(
                _fire(
                    "portfolio.theta",
                    tc["confidence"],
                    f"{stated} is {shown} of net liq, under the {bound} floor{partial}",
                )
            )

    dc = cfg["delta"]
    band = delta_band(r, dc)
    if not off("portfolio.delta") and r["bwd"] is not None and band:
        lo, hi = band
        if not lo <= r["bwd"] <= hi:
            side = 1.0 if r["bwd"] > hi else -1.0
            shown, _ = _apart(r["bwd"], hi if side > 0 else lo, 1, "+f")
            places = len(shown.partition(".")[2])
            fired.append(
                _fire(
                    "portfolio.delta",
                    dc["confidence"],
                    f"beta-weighted delta {shown} {r['benchmark']} shares "
                    f"(${r['bwd_dollars_per_1pct']:+,.0f} per 1% move) is outside "
                    f"{lo:+.{places}f} to {hi:+.{places}f}"
                    f"{partial}",
                    _drivers(r, "bwd", side),
                )
            )

    bc = cfg["breakeven"]
    be, expected = r["breakeven"], r["expected_move_pct"]
    if not off("portfolio.breakeven") and be and be["kind"] == "short_gamma" and expected:
        need = float(bc["min_multiple_of_expected_move"]) * expected
        if be["pct"] < need:
            shown, bound = _apart(be["pct"], expected, 2, "%")
            fired.append(
                _fire(
                    "portfolio.breakeven",
                    bc["confidence"],
                    f"a {shown} {r['benchmark']} move wipes out a day's theta, under the "
                    f"{bound} move its implied volatility expects{partial}",
                )
            )

    bp = (r.get("bands") or {}).get("buying_power") or {}
    if not off("portfolio.buying_power") and bp.get("inside") is False:
        shown, bound = _apart(bp["value"], bp["high"], 0, "%")
        fired.append(
            _fire(
                "portfolio.buying_power",
                cfg["buying_power"]["confidence"],
                f"buying power used is {shown} of net liq, over the {bound} the VIX at "
                f"{bp['vix']:.1f} allows: less room to adjust, and more to lose if the market gaps",
            )
        )

    cc = cfg["concentration"]
    if not off("portfolio.concentration"):
        cap = float(cc["max_share"])
        heavy = [
            u
            for u in r["underlyings"]
            if not off("portfolio.concentration", u["underlying"])
            and max(u["theta_share"] or 0.0, u["bwd_share"] or 0.0) > cap
        ]
        if heavy:
            names = ", ".join(
                f"{u['underlying']} ({max(u['theta_share'] or 0.0, u['bwd_share'] or 0.0):.0%})"
                for u in heavy
            )
            fired.append(
                _fire(
                    "portfolio.concentration",
                    cc["confidence"],
                    f"over {cap:.0%} of the book's theta (without the legs about to expire, as the band "
                    f"reads it) or beta-weighted delta in one name: {names}",
                    [
                        {
                            "underlying": u["underlying"],
                            "theta_share": u["theta_share"],
                            "bwd_share": u["bwd_share"],
                        }
                        for u in heavy
                    ],
                )
            )

    net_liq = r.get("net_liq")
    lad = r.get("ladder") or {}
    ec = cfg.get("earnings_exposure") or {}
    earn = lad.get("earnings") or {}
    if ec and not off("portfolio.earnings_exposure") and net_liq and earn.get("trades"):
        cap = float(ec["max_pct_nlv"])
        share = abs(float(earn.get("downside") or 0.0)) / net_liq
        if share > cap:
            days = ", ".join(
                f"{d['date']} ${abs(d['downside']):,.0f} ({', '.join(d['trades'])})"
                for d in earn.get("by_date") or []
            )
            fired.append(
                _fire(
                    "portfolio.earnings_exposure",
                    ec["confidence"],
                    f"{len(earn['trades'])} trades span an earnings report before their short expires "
                    f"and can still lose ${abs(earn['downside']):,.0f}, {share:.0%} of net liq, "
                    f"over {cap:.0%}: {days}",
                )
            )
    xc = cfg.get("expiry_concentration") or {}
    if xc and not off("portfolio.expiry_concentration") and net_liq:
        cap = float(xc["max_pct_nlv"])
        for e in lad.get("expiries") or []:
            share = abs(float(e.get("downside") or 0.0)) / net_liq
            if share > cap:
                fired.append(
                    _fire(
                        "portfolio.expiry_concentration",
                        xc["confidence"],
                        f"{len(e['trades'])} trades expiring {e['expiration']} can still lose "
                        f"${abs(e['downside']):,.0f}, {share:.0%} of net liq, over {cap:.0%}: "
                        f"{', '.join(e['trades'])}",
                    )
                )
    return fired


def expiry_over(r: dict[str, Any], rules: dict[str, Any], expiration: str | None) -> bool:
    """Whether the book's trades expiring on `expiration` already hold more downside than
    `portfolio.expiry_concentration` allows: a new candidate there waits (candidates.size)."""
    xc = (rules.get("portfolio") or {}).get("expiry_concentration") or {}
    net_liq = r.get("net_liq")
    if not xc or xc.get("enabled") is False or not net_liq or not expiration:
        return False
    for e in (r.get("ladder") or {}).get("expiries") or []:
        if e.get("expiration") == expiration:
            return abs(float(e.get("downside") or 0.0)) / net_liq > float(xc["max_pct_nlv"])
    return False


def _drivers(r: dict[str, Any], key: str, side: float, limit: int = 5) -> list[dict[str, Any]]:
    """The underlyings pushing `key` toward `side`, largest first."""
    pushing = [u for u in r["underlyings"] if u[key] is not None and u[key] * side > 0]
    pushing.sort(key=lambda u: -abs(u[key]))
    return [{"underlying": u["underlying"], key: u[key]} for u in pushing[:limit]]
