"""Item 32's declared test of large options orders as a signal: the rules, pure.

`docs/flow-signal-plan.md` declares the method and `docs/edge-hypotheses-plan.md` item 32 records
what was settled before the first read. This module is the arithmetic only: plain rows in, plain
numbers out, no store and no network, so every rule can be shown firing and failing on invented
data. `scripts/flow_signal_item32.py` reads the stores, builds a `Scan` and calls `evaluate`.

**What a tag is.** A name has a tag on a session when at least one of its orders that session is
large and opened (`flow.opened`: the next session's open interest rose by half the order), read
from the fill as bought or sold, and one of the three the plan names: a call bought (bullish), a put
sold (bullish), a put bought (bearish). Large means a premium at or above the cut AND in the top 1%
of the name's orders over its last `flow.LOOKBACK_SESSIONS` *fetched* sessions, at least
`MIN_HISTORY` of them (`lookback_cuts`); a name with fewer carries no tag. Multi-leg prints never
reach an order (`flow.orders`).

**The outcome** is the stock's `HORIZON`-session return minus SPY's, close to close, from the close
`ENTRY_LAG` session after the tag session (the session the tag becomes known on) to the close
`HORIZON` sessions after that, signed by the tag's side. **The comparison** is, on each grid
session, the tagged stocks' mean signed excess minus the mean signed excess of the stocks in the
same relative-strength stage without the tag (`matched_diff`); a tagged stock in a stage no untagged
stock shares is left out. The cell's series is one such difference a session; its interval is a
stationary bootstrap over sessions, widened to the level `1 - ALPHA / trials` (deflation by the
trials the family counts), and a cell passes when that interval lies above zero and the larger cuts
do at least as well.

Every constant the plan left open is named here with the reading chosen, so the study states them.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import flow, stats

#: The three tags: the order's right and side, and the direction it points.
TAGS: dict[str, dict] = {
    "call_buy": {"right": "CALL", "side": "bought", "sign": 1, "label": "Large call buying, opened"},
    "put_sell": {"right": "PUT", "side": "sold", "sign": 1, "label": "Large put selling, opened"},
    "put_buy": {"right": "PUT", "side": "bought", "sign": -1, "label": "Large put buying, opened"},
}
CUTS = flow.CUTS

MIN_HISTORY = 4  # fetched sessions behind a session for the top-1% rule to read
HORIZON = 5  # sessions of outcome
ENTRY_LAG = 1  # the outcome starts at the close this many sessions after the tag session
ALPHA = 0.05
FIRST_TRIALS = len(TAGS) * len(CUTS)  # 9
MIN_SESSIONS = 30  # sessions holding a tag, for a cell to carry a verdict
MIN_EVENTS = 30  # reports, for an earnings trial to carry a verdict
THIN_SHARE = 0.8  # a grid session holding under this share of the median session's names is thin
RESAMPLES = 20_000  # the deflated levels sit far in the tails
EXTRA_RESAMPLES = 4_000
SEED = 32

# The second family's filters (flow-signal-plan.md, "A second family").
F1_MIN_DTE = 7
F2_MAX_ITM = 0.10
F3_CAPITAL = 10_000_000.0
F4_SIZE_SHARE = 0.10
F4_SECONDS = 60.0
F6_SESSIONS = 10
FILTERS = ("F1", "F2", "F3", "F4", "F5", "F6", "F7")
#: F3 replaces the cut, so the cut it is compared with ("unfiltered") is this one.
F3_COMPARE_CUT = CUTS[1]
#: F3's baseline is the loosest put-sale tag: every F3 order is also one of those.
F3_BASELINE_CUT = CUTS[0]
#: F7 (no report ahead): not counted when the name's reaction day falls in the outcome window.
#: The trials of the second family: F1, F2, F4, F6, F7 on three tags at three cuts, F5 on one tag at
#: three cuts, F3 once.
SECOND_TRIALS = 5 * len(TAGS) * len(CUTS) + len(CUTS) + 1  # 49 (F7 added 2026-10-30, before any read)

# The earnings test.
EARN_CUT = CUTS[1]
EARN_SESSIONS = 10
EARN_COST = 0.01  # an assumed round trip on the move
EARN_TRIALS = 2


# ------------------------------------------------------------------------------------------------
# Tags from orders.


def tag_of(right: str, side: str) -> str | None:
    """The tag an order's right and side make, or None (a call sold is recorded and never tagged)."""
    for name, t in TAGS.items():
        if t["right"] == right and t["side"] == side:
            return name
    return None


