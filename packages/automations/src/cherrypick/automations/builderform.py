"""The Builder page's form and its "Today, live" read, both from the engine's own tables.

**The schema** (`schema`, `cli builder-schema`) is what the form offers, read from the tables the
engine validates against, so a field the page shows is one `arms.validate` accepts and nothing is
spelled twice: the five blocks and their keys (`enrolment.BLOCKS`), the entry kinds, the expiry kinds
and their keys (`arms.EXPIRY_KEYS`), the strike rules, their keys and match modes (`arms.STRIKE_KEYS`,
`strikes`), the gates with their parameters from each gate function's signature and the stage each
reads (`gates.REGISTRY`, `gates.CORE_STAGE`), the exits (`arms.EXIT_KEYS`), the limits, the sizes and
their progression (`sizing`), core's signal kinds and their parameters (`core.signals.KINDS`), and the
event labels with each one's name, when it falls and its next day (`events`). A signal parameter's
type is core's validator, which the schema cannot state, so the form takes the value as typed and the
module's check says what is wrong.

**Today, live** (`today`, `cli builder-today`) decides one automation once, now, as the paper loop
would: the snapshot `feed.snapshot_for` builds from the stream cache and the paper ledger, opened
read-only, then `paper.decide_all` on a copy of the automation switched on. It returns the engine's
own answer: the entry and the legs it would take, or the first failing check, with every check it
read. Nothing is written: no decision row, no register row, no replay record, no order.

**The payoff** (`payoff`, `cli builder-payoff`) is the structure the automation's rules would take,
for the chart under its legs: the expiry its rule names, the strikes its rules place
(`engine.structure`, the placement `decide` makes, without the timing, limits, event skips, gates or
size), the price at the legs' mids, and its P&L at expiry for one contract. It is placed on the live
quotes when the stream cache holds a snapshot now, and otherwise on the last session our own recorder
holds for a same-day expiry, at the first stored minute of the entry window (`driver.snapshot_at`,
the backtest's snapshot), never on a licensed store. A later expiry is placed on live quotes only: our
own stores hold no intraday chain for it. The answer names which it used. Writes nothing.
"""

from __future__ import annotations

import inspect
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from cherrypick.core import signals

from . import arms as arms_mod
from . import enrolment, entrypresets, events, fieldtypes, gates, sizing, strategies
from . import strikes as st

ENTRY_KINDS: dict[str, dict[str, bool]] = {
    "time": {"at": True, "window": False},
    "orb": {"minutes": True, "direction": True, "latest": True, "single_shot": False},
    "trigger": {"trigger": True, "bar_minutes": False, "volume": False},
}
ORB_DIRECTIONS = ("above", "below")
#: A trigger's two forms and their keys ({key: required}, `core.entrychecks.validate_trigger`): a fixed
#: time for `within` minutes, or a signal's first firing inside a window (`within` minutes from it, or
#: null for until the window closes).
TRIGGER_FORMS: dict[str, dict[str, bool]] = {
    "at": {"at": True, "within": True},
    "signal": {"signal": True, "window": True, "within": True, "daily": False},
}


def _delta_leg(name: str, right: str, side: int, target: float) -> dict:
    """A wizard leg placed by delta, in the written spelling (action, qty, side)."""
    return arms_mod.written_leg(
        {"name": name, "right": right, "side": side, "strike": {"rule": "delta", "target": target}}
    )


