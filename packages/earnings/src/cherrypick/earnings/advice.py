"""The loop side of the agentic layer for earnings — advised twins of the strat_test books.

An out-of-band advisor proposes management parameters; this re-validates them with the SAME core
code the producer used (`cherrypick.core.advice`) against this module's own `advice.bounds`
manifest, and the harness opens a **twin** position beside each admitted strategy's ordinary one.
Absent, stale, expired or invalid advice all mean baseline: no twins, and nothing else changes.

Why a twin rather than an overlay on the real book: the comparison has to be paired. The twin gets
byte-identical fill economics — same legs, same credit, same quantity, same modeled costs — so the
only thing separating `advised:strat_test:<strategy>` from `strat_test:<strategy>` is the
management params, which is the variable under test. Anything else and the difference in P&L is
unattributable.

Why the params are frozen ON THE ROW: exit thresholds are read from config at *decision* time, not
from the trade. A read-once overlay held in memory would govern entries today and silently stop
governing exits tomorrow, leaving an open position managed by rules nobody chose. Stamped on the
row, `management.effective_config` restates them at every later tick, so exit continuity is free:
advice stops, no new twins open, and the twins already on the book keep being managed and closed
under their own terms.

Bounds use **dotted** param names, `"<strategy>.<param>"` — `cherrypick.core.advice` treats a param
name as opaque, so the convention needs no contract change, and this module splits on the first dot.
v1 bounds are management/exit params only. Entry-side screens and sizing change *which* trades open,
which a twin cannot express: propose those as `creative` and let a human decide.
"""

from __future__ import annotations

import json
import os
from typing import Any

from cherrypick.core import advice as _core_advice
from cherrypick.core import home as _core_home

from cherrypick.earnings import paths as _paths

ADVISED_PREFIX = "advised:"


def decision_path() -> str:
    return str(_paths.data_path("advice_active.json"))


def decision(config: dict, session: str) -> dict[str, Any]:
    """Today's advice decision, derived ONCE per session and replayed thereafter.

    Read-once across processes: the entry scan records what it decided, and anything later replays
    that record, so advice cannot start, stop or change mid-session however late an artifact lands
    or however the config is flipped.
    """
    acfg = config.get("advice") or {}
    path = decision_path()

    recorded = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                recorded = json.load(handle)
        except (OSError, ValueError):
            recorded = None
        if recorded is not None and recorded.get("day") != session:
            recorded = None  # yesterday's decision; today re-derives its own
    if recorded is not None:
        return recorded

    if acfg.get("enabled") and acfg.get("bounds"):
        result = _core_advice.load(_core_home.state_dir(), "earnings", session, acfg.get("bounds") or {})
        params = {p["param"]: p["value"] for p in result["proposals"]} or None
        recorded = {
            "day": session,
            "params": params,
            "reason": result["reason"],
            "proposals": result["proposals"],
            "rejected": result.get("rejected") or [],
        }
    else:
        recorded = {"day": session, "params": None, "reason": "advice_disabled"}

    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(recorded, handle, indent=2)
    except OSError:
        pass  # the decision still applies to this process; the next one re-derives it
    return recorded


def params_for(decided: dict, strategy: str) -> dict[str, Any]:
    """The admitted params that belong to one strategy, with the dotted prefix stripped.

    `{"iron_fly.profit_target_pct": 0.3}` for `iron_fly` becomes `{"profit_target_pct": 0.3}` —
    strategy-local names, because that is what a strategy's own config block holds and what
    `management.effective_config` overlays onto it.
    """
    out: dict[str, Any] = {}
    for dotted, value in (decided.get("params") or {}).items():
        prefix, _, name = str(dotted).partition(".")
        if prefix == strategy and name:
            out[name] = value
    return out


def advised_book(book: str) -> str:
    """The twin's profile tag: `advised:strat_test:<strategy>` beside `strat_test:<strategy>`.

    One place, because three surfaces have to agree on it — the row written here, the verdict that
    groups by it, and the console that renders it next to its control.
    """
    return f"{ADVISED_PREFIX}{book}"


def twin_spec(save_spec: dict, params: dict[str, Any]) -> dict:
    """The advised twin of a saved entry: identical fills, its own book, its params stamped on.

    `order_id` is prefixed rather than regenerated so the pair is obvious in the ledger and a twin
    can never collide with a real order id.
    """
    return {
        **save_spec,
        "order_id": f"{ADVISED_PREFIX.rstrip(':')}-{save_spec['order_id']}",
        "profile": advised_book(save_spec["profile"]),
        "advice_params": json.dumps(params),
    }


def is_advised(profile: str | None) -> bool:
    return bool(profile) and str(profile).startswith(ADVISED_PREFIX)
