"""The filter-gate registry. A gate is `fn(ctx, **params) -> (ok, value)`: `ok` False blocks the entry,
`value` is what was read (kept so a skip is explainable). A gate that cannot read its input FAILS: the
bot does not trade on data it lacks. The one exception is an event label with no data for the date
(`events.EventCalendar.occurs` -> None), handled in `engine`, not here.

`stage` says when a gate can run: `market` before any leg is chosen, `structure` after (the gate reads the
chosen legs' prices). An arm lists gates as `{"gate": <name>, ...params}`; every bound is optional
(`min`, `max`, inclusive), so "-1.1 to 1.1" is `{"min": -1.1, "max": 1.1}`.

GEX windows. `gex_ratio` reads the N strikes of the expiry's per-strike series (`core.gex`): `nearest`
the N nearest spot, `above`/`below` the N nearest strictly above/below it. The ratio is

    (call gex + put gex) / min(call gex, |put gex|)        puts carry a negative sign

so it is positive when calls outweigh puts, zero when they balance, and a put-heavy window reads below
-1 by as many multiples of the smaller side as the larger exceeds it. The source does not define it; this
is the one signed ratio whose cut-offs (0 and -1.5) can both bind.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field

from cherrypick.core import chainmeasures as cm
from cherrypick.core import entrychecks as ec
from cherrypick.core.structures import expected_move

from . import indicators as ind
from .model import Chain, Snapshot, mid


@dataclass
class Ctx:
    snap: Snapshot
    arm: dict
    chain: Chain
    expiry: str
    legs: list = field(default_factory=list)  # Leg list, once chosen
    quotes: list = field(default_factory=list)  # (quote, signed qty) per leg, once chosen
    prices: dict | None = None
    profit_mid: float | None = None  # per share at the structure's mid; None when unbounded
    loss_mid: float | None = None


def _between(v: float | None, lo, hi) -> bool:
    if v is None:
        return False
    return (lo is None or v >= lo - 1e-12) and (hi is None or v <= hi + 1e-12)


def _pct(a: float, b: float) -> float:
    return (a - b) / b * 100.0


def sd_change(ctx, min=None, max=None):
    v = ind.sd_change(ctx.snap.spot, ctx.snap.prev_close, ctx.snap.vix_prev_close)
    return _between(v, min, max), v


def change_pct(ctx, min=None, max=None):
    v = _pct(ctx.snap.spot, ctx.snap.prev_close)
    return _between(v, min, max), v


def gap_pct(ctx, min=None, max=None):
    v = _pct(ctx.snap.day_open, ctx.snap.prev_close)
    return _between(v, min, max), v


def vix_level(ctx, min=None, max=None):
    v = ctx.snap.vix
    return _between(v, min, max), v


def vix_change_pct(ctx, min=None, max=None):
    s = ctx.snap
    v = None if s.vix is None or not s.vix_prev_close else _pct(s.vix, s.vix_prev_close)
    return _between(v, min, max), v


def above_ma(ctx, kind, n):
    v = ind.above_average(ctx.snap.closes, ctx.snap.spot, kind, n)
    return v is True, v


def rsi(ctx, min=None, max=None, n=14, bars=5):
    v = ind.intraday_rsi(ctx.snap.trail, bars, n, until=ctx.snap.minute)
    return _between(v, min, max), v


def stoch(ctx, min=None, max=None, k=14, k_smooth=3, d_smooth=3, bars=5):
    b = ind.bars(ctx.snap.trail, bars, until=ctx.snap.minute)
    r = ind.stochastic(b, k, k_smooth, d_smooth)
    v = None if r is None else r[0]
    return _between(v, min, max), v


def _window(rows: list[dict], spot: float, n: int, mode: str, cap: int | None = None) -> list[dict]:
    n = min(n, cap) if cap else n
    if mode == "above":
        rows = sorted((r for r in rows if r["strike"] > spot), key=lambda r: r["strike"])
    elif mode == "below":
        rows = sorted((r for r in rows if r["strike"] < spot), key=lambda r: -r["strike"])
    else:
        rows = sorted(rows, key=lambda r: (abs(r["strike"] - spot), r["strike"]))
    return rows[:n]


def _need(ctx, strikes: int) -> int:
    cap = ctx.snap.gex_strike_cap
    return min(strikes, cap) if cap else strikes


def gex_ratio_of(rows: list[dict]) -> float | None:
    call = sum(r["call_gex"] for r in rows)
    put = sum(r["put_gex"] for r in rows)
    small = min(call, abs(put))
    if small <= 0:
        return None
    return (call + put) / small


def gex_ratio(ctx, strikes, mode="nearest", min=None, max=None):
    rows = _window(ctx.snap.gex.get(ctx.expiry) or [], ctx.snap.spot, strikes, mode, ctx.snap.gex_strike_cap)
    v = gex_ratio_of(rows) if len(rows) == _need(ctx, strikes) else None
    return _between(v, min, max), v


def gex_put_total(ctx, strikes, mode="above", min=None, max=None):
    """The window's summed put gex (negative dollars)."""
    rows = _window(ctx.snap.gex.get(ctx.expiry) or [], ctx.snap.spot, strikes, mode, ctx.snap.gex_strike_cap)
    v = sum(r["put_gex"] for r in rows) if len(rows) == _need(ctx, strikes) else None
    return _between(v, min, max), v