#: The wizard's starting shapes: legs placed by delta, each one the engine accepts as it stands
#: (`tests/test_builderform.py` resolves every one). A starting point, never a recommendation.
SHAPES: dict[str, dict] = {
    "short_put": {
        "label": "Short put",
        "about": "Sell one put below the market.",
        "risk": "undefined risk: cash-secured",
        "credit": True,
        "legs": [_delta_leg("short_put", "P", -1, 0.16)],
    },
    "put_credit_spread": {
        "label": "Put credit spread",
        "about": "Sell a put, buy a lower one.",
        "risk": "defined risk: the width less the credit",
        "credit": True,
        "legs": [_delta_leg("short_put", "P", -1, 0.20), _delta_leg("long_put", "P", 1, 0.10)],
    },
    "call_credit_spread": {
        "label": "Call credit spread",
        "about": "Sell a call, buy a higher one.",
        "risk": "defined risk: the width less the credit",
        "credit": True,
        "legs": [_delta_leg("short_call", "C", -1, 0.20), _delta_leg("long_call", "C", 1, 0.10)],
    },
    "iron_condor": {
        "label": "Iron condor",
        "about": "A put spread and a call spread around the market.",
        "risk": "defined risk: the wider side's width less the credit",
        "credit": True,
        "legs": [
            _delta_leg("short_put", "P", -1, 0.16),
            _delta_leg("long_put", "P", 1, 0.05),
            _delta_leg("short_call", "C", -1, 0.16),
            _delta_leg("long_call", "C", 1, 0.05),
        ],
    },
    "iron_fly": {
        "label": "Iron fly",
        "about": "Sell the at-the-money put and call, buy both wings.",
        "risk": "defined risk: the width less the credit",
        "credit": True,
        "legs": [
            _delta_leg("short_put", "P", -1, 0.50),
            _delta_leg("long_put", "P", 1, 0.10),
            _delta_leg("short_call", "C", -1, 0.50),
            _delta_leg("long_call", "C", 1, 0.10),
        ],
    },
    "custom": {
        "label": "Custom legs",
        "about": "Start with one leg and add your own.",
        "risk": "risk depends on the legs",
        "credit": False,
        "legs": [_delta_leg("leg_1", "P", -1, 0.20)],
    },
}

#: The credit-trade preset, in the engine's own exit keys: half the credit taken (`profit_target_pct`
#: of max profit, which for a credit trade is the credit), a stop at a loss equal to the credit, and a
#: close at 21 days to expiry.
CREDIT_RULES = {"profit_target_pct": 50, "stop_credit_multiple": 1, "close_at_dte": 21}

#: What a new automation starts from beside its shape: the monthly cycle 45-60 days out, an entry at
#: 15:55 (inside the end-of-day tick, so a backtest can read it), one contract, one open, one a day.
WIZARD_DEFAULTS = {
    "symbol": "SPY",
    "expiry": {"kind": "between", "min": 45, "max": 60, "monthly": True},
    "entry": {"kind": "time", "at": "15:55", "window": 5},
    "weekdays": [0, 1, 2, 3, 4],
    "size": {"contracts": 1},
    "limits": {"max_open": 1, "per_day": 1},
}


def wizard_spec(shape: str) -> dict:
    """A whole automation from one starting shape: the defaults, the shape's legs named by their IDs
    (L1, L2, … in order, as the Builder names a leg it adds) and, for a credit shape, the credit-trade
    exits."""
    import copy

    s = SHAPES[shape]
    spec = copy.deepcopy(WIZARD_DEFAULTS)
    spec["legs"] = [{**copy.deepcopy(leg), "name": f"L{i + 1}"} for i, leg in enumerate(s["legs"])]
    spec["exit"] = dict(CREDIT_RULES) if s["credit"] else {"close_at_dte": 21}
    return spec


def _gate(name: str, fn) -> dict:
    params = list(inspect.signature(fn).parameters.values())[1:]
    return {
        "gate": name,
        "stage": gates.CORE_STAGE[name],
        "runs": gates.REGISTRY[name][0],
        "params": [
            {
                "name": p.name,
                "required": p.default is inspect.Parameter.empty,
                "default": None if p.default is inspect.Parameter.empty else p.default,
            }
            for p in params
        ],
    }


def _event_about(today: date) -> dict:
    """Each event label's name, when it falls in words, and its next day from `today` where the calendar
    says: null for a symbol's own dates, and past the range a data label covers."""
    try:
        cal = events.load_calendar()
    except Exception:  # noqa: BLE001 - the words still help without the data dates
        cal = events.EventCalendar()
    out = {}
    for label in sorted({*events.RULE_LABELS, *events.STORE_LABELS, *events.SYMBOL_LABELS}):
        name, when = events.ABOUT[label]
        out[label] = {"name": name, "when": when, "next": events.next_day(cal, label, today)}
    return out


