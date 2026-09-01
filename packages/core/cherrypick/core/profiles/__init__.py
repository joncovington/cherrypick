"""cherrypick.core.profiles — named risk-profile registry + merge engine (Phase A).

Consolidates the profiling *mechanism* both suite modules share; profile *definitions* stay
per-module. The two override models unify here because EarningsAgent's merge is a strict superset of
MEICAgent's flat overlay (see plan Part 10): a top-level partial override, plus optional per-namespace
deep-merges (Earnings' `strategy_overrides`). Pure dict/JSON operations, no I/O beyond an optional
external profiles file.

Phase B adds the *attribution contract* (`attribution_tag`): every trade row carries a tag naming the
named risk profile (or parallel-shadow paper book) that opened it, and reporting groups P&L by that
tag. Phase C adds the calibration harness's *comparison engine* (`compare_profiles`): group tagged
trade rows by their attribution tag and apply a module-injected summary per group — the metric math
stays per-module (it is domain-divergent) while the grouping orchestration is shared. Phase D adds the
champion/challenger advisor (`recommend_champion`, `qualify_readings`): a pure, advisory, human-gated
recommendation of which tag deserves to be live — it never mutates config or switches live risk.

Phase D was originally a fixed-ladder "graduate one rung" model (`recommend_promotion`). Replaced
2026-08-01: a ladder assumes every module's tags form one ordered, most-to-least-conservative
sequence, which broke down the moment a module's tags are parallel, unordered experiment arms
(flies' control/gex/time_window/width-N) rather than risk tiers. Forcing those through "graduate to
the fixed next list entry" produced a real, reproducible, semantically meaningless recommendation
(a synthetic fully-qualifying `control` reading returned `"graduate:time_window"`, with no basis for
that direction — `control` isn't a riskier variant of `time_window`). The replacement drops the
ordering assumption: a module either designates a `champion` (the tag currently live) and every other
tag is a challenger judged against it (`recommend_champion`), or has no champion at all and every tag
just gets a pass/fail qualification reading with no comparison (`qualify_readings`) — which is what
flies' arms and earnings' profiles actually needed all along.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

# Canonical sentinel for a trade row that carries no named profile (a live trade, or a
# pre-attribution row) when it surfaces in a profile-grouped rollup. See `attribution_tag`.
UNTAGGED = "unassigned"


def load_profiles(config: Mapping | None = None, *, external_path: Any = None) -> dict:
    """Return the `{name: profile_def}` registry from either an inline config or an external JSON file.

    Dual-source so neither module migrates its layout: MEICAgent keeps profiles in a separate
    `config.risk.json` (pass `external_path`); EarningsAgent keeps them inline under
    `config["profiles"]` (pass `config`). The external file's top-level `"profiles"` key is used.
    """
    if external_path is not None:
        with open(external_path) as f:
            data = json.load(f)
        return dict(data.get("profiles", {}))
    return dict((config or {}).get("profiles", {}))


def select_profile(profiles: Mapping, name: str) -> dict:
    """Return the named profile's override dict, or raise ValueError listing the known names."""
    if name not in profiles:
        raise ValueError(f"unknown profile '{name}' -- known profiles: {sorted(profiles)}")
    return dict(profiles[name])


def attribution_tag(value: Any, *, untagged: str = UNTAGGED) -> str:
    """Normalize a stored profile tag into a stable attribution group key.

    The attribution contract: every trade row carries a profile tag naming which named risk
    profile (or parallel-shadow paper book) opened it, and reporting groups P&L by that tag.
    This normalizes a *read* value to a group key — the profile name if one was set, else
    `untagged` for rows with no named profile. `None`, empty, and whitespace-only values are
    all treated as untagged.

    Column name and the untagged sentinel stay per module (both are baked into committed
    schemas, so this is a value convention, not a column rename): MEICAgent's
    `ic_trades.risk_profile` is nullable and uses the default `"unassigned"`; EarningsAgent's
    `trades.profile` is `NOT NULL DEFAULT 'default'`, so it passes `untagged="default"`.
    """
    if value is None:
        return untagged
    text = str(value).strip()
    return text or untagged


