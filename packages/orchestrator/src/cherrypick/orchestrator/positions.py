"""`cherrypick positions` — live P/L by underlying for the real broker account.

The suite's dashboards are all paper: every module's read model answers "what did my strategy do",
priced from its own book. None of them answer "what is the real account holding right now, and what is
it worth" — that question was being answered by hand, one broker call at a time, and the arithmetic was
redone (and mis-done) each time. This is that question as one command.

Like `reconcile` and `doctor`, this is an on-demand, broker-touching **read**: it enumerates accounts,
reads positions and balances, prices them, and never places, cancels, or modifies an order. It is
deliberately NOT on the watchdog reliability path and never scheduled — nothing downstream depends on
it, so a broker outage costs a report, not a loop. Account numbers are masked (`****1234`).

**Marks come from the stream cache first, the feed second** (the suite-wide rule). The cache is the
suite's single producer of market data and already carries every symbol some module declared; only the
symbols it does not hold — a discretionary position, typically — cost a live fetch, and that fetch goes
through the module's broker tool by subprocess, exactly as every other broker call here does. Nothing
in this path writes the shared cache: undeclared symbols seeded into it would leave rows no daemon
refreshes, stale with no owner to notice.

**Two honesty rules the numbers here obey**, because a P/L report that breaks either is worse than
none:

- *A mid is not a fill.* Every value is marked at the midpoint, and the midpoint of a $2.30-wide market
  is a fiction. Legs whose bid/ask spread is wide relative to their price are flagged, and the flag
  travels with the underlying's totals, so "APO is down $125" is never read without "and its mark is
  worth ±$115 of doubt".
- *An unpriced leg is not a free leg.* A position the cache and the feed both fail to price is reported
  as unpriced and excluded from the totals, with the count stated. Silently treating it as zero would
  understate risk in exactly the case where something is wrong.
"""

from __future__ import annotations

import time
from typing import Any

from cherrypick.core import streamcache
from cherrypick.core.db import connect_ro
from cherrypick.core.home import data_dir

from . import config as cfgmod
from . import reconcile

# How old a cached quote may be before this report refetches it live. Deliberately its own bound rather
# than the cache module's trading default, and measured against the cache rather than assumed: on
# 2026-08-18 the shared cache held 19,685 quote rows of which only ~900 were fresher than a minute, the
# rest being residue for expirations no module subscribes to any more (median age about a week). The
# first draft of this report inherited a 10-second bound and got zero cache hits; loosening it without
# measuring would have gone the other way and priced four SPY legs off 21-hour-old rows, which is
# exactly the failure that matters — a stale quote does not announce itself, it just quietly reads as
# live. A minute is long enough to hit the symbols the producer is actually streaming and far too short
# for yesterday's to survive.
REPORT_QUOTE_MAX_AGE_SECONDS = 60.0
# A leg is "wide" when its bid/ask spread exceeds this fraction of its mid. Chosen from what the marks
# are used for rather than from a market convention: below roughly a tenth, mid-vs-fill is small against
# the P/L being reported, and above it the mid stops being a number worth quoting a decision on.
WIDE_SPREAD_RATIO = 0.10
# ...but a ratio alone flags every cheap leg (a 0.01/0.03 far wing is 100% wide and $2 of doubt), so a
# leg must also carry this much absolute spread per contract to be worth a reader's attention.
WIDE_SPREAD_MIN_DOLLARS = 0.10


def _cache_db_path():
    """The shared stream cache. Same default every consumer uses; this reader never creates it."""
    return data_dir("marketdata") / "stream_cache.db"


def _num(value: Any) -> float | None:
    """Broker payloads quote every number as a string. None for anything unparseable."""
    if value is None:
        return None
    try:
        out = float(value)
        return None if out != out else out
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- marks
def _cached_marks(symbols: list[str]) -> dict[str, dict]:
    """Fresh marks for whatever the shared cache already holds. Read-only, and absent/stale/unreadable
    all mean the same thing to the caller: fetch it live instead."""
    path = _cache_db_path()
    if not path.exists():
        return {}
    try:
        conn = connect_ro(path)
    except Exception:  # noqa: BLE001 — an unreadable cache is a fetch-live signal, not a failure
        return {}
    try:
        return streamcache.quote_mids(conn, symbols, max_age_seconds=REPORT_QUOTE_MAX_AGE_SECONDS)
    finally:
        conn.close()


