"""The settings grid: exit and width settings for one saved automation, guarded against overfitting
(#1294). Pure.

The idea search reads one run's trades. A different stop, target, time exit or width changes the trades
themselves, so each setting here is its own replay: the driver (`scripts/exit_grid.py`) replays the
automation as its Builder run did and every setting beside it, in one pass over that run's whole span
(the 11-14 rule), and this reads the result. It hands back hypotheses, never a change.

- **The grid is fixed here** (`AXES`), before any result is read: one setting changed at a time, at
  coarse values, and each exit the automation sets also switched off. A setting the automation already
  has is the base, not a candidate, and a setting the module's own check refuses is left out before the
  replay (`variants`).
- **The split is the idea search's** (`ideasearch.split_date`, fixed when the grid is queued). The
  search reads the P&L of sessions before it only; the later sessions are read once per declared
  setting (`holdout`), and every use is logged.
- **A setting is scored by what it changes.** Per design session, its net less the base's (0 where
  neither traded), every session the run replayed; the mean change's session bootstrap interval must sit
  above zero.
- **Against chance, counting every try**: `core.stats.setting_placebo`, a sign-flip max-T over the
  whole grid; the setting must beat the best random change of the menu (`p_menu`). Holm and BH over
  the count tried are reported beside it.
- **Plateau and stability.** A setting's neighbours on its axis must agree (`robustness.neighbourhood`,
  the base at 0 among them; a lone peak fails); the mean change must be positive in both halves of
  the design part and in `FOLDS_NEEDED` of its `FOLDS` folds.
- **Costs and power.** The change must still be positive with every trade's fees and slippage at 1.5x
  (settlement does not scale), and `robustness.planned_mde` over the held-out sessions must say that
  part can detect it, or the setting reads "not testable on our history".
- **Shrunk claims.** Beside the design gain is the gain left after the grid's own luck: the setting's
  statistic less the median of the best random change's.
"""

from __future__ import annotations

import copy
import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from cherrypick.core import robustness, stats, structures

from . import ideasearch

KIND = "verdict"
SEP = "~"
FOLDS = ideasearch.FOLDS
FOLDS_NEEDED = ideasearch.FOLDS_NEEDED
ALPHA = ideasearch.ALPHA
BH_Q = ideasearch.BH_Q
DRAWS = ideasearch.DRAWS
SEED = ideasearch.SEED
#: Fewest held-out sessions a setting must change for its one test to give a verdict.
HELD_MIN = ideasearch.HELD_MIN
HELD_LEVEL = ideasearch.HELD_LEVEL
#: The cost multiple the change must survive (`robustness.cost_stress`'s pass stress).
COST_STRESS = 1.5
#: Fewest design sessions a setting must change to be scored.
MIN_MOVED = 10

#: axis -> (what it sets, in words; the values tried; when it applies). `exit` axes set that exit key
#: (`off` removes it); `width` scales every leg placed by points from another leg.
AXES: dict[str, dict[str, Any]] = {
    "stop_credit_multiple": {
        "words": "a stop at {v}x the credit",
        "values": (1.0, 1.5, 2.0, 3.0),
        "applies": "credit",
    },
    "stop_debit_pct": {"words": "a stop at {v}% of the debit", "values": (25, 50, 75), "applies": "debit"},
    "profit_target_pct": {"words": "a profit target at {v}% of max profit", "values": (25, 50, 75)},
    "close_before_expiry_min": {
        "words": "a close {v} minutes before expiry",
        "values": (15, 60, 120),
        "applies": "same_day",
    },
    "close_at_dte": {"words": "a close {v} days before expiry", "values": (7, 14, 21), "applies": "later"},
    "width": {"words": "the width at {v}x", "values": (0.5, 1.5, 2.0)},
}
OFF_WORDS = {
    "stop_credit_multiple": "no credit stop",
    "stop_debit_pct": "no debit stop",
    "profit_target_pct": "no profit target",
    "close_before_expiry_min": "no close before expiry",
    "close_at_dte": "no close by days to expiry",
}


