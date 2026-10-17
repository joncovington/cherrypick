"""Strategy analysis: what an arm's closed trades say beyond its headline figures.

`core.metrics` answers "how did this arm do" (net, win rate, profit factor, Sharpe, max drawdown).
This module answers the questions asked before trusting an arm with money
(docs/strategy-analysis-plan.md, Phase 1):

- `streaks`            the longest winning and losing runs, and the run in progress
- `drawdown_episodes`  every fall below a previous high: its peak, its low, when (or whether) a
                       new high came, and how long the fall and the recovery each took
- `durability`         the net once the arm's biggest winners are removed
- `stress`             how deep the worst drawdown could have been with the same sessions in
                       another order, shuffled one session at a time and in blocks

All of it is a pure function of the normalised records `core.ledgers` yields, so it reads paper,
live and replay output the same way, and the same records always give the same answer: the
stress test's resampling is seeded, never drawn from the clock.

Two rules hold throughout, the same ones `core.metrics` keeps. A record whose net was never
recorded (`net_pnl` None) joins no sum and no count. And a figure the sample cannot support comes
back None with the reason beside it, never as a number computed from too little.

`arm_reading` bundles these with `calibration_reading` for one arm; `python -m
cherrypick.core.analysis` is the JSON bridge the console reads it through, the same pattern as
`core.metrics`.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from datetime import date

from cherrypick.core.analysis import capital
from cherrypick.core.metrics import _scored, calibration_reading, max_drawdown, session_nets_dated

#: The fact set's version. Bump it when a field changes meaning, so a stored reading is never
#: compared with a newer one as though they were the same measurement.
VERSION = 1

#: Shares of the trade count whose largest winners `durability` removes.
DURABILITY_FRACTIONS = (0.01, 0.05, 0.10)

#: The stress test. Below `STRESS_MIN_SESSIONS` a reshuffled drawdown is a reshuffle of a handful
#: of numbers and says nothing, so it is withheld. The block length is in sessions: long enough to
#: keep a bad week together, short enough that a short sample still has blocks to rearrange.
STRESS_MIN_SESSIONS = 20
STRESS_RESAMPLES = 2000
STRESS_QUANTILE = 0.95
STRESS_BLOCK = 5
STRESS_SEED = 20261016

#: How many drawdown episodes a reading lists, deepest first. All of them are counted.
EPISODES_LISTED = 10


def _ordered(records: Sequence[Mapping]) -> list[Mapping]:
    """The scored records in the order they happened: by session, then entry time where the
    schema records it. Two trades in one session with no entry time keep the ledger's order."""
    return sorted(_scored(records), key=lambda r: (r.get("session") or "", r.get("entry_time") or ""))


# --------------------------------------------------------------------------- streaks
def streaks(values: Sequence[float]) -> dict:
    """The longest run of wins, the longest run of losses, and the run the series ends on.

    A win is a net above zero and a loss a net below it. A trade that nets exactly zero is neither
    and ends whichever run it interrupts: counting it as a win would flatter a scratch, and
    counting it as a loss would do the opposite."""
    longest = {"win": 0, "loss": 0}
    kind, length = None, 0
    for v in values:
        this = "win" if v > 0 else "loss" if v < 0 else None
        if this is not None and this == kind:
            length += 1
        else:
            kind, length = this, (1 if this else 0)
        if kind:
            longest[kind] = max(longest[kind], length)
    return {
        "longest_win": longest["win"],
        "longest_loss": longest["loss"],
        "current": {"kind": kind, "length": length} if kind else None,
    }


# --------------------------------------------------------------------------- drawdown episodes
def _days(a: str | None, b: str | None) -> int | None:
    """Calendar days from session `a` to session `b`, or None when either is unknown."""
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


