"""Derived stop policies — computed from a fully-marked open position's recorded path (Phase 1e:
put_max_cost/call_max_cost, put_settle_value/call_settle_value, put_touch_time/call_touch_time),
not run as separate entry streams. A fully-marked position's path contains every stop policy's
outcome: `open` (config.risk.json) records that path with no stop of its own
(per_side_stop_management: false), so stop-none / stop-0.75-net / stop-2.0-side / strike-touch are
all read-side computations over `open`'s rows — at 1x position cost, and with EXACT pairing (same
entries, same strikes, same credit) across every policy, since they are the same rows re-scored.

This is only valid because paper has no market impact and positions are independent. See
validate_against_control below for the derivation's own validation: `control` runs the real
0.95x-net-credit stop live, so re-deriving that exact policy from control's own recorded path and
comparing against control's REAL recorded pnl is the check that this module's arithmetic actually
reconstructs the mechanism it claims to, not an approximation of it.

What a fired side is priced at (reworked 2026-09-24). Whether a threshold fired is exact -- the
recorded running maximum either reached it or did not. WHERE it would have filled is not recorded,
because the cost path between ticks is not stored:

- A side whose REAL stop fired at or before this threshold's level filled at its real stop cost
  (`*_stop_cost`, the cost_now the trigger fired on). The crossing happened on that tick for any
  threshold between the real trigger and that fill, so this is exact -- and it is what keeps the
  derivation reproducing an arm's own mechanism to the cent.
- Any other fired side is priced AT ITS THRESHOLD: the first tick at or past the trigger. That is
  optimistic by the gap-through a real stop pays (a tick lands past the trigger, not on it), which
  `validate_against_control` measures from real stops as `fill_over_trigger`. Until 2026-09-24 these
  sides were priced at the running MAXIMUM instead -- the worst the side ever got, on average 2.1x
  credit on the never-stopping control, ~$1.3M of penalty against every stop policy on that arm --
  which answered "what if we had stopped at the worst moment", not "what if we had stopped".
- `strike-touch` has no threshold and no recorded cost at the touch, so it keeps the running
  maximum as its proxy, and says so.

Every derived trade is charged the four fees the real book pays (`Fees`): the opening fee, the
closing fee for each side a stop or force-close takes off, and $5 per ITM strike on a side held to
settlement. The opening fee was missing until 2026-09-24, so every derived book looked better than
the real one by it -- enough to flip width-5's answer from "the stop cost money" to "it saved money".
A force-closed trade is valued at its real force-close fill on every side the policy did not stop:
a force-close is not a stop, so it happens under every policy, and scoring those sides as held to
settlement counted ~$89k of force-close timing as stop policy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# Named policies: (basis, multiple). basis is one of:
#   "net"   -- multiple x net_credit (the whole IC's credit) is the trigger, per side's own max cost
#   "side"  -- multiple x that side's own credit is the trigger
#   "none"  -- never fires; every side is held to settlement
#   "touch" -- fires the first time spot reached (crossed toward ITM) that side's short strike
POLICIES = {
    "control": ("net", 0.95),  # today's canonical MEIC+ rule -- for validate_against_control only
    "stop-none": ("none", None),
    "stop-0.75-net": ("net", 0.75),
    "stop-2.0-side": ("side", 2.0),
    "strike-touch": ("touch", None),
}

# The stop_trigger_ratio sweep (2026-08-15, advisor creative proposal #12). Same "net" basis the
# real per-side stop uses -- a side's cost against the WHOLE IC's net credit -- so a grid point is
# the deployed mechanism at a different number, not a different mechanism. Brackets the deployed
# 0.95 in both directions, matching the advice bounds meic already declares for this parameter.
GRID_RATIOS = (0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20, 1.25)


_MULT = 100  # dollar_multiplier is 100 on every recorded row (MEIC trades whole contracts only)

# Costs are recorded to 4 decimals, so a real stop that fired exactly on its trigger can be stored a
# rounding unit UNDER it: width-10's 1.5437 fill on a 0.95 x 1.625 = 1.54375 trigger. Compared
# exactly, "did it fire" said no and the stopped side was priced as held to settlement -- 13 of
# 4,817 real stops, up to $845 each. Every fire test compares at the precision the costs carry.
_COST_EPS = 5e-5


@dataclass(frozen=True)
class Fees:
    """The fee schedule a derived book is charged on -- `paper.stop_fees()` builds it from the one
    the real book pays. Injected rather than imported to keep this module free of the fee-schedule
    dependency.

    open(symbol), one_side(symbol), full_ic(symbol): the opening fee and the closing fees for one
        side or both, as `paper.open_fees` / `close_fees_one_side` / `close_fees_full_ic`.
    expire_side(row, side, underlying): what settling `side` held to expiry at `underlying` costs --
        $5 per ITM strike, never per contract -- or None when it cannot be known.
    """

    open: Callable[[str], float]
    one_side: Callable[[str], float]
    full_ic: Callable[[str], float]
    expire_side: Callable[[dict, str, float | None], float | None]


def _thresholds(row: dict, basis: str, multiple) -> tuple[float | None, float | None]:
    """Each side's trigger, in the side's cost-to-close units. None for `none`/`touch`, which have
    no cost trigger."""
    if basis == "net":
        net = row.get("net_credit")
        t = None if net is None else multiple * net
        return t, t
    if basis == "side":
        put, call = row.get("put_credit"), row.get("call_credit")
        return (None if put is None else multiple * put), (None if call is None else multiple * call)
    return None, None


def _fill_price(row: dict, side: str, basis: str, thresh: float | None) -> float | None:
    """Where a FIRED side is bought back -- see the module docstring for the three cases."""
    max_cost = row.get(f"{side}_max_cost")
    if basis == "touch":
        return max_cost  # no threshold and no cost recorded at the touch: the running max, a proxy
    real_fill = row.get(f"{side}_stop_cost")
    ratio, net = row.get("stop_trigger_current"), row.get("net_credit")
    if real_fill is not None and ratio is not None and net is not None and thresh >= ratio * net - _COST_EPS:
        return real_fill  # the real stop crossed on this same tick: its fill is this fill
    return thresh


def derive(row: dict, policy_name: str | tuple, *, fees: Fees) -> dict:
    """Score one ic_trades row (a plain dict — pass dict(sqlite_row) for a sqlite3.Row) under
    `policy_name` from POLICIES, or under a raw ``(basis, multiple)`` spec (what the grid passes,
    so a swept ratio needs no entry in POLICIES). Returns
    {"derivable", "put_fired", "call_fired", "pnl", "fee"}. `derivable` is False (pnl None) when a
    side that needed to be priced is missing the recorded field the policy requires -- e.g. a
    pre-Phase-1e row with no put_max_cost/put_settle_value, a force-closed row whose side exit was
    not attached (`put_exit_price`/`call_exit_price`, from `ic_spread_legs`), or a held side whose
    settlement price is unrecorded so its settlement fee cannot be known.

    `pnl` is GROSS (credit minus fill, settle or force-close cost, both sides) -- ic_trades.pnl's own
    convention -- and `fee` is every fee the derived book pays, opening fee included, so
    `pnl - fee` and a real row's `pnl - fees` are the same quantity.
    """
    basis, multiple = POLICIES[policy_name] if isinstance(policy_name, str) else policy_name
    if basis not in ("none", "touch", "net", "side"):
        raise ValueError(f"unknown basis {basis!r}")
    forced = row.get("status") == "force_closed"
    thresholds = dict(zip(("put", "call"), _thresholds(row, basis, multiple), strict=True))

    fired: dict[str, bool] = {}
    price: dict[str, float | None] = {}
    expiry_fee = 0.0
    settle_unknown = False
    for side in ("put", "call"):
        if basis == "none":
            fired[side] = False
        elif basis == "touch":
            fired[side] = row.get(f"{side}_touch_time") is not None
        else:
            thresh, max_cost = thresholds[side], row.get(f"{side}_max_cost")
            fired[side] = thresh is not None and max_cost is not None and max_cost >= thresh - _COST_EPS
        if fired[side]:
            price[side] = _fill_price(row, side, basis, thresholds[side])
        elif forced:
            price[side] = row.get(f"{side}_exit_price")  # a force-close happens under every policy
        else:
            price[side] = row.get(f"{side}_settle_value")
            side_fee = fees.expire_side(row, side, row.get("settle_underlying"))
            if side_fee is None:
                settle_unknown = True
            else:
                expiry_fee += side_fee

    pnls: dict[str, float | None] = {}
    for side in ("put", "call"):
        credit = row.get(f"{side}_credit")
        pnls[side] = (
            None if credit is None or price[side] is None else round((credit - price[side]) * _MULT, 2)
        )
    if pnls["put"] is None or pnls["call"] is None or settle_unknown:
        return {
            "derivable": False,
            "put_fired": fired["put"],
            "call_fired": fired["call"],
            "pnl": None,
            "fee": None,
        }

    symbol = row.get("symbol")
    closed = {side: fired[side] or forced for side in ("put", "call")}
    if closed["put"] and closed["call"]:
        both_same_way = fired["put"] == fired["call"]  # both stopped, or both force-closed
        close_fee = fees.full_ic(symbol) if both_same_way else 2 * fees.one_side(symbol)
    elif closed["put"] or closed["call"]:
        close_fee = fees.one_side(symbol)
    else:
        close_fee = 0.0

    return {
        "derivable": True,
        "put_fired": fired["put"],
        "call_fired": fired["call"],
        "pnl": round(pnls["put"] + pnls["call"], 2),
        "fee": round(fees.open(symbol) + close_fee + expiry_fee, 4),
    }


def censored_above(row: dict) -> float | None:
    """The net-credit ratio above which THIS row's recorded path says nothing, or None if the row
    can answer any ratio.

    The trap the grid would otherwise walk into. `*_max_cost` is a running maximum recorded *while
    the side is open*, so a side that actually stopped stopped being observed at that moment: its
    max_cost is the stop fill and the path beyond it was never seen. Scoring a LOOSER ratio against
    it would silently answer "that threshold never fired" when the truth is "we cut the recording
    off before it could." A side held to settlement has a complete path and censors nothing.

    So this returns max(recorded max_cost) / net_credit for a row whose status is 'stopped' — every
    ratio at or below that is answerable, everything above it is not — and None otherwise. Note the
    permissive `open` arm runs with per_side_stop_management off, which is exactly why it is the
    arm the sweep is scored over: none of its rows censor anything.
    """
    if row.get("status") != "stopped":
        return None
    net_credit = row.get("net_credit")
    if not net_credit:
        return None
    costs = [c for c in (row.get("put_max_cost"), row.get("call_max_cost")) if c is not None]
    return round(max(costs) / net_credit, 6) if costs else None


def capital_at_risk(row: dict) -> float | None:
    """An IC's defined max loss: (wing_width - net_credit) x 100 x quantity. Exact from the row --
    both inputs are recorded -- so the grid's result is reportable on max risk rather than only in
    dollars, which is what makes it comparable with any other arm's return_on_capital."""
    width, credit = row.get("wing_width"), row.get("net_credit")
    if width is None or credit is None:
        return None
    return round((width - credit) * _MULT * (row.get("quantity") or 1), 2)