# --------------------------------------------------------------------------- the grid
def _same_day(arm: Mapping) -> bool:
    e = arm.get("expiry") or {}
    return e.get("kind") in ("dte", "market_days") and e.get("days") == 0


def _sold(arm: Mapping) -> bool:
    return any(int(g.get("side", 0)) < 0 for g in arm.get("legs") or [] if isinstance(g.get("side"), int))


def _bought(arm: Mapping) -> bool:
    return any(int(g.get("side", 0)) > 0 for g in arm.get("legs") or [] if isinstance(g.get("side"), int))


def _applies(axis: str, arm: Mapping) -> bool:
    """Whether an axis can act on the automation, from its rule alone (no P&L is read): a credit stop
    on a structure that enters for a credit, a debit stop on one that enters for a debit (both where
    the rule doesn't say, but only the kind it already sets where it sets one; a setting that never
    fires changes no session and is not counted), the minutes-before-expiry close on a same-day
    expiry and the days-to-expiry close on a later one, a width where a leg is placed by points from
    another."""
    when = AXES[axis].get("applies")
    own = arm.get("exit") or {}
    kind = structures.entry_kind(arm)
    if when == "credit":
        return _sold(arm) and kind != "debit" and "stop_debit_pct" not in own
    if when == "debit":
        return _bought(arm) and kind != "credit" and "stop_credit_multiple" not in own
    if when == "same_day":
        return _same_day(arm)
    if when == "later":
        return not _same_day(arm)
    if axis == "width":
        return bool(_width_legs(arm))
    return True


def _width_legs(arm: Mapping) -> list[str]:
    out = []
    for g in arm.get("legs") or []:
        rule = g.get("strike") or {}
        if rule.get("rule") == "from_leg" and rule.get("points") and rule.get("pct") is None:
            out.append(g["name"])
    return out


def _fmt(v: float) -> str:
    return f"{v:g}"


def variant_name(base: str, vid: str) -> str:
    return f"{base}{SEP}{vid}"


def _own(arm: dict, name: str, base: str) -> dict:
    """The setting's copy under its own name: a group cap counts only its own positions, and a rule
    that names the automation names the copy."""
    out = copy.deepcopy(arm)
    out["name"] = name
    budget = out.get("budget")
    if isinstance(budget, dict) and budget.get("members") is not None:
        budget["members"] = [name]
    for g in out.get("gates") or []:
        for k, v in list(g.items()):
            if v == base:
                g[k] = name
    return out


def variants(arm: Mapping) -> dict[str, dict]:
    """Every setting the grid tries for `arm` (the engine's loaded form), from its rule alone: {id:
    {"axis", "value", "words", "arm"}}. The id is `axis=value` (`width=x1.5`, `profit_target_pct=off`).
    The automation's own value on an axis is the base, never a candidate."""
    base = arm["name"]
    exit_rules = arm.get("exit") or {}
    out: dict[str, dict] = {}
    for axis, spec in AXES.items():
        if not _applies(axis, arm):
            continue
        if axis == "width":
            for m in spec["values"]:
                vid = f"width=x{_fmt(m)}"
                a = copy.deepcopy(dict(arm))
                for g in a["legs"]:
                    if g["name"] in _width_legs(arm):
                        g["strike"]["points"] = g["strike"]["points"] * m
                out[vid] = {"axis": axis, "value": m, "words": spec["words"].format(v=_fmt(m)), "arm": a}
            continue
        own = exit_rules.get(axis)
        for v in spec["values"]:
            if own is not None and float(own) == float(v):
                continue
            vid = f"{axis}={_fmt(v)}"
            a = copy.deepcopy(dict(arm))
            a["exit"] = {**exit_rules, axis: v}
            out[vid] = {"axis": axis, "value": v, "words": spec["words"].format(v=_fmt(v)), "arm": a}
        if own is not None:
            a = copy.deepcopy(dict(arm))
            a["exit"] = {k: v for k, v in exit_rules.items() if k != axis}
            out[f"{axis}=off"] = {"axis": axis, "value": None, "words": OFF_WORDS[axis], "arm": a}
    for vid, row in out.items():
        row["arm"] = _own(row["arm"], variant_name(base, vid), base)
    return out