def gex_max_otm(ctx, strikes, side, max):
    """How far out of the money the largest-|gex| strike of the N nearest sits, on `side`."""
    rows = _window(
        ctx.snap.gex.get(ctx.expiry) or [], ctx.snap.spot, strikes, "nearest", ctx.snap.gex_strike_cap
    )
    if not rows:
        return False, None
    top = max_by_abs(rows)
    spot = ctx.snap.spot
    v = (top["strike"] - spot) / spot * 100.0 if side == "call" else (spot - top["strike"]) / spot * 100.0
    return v <= max + 1e-12, v


def max_by_abs(rows: list[dict]) -> dict:
    return sorted(rows, key=lambda s: (-s["abs_gex"], s["strike"]))[0]


def expected_move_pct(ctx, min=None, max=None):
    """The expiry's at-the-money straddle as an expected move, in percent of spot."""
    c = ctx.chain
    k = c.nearest_strike(ctx.snap.spot)
    call, put = (c.get(k, "C"), c.get(k, "P")) if k is not None else (None, None)
    if not call or not put or not call.get("ask") or not put.get("ask"):
        return False, None
    v = expected_move(mid(call), mid(put)) / ctx.snap.spot * 100.0
    return _between(v, min, max), v


def _vol_history(ctx, lookback: int) -> tuple[float | None, list[float]]:
    """(the vol index's last close before the session, the `lookback` closes before that), or (None, [])
    when the snapshot carries fewer: an IV gate never ranks against a short history."""
    closes = ctx.snap.vol_closes
    if len(closes) < lookback + 1:
        return None, []
    return closes[-1], closes[-lookback - 1 : -1]


def vol_index_rank(ctx, min=None, max=None, lookback=252):
    """The underlying's vol index (`core.volindex`): its last close's rank between the low and high of the
    `lookback` closes before it, 0 to 100. The close known at entry, as the signal study's expected move."""
    value, history = _vol_history(ctx, lookback)
    v = None if value is None else cm.iv_rank(value, history)
    return _between(v, min, max), v


def vol_index_percentile(ctx, min=None, max=None, lookback=252):
    """The share of the `lookback` closes before the vol index's last close that sat below it, 0 to 100."""
    value, history = _vol_history(ctx, lookback)
    v = None if value is None else cm.iv_percentile(value, history)
    return _between(v, min, max), v


def vol_index_minus_rv(ctx, min=None, max=None, n=20):
    """The vol index's last close less the underlying's realized vol over its last `n` returns, both in vol
    points: positive when options price more movement than the underlying has shown."""
    closes = ctx.snap.closes[-(n + 1) :]
    iv = ctx.snap.vol_closes[-1] if ctx.snap.vol_closes else None
    v = None if len(closes) < n + 1 else cm.iv_minus_rv(iv, closes)
    return _between(v, min, max), v


def no_position_today(ctx, arm):
    """The named arm has not entered today (any status) and holds nothing open."""
    s = ctx.snap
    held = s.session in s.entries.get(arm, []) or any(p.arm == arm for p in s.open_positions)
    return not held, held