def shadow_settle(row: dict, *, fees: Fees) -> dict:
    """The per-fill shadow ledger: what this fill would have been worth held to expiry with no stop
    at all, beside what it really did, plus the excursion the stop threshold is actually compared
    against.

    The counterfactual is not an estimate — `*_settle_value` is recorded for stopped sides too, so
    a stopped fill's held-to-expiry value is a stored number rather than a reconstruction.

    `mae_over_credit` is max_cost over net_credit, NOT the spot-based `*_mae_spot` columns. The
    proposal that asked for this called it "the empirical value stop_trigger_ratio is compared
    against", and that value is a COST ratio: the stop fires when a side's cost-to-close reaches
    ratio x net_credit. Spot excursion is a different quantity that no threshold here reads.

    `mfe_over_credit` is always None, and deliberately: favourable excursion is not recorded
    anywhere (only the adverse running maximum is), and the stream cache keeps no quote history to
    reconstruct it from. Rendering it as 0.0 would be the "misleadingly precise zero" this suite
    already has a rule about. It needs its own instrumentation change on the write path.
    """
    none_out = derive(row, "stop-none", fees=fees)
    net_credit = row.get("net_credit")
    puts, calls = row.get("put_max_cost"), row.get("call_max_cost")
    worst = max([c for c in (puts, calls) if c is not None], default=None)
    real_pnl, real_fee = row.get("pnl"), row.get("fees")

    shadow_net = None if none_out["pnl"] is None else round(none_out["pnl"] - none_out["fee"], 2)
    real_net = None if real_pnl is None else round(real_pnl - (real_fee or 0.0), 2)
    return {
        "ic_order_id": row.get("ic_order_id"),
        "trade_date": row.get("trade_date"),
        "arm": row.get("arm"),
        "symbol": row.get("symbol"),
        "stop_fired": row.get("status") == "stopped",
        "credit_at_entry": net_credit,
        "capital_at_risk": capital_at_risk(row),
        "realized_net": real_net,
        "shadow_settle_net": shadow_net,
        # Positive means the stop COST money: holding would have paid more than stopping did.
        "stop_cost": None if (shadow_net is None or real_net is None) else round(shadow_net - real_net, 2),
        "mae_over_credit": (None if (worst is None or not net_credit) else round(worst / net_credit, 4)),
        "mfe_over_credit": None,  # not recorded; see the docstring
        "censored_above": censored_above(row),
    }


