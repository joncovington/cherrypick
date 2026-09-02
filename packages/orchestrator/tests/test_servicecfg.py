"""Tests for stale-config detection and service recycling.

The properties that carry the design, asserted rather than left to prose:
  - a running-but-stale service is recycled, which no liveness check can catch,
  - an unstamped service is adopted, never restarted on sight,
  - an unreadable config is an unknown, not a change,
  - the stamp only advances when the restart actually happened.

Nothing here starts a process or touches a network.
"""

import json

import pytest

import cherrypick.orchestrator.servicecfg as sc
import cherrypick.orchestrator.watchdog as wd
from cherrypick.orchestrator import config as cfgmod

pytestmark = pytest.mark.unit


SVC = {
    "id": "gex-recorder",
    "enabled": True,
    "path": "../gex",
    "status_argv": ["run.py", "record", "--status"],
    "start_argv": ["run.py", "record"],
    "stop_argv": ["run.py", "record", "--stop"],
    "auto_restart": True,
}


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """State in a temp dir, plus a service checkout with its own in-repo config."""
    monkeypatch.setattr(cfgmod, "STATE_DIR", tmp_path / "state", raising=False)
    monkeypatch.setattr(cfgmod, "state_file", lambda name: tmp_path / "state" / name)
    monkeypatch.setattr(cfgmod, "ensure_dirs", lambda: None)
    # No home config for these names, so the in-repo candidate is the one that wins.
    monkeypatch.setattr(sc._home, "config_path", lambda name=None: tmp_path / "nonexistent" / f"{name}.json")

    root = tmp_path / "gex"
    (root / "config").mkdir(parents=True)
    (root / "config" / "config.json").write_text(
        json.dumps({"source": {"stream_cache_db": "../meic/cache.db"}}), encoding="utf-8"
    )
    return root


def _write_service_config(root, payload):
    (root / "config" / "config.json").write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------- hashing
def test_hash_moves_when_the_services_own_config_changes(wired):
    """The 2026-07-23 case: the daemon's own config moved off the retired cache and nothing noticed."""
    before, source = sc.effective_config(SVC, wired)
    _write_service_config(wired, {"source": {"stream_cache_db": "../streamer/cache.db"}})
    after, _ = sc.effective_config(SVC, wired)
    assert before != after
    assert source.endswith("config.json")


def test_hash_moves_when_the_launch_command_changes(wired):
    """A different argv describes a different process than the one currently running."""
    before, _ = sc.effective_config(SVC, wired)
    after, _ = sc.effective_config({**SVC, "start_argv": ["run.py", "record", "--fast"]}, wired)
    assert before != after


def test_comment_keys_do_not_move_the_hash(wired):
    """config.example.json documents every block with `_comment`. Editing the prose must not recycle
    a daemon."""
    before, _ = sc.effective_config(SVC, wired)
    after, _ = sc.effective_config({**SVC, "_comment": "reworded entirely"}, wired)
    assert before == after


