"""A stored snapshot moved to newer quotes, between full snapshots (2026-11-02).

A full snapshot is about a minute of broker reads (positions, history, chains, greeks, orders) and runs
every 30 minutes. Between them the prices move and little else does, and the stream cache already
quotes every held leg and underlying (`stream_request`). So once a minute the orchestrator reads those
quotes into an **overlay** (`manage-marks`), and this moves the day's stored snapshot to it:

- each held leg the overlay quotes is re-priced at that quote by `core.legmarks.apply_mark`, the same
  arithmetic the full snapshot priced it with; a leg the overlay does not quote keeps the snapshot's
  mark, its age and source as they were;
- each underlying's (and the benchmark's) `last` moves to its quoted mid, since every rule reads spot
  from there;
- the underlyings' and accounts' totals are summed again.

Nothing else moves: positions, history, chains, greeks, orders and balances stay the snapshot's until
the next full one. The result carries `remark` saying when, against which snapshot and how much moved.

A pure function of snapshot and overlay. The overlay is stored beside the evaluation it produced
(`marks-<session>.json`), so `reevaluate` gives the same answer twice; the snapshot is never rewritten.
"""

from __future__ import annotations

import copy

from cherrypick.core import legmarks
from cherrypick.core.streamcache import occ_to_streamer_symbol


class StaleOverlay(ValueError):
    """An overlay read against another snapshot than the one stored for its session."""


def streamer_symbol(symbol: str) -> str:
    """The streamer's spelling of a held leg: the option's dxfeed symbol, or the share's own."""
    return occ_to_streamer_symbol(symbol) or str(symbol).strip()


def wanted(snapshot: dict) -> list[str]:
    """Every streamer symbol a re-mark of `snapshot` can use: its legs and its quoted underlyings."""
    out = {streamer_symbol(leg["symbol"]) for leg in _legs(snapshot) if leg.get("symbol")}
    out |= {str(s) for s in snapshot.get("metrics") or {}}
    return sorted(out)


def _legs(snapshot: dict) -> list[dict]:
    return [
        leg
        for account in (snapshot.get("positions") or {}).get("accounts") or []
        for u in account.get("underlyings") or []
        for leg in u.get("legs") or []
    ]


def _multiplier(leg: dict) -> float:
    """The leg's multiplier as its snapshot priced it: stated on legs from 2026-11-02, read back from a
    priced leg's value before that, an option's 100 or a share's 1 for an older leg never priced."""
    if leg.get("multiplier"):
        return float(leg["multiplier"])
    qty, mid, value = leg.get("quantity"), leg.get("mid"), leg.get("value")
    if leg.get("priced") and qty and mid and value is not None:
        return abs(value / (qty * mid))
    return 100.0 if leg.get("right") else 1.0


def apply(snapshot: dict, overlay: dict) -> dict:
    """`snapshot` moved to `overlay`'s quotes, as a new snapshot; `snapshot` is not changed."""
    if overlay.get("session") != snapshot.get("session") or overlay.get("base_generated_at") != snapshot.get(
        "generated_at"
    ):
        raise StaleOverlay(
            f"the marks were read against the {overlay.get('base_generated_at')} snapshot, the stored one is "
            f"{snapshot.get('generated_at')}"
        )
    quotes = {s: q for s, q in (overlay.get("quotes") or {}).items() if (q or {}).get("mid") is not None}
    out = copy.deepcopy(snapshot)
    moved = kept = 0
    for account in (out.get("positions") or {}).get("accounts") or []:
        legs = [leg for u in account.get("underlyings") or [] for leg in u.get("legs") or []]
        for leg in legs:
            quote = quotes.get(streamer_symbol(leg.get("symbol") or ""))
            if quote is None:
                kept += 1
                continue
            legmarks.apply_mark(
                leg,
                quote,
                contracts=float(leg.get("quantity") or 0.0),
                multiplier=_multiplier(leg),
                open_price=leg.get("open_price"),
                prev_close=leg.get("prev_close"),
            )
            moved += 1
        account["underlyings"] = legmarks.group_by_underlying(legs)
        account.update(legmarks.totals(legs))
    spots = 0
    for symbol, metric in (out.get("metrics") or {}).items():
        quote = quotes.get(str(symbol))
        if quote is not None and isinstance(metric, dict):
            metric["last"] = quote["mid"]
            metric["last_source"] = "stream_cache"
            spots += 1
    out["remark"] = {
        "at": overlay.get("at"),
        "base_generated_at": snapshot.get("generated_at"),
        "max_age_seconds": overlay.get("max_age_seconds"),
        "legs_remarked": moved,
        "legs_kept": kept,
        "spots_remarked": spots,
    }
    return out