def _live_marks(root, symbols: list[str], tool: list[str] | None) -> dict[str, dict]:
    """Marks for the symbols the cache could not answer, via the module's broker tool by subprocess.

    Batched into one call: the feed subscribes to all of them at once, so per-symbol calls would pay
    the subscription round-trip repeatedly for no better answer.
    """
    if not symbols:
        return {}
    payload = reconcile._tt(root, "get_quotes", "--symbols", *symbols, tool=tool)
    if not payload.get("ok"):
        return {}
    out: dict[str, dict] = {}
    for sym, quote in (payload.get("quotes") or {}).items():
        if quote.get("mid") is None:
            continue
        out[sym] = {
            "bid": _num(quote.get("bid")),
            "ask": _num(quote.get("ask")),
            "mid": _num(quote.get("mid")),
            "age_seconds": 0.0,
            "source": "feed",
        }
    return out


# --------------------------------------------------------------------------- per-leg arithmetic
def _price_leg(position: dict, mark: dict | None) -> dict:
    """One position priced at its mark, with the sign conventions stated rather than implied.

    `quantity` from the broker is unsigned and the direction is a separate field, so the sign is applied
    here once: a short leg's value and P/L both move against the mark. `average_open_price` is per
    share/contract and already net of the direction, so the same signed multiplier serves both the
    open-to-now and the close-to-now (day) figures.
    """
    occ = str(position.get("symbol") or "")
    is_option = str(position.get("instrument_type") or "") == "Equity Option"
    direction = str(position.get("quantity_direction") or "")
    sign = -1 if direction == "Short" else 1
    quantity = _num(position.get("quantity")) or 0.0
    multiplier = (_num(position.get("multiplier")) or 1.0) if is_option else 1.0
    open_price = _num(position.get("average_open_price"))
    prev_close = _num(position.get("close_price"))
    contracts = sign * quantity

    leg: dict[str, Any] = {
        "symbol": occ,
        "underlying": str(position.get("underlying_symbol") or occ),
        "instrument_type": position.get("instrument_type"),
        "quantity": contracts,
        "expiration": str(position.get("expires_at") or "")[:10] or None,
        "open_price": open_price,
        "prev_close": prev_close,
        "priced": False,
    }
    if is_option and len(occ) >= 19:
        leg["right"] = occ[12]
        strike = _num(occ[13:])
        leg["strike"] = strike / 1000.0 if strike is not None else None

    if mark is None or mark.get("mid") is None:
        return leg

    mid = mark["mid"]
    bid, ask = mark.get("bid"), mark.get("ask")
    spread = (ask - bid) if (bid is not None and ask is not None) else None
    leg.update(
        {
            "priced": True,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "mark_source": mark.get("source"),
            "mark_age_seconds": mark.get("age_seconds"),
            "value": contracts * multiplier * mid,
            "open_pl": (contracts * multiplier * (mid - open_price)) if open_price is not None else None,
            "day_pl": (contracts * multiplier * (mid - prev_close)) if prev_close is not None else None,
            "spread": spread,
            # What the mid could be wrong by if this leg had to trade at the bad side of its own market.
            "mark_doubt": (abs(contracts) * multiplier * spread / 2.0) if spread is not None else None,
            "wide": bool(
                spread is not None
                and spread >= WIDE_SPREAD_MIN_DOLLARS
                and mid > 0
                and (spread / mid) >= WIDE_SPREAD_RATIO
            ),
        }
    )
    return leg


def _sum(values) -> float:
    return sum(v for v in values if v is not None)


