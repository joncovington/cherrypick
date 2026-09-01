"""Decide what to do with an open position, and whether we are allowed to do it yet.

Two separate questions, deliberately kept apart. `evaluate` answers "what should happen to this
position", purely from a priced snapshot and the clock. `execution_gate` answers "may we act on this
mark at all" — before the execution window, on quotes too wide to trust, on a mark that could not be
priced. A verdict blocked by a gate is still recorded, because the record that the system saw the
exit before it was allowed to take it is what makes a 09:41 exit on a 09:33 target explicable.

Nothing here does I/O, so every rule is testable against a fixed clock and a dict of quotes.

**Per-strategy thresholds are NOT reimplemented here.** Each strategy's own `evaluate_position`
already owns its profit target, stop, leg-delta stop, and (for the calendars) its front-expiration
time stop; this module hands it the config it expects and layers on the rules it has no way to know
about — the ones that need the position's whole history rather than one tick:

  * the PEAD gate: a loser closes the first morning, a winner may carry (see `_HOLD_NOTE`),
  * the session cap: three sessions and out, whatever the verdict,
  * the pin guard: no short strike left near spot in the last hour of its expiration day.

The research behind each is in docs/10-exits.md, per strategy, with the ones that are house rules
rather than published findings marked as such.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as _time

# One ET for the suite — see cherrypick.core.clock. Re-exported: callers and tests read
# `management.ET` rather than deriving their own.
from cherrypick.core.clock import ET  # noqa: F401

from cherrypick.earnings import scanner
from cherrypick.earnings.strategies import (
    atm_calendar,
    broken_wing_butterfly,
    directional_credit_spread,
    double_calendar,
    iron_condor,
    iron_fly,
)

_HOLD_NOTE = """Holding a winner past the first morning is worth roughly +1.4pp on average as the
residual IV crush drains over three to five sessions; holding a LOSER fights post-earnings drift,
which continues rather than mean-reverting. So the carry is gated on profitability, not on the
verdict alone."""

# Every strategy's evaluate_position, as (module, takes_first_check_flag, takes_open_legs, takes_now).
# The first three shapes are real and predate this module (only the strategies that can act on a
# first-of-day gap take the flag, and only double_calendar has independently-closeable legs);
# normalising them here keeps that history in one place instead of at every call site.
#
# `takes_now` marks the evaluators carrying a time-based rule — the credit strategies' backstop and
# the calendars' front-expiration stop. They are handed the tick's own clock; broken_wing_butterfly
# has no such rule and is not.
_EVALUATORS = {
    "iron_fly": (iron_fly, False, False, True),
    "iron_condor": (iron_condor, False, False, True),
    "directional_credit_spread": (directional_credit_spread, False, False, True),
    "broken_wing_butterfly": (broken_wing_butterfly, True, False, False),
    "atm_calendar": (atm_calendar, True, False, True),
    "double_calendar": (double_calendar, True, True, True),
}

# Strategies entered the afternoon before an announcement and meant to be out once the crush is
# realised. The calendars are excluded: they are held across expirations by construction and run
# their own front-expiration time stop.
OVERNIGHT = ("iron_fly", "iron_condor", "directional_credit_spread", "broken_wing_butterfly")

POLICY_DEFAULTS = {
    # Sessions a profitable position may be carried before it is closed regardless. Three, because
    # the residual crush is spent by then and what is left is direction, which this system has no
    # edge on.
    "hold_winners_max_days": 3,
    "close_losers_first_morning": True,
    # Marks before this are recorded and never acted on. Opening-auction spreads can exceed the edge
    # being managed, and a target computed off that mid is arithmetic rather than a price.
    "exec_window_start": "09:40",
    "max_leg_spread_pct": 0.35,
    # The floor under the percentage: a leg is refused only when wide in percent AND in money.
    # A short that has done its job quotes 0.00/0.01 -- a one-cent buyback and, as a ratio, a 200%
    # spread -- and that is the WIN case, not an illiquidity case. See `_spread_blocks`.
    "max_leg_spread_abs": 0.05,
    # Pin risk: a short strike sitting on spot into the close of its expiration day.
    "pin_guard_dollars": 1.00,
    "pin_guard_window_minutes": 60,
    # The same-session backstop the strategies carry (`exit_after_announcement_minutes`) predates
    # multi-day holds and would fire on every position the first morning -- 18 hours have passed by
    # then. The session cap above is the time rule now, so this is set past any hold it could
    # preempt. Lowering it re-enables a same-session forced close.
    "exit_after_announcement_minutes": 60 * 24 * 10,
}


@dataclass(frozen=True)
class Decision:
    """A verdict about one position. `executed` is decided by the caller after `execution_gate`."""

    action: str  # "hold" | "close_all" | "close_side"
    reason: str
    detail: dict = field(default_factory=dict)

    @property
    def closes(self) -> bool:
        return self.action in ("close_all", "close_side")


def policy_for(strategy: str, config: dict) -> dict:
    """Effective management policy: defaults, overridden by `management` then
    `management.<strategy>` in config. Per-strategy last so one strategy can be tuned without
    restating the common keys."""
    management = config.get("management") or {}
    common = {k: v for k, v in management.items() if not isinstance(v, dict)}
    per_strategy = management.get(strategy) or {}
    return {**POLICY_DEFAULTS, **common, **per_strategy}


def effective_config(trade: dict, config: dict) -> dict:
    """`config` with this trade's frozen advised params overlaid onto its own strategy block.

    The advised book is a TWIN: an `advised:strat_test:<strategy>` row opened beside the control
    with identical fill economics, differing only in the management params stamped on it at entry.
    This is the single choke point where that difference is applied, so an advised position is
    managed under its own terms at every later tick and the control is never touched.

    Pure, and deliberately per-trade rather than per-session: exit thresholds are read at DECISION
    time, so a session-level overlay would stop governing an open position the moment advice lapsed
    and hand it to rules nobody chose. A row with no `advice_params` returns `config` unchanged.
    """
    raw = trade.get("advice_params")
    if not raw:
        return config
    try:
        params = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        return config  # an unreadable stamp is the control's config, never a guess
    strategy = trade.get("strategy")
    if not params or not strategy:
        return config
    strategies = config.get("strategies") or {}
    return {
        **config,
        "strategies": {**strategies, strategy: {**(strategies.get(strategy) or {}), **params}},
    }


def strategy_config(strategy: str, config: dict, policy: dict) -> dict:
    """The config a strategy's own `evaluate_position` should see.

    Its thresholds live under `strategies.<name>` and it reads them itself; what it cannot know is
    that the same-session backstop has been superseded by the session cap, so that one value is
    injected from the policy. Everything else is passed through untouched.
    """
    return {**config, "exit_after_announcement_minutes": policy["exit_after_announcement_minutes"]}


def strike_from_occ(symbol: str) -> float | None:
    """Strike out of a standard OCC symbol — the last 8 digits, in thousandths of a dollar."""
    try:
        return int(symbol[-8:]) / 1000.0
    except (TypeError, ValueError):
        return None


def short_strikes(legs: list[dict]) -> list[float]:
    """Strikes this position is SHORT. Only these can be assigned, so only these carry pin risk."""
    out = []
    for leg in legs:
        if leg.get("action") == "Sell to Open":
            strike = strike_from_occ(leg.get("symbol") or "")
            if strike is not None:
                out.append(strike)
    return out


def unrealized_pnl(trade: dict, exit_debit: float) -> float:
    """What the position is worth right now, on the same formula the realised close uses.

    Deliberately the same arithmetic as `cmd_run_closes` rather than a better one: a mark that
    disagreed with the P&L eventually recorded would make every excursion column a different
    measurement from the result it is supposed to explain.
    """
    return (trade["entry_credit"] - exit_debit) * 100


def _pin_risk(trade: dict, legs: list[dict], spot: float | None, policy: dict, now: datetime) -> bool:
    """A short strike within a dollar of spot, in the last hour of its expiration day.

    Assignment is decided by the settlement print, not by where the option sat all afternoon, so
    this is a guard against an outcome that is still undetermined — which is exactly why it fires on
    proximity rather than on being in the money.
    """
    if spot is None:
        return False
    try:
        expiry = date.fromisoformat(str(trade.get("expiration")))
    except (TypeError, ValueError):
        return False
    if now.date() != expiry:
        return False
    minutes_left = (16 * 60) - (now.hour * 60 + now.minute)
    if minutes_left > policy["pin_guard_window_minutes"] or minutes_left < 0:
        return False
    return any(abs(strike - spot) <= policy["pin_guard_dollars"] for strike in short_strikes(legs))


def _strategy_verdict(trade: dict, quotes: dict, config: dict, *, open_legs, is_first_check_of_day, now):
    """Run the strategy's own evaluate_position, whatever shape its signature takes.

    `now` is threaded to every evaluator that has a time-based rule, so the clock those rules read is
    the tick being evaluated rather than whenever the process happens to be running. They each still
    default to the machine clock for callers outside this manager.
    """
    module, takes_flag, takes_legs, takes_now = _EVALUATORS[trade["strategy"]]
    kwargs = {}
    if takes_flag:
        kwargs["is_first_check_of_day"] = is_first_check_of_day
    if takes_now:
        kwargs["now"] = now
    if takes_legs:
        return module.evaluate_position(dict(trade), open_legs or [], quotes, config, **kwargs)
    return module.evaluate_position(dict(trade), quotes, config, **kwargs)


def evaluate(
    trade: dict,
    snapshot: dict,
    config: dict,
    *,
    now: datetime,
    sessions_held: int | None,
    is_first_check_of_day: bool = False,
    open_legs: list[dict] | None = None,
) -> Decision:
    """What should happen to this position, given a priced snapshot and the clock.

    Order of precedence, and why: the pin guard first because it is about an outcome nothing else
    prices; then the strategy's own verdict, which owns every threshold; then the two rules that
    need more than one tick to see -- the session cap and the PEAD gate -- which only ever turn a
    hold into a close, never the reverse.
    """
    strategy = trade.get("strategy")
    if strategy not in _EVALUATORS:
        return Decision("hold", "unknown_strategy", {"strategy": strategy})

    # An advised twin carries its own management params, frozen on the row at entry. Applied here,
    # once, so every rule below — the policy, the strategy's own thresholds, the session cap — sees
    # the same config. A control row is untouched by this line.
    config = effective_config(trade, config)
    policy = policy_for(strategy, config)
    legs = json.loads(trade.get("legs_json") or "[]")
    quotes = snapshot["quotes"]

    if _pin_risk(trade, legs, snapshot.get("spot"), policy, now):
        return Decision("close_all", "pin_risk", {"spot": snapshot.get("spot")})

    verdict = _strategy_verdict(
        trade,
        quotes,
        strategy_config(strategy, config, policy),
        open_legs=open_legs,
        is_first_check_of_day=is_first_check_of_day,
        now=now,
    )
    action = verdict.get("action", "hold")
    if action != "hold":
        return Decision(action, verdict.get("reason") or action, {"from": "strategy"})

    exit_debit = scanner.compute_generic_exit_debit(legs, quotes)
    if exit_debit is None:
        return Decision("hold", "unpriceable", {})
    pnl = unrealized_pnl(trade, exit_debit)

    # The session cap binds whatever the verdict: three sessions in, the crush is spent and what is
    # left is direction. Applied to the overnight structures only -- the calendars are held across
    # expirations by design and stop themselves on front DTE.
    if strategy in OVERNIGHT and sessions_held is not None:
        if sessions_held >= policy["hold_winners_max_days"]:
            return Decision("close_all", "max_hold", {"sessions_held": sessions_held, "pnl": pnl})

    # The PEAD gate. A winner may carry; a loser closes on the first reliable marks, because the gap
    # that put it there tends to continue rather than revert.
    if strategy in OVERNIGHT and is_first_check_of_day and policy["close_losers_first_morning"] and pnl <= 0:
        return Decision("close_all", "pead_loser", {"pnl": pnl})

    return Decision("hold", "working", {"pnl": pnl, "exit_debit": exit_debit})


def execution_gate(snapshot: dict, config: dict, strategy: str, *, now: datetime) -> str | None:
    """Why this mark may not be acted on, or None if it may.

    Separate from `evaluate` so a blocked verdict is still a verdict: it gets recorded with the gate
    that held it, and the next tick reconsiders. Folding this into the decision would make an exit
    the system saw at 09:33 and took at 09:41 indistinguishable from one it did not notice until
    09:41.
    """
    policy = policy_for(strategy, config)
    if not snapshot.get("ok"):
        return "unusable_mark"

    try:
        hour, minute = (int(x) for x in str(policy["exec_window_start"]).split(":"))
    except (TypeError, ValueError):
        hour, minute = 9, 40
    if now.timetz().replace(tzinfo=None) < _time(hour, minute):
        return "before_exec_window"

    if _spread_blocks(snapshot, policy):
        return "spread_too_wide"
    return None


def _spread_blocks(snapshot: dict, policy: dict) -> bool:
    """Whether any leg is too wide to act on -- wide in PERCENT and in MONEY, both, per leg.

    A percentage alone is the wrong instrument on the way out, where the common case is a short
    that has gone nearly worthless: `bid 0.00 / ask 0.01` is a one-cent buyback and, as a ratio,
    exactly a 200% spread. Measured before this change: 32 distinct positions hit their profit
    target, were refused by the percentage test (5,695 of the gated ticks at exactly 2.000), and
    every one of them rode to expiry instead of taking the exit the 2026-08-12 managed lifecycle
    exists to test. calendars had the same defect on its Friday close (fixed 2026-08-31); curve's
    entry gate documented the identical arithmetic from the other side.

    Judged PER LEG off the snapshot's own quotes: the widest-by-percent and widest-by-money can be
    different legs, and two separate maxima would refuse a structure neither leg justifies. A
    snapshot without quotes falls back to the aggregate percentage, so nothing widens silently.
    """
    max_pct = policy["max_leg_spread_pct"]
    quotes = snapshot.get("quotes")
    if not quotes:
        widest = snapshot.get("max_spread_pct")
        return widest is not None and widest > max_pct
    max_abs = policy.get("max_leg_spread_abs", 0.05)
    for q in quotes.values():
        bid, ask, mid = q.get("bid"), q.get("ask"), q.get("mid")
        if bid is None or ask is None or not mid or mid <= 0:
            continue
        pct = (ask - bid) / mid
        if pct > max_pct and (ask - bid) > max_abs:
            return True
    return False