def base_value(arm: Mapping, axis: str) -> float | None:
    """The automation's own value on an axis: its exit key, 1 for a width, None where it sets none."""
    if axis == "width":
        return 1.0
    v = (arm.get("exit") or {}).get(axis)
    return None if v is None else float(v)


# --------------------------------------------------------------------------- scoring
def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f else 0.0


def _net(t: Mapping) -> float:
    net = t.get("net")
    return _num(net.get(KIND) if isinstance(net, dict) else net)


def _cost(t: Mapping) -> float:
    s = t.get("slippage")
    return _num(t.get("fees")) + _num(s.get(KIND) if isinstance(s, dict) else s)


def session_nets(trades: Iterable[Mapping], *, stress: float = 1.0) -> dict[str, float]:
    """Net by entry session, each trade's fees and slippage at `stress` times."""
    out: dict[str, float] = {}
    for t in trades:
        s = str(t.get("session") or "")[:10]
        if s:
            out[s] = out.get(s, 0.0) + _net(t) - (stress - 1.0) * _cost(t)
    return out


def changes(
    base: Sequence[Mapping], setting: Sequence[Mapping], sessions: Sequence[str], *, stress: float = 1.0
) -> dict[str, float]:
    """Per session of `sessions`, the setting's net less the base's (0 where neither traded)."""
    b, v = session_nets(base, stress=stress), session_nets(setting, stress=stress)
    return {s: round(v.get(s, 0.0) - b.get(s, 0.0), 6) for s in sessions}


