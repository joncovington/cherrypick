"""cherrypick.core.robustness — what a declared test states before it runs, and reports after it passes.

`docs/trading-book-review-plan.md` B12, B13 and B9; the declaration template in
`docs/edge-hypotheses-plan.md` names each line these functions fill.

- **B12, minimum detectable effect** (`planned_mde`): the smallest edge the planned sample can detect
  at the pass bar, from the spread of comparable past results, so a "no verdict" says what it rules
  out. Paired by default; the arithmetic is `core.stats.minimum_detectable_effect`.
- **B13, robustness report** (`neighbourhood`, `best_sessions`, `cost_stress`, `drawdown_range`,
  `max_size`): beside every PASSING result -- the parameter neighbourhood as description (a
  plateau, or a lone peak flagged), how much of the net its few best sessions carry, the result at
  1.5x and 2x costs against a declared stress, the B1 bootstrap of its drawdown, and the largest
  size whose 99th-percentile drawdown stays inside a declared share of the account. Never used to
  re-choose the declared setting.
- **B9, replication across names** (`name_half`): a fixed hash of the symbol splits the universe in
  two, so a pass can be required in both name-halves as well as both time-halves.

Pure functions over numbers and symbols; nothing here reads a ledger, a clock or a file.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from cherrypick.core import killcriteria as _kill
from cherrypick.core.stats import DEFAULT_ALPHA, DEFAULT_POWER, mean_sd, minimum_detectable_effect

DEFAULT_STRESSES = (1.5, 2.0)
DEFAULT_PASS_STRESS = 1.5
#: A neighbour keeping at least this share of the declared setting's mean counts as on the plateau.
PLATEAU_SHARE = 0.5
#: How many of a result's best sessions `best_sessions` sets aside.
BEST_SESSIONS = 5
NAME_SALT = "cherrypick-b9"


# --------------------------------------------------------------------------- B9
def name_half(symbol: str, *, salt: str = NAME_SALT) -> int:
    """0 or 1, fixed for a symbol forever: the first bit of sha256(salt + upper-cased symbol). Not
    Python's `hash`, which is salted per process."""
    digest = hashlib.sha256(f"{salt}:{symbol.strip().upper()}".encode()).digest()
    return digest[0] & 1


def split_names(symbols: Sequence[str], *, salt: str = NAME_SALT) -> tuple[list[str], list[str]]:
    halves: tuple[list[str], list[str]] = ([], [])
    for s in sorted(set(symbols)):
        halves[name_half(s, salt=salt)].append(s)
    return halves


# --------------------------------------------------------------------------- B12
def planned_mde(
    past_values: Sequence[float],
    planned_n: int,
    *,
    paired: bool = True,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
    sided: int = 1,
) -> dict:
    """The declaration's MDE line. `past_values` are comparable past per-unit results (paired
    differences when `paired`, else one arm's values: an unpaired comparison of two arms needs
    twice the variance, so its MDE is multiplied by sqrt 2)."""
    _, sd = mean_sd(list(past_values))
    mde = minimum_detectable_effect(sd, planned_n, alpha=alpha, power=power, sided=sided)
    if mde is not None and not paired:
        mde *= 2**0.5
    return {
        "planned_n": planned_n,
        "sd": None if sd is None else round(sd, 4),
        "paired": paired,
        "alpha": alpha,
        "power": power,
        "mde": None if mde is None else round(mde, 4),
    }


# --------------------------------------------------------------------------- B13
def neighbourhood(
    means: Mapping[float, float | None], declared: float, *, share: float = PLATEAU_SHARE
) -> dict:
    """`means` maps each tested setting to its mean result. The declared setting is on a plateau when
    its nearest neighbour on each side (where one exists) keeps at least `share` of its mean with
    the same sign; otherwise it is a lone peak. Description only."""
    if declared not in means or means[declared] is None:
        return {"declared": declared, "verdict": "unmeasured", "rows": []}
    base = means[declared]
    keys = sorted(k for k, v in means.items() if v is not None)
    i = keys.index(declared)
    near = [keys[j] for j in (i - 1, i + 1) if 0 <= j < len(keys)]

    def holds(k):
        v = means[k]
        return (base > 0 and v >= share * base) or (base <= 0 and v <= share * base)

    rows = [{"setting": k, "mean": means[k], "declared": k == declared} for k in keys]
    if not near:
        verdict = "no_neighbours"
    else:
        verdict = "plateau" if all(holds(k) for k in near) else "lone_peak"
    return {"declared": declared, "verdict": verdict, "neighbours": near, "rows": rows}