def schema(today: date | None = None) -> dict:
    """Every choice the Builder's form offers, from the engine's tables. `today` (Eastern unless given)
    dates each event label's next day."""
    today = today or datetime.now(ZoneInfo("America/New_York")).date()
    return {
        "ok": True,
        "blocks": {b: list(keys) for b, keys in enrolment.BLOCKS.items()},
        "entry": {k: dict(v) for k, v in ENTRY_KINDS.items()},
        "orb_directions": list(ORB_DIRECTIONS),
        "trigger_forms": {k: dict(v) for k, v in TRIGGER_FORMS.items()},
        "signals": {
            k: sorted({*kind.params, *signals.OPTIONAL.get(k, {})})
            for k, kind in sorted(signals.KINDS.items())
        },
        "signal_rules": sorted(signals._RULES),
        "weekdays": [0, 1, 2, 3, 4],
        "event_labels": sorted({*events.RULE_LABELS, *events.STORE_LABELS, *events.SYMBOL_LABELS}),
        "event_about": _event_about(today),
        "types": fieldtypes.types(sorted({*events.RULE_LABELS, *events.STORE_LABELS, *events.SYMBOL_LABELS})),
        "expiry": {k: dict(v) for k, v in arms_mod.EXPIRY_KEYS.items()},
        # The far expiry's rules: every expiry rule, and after_near (`arms.AFTER_NEAR_KEYS`).
        "far_expiry": {
            **{k: dict(v) for k, v in arms_mod.EXPIRY_KEYS.items()},
            "after_near": dict(arms_mod.AFTER_NEAR_KEYS),
        },
        "leg_expiries": list(arms_mod.LEG_EXPIRIES),
        "expiry_flags": list(arms_mod.EXPIRY_FLAGS),
        "strike_rules": {
            k: {
                "keys": dict(v),
                "matches": []
                if k in st.GEX_RULES or k == "same_as"
                else list(st.VALUE_MATCHES if k in st.VALUE_RULES else st.MATCHES),
                "default_match": st.VALUE_RULES.get(k) if k in st.VALUE_RULES else st.PRICE_RULES.get(k),
            }
            for k, v in arms_mod.STRIKE_KEYS.items()
        },  # fmt: skip
        "gates": [_gate(n, fn) for n, (_, fn) in gates.REGISTRY.items()],
        "exits": sorted(arms_mod.EXIT_KEYS),
        "limits": sorted(arms_mod.LIMIT_KEYS),
        "sizes": {k: sorted(v) for k, v in sizing.SIZE_KEYS.items()},
        "progression": sorted(sizing.PROGRESSION_KEYS),
        "shapes": {
            k: {
                "label": s["label"],
                "about": s["about"],
                "risk": s["risk"],
                "credit": s["credit"],
                "spec": wizard_spec(k),
            }
            for k, s in SHAPES.items()
        },
        "credit_rules": dict(CREDIT_RULES),
        "strategy": strategies.schema(),
        "entry_presets": entrypresets.schema(),
    }


def resolve(config: dict, name: str, automation: dict | None) -> tuple[dict | None, list[str]]:
    """The automation as the loop would run it: the saved one by `name`, or `automation` laid over the
    config as a save would write it (`drafts.draft`'s resolution). (arm, problems)."""
    import copy

    from . import config as config_mod
    from . import drafts

    if not isinstance(name, str) or not drafts.NAME.match(name):
        return None, [f"name: lower case letters, digits and hyphens ({drafts.NAME.pattern})"]
    if automation is None:
        found = config_mod.load_arms(config).get(name)
        return (found, []) if found is not None else (None, [f"no automation {name!r}"])
    if not isinstance(automation, dict):
        return None, ["automation: an object"]
    entry = {k: copy.deepcopy(v) for k, v in automation.items() if k not in ("name", "register")}
    overrides = {**config_mod.arm_overrides(config), name: entry}
    try:
        return config_mod.load_arms(drafts._with(config, overrides))[name], []
    except ValueError as exc:
        mine = [p for p in str(exc).split("; ") if p.split(":")[0].split(".")[0] == name]
        return None, mine or [str(exc)]


