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
