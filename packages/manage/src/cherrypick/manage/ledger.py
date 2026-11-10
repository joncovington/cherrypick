"""The ledger: what each name has earned, read from the account's whole trade history.

A pure reading of the snapshot's account-wide history (`account_transactions`), the rows the premium
pace reads (`premium.closed_lots`, here with stock spans too). For every name:

- **puts** and **calls** -- the net cash of each option leg that went from flat back to flat, fees
  included, on the day it closed (bought back, expired, exercised, assigned or cash settled). A roll
  is the old leg closing and a new one opening: the old one counts when it closes.
- **shares** -- the net cash of each stock span from flat back to flat: the shares' realised P&L.
  Shares an assignment delivered are bought at the strike there, so the put's premium is in `puts`
  and the stock's move is in `shares`, each once.
- **dividends** -- each dividend received, on its day.
- **fees** -- what the closed spans paid in fees (signed as the broker writes them, negative).

`net` is puts + calls + shares + dividends. Nothing still held counts: an open leg's cash so far is
listed apart under `open`, never as kept. Two exclusions are stated with their count and net, as the
pace states them: 0DTE legs on `exclude.zero_dte_underlyings` (the live loops' and the hand's own
books, judged on their pages) and spans the history entered mid-position (their cash is incomplete).

Windows are counted back from the session by the closing day: 1M (30 days), 3M (91), 6M (182), 1Y
(365), YTD and ALL. Months are calendar months in ET, the last `MONTHS`. Advice-free and read-only.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from cherrypick.manage import premium as _premium

MONTHS = 12
WINDOWS = (("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365), ("YTD", None), ("ALL", None))
SOURCES = ("puts", "calls", "shares", "dividends")


def _right(lot: dict[str, Any]) -> str:
    """ "P" or "C" for an option leg (its OCC letter), "S" for a stock span."""
    s = str(lot.get("symbol") or "").replace(" ", "")
    if _premium._occ_expiry(str(lot.get("symbol") or "")) is None:
        return "S"
    for i in range(len(s) - 9, 0, -1):
        if s[i] in "CP" and s[i - 6 : i].isdigit():
            return s[i]
    return "S"


def _sym(value: Any) -> str:
    """A contract symbol with its padding collapsed, as `wheel.py` writes it."""
    return " ".join(str(value or "").split())


def _source(right: str) -> str:
    return {"P": "puts", "C": "calls"}.get(right, "shares")


def _since(session: date, name: str, days: int | None) -> date | None:
    if name == "ALL":
        return None
    if name == "YTD":
        return date(session.year, 1, 1)
    return session - timedelta(days=int(days or 0))


def _blank() -> dict[str, Any]:
    return {**{s: 0.0 for s in SOURCES}, "fees": 0.0, "net": 0.0, "legs": 0}


def _add(bucket: dict[str, Any], source: str, net: float, fees: float, legs: int) -> None:
    bucket[source] += net
    bucket["fees"] += fees
    bucket["net"] += net
    bucket["legs"] += legs


def _rounded(bucket: dict[str, Any]) -> dict[str, Any]:
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in bucket.items()}


def _dividends(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if r.get("transaction_type") == "Money Movement" and r.get("transaction_sub_type") == "Dividend":
            day = _premium._et_day(r.get("executed_at"))
            if day is None:
                continue
            name = str(r.get("underlying_symbol") or r.get("symbol") or "").upper()
            out.append({"underlying": name, "day": day, "net": _premium._f(r.get("net_value")) or 0.0})
    return out


def _how(ended: Any) -> str:
    return {
        "Expiration": "expired",
        "Assignment": "assigned",
        "Cash Settled Assignment": "assigned, cash settled",
        "Exercise": "exercised",
        "Cash Settled Exercise": "exercised, cash settled",
        "Buy to Close": "bought back",
        "Sell to Close": "sold",
    }.get(str(ended or ""), str(ended or "closed").lower())


def reading(
    rows: list[dict[str, Any]] | None,
    error: str | None,
    session: date,
    rules: dict[str, Any],
    scope: list[str] | None = None,
    contracts: list[str] | None = None,
) -> dict[str, Any]:
    """The account's ledger: per name and window, by month, every closed span, and what is still held.

    With `scope` (the wheel's names), only those names count, in every total and month; `scope` says so.
    With `contracts` too (every option contract the wheel's cycles traded), an option leg on a scoped
    name counts only when it is one of them: another trade on the same name (a PMCC, a spread) is not
    the wheel's, and its legs are stated apart under `excluded.outside_wheel`. Shares and dividends on
    the name still count. `outside_wheel` counts the closed legs it set aside, as the ledger counts.
    """
    if rows is None or error:
        return {
            "error": error or "no account-wide history in this snapshot",
            "scope": scope,
            "names": [],
            "windows": {},
        }
    zero_dte = {str(s).upper() for s in ((rules.get("exclude") or {}).get("zero_dte_underlyings") or [])}
    spans = _premium.closed_lots(rows, zero_dte, shares=True)
    divs = _dividends(rows)
    outside: list[dict[str, Any]] = []
    if scope is not None:
        keep = set(scope)
        spans = {k: [s for s in v if s["underlying"] in keep] for k, v in spans.items()}
        divs = [d for d in divs if d["underlying"] in keep]
    if contracts is not None:
        wheel_legs = {_sym(c) for c in contracts}

        def wheels(s: dict[str, Any]) -> bool:
            return _right(s) == "S" or _sym(s["symbol"]) in wheel_legs

        # Closed legs only, as everything the ledger counts: a still-held leg of another trade is just
        # not listed under `open`.
        outside = [s for s in spans["lots"] if not wheels(s)]
        spans = {k: ([s for s in v if wheels(s)] if k in ("lots", "open") else v) for k, v in spans.items()}
    windows = [(name, _since(session, name, days)) for name, days in WINDOWS]
    months = _premium._months_back(session, MONTHS)

    per_name: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: {w: _blank() for w, _ in windows})
    account = {w: _blank() for w, _ in windows}
    by_month = {m: {"month": m, **{s: 0.0 for s in SOURCES}, "fees": 0.0, "net": 0.0} for m in months}
    lots_out = []

    def count(name: str, day: date, source: str, net: float, fees: float, legs: int) -> None:
        for w, since in windows:
            if since is None or day >= since:
                _add(per_name[name][w], source, net, fees, legs)
                _add(account[w], source, net, fees, legs)
        m = by_month.get(_premium._month(day))
        if m is not None:
            m[source] += net
            m["fees"] += fees
            m["net"] += net

    for lot in spans["lots"]:
        right = _right(lot)
        fees = lot["net"] - lot["gross"]
        count(lot["underlying"], lot["closed"], _source(right), lot["net"], fees, 1)
        lots_out.append(
            {
                "underlying": lot["underlying"],
                "symbol": lot["symbol"],
                "right": right,
                "opened": lot["opened"].isoformat() if lot["opened"] else None,
                "closed": lot["closed"].isoformat(),
                "how": _how(lot.get("ended")),
                "net": round(lot["net"], 2),
                "fees": round(fees, 2),
            }
        )
    for d in divs:
        count(d["underlying"], d["day"], "dividends", d["net"], 0.0, 0)
        lots_out.append(
            {
                "underlying": d["underlying"],
                "symbol": d["underlying"],
                "right": "D",
                "opened": None,
                "closed": d["day"].isoformat(),
                "how": "dividend",
                "net": round(d["net"], 2),
                "fees": 0.0,
            }
        )
    lots_out.sort(key=lambda r: (r["closed"], r["underlying"], r["symbol"]), reverse=True)

    still = [
        {
            **s,
            "opened": s["opened"].isoformat() if s["opened"] else None,
            "right": _right(s),
            "net": round(s["net"], 2),
        }
        for s in spans["open"]
        if not (
            s["underlying"] in zero_dte
            and s["opened"] is not None
            and _premium._occ_expiry(str(s["symbol"])) == s["opened"]
        )
    ]
    first = min(
        (d for d in (_premium._et_day(r.get("executed_at")) for r in rows) if d is not None), default=None
    )

    def summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {"legs": len(items), "net": round(sum(i["net"] for i in items), 2)}

    names = sorted(
        ({"underlying": n, "windows": {w: _rounded(b) for w, b in ws.items()}} for n, ws in per_name.items()),
        key=lambda x: (-x["windows"]["ALL"]["net"], x["underlying"]),
    )
    return {
        "error": None,
        "scope": scope,
        "history_from": first.isoformat() if first else None,
        "rows": len(rows),
        "windows": {w: _rounded(b) for w, b in account.items()},
        "months": [_rounded(m) for m in by_month.values()],
        "names": names,
        "lots": lots_out,
        "open": still,
        "excluded": {
            "zero_dte": {**summary(spans["zero_dte"]), "underlyings": sorted(zero_dte)},
            "unmatched": summary(spans["unmatched"]),
            "outside_wheel": {
                **summary(outside),
                "underlyings": sorted({s["underlying"] for s in outside}),
            },
        },
    }
