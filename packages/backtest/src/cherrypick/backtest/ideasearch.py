"""The idea search: entry filters for one saved automation, guarded against overfitting (#1294). Pure.

It reads one Builder run of the automation (one run over its whole window, the 11-14 rule) and hands
back hypotheses, never a change: a surviving idea becomes a declared register item, tested once on the
part of history the search never read, then a paper experiment arm beside its control.

- **The split is fixed first** (`split_date`, two thirds of the run's span, set when the search is
  queued and stored with the result). The search reads the P&L only of trades entered before it. The
  later part's sessions are named, so a filter's held-out size can be counted from what was known at
  entry, but its P&L is read only by `holdout`, once per declared idea, and every use is logged.
- **A filter is scored by what it skips.** Skipping sessions raises total P&L only if they lost money,
  so the score is the skipped sessions' mean net, with its session bootstrap interval, and the interval
  must sit below zero. A filter that lifts the per-trade mean by skipping profitable days fails.
- **Against chance, counting every try.** Every candidate is read against random filters by
  `core.stats.menu_placebo`: it must beat the best random filter of the whole menu (`p_menu`). The
  result says how many were tried, and which survive Holm and BH over that count.
- **A short menu fixed here** (`DIMS`, `EVENT_LABELS`), before any result is read: each condition is a
  bin or a tail of bins of an entry-time feature known before the session's entry (the prior close's
  VIX, RSI, trend and move, VIX over realised volatility, the opening gap, the weekday, an event day).
  At most two conditions, each a single bin, and the second must add to the first on its own.
- **Plateau and stability.** An ordered condition's neighbours must agree (`robustness.neighbourhood`;
  a lone peak fails), and the skipped mean must be negative in both halves of the design part and in
  `FOLDS_NEEDED` of its `FOLDS` walk-forward folds.
- **Costs and power.** The kept trades must still pay at 1.5x fees and slippage
  (`robustness.cost_stress`; settlement is the exercise charge and does not scale), and
  `robustness.planned_mde` must say the held-out part can detect the effect, or the idea reads "not
  testable on our history" and is never sent to it.
- **Shrunk claims.** The design uplift is biased by the search. Beside it is the uplift left after the
  menu's own luck: the candidate's statistic less the median of the best random filter's.

Description only: no verdict on the automation, no config change, nothing to the advisor. The verdict
of a declared idea is `holdout`'s, by the rule stated in its declaration.
"""

from __future__ import annotations

import math
import statistics
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from cherrypick.core import indicators, robustness, stats

KIND = "verdict"
#: The split's place in the run's span: the search reads the first two thirds.
SPLIT_SHARE = 2 / 3
#: Fewest design sessions a filter must skip to be tried; and the largest share it may skip.
MIN_SKIPPED = 10
MAX_SKIP_SHARE = 0.5
FOLDS = 4
FOLDS_NEEDED = 3
ALPHA = 0.05
BH_Q = 0.10
DRAWS = 2000
SEED = 1294
#: Fewest held-out sessions an idea must skip for its one test to give a verdict.
HELD_MIN = 10
#: The held-out rule: the skipped sessions' one-sided 95% upper bound (a two-sided 90% interval's).
HELD_LEVEL = 0.90
REALISED_WINDOW = 20
TREND_WINDOW = 200
#: The rows a result keeps: every survivor, then the best by `p_menu` up to this many.
KEEP_ROWS = 40
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
DAY_NAMES = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays")

#: name -> (what it is, in words; bin edges; bin names; unit). Ordered bands, fixed before any read.
DIMS: dict[str, tuple[str, tuple[float, ...], tuple[str, ...], str]] = {
    "vix": (
        "VIX at the prior close",
        (15, 20, 25, 30),
        ("under 15", "15-20", "20-25", "25-30", "30 and over"),
        "",
    ),
    "vix_vs_realised": (
        "VIX over the symbol's 20-day realised volatility",
        (0.8, 1.2, 1.6),
        ("under 0.8", "0.8-1.2", "1.2-1.6", "1.6 and over"),
        "",
    ),
    "gap": (
        "the opening gap",
        (-1, -0.25, 0.25, 1),
        ("down 1% or more", "down 0.25-1%", "within 0.25%", "up 0.25-1%", "up 1% or more"),
        "%",
    ),
    "prior_move": (
        "the prior session's move",
        (-1, -0.25, 0.25, 1),
        ("down 1% or more", "down 0.25-1%", "within 0.25%", "up 0.25-1%", "up 1% or more"),
        "%",
    ),
    "rsi": ("RSI(14) at the prior close", (30, 50, 70), ("under 30", "30-50", "50-70", "70 and over"), ""),
    "trend": ("the prior close", (0,), ("below its 200-day average", "above its 200-day average"), ""),
}
#: The event days a filter may skip (`automations.events` labels), each a day dimension of one bin.
EVENT_LABELS = (
    "FOMC", "CPI", "NFP", "PPI", "PCE", "OPEX", "TripleWitching", "VIXExpiry", "FuturesRoll",
    "MonthEnd", "QuarterEnd", "FirstWeekly", "PMI-Mfg", "Earnings",
)  # fmt: skip


