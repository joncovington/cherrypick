"""The gates-off oracle: what each of a MEIC arm's entry gates adds over the trigger it gates.

A gate is judged today one study at a time, by an arm with the gate and one without (items 33, 41,
the GEX sign gate). The oracle asks it of every gate an arm has switched on, at once, from one replay:

- **`<arm>`**, the arm as declared;
- **`<arm>-open`**, the same arm with every listed gate switched off: the bare trigger;
- **`<arm>-no-<gate>`**, the arm with only that gate switched off, one per gate the arm has on.

Two readings come out of it.

- **Attribution:** every trade `-open` took is matched with what `<arm>` recorded on the same tick
  of the same session (`entry_attempts`, which the replay writes for every arm). A tick `<arm>`
  refused for a gate's reason is that gate's trade, and its net is what the gate kept out. MEIC
  checks gates in a fixed order and records the first refusal only, so a tick two gates would refuse
  is credited to the earlier one, and a tick `<arm>` refused for spacing or capacity (its book had
  diverged from `-open`'s by then) is counted as `path`, never as a gate's.
- **Leave-one-out:** each `-no-<gate>` against `<arm>`, paired per session (a session without a
  trade counts at zero). This is the gate's marginal value with every other gate in place, free of
  the ordering and of the path; attribution says where it came from.

Only switchable gates are listed. The 0DTE check, the entry window, spacing, the daily target and
the concurrency cap are the trigger itself, and the quarterly, triple-witching and FOMC rules have no
off switch; all of them stay on in every arm.

It is a read, never a verdict: no pass test, no trial (the script runs it provisionally over the
design window and refuses the holdout), and nothing in it changes a live gate. Everything here is
pure; `scripts/meic_gate_oracle.py` runs the replay and reads the ledgers.
"""

from __future__ import annotations

import math
import sqlite3
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

OPEN = "open"
NO = "no"
#: A tick `<arm>` also filled on: no gate kept that trade out.
BOTH = "both_filled"
#: A tick `<arm>` refused for a reason no listed gate gives (spacing, capacity, window): its book had
#: already diverged from `-open`'s, so the trade is the path's, not a gate's.
PATH = "path"
#: A tick with no `<arm>` row at all.
NO_ROW = "no_row"


@dataclass(frozen=True)
class Gate:
    name: str
    #: The overrides that switch the gate off.
    off: Mapping[str, object]
    #: The `block_detail` reasons it refuses with (MEIC's `paper.evaluate_entry`).
    reasons: tuple[str, ...]
    #: The key that says whether it is on, and its default when the arm does not set it.
    key: str
    default: object


#: MEIC's switchable entry gates, in `paper.evaluate_entry`'s order (meic/GATES.md lists them).
MEIC_GATES: tuple[Gate, ...] = (
    Gate("iv-rank", {"min_iv_rank": 0.0}, ("iv_rank_below_floor",), "min_iv_rank", None),
    Gate(
        "vix",
        {"regime_vix_pause_threshold": 999},
        ("regime_vix_elevated",),
        "regime_vix_pause_threshold",
        None,
    ),
    Gate(
        "vix1d",
        {"regime_vix1d_ratio_pause_threshold": 999},
        ("regime_vix1d_ratio_elevated",),
        "regime_vix1d_ratio_pause_threshold",
        1.30,
    ),
    Gate(
        "atr",
        {"regime_atr_pause_threshold_pct": None},
        ("regime_atr_elevated",),
        "regime_atr_pause_threshold_pct",
        None,
    ),
    Gate(
        "gex-negative",
        {"regime_gex_block_negative": False},
        ("regime_gex_negative",),
        "regime_gex_block_negative",
        True,
    ),
    Gate(
        "gex-positive",
        {"regime_gex_require_positive": False},
        ("regime_gex_not_positive",),
        "regime_gex_require_positive",
        False,
    ),
    Gate(
        "gex-flip",
        {"regime_gex_min_flip_distance_pct": None},
        ("regime_gex_flip_too_close",),
        "regime_gex_min_flip_distance_pct",
        None,
    ),
    Gate(
        "late-bias",
        {"late_entry_bias_enabled": False},
        ("late_entry_bias_wait",),
        "late_entry_bias_enabled",
        False,
    ),
    Gate(
        "stabilization", {"stabilization_entry": False}, ("stabilization_wait",), "stabilization_entry", False
    ),
    Gate(
        "stabilization-placebo",
        {"stabilization_placebo_rate": None},
        ("stabilization_placebo_wait",),
        "stabilization_placebo_rate",
        None,
    ),
)
_BY_REASON = {r: g.name for g in MEIC_GATES for r in g.reasons}


def is_on(gate: Gate, params: Mapping) -> bool:
    """Whether the arm has the gate on: its key, or the code's default when the arm does not set it,
    against the gate's off value. A pause threshold of 999 or more is off (the suite's spelling of
    "never pause"); a floor, rate or distance of zero is off."""
    value = params.get(gate.key, gate.default)
    off = gate.off[gate.key]
    if value is None:
        return False
    if off is False:
        return bool(value)
    if off == 999:
        return value < 999
    return value > 0