def compare_profiles(rows, *, tag_key: str, summarize, untagged: str = UNTAGGED) -> dict:
    """Group profile-tagged trade rows by their attribution tag and summarize each group.

    The calibration harness's comparison engine (plan Part 10 Phase C). It consolidates the
    *orchestration* both modules hand-roll — MEICAgent's `cmd_get_range_summary` groups
    `ic_trades` by `risk_profile` then calls `_range_stats_for_rows` per group; EarningsAgent's
    `cmd_get_pnl_summary` groups `trades` by `profile` then aggregates per group — while leaving
    the metric math injected via `summarize`, because it is deliberately domain-divergent (MEIC
    annualizes Sharpe on a daily return series; Earnings does not, on discrete event trades).

    - `rows`: iterable of mappings (a module's trade rows; sqlite3.Row or dict). Each must carry
      `tag_key`. The caller filters (e.g. to closed trades) before passing them in.
    - `tag_key`: column naming the profile tag — `"risk_profile"` (MEIC) or `"profile"` (Earnings).
    - `summarize`: `callable(list_of_rows_for_one_profile) -> value` (any JSON-able summary); the
      module's own metric bundle. Called once per profile group, never on the whole set.
    - `untagged`: sentinel for rows with no profile tag, applied via `attribution_tag` (MEIC uses
      the `"unassigned"` default; Earnings passes `"default"` to match its non-null column).

    Returns `{profile_tag: summarize(group)}`, groups in first-seen row order (deterministic and
    behaviour-preserving for the callers being consolidated). Empty `rows` -> `{}`.
    """
    groups: dict[str, list] = {}
    for r in rows:
        tag = attribution_tag(r[tag_key], untagged=untagged)
        groups.setdefault(tag, []).append(r)
    return {tag: summarize(group) for tag, group in groups.items()}


# The fields an arm-identity collision is judged on (2026-08-14). Deliberately the metrics that
# summarize a whole book rather than raw per-trade rows -- two arms sharing these by coincidence
# across more than a couple of trades is far less likely than two arms sharing the same underlying
# trades under different tags, or a config mistake that never differentiated them.
IDENTITY_FIELDS = ("sample", "win_rate", "days", "net_pnl", "sharpe", "max_drawdown")


def find_identical_readings(
    readings: Mapping[str, Mapping], *, fields: tuple = IDENTITY_FIELDS
) -> list[dict]:
    """Detect tags whose reading is identical across `fields` — a config-collision check, not a
    comparison. Two arms that read byte-identical are either the same book trading under two
    names, or a config mistake that never actually differentiated them; either way, a reader
    (human or model) comparing them concludes there is independent evidence where there is none.

    Found live 2026-08-14: meic's `gex-open`/`gex-blocked` and `small-xsp`/
    `explore-xsp-loosecredit` read identical in every field despite naming opposite/different gate
    conditions — this is the suite-level fix for that, since a reporting-layer defect like it can
    recur in any module, not just the one it was first noticed in.

    Pure and additive: does not mutate `readings`, does not change what `compare_profiles`,
    `qualify_readings` or `recommend_champion` return — callers decide what to do with a
    collision (the fact pack surfaces it as a warning; nothing here forces a merge).

    A tag whose reading carries `None` on any of `fields` is never grouped with anything — an
    unmeasured value cannot certify two readings are the same, so two zero-sample arms (which
    read `None` for `win_rate`/`sharpe`) are correctly never reported as colliding.

    Returns one entry per collision group of size >= 2, in first-seen tag order:
    `[{"tags": [...], "fields": {name: value, ...}}]`. Empty list when nothing collides.
    """
    seen: dict[tuple, list[str]] = {}
    order: list[tuple] = []
    for tag, reading in readings.items():
        if not reading:
            continue
        key = tuple(reading.get(f) for f in fields)
        if any(v is None for v in key):
            continue
        if key not in seen:
            seen[key] = []
            order.append(key)
        seen[key].append(tag)
    return [
        {"tags": seen[key], "fields": dict(zip(fields, key, strict=True))}
        for key in order
        if len(seen[key]) >= 2
    ]


# The qualification bar a challenger's reading must clear before its metric is even compared to the
# champion's (was PROMOTION_RULE — a rung-graduation bar; renamed because these thresholds now gate
# entry into a COMPARISON, they don't by themselves promote anything anywhere). Overridable per call.
QUALIFICATION_RULE = {"min_days": 14, "min_win_rate": 0.60, "min_sample": 20}


def _check(value, threshold) -> dict:
    return {"value": value, "threshold": threshold, "pass": value is not None and value >= threshold}