# --- structure stage -------------------------------------------------------------------------------
def mid_price(ctx, min=None, max=None):
    v = None if ctx.prices is None else abs(ctx.prices["mid"])
    return _between(v, min, max), v


def bid_ask(ctx, max):
    """The structure's whole bid-ask width, per share."""
    v = None if ctx.prices is None else 2 * ctx.prices["cost"]
    return _between(v, None, max), v


def reward_risk(ctx, min):
    """Maximum profit over maximum loss, in percent, at the structure's mid. A structure with unbounded
    profit passes."""
    if ctx.prices is None or not ctx.loss_mid or ctx.loss_mid <= 0:
        return False, None
    if ctx.profit_mid is None:
        return True, None
    v = ctx.profit_mid / ctx.loss_mid * 100.0
    return v >= min - 1e-9, v


REGISTRY = {
    "sd_change": ("market", sd_change),
    "change_pct": ("market", change_pct),
    "gap_pct": ("market", gap_pct),
    "vix_level": ("market", vix_level),
    "vix_change_pct": ("market", vix_change_pct),
    "above_ma": ("market", above_ma),
    "rsi": ("market", rsi),
    "stoch": ("market", stoch),
    "gex_ratio": ("market", gex_ratio),
    "gex_put_total": ("market", gex_put_total),
    "gex_max_otm": ("market", gex_max_otm),
    "expected_move_pct": ("market", expected_move_pct),
    "vol_index_rank": ("market", vol_index_rank),
    "vol_index_percentile": ("market", vol_index_percentile),
    "vol_index_minus_rv": ("market", vol_index_minus_rv),
    "no_position_today": ("market", no_position_today),
    "mid_price": ("structure", mid_price),
    "bid_ask": ("structure", bid_ask),
    "reward_risk": ("structure", reward_risk),
}


#: Which of core's stages each gate belongs to, by what it reads: the underlying and VIX (`signal`), the
#: chain (`chain`), or the chosen legs (`structure`). A label for the builder and `core.trace`: the
#: engine still runs a gate when the engine's own stage says so, in the arm's order.
CORE_STAGE = {
    "sd_change": "signal",
    "change_pct": "signal",
    "gap_pct": "signal",
    "vix_level": "signal",
    "vix_change_pct": "signal",
    "above_ma": "signal",
    "rsi": "signal",
    "stoch": "signal",
    "no_position_today": "signal",
    "gex_ratio": "chain",
    "gex_put_total": "chain",
    "gex_max_otm": "chain",
    "expected_move_pct": "chain",
    "vol_index_rank": "signal",
    "vol_index_percentile": "signal",
    "vol_index_minus_rv": "signal",
    "mid_price": "structure",
    "bid_ask": "structure",
    "reward_risk": "structure",
}


def _any(_v: object) -> bool:
    return True


def _condition(name: str, fn) -> ec.Condition:
    """A gate as a core condition: its parameters are its function's (those with a default are
    optional), it reads the engine's `Ctx` from the context's `ensemble` entry, and it takes no bound
    it does not name."""
    sig = list(inspect.signature(fn).parameters.values())[1:]
    required = {p.name: _any for p in sig if p.default is inspect.Parameter.empty}
    optional = {p.name: _any for p in sig if p.default is not inspect.Parameter.empty}
    return ec.Condition(
        name,
        CORE_STAGE[name],
        lambda c, p, _fn=fn: _fn(c.extra["ensemble"], **p),
        lambda _p: {"ensemble"},
        required,
        optional,
    )


#: Core's conditions with the engine's gates added: what `run` evaluates through.
CONDITIONS = ec.CORE.extended(*(_condition(name, fn) for name, (_, fn) in REGISTRY.items()))


def check_of(gate: dict) -> dict:
    """An arm's gate as a core condition spec."""
    return {"check": gate["gate"], **{k: v for k, v in gate.items() if k not in ("gate", "off")}}


def run(gate: dict, ctx: Ctx):
    """The gate's (ok, value), evaluated by `core.entrychecks` over this engine's context."""
    verdict = ec.evaluate(
        [check_of(gate)], ec.Context(extra={"ensemble": ctx}), stages=ec.STAGES, registry=CONDITIONS
    )
    check = verdict.checks[0]
    return check.passed, check.value


def stage_of(gate: dict) -> str:
    return REGISTRY[gate["gate"]][0]