def lookback_cuts(days: Sequence[tuple[str, list[int]]]) -> dict[str, float | None]:
    """For each of a name's fetched sessions, the premium from which the top `flow.TOP_SHARE` of its
    orders over the previous `flow.LOOKBACK_SESSIONS` fetched sessions begins, or None where fewer
    than `MIN_HISTORY` sessions stand behind it (or those held no orders). `days` is
    (session, premium histogram) pairs; the order of the sessions decides, not their spacing, because
    the fetch holds a grid and the sessions before reports, not every session."""
    ordered = sorted(days, key=lambda d: d[0])
    pooled = [0] * flow.N_BUCKETS
    out: dict[str, float | None] = {}
    for k, (session, _) in enumerate(ordered):
        if k >= 1:
            for i, n in enumerate(ordered[k - 1][1]):
                pooled[i] += n
        gone = k - flow.LOOKBACK_SESSIONS - 1
        if gone >= 0:
            for i, n in enumerate(ordered[gone][1]):
                pooled[i] -= n
        behind = min(k, flow.LOOKBACK_SESSIONS)
        out[session] = flow.top_share_cut([pooled]) if behind >= MIN_HISTORY else None
    return out


def itm_share(right: str, strike: float, close: float | None) -> float | None:
    """How far in the money a contract is, as a share of the close: positive in, negative out."""
    if not close or close <= 0:
        return None
    return (close - strike) / close if right == "CALL" else (strike - close) / close


def days_to_expiry(session: str, expiration: str) -> int:
    return (date.fromisoformat(expiration) - date.fromisoformat(session)).days


def straddle_partner(order: dict, day_orders: Iterable[dict]) -> bool:
    """F4: an order of the other right at the same strike and expiry, on the same side, within
    `F4_SIZE_SHARE` of this one's size and starting within `F4_SECONDS` of it. Searched among the
    day's kept orders only (those at or above `flow.KEEP_PREMIUM`), which is all the store holds."""
    for o in day_orders:
        if (
            o is not order
            and o["right"] != order["right"]
            and o["strike"] == order["strike"]
            and o["expiration"] == order["expiration"]
            and o["side"] == order["side"]
            and abs(o["size"] - order["size"]) <= F4_SIZE_SHARE * order["size"]
            and abs(o["first_ms"] - order["first_ms"]) <= F4_SECONDS * 1000.0
        ):
            return True
    return False


def candidates(
    session: str, close: float | None, day_orders: Sequence[dict], top_cut: float | None
) -> list[dict]:
    """A name-day's orders that are large (at the smallest cut and in the name's top 1%), opened and one
    of the three tags, each with what the second family's filters read. The cut is applied later
    (`passes`), so one pass over the store serves every cut. `top_cut` None (no look-back) tags nothing."""
    if top_cut is None:
        return []
    out = []
    for o in day_orders:
        tag = tag_of(o["right"], o["side"])
        if tag is None or o["premium"] < max(flow.KEEP_PREMIUM, top_cut):
            continue
        if not flow.opened(o["size"], o.get("oi_before"), o.get("oi_after")):
            continue
        out.append(
            {
                "tag": tag,
                "premium": o["premium"],
                "committed": o["strike"] * o["size"] * 100.0,
                "dte": days_to_expiry(session, o["expiration"]),
                "itm": itm_share(o["right"], o["strike"], close),
                "partner": straddle_partner(o, day_orders),
            }
        )
    return out