def test_unreadable_config_yields_no_hash(wired, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("permission denied")

    monkeypatch.setattr(sc.Path, "read_bytes", boom)
    assert sc.effective_config(SVC, wired) == (None, None)


def test_a_service_with_no_config_file_still_hashes_its_entry(tmp_path, monkeypatch):
    """Configured entirely by argv is a legitimate shape — it should still catch an argv change."""
    monkeypatch.setattr(sc._home, "config_path", lambda name=None: tmp_path / "none" / f"{name}.json")
    root = tmp_path / "bare"
    root.mkdir()
    digest, source = sc.effective_config(SVC, root)
    assert digest and source is None


# --------------------------------------------------------------------------- staleness
def test_unstamped_service_is_adopted_not_restarted(wired):
    """No stamp means no evidence of staleness. Guessing would restart every running service once,
    on the first tick after this ships."""
    state = sc.staleness(SVC, wired)
    assert state["adopt"] is True and state["stale"] is False


def test_stale_only_when_the_stamp_and_the_config_disagree(wired):
    digest, source = sc.effective_config(SVC, wired)
    sc.write_stamp(SVC["id"], digest, source)
    assert sc.staleness(SVC, wired)["stale"] is False  # unchanged

    _write_service_config(wired, {"source": {"stream_cache_db": "../streamer/cache.db"}})
    state = sc.staleness(SVC, wired)
    assert state["stale"] is True and state["stamped"] == digest


def test_unreadable_config_is_not_treated_as_a_change(wired, monkeypatch):
    """A transient read error must not recycle a healthy process."""
    digest, source = sc.effective_config(SVC, wired)
    sc.write_stamp(SVC["id"], digest, source)
    monkeypatch.setattr(sc.Path, "read_bytes", lambda *_a, **_k: (_ for _ in ()).throw(OSError("gone")))
    state = sc.staleness(SVC, wired)
    assert state["stale"] is False and state["adopt"] is False


def test_clear_stamp_is_idempotent(wired):
    sc.clear_stamp(SVC["id"])  # never stamped
    sc.write_stamp(SVC["id"], "abc123", "somewhere")
    sc.clear_stamp(SVC["id"])
    assert sc.read_stamp(SVC["id"]) == {}


# --------------------------------------------------------------------------- the watchdog wiring
@pytest.fixture
def spy(monkeypatch):
    """Capture stop/start instead of spawning anything."""
    calls = {"stop": 0, "start": 0}

    def bump(key):
        calls[key] += 1
        return True

    monkeypatch.setattr(wd, "_stop_streamer", lambda root, spec: bump("stop"))
    monkeypatch.setattr(wd, "_start_streamer", lambda root, argv: bump("start"))
    return calls


def _go_stale(root):
    _write_service_config(root, {"source": {"stream_cache_db": "../streamer/cache.db"}})


def test_running_but_stale_service_is_recycled(wired, spy):
    """The whole point: the process is up, healthy, and answering --status truthfully — and wrong."""
    digest, source = sc.effective_config(SVC, wired)
    sc.write_stamp(SVC["id"], digest, source)
    _go_stale(wired)

    finding = wd._recycle_if_stale(SVC, wired, SVC["id"])
    assert spy == {"stop": 1, "start": 1}  # stop THEN start: single-instance guard blocks a bare start
    assert "recycled onto new config" in finding.title
    # The new process's config is now the stamp, so the next tick is quiet.
    assert sc.staleness(SVC, wired)["stale"] is False


def test_a_healthy_current_service_is_left_alone(wired, spy):
    digest, source = sc.effective_config(SVC, wired)
    sc.write_stamp(SVC["id"], digest, source)
    finding = wd._recycle_if_stale(SVC, wired, SVC["id"])
    assert spy == {"stop": 0, "start": 0} and finding.status == wd.OK


def test_first_sighting_adopts_without_touching_the_process(wired, spy):
    finding = wd._recycle_if_stale(SVC, wired, SVC["id"])
    assert spy == {"stop": 0, "start": 0} and finding.status == wd.OK
    assert sc.read_stamp(SVC["id"])["config_hash"]  # recorded, so the NEXT change is caught


def test_auto_restart_off_warns_but_never_touches_the_service(wired, spy):
    """A service the orchestrator may not restart is not one it may recycle."""
    svc = {**SVC, "auto_restart": False}
    digest, source = sc.effective_config(svc, wired)
    sc.write_stamp(svc["id"], digest, source)
    _go_stale(wired)

    finding = wd._recycle_if_stale(svc, wired, svc["id"])
    assert spy == {"stop": 0, "start": 0}
    assert finding.status == wd.WARN and "stale config" in finding.title
    assert sc.staleness(svc, wired)["stale"] is True  # still flagged next tick, not silently stamped


def test_a_failed_recycle_does_not_advance_the_stamp(wired, monkeypatch):
    """Stamping a failed restart would mark the stale process as current and never retry."""
    digest, source = sc.effective_config(SVC, wired)
    sc.write_stamp(SVC["id"], digest, source)
    _go_stale(wired)
    monkeypatch.setattr(wd, "_stop_streamer", lambda root, spec: True)
    monkeypatch.setattr(wd, "_start_streamer", lambda root, argv: False)

    finding = wd._recycle_if_stale(SVC, wired, SVC["id"])
    assert "recycle failed" in finding.title
    assert sc.read_stamp(SVC["id"])["config_hash"] == digest  # unchanged
    assert sc.staleness(SVC, wired)["stale"] is True  # so the next tick tries again


def test_a_stale_check_hiccup_never_fails_the_tick(wired, monkeypatch):
    monkeypatch.setattr(sc, "staleness", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert wd._recycle_if_stale(SVC, wired, SVC["id"]).status == wd.OK


# --------------------------------------------------------------------------- the streamer
STREAMER = {
    "enabled": True,
    "path": "../streamer",
    "status_argv": ["run.py", "--status"],
    "start_argv": ["run.py"],
    "stop_argv": ["run.py", "--stop"],
    "auto_restart": True,
}


def test_a_stale_streamer_is_recycled(wired, spy):
    """The producer every module reads from — the one worth catching most."""
    digest, source = sc.effective_config(STREAMER, wired)
    sc.write_stamp("streamer", digest, source)
    _go_stale(wired)

    finding = wd._recycle_streamer_if_stale("streamer", wired, STREAMER, settling=False)
    assert spy == {"stop": 1, "start": 1}
    assert "recycled onto new config" in finding.title


def test_a_settling_streamer_is_left_alone(wired, spy):
    """A streamer restarted seconds ago has not resubscribed yet. Recycling it again starts a loop —
    the same reason the stall path honours `settling`."""
    digest, source = sc.effective_config(STREAMER, wired)
    sc.write_stamp("streamer", digest, source)
    _go_stale(wired)

    finding = wd._recycle_streamer_if_stale("streamer", wired, STREAMER, settling=True)
    assert spy == {"stop": 0, "start": 0} and finding.status == wd.OK


def test_two_producers_keep_separate_stamps(wired, spy):
    """During a cutover both the standalone producer and a module's own streamer may exist. One
    stamp between them would have each recycling the other, forever."""
    digest, source = sc.effective_config(STREAMER, wired)
    sc.write_stamp("streamer", digest, source)

    # meic.streamer has never been stamped, so it adopts rather than recycling on the other's stamp.
    finding = wd._recycle_streamer_if_stale("meic.streamer", wired, STREAMER, settling=False)
    assert spy == {"stop": 0, "start": 0} and finding.status == wd.OK
    assert sc.read_stamp("meic.streamer")["config_hash"] == digest
    assert sc.read_stamp("streamer")["config_hash"] == digest  # untouched


def test_streamer_recycle_honours_auto_restart(wired, spy):
    svc = {**STREAMER, "auto_restart": False}
    digest, source = sc.effective_config(svc, wired)
    sc.write_stamp("streamer", digest, source)
    _go_stale(wired)

    finding = wd._recycle_streamer_if_stale("streamer", wired, svc, settling=False)
    assert spy == {"stop": 0, "start": 0}
    assert finding.status == wd.WARN and "stale config" in finding.title


# --------------------------------------------------------------------------- stale subscriptions
# A producer's underlyings bind once, when it builds its streamer. A module that starts needing a new
# symbol writes its request file and the running process never sees it — the same file-versus-process
# gap the config hash catches, on a different input.
@pytest.fixture
def requests(tmp_path, monkeypatch):
    """An isolated stream_requests directory — these must never read the developer's real one."""
    directory = tmp_path / "stream_requests"
    directory.mkdir()
    monkeypatch.setattr(sc._streamrequests, "requests_dir", lambda: directory)

    def write(module, symbols=(), legs=(), window_hints=None):
        (directory / f"{module}.json").write_text(
            json.dumps(
                {
                    "symbols": list(symbols),
                    "legs": list(legs),
                    "leg_sources": [],
                    "window_hints": window_hints or {},
                }
            ),
            encoding="utf-8",
        )

    return write


def _stamp_producer(wired, label="streamer"):
    digest, source = sc.effective_config(STREAMER, wired)
    sc.write_stamp(label, digest, source, sc.subscription_snapshot())
    return digest


def test_a_new_underlying_recycles_the_producer(wired, spy, requests):
    """The gap issue #62 names: a module's symbol set changes and the running producer never re-reads."""
    requests("earnings", symbols=["AAPL"])
    _stamp_producer(wired)
    requests("earnings", symbols=["AAPL", "MSFT"])

    finding = wd._recycle_streamer_if_stale("streamer", wired, STREAMER, settling=False)
    assert spy == {"stop": 1, "start": 1}
    assert "recycled onto new subscriptions" in finding.title
    assert "MSFT" in finding.message
    # The restarted process subscribed it, so the next tick is quiet.
    assert sc.staleness(STREAMER, wired, "streamer", check_subscriptions=True)["stale"] is False


def test_a_dropped_underlying_leaves_the_producer_alone(wired, spy, requests):
    """Growth only. An over-subscribed producer serves every consumer correctly, and a restart costs a
    settling window — so a module whose request tracks its open positions (rewritten as each one closes)
    must not recycle the feed its own consumers are reading from."""
    requests("earnings", symbols=["AAPL", "MSFT"])
    _stamp_producer(wired)
    requests("earnings", symbols=["AAPL"])

    finding = wd._recycle_streamer_if_stale("streamer", wired, STREAMER, settling=False)
    assert spy == {"stop": 0, "start": 0} and finding.status == wd.OK


def _age_stamp(label: str, seconds: float) -> None:
    """Backdate a producer's launch stamp, so the hint cooldown can be tested without waiting."""
    path = sc.stamp_path(label)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stamped_at"] = payload["stamped_at"] - seconds
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_a_widened_window_hint_is_deferred_right_after_launch(wired, spy, requests):
    """The 2026-08-17 storm: pmcc walked its window hint up an escalation ladder and every step
    recycled the producer, roughly one restart per five minutes for two hours. A narrower-than-ideal
    window is a recorded refusal the module retries, not blindness — so it waits."""
    requests("flies", symbols=["XSP"], window_hints={"XSP": 40})
    _stamp_producer(wired)
    requests("flies", symbols=["XSP"], window_hints={"XSP": 90})

    finding = wd._recycle_streamer_if_stale("streamer", wired, STREAMER, settling=False)
    assert spy == {"stop": 0, "start": 0} and finding.status == wd.OK
    # Deferred is not the same state as unchanged, and the reason has to say so.
    state = sc.staleness(STREAMER, wired, "streamer", check_subscriptions=True)
    assert state["stale"] is False
    assert "holding off" in state["reason"] and "XSP=90" in state["reason"]
    assert state["deferred"] == {"window_hints": {"XSP": [90, 90]}}


def test_a_widened_window_hint_recycles_once_the_cooldown_passes(wired, spy, requests):
    """Deferred, not dropped — the widening is still served, just not on the same minute."""
    requests("flies", symbols=["XSP"], window_hints={"XSP": 40})
    _stamp_producer(wired)
    requests("flies", symbols=["XSP"], window_hints={"XSP": 90})
    _age_stamp("streamer", sc.HINT_RECYCLE_COOLDOWN_S + 1)

    finding = wd._recycle_streamer_if_stale("streamer", wired, STREAMER, settling=False)
    assert spy == {"stop": 1, "start": 1}
    assert "XSP=90" in finding.message


def test_a_new_symbol_recycles_even_inside_the_cooldown(wired, spy, requests):
    """The urgency distinction the cooldown rests on: a module that cannot see an instrument at all
    is blind, and blindness beats tidiness. Only window widenings wait."""
    requests("earnings", symbols=["AAPL"])
    _stamp_producer(wired)
    requests("earnings", symbols=["AAPL", "MSFT"], window_hints={"AAPL": 90})

    finding = wd._recycle_streamer_if_stale("streamer", wired, STREAMER, settling=False)
    assert spy == {"stop": 1, "start": 1}
    assert "MSFT" in finding.message


def test_a_stamp_with_no_launch_time_keeps_the_old_behaviour(wired, spy, requests):
    """Stamps written before the cooldown existed carry no `stamped_at`. An unknown age must not
    invent a cooldown — it falls back to recycling, exactly as it did before."""
    requests("flies", symbols=["XSP"], window_hints={"XSP": 40})
    _stamp_producer(wired)
    path = sc.stamp_path("streamer")
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["stamped_at"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    requests("flies", symbols=["XSP"], window_hints={"XSP": 90})

    finding = wd._recycle_streamer_if_stale("streamer", wired, STREAMER, settling=False)
    assert spy == {"stop": 1, "start": 1}
    assert "XSP=90" in finding.message


def test_a_narrowed_window_hint_leaves_the_producer_alone(wired, spy, requests):
    requests("flies", symbols=["XSP"], window_hints={"XSP": 90})
    _stamp_producer(wired)
    requests("flies", symbols=["XSP"], window_hints={"XSP": 40})

    finding = wd._recycle_streamer_if_stale("streamer", wired, STREAMER, settling=False)
    assert spy == {"stop": 0, "start": 0} and finding.status == wd.OK


def test_changing_legs_never_recycles_the_producer(wired, spy, requests):
    """Legs are re-read every subscription poll — a position opening or closing needs no restart, and
    must not be made to look like a reason for one."""
    requests("meic", symbols=["SPX"], legs=[".SPXW260812P6400"])
    _stamp_producer(wired)
    requests("meic", symbols=["SPX"], legs=[".SPXW260812P6400", ".SPXW260812C6500"])

    finding = wd._recycle_streamer_if_stale("streamer", wired, STREAMER, settling=False)
    assert spy == {"stop": 0, "start": 0} and finding.status == wd.OK


def test_a_producer_stamped_before_subscriptions_were_tracked_adopts(wired, spy, requests):
    """Same rule as an unstamped service: an unknown launch union is not evidence of staleness, so the
    first tick after this ships records it rather than restarting every producer at once."""
    requests("earnings", symbols=["AAPL"])
    digest, source = sc.effective_config(STREAMER, wired)
    sc.write_stamp("streamer", digest, source)  # old-shape stamp, no subscriptions

    finding = wd._recycle_streamer_if_stale("streamer", wired, STREAMER, settling=False)
    assert spy == {"stop": 0, "start": 0} and finding.status == wd.OK
    assert sc.read_stamp("streamer")["subscriptions"]["symbols"] == ["AAPL"]


def test_an_unreadable_request_set_is_not_treated_as_a_change(wired, spy, requests, monkeypatch):
    """Same posture as an unreadable config: silence is an unknown, not "nobody wants anything"."""
    requests("earnings", symbols=["AAPL"])
    _stamp_producer(wired)
    monkeypatch.setattr(
        sc._streamrequests,
        "subscription_snapshot",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("gone")),
    )
    finding = wd._recycle_streamer_if_stale("streamer", wired, STREAMER, settling=False)
    assert spy == {"stop": 0, "start": 0} and finding.status == wd.OK


def test_a_plain_service_never_consults_the_registry(wired, spy, requests):
    """Only a producer subscribes. A recorder's staleness has nothing to do with stream requests."""
    requests("earnings", symbols=["AAPL"])
    digest, source = sc.effective_config(SVC, wired)
    sc.write_stamp(SVC["id"], digest, source)
    requests("earnings", symbols=["AAPL", "MSFT"])

    finding = wd._recycle_if_stale(SVC, wired, SVC["id"])
    assert spy == {"stop": 0, "start": 0} and finding.status == wd.OK


# --------------------------------------------------- directional window hints (2026-08-26)
#
# `window_hints` became a (below, above) span so pmcc's deep-ITM long stops buying an identical
# depth above spot that no module can read. The shortfall comparison is the migration hazard: a
# stamp written before the change holds a bare int, and reading it as anything other than a
# symmetric span would recycle every producer in the suite on the first tick after this landed.


def test_shortfall_reads_a_pre_directional_stamp_as_symmetric():
    stamped = {"symbols": ["TQQQ"], "window_hints": {"TQQQ": 163}}  # written before spans existed
    current = {"symbols": ["TQQQ"], "window_hints": {"TQQQ": [163, 163]}}
    assert sc.subscription_shortfall(stamped, current) == {}


def test_shortfall_sees_growth_on_either_side_of_the_window():
    stamped = {"symbols": ["TQQQ"], "window_hints": {"TQQQ": [163, 30]}}
    deeper = {"symbols": ["TQQQ"], "window_hints": {"TQQQ": [200, 30]}}
    higher = {"symbols": ["TQQQ"], "window_hints": {"TQQQ": [163, 45]}}
    assert sc.subscription_shortfall(stamped, deeper) == {"window_hints": {"TQQQ": [200, 30]}}
    assert sc.subscription_shortfall(stamped, higher) == {"window_hints": {"TQQQ": [163, 45]}}


def test_a_window_that_deepens_one_side_while_narrowing_the_other_is_still_a_shortfall():
    """The running producer does not hold the new depth, whatever happened on the far side —
    a net-narrower window is not the same as a window nobody is short of."""
    stamped = {"symbols": ["TQQQ"], "window_hints": {"TQQQ": [163, 163]}}
    current = {"symbols": ["TQQQ"], "window_hints": {"TQQQ": [200, 12]}}
    assert sc.subscription_shortfall(stamped, current) == {"window_hints": {"TQQQ": [200, 12]}}


def test_shortfall_is_silent_on_a_window_that_only_narrows():
    stamped = {"symbols": ["TQQQ"], "window_hints": {"TQQQ": [163, 163]}}
    current = {"symbols": ["TQQQ"], "window_hints": {"TQQQ": [163, 12]}}
    assert sc.subscription_shortfall(stamped, current) == {}


def test_describe_shortfall_names_the_side_when_a_window_is_directional():
    assert "TQQQ=163v/12^" in sc.describe_shortfall({"window_hints": {"TQQQ": [163, 12]}})
    assert "XSP=90" in sc.describe_shortfall({"window_hints": {"XSP": [90, 90]}})
