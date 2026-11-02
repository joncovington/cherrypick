"""The minute re-mark: a stored snapshot moved to newer quotes, and how the CLI stores it."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cherrypick.manage import __main__ as cli
from cherrypick.manage import remark

CALL = "XYZ   261120C00105000"
STREAMER = ".XYZ261120C105"


def _snapshot(generated_at="2026-11-02T10:00:30-0500"):
    short_call = {
        "symbol": CALL, "underlying": "XYZ", "instrument_type": "Equity Option", "quantity": -2.0,
        "right": "C", "strike": 105.0, "open_price": 3.00, "prev_close": 2.50, "multiplier": 100.0,
        "priced": True, "bid": 1.90, "ask": 2.10, "mid": 2.00, "mark_source": "stream_cache",
        "mark_age_seconds": 4.0, "value": -400.0, "open_pl": 200.0, "day_pl": 100.0, "spread": 0.20,
        "mark_doubt": 20.0, "wide": False,
    }  # fmt: skip
    shares = {
        "symbol": "XYZ", "underlying": "XYZ", "instrument_type": "Equity", "quantity": 200.0,
        "open_price": 90.0, "prev_close": 99.0, "multiplier": 1.0, "priced": True, "bid": 99.9,
        "ask": 100.1, "mid": 100.0, "value": 20000.0, "open_pl": 2000.0, "day_pl": 200.0,
    }  # fmt: skip
    other = {**shares, "symbol": "ABC", "underlying": "ABC", "quantity": 10.0, "value": 500.0, "mid": 50.0}
    return {
        "session": "2026-11-02",
        "generated_at": generated_at,
        "positions": {
            "ok": True,
            "accounts": [
                {
                    "account": "****0000",
                    "underlyings": [
                        {"underlying": "XYZ", "legs": [short_call, shares]},
                        {"underlying": "ABC", "legs": [other]},
                    ],
                }
            ],
        },  # fmt: skip
        "metrics": {"XYZ": {"last": 100.0}, "ABC": {"last": 50.0}, "SPY": {"last": 600.0}},
    }


def _overlay(snapshot, **quotes):
    return {
        "session": snapshot["session"],
        "base_generated_at": snapshot["generated_at"],
        "at": "2026-11-02T10:31:05-05:00",
        "max_age_seconds": 60.0,
        "quotes": quotes,
    }


def test_a_quoted_leg_and_spot_move_and_the_rest_keeps_the_snapshot_s_mark():
    snap = _snapshot()
    q = {"bid": 0.95, "ask": 1.05, "mid": 1.00, "age_seconds": 2.0, "source": "stream_cache"}
    out = remark.apply(
        snap, _overlay(snap, **{STREAMER: q, "XYZ": {"bid": 104.9, "ask": 105.1, "mid": 105.0}})
    )
    xyz = next(u for u in out["positions"]["accounts"][0]["underlyings"] if u["underlying"] == "XYZ")
    call = next(leg for leg in xyz["legs"] if leg["symbol"] == CALL)
    # Short two at 1.00, opened at 3.00: worth -200, up 400 on the open, 300 on the day.
    assert (call["mid"], call["value"], call["open_pl"], call["day_pl"]) == (1.00, -200.0, 400.0, 300.0)
    assert call["mark_age_seconds"] == 2.0 and call["mark_doubt"] == pytest.approx(10.0)
    shares = next(leg for leg in xyz["legs"] if leg["symbol"] == "XYZ")
    assert shares["value"] == 21000.0, "the shares are re-marked at the underlying's own quote"
    abc = next(u for u in out["positions"]["accounts"][0]["underlyings"] if u["underlying"] == "ABC")
    assert abc["legs"][0]["value"] == 500.0, "an unquoted leg keeps its snapshot mark"
    assert out["metrics"]["XYZ"]["last"] == 105.0 and out["metrics"]["ABC"]["last"] == 50.0
    assert xyz["value"] == pytest.approx(20800.0) and out["positions"]["accounts"][0][
        "value"
    ] == pytest.approx(21300.0)
    assert out["remark"] == {
        "at": "2026-11-02T10:31:05-05:00", "base_generated_at": snap["generated_at"], "max_age_seconds": 60.0,
        "legs_remarked": 2, "legs_kept": 1, "spots_remarked": 1,
    }  # fmt: skip
    assert snap["positions"]["accounts"][0]["underlyings"][0]["legs"][0]["mid"] == 2.00, (
        "the input is untouched"
    )


def test_an_overlay_read_against_another_snapshot_is_refused():
    snap = _snapshot()
    with pytest.raises(remark.StaleOverlay):
        remark.apply(_snapshot(generated_at="2026-11-02T10:30:40-0500"), _overlay(snap))


def test_a_leg_from_before_multipliers_were_stated_is_read_back_from_its_value():
    snap = _snapshot()
    leg = snap["positions"]["accounts"][0]["underlyings"][0]["legs"][0]
    del leg["multiplier"]
    out = remark.apply(snap, _overlay(snap, **{STREAMER: {"bid": 0.9, "ask": 1.1, "mid": 1.0}}))
    assert out["positions"]["accounts"][0]["underlyings"][0]["legs"][0]["value"] == -200.0


def test_the_symbols_a_remark_can_use_are_the_legs_and_the_quoted_underlyings():
    assert remark.wanted(_snapshot()) == sorted({STREAMER, "XYZ", "ABC", "SPY"})


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MANAGE_DATA_DIR", str(tmp_path))
    seen = []

    def fake_evaluate(snapshot, rules, warnings=None):
        seen.append(snapshot)
        return {"ok": True, "session": snapshot["session"], "accounts": []}

    monkeypatch.setattr(cli._evaluate, "evaluate", fake_evaluate)
    monkeypatch.setattr(cli._stream_request, "register", lambda result: None)
    return tmp_path, seen


def test_remark_evaluates_the_moved_snapshot_and_stores_the_overlay_never_the_snapshot(home):
    folder, seen = home
    snap = _snapshot()
    (folder / "snapshot-2026-11-02.json").write_text(json.dumps(snap), encoding="utf-8")
    overlay = _overlay(snap, **{STREAMER: {"bid": 0.9, "ask": 1.1, "mid": 1.0}})
    path = folder / "overlay.json"
    path.write_text(json.dumps(overlay), encoding="utf-8")
    out = cli.cmd_remark(SimpleNamespace(overlay=str(path), rules=None))
    assert out["ok"] and out["remark"]["legs_remarked"] == 1
    assert seen[-1]["remark"]["legs_remarked"] == 1
    assert json.loads((folder / "snapshot-2026-11-02.json").read_text(encoding="utf-8")) == snap
    assert json.loads((folder / "marks-2026-11-02.json").read_text(encoding="utf-8")) == overlay
    assert (
        json.loads((folder / "eval-2026-11-02.json").read_text(encoding="utf-8"))["remark"]["legs_remarked"]
        == 1
    )
    # A re-run gives the same answer twice: it reads the stored overlay against the stored snapshot.
    cli.cmd_reevaluate(SimpleNamespace(session="2026-11-02", rules=None))
    assert seen[-1]["remark"] == seen[-2]["remark"]
    # A new full snapshot retires the overlay, and an overlay read against the old one is refused.
    newer = _snapshot(generated_at="2026-11-02T10:30:40-0500")
    fresh = folder / "fresh.json"
    fresh.write_text(json.dumps(newer), encoding="utf-8")
    cli.cmd_evaluate(SimpleNamespace(snapshot=str(fresh), rules=None))
    assert not (folder / "marks-2026-11-02.json").exists()
    stale = cli.cmd_remark(SimpleNamespace(overlay=str(path), rules=None))
    assert stale["ok"] is False and stale["stale"] is True
    assert "remark" not in seen[-1] or seen[-1].get("remark") is None


def test_remark_with_no_stored_snapshot_says_so(home):
    folder, _ = home
    path = folder / "overlay.json"
    path.write_text(json.dumps(_overlay(_snapshot())), encoding="utf-8")
    out = cli.cmd_remark(SimpleNamespace(overlay=str(path), rules=None))
    assert out["ok"] is False and "no stored snapshot" in out["error"]
