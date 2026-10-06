"""The guard mutants, and the pytest plugin that applies one -- in memory, never on disk.

A guard is only worth its failures ("A guard has to be shown to fail", the root CLAUDE.md). Each
entry below breaks one guard the way a regression would, and names the test that must then FAIL.
`scripts/guard_mutants.py` runs those tests once clean (they must pass) and once per mutant (they
must not). The source is never edited: the plugin patches a module attribute when pytest starts,
inside that one test process, so a mutant cannot leak into the checkout or another run.

Loaded with `-p guard_mutants_plugin` and the mutant's id in CHERRYPICK_GUARD_MUTANT. Without that
variable it does nothing.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass

ENV = "CHERRYPICK_GUARD_MUTANT"


@dataclass(frozen=True)
class Mutant:
    id: str
    breaks: str  # what the mutant does, in the words a failure report should use
    package: str  # packages/<package>, where pytest runs (its own conftest and config)
    tests: tuple[str, ...]  # node ids, relative to the package, that must fail under the mutant
    module: str
    attr: str
    replacement: str  # a key into REPLACEMENTS


def _always_true(*_args, **_kwargs):
    return True


def _whole_day(_session):
    return float("-inf"), float("inf")


def _all_window_events(_self, _window_key):
    from cherrypick.core.streamrequests import WINDOW_EVENTS

    return WINDOW_EVENTS


def _devnull_stderr(_self, _spec, _st):
    import subprocess

    return subprocess.DEVNULL


def _nothing(*_args, **_kwargs):
    return None


def _always_false(*_args, **_kwargs):
    return False


def _gone_at_once(_self, _spec, _st):
    return True, 0


def _no_groups(*_args, **_kwargs):
    return {}


def _unguarded(_log, fn, *args, **kwargs):
    return fn(*args, **kwargs)


def _features_read_ahead():
    """The real `feature_series`, except each session's close extremity is the NEXT session's: the
    one-bar look-ahead the range-features truncation guard exists to catch."""
    original = importlib.import_module("cherrypick.core.rangefeatures").feature_series

    def patched(bars):
        out = original(bars)
        for i in range(len(out) - 1):
            out[i]["close_extremity"] = out[i + 1]["close_extremity"]
        return out

    return patched


def _decide_after_window():
    """contango's `record_misses` replaced by a full decision pass: the late fill after the decision
    window that the after-window rule exists to forbid."""
    loop = importlib.import_module("cherrypick.contango.paper_loop")

    def patched(config, conn, *, cache_path, day):
        return loop.run_decisions(config, conn, cache_path=cache_path, day=day, final=True)

    return patched


def _ignore_as_of(rows, _as_of):
    """flies' intraday pack reading every row regardless of its instant: the look-ahead the
    truncation test exists to catch."""
    return list(rows)


def _uncapped(chosen, _ceiling):
    """flies' live agent mode taking the day's choice whatever config allows."""
    return chosen


def _agent_fails_open(config, arm, _day, _now):
    """flies' intraday-agent arm dropping its gate when there is no fresh decision, rather than
    falling back to the fixed rule."""
    arms = dict(config.get("arms") or {})
    arms[arm] = {**(arms.get(arm) or {}), "refuse_completion_against_trend": False}
    return {**config, "arms": arms}, None


def _no_t(_df):
    """flies' agent qualification judging a paired mean with no allowance for its noise."""
    return 0.0


def _unscored_passes(cid, mode, label, value, threshold, passed):
    """flies' agent qualification counting a criterion it cannot score yet as passed."""
    return {
        "id": cid,
        "mode": mode,
        "label": label,
        "value": value,
        "threshold": threshold,
        "pass": True if passed is None else passed,
    }


def _shadow_acts(params, acfg, arm_record, session, now):
    """flies' live agent acting on its decision in shadow mode, which only records."""
    from cherrypick.flies import intraday_advice

    mode = intraday_advice.live_mode_today(acfg, arm_record, session)
    decision = intraday_advice.read_decision("live", session=session, now=now) if mode != "off" else None
    if decision is not None:
        params = {**params, "refuse_completion_against_trend": decision["trend_gate"] == "on"}
    return params, {"mode": mode, "decision": decision}


def _every_mode_offered(_cfg, _qualification):
    """flies' live selection offering every agent mode whatever the evidence says."""
    return ["off", "shadow", "gates", "gates_and_closures"]


def _identity(mode):
    """flies' live agent treating a mode with no live implementation as if it had one."""
    return mode


