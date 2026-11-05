"""Your limits against the book (2026-11-04): the measures a person sets in the ruleset's `limits`
block, each beside what the book holds now.

| measure | from | a blank limit falls back to |
|---|---|---|
| delta, benchmark shares | the reading's `bwd` | its delta band |
| dollar delta | `bwd` x the benchmark's price | none |
| theta a day | theta without the front legs (the band's) | the theta band x net liq |
| vega | the reading's `vega` | none |
| buying power used / net liq | `buying_power_used_pct` | the VIX-scaled band |
| one name's worst case / net liq | trades' worst case from open, by name | the candidate cap |
| the day's loss | each leg's mark less its previous close | `day_loss_alert_pct` x net liq |

The candidate cap is `portfolio.candidates.notional_per_name_pct`, by the account's kind.

Each row says whose limit it is (`yours`, `guidance band`, `candidate cap`, or `none`) and its state:
`over` past it, `near` within the last `near_share` of the way to it, `within`, or `unknown` with no
figure. The day's loss is an alert, not a band: `crossed` when the loss reaches it, which the
orchestrator posts once a session. Advice only: a limit stops nothing and fires no rule.
"""

from __future__ import annotations

from typing import Any

from cherrypick.manage import candidates as _candidates


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


def _state(value: float | None, low: float | None, high: float | None, near: float) -> str:
    if value is None or (low is None and high is None):
        return "unknown" if value is None else "within"
    if (low is not None and value < low) or (high is not None and value > high):
        return "over"
    # Near: in the last (1 - near) of the way from zero (or the band's middle) to the edge it faces.
    if low is not None and high is not None and low < 0 < high:
        edge = high if value >= 0 else low
        return "near" if abs(value) >= near * abs(edge) else "within"
    if high is not None and (low is None or low >= 0):
        return "near" if high > 0 and value >= near * high else "within"
    if low is not None and high is None:
        return "near" if low < 0 and value <= near * low else "within"
    return "within"


def _row(
    rid: str,
    label: str,
    unit: str,
    value: float | None,
    low: float | None,
    high: float | None,
    source: str,
    near: float,
) -> dict[str, Any]:
    return {
        "id": rid,
        "label": label,
        "unit": unit,
        "value": None if value is None else round(value, 4),
        "low": None if low is None else round(low, 4),
        "high": None if high is None else round(high, 4),
        "source": source,
        "state": _state(value, low, high, near)
        if source != "none"
        else ("unknown" if value is None else "no limit"),
    }


def _day_pl(underlyings: list[dict[str, Any]] | None) -> tuple[float | None, str | None]:
    total = 0.0
    for u in underlyings or []:
        for leg in u.get("legs") or []:
            qty = _f(leg.get("quantity")) or 0.0
            if not qty:
                continue
            mid, prev = _f(leg.get("mid")), _f(leg.get("prev_close"))
            if mid is None or prev is None:
                return None, f"{leg.get('symbol')} has no {'mark' if mid is None else 'previous close'}"
            mult = _f(leg.get("multiplier")) or (100.0 if leg.get("right") in ("C", "P") else 1.0)
            total += (mid - prev) * qty * mult
    return total, None