def passes(c: dict, tag: str, cut: float, flt: str | None) -> bool:
    """Whether a candidate order makes `tag` at `cut` under an order-level filter (F1-F4); F5 and F6
    act on the name-session, in `tagged_syms`. F3 replaces the premium cut with committed capital."""
    if c["tag"] != tag:
        return False
    if flt == "F3":
        if c["committed"] < F3_CAPITAL:
            return False
    elif c["premium"] < cut:
        return False
    if flt == "F1":
        return c["dte"] >= F1_MIN_DTE
    if flt == "F2":
        return c["itm"] is not None and c["itm"] <= F2_MAX_ITM
    if flt == "F4":
        return not c["partner"]
    return True


# ------------------------------------------------------------------------------------------------
# The scan: what the study reads.


@dataclass
class Scan:
    """One window's readings, built from the stores by the script and consumed here.

    ``grid``: the grid sessions, in order. ``rows``: session -> symbol -> (stage, unsigned 5-session
    excess), for the names the fetch holds that session with a look-back and an outcome. ``cands``:
    symbol -> session -> `candidates`, every fetched session of the name. ``complete``: session ->
    names fetched whole (the thin-session test). ``reported``: session -> names whose outcome window
    holds a report. ``index``: session -> position on the market calendar (F6's ten sessions).
    ``events``: the earnings test's reports, {sym, react, pre: candidates, excess}."""

    grid: list[str]
    rows: dict[str, dict[str, tuple[str, float]]]
    cands: dict[str, dict[str, list[dict]]]
    complete: dict[str, int]
    reported: dict[str, set[str]]
    index: dict[str, int]
    events: list[dict] = field(default_factory=list)
    flow_syms: dict[str, list[str]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        by: dict[str, list[str]] = {}
        for sym, days in self.cands.items():
            for session, cs in days.items():
                if cs:
                    by.setdefault(session, []).append(sym)
        self.flow_syms = {s: sorted(v) for s, v in by.items()}


def repeats(scan: Scan, sym: str, session: str, tag: str, cut: float) -> bool:
    """F6: a second large opened order of the same tag in the name within the previous
    `F6_SESSIONS` market sessions, among those the fetch holds."""
    here = scan.index.get(session)
    if here is None:
        return False
    for other, cs in scan.cands.get(sym, {}).items():
        j = scan.index.get(other)
        if j is not None and here - F6_SESSIONS <= j < here and any(passes(c, tag, cut, None) for c in cs):
            return True
    return False


def tagged_syms(scan: Scan, session: str, tag: str, cut: float, flt: str | None = None) -> set[str]:
    """The names holding `tag` at `cut` on `session` (under filter `flt`)."""
    out = set()
    for sym in scan.flow_syms.get(session, ()):
        cs = scan.cands[sym][session]
        if not any(passes(c, tag, cut, flt) for c in cs):
            continue
        if flt == "F5" and any(passes(c, "put_sell", cut, None) for c in cs):
            continue
        if flt == "F6" and not repeats(scan, sym, session, tag, cut):
            continue
        if flt == "F7" and sym in scan.reported.get(session, ()):
            continue
        out.add(sym)
    return out


# ------------------------------------------------------------------------------------------------
# The comparison.


def matched_diff(
    rows: dict[str, tuple[str, float]], tagged: set[str], excluded: set[str], sign: int
) -> tuple[float, int] | None:
    """One session's tagged mean signed excess minus the mean signed excess of the untagged names in
    the same stage. ``rows``: symbol -> (stage, excess). ``tagged`` are measured; the baseline is
    every row not in ``excluded`` (which holds the tagged and, for a filtered tag, the names the
    unfiltered tag also marks). A tagged name in a stage with no baseline name is left out. Returns
    (the difference, the tagged names used), or None when none was usable."""
    sums: dict[str, list[float]] = {}
    for sym, (stage, ex) in rows.items():
        if sym not in excluded:
            acc = sums.setdefault(stage, [0.0, 0])
            acc[0] += sign * ex
            acc[1] += 1
    diffs = []
    for sym in tagged:
        row = rows.get(sym)
        if row is None or row[0] not in sums:
            continue
        total, n = sums[row[0]]
        diffs.append(sign * row[1] - total / n)
    if not diffs:
        return None
    return statistics.fmean(diffs), len(diffs)


def baseline_of(tag: str, cut: float, flt: str | None) -> tuple[str, float, str | None]:
    """The tag whose holders are kept out of the baseline: the cell's own unfiltered tag (F3: the
    smallest cut's put sale, since every F3 order is one)."""
    if flt == "F3":
        return "put_sell", F3_BASELINE_CUT, None
    return tag, cut, None


def cell_series(
    scan: Scan,
    sessions: Sequence[str],
    tag: str,
    cut: float,
    flt: str | None = None,
    *,
    lag: int = 0,
    drop: frozenset[str] = frozenset(),
) -> dict:
    """The cell's series over `sessions`: per session the matched difference, and the tagged names it
    used. ``lag`` reads the tags of the grid session ``lag`` earlier against this session's outcomes
    (the stale-tag placebo); ``drop`` leaves names out altogether."""
    sign = TAGS[tag]["sign"]
    b_tag, b_cut, b_flt = baseline_of(tag, cut, flt)
    out_s, out_d, out_n = [], [], []
    for session in sessions:
        k = scan.grid.index(session)
        if k - lag < 0:
            continue
        src = scan.grid[k - lag]
        rows = scan.rows.get(session, {})
        if drop:
            rows = {s: r for s, r in rows.items() if s not in drop}
        tagged = tagged_syms(scan, src, tag, cut, flt) & rows.keys()
        if not tagged:
            continue
        excluded = tagged_syms(scan, src, b_tag, b_cut, b_flt) | tagged
        got = matched_diff(rows, tagged, excluded, sign)
        if got is not None:
            out_s.append(session)
            out_d.append(got[0])
            out_n.append(got[1])
    return {"sessions": out_s, "diffs": out_d, "n": out_n}


def most_frequent(
    scan: Scan, sessions: Sequence[str], tag: str, cut: float, flt: str | None, k: int = 5
) -> list[str]:
    """The `k` names tagged on the most of `sessions` (ties by symbol), for the 'remove them' reading."""
    count: dict[str, int] = {}
    for s in sessions:
        for sym in tagged_syms(scan, s, tag, cut, flt) & scan.rows.get(s, {}).keys():
            count[sym] = count.get(sym, 0) + 1
    return sorted(count, key=lambda x: (-count[x], x))[:k]


def deflated_interval(
    values: Sequence[float], trials: int, *, resamples: int = RESAMPLES
) -> tuple[float, float] | None:
    """The stationary-bootstrap interval at the level `1 - ALPHA / trials`: deflation by the
    trials the family counts, however many were looked at first."""
    return stats.block_bootstrap_ci(
        values, level=1.0 - ALPHA / max(int(trials), 1), resamples=resamples, seed=SEED
    )


def larger_cuts_ok(means: dict[float, float | None], cut: float) -> bool:
    """Bigger orders must not predict worse: every larger cut's mean at least this cut's. A larger
    cut with no reading cannot show it, so it fails the check; the largest cut has none to beat."""
    here = means.get(cut)
    if here is None:
        return False
    return all(means.get(c) is not None and means[c] >= here for c in sorted(means) if c > cut)


def verdict_word(sessions: int, lo: float | None, *, ok: bool = True, minimum: int = MIN_SESSIONS) -> str:
    """'no verdict' under `minimum` sessions or without an interval; else 'pass' when the interval lies
    above zero and every further check (`ok`) holds, 'fail' otherwise."""
    if sessions < minimum or lo is None:
        return "no verdict"
    return "pass" if lo > 0 and ok else "fail"


def thin_sessions(complete: dict[str, int], grid: Sequence[str]) -> list[str]:
    """Grid sessions holding fewer than `THIN_SHARE` of the median grid session's fetched names."""
    counts = [complete.get(s, 0) for s in grid]
    if not counts:
        return []
    floor = THIN_SHARE * statistics.median(counts)
    return [s for s in grid if complete.get(s, 0) < floor]


# ------------------------------------------------------------------------------------------------
# The families.


def cell_keys() -> list[tuple[str, str | None, str, float]]:
    """Every declared cell as (family, filter, tag, cut): the nine of the first family, then the forty of
    the second. F3 has one (put selling, its cut the comparison's)."""
    keys: list[tuple[str, str | None, str, float]] = [("first", None, t, c) for t in TAGS for c in CUTS]
    for flt in ("F1", "F2", "F4", "F6", "F7"):
        keys += [("second", flt, t, c) for t in TAGS for c in CUTS]
    keys += [("second", "F5", "put_buy", c) for c in CUTS]
    keys.append(("second", "F3", "put_sell", F3_COMPARE_CUT))
    return keys


def key_name(flt: str | None, tag: str, cut: float) -> str:
    if flt == "F3":
        return "F3/put_sell"
    return f"{tag}/{int(cut)}" if flt is None else f"{flt}/{tag}/{int(cut)}"


def _mean_ci(series: dict, trials: int, resamples: int) -> dict:
    d = series["diffs"]
    ci = deflated_interval(d, trials, resamples=resamples) if len(d) >= 2 else None
    return {
        "sessions": len(d),
        "tagged": sum(series["n"]),
        "mean": statistics.fmean(d) if d else None,
        "interval": None if ci is None else list(ci),
    }


def _reading(series: dict) -> dict:
    """A reported (not judged) reading: the mean and the plain 95% interval."""
    d = series["diffs"]
    ci = stats.block_bootstrap_ci(d, resamples=EXTRA_RESAMPLES, seed=SEED) if len(d) >= 2 else None
    return {
        "sessions": len(d),
        "tagged": sum(series["n"]),
        "mean": statistics.fmean(d) if d else None,
        "ci95": None if ci is None else list(ci),
    }


def first_family(
    scan: Scan,
    sessions: Sequence[str],
    *,
    extras: bool = True,
    only: set[str] | None = None,
    trials: int = FIRST_TRIALS,
    require_larger: bool = True,
) -> dict:
    """The nine cells of the first family over `sessions`: mean, interval deflated by `trials`, the
    larger-cut check (`require_larger`; the holdout does not ask it again) and a verdict word, with
    the reported readings beside each when `extras`. ``only`` names the cells to build."""
    cells: dict[str, dict] = {}
    means: dict[str, dict[float, float | None]] = {t: {} for t in TAGS}
    series: dict[tuple[str, float], dict] = {}
    for tag in TAGS:
        for cut in CUTS:
            if not require_larger and only is not None and key_name(None, tag, cut) not in only:
                continue
            series[(tag, cut)] = cell_series(scan, sessions, tag, cut)
            d = series[(tag, cut)]["diffs"]
            means[tag][cut] = statistics.fmean(d) if d else None
    for tag in TAGS:
        for cut in CUTS:
            name = key_name(None, tag, cut)
            if only is not None and name not in only:
                continue
            res = _mean_ci(series[(tag, cut)], trials, RESAMPLES)
            ok = larger_cuts_ok(means[tag], cut) if require_larger else True
            lo = None if res["interval"] is None else res["interval"][0]
            cell = {"family": "first", "filter": None, "tag": tag, "cut": cut, **res,
                    "larger_ok": ok if require_larger else None,
                    "verdict": verdict_word(res["sessions"], lo, ok=ok), "trials": trials}  # fmt: skip
            if extras:
                top5 = most_frequent(scan, sessions, tag, cut, None)
                cell["readings"] = {
                    "placebo": _reading(cell_series(scan, sessions, tag, cut, lag=1)),
                    "drop_five": {
                        **_reading(cell_series(scan, sessions, tag, cut, drop=frozenset(top5))),
                        "names": top5,
                    },
                }
            cells[name] = cell
    return cells


def unfiltered_name(flt: str, tag: str, cut: float) -> str:
    """The first-family cell a filtered one must beat: the same tag at the same cut (F3: the middle cut's
    put sale, as F3 replaces the cut)."""
    return key_name(None, "put_sell", F3_COMPARE_CUT) if flt == "F3" else key_name(None, tag, cut)


def second_family(
    scan: Scan,
    sessions: Sequence[str],
    first: dict,
    *,
    only: set[str] | None = None,
    trials: int = SECOND_TRIALS,
    require_larger: bool = True,
) -> dict:
    """The forty cells of the second family. A filtered tag must also beat its unfiltered tag (the first
    family's cell, in ``first``, over the same sessions) and, unless `require_larger` is off, the larger
    cuts of its own filter must do at least as well."""
    cells_keys = [(f, t, c) for fam, f, t, c in cell_keys() if fam == "second"]
    series = {
        key_name(f, t, c): cell_series(scan, sessions, t, c, f)
        for f, t, c in cells_keys
        if only is None or key_name(f, t, c) in only
    }
    means: dict[tuple[str, str], dict[float, float | None]] = {}
    for f, t, c in cells_keys:
        d = series.get(key_name(f, t, c), {}).get("diffs")
        means.setdefault((f, t), {})[c] = statistics.fmean(d) if d else None
    cells: dict[str, dict] = {}
    for f, t, c in cells_keys:
        name = key_name(f, t, c)
        if name not in series:
            continue
        res = _mean_ci(series[name], trials, RESAMPLES)
        ok = True if f == "F3" or not require_larger else larger_cuts_ok(means[(f, t)], c)
        base = first.get(unfiltered_name(f, t, c))
        base_mean = None if base is None else base["mean"]
        beats = res["mean"] is not None and base_mean is not None and res["mean"] > base_mean
        lo = None if res["interval"] is None else res["interval"][0]
        cells[name] = {"family": "second", "filter": f, "tag": t, "cut": c, **res,
                       "larger_ok": ok if require_larger and f != "F3" else None,
                       "unfiltered_mean": base_mean, "beats_unfiltered": beats,
                       "verdict": verdict_word(res["sessions"], lo, ok=ok and beats), "trials": trials}  # fmt: skip # noqa: E501
    return cells


# ------------------------------------------------------------------------------------------------
# The earnings test.


def earnings_sign(pre: Iterable[dict]) -> int:
    """+1 bullish, -1 bearish, 0 for none or a tie: the net of bullish minus bearish premium over the
    large opened orders (at `EARN_CUT`) in the sessions before a report."""
    net, seen = 0.0, False
    for c in pre:
        if c["premium"] >= EARN_CUT:
            seen = True
            net += TAGS[c["tag"]]["sign"] * c["premium"]
    if not seen or net == 0:
        return 0
    return 1 if net > 0 else -1


def earnings_trial(events: Sequence[dict], sign: int, trials: int = EARN_TRIALS) -> dict:
    """One direction's reports: hit rate (the move went the tag's way) against 50% and the mean signed
    excess less the assumed round-trip cost. ``events``: {react, sign, excess}. Passes when the
    deflated interval on the hit rate lies above one half and the mean after cost is positive."""
    mine = sorted((e for e in events if e["sign"] == sign), key=lambda e: e["react"])
    signed = [sign * e["excess"] for e in mine]
    hits = [1.0 if x > 0 else 0.0 for x in signed]
    ci = deflated_interval(hits, trials) if len(hits) >= 2 else None
    mean = statistics.fmean(signed) if signed else None
    after = None if mean is None else mean - EARN_COST
    lo = None if ci is None else ci[0]
    ok = after is not None and after > 0
    word = "no verdict" if len(hits) < MIN_EVENTS or lo is None else "pass" if lo > 0.5 and ok else "fail"
    return {
        "family": "earnings", "direction": "bullish" if sign > 0 else "bearish", "reports": len(hits),
        "hit_rate": statistics.fmean(hits) if hits else None,
        "interval": None if ci is None else list(ci),
        "mean_signed_excess": mean, "mean_after_cost": after, "verdict": word, "trials": trials,
    }  # fmt: skip


def earnings(scan: Scan, only: set[str] | None = None, trials: int = EARN_TRIALS) -> dict:
    events = []
    for e in scan.events:
        s = earnings_sign(e["pre"])
        if s:
            events.append({"react": e["react"], "sign": s, "excess": e["excess"]})
    out = {}
    for sign, name in ((1, "earnings/bullish"), (-1, "earnings/bearish")):
        if only is None or name in only:
            out[name] = earnings_trial(events, sign, trials)
    return out


# ------------------------------------------------------------------------------------------------
# The whole study, and the holdout's rule.


def evaluate(scan: Scan) -> dict:
    """The design window's study: all three families over the grid sessions, the first family also
    without the thin sessions."""
    sessions = list(scan.grid)
    thin = thin_sessions(scan.complete, sessions)
    first = first_family(scan, sessions)
    kept = [s for s in sessions if s not in thin]
    without = first_family(scan, kept, extras=False) if thin else None
    return {
        "thin_sessions": thin,
        "first": first,
        "first_without_thin": without,
        "second": second_family(scan, sessions, first),
        "earnings": earnings(scan),
    }


def passed(design: dict) -> list[str]:
    """The cells that passed the design window, in the order declared."""
    out = []
    for fam in ("first", "second", "earnings"):
        out += [k for k, c in design.get(fam, {}).items() if c["verdict"] == "pass"]
    return out


def evaluate_carried(scan: Scan, carried: Sequence[str]) -> dict:
    """The holdout: only the cells that passed design, each read once at its own cut. Deflation is by
    the number carried in its family (a cell alone is read at the plain 95%); the larger-cut check is a
    design check and is not asked again, but a filtered tag must still beat its unfiltered tag."""
    carried_set = set(carried)
    sessions = list(scan.grid)
    first_names = {k for k in carried_set if k.split("/")[0] in TAGS}
    earn_names = {k for k in carried_set if k.startswith("earnings/")}
    second_names = carried_set - first_names - earn_names
    need = set(first_names)
    for k in second_names:  # a filtered tag's unfiltered comparison
        parts = k.split("/")
        need.add(key_name(None, "put_sell", F3_COMPARE_CUT) if parts[0] == "F3" else f"{parts[1]}/{parts[2]}")
    first = first_family(scan, sessions, only=need, trials=max(len(first_names), 1), require_larger=False)
    second = second_family(
        scan, sessions, first, only=second_names, trials=max(len(second_names), 1), require_larger=False
    )
    return {
        "first": {k: c for k, c in first.items() if k in first_names},
        "second": second,
        "earnings": earnings(scan, earn_names, max(len(earn_names), 1)),
    }


def spent(out_dir, window: str) -> str | None:
    """Why a window's study may not run, or None. The holdout needs a design result that carries a
    pass, and is read once: a result or a spent marker already there refuses it."""
    d = Path(out_dir)
    if window == "design":
        return None
    if (d / "results-holdout.json").exists() or (d / "holdout-spent.json").exists():
        return "the holdout is spent: its result is already there (it is read once)"
    design = d / "results-design.json"
    if not design.exists():
        return "no design result: the holdout is read only for tags that passed design"
    try:
        data = json.loads(design.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "the design result cannot be read"
    if not data.get("passed"):
        return "no tag passed design: there is nothing to spend the holdout on"
    return None