def _qualify_one(reading: Mapping, thresholds: Mapping) -> dict:
    """The threshold checks shared by `recommend_champion` and `qualify_readings` — factored out so
    the two public functions cannot drift on what "qualified" means. Same three base checks as the
    old `recommend_promotion` (sample/win_rate/days) plus the three opt-in hardened checks:

    - `min_net_pnl` — net-of-cost P&L must clear the bar. Opt in with 0.0 to mean "an arm that lost
      money does not qualify, whatever its win rate." The base three cannot see that case at all,
      and the failure is not hypothetical: on 2026-08-14 flies' control, gex and time_window all
      read `qualified: true` while lifetime-negative (-1,698.61 / -1,697.94 / -87.69), because a
      butterfly book wins often and loses big — precisely the shape a win-rate gate is blind to.
      A threshold rather than a hardcoded `> 0` so a module can demand a real margin instead of
      break-even; note `_check` is `>=`, so 0.0 admits an exactly-flat book.
    - `min_return_on_capital` — net P&L as a fraction of capital at risk must clear the bar. A
      reading whose records carry no capital reads None and FAILS: unknown capital cannot certify a
      capital-efficiency threshold.
    - `require_slippage_survival` — the reading must stay profitable with the modeled slippage
      fraction DOUBLED (`net_pnl_2x_slippage > 0`), and the recorded slippage must cover the whole
      sample: a stress test over part of the evidence certifies nothing.

    The last two are deliberately un-satisfiable by an uninstrumented module — a book with no
    recorded slippage or capital cannot pass them at any threshold. That is the intended reading:
    they certify a measurement, so switching them on for a module states "nothing here may qualify
    until it is measured", not "these are nice to have."

    Returns `{"qualified": bool, "checks": {name: {value, threshold, pass}}}`.
    """
    checks = {
        "sample": _check(reading.get("sample"), thresholds["min_sample"]),
        "win_rate": _check(reading.get("win_rate"), thresholds["min_win_rate"]),
        "days": _check(reading.get("days"), thresholds["min_days"]),
    }
    if "min_net_pnl" in thresholds:
        checks["net_pnl"] = _check(reading.get("net_pnl"), thresholds["min_net_pnl"])
    if "min_return_on_capital" in thresholds:
        checks["return_on_capital"] = _check(
            reading.get("return_on_capital"), thresholds["min_return_on_capital"]
        )
    if thresholds.get("require_slippage_survival"):
        stressed = reading.get("net_pnl_2x_slippage")
        full_coverage = (reading.get("slippage_coverage") or 0) >= (reading.get("sample") or 0) > 0
        checks["slippage_survival"] = {
            "value": stressed,
            "threshold": "net > 0 at 2x slippage over the full sample",
            "pass": bool(full_coverage and stressed is not None and stressed > 0),
        }
    return {"qualified": all(c["pass"] for c in checks.values()), "checks": checks}


def qualify_readings(readings: Mapping[str, Mapping], *, rule: Mapping | None = None) -> dict:
    """Per-tag qualification only — no champion, no comparison, no promotion verdict.

    For a module whose tags are parallel, unordered experiments (flies' control/gex/time_window/
    width-N arms; earnings' profiles) rather than a risk sequence with one currently-live reference
    to compare against. Applies the exact same threshold checks `recommend_champion` uses to every
    tag independently. `calibrate.py` selects this function over `recommend_champion` based on
    whether a module's config declares a `champion` — this function doesn't know about that key,
    callers choose it.

    Returns `{tag: {"qualified": bool, "checks": {...}}}` — deliberately no `recommendation`, no
    `eligible`, no cross-tag comparison anywhere in the shape. That absence is itself the fix for the
    bug this replaces: a fully-qualifying reading fed through the old ladder model returned
    `"graduate:<next>"` even when "next" was an unrelated parallel arm; fed through this function it
    cannot produce anything resembling a promotion, because the shape has nowhere to put one.
    """
    thresholds = {**QUALIFICATION_RULE, **(rule or {})}
    return {tag: _qualify_one(reading, thresholds) for tag, reading in readings.items()}










def merge_profile(
    base: Mapping,
    profile_def: Mapping,
    *,
    reserved_keys: tuple = (),
    nested_namespaces: Mapping | None = None,
    validate: bool = False,
) -> dict:
    """Merge a profile's overrides onto `base`, returning a NEW config (base is not mutated).

    - Top-level keys in `profile_def` partially override `base`, EXCEPT: keys starting with `_`
      (comments) and keys in `reserved_keys` (e.g. `"description"`) are skipped, and keys named in
      `nested_namespaces` are handled as deep-merges (below) rather than top-level overrides.
    - `nested_namespaces`: `{profile_key: base_key}`. For each, `profile_def[profile_key]` is a
      `{entry_name: overrides}` map; each `overrides` dict is shallow-merged onto
      `base[base_key][entry_name]` (entries absent from base are skipped). This is EarningsAgent's
      `strategy_overrides -> strategies` merge; MEICAgent passes none (flat overlay).
    - `validate=True` raises KeyError for any top-level override key not already present in `base`
      (fail-closed typo guard); `_`/reserved/namespace keys are exempt.

    Generalizes MEICAgent's `_merged_params` (no namespaces) and the profile step of EarningsAgent's
    `_load_config` (with `strategy_overrides`).
    """
    nested_namespaces = dict(nested_namespaces or {})
    reserved = set(reserved_keys) | set(nested_namespaces)
    result = dict(base)

    for key, value in profile_def.items():
        if key.startswith("_") or key in reserved:
            continue
        if validate and key not in base:
            raise KeyError(f"profile key '{key}' not in base config (fail-closed validation)")
        result[key] = value

    for profile_key, base_key in nested_namespaces.items():
        overrides_map = profile_def.get(profile_key) or {}
        if not overrides_map:
            continue
        target = dict(result.get(base_key) or {})  # copy so `base` is not mutated
        for entry_name, overrides in overrides_map.items():
            if entry_name in target:
                target[entry_name] = {**target[entry_name], **overrides}
        result[base_key] = target

    return result
