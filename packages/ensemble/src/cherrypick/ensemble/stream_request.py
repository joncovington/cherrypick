"""Declare this module's stream needs: each arm's symbol, the expiry an arm is about to enter, its strikes.

Writes `~/.cherrypick/state/stream_requests/ensemble.json`. The request is as narrow as the arms allow
(the subscription budget, `packages/streamer/CLAUDE.md`), and it is derived from the enabled arms, so a new
arm needs no code here:

- `symbols`: every enabled arm's underlying, for the spot, the daily bars (`history_days`: the longest
  moving average an arm reads, plus a margin) and the posted close. `nearest_window` is false for each:
  this module never reads the nearest expiry as such.
- `expirations`: an expiry an arm enters, from `stream_lead_minutes` before its entry window opens until
  the window has closed, plus every expiry a position still holds. An arm's expiry is the listed one its
  rule names (`feed.expiries_for`); a cache that has not listed the symbol's expirations yet gets every
  date the rule allows.
- `window_hints`: the symbol's `window_strikes` (config), only while an arm is in that lead-to-close
  span: the default window is 30 strikes a side, which a 35-delta put or a 50-strike GEX window can
  outrun.
- `legs`: VIX, when an arm reads the standard-deviation move or the VIX level.
- `leg_sources`: every open position's legs, from this module's own ledger, so marks and closes continue
  to the expiry.
- `window_events`: Quote only, unless an arm places a strike by delta or reads GEX, which add Greeks and
  Summary (open interest).

Best-effort: a failed write never breaks the loop. A missing chain is the provider's refusal.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from cherrypick.core import streamrequests as _sr

from . import config as config_mod
from . import db, feed, provider

_MODULE = "ensemble"
_log = logging.getLogger("ensemble_paper_loop")


def active_arms(arms: dict[str, dict], today: date) -> dict[str, dict]:
    """The enabled arms whose weekday is today's."""
    return {n: a for n, a in config_mod.enabled(arms).items() if today.weekday() in a["weekdays"]}


def in_lead(arm: dict, minute: int, lead: int) -> bool:
    first, last = feed.entry_window(arm)
    return first - lead <= minute <= last + 2


def wanted(
    config: dict, arms: dict[str, dict], conn, cache_path: str, when: datetime, params: dict | None = None
) -> dict:
    """Pure over the ledger and the cache: what the request should ask for at `when`."""
    params = params or config_mod.defaults(config)
    today, minute = when.date(), when.hour * 60 + when.minute
    specs = config_mod.symbols(config, arms)
    held = db.open_expirations(conn)
    todays = active_arms(arms, today)
    lead = int(params["stream_lead_minutes"])
    out: dict = {
        "symbols": sorted(specs),
        "expirations": {},
        "window_hints": {},
        "window_events": {},
        "history_days": {},
        "legs": [],
    }
    for sym, spec in specs.items():
        mine = [a for a in config_mod.enabled(arms).values() if a["symbol"].upper() == sym]
        dates = set(held.get(sym, []))
        near = [a for a in mine if a["name"] in todays and in_lead(a, minute, lead)]
        if near:
            listed = provider.listed_expirations(cache_path, sym)
            for a in near:
                dates.update(feed.expiries_for(a, today, listed))
            out["window_hints"][sym] = spec["window_strikes"]
        if dates:
            out["expirations"][sym] = sorted(dates)
        needs = [feed.reads(a) for a in mine]
        out["window_events"][sym] = (
            ["Quote", "Greeks", "Summary"] if any(n["greeks"] for n in needs) else ["Quote"]
        )
        out["history_days"][sym] = max(n["closes"] for n in needs) + int(params["history_margin_days"])
        if any(n["vix"] for n in needs):
            out["legs"].append("VIX")
            out["history_days"]["VIX"] = int(params["history_margin_days"])
    return out


def write(
    config: dict, arms: dict[str, dict], conn, db_path: str, *, cache_path: str, when: datetime | None = None
):
    from cherrypick.core.clock import now_et

    need = wanted(config, arms, conn, cache_path, when or now_et())
    return _sr.write_request(
        _MODULE,
        need["symbols"],
        legs=need["legs"],
        leg_sources=[
            _sr.leg_source(db_path, "SELECT streamer_symbol FROM ensemble_legs WHERE status = 'open'")
        ],
        window_hints=need["window_hints"],
        expirations=need["expirations"],
        history_days=need["history_days"],
        window_events=need["window_events"],
        nearest_window={s: False for s in need["symbols"]},
    )


def register(
    config: dict, arms: dict[str, dict], conn, db_path: str, *, cache_path: str, when: datetime | None = None
) -> None:
    """Best-effort: never raises into the caller."""
    _sr.register_best_effort(write, config, arms, conn, db_path, cache_path=cache_path, when=when, log=_log)