# --------------------------------------------------------------------------- trades and the split
def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f else 0.0


def _net(t: dict) -> float:
    net = t.get("net")
    return _num(net.get(KIND) if isinstance(net, dict) else net)


def _cost(t: dict) -> float:
    s = t.get("slippage")
    return _num(t.get("fees")) + _num(s.get(KIND) if isinstance(s, dict) else s)


def split_date(first: str, last: str, share: float = SPLIT_SHARE) -> str:
    """The day `share` of the way from `first` to `last`: the first held-out session is on or after it."""
    a, b = date.fromisoformat(first), date.fromisoformat(last)
    return (a + timedelta(days=round((b - a).days * share))).isoformat()


def session_nets(trades: Iterable[dict]) -> dict[str, float]:
    """Net by entry session: a filter decides at entry, so a trade belongs to the day it opened."""
    out: dict[str, float] = {}
    for t in trades:
        s = str(t.get("session") or "")[:10]
        if s:
            out[s] = out.get(s, 0.0) + _net(t)
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------- entry-time features
def _band(value: float | None, edges: Sequence[float]) -> int | None:
    if value is None:
        return None
    return sum(1 for e in edges if value >= e)


def features(
    sessions: Sequence[str],
    bars: Sequence[Mapping],
    vix: Mapping[str, float],
    events: Mapping[str, Mapping[str, bool | None]] | None = None,
) -> dict[str, dict[str, int | None]]:
    """Each session's bin in every dimension, from what was known before its entry: the bars up to the
    prior close (the session's own open for the gap), the VIX close before the session, the event
    calendar. `bars` are the symbol's daily `{session, open, close}`, oldest first; `events` maps a
    session to `{label: on}` (None where the calendar does not know). A value that cannot be read is
    None, and a filter never fires on it."""
    days = [str(b["session"])[:10] for b in bars]
    closes = [_num(b.get("close")) for b in bars]
    sma = indicators.sma(closes, TREND_WINDOW)
    rsi = indicators.rsi(closes, 14)
    rets = [None] + [
        math.log(closes[i] / closes[i - 1]) if closes[i] > 0 and closes[i - 1] > 0 else None
        for i in range(1, len(closes))
    ]
    vix_days = sorted(vix)
    out: dict[str, dict[str, int | None]] = {}
    for s in sessions:
        i = bisect_left(days, s)
        own = i < len(days) and days[i] == s
        p = i - 1  # the prior session's bar
        row: dict[str, Any] = dict.fromkeys(DIMS)
        j = bisect_left(vix_days, s) - 1
        v = vix[vix_days[j]] if j >= 0 else None
        row["vix"] = _band(v, DIMS["vix"][1])
        if p >= 0:
            c = closes[p]
            if own and c > 0:
                row["gap"] = _band((_num(bars[i].get("open")) / c - 1) * 100, DIMS["gap"][1])
            if p >= 1 and closes[p - 1] > 0:
                row["prior_move"] = _band((c / closes[p - 1] - 1) * 100, DIMS["prior_move"][1])
            if sma[p] is not None:
                row["trend"] = _band(c - sma[p], DIMS["trend"][1])
            if rsi[p] is not None:
                row["rsi"] = _band(rsi[p], DIMS["rsi"][1])
            window = rets[max(1, p - REALISED_WINDOW + 1) : p + 1]
            if v is not None and len(window) == REALISED_WINDOW and all(r is not None for r in window):
                rv = statistics.stdev(window) * math.sqrt(252) * 100
                if rv > 0:
                    row["vix_vs_realised"] = _band(v / rv, DIMS["vix_vs_realised"][1])
        wd = date.fromisoformat(s).weekday()
        row["weekday"] = wd if wd < 5 else None
        flags = (events or {}).get(s) or {}
        for label in EVENT_LABELS:
            on = flags.get(label)
            row[f"event:{label}"] = None if on is None else int(bool(on))
        out[s] = row
    return out