def _group_by_underlying(legs: list[dict]) -> list[dict]:
    """Legs collapsed to one row per underlying, sorted by absolute open P/L.

    Sorted by magnitude rather than signed value because the reader's question is "what needs my
    attention", and a $200 loser and a $200 winner are equally worth seeing before a $3 anything.
    """
    groups: dict[str, dict] = {}
    for leg in legs:
        group = groups.setdefault(
            leg["underlying"],
            {"underlying": leg["underlying"], "legs": [], "unpriced": 0},
        )
        group["legs"].append(leg)
        if not leg["priced"]:
            group["unpriced"] += 1
    out = []
    for group in groups.values():
        priced = [leg for leg in group["legs"] if leg["priced"]]
        group.update(
            {
                "leg_count": len(group["legs"]),
                "value": _sum(leg.get("value") for leg in priced),
                "open_pl": _sum(leg.get("open_pl") for leg in priced),
                "day_pl": _sum(leg.get("day_pl") for leg in priced),
                "mark_doubt": _sum(leg.get("mark_doubt") for leg in priced),
                "wide": any(leg.get("wide") for leg in priced),
            }
        )
        out.append(group)
    return sorted(out, key=lambda g: -abs(g["open_pl"]))


# --------------------------------------------------------------------------- entrypoint
def run(cfg: dict[str, Any] | None = None, *, account: str | None = None) -> dict[str, Any]:
    """Price every position on the real login and group the result by underlying.

    `account` filters to one account by its last 4 digits, matching how the desk's allowlist and every
    masked report refer to accounts — a caller should never need the full number to ask for a report
    about it.
    """
    cfg = cfgmod.load_config() if cfg is None else cfg
    forced = (cfg.get("reconcile") or {}).get("broker_module")
    broker = reconcile._query_broker(cfg, forced)
    if not broker.get("reachable"):
        return {
            "ok": False,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "error": broker.get("detail") or "broker unreachable",
            "accounts": [],
        }

    module = broker["module"]
    mcfg = cfgmod.enabled_modules(cfg)[module]
    root = cfgmod.module_root(mcfg, module)
    tool = cfgmod.broker_tool(mcfg, module)

    entries = broker.get("accounts") or []
    if account:
        wanted = str(account)[-4:]
        entries = [e for e in entries if str(e.get("account", "")).endswith(wanted)]

    # One market-data pass for every account at once: the same underlying can appear in two accounts,
    # and quoting it twice would cost a second subscription for an identical answer.
    positions = [(entry, pos) for entry in entries for pos in (entry.get("open_positions") or [])]
    wanted_syms: dict[str, str] = {}
    for _entry, pos in positions:
        occ = str(pos.get("symbol") or "")
        streamer_sym = streamcache.occ_to_streamer_symbol(occ) or occ.strip()
        wanted_syms[occ] = streamer_sym
    unique = sorted(set(wanted_syms.values()))
    marks = _cached_marks(unique)
    from_cache = len(marks)
    marks.update(_live_marks(root, [s for s in unique if s not in marks], tool))

    accounts_out = []
    for entry in entries:
        legs = [
            _price_leg(pos, marks.get(wanted_syms[str(pos.get("symbol") or "")]))
            for pos in (entry.get("open_positions") or [])
        ]
        priced = [leg for leg in legs if leg["priced"]]
        accounts_out.append(
            {
                "account": entry.get("account"),
                "designated": entry.get("designated"),
                "error": entry.get("error"),
                "balances": entry.get("balances") or {},
                "underlyings": _group_by_underlying(legs),
                "leg_count": len(legs),
                "unpriced_count": len(legs) - len(priced),
                "value": _sum(leg.get("value") for leg in priced),
                "open_pl": _sum(leg.get("open_pl") for leg in priced),
                "day_pl": _sum(leg.get("day_pl") for leg in priced),
                "mark_doubt": _sum(leg.get("mark_doubt") for leg in priced),
            }
        )

    return {
        "ok": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "broker_module": module,
        "marks": {
            "requested": len(unique),
            "from_cache": from_cache,
            "from_feed": len(marks) - from_cache,
            "unpriced": len(unique) - len(marks),
            "max_age_seconds": REPORT_QUOTE_MAX_AGE_SECONDS,
            # The oldest mark that actually priced something, so a reader can see how current the report
            # is instead of inferring it from the bound. Under the bound by construction; printed anyway
            # because "all marks within 60s" and "all marks within 1s" support different decisions.
            "oldest_age_seconds": max((m.get("age_seconds") or 0.0 for m in marks.values()), default=0.0),
        },
        "accounts": accounts_out,
    }


# --------------------------------------------------------------------------- rendering
def _money(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value:+,.0f}" if signed else f"{value:,.0f}"