def _check(c) -> dict:
    name, passed, value = c
    return {"check": name, "passed": bool(passed), "value": value}


def today(config: dict, arm: dict, when: datetime, *, conn, cache_path: str, evts) -> dict:
    """One automation decided now, as paper would decide it, on a read-only snapshot. `arm` is the
    resolved automation (saved, or a draft the module's check returned)."""
    from . import config as config_mod
    from . import feed, paper

    live = {**arm, "enabled": True}
    sym = live["symbol"].upper()
    spec = config_mod.symbols(config, {live["name"]: live}).get(sym)
    if spec is None:
        return {
            "ok": True,
            "name": live["name"],
            "state": "no_symbol",
            "reason": f"no stream settings for {sym}",
        }
    window = feed.entry_window(live)
    minute = when.hour * 60 + when.minute
    params = config_mod.defaults(config)
    snap, info = feed.snapshot_for(cache_path, conn, spec, sym, when, params, [live], events=evts)
    out: dict[str, Any] = {
        "ok": True,
        "name": live["name"],
        "at": when.isoformat(timespec="minutes"),
        "window": [
            f"{window[0] // 60:02d}:{window[0] % 60:02d}",
            f"{window[1] // 60:02d}:{window[1] % 60:02d}",
        ],
        "in_window": window[0] <= minute <= window[1],
    }
    if snap is None:
        return {**out, "state": "no_snapshot", "reason": info.get("refusal"), "detail": info.get("detail")}
    (d,) = paper.decide_all({live["name"]: live}, snap, costs=config) or (None,)
    if d is None:
        return {**out, "state": "not_decided", "reason": "the engine took no decision for it"}
    res = {**out, "state": "would_enter" if d.enter else "would_skip", "reason": d.reason,
           "checks": [_check(c) for c in d.checks], "spot": snap.spot}  # fmt: skip
    if d.enter and d.entry is not None:
        e = d.entry
        res["entry"] = {
            "expiry": e.expiry,
            "quantity": e.quantity,
            "legs": [{"name": g.name, "right": g.right, "side": g.side, "strike": g.strike} for g in e.legs],
            "prices": e.prices,
            "max_loss": e.max_loss,
            "max_profit": e.max_profit,
        }
    return res


#: How far beyond the outer strikes (and the spot) the payoff is drawn: this share of the spot, or the
#: distance between the outer strikes when that is wider.
PAYOFF_MARGIN = 0.004
#: How many of the store's latest sessions are tried for one that holds the entry window.
RECORDED_TRIES = 10


#: Why a structure with a far leg draws no payoff yet.
FAR_PAYOFF_WHY = "a leg on a later expiry: its payoff needs that leg's time value, which is not modeled yet"


def payoff(config: dict, arm: dict, when: datetime, *, conn, cache_path: str, evts, recorded=None) -> dict:
    """The structure `arm` would take and its P&L at expiry, on the live quotes or, without them, on
    the last recorded session (module docstring). `recorded` stands in for `recorded_structure`."""
    from . import config as config_mod
    from . import engine, feed
    from . import sketch as sketch_mod

    live = {**arm, "enabled": True}
    sym = live["symbol"].upper()
    if engine.has_far_legs(live):
        # A payoff at the near expiry leaves out the far legs' time value, which is most of the trade:
        # nothing is drawn until that value is modeled, rather than a shape that is not the trade's.
        why = FAR_PAYOFF_WHY
        return {
            "ok": True,
            "name": live["name"],
            "symbol": sym,
            "state": "not_placed",
            "live": why,
            "recorded": why,
        }
    out: dict[str, Any] = {"ok": True, "name": live["name"], "symbol": sym, "sketch": sketch_mod.sketch(live)}
    spec = config_mod.symbols(config, {live["name"]: live}).get(sym)
    if spec is None:
        live_why = f"no stream settings for {sym}"
    else:
        params = config_mod.defaults(config)
        snap, info = feed.snapshot_for(cache_path, conn, spec, sym, when, params, [live], events=evts)
        if snap is None:
            live_why = str(info.get("refusal") or "no snapshot")
        else:
            got = engine.structure(live, snap)
            if "prices" in got:
                return _placed(out, got, snap, {"kind": "live", "at": when.isoformat(timespec="minutes")})
            live_why = got["why"]
    snap, source, rec_why = (recorded or recorded_structure)(live, when.date(), evts)
    if snap is not None:
        got = engine.structure(live, snap)
        if "prices" in got:
            return _placed(out, got, snap, source)
        rec_why = got["why"]
    return {**out, "state": "not_placed", "live": live_why, "recorded": rec_why}