# --------------------------------------------------------------------------- the menu
def _bins(dim: str) -> int:
    if dim in DIMS:
        return len(DIMS[dim][2])
    return len(WEEKDAYS) if dim == "weekday" else 2


def _ordered(dim: str) -> bool:
    return dim in DIMS


def conditions(dim: str) -> list[tuple[str, str, int]]:
    """A dimension's conditions: each bin (an event's "on" only), and for an ordered band of three or
    more bins each tail of two or more bins from either end, short of all of them."""
    if dim.startswith("event:"):
        return [(dim, "bin", 1)]
    m = _bins(dim)
    out = [(dim, "bin", i) for i in range(m)]
    if _ordered(dim) and m >= 3:
        out += [(dim, "below", i) for i in range(2, m)]
        out += [(dim, "above", i) for i in range(1, m - 1)]
    return out


def holds(cond: tuple[str, str, int], row: Mapping[str, int | None]) -> bool:
    dim, kind, i = cond
    v = row.get(dim)
    if v is None:
        return False
    return v == i if kind == "bin" else v < i if kind == "below" else v >= i


def _state(cond: tuple[str, str, int]) -> tuple[bool, str]:
    """(whether it is a kind of day, the condition as a state): "FOMC days", "VIX at the prior close
    is 25 and over"."""
    dim, kind, i = cond
    if dim.startswith("event:"):
        return True, f"{dim.split(':', 1)[1]} days"
    if dim == "weekday":
        return True, DAY_NAMES[i]
    label, edges, names, unit = DIMS[dim]
    if kind == "bin":
        return False, f"{label} is {names[i]}"
    edge = f"{edges[i - 1]:g}{unit}"
    return False, f"{label} is under {edge}" if kind == "below" else f"{label} is {edge} and over"


def words(cond: tuple[str, str, int]) -> str:
    """One condition in words: "on FOMC days", "when VIX at the prior close is 25 and over"."""
    day, text = _state(cond)
    return f"on {text}" if day else f"when {text}"


def idea_words(conds: Sequence[tuple[str, str, int]]) -> str:
    """A filter in words. Two conditions hold at once: "skip on Mondays when VIX at the prior close is
    25 and over", "skip on Mondays that are also OPEX days"."""
    parts = sorted((_state(c) for c in conds), key=lambda s: not s[0])
    if len(parts) == 1:
        return "skip " + words(conds[0])
    (d1, a), (d2, b) = parts
    if d1 and d2:
        return f"skip on {a} that are also {b}"
    if d1:
        return f"skip on {a} when {b}"
    return f"skip when {a} and {b}"


def cid(conds: Sequence[tuple[str, str, int]]) -> str:
    return "&".join(f"{d}:{k}:{i}" for d, k, i in conds)


def parse_cid(text: str) -> list[tuple[str, str, int]]:
    out = []
    for part in text.split("&"):
        dim, kind, i = part.rsplit(":", 2)
        out.append((dim, kind, int(i)))
    return out


def dimensions() -> list[str]:
    return [*DIMS, "weekday", *(f"event:{label}" for label in EVENT_LABELS)]


def skips(
    conds: Sequence[tuple[str, str, int]], feats: Mapping[str, Mapping], sessions: Iterable[str]
) -> list[str]:
    return [s for s in sessions if all(holds(c, feats.get(s) or {}) for c in conds)]