def active(params: Mapping, gates: Iterable[Gate] = MEIC_GATES) -> list[Gate]:
    return [g for g in gates if is_on(g, params)]


def arm_name(base: str, gate: str | None = None) -> str:
    return f"{base}-{OPEN}" if gate is None else f"{base}-{NO}-{gate}"


def oracle_profiles(base: str, profile: Mapping, params: Mapping) -> dict[str, dict]:
    """The oracle's arms over one declared arm: `profile` is its registry entry, `params` the same
    merged over the base config (what decides whether a gate is on). The base arm itself is not
    redefined; each new arm is the profile with overrides on top."""
    on = active(params)
    clean = {k: v for k, v in profile.items() if not str(k).startswith("_")}
    out = {arm_name(base): {**clean, **{k: v for g in on for k, v in g.off.items()}}}
    for g in on:
        out[arm_name(base, g.name)] = {**clean, **g.off}
    return out


def attribute(open_fills: Iterable[Mapping], gated: Mapping[tuple[str, str], Mapping]) -> list[dict]:
    """Each `-open` trade with what `<arm>` did on its tick. `open_fills`: {session, ts, net};
    `gated`: (session, ts) -> {outcome, block_detail}. Pure."""
    out = []
    for f in open_fills:
        row = gated.get((f["session"], f["ts"]))
        if row is None:
            by = NO_ROW
        elif row.get("outcome") == "filled":
            by = BOTH
        else:
            by = _BY_REASON.get(row.get("block_detail") or "", PATH)
        out.append({**f, "by": by})
    return out


def _paired(a: Mapping[str, float], b: Mapping[str, float], sessions: Iterable[str]) -> dict:
    diffs = [a.get(s, 0.0) - b.get(s, 0.0) for s in sessions]
    n = len(diffs)
    if n < 2:
        return {"sessions": n, "mean": None, "t": None, "sessions_differing": None}
    mean = statistics.fmean(diffs)
    sd = statistics.stdev(diffs)
    return {
        "sessions": n,
        "mean": mean,
        "t": None if sd == 0 else mean / (sd / math.sqrt(n)),
        "sessions_differing": sum(1 for d in diffs if d != 0),
    }


def summary(
    base: str,
    gates: Iterable[str],
    by_session: Mapping[str, Mapping[str, float]],
    sessions: Iterable[str],
    attributed: Iterable[Mapping],
) -> dict:
    """The oracle's read. `by_session`: arm -> session -> net (absent = no trade = 0). Per gate: the
    `-open` trades attributed to it (count, summed and mean net: what it kept out; negative means it
    kept out losers), and leave-one-out (`-no-<gate>` minus `<arm>`, paired: what removing it would
    change; negative means the gate helps). Pure."""
    days = sorted(set(sessions))
    rows = list(attributed)
    base_s = by_session.get(base, {})
    out: dict = {
        "base": base,
        "sessions": len(days),
        "open_vs_base": _paired(by_session.get(arm_name(base), {}), base_s, days),
        "gates": {},
        "unattributed": {},
    }
    for g in gates:
        mine = [r["net"] for r in rows if r["by"] == g]
        out["gates"][g] = {
            "blocked_trades": len(mine),
            "blocked_net": sum(mine),
            "blocked_mean": statistics.fmean(mine) if mine else None,
            "leave_one_out": _paired(by_session.get(arm_name(base, g), {}), base_s, days),
        }
    for tag in (BOTH, PATH, NO_ROW):
        mine = [r["net"] for r in rows if r["by"] == tag]
        out["unattributed"][tag] = {"trades": len(mine), "net": sum(mine)}
    return out


# ------------------------------------------------------------------------------------------------
# Reading one run's ledgers (results/runs/<run_id>/<session>.db, one per session, every arm in it).


def read_session(db: Path, arms: Iterable[str]) -> tuple[dict[str, float], list[dict], dict]:
    """(arm -> net, `-open`'s fills, `<arm>`'s ticks) for one session ledger, for `arms` = (base,
    open). Net is `pnl - fees` over closed trades, as `core.ledgers` reads `meic_ic`."""
    base, opened = arms
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        net: dict[str, float] = {}
        trade_net: dict[str, float] = {}
        for arm, oid, pnl, fees in conn.execute(
            "SELECT arm, ic_order_id, pnl, fees FROM ic_trades WHERE exit_time IS NOT NULL"
        ):
            v = (pnl or 0.0) - (fees or 0.0)
            net[arm] = net.get(arm, 0.0) + v
            trade_net[oid] = v
        fills, ticks = [], {}
        for arm, day, ts, outcome, detail, oid in conn.execute(
            "SELECT arm, trade_date, ts, outcome, block_detail, ic_order_id FROM entry_attempts "
            "WHERE arm IN (?, ?)",
            (base, opened),
        ):
            if arm == opened and outcome == "filled" and oid in trade_net:
                fills.append({"session": day, "ts": ts, "net": trade_net[oid]})
            elif arm == base:
                ticks[(day, ts)] = {"outcome": outcome, "block_detail": detail}
        return net, fills, ticks
    finally:
        conn.close()