def best_sessions(session_nets: Mapping[str, float], *, k: int = BEST_SESSIONS) -> dict:
    """How much of a result its `k` best sessions carry. `session_nets` maps each session to its
    net. `rest_mean` is the mean of every other session: zero or below means the result is those
    few days, which a one-session check misses when the best day alone is not decisive. `share` is
    the best sessions' part of a positive total, None when the total is zero or below. Ties go to
    the earlier session, so the same nets always name the same days. Description only."""
    if k < 1:
        raise ValueError("k must be at least 1")
    ranked = sorted(session_nets.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ranked) <= k:
        return {"k": k, "n": len(ranked), "insufficient": True}
    best, rest = ranked[:k], [v for _, v in ranked[k:]]
    total = sum(v for _, v in ranked)
    best_net = sum(v for _, v in best)
    rest_mean = sum(rest) / len(rest)
    return {
        "k": k,
        "n": len(ranked),
        "sessions": [s for s, _ in best],
        "best_net": round(best_net, 2),
        "total_net": round(total, 2),
        "share": round(best_net / total, 4) if total > 0 else None,
        "rest_mean": round(rest_mean, 4),
        "rests_on_best": rest_mean <= 0,
    }


def cost_stress(
    nets: Sequence[float],
    costs: Sequence[float],
    *,
    stresses: Sequence[float] = DEFAULT_STRESSES,
    pass_stress: float = DEFAULT_PASS_STRESS,
) -> dict:
    """Each trade's net with its costs scaled: net - (m - 1) x cost. The declared `pass_stress` must
    keep the mean positive for the pass to stand."""
    if len(nets) != len(costs):
        raise ValueError("nets and costs differ in length")
    rows = {}
    for m in sorted({1.0, *stresses, pass_stress}):
        stressed = [n - (m - 1) * c for n, c in zip(nets, costs, strict=True)]
        mean, _ = mean_sd(stressed)
        rows[m] = None if mean is None else round(mean, 4)
    held = rows[pass_stress] is not None and rows[pass_stress] > 0
    return {"mean_by_multiplier": rows, "pass_stress": pass_stress, "survives": held}


def drawdown_range(nets: Sequence[float], *, sessions: int | None = None, horizon: int = 100, **kw) -> dict:
    """The B1 bootstrap of the result's own trades: the drawdown and losing-streak range a live arm
    should expect, and its frozen thresholds if it goes live."""
    return _kill.derive(list(nets), sessions=sessions, horizon=horizon, **kw)


def max_size(drawdown_p99_per_unit: float | None, account: float, share: float) -> int:
    """The largest whole size whose 99th-percentile drawdown (per unit of size) stays within
    `share` of `account`. 0 when even one unit breaches it, or nothing was measured."""
    if not drawdown_p99_per_unit or drawdown_p99_per_unit <= 0:
        return 0
    return int(account * share // drawdown_p99_per_unit)


def report(
    nets: Sequence[float],
    costs: Sequence[float],
    *,
    means_by_setting: Mapping[float, float | None] | None = None,
    declared: float | None = None,
    sessions: int | None = None,
    account: float | None = None,
    account_share: float | None = None,
    pass_stress: float = DEFAULT_PASS_STRESS,
) -> dict:
    """The whole B13 report for one passing result."""
    out = {"cost_stress": cost_stress(nets, costs, pass_stress=pass_stress)}
    if means_by_setting is not None and declared is not None:
        out["neighbourhood"] = neighbourhood(means_by_setting, declared)
    dd = drawdown_range(nets, sessions=sessions)
    out["drawdown"] = dd
    if account and account_share and not dd.get("insufficient"):
        out["max_size"] = max_size(dd["drawdown_block"], account, account_share)
    return out