def _leg_label(leg: dict) -> str:
    if leg.get("strike") is None:
        return "shares"
    exp = (leg.get("expiration") or "")[2:]
    strike = leg["strike"]
    return f"{exp} {strike:g}{leg.get('right') or ''}"


def format_report(result: dict[str, Any], *, detail: bool = False) -> str:
    """The text report. `detail` expands each underlying into its legs."""
    lines = ["cherrypick positions - live P/L by underlying", "=" * 60]
    if not result.get("ok"):
        lines.append(f"broker unreachable: {result.get('error')}")
        return "\n".join(lines)

    marks = result.get("marks") or {}
    lines.append(
        f"marks: {marks.get('from_cache', 0)} from stream cache, {marks.get('from_feed', 0)} live"
        + (f", {marks['unpriced']} UNPRICED" if marks.get("unpriced") else "")
        + f"   oldest {marks.get('oldest_age_seconds', 0):.0f}s"
        + f"   (via {result.get('broker_module')})"
    )

    for acct in result.get("accounts") or []:
        lines.append("-" * 60)
        if acct.get("error"):
            lines.append(f"account {acct['account']}: {acct['error']}")
            continue
        head = f"account {acct['account']}"
        if acct.get("designated"):
            lines.append(f"{head} (live - designated)")
        else:
            lines.append(head)
        balances = acct.get("balances") or {}
        netliq = next((v for k, v in balances.items() if "net-liquidating-value" in str(k).lower()), None)
        if netliq is not None:
            lines.append(f"  net liq {_money(_num(netliq))}")
        lines.append(
            f"  {acct['leg_count']} leg(s)   mark value {_money(acct['value'])}"
            f"   open P/L {_money(acct['open_pl'], signed=True)}"
            f"   day P/L {_money(acct['day_pl'], signed=True)}"
        )
        if acct.get("mark_doubt"):
            lines.append(f"  mid-vs-natural doubt across the book: +/-{_money(acct['mark_doubt'])}")
        if acct.get("unpriced_count"):
            lines.append(
                f"  WARNING {acct['unpriced_count']} leg(s) could not be priced"
                " - EXCLUDED from the totals above"
            )
        lines.append("")
        lines.append(f"  {'UNDERLYING':<12}{'OPEN P/L':>11}{'DAY':>10}{'VALUE':>11}{'LEGS':>6}  FLAGS")
        for group in acct.get("underlyings") or []:
            flags = []
            if group.get("wide"):
                flags.append(f"wide (+/-{_money(group['mark_doubt'])})")
            if group.get("unpriced"):
                flags.append(f"{group['unpriced']} unpriced")
            lines.append(
                f"  {group['underlying']:<12}{_money(group['open_pl'], signed=True):>11}"
                f"{_money(group['day_pl'], signed=True):>10}{_money(group['value']):>11}"
                f"{group['leg_count']:>6}  {', '.join(flags)}"
            )
            if not detail:
                continue

            def _leg_order(leg: dict):
                """Expiry, then calls before puts, then strike — how a spread reads on a ticket."""
                return (leg.get("expiration") or "", leg.get("right") or "", leg.get("strike") or 0)

            for leg in sorted(group["legs"], key=_leg_order):
                if not leg["priced"]:
                    lines.append(f"      {leg['quantity']:+.0f} {_leg_label(leg):<14} UNPRICED")
                    continue
                bid, ask = leg.get("bid"), leg.get("ask")
                book = f"({bid:.2f}/{ask:.2f})" if bid is not None and ask is not None else ""
                lines.append(
                    f"      {leg['quantity']:+.0f} {_leg_label(leg):<14}"
                    f" open {leg['open_price'] or 0:>7.2f}  mid {leg['mid']:>7.2f} {book:<16}"
                    f" P/L {_money(leg.get('open_pl'), signed=True):>8}"
                    f"  day {_money(leg.get('day_pl'), signed=True):>7}"
                    + ("  WIDE" if leg.get("wide") else "")
                )
        lines.append("")

    lines.append("=" * 60)
    lines.append("Marked at the midpoint. A mid is not a fill: flagged rows carry the stated doubt.")
    return "\n".join(lines)