def _gate_sees_ahead(decisions, at, ttl_seconds):
    """flies' replay letting an entry follow a decision made AFTER it (look-ahead)."""
    near = [d for d in decisions if abs(d["as_of"] - at) <= ttl_seconds]
    return max(near, key=lambda d: d["as_of"])["trend_gate"] if near else None


def _replay_overwrites(values, decided, replay):
    """flies' qualification letting a replayed session replace a forward one."""
    taken = []
    for day, s in sorted(((replay or {}).get("sessions") or {}).items()):
        for k in ("control", "rule", "agent"):
            values[k][day] = s[k]
        if s.get("decided"):
            decided.add(day)
        taken.append(day)
    return taken


def _always_filled(pack):
    """flies' pack completeness calling every block recorded, so thin packs pool with full ones."""
    return {k: "filled" for k in pack if not str(k).startswith("_") and k not in ("pack_version", "now")}


REPLACEMENTS = {
    "always_true": lambda: _always_true,
    "whole_day": lambda: _whole_day,
    "never_a_leftover": lambda: 10**12,
    "all_window_events": lambda: _all_window_events,
    "devnull_stderr": lambda: _devnull_stderr,
    "nothing": lambda: _nothing,
    "always_false": lambda: _always_false,
    "gone_at_once": lambda: _gone_at_once,
    "no_groups": lambda: _no_groups,
    "unguarded": lambda: _unguarded,
    "features_read_ahead": _features_read_ahead,
    "decide_after_window": _decide_after_window,
    "ignore_as_of": lambda: _ignore_as_of,
    "uncapped": lambda: _uncapped,
    "agent_fails_open": lambda: _agent_fails_open,
    "no_t": lambda: _no_t,
    "unscored_passes": lambda: _unscored_passes,
    "shadow_acts": lambda: _shadow_acts,
    "every_mode_offered": lambda: _every_mode_offered,
    "identity": lambda: _identity,
    "gate_sees_ahead": lambda: _gate_sees_ahead,
    "replay_overwrites": lambda: _replay_overwrites,
    "always_filled": lambda: _always_filled,
}


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        id="gex-recorder-rth-gate",
        breaks="the GEX recorder writes regime rows outside RTH",
        package="gex",
        tests=("tests/test_service.py::test_record_regimes_writes_nothing_outside_rth",),
        module="cherrypick.gex.regime",
        attr="in_rth",
        replacement="always_true",
    ),
    Mutant(
        id="gex-leftover-cut",
        breaks="the GEX provider sums strikes the window re-centred away from",
        package="gex",
        tests=("tests/test_service.py::test_a_leftover_strike_is_not_summed_into_the_profile",),
        module="cherrypick.gex.provider",
        attr="LEFTOVER_ROW_SECONDS",
        replacement="never_a_leftover",
    ),
    Mutant(
        id="regime-join-rth",
        breaks="core.regime joins an off-hours GEX row as the regime",
        package="core",
        tests=("tests/test_regime.py::test_an_off_hours_gex_row_is_never_the_regime",),
        module="cherrypick.core.clock",
        attr="in_rth",
        replacement="always_true",
    ),
    Mutant(
        id="overview-levels-rth",
        breaks="the overview's pre-open levels read an off-hours GEX row",
        package="overview",
        tests=(
            "tests/test_facts.py::test_gex_levels_skip_off_hours_rows_for_the_prior_sessions_last_rth_reading",
        ),
        module="cherrypick.core.clock",
        attr="in_rth",
        replacement="always_true",
    ),
    Mutant(
        id="advisor-open-walls-rth",
        breaks="the advisor reads a midnight row as the session's open walls",
        package="advisor",
        tests=("tests/test_factpack.py::test_flies_band_containment_separates_breach_from_containment",),
        module="cherrypick.core.clock",
        attr="rth_bounds",
        replacement="whole_day",
    ),
    Mutant(
        id="producer-window-events",
        breaks="the producer subscribes every event on a window whose symbol declared fewer",
        package="core",
        tests=("tests/test_streamer_expirations.py::test_a_declared_event_set_is_all_a_window_subscribes",),
        module="cherrypick.core.streamer",
        attr="ChainStreamer._window_events",
        replacement="all_window_events",
    ),
    Mutant(
        id="producer-nearest-window",
        breaks="the producer builds a nearest window every declarer of the symbol declined",
        package="core",
        tests=(
            "tests/test_streamer_expirations.py::"
            "test_a_declined_nearest_window_is_not_subscribed_but_requested_dates_are",
        ),
        module="cherrypick.core.streamer",
        attr="ChainStreamer._nearest_window",
        replacement="always_true",
    ),
    Mutant(
        id="supervisor-stderr-capture",
        breaks="the supervisor discards its children's stderr again",
        package="orchestrator",
        tests=(
            "tests/test_supervisor_stderr.py::test_a_childs_stderr_goes_to_its_own_file_with_a_launch_header",
        ),
        module="cherrypick.orchestrator.supervisor",
        attr="Supervisor._open_stderr",
        replacement="devnull_stderr",
    ),
    Mutant(
        id="supervisor-restart-request",
        breaks="a requested restart is counted as a crash",
        package="orchestrator",
        tests=("tests/test_supervisor_stderr.py::test_a_requested_restart_is_not_a_failure",),
        module="cherrypick.orchestrator.supervisor",
        attr="consume_restart_request",
        replacement="nothing",
    ),
    Mutant(
        id="supervisor-overlap-guard",
        breaks="the supervisor launches a job whose previous run is still alive",
        package="orchestrator",
        tests=("tests/test_supervisor.py::test_overlap_guard_never_double_fires",),
        module="cherrypick.orchestrator.supervisor",
        attr="Supervisor._job_running",
        replacement="always_false",
    ),
    Mutant(
        id="supervisor-wait-before-relaunch",
        breaks="a restart relaunches before the old process is gone",
        package="orchestrator",
        tests=("tests/test_holds_and_restarts.py::test_a_child_that_will_not_die_is_not_replaced",),
        module="cherrypick.orchestrator.supervisor",
        attr="Supervisor._stop_child",
        replacement="gone_at_once",
    ),
    Mutant(
        id="watchdog-duplicate-processes",
        breaks="the watchdog no longer sees two copies of a job",
        package="orchestrator",
        tests=("tests/test_duplicate_processes.py::test_two_copies_of_a_job_are_found",),
        module="cherrypick.orchestrator.watchdog",
        attr="_duplicate_groups",
        replacement="no_groups",
    ),
    Mutant(
        id="looplock-live-holder",
        breaks="a loop lock is taken from a holder that is still running",
        package="earnings",
        tests=("tests/test_paper_loop.py::test_a_live_holder_is_never_stolen_however_old_its_lock",),
        module="cherrypick.core.looplock",
        attr="pid_alive",
        replacement="always_false",
    ),
    Mutant(
        id="flies-fill-telemetry-guard",
        breaks="a fill-telemetry failure aborts a live fill confirmation",
        package="flies",
        tests=("tests/test_fill_facts.py::test_a_telemetry_failure_never_stops_a_fill_being_confirmed",),
        module="cherrypick.flies.live_loop",
        attr="_telemetry",
        replacement="unguarded",
    ),
    Mutant(
        id="rangefeatures-lookahead",
        breaks="a range feature reads the session after its own (docs/range-features.md)",
        package="core",
        tests=("tests/test_rangefeatures.py::test_features_are_truncation_invariant",),
        module="cherrypick.core.rangefeatures",
        attr="feature_series",
        replacement="features_read_ahead",
    ),
    Mutant(
        id="contango-no-fill-after-window",
        breaks="contango switches an arm after its decision window closed",
        package="contango",
        tests=(
            "tests/test_paper_loop.py::test_after_the_window_an_undecided_arm_is_missed_and_never_trades",
        ),
        module="cherrypick.contango.paper_loop",
        attr="record_misses",
        replacement="decide_after_window",
    ),
    Mutant(
        id="flies-intraday-pack-lookahead",
        breaks="the intraday agent's fact pack reads readings recorded after its own instant",
        package="flies",
        tests=(
            "tests/test_intraday_pack.py::test_the_pack_is_identical_with_everything_after_its_instant_deleted",
        ),
        module="cherrypick.flies.intraday_pack",
        attr="_at_or_before",
        replacement="ignore_as_of",
    ),
    Mutant(
        id="flies-intraday-live-mode-cap",
        breaks="the agent's live mode exceeds what config's live_mode_max allows",
        package="flies",
        tests=("tests/test_intraday_advice.py::test_the_live_mode_is_the_days_choice_capped_by_config",),
        module="cherrypick.flies.intraday_advice",
        attr="_capped",
        replacement="uncapped",
    ),
    Mutant(
        id="flies-intraday-agent-falls-back",
        breaks="the intraday-agent arm runs ungated when the agent has no fresh decision",
        package="flies",
        tests=(
            "tests/test_intraday_arms.py::test_the_agent_arms_gate_follows_a_fresh_decision_and_falls_back_to_the_rule",
        ),
        module="cherrypick.flies.paper_loop",
        attr="tick_config",
        replacement="agent_fails_open",
    ),
    Mutant(
        id="flies-agent-eval-t-bound",
        breaks="the agent qualifies on a paired mean with no allowance for its noise",
        package="flies",
        tests=("tests/test_intraday_eval.py::test_a_noisy_positive_mean_does_not_pass_the_one_sided_bound",),
        module="cherrypick.flies.intraday_eval",
        attr="t95",
        replacement="no_t",
    ),
    Mutant(
        id="flies-agent-eval-unscored-locks",
        breaks="a live agent mode unlocks on a criterion the suite cannot score yet",
        package="flies",
        tests=(
            "tests/test_intraday_eval.py::test_nothing_past_shadow_unlocks_while_a_criterion_cannot_be_scored",
        ),
        module="cherrypick.flies.intraday_eval",
        attr="_criterion",
        replacement="unscored_passes",
    ),
    Mutant(
        id="flies-live-agent-shadow-only-records",
        breaks="the live agent acts on its decision on a shadow day",
        package="flies",
        tests=(
            "tests/test_intraday_live.py::test_shadow_reads_the_decision_and_never_changes_a_param",
            "tests/test_live_scaffold.py::test_the_live_shadow_enters_as_always_and_stamps_what_the_agents_gate_would_have_done",
        ),
        module="cherrypick.flies.intraday_advice",
        attr="live_tick",
        replacement="shadow_acts",
    ),
    Mutant(
        id="flies-live-agent-mode-offered",
        breaks="/live-flies-start can choose an agent mode the evidence does not offer",
        package="flies",
        tests=(
            "tests/test_intraday_live.py::test_a_mode_the_evidence_does_not_offer_is_refused_and_nothing_is_written",
        ),
        module="cherrypick.flies.intraday_eval",
        attr="offered_live_modes",
        replacement="every_mode_offered",
    ),
    Mutant(
        id="flies-live-closes-unbuilt",
        breaks="a live agent mode with no live implementation (closes) is offered or put in force",
        package="flies",
        tests=(
            "tests/test_intraday_live.py::test_offered_modes_are_the_evidence_narrowed_by_config_and_by_what_live_can_do",
            "tests/test_intraday_live.py::test_config_caps_the_days_choice_and_an_unbuilt_mode_runs_as_gates",
        ),
        module="cherrypick.flies.intraday_advice",
        attr="built_mode",
        replacement="identity",
    ),
    Mutant(
        id="flies-replay-decision-lookahead",
        breaks="the agent replay lets an entry follow a decision made after it",
        package="flies",
        tests=(
            "tests/test_intraday_replay.py::test_a_decision_counts_only_after_it_was_made_and_while_it_is_fresh",
        ),
        module="cherrypick.flies.intraday_replay",
        attr="gate_at",
        replacement="gate_sees_ahead",
    ),
    Mutant(
        id="flies-replay-keeps-forward",
        breaks="a replayed session overwrites a forward one in the agent's qualification",
        package="flies",
        tests=("tests/test_intraday_replay.py::test_the_replay_never_overwrites_a_forward_session",),
        module="cherrypick.flies.intraday_eval",
        attr="merge_replay",
        replacement="replay_overwrites",
    ),
    Mutant(
        id="flies-pack-completeness",
        breaks="a pack missing recorded blocks is reported as complete",
        package="flies",
        tests=("tests/test_intraday_pack.py::test_completeness_judges_recorded_fields_not_facts_about_the_session",),
        module="cherrypick.flies.intraday_pack",
        attr="completeness",
        replacement="always_filled",
    ),
)

BY_ID = {m.id: m for m in MUTANTS}


def resolve(mutant: Mutant):
    """(owner, attribute name) for a mutant's target; raises if it no longer exists, so a rename
    turns into a loud error rather than a mutant quietly patching nothing."""
    owner = importlib.import_module(mutant.module)
    *path, name = mutant.attr.split(".")
    for part in path:
        owner = getattr(owner, part)
    if not hasattr(owner, name):
        raise AttributeError(f"{mutant.module}.{mutant.attr} no longer exists")
    return owner, name


def pytest_configure(config):  # noqa: ARG001 -- pytest hook signature
    mutant_id = os.environ.get(ENV)
    if not mutant_id:
        return
    mutant = BY_ID[mutant_id]
    owner, name = resolve(mutant)
    setattr(owner, name, REPLACEMENTS[mutant.replacement]())