def score_grid(row: dict, *, fees: Fees, ratios: tuple = GRID_RATIOS) -> dict[float, dict]:
    """Score one row at every ratio in `ratios` on the net basis — the whole stop curve for one
    fill, from one recorded path, at zero risk and zero extra position cost.

    A ratio this row cannot answer comes back `derivable: False` with `censored: True` rather than
    a number (see `censored_above`). That distinction is the point: "this threshold would not have
    fired" and "we stopped watching before it could" are opposite conclusions.
    """
    limit = censored_above(row)
    out: dict[float, dict] = {}
    for ratio in ratios:
        if limit is not None and ratio > limit:
            out[ratio] = {
                "derivable": False,
                "censored": True,
                "pnl": None,
                "fee": None,
                "put_fired": None,
                "call_fired": None,
            }
            continue
        scored = derive(row, ("net", ratio), fees=fees)
        out[ratio] = {**scored, "censored": False}
    return out


def validate_against_control(rows: list[dict], *, fees: Fees, tolerance: float = 0.5) -> dict:
    """The derivation's own validation: re-derive each STOPPING arm's real mechanism from its own
    recorded paths and compare against its REAL recorded gross `pnl`. That checks that derive()'s
    arithmetic reconstructs the mechanism it claims to, not merely a plausible approximation of it.

    Which arms: those with at least one real stop in `rows` (a `*_stop_cost` recorded). Until
    2026-09-24 this was hard-coded to `control` -- right when `control` was the book that stopped,
    and meaningless after the 2026-08-21 redefinition made `control` the never-stopping substrate:
    re-deriving a 0.95 stop over a book that never ran one failed 4,678 of 6,043 rows. Each row is
    re-derived at its OWN recorded `stop_trigger_current`, the ratio it really ran.

    Scoped to status in ('stopped', 'expired') -- a force-closed row is valued at its real force
    close under every policy, so re-deriving it tests nothing. Also reports `fill_over_trigger`:
    real stop fill over its trigger, the gap-through that pricing any OTHER threshold at its
    trigger leaves out (see the module docstring).

    tolerance is a dollar bound per trade. Returns a summary dict; raises nothing.
    """
    stopping_arms = sorted(
        {
            r.get("arm")
            for r in rows
            if r.get("put_stop_cost") is not None or r.get("call_stop_cost") is not None
        }
        - {None}
    )
    compared, mismatches, overshoot = [], [], []
    skipped_force_closed = skipped_no_recorded_pnl = 0
    for row in rows:
        if row.get("arm") not in stopping_arms:
            continue
        status = row.get("status")
        if status == "force_closed":
            skipped_force_closed += 1
            continue
        if status not in ("stopped", "expired"):
            continue
        ratio, net = row.get("stop_trigger_current"), row.get("net_credit")
        for side in ("put", "call"):
            fill = row.get(f"{side}_stop_cost")
            if fill is not None and ratio and net:
                overshoot.append(fill / (ratio * net))
        real_pnl = row.get("pnl")
        if real_pnl is None or ratio is None:
            skipped_no_recorded_pnl += 1
            continue
        derived = derive(row, ("net", ratio), fees=fees)
        if not derived["derivable"]:
            skipped_no_recorded_pnl += 1
            continue
        delta = round(derived["pnl"] - real_pnl, 2)
        entry = {
            "ic_order_id": row.get("ic_order_id"),
            "arm": row.get("arm"),
            "real_pnl": real_pnl,
            "derived_pnl": derived["pnl"],
            "delta": delta,
        }
        compared.append(entry)
        if abs(delta) > tolerance:
            mismatches.append(entry)

    overshoot.sort()

    def _q(q: float) -> float | None:
        return round(overshoot[min(len(overshoot) - 1, int(q * len(overshoot)))], 4) if overshoot else None

    return {
        "arms": stopping_arms,
        "compared": len(compared),
        "mismatches": mismatches,
        "skipped_force_closed": skipped_force_closed,
        "skipped_no_recorded_pnl": skipped_no_recorded_pnl,
        "ok": not mismatches and len(compared) > 0,
        "max_abs_delta": max((abs(e["delta"]) for e in compared), default=None),
        "fill_over_trigger": {"stops": len(overshoot), "median": _q(0.5), "p90": _q(0.9)},
    }