def _mean(xs: Sequence[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def stability(diff: Mapping[str, float], design: Sequence[str]) -> dict:
    """The mean change's sign in each half of the design part and in each fold (equal runs of design
    sessions, in order)."""

    def part(chunk):
        m = _mean([diff[s] for s in chunk])
        return {
            "sessions": len(chunk),
            "mean": None if m is None else round(m, 4),
            "positive": bool(m and m > 0),
        }

    n = len(design)
    halves = [part(design[: n // 2]), part(design[n // 2 :])]
    folds = [part(design[n * f // FOLDS : n * (f + 1) // FOLDS]) for f in range(FOLDS)]
    held = sum(1 for f in folds if f["positive"])
    return {
        "halves": halves,
        "folds": folds,
        "folds_positive": held,
        "stable": all(h["positive"] for h in halves) and held >= FOLDS_NEEDED,
    }


def plateau(vid: str, grid: Mapping[str, dict], means: Mapping[str, float | None], base: Mapping) -> dict:
    """The setting's neighbours on its own axis, the automation's own value among them at 0 change.
    An `off` setting has no neighbours on a scale."""
    row = grid[vid]
    if row["value"] is None:
        return {"verdict": "no_neighbours", "rows": []}
    axis = row["axis"]
    on_axis = {
        float(r["value"]): means.get(k)
        for k, r in grid.items()
        if r["axis"] == axis and r["value"] is not None
    }
    own = base_value(base, axis)
    if own is not None:
        on_axis.setdefault(own, 0.0)
    read = robustness.neighbourhood(on_axis, float(row["value"]))
    return {"verdict": read["verdict"], "rows": read["rows"]}


def search(
    base: Sequence[Mapping],
    settings: Mapping[str, Sequence[Mapping]],
    grid: Mapping[str, dict],
    arm: Mapping,
    sessions: Sequence[str],
    split: str,
    *,
    draws: int = DRAWS,
    seed: int = SEED,
    tick=None,
) -> dict:
    """The grid read over its design sessions (every session the run replayed before `split`). `base`
    and `settings` are the replay's trades, the base's and each setting's; the held-out sessions are
    counted by name only. `grid` is `variants`' answer for `arm`."""
    design = [s for s in sessions if s < split]
    held = [s for s in sessions if s >= split]
    diffs = {k: changes(base, settings.get(k) or [], design) for k in grid}
    moved = {k: d for k, d in diffs.items() if sum(1 for v in d.values() if v) >= MIN_MOVED}
    placebo = stats.setting_placebo(moved, draws=draws, seed=seed, tick=tick)
    ranked = placebo["candidates"]
    pvals = {k: v["p"] for k, v in ranked.items() if v.get("p") is not None}
    holm = stats.holm(pvals, ALPHA) if pvals else {}
    bh = stats.benjamini_hochberg(pvals, BH_Q) if pvals else {}
    null_high = placebo["null_max_median"]
    n = len(design)
    means = {
        k: (_mean(list(d.values())) if k in ranked and ranked[k].get("z") is not None else None)
        for k, d in diffs.items()
    }
    base_nets = session_nets(base)
    rows = []
    for k, row in grid.items():
        d = diffs[k]
        read = ranked.get(k) or {}
        moved_n = sum(1 for v in d.values() if v)
        if read.get("z") is None:
            rows.append(
                {
                    "id": k,
                    "axis": row["axis"],
                    "setting": row["words"],
                    "moved": moved_n,
                    "scored": False,
                    "why": "changes no session"
                    if moved_n == 0
                    else f"changes fewer than {MIN_MOVED} sessions",
                    "survives": False,
                }
            )
            continue
        vals = [d[s] for s in design]
        ci = stats.bootstrap_ci(vals, seed=seed)
        z, scale = read["z"], read["scale"]
        z_left = max(0.0, z - null_high) if null_high is not None else 0.0
        stressed = changes(base, settings.get(k) or [], design, stress=COST_STRESS)
        stressed_mean = _mean(list(stressed.values()))
        mde = (
            robustness.planned_mde(vals, len(held))
            if len(held) >= 2
            else {"planned_n": len(held), "mde": None}
        )
        effect = z_left * scale / n
        testable = (
            mde.get("mde") is not None and len(held) >= HELD_MIN and effect > 0 and mde["mde"] <= effect
        )
        plat = plateau(k, grid, means, arm)
        stab = stability(d, design)
        gates = {
            "gains": ci is not None and ci[0] > 0,
            "beats_menu": read["p_menu"] <= ALPHA,
            "plateau": plat["verdict"] != "lone_peak",
            "stable": stab["stable"],
            "pays_at_1_5x": stressed_mean is not None and stressed_mean > 0,
        }
        rows.append(
            {
                "id": k,
                "axis": row["axis"],
                "setting": row["words"],
                "moved": moved_n,
                "scored": True,
                "mean_change": read["mean"],
                "change_ci": None if ci is None else [round(ci[0], 2), round(ci[1], 2)],
                "gain": round(sum(vals), 2),
                "gain_shrunk": round(z_left * scale, 2),
                "z": z,
                "p": read["p"],
                "p_menu": read["p_menu"],
                "holm": bool(holm.get(k)),
                "bh": bool(bh.get(k)),
                "plateau": plat,
                "stability": stab,
                "cost": {"mean_change_at_1_5x": None if stressed_mean is None else round(stressed_mean, 4)},
                "held_out": {"planned_sessions": len(held), "mde": mde.get("mde"), "testable": testable},
                "gates": gates,
                "survives": all(gates.values()),
            }
        )
    rows.sort(
        key=lambda r: (
            (0, -r["gain_shrunk"], r["id"])
            if r["survives"]
            else (1, r["p_menu"], -r["z"])
            if r["scored"]
            else (2, 0, r["id"])
        )
    )
    return {
        "split": split,
        "base": {
            "sessions": n,
            "trades": sum(1 for t in base if str(t.get("session") or "")[:10] < split),
            "net": round(sum(v for s, v in base_nets.items() if s < split), 2),
            "held_out_sessions": len(held),
        },
        "tried": placebo["tried"],
        "draws": placebo["draws"],
        "null_max_median": null_high,
        "survivors": sum(1 for r in rows if r["survives"]),
        "holm_survivors": sum(1 for r in rows if r.get("holm")),
        "bh_survivors": sum(1 for r in rows if r.get("bh")),
        "rules": rules(),
        "rows": rows,
    }


def rules() -> dict:
    """The fixed rules a result was read under, stored with it."""
    return {
        "axes": {
            k: {"values": list(v["values"]), "applies": v.get("applies", "any")} for k, v in AXES.items()
        },
        "min_moved": MIN_MOVED,
        "folds": FOLDS,
        "folds_needed": FOLDS_NEEDED,
        "alpha": ALPHA,
        "bh_q": BH_Q,
        "cost_stress": COST_STRESS,
        "seed": SEED,
        "held_min": HELD_MIN,
        "held_level": HELD_LEVEL,
    }


# --------------------------------------------------------------------------- the one held-out test
def holdout(
    base: Sequence[Mapping],
    setting: Sequence[Mapping],
    sessions: Sequence[str],
    split: str,
    *,
    seed: int = SEED,
) -> dict:
    """A declared setting's one test on the sessions from `split`: the mean change's interval (90%
    two-sided, so its lower bound is one-sided 95%) must sit above zero. Fewer than `HELD_MIN` changed
    sessions gives no verdict."""
    held = [s for s in sessions if s >= split]
    d = changes(base, setting, held)
    vals = [d[s] for s in held]
    moved = sum(1 for v in vals if v)
    out: dict[str, Any] = {
        "sessions": len(held),
        "moved": moved,
        "mean_change": None if not vals else round(statistics.fmean(vals), 4),
        "gain": round(sum(vals), 2),
    }
    if moved < HELD_MIN:
        return {**out, "ci": None, "verdict": "no_verdict"}
    ci = stats.bootstrap_ci(vals, level=HELD_LEVEL, seed=seed)
    return {**out, "ci": [round(ci[0], 2), round(ci[1], 2)], "verdict": "passed" if ci[0] > 0 else "failed"}


def declaration(result: Mapping, setting: str, *, result_path: str) -> dict:
    """A register row drafted from one surviving setting, its split already fixed. No figures: the id,
    the study and the mechanism are the declarer's to write."""
    row = next((r for r in result.get("rows") or [] if r.get("id") == setting), None)
    if row is None:
        raise ValueError(f"no setting {setting!r} in this result")
    span = result.get("sessions") or [None, None]
    return {
        "id": None,
        "study": None,
        "title": f"{result.get('automation')}: {row['setting']}",
        "status": "declared",
        "legacy": False,
        "setup": result.get("automation"),
        "side": "exit",
        "structure": "exit or width setting",
        "window": "held-out sessions of one replay of the automation and its settings",
        "measure": (
            f"per session, the net with {row['setting']} less the automation's own, every session replayed"
        ),
        "expected": "positive: the setting raises the net",
        "sample": (
            f"one replay of {result.get('automation')} and its settings over {span[0]}..{span[-1]}, "
            f"split fixed at {result.get('split')} when the grid was queued; the search read only sessions "
            "before it"
        ),
        "rule": (
            f"on the sessions from {result.get('split')}: the mean change's session-bootstrap interval "
            f"({int(HELD_LEVEL * 100)}% two-sided, seed {SEED}) has its lower bound above zero; fewer than "
            f"{HELD_MIN} changed sessions = no verdict; tested once"
        ),
        "mechanism": None,
        "rivals": [
            {
                "explanation": "chance: the best of the grid the search tried",
                "check": f"beat the best random change of {result.get('tried')} settings in the design part, "
                "and the held-out sessions were never read by the search",
            },
            {
                "explanation": "a lone peak on its axis",
                "check": "the neighbouring settings agreed (plateau) in the design part",
            },
        ],
        "settings_grid": {"result": result_path, "setting": setting, "split": result.get("split")},
    }
