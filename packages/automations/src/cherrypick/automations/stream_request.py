"""Declare this module's stream needs: each arm's symbol, the expiry an arm is about to enter, its strikes.

Writes `~/.cherrypick/state/stream_requests/automations.json`. The request is as narrow as the arms allow
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

An automation joins the request on its start date (`active_arms`), so enrolling one is what asks for
its chain. The request is priced against the subscription budget before it is written (`fit`): one
that would put the producer over its ceiling drops its entry expiries and widened windows, keeps every
held position's, and the status (`stream_budget`) names what it dropped.

Best-effort: a failed write never breaks the loop. A missing chain is the provider's refusal.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from cherrypick.core import streamrequests as _sr

from . import config as config_mod
from . import db, enrolment, feed, provider

_MODULE = "automations"
#: The request file under the module's old name, removed on each write so the streamer never unions a
#: stale one.
_OLD_MODULE = "ensemble"
_log = logging.getLogger("automations_paper_loop")
_OPEN_LEGS = "SELECT streamer_symbol FROM ensemble_legs WHERE status = 'open'"


def active_arms(arms: dict[str, dict], today: date) -> dict[str, dict]:
    """The enabled arms whose weekday is today's, from their start date (`enrolment.joins`)."""
    return {
        n: a
        for n, a in config_mod.enabled(arms).items()
        if today.weekday() in a["weekdays"] and enrolment.joins(a, today)
    }


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
        "symbols": sorted(set(specs) | config_mod.volume_symbols(arms)),
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


def shed(need: dict, held: dict[str, list[str]]) -> tuple[dict, dict[str, list[str]]]:
    """`need` without what only an entry asked for: each symbol keeps the expiries a position holds,
    and no widened window. Returns the narrowed request and, per symbol, the expiries it dropped."""
    kept: dict[str, list[str]] = {}
    dropped: dict[str, list[str]] = {}
    for sym, dates in need["expirations"].items():
        hold = set(held.get(sym, []))
        if keep := sorted(d for d in dates if d in hold):
            kept[sym] = keep
        if gone := sorted(d for d in dates if d not in hold):
            dropped[sym] = gone
    return {**need, "expirations": kept, "window_hints": {}}, dropped


def _payload(need: dict, db_path: str | None) -> dict:
    sources = [_sr.leg_source(db_path, _OPEN_LEGS)] if db_path else []
    return _sr.build_payload(
        need["symbols"],
        legs=need["legs"],
        leg_sources=sources,
        window_hints=need["window_hints"],
        expirations=need["expirations"],
        history_days=need["history_days"],
        window_events=need["window_events"],
        nearest_window={s: False for s in need["symbols"]},
    )


def fit(
    config: dict, arms: dict[str, dict], conn, *, cache_path: str, when: datetime, db_path: str | None = None
) -> tuple[dict, dict]:
    """The request to write and a report of how it met the subscription budget.

    The whole request is priced first (`streamrequests.fits`, against the registry with this module's
    file replaced). When it would put the producer over `configured_budget`, the request is narrowed
    with `shed`: today's entries lose their chain, so they refuse for want of quotes, and every
    position keeps its own. An entry never takes the feed past its ceiling."""
    need = wanted(config, arms, conn, cache_path, when)
    payload = _payload(need, db_path)
    status = _sr.fits(_MODULE, payload, today=when.date())
    report: dict = {"total": status["total"], "budget": status["budget"], "over": status["over"], "shed": {}}
    if status["over"]:
        narrowed, dropped = shed(need, db.open_expirations(conn))
        if dropped or need["window_hints"]:
            payload = _payload(narrowed, db_path)
            after = _sr.fits(_MODULE, payload, today=when.date())
            report.update(shed=dropped, widened=sorted(need["window_hints"]), total_after=after["total"])
    return payload, report


def budget(config: dict, arms: dict[str, dict], conn, *, cache_path: str, when: datetime) -> dict:
    """The status read of `fit`: what this module's request costs and what it had to shed."""
    try:
        return fit(config, arms, conn, cache_path=cache_path, when=when)[1]
    except Exception as exc:  # noqa: BLE001 — a status read never fails on the registry
        return {"error": f"{type(exc).__name__}: {exc}"}


def write(
    config: dict, arms: dict[str, dict], conn, db_path: str, *, cache_path: str, when: datetime | None = None
):
    from cherrypick.core.clock import now_et

    payload, report = fit(config, arms, conn, cache_path=cache_path, when=when or now_et(), db_path=db_path)
    if report["shed"] or report.get("widened"):
        _log.warning("stream request over the subscription budget; entry chains shed: %s", report)
    _sr.request_path(_OLD_MODULE).unlink(missing_ok=True)
    return _sr.write_payload(_MODULE, payload)


def register(
    config: dict, arms: dict[str, dict], conn, db_path: str, *, cache_path: str, when: datetime | None = None
) -> None:
    """Best-effort: never raises into the caller."""
    _sr.register_best_effort(write, config, arms, conn, db_path, cache_path=cache_path, when=when, log=_log)