def recorded_structure(arm: dict, today: date, evts):
    """(snapshot, source, why) for `arm` on the last session our own recorder holds for its same-day
    expiry, at the first stored minute of its entry window: the snapshot the backtest driver builds.
    (None, None, why) where there is none. Never a licensed store."""
    from cherrypick.core import home, sources, volindex
    from cherrypick.core.sources import inventory

    from . import driver, feed
    from .history import StoreHistory

    if enrolment._expiry_class(arm) != "0dte":
        return (
            None,
            None,
            "a later expiry is placed on live quotes only (our own stores hold no intraday chain for it)",
        )
    found = None
    for w in enrolment.windows(arm, inventory.load(), today, sources.coverage, licensed_ok=False):
        source = sources.by_name(w["source"])
        if w["licensed"] or source.licensed or source.probe.backend != "sqlite":
            continue
        path = home.data_dir() / source.probe.where
        if path.exists():
            found = (w, source, path)
            break
    if found is None:
        return None, None, f"none of our own stores holds {arm['symbol'].upper()}'s same-day chain"
    w, source, path = found
    h = StoreHistory(path, arm["symbol"])
    first, last = feed.entry_window(arm)
    vix = volindex.closes("VIX") if feed.reads(arm)["vix"] else None
    for session in [d for d in h.sessions if d <= w["end"]][-RECORDED_TRIES:][::-1]:
        minute = next((m for m in h.minutes(session) if first <= m <= last), None)
        if minute is None:
            continue
        snap = driver.snapshot_at([arm], h, session, minute, driver.Book(), events=evts, vix_daily=vix)
        if snap is not None:
            at = f"{session}T{minute // 60:02d}:{minute % 60:02d}"
            return snap, {"kind": "recorded", "at": at, "source": source.name}, None
    return None, None, f"{source.name} holds no recent session with a minute in the entry window"


def _placed(out: dict, got: dict, snap, source: dict) -> dict:
    """The answer for a placed structure: per share prices as the engine gives them, and the payoff for
    one contract in whole-position dollars."""
    from . import pricing

    legs, credit = got["legs"], got["prices"]["mid"]
    ks = [leg.strike for leg in legs]
    pad = max(snap.spot * PAYOFF_MARGIN, max(ks) - min(ks))
    lo, hi = min(min(ks), snap.spot) - pad, max(max(ks), snap.spot) + pad
    points = pricing.payoff_curve(legs, credit, lo, hi)
    m = pricing.MULTIPLIER

    def dollars(v):
        return None if v is None else round(v * m, 2)

    listed = got["chain"].strikes
    atm = min(listed, key=lambda k: (abs(k - snap.spot), k)) if listed else snap.spot
    return {
        **out,
        "state": "placed",
        "source": source,
        "spot": snap.spot,
        "atm": atm,
        "expiry": got["expiry"],
        "legs": [
            {
                "name": g.name,
                "right": g.right,
                "side": g.side,
                "strike": g.strike,
                "offset": round(g.strike - atm, 2),
            }
            for g in legs
        ],  # fmt: skip
        "prices": got["prices"],
        "max_profit": dollars(got["max_profit"]),
        "max_loss": dollars(got["max_loss"]),
        "curve": [[round(x, 2), dollars(y)] for x, y in points],
        "breakevens": [round(b, 2) for b in pricing.breakevens(points)],
        "chain": chain_slice(got["chain"], ks, snap.spot),
    }


