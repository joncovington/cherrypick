"""An enrolled automation's chain request is priced against the subscription budget before it is written:
over the ceiling, today's entries lose their chain and every held position keeps its own."""

from __future__ import annotations

import json

from cherrypick.core import home, streamrequests
from test_paper_loop import DAY, at, world

from cherrypick.automations import paper_loop, stream_request


def set_budget(n: int) -> None:
    path = home.config_path("streamer")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"streamer": {"subscription_budget": n}}), encoding="utf-8")


def write(w, tmp_path, when):
    path = stream_request.write(
        w.config, w.arms, w.conn, str(tmp_path / "paper.db"), cache_path=w.cache_path, when=when
    )
    return json.loads(path.read_text(encoding="utf-8"))


def fit(w, when):
    return stream_request.fit(w.config, w.arms, w.conn, cache_path=w.cache_path, when=when)[1]


def test_a_request_inside_the_budget_is_written_whole(tmp_path):
    w = world(tmp_path)
    set_budget(10_000)
    request = write(w, tmp_path, at(9, 55))
    assert request["expirations"] == {"SPX": [DAY]} and request["window_hints"] == {"SPX": [60, 60]}
    report = fit(w, at(9, 55))
    assert report["over"] is False and report["shed"] == {} and 0 < report["total"] <= 10_000


def test_over_the_budget_an_entry_loses_its_chain(tmp_path):
    w = world(tmp_path)
    set_budget(100)  # one SPX window at 60 strikes a side is ~242
    request = write(w, tmp_path, at(9, 55))
    assert request["expirations"] == {} and request["window_hints"] == {}
    assert request["symbols"] == ["SPX"]  # the spot and daily bars are kept
    report = fit(w, at(9, 55))
    assert report["over"] is True and report["shed"] == {"SPX": [DAY]} and report["widened"] == ["SPX"]
    assert report["total_after"] < report["total"]


def test_a_held_position_keeps_its_expiry_over_the_budget(tmp_path):
    w = world(tmp_path)
    paper_loop.run_once(
        w.config, w.arms, w.conn, cache_path=w.cache_path, when=at(10, 0), evts=None, replay_to=w.replay
    )
    set_budget(100)
    request = write(w, tmp_path, at(14, 0))
    assert request["expirations"] == {"SPX": [DAY]}
    assert streamrequests.legs_from_source(request["leg_sources"][0])
    assert fit(w, at(14, 0))["shed"] == {}


def test_shed_keeps_only_what_a_position_holds():
    need = {"expirations": {"SPX": ["a", "b"], "GLD": ["c"]}, "window_hints": {"SPX": 60}, "symbols": ["SPX"]}
    narrowed, dropped = stream_request.shed(need, {"SPX": ["a"]})
    assert narrowed["expirations"] == {"SPX": ["a"]} and narrowed["window_hints"] == {}
    assert dropped == {"SPX": ["b"], "GLD": ["c"]} and narrowed["symbols"] == ["SPX"]


def test_this_modules_own_old_file_is_not_counted_twice(tmp_path):
    w = world(tmp_path)
    set_budget(1_000)
    stale = streamrequests.build_payload(
        ["SPX"], expirations={"SPX": [DAY, "2026-11-18", "2026-11-19", "2026-11-20"]}
    )
    streamrequests.write_payload("automations", stale)
    report = fit(w, at(9, 55))
    assert report["over"] is False and report["shed"] == {}


def test_another_modules_request_counts_toward_the_ceiling(tmp_path):
    w = world(tmp_path)
    set_budget(1_000)
    other = streamrequests.build_payload(
        ["SPX"], expirations={"SPX": ["2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23"]}
    )
    streamrequests.write_payload("gex", other)
    assert fit(w, at(9, 55))["shed"] == {"SPX": [DAY]}


def test_the_status_reports_the_budget(tmp_path):
    w = world(tmp_path)
    set_budget(100)
    status = paper_loop.run_status(w.config, w.arms, w.conn, cache_path=w.cache_path)
    assert {"total", "budget", "over", "shed"} <= set(status["stream_budget"])
