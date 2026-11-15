"""cherrypick.core.entrychecks -- the shared half of an entry decision: when to enter, and which checks pass.

Every module that decides an entry this way decides it with the same code, so two arms that read "after
the opening-range break, if IV rank is 20 to 60, keep it only if the credit is at least 0.80" are judged
alike (the reason `core.entry` holds cadence and the leg-sign rule). Leg selection beyond
`core.chaindelta`, exits, sizing, fills and the loop stay in each module.

**The trigger** (`entry_due`) says whether an entry is due now:

- `{"at": "HH:MM", "within": N}`: from that minute for N minutes (a loop that misses the minute
  still enters inside N; after it, the day's entry is missed).
- `{"signal": <core.signals spec>, "window": {"open": "HH:MM", "close": "HH:MM"}, "within": N | None}`:
  from the first firing inside the window, for N minutes or (None) until the window closes.
- The same with `"daily": true`: the signal reads **completed daily bars only**. Any bar of today's
  session is dropped, so a still-forming day is never read; the entry is due today from the window's
  open (for `within`, or until it closes) when the signal fired on the last completed session, which
  must be the previous trading day (`stale_bars` otherwise, never an older day's firing).

Either way the module's cadence applies through `core.entry.entry_allowed` (`last_fill`,
`spacing_seconds`), and the answer is a `Due` with the reason when it is not due. A fixed time is a
trigger that always fires then, so a module with a fixed entry needs no special case.

**The staged checks** (`evaluate`) run a declared list of conditions, each `{"check": name, ...params}`,
in stage order -- `signal`, then `chain`, then `structure` -- and stop at the first that fails. A module
evaluates `signal` and `chain` before it picks legs and `structure` after (`stages=`). Every condition
evaluated is a `Check` (stage, name, passed, value, reason), the one record shape `core.trace` can show
for any module.

**Missing data never passes.** Each condition declares what it reads (`bars`, `events`, `closes`,
`iv_history`, `chain:<role>`, `legs`, or a module's own `extra` key). One whose data the context lacks
fails with the reason `missing: <tag>`, and `coverage` says the same before a run, so the builder can
switch a block off and name the store it lacks.

**The context** (`Context`) is plain data each module fills from its own state; core imports no module
type. Chains are keyed by ROLE, the name an arm gives each expiry it reads (`entry` for the one it
trades, `30d` for a term-structure reference...): the module resolves roles to listed expiries, so a
condition never guesses which expiry it meant.

**The registry.** `CORE` holds the conditions over `core.signals` and `core.chainmeasures`. A module adds
its own with `CORE.extended(...)`, a new registry; nothing mutates the shared one. A condition's own
parameters have no defaults; `min` and `max` are bounds, and an absent one is no bound. A `bounded`
condition (every one in `CORE`) needs at least one; a module's own may not take bounds at all.

Pure functions over the context: no clock, no cache, no network.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from cherrypick.core import calendar as cal
from cherrypick.core import chainmeasures as cm
from cherrypick.core import entry as cadence
from cherrypick.core import signals as sg

STAGES = ("signal", "chain", "structure")


# --- the context ------------------------------------------------------------------------------------


@dataclass
class Context:
    """What the checks read. Leave out what the module does not have; a condition that needs it fails
    as missing."""

    bars: Sequence[sg.Bar] = ()  # the underlying's bars through now, oldest first
    events: Mapping[str, Iterable[str]] | None = None  # event label -> ISO dates, for event signals
    closes: Sequence[float] | None = None  # daily closes strictly before the session, oldest first
    iv_history: Sequence[float] | None = None  # the same IV reading over the lookback, in vol points
    chains: Mapping[str, Sequence[Mapping[str, Any]]] = field(default_factory=dict)  # role -> rows
    years: Mapping[str, float] = field(default_factory=dict)  # role -> years to that expiry
    legs: Sequence[Mapping[str, Any]] | None = None  # the chosen legs, once chosen
    extra: Mapping[str, Any] = field(default_factory=dict)  # a module's own inputs, by key

    def available(self) -> set[str]:
        out = set(self.extra)
        if self.bars:
            out.add("bars")
        if self.events is not None:
            out.add("events")
        if self.closes:
            out.add("closes")
        if self.iv_history:
            out.add("iv_history")
        if self.legs is not None:
            out.add("legs")
        out |= {f"chain:{role}" for role, rows in self.chains.items() if rows and role in self.years}
        return out


# --- the check record ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    stage: str
    name: str
    passed: bool
    value: Any
    reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Verdict:
    """`passed` when every evaluated condition passed; `failed` is the first that did not."""

    passed: bool
    checks: tuple[Check, ...]

    @property
    def failed(self) -> Check | None:
        return next((c for c in self.checks if not c.passed), None)


# --- conditions and the registry ----------------------------------------------------------------------

Validator = Callable[[object], bool]


@dataclass(frozen=True)
class Condition:
    """One kind of check. `fn(ctx, params)` returns (passed, value); `needs(params)` the data tags it
    reads; `params` the required parameters and `optional` the ones that may be left out."""

    name: str
    stage: str
    fn: Callable[[Context, Mapping[str, Any]], tuple[bool, Any]]
    needs: Callable[[Mapping[str, Any]], set[str]]
    params: Mapping[str, Validator]
    optional: Mapping[str, Validator] = field(default_factory=dict)
    bounded: bool = False  # True: at least one of `min` and `max` is required


class Registry:
    def __init__(self, conditions: Iterable[Condition] = ()):
        self._by: dict[str, Condition] = {}
        for c in conditions:
            if c.stage not in STAGES:
                raise ValueError(f"condition {c.name!r}: unknown stage {c.stage!r}")
            if c.name in self._by:
                raise ValueError(f"condition {c.name!r} is registered twice")
            self._by[c.name] = c

    def extended(self, *conditions: Condition) -> Registry:
        """A new registry with these added; the original is unchanged."""
        return Registry([*self._by.values(), *conditions])

    def get(self, name: str) -> Condition | None:
        return self._by.get(name)

    def names(self) -> list[str]:
        return sorted(self._by)


def _num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pos(v: object) -> bool:
    return _num(v) and v > 0


def _pos_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _role(v: object) -> bool:
    return isinstance(v, str) and bool(v)


def _one_of(*allowed: str) -> Validator:
    return lambda v: v in allowed


_BOUNDS: dict[str, Validator] = {"min": _num, "max": _num}


def _within(value: float | None, params: Mapping[str, Any]) -> bool:
    if value is None:
        return False
    lo, hi = params.get("min"), params.get("max")
    return (lo is None or value >= lo - 1e-12) and (hi is None or value <= hi + 1e-12)


def _bounded(fn: Callable[[Context, Mapping[str, Any]], float | None]):
    def run(ctx: Context, params: Mapping[str, Any]) -> tuple[bool, Any]:
        v = fn(ctx, params)
        return _within(v, params), v

    return run


def _atm(ctx: Context, role: str, iv_from: str) -> float | None:
    return cm.atm_iv(ctx.chains[role], years=ctx.years[role], iv_from=iv_from)


def _signal(ctx: Context, p: Mapping[str, Any]) -> tuple[bool, Any]:
    st = sg.state(p["spec"], ctx.bars, ctx.events)
    return st[-1] is True, st[-1]


def _signal_needs(p: Mapping[str, Any]) -> set[str]:
    def kinds(spec: Mapping[str, Any]) -> Iterable[str]:
        yield spec["kind"]
        for other in spec.get("only_when", ()):
            yield from kinds(other)

    spec = p.get("spec")
    if not isinstance(spec, Mapping) or "kind" not in spec:
        return {"bars"}
    return {"bars", "events"} if "event" in set(kinds(spec)) else {"bars"}


def _chain_needs(*keys: str, also: tuple[str, ...] = ()) -> Callable[[Mapping[str, Any]], set[str]]:
    return lambda p: {f"chain:{p.get(k)}" for k in keys} | set(also)


_IV = {"iv_from": _one_of("row", "mid")}
_DELTA = {"delta_from": _one_of("row", "mid")}

CORE = Registry(
    [
        Condition(
            "signal",
            "signal",
            _signal,
            _signal_needs,
            {"spec": lambda v: isinstance(v, Mapping)},
        ),
        Condition(
            "atm_iv",
            "chain",
            _bounded(lambda c, p: _atm(c, p["chain"], p["iv_from"])),
            _chain_needs("chain"),
            {"chain": _role, **_IV},
            _BOUNDS,
            bounded=True,
        ),
        Condition(
            "iv_rank",
            "chain",
            _bounded(
                lambda c, p: (
                    cm.iv_rank(_atm(c, p["chain"], p["iv_from"]), c.iv_history)
                    if _atm(c, p["chain"], p["iv_from"]) is not None
                    else None
                )
            ),
            _chain_needs("chain", also=("iv_history",)),
            {"chain": _role, **_IV},
            _BOUNDS,
            bounded=True,
        ),
        Condition(
            "iv_percentile",
            "chain",
            _bounded(
                lambda c, p: (
                    cm.iv_percentile(_atm(c, p["chain"], p["iv_from"]), c.iv_history)
                    if _atm(c, p["chain"], p["iv_from"]) is not None
                    else None
                )
            ),
            _chain_needs("chain", also=("iv_history",)),
            {"chain": _role, **_IV},
            _BOUNDS,
            bounded=True,
        ),
        Condition(
            "iv_minus_rv",
            "chain",
            _bounded(
                lambda c, p: cm.iv_minus_rv(
                    _atm(c, p["chain"], p["iv_from"]), list(c.closes)[-(p["days"] + 1) :]
                )
            ),
            _chain_needs("chain", also=("closes",)),
            {"chain": _role, "days": _pos_int, **_IV},
            _BOUNDS,
            bounded=True,
        ),
        Condition(
            "skew_25d",
            "chain",
            _bounded(
                lambda c, p: cm.skew_25d(
                    c.chains[p["chain"]],
                    years=c.years[p["chain"]],
                    iv_from=p["iv_from"],
                    delta_from=p["delta_from"],
                )
            ),
            _chain_needs("chain"),
            {"chain": _role, **_IV, **_DELTA},
            _BOUNDS,
            bounded=True,
        ),
        Condition(
            "term_spread",
            "chain",
            _bounded(
                lambda c, p: cm.term_spread(_atm(c, p["near"], p["iv_from"]), _atm(c, p["far"], p["iv_from"]))
            ),
            _chain_needs("near", "far"),
            {"near": _role, "far": _role, **_IV},
            _BOUNDS,
            bounded=True,
        ),
        Condition(
            "term_ratio",
            "chain",
            _bounded(
                lambda c, p: cm.term_ratio(_atm(c, p["near"], p["iv_from"]), _atm(c, p["far"], p["iv_from"]))
            ),
            _chain_needs("near", "far"),
            {"near": _role, "far": _role, **_IV},
            _BOUNDS,
            bounded=True,
        ),
        Condition(
            "net_greek",
            "structure",
            _bounded(lambda c, p: cm.net_greeks(c.legs, multiplier=p["multiplier"])[p["greek"]]),
            lambda p: {"legs"},
            {"greek": _one_of(*cm.GREEKS), "multiplier": _pos},
            _BOUNDS,
            bounded=True,
        ),
    ]
)


# --- validation and coverage ------------------------------------------------------------------------


def validate(
    conditions: Sequence[Mapping[str, Any]], registry: Registry = CORE, path: str = "checks"
) -> None:
    """Refuse a list that names an unknown condition, misses a parameter, carries an unknown one or a
    bad value, or bounds nothing; naming the path."""
    if not isinstance(conditions, (list, tuple)):
        raise ValueError(f"{path}: a list of conditions")
    for i, spec in enumerate(conditions):
        where = f"{path}[{i}]"
        if not isinstance(spec, Mapping):
            raise ValueError(f"{where}: a condition is a mapping with a 'check'")
        cond = registry.get(spec.get("check"))  # type: ignore[arg-type]
        if cond is None:
            raise ValueError(f"{where}.check: unknown condition {spec.get('check')!r}")
        keys = set(spec) - {"check"}
        unknown = keys - set(cond.params) - set(cond.optional)
        if unknown:
            raise ValueError(f"{where}: unknown key {sorted(unknown)[0]!r} for {cond.name}")
        for key, ok in cond.params.items():
            if key not in spec:
                raise ValueError(f"{where}: {cond.name} needs {key!r}")
            if not ok(spec[key]):
                raise ValueError(f"{where}.{key}: bad value {spec[key]!r}")
        for key in keys & set(cond.optional):
            if not cond.optional[key](spec[key]):
                raise ValueError(f"{where}.{key}: bad value {spec[key]!r}")
        if cond.bounded and not keys & set(_BOUNDS):
            raise ValueError(f"{where}: {cond.name} needs a 'min' or a 'max'")
        if cond.name == "signal":
            sg.validate(spec["spec"], f"{where}.spec")


def _params(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in spec.items() if k != "check"}


def _missing(cond: Condition, params: Mapping[str, Any], available: set[str]) -> set[str]:
    return cond.needs(params) - available


def coverage(
    conditions: Sequence[Mapping[str, Any]], available: set[str], registry: Registry = CORE
) -> dict[int, list[str]]:
    """For each condition (by index) the data tags it needs that `available` lacks; empty when covered."""
    validate(conditions, registry)
    out = {}
    for i, spec in enumerate(conditions):
        gap = _missing(registry.get(spec["check"]), _params(spec), available)
        if gap:
            out[i] = sorted(gap)
    return out


# --- evaluation ---------------------------------------------------------------------------------------


def evaluate(
    conditions: Sequence[Mapping[str, Any]],
    ctx: Context,
    *,
    stages: Sequence[str],
    registry: Registry = CORE,
) -> Verdict:
    """Run the conditions of `stages`, in stage order (the list's order within a stage), stopping at the
    first that fails."""
    for s in stages:
        if s not in STAGES:
            raise ValueError(f"unknown stage {s!r}")
    validate(conditions, registry)
    order = [(STAGES.index(registry.get(c["check"]).stage), i) for i, c in enumerate(conditions)]
    checks: list[Check] = []
    available = ctx.available()
    for _, i in sorted(order):
        spec = conditions[i]
        cond = registry.get(spec["check"])
        if cond.stage not in stages:
            continue
        params = _params(spec)
        gap = _missing(cond, params, available)
        if gap:
            checks.append(Check(cond.stage, cond.name, False, None, "missing: " + ", ".join(sorted(gap))))
            return Verdict(False, tuple(checks))
        passed, value = cond.fn(ctx, params)
        checks.append(Check(cond.stage, cond.name, bool(passed), value))
        if not passed:
            return Verdict(False, tuple(checks))
    return Verdict(True, tuple(checks))


# --- the trigger --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Due:
    """`due` now or not; `fired` the minute the trigger fired (None if it has not); `reason` when not
    due: `not_yet`, `missed` (the trigger fired but its time to enter has passed), `spacing` (with
    `wait_seconds`), or `no_firing`."""

    due: bool
    fired: int | None
    reason: str | None = None
    wait_seconds: int = 0


def _hhmm(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def _hhmm_ok(v: object) -> bool:
    if not isinstance(v, str) or len(v.split(":")) != 2:
        return False
    h, m = v.split(":")
    return h.isdigit() and m.isdigit() and int(h) < 24 and int(m) < 60


def validate_trigger(trigger: object, path: str = "trigger") -> None:
    if not isinstance(trigger, Mapping):
        raise ValueError(f"{path}: a trigger is a mapping")
    if "at" in trigger:
        extra = set(trigger) - {"at", "within"}
        if extra:
            raise ValueError(f"{path}: unknown key {sorted(extra)[0]!r} for a fixed time")
        if not _hhmm_ok(trigger["at"]):
            raise ValueError(f"{path}.at: bad time {trigger['at']!r}")
        if not _pos_int(trigger.get("within")):
            raise ValueError(f"{path}.within: a fixed time needs a positive number of minutes")
        return
    if "signal" not in trigger:
        raise ValueError(f"{path}: needs 'at' or 'signal'")
    extra = set(trigger) - {"signal", "window", "within", "daily"}
    if extra:
        raise ValueError(f"{path}: unknown key {sorted(extra)[0]!r} for a signal trigger")
    if "daily" in trigger and not isinstance(trigger["daily"], bool):
        raise ValueError(f"{path}.daily: true or false")
    if trigger.get("daily") and trigger["signal"].get("seed") is False:
        # Each daily bar is its own session: a signal that starts every session unarmed never fires.
        raise ValueError(f"{path}.signal.seed: a daily signal carries its state across sessions; drop seed")
    sg.validate(trigger["signal"], f"{path}.signal")
    window = trigger.get("window")
    if not isinstance(window, Mapping) or set(window) != {"open", "close"}:
        raise ValueError(f"{path}.window: needs exactly 'open' and 'close'")
    if not (_hhmm_ok(window["open"]) and _hhmm_ok(window["close"])) or _hhmm(window["open"]) > _hhmm(
        window["close"]
    ):
        raise ValueError(f"{path}.window: bad times {dict(window)!r}")
    if "within" not in trigger or not (trigger["within"] is None or _pos_int(trigger["within"])):
        raise ValueError(f"{path}.within: a positive number of minutes, or null for until the window closes")


def _daily_fired(
    trigger: Mapping[str, Any], bars: Sequence[sg.Bar], events, today
) -> tuple[int | None, str | None]:
    """A daily trigger: (the window's open, None) when the signal fired on the last completed session
    and that session is the previous trading day; (None, None) when it did not fire; (None,
    "stale_bars") when the bars stop before the previous trading day."""
    done = [b for b in bars if b.session < today.isoformat()]
    if not done or done[-1].session != cal.previous_trading_day(today).isoformat():
        return None, "stale_bars"
    last = done[-1].session
    if any(f.session == last for f in sg.firings(trigger["signal"], done, events)):
        return _hhmm(trigger["window"]["open"]), None
    return None, None


def _fired(trigger: Mapping[str, Any], bars: Sequence[sg.Bar], events) -> int | None:
    if "at" in trigger:
        return _hhmm(trigger["at"])
    lo, hi = _hhmm(trigger["window"]["open"]), _hhmm(trigger["window"]["close"])
    session = bars[-1].session if bars else None
    for f in sg.firings(trigger["signal"], bars, events):
        if f.session == session and lo <= f.minute <= hi:
            return f.minute
    return None


def _until(trigger: Mapping[str, Any], fired: int) -> int:
    """The last minute the entry is due."""
    if trigger.get("within") is None:
        return _hhmm(trigger["window"]["close"])
    end = fired + trigger["within"] - 1
    return min(end, _hhmm(trigger["window"]["close"])) if "window" in trigger else end


def entry_due(
    trigger: Mapping[str, Any],
    *,
    now: datetime,
    bars: Sequence[sg.Bar] = (),
    events: Mapping[str, Iterable[str]] | None = None,
    last_fill: datetime | None = None,
    spacing_seconds: int = 0,
) -> Due:
    """Whether an entry is due at `now` (Eastern): the trigger has fired today, `now` is inside its time
    to enter, and the module's cadence allows it. `bars` are today's (and, for a seeded signal,
    earlier sessions') bars completed by `now`; a fixed time reads none; a daily trigger, daily bars
    (any of today's is dropped)."""
    validate_trigger(trigger)
    minute = now.hour * 60 + now.minute
    if trigger.get("daily"):
        fired, stale = _daily_fired(trigger, bars, events, now.date())
        if stale:
            return Due(False, None, stale)
    else:
        fired = _fired(trigger, bars, events)
    if fired is None:
        closed = "window" in trigger and minute > _hhmm(trigger["window"]["close"])
        return Due(False, None, "no_firing" if closed else "not_yet")
    if minute < fired:
        return Due(False, fired, "not_yet")
    if minute > _until(trigger, fired):
        return Due(False, fired, "missed")
    ok, wait = cadence.entry_allowed(last_fill, now, spacing_seconds)
    if not ok:
        return Due(False, fired, "spacing", wait)
    return Due(True, fired)
