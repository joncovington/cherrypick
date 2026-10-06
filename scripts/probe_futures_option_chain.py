"""Probe the broker's futures-option chain endpoints and report what a producer would have to handle.

The streamer's chain path is equity/index only (`core.streamer._fetch_full_chain` calls
`get_option_chain`, i.e. `/option-chains/{symbol}`). Before any producer work for /MNQ
(docs/history/ivan-plan.md, Phase 3), this asks the two futures endpoints the SDK already wraps
and reports the facts that design depends on, rather than assuming them:

  - flat:   `get_future_option_chain`  -> /futures-option-chains/{product}         (one FutureOption per item)
  - nested: `NestedFutureOptionChain`  -> /futures-option-chains/{product}/nested  (futures + subchains)

For each it reports latency and size; settlement per root from the PRODUCT catalogue
(`/instruments/future-option-products` -- the option object's own `settlement_type` reads "Future" even
for cash-settled series), which defined products have nothing listed, and how far out each settlement
style reaches; per option ROOT the underlying future, exercise style and expiration types; which expiry
dates carry MORE THAN ONE root (the window filter would
otherwise mix them); strike spacing and how many put strikes fall in a band below the market (the
subscription cost of the design's put band); streamer-symbol samples and whether `is_option_symbol`
recognises them; and whether a FutureOption carries every field `streamcache.write_chain` reads.

Read-only against the broker: it lists instruments and places nothing. Network-reaching, so it lives in
`scripts/` with `refresh_futures_contracts.py`, never in a package. Writes nothing unless `--dump` is
given. The reference price is the /NQ leg already in the stream cache (MNQ and NQ track one index), so
no quote is requested from the broker.

    python scripts/probe_futures_option_chain.py [--product MNQ --product NQ] [--dump DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from cherrypick.core import home as _home
from cherrypick.core import streamcache
from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

# The DTE spans docs/history/ivan-plan.md would select from: near18 (21-45), control/nodip (45-60), and the
# roll target (out to max_roll_dte).
DTE_SPANS = {"near18": (21, 45), "control": (45, 60), "roll": (90, 180)}
# Put-band depths below the reference price, as a fraction of it: the entry band's deep edge and
# the roll band's.
BAND_DEPTHS = (0.10, 0.15, 0.25)
# What `streamcache.write_chain` / `atm_window_syms` read off a chain object.
CHAIN_FIELDS = ("strike_price", "expiration_date", "streamer_symbol", "underlying_symbol", "option_type")


def _reference_price() -> tuple[float | None, str | None]:
    """The /NQ front contract's last trade from the stream cache, read-only. (None, None) when the
    cache or the futures map cannot answer -- the strike-band figures are then skipped, not guessed."""
    try:
        fmap = json.loads((_home.state_dir() / "futures_contracts.json").read_text(encoding="utf-8"))
        sym = fmap["contracts"]["NQ"][0]["streamer_symbol"]
    except (OSError, ValueError, KeyError, IndexError):
        return None, None
    cache = _home.data_dir("marketdata") / "stream_cache.db"
    try:
        conn = sqlite3.connect(f"file:{cache}?mode=ro", uri=True)
        row = conn.execute("SELECT last FROM stream_trades WHERE symbol = ?", (sym,)).fetchone()
        conn.close()
    except sqlite3.Error:
        return None, sym
    return (float(row[0]) if row and row[0] is not None else None), sym


def _spacing(strikes: list[float], ref: float | None) -> dict:
    """Strike increments overall and within 3% of the reference price."""
    s = sorted(set(strikes))
    out = {
        "count": len(s),
        "min": s[0] if s else None,
        "max": s[-1] if s else None,
        "steps": dict(_steps(s).most_common(4)),
    }
    if ref and s:
        near = [k for k in s if abs(k - ref) <= ref * 0.03]
        out["steps_near_ref"] = dict(_steps(near).most_common(3))
        out["puts_in_band"] = {
            f"{int(d * 100)}%": sum(1 for k in s if ref * (1 - d) <= k <= ref) for d in BAND_DEPTHS
        }
    return out


def _steps(strikes: list[float]) -> Counter:
    return Counter(round(b - a, 4) for a, b in zip(strikes, strikes[1:], strict=False))


def _is_put(option) -> bool:
    kind = getattr(option.option_type, "value", option.option_type)
    return str(kind).upper().startswith("P")


def product_catalogue(products) -> dict[str, dict]:
    """Root symbol -> what the PRODUCT says about it. This, not the option object, is the authority on
    settlement: on 2026-09-30 every MNQ option reported `settlement_type: "Future"`, including the
    financially settled MN1A-MN5E series whose product reads `cash_settled: true`."""
    return {
        p.root_symbol: {
            "future": p.future_product.code if p.future_product else None,
            "cash_settled": p.cash_settled,
            "product_type": p.product_type,
            "expiration_type": p.expiration_type,
        }
        for p in products
    }


def settlement_view(chain: dict, product: str, catalogue: dict[str, dict]) -> dict:
    """Which of this future's option products are listed, which are defined but have nothing listed (a
    series being wound down), and how far out each settlement style reaches."""
    listed: dict[str, date] = {}
    for exp, options in chain.items():
        for o in options:
            root = o.option_root_symbol
            listed[root] = max(listed.get(root, exp), exp)
    defined = sorted(r for r, c in catalogue.items() if c["future"] == product)
    furthest: dict[str, str] = {}
    for root, last in listed.items():
        kind = "cash" if catalogue.get(root, {}).get("cash_settled") else "physical"
        if catalogue.get(root) is None:
            kind = "unknown"
        if kind not in furthest or last.isoformat() > furthest[kind]:
            furthest[kind] = last.isoformat()
    return {
        "listed_roots": {r: catalogue.get(r, {}).get("cash_settled") for r in sorted(listed)},
        "defined_but_unlisted": [r for r in defined if r not in listed],
        "furthest_expiry_by_settlement": furthest,
    }


def summarise_flat(chain: dict, ref: float | None, today: date) -> dict:
    by_root: dict[str, dict] = {}
    roots_per_date: dict[str, set] = defaultdict(set)
    missing_fields: Counter = Counter()
    samples: list[str] = []
    for exp, options in chain.items():
        for o in options:
            root = o.option_root_symbol
            roots_per_date[exp.isoformat()].add(root)
            r = by_root.setdefault(
                root,
                {
                    "underlyings": set(),
                    "exercise": set(),
                    "settlement": set(),
                    "expiries": set(),
                    "multiplier": set(),
                    "options": 0,
                },
            )
            r["underlyings"].add(o.underlying_symbol)
            r["exercise"].add(o.exercise_style)
            r["settlement"].add(o.settlement_type)
            r["expiries"].add(exp.isoformat())
            r["multiplier"].add(str(o.multiplier))
            r["options"] += 1
            for f in CHAIN_FIELDS:
                if getattr(o, f, None) in (None, ""):
                    missing_fields[f] += 1
            if len(samples) < 4 and o.streamer_symbol:
                samples.append(o.streamer_symbol)

    roots = {
        root: {
            "options": r["options"],
            "underlyings": sorted(r["underlyings"]),
            "exercise_style": sorted(r["exercise"]),
            "settlement_type": sorted(r["settlement"]),
            "multiplier": sorted(r["multiplier"]),
            "expiries": len(r["expiries"]),
            "nearest": min(r["expiries"]),
        }
        for root, r in sorted(by_root.items())
    }
    shared = {d: sorted(rs) for d, rs in sorted(roots_per_date.items()) if len(rs) > 1}

    spans: dict[str, list] = {}
    for name, (lo, hi) in DTE_SPANS.items():
        rows = []
        for exp, options in sorted(chain.items()):
            dte = (exp - today).days
            if not lo <= dte <= hi:
                continue
            per_root: dict[str, list[float]] = defaultdict(list)
            underlying: dict[str, str] = {}
            for o in options:
                if _is_put(o):
                    per_root[o.option_root_symbol].append(float(o.strike_price))
                    underlying[o.option_root_symbol] = o.underlying_symbol
            for root, strikes in sorted(per_root.items()):
                rows.append(
                    {
                        "expiration": exp.isoformat(),
                        "dte": dte,
                        "root": root,
                        "underlying": underlying[root],
                        "puts": _spacing(strikes, ref),
                    }
                )
        spans[name] = rows

    return {
        "roots": roots,
        "dates_with_multiple_roots": {"count": len(shared), "examples": dict(list(shared.items())[:6])},
        "chain_fields_missing": dict(missing_fields),
        "streamer_symbol_samples": samples,
        "is_option_symbol": {s: streamcache.is_option_symbol(s) for s in samples},
        "dte_spans": spans,
    }


def summarise_nested(nested) -> dict:
    futures = [
        {
            "symbol": f.symbol,
            "expiration": f.expiration_date.isoformat(),
            "active_month": f.active_month,
            "next_active_month": f.next_active_month,
        }
        for f in sorted(nested.futures, key=lambda f: f.expiration_date)
    ]
    subchains = []
    for sc in nested.option_chains:
        types = Counter(e.expiration_type for e in sc.expirations)
        settle = Counter(e.settlement_type for e in sc.expirations)
        roots = Counter(e.option_root_symbol for e in sc.expirations)
        sample = sc.expirations[0] if sc.expirations else None
        subchains.append(
            {
                "underlying_symbol": sc.underlying_symbol,
                "root_symbol": sc.root_symbol,
                "exercise_style": sc.exercise_style,
                "expirations": len(sc.expirations),
                "expiration_types": dict(types),
                "settlement_types": dict(settle),
                "option_roots": dict(roots),
                "sample_strike": (
                    {
                        "strike": str(sample.strikes[0].strike_price),
                        "put": sample.strikes[0].put,
                        "put_streamer_symbol": sample.strikes[0].put_streamer_symbol,
                    }
                    if sample and sample.strikes
                    else None
                ),
                "notional_value": str(sample.notional_value) if sample else None,
            }
        )
    return {"futures": futures, "subchains": subchains}


async def probe(session, product: str, ref: float | None, dump: Path | None, catalogue: dict) -> dict:
    from tastytrade.instruments import NestedFutureOptionChain, get_future_option_chain

    out: dict = {"product": product}
    today = date.today()

    t0 = time.perf_counter()
    try:
        flat = await get_future_option_chain(session, product)
        out["flat"] = {
            "seconds": round(time.perf_counter() - t0, 2),
            "expirations": len(flat),
            "options": sum(len(v) for v in flat.values()),
            "settlement": settlement_view(flat, product, catalogue),
            **summarise_flat(flat, ref, today),
        }
    except Exception as exc:  # noqa: BLE001 -- a probe reports a failure, it does not hide it
        out["flat"] = {"error": f"{type(exc).__name__}: {exc}", "seconds": round(time.perf_counter() - t0, 2)}

    t0 = time.perf_counter()
    try:
        nested = await NestedFutureOptionChain.get(session, product)
        out["nested"] = {"seconds": round(time.perf_counter() - t0, 2), **summarise_nested(nested)}
        if dump:
            dump.mkdir(parents=True, exist_ok=True)
            (dump / f"{product}_nested.json").write_text(nested.model_dump_json(indent=1), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        out["nested"] = {
            "error": f"{type(exc).__name__}: {exc}",
            "seconds": round(time.perf_counter() - t0, 2),
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", action="append", help="futures product code (default: MNQ and NQ)")
    ap.add_argument("--dump", type=Path, help="also write each nested response as JSON into this directory")
    args = ap.parse_args(argv)
    products = [p.lstrip("/").upper() for p in (args.product or ["MNQ", "NQ"])]

    store = CredentialStore(SHARED_SERVICE)
    missing = store.missing_secrets()
    if missing:
        print(json.dumps({"ok": False, "reason": "credentials_missing", "missing": list(missing)}))
        return 1

    ref, ref_symbol = _reference_price()
    try:
        session = SessionManager(store).get_session()

        async def run_all():
            from tastytrade.instruments import FutureOptionProduct

            catalogue = product_catalogue(await FutureOptionProduct.get(session))
            return [await probe(session, p, ref, args.dump, catalogue) for p in products]

        results = asyncio.run(run_all())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}))
        return 1

    print(
        json.dumps(
            {"ok": True, "reference": {"symbol": ref_symbol, "last": ref}, "results": results},
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
