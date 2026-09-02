"""What each module will accept advice about — read from its deployed config, never written.

A module opts in by putting an ``advice`` block in its own config. That block is the human's half of
the contract: which parameters may move, and between which values. Nothing here can create one,
widen one, or route around one — the advisor reads the block, and every proposal is checked against
it by ``cherrypick.core.advice``, the same code the loop re-checks with at session start.

Bounds are re-read on every enact, deliberately. A human who tightens a range tonight has tightened
it by tomorrow morning, without touching the experiment that is running inside it.

Three shapes, one contract:

* **meic** — ``advice.{enabled, base_profile, bounds}``; params are risk-profile keys, and the
  advised book is ``advised:<base_profile>``.
* **flies** — ``advice.{enabled, base_arm, bounds}``; params are ``merged_params`` keys, and the
  advised book is the synthetic arm ``advised:<base_arm>``.
* **earnings** — ``advice.{enabled, bounds}`` with **dotted** param names,
  ``"<strategy>.<param>"``. ``core.advice`` treats a param name as opaque, so the dotted convention
  needs no contract change; the consumer splits on the first dot. v1 bounds are management/exit
  params only — entry-side screens change *which* trades open, which a twin book cannot express.
* **calendars** — ``advice.{enabled, base_book, bounds}``; params are that module's management/exit
  keys (profit target, stop, time exit, long disposition, strike-touch), and the advised book is
  ``advised:<base_book>``. Exit-only by construction — the module's entry is unconditional every
  week, so there is nothing entry-side to advise, which makes it the cleanest fit for the v1
  management-params-only contract. Entries only happen on the weekly entry day, so an artifact
  landing any other session admits params that open nothing — expected, not a failure.
* **pmcc** — ``advice.{enabled, base_book, bounds}``; params are that module's management keys plus
  the entry yield floor (``tv_close_threshold``, ``target_weekly_yield_min``), and the advised book
  is ``advised:<base_book>``. The roll-vs-hold choice is NOT advisable — it is the module's own
  book contrast, not a parameter.
"""

from __future__ import annotations

from typing import Any

from cherrypick.advisor import paths as _paths
from cherrypick.advisor import store as _store

# Where each module keeps the base book an advised book shadows, and what to call it when the
# config does not say. Earnings has no single base profile: its advised books are per-strategy
# twins (`advised:strat_test:<strategy>`), so the base is a prefix, not a profile name.
_BASE_KEY = {
    "meic": ("base_profile", "control"),
    "flies": ("base_arm", "control"),
    "earnings": ("base_prefix", "strat_test"),
    "calendars": ("base_book", "control"),
    "pmcc": ("base_book", "control"),
    # bwb and curve added 2026-08-26. Both consume advice through the same
    # `core.advice.session_decision` every other module uses, both declare an `advice` block with
    # bounds, and the suite config already listed bwb under `advisor.modules` — but neither was in
    # this map, so `MODULES` excluded them, `enact` never selected them, and NO ARTIFACT HAS EVER
    # BEEN WRITTEN for either. bwb had twelve declared bounds and a loop reading for advice that
    # could not arrive; the config granted the advisor a module no code path could act on.
    #
    # curve is included even though its own `advice.enabled` is false. That is the point: each
    # module's own config decides whether anything happens, and a module absent from this map cannot
    # decide at all. Being here and disabled is a state that reports itself; being missing is not.
    "bwb": ("base_book", "control"),
    "curve": ("base_book", "control"),
}

MODULES = tuple(_BASE_KEY)


def resolve(module: str) -> dict[str, Any]:
    """This module's advice posture right now: ``{module, enabled, base_profile, bounds, reason}``.

    ``enabled`` is false whenever advice cannot be admitted for any reason — no config, no
    ``advice`` block, the block switched off, or an empty bounds manifest. `reason` says which, and
    it is the string the console shows and the rejection records, because "the advisor proposed
    nothing" and "the module refuses advice" look identical from the outside otherwise.
    """
    if module not in _BASE_KEY:
        return _off(module, None, f"unknown module {module!r}")

    key, default_base = _BASE_KEY[module]
    config = _store.read_json(_paths.module_config_path(module), default=None)
    if not isinstance(config, dict):
        return _off(module, default_base, "module_advice_disabled: module config not found")

    block = config.get("advice")
    if not isinstance(block, dict):
        return _off(module, default_base, "module_advice_disabled: no advice block in config")

    base = str(block.get(key) or default_base)
    manifest = block.get("bounds")
    if not isinstance(manifest, dict) or not manifest:
        return _off(module, base, "module_advice_disabled: advice.bounds is empty")
    if not block.get("enabled"):
        return _off(module, base, "module_advice_disabled: advice.enabled is false")

    return {
        "module": module,
        "enabled": True,
        "base_profile": base,
        "bounds": manifest,
        "reason": None,
    }


def _off(module: str, base: str | None, reason: str) -> dict[str, Any]:
    return {"module": module, "enabled": False, "base_profile": base, "bounds": {}, "reason": reason}


def all_modules(modules: tuple[str, ...] | list[str] | None = None) -> dict[str, dict[str, Any]]:
    return {m: resolve(m) for m in (modules or MODULES)}


def advised_tag(module: str, base_profile: str, strategy: str | None = None) -> str:
    """The profile tag the module's consumer will write on the advised book's rows.

    One place, because three surfaces need to agree on it: the consumer that tags the rows, the
    verdict that groups by it, and the console that renders it beside its control.
    """
    if module == "earnings":
        # Earnings twins are per-strategy: advised:strat_test:<strategy> beside strat_test:<strategy>.
        return f"advised:{base_profile}:{strategy}" if strategy else f"advised:{base_profile}"
    return f"advised:{base_profile}"


def split_param(module: str, param: str) -> tuple[str | None, str]:
    """``("iron_fly", "profit_target_pct")`` for earnings' dotted names; ``(None, param)`` elsewhere.

    Only earnings scopes a param to a strategy, because only earnings reads its exit thresholds from
    a per-strategy config block at decision time.
    """
    if module == "earnings" and "." in param:
        strategy, _, name = param.partition(".")
        return strategy, name
    return None, param


def strategies_in(module: str, params: dict[str, Any]) -> list[str]:
    """Which strategies an admitted param set touches — the earnings consumer opens one twin per
    strategy named here, and nothing for a strategy nobody proposed anything about."""
    seen: list[str] = []
    for param in params:
        strategy, _ = split_param(module, param)
        if strategy and strategy not in seen:
            seen.append(strategy)
    return seen
