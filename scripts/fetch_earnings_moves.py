"""Next week's earnings for the market report, each with the move its options imply.

Phase 5 of docs/market-report-plan.md. For every stock the technicals store holds (the universe
candidates and the captured names) with an announcement in the next `DAYS` calendar days on the
local Dolt earnings calendar, this prices the at-the-money straddle on the first expiration the
market can trade the print on, and turns it into an expected move with the suite's one definition,
`cherrypick.core.structures.expected_move` (0.85 x the straddle mid).

**The expiration is the earnings module's rule, copied not reinvented:** the reaction date is the
report day for "Before market open" and the next day for "After market close", and the expiration
is the first on or after it (`packages/earnings`, `scanner.select_front_expiration`). A same-day
expiration for an after-close print expires before the market can react, which that module learned
live. The copy lives here because the technicals and overview CI jobs do not install the earnings
package; a test pins the two equal wherever both are importable.

A script, not package code: it reads the broker (chains and quotes, REST, read-only). It writes
`~/.cherrypick/data/market-report/earnings/week.json`, which the morning pack reads. One chain
request per name, a second apart; a name whose options cannot be priced is listed with the reason.

    python scripts/fetch_earnings_moves.py [--days 7]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

DAYS = 7
PAUSE_S = 1.0
AMC = "After market close"


def reaction_date(earnings_date: date, when: str | None) -> date:
    """The earnings module's rule: an after-close print is traded the next day."""
    return earnings_date + timedelta(days=1) if when == AMC else earnings_date


def front_expiration(expirations: list[date], earnings_date: date, when: str | None) -> date | None:
    rd = reaction_date(earnings_date, when)
    eligible = [e for e in expirations if e >= rd]
    return min(eligible) if eligible else None


def move_row(symbol, earnings_date, when, expiration, spot, strike, call, put) -> dict:
    """One name's row from its straddle quotes (each {bid, ask}); the reason when unpriceable."""
    from cherrypick.core.structures import expected_move

    row = {
        "symbol": symbol,
        "date": earnings_date.isoformat(),
        "when": when,
        "expiration": expiration.isoformat() if expiration else None,
        "spot": spot,
        "strike": strike,
        "expected_move": None,
        "expected_move_pct": None,
        "reason": None,
    }

    def mid(q):
        b, a = (q or {}).get("bid"), (q or {}).get("ask")
        return (b + a) / 2 if b and a and a >= b else None

    cm, pm = mid(call), mid(put)
    if expiration is None:
        row["reason"] = "no expiration after the event"
    elif not spot:
        row["reason"] = "no underlying quote"
    elif cm is None or pm is None:
        row["reason"] = "straddle not two-sided"
    else:
        em = expected_move(cm, pm)
        row.update(
            expected_move=round(em, 2),
            expected_move_pct=round(100 * em / spot, 2),
            straddle=round(cm + pm, 2),
        )
    return row


def out_path() -> Path:
    from cherrypick.core import home

    return home.data_dir("market-report") / "earnings" / "week.json"


def upcoming(days: int, today: date) -> list[tuple[str, date, str | None]]:
    """(symbol, date, when) for the store's stocks with an announcement in the window."""
    import mysql.connector
    from cherrypick.technicals import liquidity, store, symbols

    conn = store.connect()
    # A listed view that prices options: names held illiquid are neither priced nor listed.
    wanted = set(store.stocks(conn, symbols.all_symbols())) - liquidity.illiquid()
    conn.close()
    cn = mysql.connector.connect(
        host="127.0.0.1", port=3306, user="root", database="earnings", connection_timeout=15
    )
    try:
        cur = cn.cursor()
        cur.execute(
            "SELECT act_symbol, date, `when` FROM earnings_calendar "
            "WHERE date >= %s AND date <= %s ORDER BY date",
            (today, today + timedelta(days=days)),
        )
        return [(s, d, w) for s, d, w in cur.fetchall() if s in wanted]
    finally:
        cn.close()


async def price(session, events) -> list[dict]:
    from tastytrade.instruments import NestedOptionChain
    from tastytrade.market_data import get_market_data_by_type

    rows = []
    spots = {}
    syms = sorted({s for s, _, _ in events})
    for i in range(0, len(syms), 100):
        for q in await get_market_data_by_type(
            session, equities=[s.replace(".", "/") for s in syms[i : i + 100]]
        ):
            # The session's CLOSE, not the live mid: this runs after the close, when the equity book
            # is the overnight one (a weekend NMR read 9.55 x 10.98), while the option quotes are the
            # session's closing ones. A straddle priced against an overnight spot mixes two markets.
            close = q.close or q.prev_close
            if close:
                spots[q.symbol.replace("/", ".")] = float(close)
    for n, (sym, d, when) in enumerate(events):
        if n:
            await asyncio.sleep(PAUSE_S)
        spot = spots.get(sym)
        try:
            chains = await NestedOptionChain.get(session, sym.replace(".", "/"))
        except Exception as exc:  # noqa: BLE001 -- listed with its reason, never dropped
            rows.append(
                {**move_row(sym, d, when, None, spot, None, None, None), "reason": f"chain: {exc}"[:120]}
            )
            continue
        chain = next(
            (
                c
                for c in (chains if isinstance(chains, list) else [chains])
                if c.root_symbol == sym.replace(".", "/")
            ),
            None,
        )
        exps = {e.expiration_date: e for e in (chain.expirations if chain else [])}
        exp = front_expiration(sorted(exps), d, when)
        call = put = strike = None
        if exp and spot:
            s = min(exps[exp].strikes, key=lambda x: abs(float(x.strike_price) - spot))
            strike = float(s.strike_price)
            quotes = {q.symbol: q for q in await get_market_data_by_type(session, options=[s.call, s.put])}
            call = (
                {"bid": float(quotes[s.call].bid or 0), "ask": float(quotes[s.call].ask or 0)}
                if s.call in quotes
                else None
            )
            put = (
                {"bid": float(quotes[s.put].bid or 0), "ask": float(quotes[s.put].ask or 0)}
                if s.put in quotes
                else None
            )
        rows.append(move_row(sym, d, when, exp, spot, strike, call, put))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--days", type=int, default=DAYS)
    args = ap.parse_args(argv)
    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

    store_ = CredentialStore(SHARED_SERVICE)
    if store_.missing_secrets():
        print(json.dumps({"ok": False, "reason": "credentials_missing"}))
        return 1
    today = datetime.now(UTC).astimezone().date()
    try:
        events = upcoming(args.days, today)
        rows = asyncio.run(price(SessionManager(store_).get_session(), events)) if events else []
    except Exception as exc:  # noqa: BLE001 -- the previous file stays; say why
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
        return 1
    doc = {
        "generated_at": datetime.now(UTC).isoformat(),
        "from": today.isoformat(),
        "days": args.days,
        "rows": rows,
    }
    path = out_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tmp.replace(path)
    print(
        json.dumps(
            {
                "ok": True,
                "events": len(rows),
                "priced": sum(1 for r in rows if r["expected_move"]),
                "path": str(path),
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