def menu(feats: Mapping[str, Mapping], design: Sequence[str]) -> dict[str, list[tuple[str, str, int]]]:
    """Every candidate the search tries, from features alone (no P&L is read to build it): each
    condition, and each pair of single-bin conditions from two dimensions, that skips at least
    `MIN_SKIPPED` design sessions and at most `MAX_SKIP_SHARE` of them. A pair whose second condition
    skips nothing the first does not already is left out: it is the first condition."""
    n = len(design)
    hi = MAX_SKIP_SHARE * n
    sets: dict[tuple, frozenset] = {}
    for dim in dimensions():
        for c in conditions(dim):
            sets[c] = frozenset(skips([c], feats, design))
    out: dict[str, list[tuple[str, str, int]]] = {}
    for c, s in sets.items():
        if MIN_SKIPPED <= len(s) <= hi:
            out[cid([c])] = [c]
    bins = [c for c in sets if c[1] == "bin"]
    for a in range(len(bins)):
        for b in range(a + 1, len(bins)):
            ca, cb = bins[a], bins[b]
            if ca[0] == cb[0]:
                continue
            both = sets[ca] & sets[cb]
            if both in (sets[ca], sets[cb]) or not MIN_SKIPPED <= len(both) <= hi:
                continue
            out[cid([ca, cb])] = [ca, cb]
    return out