def drawdown_episodes(dated: Sequence[tuple[str, float]]) -> list[dict]:
    """Every episode of the equity path below a previous high, in the order they began.

    `dated` is (session, net) in session order, as `session_nets_dated` gives it. Equity starts at
    zero before the first session, and that start counts as a high: an arm that loses from its
    first trade is in a drawdown from that trade, with `peak_session` None.

    An episode runs from its high to the first session that regains it. Its duration is split in
    two, because a fall and a slow climb back are different risks: `sessions_falling` (high to
    low) and `sessions_recovering` (low to new high). Each is also given in calendar days. An
    episode the series ends inside has `recovered_session` None and `open` True; its recovery has
    not happened, so its recovering length is None rather than the time so far. Sessions here are
    the sessions the arm traded, which is why calendar days are given beside them."""
    episodes: list[dict] = []
    equity = peak = 0.0
    peak_i = -1  # index of the session that set the high; -1 is the start, before any session
    current: dict | None = None
    sessions = [s for s, _ in dated]

    def at(i: int) -> str | None:
        return sessions[i] if i >= 0 else None

    for i, (_, net) in enumerate(dated):
        equity = round(equity + net, 2)
        if equity >= peak:
            if current is not None:
                current.update(
                    recovered_session=at(i),
                    sessions_recovering=i - current["_low_i"],
                    days_recovering=_days(current["low_session"], at(i)),
                    open=False,
                )
                episodes.append(current)
                current = None
            peak, peak_i = equity, i
            continue
        if current is None:
            current = {
                "peak_session": at(peak_i),
                "peak_equity": peak,
                "low_session": at(i),
                "low_equity": equity,
                "_peak_i": peak_i,
                "_low_i": i,
            }
        elif equity < current["low_equity"]:
            current.update(low_session=at(i), low_equity=equity, _low_i=i)
    if current is not None:
        current.update(recovered_session=None, sessions_recovering=None, days_recovering=None, open=True)
        episodes.append(current)

    out = []
    for e in episodes:
        peak_i, low_i = e.pop("_peak_i"), e.pop("_low_i")
        e["depth"] = round(e["peak_equity"] - e["low_equity"], 2)
        e["sessions_falling"] = low_i - peak_i
        # From the start (no peak session) the fall is measured from the first session traded.
        e["days_falling"] = _days(e["peak_session"] or (sessions[0] if sessions else None), e["low_session"])
        out.append(e)
    return out


def _episode_summary(episodes: list[dict]) -> dict:
    """The episodes a reading shows: the deepest few, plus the counts a reader asks first."""
    closed = [e for e in episodes if not e["open"]]

    def length(e: dict) -> int:
        return e["sessions_falling"] + (e["sessions_recovering"] or 0)

    return {
        "count": len(episodes),
        "open": next((e for e in episodes if e["open"]), None),
        "deepest": sorted(episodes, key=lambda e: e["depth"], reverse=True)[:EPISODES_LISTED],
        "longest_closed": max(closed, key=length) if closed else None,
    }


# --------------------------------------------------------------------------- durability
def durability(values: Sequence[float], fractions: Sequence[float] = DURABILITY_FRACTIONS) -> dict:
    """The net with the largest winners taken out: for each fraction, the top ceil(fraction x n)
    winning trades are removed and the rest summed.

    This removes rows from the result; it does not re-run the strategy. A strategy whose entries
    depend on earlier ones (one position at a time) would have traded differently without them,
    and the figure cannot say how. It answers how much of the result rests on a few trades, which
    is what an arm whose wins are rare and large needs answering. Only winners are removed: a
    fraction with fewer winners than its count removes every winner there is, and says so."""
    nets = list(values)
    total = round(sum(nets), 2)
    winners = sorted((v for v in nets if v > 0), reverse=True)
    rows = []
    for f in fractions:
        k = math.ceil(f * len(nets)) if nets else 0
        removed = winners[:k]
        net = round(total - sum(removed), 2)
        rows.append(
            {
                "fraction": f,
                "asked": k,
                "removed": len(removed),
                "removed_net": round(sum(removed), 2),
                "net": net,
                "sign_flips": (total > 0) != (net > 0) if nets else False,
            }
        )
    return {"net": total, "trades": len(nets), "without_top": rows}


# --------------------------------------------------------------------------- stress test
def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)], 2)