def reading(
    book: dict[str, Any],
    trades: list[dict[str, Any]],
    underlyings: list[dict[str, Any]] | None,
    account_type: dict[str, Any] | None,
    rules: dict[str, Any],
) -> dict[str, Any]:
    cfg = rules.get("limits") or {}
    near = float(cfg.get("near_share") or 0.8)
    net_liq = _f(book.get("net_liq"))
    bands = book.get("bands") or {}
    rows = []

    # Delta, benchmark shares.
    bwd = _f(book.get("bwd"))
    mine = _f(cfg.get("delta_bwd"))
    band = bands.get("delta") or {}
    if mine is not None:
        rows.append(_row("delta", "delta, beta-weighted", "shares", bwd, -mine, mine, "yours", near))
    elif band:
        rows.append(
            _row(
                "delta",
                "delta, beta-weighted",
                "shares",
                bwd,
                _f(band.get("low")),
                _f(band.get("high")),
                "guidance band",
                near,
            )
        )
    else:
        rows.append(_row("delta", "delta, beta-weighted", "shares", bwd, None, None, "none", near))

    # Dollar delta.
    spot = _f(book.get("benchmark_spot"))
    dollars = None if bwd is None or spot is None else bwd * spot
    mine = _f(cfg.get("dollar_delta"))
    rows.append(
        _row(
            "dollar_delta",
            "dollar delta, beta-weighted",
            "$",
            dollars,
            None if mine is None else -mine,
            mine,
            "yours" if mine is not None else "none",
            near,
        )
    )

    # Theta a day, on the band's own figure (without the legs about to expire).
    theta = _f(book.get("theta_ex_front"))
    if theta is None:
        theta = _f(book.get("theta"))
    mine = _f(cfg.get("theta_day"))
    tb = bands.get("theta") or {}
    if mine is not None:
        rows.append(_row("theta", "theta a day", "$", theta, None, mine, "yours", near))
    elif tb and net_liq:
        lo, hi = _f(tb.get("low")), _f(tb.get("high"))
        rows.append(
            _row(
                "theta",
                "theta a day",
                "$",
                theta,
                None if lo is None else lo * net_liq,
                None if hi is None else hi * net_liq,
                "guidance band",
                near,
            )
        )
    else:
        rows.append(_row("theta", "theta a day", "$", theta, None, None, "none", near))

    # Vega.
    vega = _f(book.get("vega"))
    mine = _f(cfg.get("vega"))
    rows.append(
        _row(
            "vega",
            "vega, per vol point",
            "$",
            vega,
            None if mine is None else -mine,
            mine,
            "yours" if mine is not None else "none",
            near,
        )
    )

    # Buying power used.
    used = _f(book.get("buying_power_used_pct"))
    mine = _f(cfg.get("buying_power_pct"))
    bp = bands.get("buying_power") or {}
    if mine is not None:
        rows.append(
            _row("buying_power", "buying power used / net liq", "share", used, None, mine, "yours", near)
        )
    elif bp.get("high") is not None:
        rows.append(
            _row(
                "buying_power",
                "buying power used / net liq",
                "share",
                used,
                None,
                _f(bp["high"]),
                "guidance band",
                near,
            )
        )
    else:
        rows.append(
            _row("buying_power", "buying power used / net liq", "share", used, None, None, "none", near)
        )

    # One name's worst case.
    by_name: dict[str, float] = {}
    unknown_names = set()
    for t in trades:
        worst = _f((t.get("profile") or {}).get("max_loss_from_open"))
        name = str(t.get("underlying") or "")
        if worst is None:
            unknown_names.add(name)
            continue
        by_name[name] = by_name.get(name, 0.0) + max(0.0, -worst)
    top = max(by_name.items(), key=lambda kv: kv[1]) if by_name else None
    share = None if top is None or not net_liq else top[1] / net_liq
    mine = _f(cfg.get("per_name_pct"))
    basis = _candidates.account_basis(account_type)
    cap = _f(
        (((rules.get("portfolio") or {}).get("candidates") or {}).get("notional_per_name_pct") or {}).get(
            "cash" if basis == "cash" else "margin"
        )
    )
    label = "one name's worst case / net liq" + (f" ({top[0]})" if top else "")
    if mine is not None:
        rows.append(_row("per_name", label, "share", share, None, mine, "yours", near))
    else:
        rows.append(
            _row(
                "per_name",
                label,
                "share",
                share,
                None,
                cap,
                "candidate cap" if cap is not None else "none",
                near,
            )
        )

    # The day's loss: an alert.
    day, why = _day_pl(underlyings)
    alert = _f(cfg.get("day_loss_alert"))
    source = "yours"
    if alert is None:
        pct = _f(cfg.get("day_loss_alert_pct"))
        alert = None if pct is None or not net_liq else pct * net_liq
        source = "share of net liq" if alert is not None else "none"
    day_row = _row(
        "day_loss", "the day's P&L", "$", day, None if alert is None else -abs(alert), None, source, near
    )
    rows.append(day_row)
    return {
        "rows": rows,
        "unknown_names": sorted(n for n in unknown_names if n not in by_name),
        "day_loss": {
            "pnl": None if day is None else round(day, 2),
            "alert": None if alert is None else round(-abs(alert), 2),
            "crossed": None if day is None or alert is None else day <= -abs(alert),
            "why": why,
        },
    }