# --------------------------------------------------------------------------- the reads of one candidate
def _mean(xs: Sequence[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def _neighbour(cond: tuple[str, str, int], step: int) -> tuple[str, str, int] | None:
    """The condition one bin (or, for a tail, one edge) along. A tail's neighbour may be a single end
    bin, never every bin or none."""
    dim, kind, i = cond
    j, m = i + step, _bins(dim)
    lo, hi = (0, m - 1) if kind == "bin" else (1, m - 1)
    return (dim, kind, j) if lo <= j <= hi else None


def plateau(conds, feats, design, nets) -> dict:
    """Each ordered condition moved one bin (or one edge) either way, the others held: its neighbours'
    skipped means against its own (`robustness.neighbourhood`). A lone peak on any axis fails."""
    axes = []
    for k, c in enumerate(conds):
        if not _ordered(c[0]):
            continue
        means: dict[float, float | None] = {}
        for step in (-1, 0, 1):
            nb = c if step == 0 else _neighbour(c, step)
            if nb is None:
                continue
            variant = [*conds[:k], nb, *conds[k + 1 :]]
            sk = skips(variant, feats, design)
            means[float(nb[2])] = (
                round(_mean([nets[s] for s in sk]), 4) if len(sk) >= MIN_SKIPPED or step == 0 else None
            )
        read = robustness.neighbourhood(means, float(c[2]))
        axes.append({"condition": words(c), "verdict": read["verdict"]})
    verdicts = {a["verdict"] for a in axes}
    verdict = (
        "lone_peak" if "lone_peak" in verdicts else "plateau" if "plateau" in verdicts else "no_neighbours"
    )
    return {"verdict": verdict, "axes": axes}


def stability(skipped: set[str], design: Sequence[str], nets: Mapping[str, float]) -> dict:
    """The skipped mean's sign in each half of the design part and in each walk-forward fold (equal
    runs of design sessions, in order). A fold with no skipped session counts against it."""

    def part(chunk):
        xs = [nets[s] for s in chunk if s in skipped]
        m = _mean(xs)
        return {
            "skipped": len(xs),
            "mean": None if m is None else round(m, 4),
            "negative": bool(xs) and m < 0,
        }

    n = len(design)
    halves = [part(design[: n // 2]), part(design[n // 2 :])]
    folds = [part(design[n * f // FOLDS : n * (f + 1) // FOLDS]) for f in range(FOLDS)]
    held = sum(1 for f in folds if f["negative"])
    return {
        "halves": halves,
        "folds": folds,
        "folds_negative": held,
        "stable": all(h["negative"] for h in halves) and held >= FOLDS_NEEDED,
    }


def adds(conds, feats, design, nets) -> dict | None:
    """Each part of a filter must earn its place. A pair beats each of its conditions alone only if what
    that condition alone would also skip made money: the second condition must add to the first on its
    own. A tail of bins beats the next narrower tail only if its widest bin, the one it adds, lost. None
    for a single bin."""
    if len(conds) < 2:
        dim, kind, i = conds[0]
        if kind == "bin":
            return None
        edge = (dim, "bin", i - 1 if kind == "below" else i)
        extra = round(sum(nets[s] for s in skips([edge], feats, design)), 2)
        return {"widest_bin": words(edge), "widest_bin_net": extra, "adds": extra < 0}
    a, b = (set(skips([c], feats, design)) for c in conds)
    only_a = round(sum(nets[s] for s in a - b), 2)
    only_b = round(sum(nets[s] for s in b - a), 2)
    return {"first_alone_extra": only_a, "second_alone_extra": only_b, "adds": only_a > 0 and only_b > 0}


# --------------------------------------------------------------------------- the search
def search(
    trades: Sequence[dict],
    feats: Mapping[str, Mapping],
    split: str,
    *,
    draws: int = DRAWS,
    seed: int = SEED,
    tick=None,
) -> dict:
    """The idea search over one run's trades, reading the P&L of design trades only (entered before
    `split`). Held-out sessions are counted by name, for each idea's planned test size. `tick` is
    the placebo's progress callback (`core.stats.menu_placebo`)."""
    design_trades = [t for t in trades if str(t.get("session") or "")[:10] < split]
    held = sorted(
        {str(t.get("session") or "")[:10] for t in trades if str(t.get("session") or "")[:10] >= split}
    )
    nets = session_nets(design_trades)
    design = list(nets)
    base = {
        "sessions": len(design),
        "trades": len(design_trades),
        "net": round(sum(nets.values()), 2),
        "mean_session": round(_mean(list(nets.values())) or 0.0, 4),
        "held_out_sessions": len(held),
    }
    cands = menu(feats, design)
    sets = {k: skips(c, feats, design) for k, c in cands.items()}
    placebo = stats.menu_placebo(nets, sets, draws=draws, seed=seed, tick=tick)
    ranked = placebo["candidates"]
    pvals = {k: v["p"] for k, v in ranked.items() if v.get("p") is not None}
    holm = stats.holm(pvals, ALPHA) if pvals else {}
    bh = stats.benjamini_hochberg(pvals, BH_Q) if pvals else {}
    null_low = placebo["null_min_median"]
    mu = placebo["mean"]
    rows = []
    for k, conds in cands.items():
        read = ranked[k]
        if read.get("z") is None:
            continue
        sk = set(sets[k])
        vals = [nets[s] for s in design if s in sk]
        ci = stats.bootstrap_ci(vals, seed=seed)
        kept = [t for t in design_trades if str(t.get("session") or "")[:10] not in sk]
        cost = robustness.cost_stress([_net(t) for t in kept], [_cost(t) for t in kept]) if kept else None
        z, scale, n_sk = read["z"], read["scale"], len(vals)
        z_left = min(0.0, z - null_low) if null_low is not None else 0.0
        shrunk_mean = mu + z_left * scale / n_sk
        held_n = len(skips(conds, feats, held))
        mde = robustness.planned_mde(vals, held_n) if held_n >= 2 else {"planned_n": held_n, "mde": None}
        effect = -shrunk_mean
        testable = mde.get("mde") is not None and held_n >= HELD_MIN and effect > 0 and mde["mde"] <= effect
        plat = plateau(conds, feats, design, nets)
        stab = stability(sk, design, nets)
        add = adds(conds, feats, design, nets)
        gates = {
            "loses": ci is not None and ci[1] < 0,
            "beats_menu": read["p_menu"] <= ALPHA,
            "plateau": plat["verdict"] != "lone_peak",
            "stable": stab["stable"],
            "adds": add is None or add["adds"],
            "pays_at_1_5x": bool(cost and cost["survives"]),
        }
        rows.append(
            {
                "id": k,
                "idea": idea_words(conds),
                "conditions": [words(c) for c in conds],
                "skipped": n_sk,
                "skipped_share": round(n_sk / len(design), 4),
                "skipped_mean": read["mean"],
                "skipped_ci": None if ci is None else [round(ci[0], 2), round(ci[1], 2)],
                "uplift": round(-sum(vals), 2),
                "uplift_shrunk": round(-(n_sk * mu + z_left * scale), 2),
                "z": z,
                "p": read["p"],
                "p_menu": read["p_menu"],
                "holm": bool(holm.get(k)),
                "bh": bool(bh.get(k)),
                "plateau": plat,
                "stability": stab,
                "adds": add,
                "cost": None
                if cost is None
                else {
                    "mean_by_multiplier": {str(m): v for m, v in cost["mean_by_multiplier"].items()},
                    "survives": cost["survives"],
                },  # fmt: skip
                "held_out": {"planned_sessions": held_n, "mde": mde.get("mde"), "testable": testable},
                "gates": gates,
                "survives": all(gates.values()),
            }
        )
    # Survivors by the uplift left after the search's luck; the rest by how near chance they sit.
    rows.sort(key=lambda r: (0, -r["uplift_shrunk"], r["id"]) if r["survives"] else (1, r["p_menu"], r["z"]))
    keep = [r for r in rows if r["survives"]]
    keep += [r for r in rows if not r["survives"]][: max(0, KEEP_ROWS - len(keep))]
    return {
        "split": split,
        "base": base,
        "tried": placebo["tried"],
        "draws": placebo["draws"],
        "null_min_median": null_low,
        "survivors": sum(1 for r in rows if r["survives"]),
        "holm_survivors": sum(1 for r in rows if r["holm"]),
        "bh_survivors": sum(1 for r in rows if r["bh"]),
        "rules": rules(),
        "rows": keep,
    }


def rules() -> dict:
    """The fixed rules a result was read under, stored with it."""
    return {
        "split_share": round(SPLIT_SHARE, 4),
        "min_skipped": MIN_SKIPPED,
        "max_skip_share": MAX_SKIP_SHARE,
        "folds": FOLDS,
        "folds_needed": FOLDS_NEEDED,
        "alpha": ALPHA,
        "bh_q": BH_Q,
        "seed": SEED,
        "held_min": HELD_MIN,
        "held_level": HELD_LEVEL,
    }


# --------------------------------------------------------------------------- the one held-out test
def holdout(
    trades: Sequence[dict], feats: Mapping[str, Mapping], split: str, idea: str, *, seed: int = SEED
) -> dict:
    """A declared idea's one test, on the sessions entered on or after `split`: the sessions it would
    have skipped must have lost, the interval's upper bound (one-sided 95%) below zero. Fewer than
    `HELD_MIN` skipped sessions gives no verdict."""
    nets = session_nets(t for t in trades if str(t.get("session") or "")[:10] >= split)
    held = list(nets)
    sk = skips(parse_cid(idea), feats, held)
    vals = [nets[s] for s in sk]
    out: dict[str, Any] = {
        "sessions": len(held),
        "skipped": len(vals),
        "skipped_mean": None if not vals else round(statistics.fmean(vals), 4),
        "uplift": round(-sum(vals), 2),
    }
    if len(vals) < HELD_MIN:
        return {**out, "ci": None, "verdict": "no_verdict"}
    ci = stats.bootstrap_ci(vals, level=HELD_LEVEL, seed=seed)
    return {**out, "ci": [round(ci[0], 2), round(ci[1], 2)], "verdict": "passed" if ci[1] < 0 else "failed"}


def declaration(result: Mapping, idea: str, *, result_path: str) -> dict:
    """A register row drafted from one surviving idea, its split already fixed. The id, the study slug,
    the mechanism and the rivals' checks beyond the search's own are the declarer's to write; the row
    carries no figures, only the idea, the split and the rule."""
    row = next((r for r in result.get("rows") or [] if r.get("id") == idea), None)
    if row is None:
        raise ValueError(f"no idea {idea!r} in this result")
    span = result.get("sessions") or [None, None]
    return {
        "id": None,
        "study": None,
        "title": f"{result.get('automation')}: {row['idea']}",
        "status": "declared",
        "legacy": False,
        "setup": result.get("automation"),
        "side": "filter",
        "structure": "entry filter",
        "window": "held-out sessions of one Builder run",
        "measure": f"the net of the sessions the filter would skip ({row['idea']}), per entry session",
        "expected": "negative: the skipped sessions lost money",
        "sample": (
            f"one run of {result.get('automation')} over {span[0]}..{span[-1]}, split fixed at "
            f"{result.get('split')} when the search was queued; the search read only sessions before it"
        ),
        "rule": (
            f"on the sessions from {result.get('split')}: the skipped sessions' session-bootstrap interval "
            f"({int(HELD_LEVEL * 100)}% two-sided, seed {SEED}) has its upper bound below zero; fewer than "
            f"{HELD_MIN} skipped sessions = no verdict; tested once"
        ),
        "mechanism": None,
        "rivals": [
            {
                "explanation": "chance: the best of the menu the search tried",
                "check": f"beat the best random filter of {result.get('tried')} tries in the design part, "
                "and the held-out sessions were never read by the search",
            },
            {
                "explanation": "a lone peak in a band",
                "check": "the neighbouring bins agreed (plateau) in the design part",
            },
        ],
        "idea_search": {"result": result_path, "idea": idea, "split": result.get("split")},
    }