def stress(
    session_values: Sequence[float],
    *,
    resamples: int = STRESS_RESAMPLES,
    quantile: float = STRESS_QUANTILE,
    block: int = STRESS_BLOCK,
    seed: int = STRESS_SEED,
    min_sessions: int = STRESS_MIN_SESSIONS,
) -> dict:
    """How deep the worst drawdown could have been with the same session results in another order.

    `reordered` shuffles the sessions one at a time. `grouped` cuts the series into blocks of
    `block` consecutive sessions (from a random offset, so the cuts move between resamples)
    and shuffles the blocks, so a bad stretch stays a stretch, which is closer to how losses
    actually arrive. Each reports the `quantile` of the resampled max drawdowns.

    Both are permutations: every resample holds exactly the recorded sessions, so the total is
    the same and only the order changes. A bootstrap with replacement (`stats.stationary_indices`)
    was tried first and is not used: it can draw one bad week twice and drop a good one, which
    answers a different question (another sample, not another order) and on flies' control arm
    read about 65% deeper than the reordering for that reason. Neither figure invents a price or a
    trade: a market worse than the one recorded is outside what this can say.

    Withheld (None, with the reason) below `min_sessions`. The seed is fixed so the same sessions
    always give the same figures."""
    values = list(session_values)
    out = {
        "historical": max_drawdown(values),
        "reordered": None,
        "grouped": None,
        "sessions": len(values),
        "quantile": quantile,
        "resamples": resamples,
        "block": block,
        "seed": seed,
        "withheld": None,
    }
    if len(values) < min_sessions:
        out["withheld"] = f"{len(values)} sessions, fewer than {min_sessions}"
        return out
    rng = random.Random(seed)
    size = max(1, block)
    reordered, grouped = [], []
    for _ in range(resamples):
        shuffled = values[:]
        rng.shuffle(shuffled)
        reordered.append(max_drawdown(shuffled))
        cut = rng.randrange(size)
        blocks = [values[:cut]] if cut else []
        blocks += [values[i : i + size] for i in range(cut, len(values), size)]
        rng.shuffle(blocks)
        grouped.append(max_drawdown([v for block in blocks for v in block]))
    out["reordered"] = _quantile(reordered, quantile)
    out["grouped"] = _quantile(grouped, quantile)
    return out


# --------------------------------------------------------------------------- one arm
#: Trade fields `core.ledgers` supplies for some schemas (see its docstring); their coverage is
#: reported so a reading never implies a field was recorded where it was not.
TRADE_FIELDS = ("entry_time", "exit_time", "entry_spot", "exit_spot")


def _capital(ordered: Sequence[Mapping], dated, allocations) -> dict:
    """Observed peak risk and the declared allocation (`capital.py`), with the two figures each
    makes readable: return on the peak, and the session max drawdown as a share of it."""
    peak = capital.peak_risk(ordered)
    net = round(sum(v for _, v in dated), 2)
    dd = max_drawdown([v for _, v in dated])
    p = peak["peak"]
    # Over the trades whose capital is known only when that is every trade: a ratio over part of
    # the sample, read against the whole sample's net, is the partial-coverage trap metrics avoids.
    full = peak["capital_known"] == peak["trades"] and p
    return {
        "peak_risk": peak,
        "return_on_peak_risk": round(net / p, 4) if full else None,
        "max_drawdown_over_peak_risk": round(dd / p, 4) if full else None,
        "allocation": capital.allocation_reading(dated, allocations),
    }


def arm_reading(records: Sequence[Mapping], allocations: Sequence[Mapping] | None = None) -> dict:
    """The analysis of one arm's closed records: `calibration_reading` plus everything above.

    `allocations` are the arm's declared allocations (`capital.load_allocations`), or None."""
    ordered = _ordered(records)
    nets = [r["net_pnl"] for r in ordered]
    dated = session_nets_dated(ordered)
    episodes = drawdown_episodes(dated)
    best = max(ordered, key=lambda r: r["net_pnl"], default=None)
    worst = min(ordered, key=lambda r: r["net_pnl"], default=None)

    def trade(r: Mapping | None) -> dict | None:
        if r is None:
            return None
        return {"net": round(r["net_pnl"], 2), "session": r.get("session"), "entry_time": r.get("entry_time")}

    return {
        "version": VERSION,
        "reading": calibration_reading(records),
        "best_trade": trade(best),
        "worst_trade": trade(worst),
        # Two runs, because a module that holds several positions a session (flies' verticals,
        # MEIC's entries) can string many winning trades through a losing day: control's longest
        # trade-level winning run was 22 on an arm that lost money. The session run is the one
        # that says how many days in a row an arm made or lost.
        "streaks": streaks(nets),
        "session_streaks": streaks([v for _, v in dated]),
        "drawdowns": _episode_summary(episodes),
        "durability": durability(nets),
        "stress": stress([v for _, v in dated]),
        "capital": _capital(ordered, dated, allocations),
        "coverage": {
            "trades": len(ordered),
            **{f: sum(1 for r in ordered if r.get(f) is not None) for f in TRADE_FIELDS},
        },
    }
