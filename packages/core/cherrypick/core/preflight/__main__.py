"""`python -m cherrypick.core.preflight` -- broker dry-runs of trades under consideration. Places nothing.

    python -m cherrypick.core.preflight --keyring-service meicagent \\
        --trade "SPY Feb 580/560P" 1.61d +.SPY270219P580 -.SPY270219P560 \\
        --trade "SPY Jan/Feb 600P cal" 2.92d -.SPY270115P600 +.SPY270219P600

A trade is a name, a limit price ending in `d` (debit) or `c` (credit), then its legs: `+` buys,
`-` sells, an optional count after the sign (`+2.SPY270219P600`), then an OCC, streamer or share
symbol. `--close` makes every leg a closing one. `--file` reads a JSON list of trades in the
package's own shape instead. The account is a keyring service's designated one, or `--login NAME`
(with `--account LAST4` when that login has several).
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from cherrypick.core import preflight

_LEG = re.compile(r"^([+~-])(\d*)(.+)$")
_PRICE = re.compile(r"^(\d+(?:\.\d+)?)([dc])$", re.IGNORECASE)
#: A sell leg (`-.SPY...`) looks like an option to argparse. Every real option here is `--name`
#: (or `-h`), so a single-dash token is a leg; it is carried through parsing as `~`.
_SELL_TOKEN = re.compile(r"^-(?!-|h$)")


def protect_sells(argv: list[str]) -> list[str]:
    return [_SELL_TOKEN.sub("~", a) for a in argv]


def parse_trade(tokens: list[str], *, closing: bool = False) -> dict:
    if len(tokens) < 3:
        raise ValueError(f"a trade needs a name, a price and at least one leg: {tokens!r}")
    name, price, *legs = tokens
    m = _PRICE.match(price.strip())
    if not m:
        raise ValueError(f"price must look like 1.61d or 0.80c, not {price!r}")
    out_legs = []
    for tok in legs:
        lm = _LEG.match(tok.strip())
        if not lm:
            raise ValueError(f"a leg starts with + or -: {tok!r}")
        side, qty, sym = lm.groups()
        verb = "buy" if side == "+" else "sell"
        out_legs.append(
            {
                "symbol": sym.strip(),
                "action": f"{verb} to {'close' if closing else 'open'}",
                "quantity": int(qty or 1),
            }
        )
    return {
        "name": name,
        "price": float(m.group(1)),
        "price_effect": "debit" if m.group(2).lower() == "d" else "credit",
        "legs": out_legs,
    }


def _money(v) -> str:
    return "-" if v is None else f"${v:,.2f}"


def table(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        if r.get("accepted"):
            lines.append(
                f"{r['name']}: BPR {_money(r.get('bpr'))}  fees {_money(r.get('fees'))}  "
                f"BP {_money(r.get('current_buying_power'))} -> {_money(r.get('new_buying_power'))}"
                + (f"  [{r['account']}]" if r.get("account") else "")
            )
        else:
            lines.append(f"{r['name']}: REFUSED  {r.get('refusal')}")
        for w in r.get("warnings") or []:
            lines.append(f"    warning {w}")
        for e in r.get("errors") or []:
            lines.append(f"    error {e}")
    lines.append("(dry run: nothing was placed)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m cherrypick.core.preflight", description=__doc__.splitlines()[0]
    )
    ap.add_argument("--trade", nargs="+", action="append", metavar="NAME PRICE LEG", help="repeatable")
    ap.add_argument("--file", help="a JSON list of trades")
    ap.add_argument("--close", action="store_true", help="--trade legs close instead of open")
    ap.add_argument("--keyring-service")
    ap.add_argument("--login")
    ap.add_argument("--account", help="with --login: the last 4 of the account")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(protect_sells(list(sys.argv[1:] if argv is None else argv)))

    trades = []
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            trades += list(json.load(f))
    for tokens in args.trade or []:
        trades.append(parse_trade(tokens, closing=args.close))
    if not trades:
        ap.error("give --trade or --file")
    if not (args.keyring_service or args.login):
        ap.error("name the account: --keyring-service or --login")
    broker = preflight.make_broker(
        keyring_service=args.keyring_service, login=args.login, account_last4=args.account
    )
    rows = preflight.review(trades, broker)
    print(json.dumps(rows, indent=1, default=str) if args.json else table(rows))
    return 0 if all(r.get("accepted") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
