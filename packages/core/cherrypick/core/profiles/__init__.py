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


def recommend_champion(
    readings: Mapping[str, Mapping],
    champion: str | None,
    *,
    rule: Mapping | None = None,
    deliberate_only=(),
    margin: float = 0.0,
) -> dict:
    """Advisory-only: does any challenger beat the current champion? (replaces the fixed-ladder
    `recommend_promotion` — plan Part 10 Phase D, champion/challenger revision, 2026-08-01)

    The ladder model assumed risk profiles form one conservative-to-aggressive sequence and a
    profile only ever "graduates" to its fixed next rung. That is the wrong shape for the actual
    question — *which currently-tested configuration has earned the right to replace what's live* —
    which is a selection among competitors, not an ordinal climb. This function compares the tag
    that is currently live (`champion`) against every OTHER tag in `readings` (`challengers`), not
    just an adjacent one, so a challenger can win outright without first "graduating through"
    whatever used to sit between it and the champion in a list. It NEVER mutates config or switches
    the live profile — auto-promoting live risk from paper results is a capital-authority action,
    kept human-gated (consistent with the governor/watchdog fail-closed philosophy); the
    caller/human applies the recommendation.

    - `readings`: `{tag: reading}` for every tag observed this epoch — typically `compare_profiles`'s
      return value directly. The WHOLE table, not one profile's reading, because every non-champion
      tag is a candidate challenger. A tag absent from `readings` (no closed trades yet) is simply
      absent from `challengers`, not an error.
    - `champion`: the tag currently live for this module. Champions with no reading at all
      (`champion not in readings` — brand new, zero closed trades) degrade gracefully: every
      qualified, non-`deliberate_only` challenger trivially beats a champion with nothing to lose to.
      The champion's OWN reading is never required to itself clear `QUALIFICATION_RULE` — it is
      already live; requiring it to re-qualify every reading period would make a freshly-promoted
      champion briefly unbeatable by construction, re-introducing the rigidity this replaces.
    - `rule`: threshold overrides merged onto `QUALIFICATION_RULE`.
    - `deliberate_only`: tags never auto-recommended even when they qualify and beat the champion
      outright (a human opts in explicitly) — e.g. an experimental arm allowed to run and be
      measured, but never silently promoted to champion.
    - `margin`: how much a challenger's ranking metric must exceed the champion's before it "beats"
      the champion (default 0.0 — any positive edge counts).

    Ranking metric, decided independently per champion/challenger pairing: `return_on_capital` when
    BOTH sides carry a non-None value, else `net_pnl` (see `cherrypick.core.metrics.
    calibration_reading`'s own docstring — "a 2-wide and a 10-wide IC must not weigh equally"; net
    P&L alone rewards bigger size, not better risk-adjusted return). Never forces a missing value to
    0 — that would be exactly the "misleadingly precise zero" `metrics.py` warns against.

    Returns `{champion, champion_metric, challengers, eligible, recommendation, reason}`.
    `challengers` maps every non-champion tag in `readings` to `{qualified, checks, metric,
    beats_champion, deliberate_only}` — `checks` is the same `{name: {value, threshold, pass}}` shape
    the old ladder model used, unchanged. `eligible` is True iff at least one qualified,
    non-`deliberate_only` challenger beats the champion by >= `margin`; `recommendation` is
    `"retain:<champion>"` or `"champion:<tag>"` for the single best such challenger (highest metric
    value; first-seen order — matching `compare_profiles`'s own determinism — breaks an exact tie). A
    challenger's own `beats_champion` is always reported even when it isn't the overall winner or is
    vetoed by `deliberate_only`, so a caller can distinguish "qualified, ahead, but never
    auto-recommended" from "qualified, not yet ahead."
    """
    thresholds = {**QUALIFICATION_RULE, **(rule or {})}
    champion_reading = readings.get(champion) if champion is not None else None
    champion_metric = _metric(champion_reading) if champion_reading is not None else None

    challengers: dict[str, dict] = {}
    for tag, reading in readings.items():
        if tag == champion:
            continue
        q = _qualify_one(reading, thresholds)
        metric = _metric(reading)
        beats = _beats(metric, champion_metric, margin)
        challengers[tag] = {
            "qualified": q["qualified"],
            "checks": q["checks"],
            "metric": metric,
            "beats_champion": bool(q["qualified"] and beats),
            "deliberate_only": tag in deliberate_only,
        }

    winners = {tag: c for tag, c in challengers.items() if c["beats_champion"] and not c["deliberate_only"]}
    if winners:
        best = max(winners, key=lambda t: winners[t]["metric"]["value"])
        eligible, recommendation = True, f"champion:{best}"
        c = winners[best]
        reason = (
            f"{best} qualified and beats champion {champion} on {c['metric']['name']} "
            f"({_fmt_metric(c['metric'])} vs {_fmt_metric(champion_metric)}); recommending promotion."
        )
    else:
        eligible, recommendation = False, f"retain:{champion}"
        reason = f"no qualified, non-deliberate-only challenger beats champion {champion}; retaining."

    return {
        "champion": champion,
        "champion_metric": champion_metric,
        "challengers": challengers,
        "eligible": eligible,
        "recommendation": recommendation,
        "reason": reason,
    }


def _metric(reading: Mapping) -> dict:
    """`{"name": "return_on_capital" | "net_pnl", "value": float}` for one reading. Prefers
    return_on_capital (never forced from a missing value); net_pnl is always numeric on a
    `calibration_reading`, so this never returns a None value."""
    roc = reading.get("return_on_capital")
    if roc is not None:
        return {"name": "return_on_capital", "value": roc}
    return {"name": "net_pnl", "value": reading.get("net_pnl", 0.0)}


def _beats(challenger_metric: dict, champion_metric: dict | None, margin: float) -> bool:
    """Does the challenger's metric exceed the champion's by at least `margin`? A champion with no
    metric at all (no reading yet) has nothing to lose to -- any challenger trivially beats it."""
    if champion_metric is None:
        return True
    return challenger_metric["value"] - champion_metric["value"] >= margin


def _fmt_metric(metric: dict | None) -> str:
    if metric is None:
        return "n/a (no champion reading)"
    value = metric["value"]
    return f"{value * 100:.1f}%" if metric["name"] == "return_on_capital" else f"{value:.2f}"


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