#: How many listed strikes either side of each leg (and of the spot) the chain example shows.
CHAIN_AROUND = 2


def chain_slice(chain, strikes: list[float], spot: float) -> list[dict]:
    """The listed strikes near the legs and the spot, as the chain they were placed on: each strike's
    put and call (bid, ask, delta), its `offset` from the listed strike nearest the spot (0), and `gap`
    where listed strikes between were left out."""
    listed = chain.strikes
    if not listed:
        return []
    near = min(listed, key=lambda k: (abs(k - spot), k))
    keep: set[int] = set()
    for k in [*strikes, near]:
        i = listed.index(k) if k in listed else min(range(len(listed)), key=lambda j: abs(listed[j] - k))
        keep.update(range(max(0, i - CHAIN_AROUND), min(len(listed), i + CHAIN_AROUND + 1)))

    def side(k: float, right: str) -> dict | None:
        q = chain.get(k, right)
        if q is None:
            return None
        return {f: q.get(f) for f in ("bid", "ask", "delta")}

    out, last = [], None
    for i in sorted(keep):
        k = listed[i]
        out.append({"strike": k, "offset": round(k - near, 2), "put": side(k, "P"), "call": side(k, "C"),
                    "gap": last is not None and i > last + 1})  # fmt: skip
        last = i
    return out


#: The strategies sold for a credit when short: they start from the credit-trade exits, as the wizard's
#: credit shapes do.
#: The DTE from which the 21-day close is kept: a trade entered nearer than this would close at once.
CLOSE_AT_DTE_FROM = 35
CREDIT_STRATEGIES = {"option", "vertical", "straddle", "strangle", "iron_condor", "iron_fly", "jade_lizard"}


def strategy_answer(name: str, given: dict | None) -> dict:
    """`cli builder-strategy`: the strategy's legs and expiry for the legs section, and a whole
    automation from it for the wizard (the wizard's defaults, the legs, the expiry and, for a credit
    strategy, the credit-trade exits). A setting it cannot build from comes back as a problem."""
    import copy

    try:
        built = strategies.build(name, given or {})
    except strategies.Refused as exc:
        return {"ok": True, "strategy": name, "problems": [str(exc)]}
    s = built["settings"]
    credit = name in CREDIT_STRATEGIES and s.get("direction", "short") == "short" or name == "jade_lizard"
    spec = {**copy.deepcopy(WIZARD_DEFAULTS), "legs": built["legs"], "expiry": built["expiry"]}
    exit_rules = dict(CREDIT_RULES) if credit else {"close_at_dte": 21}
    if s["dte"] < CLOSE_AT_DTE_FROM:
        exit_rules.pop("close_at_dte", None)  # a 21-day close would end a nearer trade the next session
    if "far_expiry" in built:
        # A far leg closes before the near expiry: the engine requires it (`arms._far_problems`).
        spec["far_expiry"] = built["far_expiry"]
        exit_rules["close_before_expiry_min"] = strategies.CLOSE_BEFORE_NEAR_MIN
    spec["exit"] = exit_rules
    return {"ok": True, **built, "credit": credit, "spec": spec, "problems": []}


def entry_answer(name: str, given: dict | None, filters: list[dict] | None) -> dict:
    """`cli builder-entry`: the entry preset's `entry` and the gates of the filters stacked on it, in
    their order, for the signal section and the wizard. A preset or setting it cannot build from comes
    back as a problem, named by the filter it belongs to."""
    problems: list[str] = []
    try:
        built = entrypresets.build_entry(name, given or {})
    except entrypresets.Refused as exc:
        built, problems = None, [str(exc)]
    stacked, gates_out = [], []
    for i, f in enumerate(filters or []):
        try:
            one = entrypresets.build_filter(str(f.get("filter", "")), f.get("settings") or {})
        except entrypresets.Refused as exc:
            problems.append(f"filters[{i}]: {exc}")
            continue
        stacked.append(one)
        gates_out.extend(one["gates"])
    if built is None or problems:
        return {"ok": True, "preset": name, "problems": problems}
    return {"ok": True, **built, "filters": stacked, "gates": gates_out, "problems": []}
