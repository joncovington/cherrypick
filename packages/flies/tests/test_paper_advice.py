"""The advised arm — the loop side of the agentic layer for this module.

Mirrors MEIC's `test_paper_advice.py`, because the contract is the same one and re-validating with
`cherrypick.core.advice` is what makes the two sides unable to disagree. What differs is what this
module does with an admitted set: MEIC builds a shadow profile with a management twin, flies builds
a whole new BOOK and holds it to settlement.

The settlement test is the one that earns its place. Flies has no exits, so an advised book that
stops receiving advice has nothing to decide — but it still has to close, and closing it is the
tick's roster's job. A settlement pass that read a narrower roster than the tick entered on would
leave a real book open with no path to settling it, and nothing else in the suite would notice.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cherrypick.core import advice as core_advice
from test_engine import BASE_CONFIG
from test_paper_loop import TRADING_DAY, at
from test_provider import intrinsic_quotes, seed

from cherrypick.flies import db as dbmod
from cherrypick.flies import paper_loop

DAY = "2026-07-20"
BOUNDS = {"min_credit_pct_of_width": {"min": 0.15, "max": 0.30}}


@pytest.fixture()
def chain(tmp_path):
    """A fresh 0DTE SPX chain, spot just under the 6000 strike — the same fixture the paper-loop
    tests build, kept local rather than imported so neither file owns the other's setup."""
    import sqlite3

    from cherrypick.core.streamcache import DDL

    path = tmp_path / "stream_cache.db"
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    conn.commit()
    conn.close()
    seed(
        path,
        spot=5998.0,
        strikes=[5990, 5995, 6000, 6005, 6010],
        expiration=TRADING_DAY.date().isoformat(),
        quote_for=intrinsic_quotes(5998.0),
    )
    return path


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    db = tmp_path / "flies" / "paper_trades.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    # The decision file lives beside the DB; point the module's data dir at the tmp one.
    monkeypatch.setenv("FLIES_DB_PATH", str(db))
    return dbmod.connect(str(db))


def config(**advice):
    return {
        "symbols": ["SPX"],
        "defaults": {**BASE_CONFIG["defaults"], "entry_modes": ["legged"]},
        "arms": {"control": {"min_credit_pct_of_width": 0.2}},
        "advice": {"enabled": True, "base_arm": "control", "bounds": BOUNDS, **advice},
    }


def _write_artifact(home: Path, proposals, session=DAY, hours=12, experiment_id=None, experiments=None):
    """An artifact in the legacy single-overlay shape by default (its one entry resolves to
    `advised:control`), or -- with `experiments` -- one entry per concurrent experiment."""
    core_advice.write(
        core_advice.advice_path(home / "state", "flies", session),
        "flies",
        session,
        proposals,
        advisor="test",
        expires_at=(datetime.now(UTC) + timedelta(hours=hours)).isoformat(),
        experiment_id=experiment_id,
        experiments=experiments,
    )


def _experiment(name, value, experiment_id, base="control", param="min_credit_pct_of_width"):
    return {
        "experiment_id": experiment_id,
        "name": name,
        "tag": core_advice.advised_tag(name),
        "base": base,
        "proposals": [{"param": param, "value": value, "rationale": name}],
        "rejected": [],
    }


def _proposal(value=0.25):
    return [{"param": "min_credit_pct_of_width", "value": value, "rationale": "wider entry floor"}]


# --------------------------------------------------------------------------- the decision


def test_valid_advice_produces_an_advised_arm_beside_its_base(managed_home, conn):
    _write_artifact(managed_home, _proposal())
    arms = paper_loop.session_arms(cfg := config(), conn, DAY)

    assert arms == ["control", "advised:control"]
    # The advised arm differs from control in EXACTLY the advice — everything else is the base
    # arm's, which is what makes the pair a comparison rather than two unrelated books.
    from cherrypick.flies import engine

    advised = engine.merged_params(cfg, "advised:control")
    base = engine.merged_params(cfg, "control")
    assert advised["min_credit_pct_of_width"] == 0.25
    assert {k: v for k, v in advised.items() if k not in ("arm", "min_credit_pct_of_width")} == {
        k: v for k, v in base.items() if k not in ("arm", "min_credit_pct_of_width")
    }


def test_each_experiment_opens_its_own_arm_named_for_it(managed_home, conn):
    """Many experiments per module (2026-09-17): two entries on the same base are two arms of the
    same control, each carrying exactly its own overlay, tagged `advised:<experiment name>`."""
    _write_artifact(
        managed_home,
        [],
        experiments=[
            _experiment("Credit Floor (probe)", 0.25, "exp-2026-09-17-flies-1"),
            _experiment("credit-floor-wide", 0.30, "exp-2026-09-17-flies-2"),
        ],
    )
    arms = paper_loop.session_arms(cfg := config(), conn, DAY)
    assert arms == ["control", "advised:credit-floor-probe", "advised:credit-floor-wide"]

    from cherrypick.flies import engine

    assert engine.merged_params(cfg, "advised:credit-floor-probe")["min_credit_pct_of_width"] == 0.25
    assert engine.merged_params(cfg, "advised:credit-floor-wide")["min_credit_pct_of_width"] == 0.30
    # Each arm is stamped with ITS experiment, resolved from the decision by tag; control is nobody's.
    decision = paper_loop.advice_decision(cfg, DAY)
    assert core_advice.stamp_for("advised:credit-floor-probe", decision) == "exp-2026-09-17-flies-1"
    assert core_advice.stamp_for("advised:credit-floor-wide", decision) == "exp-2026-09-17-flies-2"
    assert core_advice.stamp_for("control", decision) is None


def test_one_experiments_rejection_is_its_baseline_day_and_nobody_elses(managed_home, conn):
    _write_artifact(
        managed_home,
        [],
        experiments=[
            _experiment("too-rich", 0.90, "exp-2026-09-17-flies-1"),
            _experiment("in-bounds", 0.30, "exp-2026-09-17-flies-2"),
        ],
    )
    assert paper_loop.session_arms(config(), conn, DAY) == ["control", "advised:in-bounds"]


def test_an_advised_arm_shadows_the_base_its_entry_names(managed_home, conn):
    """The base comes from the entry, not from the tag -- `advised:wide-twin` names no arm."""
    cfg = config()
    cfg["arms"]["wide"] = {"min_credit_pct_of_width": 0.2, "wing_width_strikes": 3}
    _write_artifact(
        managed_home, [], experiments=[_experiment("wide-twin", 0.25, "exp-2026-09-17-flies-1", base="wide")]
    )
    paper_loop.session_arms(cfg, conn, DAY)
    assert cfg["arms"]["advised:wide-twin"] == {"min_credit_pct_of_width": 0.25, "wing_width_strikes": 3}


def test_a_legacy_decision_file_still_opens_advised_control(managed_home, conn):
    """A decision recorded before `experiments` existed (the shape on disk through 2026-09-16)
    resolves to the one `advised:<base_arm>` arm its rows were always tagged with."""
    legacy = {
        "day": DAY,
        "base_arm": "control",
        "params": {"min_credit_pct_of_width": 0.25},
        "reason": None,
        "experiment_id": "exp-2026-09-14-flies-1",
    }
    path = Path(paper_loop._advice_decision_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(legacy), encoding="utf-8")
    cfg = config()
    assert paper_loop.session_arms(cfg, conn, DAY) == ["control", "advised:control"]
    assert cfg["arms"]["advised:control"]["min_credit_pct_of_width"] == 0.25
    assert core_advice.stamp_for("advised:control", paper_loop.advice_decision(cfg, DAY)) == (
        "exp-2026-09-14-flies-1"
    )


def test_absent_advice_is_baseline(managed_home, conn):
    assert paper_loop.session_arms(config(), conn, DAY) == ["control"]
    assert paper_loop.advice_decision(config(), DAY)["reason"] == "absent"


def test_out_of_bounds_advice_is_baseline_and_says_why(managed_home, conn):
    """Reject-all: one violation invalidates the whole artifact, so nothing rides in behind it."""
    _write_artifact(managed_home, _proposal(0.9))
    assert paper_loop.session_arms(config(), conn, DAY) == ["control"]
    assert "reject-all" in paper_loop.advice_decision(config(), DAY)["reason"]


def test_advice_for_another_session_is_never_sticky(managed_home, conn):
    _write_artifact(managed_home, _proposal(), session="2026-07-17")
    assert paper_loop.session_arms(config(), conn, DAY) == ["control"]


def test_expired_advice_is_baseline(managed_home, conn):
    _write_artifact(managed_home, _proposal(), hours=-1)
    assert paper_loop.session_arms(config(), conn, DAY) == ["control"]


def test_a_module_that_declares_no_bounds_refuses_advice(managed_home, conn):
    _write_artifact(managed_home, _proposal())
    cfg = config()
    cfg["advice"]["bounds"] = {}
    assert paper_loop.session_arms(cfg, conn, DAY) == ["control"]
    assert paper_loop.advice_decision(cfg, DAY)["reason"] == "advice_disabled: advice.bounds is empty"


def test_the_switch_in_the_modules_own_config_is_obeyed(managed_home, conn):
    _write_artifact(managed_home, _proposal())
    assert paper_loop.session_arms(config(enabled=False), conn, DAY) == ["control"]


# --------------------------------------------------------------------------- read-once


def test_the_decision_is_frozen_for_the_session(managed_home, conn):
    """Advice must not be able to start, stop or change mid-session — however late an artifact
    lands, and however the config is flipped intraday."""
    _write_artifact(managed_home, _proposal())
    assert paper_loop.session_arms(config(), conn, DAY) == ["control", "advised:control"]

    core_advice.advice_path(managed_home / "state", "flies", DAY).unlink()
    assert paper_loop.session_arms(config(), conn, DAY) == ["control", "advised:control"]

    decision = json.loads(Path(paper_loop._advice_decision_path()).read_text(encoding="utf-8"))
    assert decision["day"] == DAY and decision["params"] == {"min_credit_pct_of_width": 0.25}


def test_a_new_day_re_derives_its_own_decision(managed_home, conn):
    _write_artifact(managed_home, _proposal())
    paper_loop.session_arms(config(), conn, DAY)
    # No artifact for the next session: tomorrow is baseline, yesterday's decision is not inherited.
    assert paper_loop.session_arms(config(), conn, "2026-07-21") == ["control"]


# --------------------------------------------------------------------------- the book


def test_an_advised_book_is_entered_and_tagged_as_its_own_arm(managed_home, chain, conn):
    _write_artifact(managed_home, _proposal(0.15), experiment_id="exp-2026-09-14-flies-1")
    paper_loop.run_once(config(), conn, cache_path=str(chain), when=at(12))

    arms = [r[0] for r in conn.execute("SELECT DISTINCT arm FROM fly_positions").fetchall()]
    assert sorted(arms) == ["advised:control", "control"]
    # The experiment rides on the advised arm's rows and on nothing else (2026-09-16).
    stamped = dict(conn.execute("SELECT DISTINCT arm, experiment_id FROM fly_positions").fetchall())
    assert stamped == {"advised:control": "exp-2026-09-14-flies-1", "control": None}
    # An advised arm is a new book, not a measurement break in an existing one: control's own rows
    # are untouched and stay poolable with every session before this one.
    books = [r[0] for r in conn.execute("SELECT DISTINCT arm FROM fly_books").fetchall()]
    assert "advised:control" in books and "control" in books


def test_two_experiments_enter_two_books_with_distinct_stamps(managed_home, chain, conn):
    _write_artifact(
        managed_home,
        [],
        experiments=[
            _experiment("floor-15", 0.15, "exp-2026-09-17-flies-1"),
            _experiment("floor-16", 0.16, "exp-2026-09-17-flies-2"),
        ],
    )
    paper_loop.run_once(config(), conn, cache_path=str(chain), when=at(12))
    stamped = dict(conn.execute("SELECT DISTINCT arm, experiment_id FROM fly_positions").fetchall())
    assert stamped == {
        "advised:floor-15": "exp-2026-09-17-flies-1",
        "advised:floor-16": "exp-2026-09-17-flies-2",
        "control": None,
    }


def test_an_open_experiment_arm_settles_under_its_base_after_advice_is_gone(managed_home, chain, conn):
    """An `advised:<name>` arm holding rows must still resolve a base for settlement when the
    decision that opened it is gone -- the tag no longer carries one to split out."""
    _write_artifact(managed_home, [], experiments=[_experiment("floor-15", 0.15, "exp-2026-09-17-flies-1")])
    paper_loop.run_once(config(), conn, cache_path=str(chain), when=at(12))
    core_advice.advice_path(managed_home / "state", "flies", DAY).unlink()
    Path(paper_loop._advice_decision_path()).unlink()

    cfg = config(enabled=False)
    assert paper_loop.session_arms(cfg, conn, DAY) == ["control", "advised:floor-15"]
    assert cfg["arms"]["advised:floor-15"] == cfg["arms"]["control"]
    paper_loop.run_settle(cfg, conn, cache_path=str(chain), when=at(16, 25), price=5000.0)
    assert (
        conn.execute("SELECT status FROM fly_books WHERE arm = 'advised:floor-15'").fetchone()[0] == "settled"
    )


def test_an_advised_book_settles_even_when_advice_has_gone_away(managed_home, chain, conn):
    """The whole reason settlement and the tick share one roster helper.

    A book opened this morning under advice must close this afternoon whatever happened to the
    artifact, the decision file, or the config in between — flies holds to settlement and has no
    other way out.
    """
    _write_artifact(managed_home, _proposal(0.15))
    paper_loop.run_once(config(), conn, cache_path=str(chain), when=at(12))
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM fly_positions WHERE arm = 'advised:control' AND status = 'open'"
        ).fetchone()[0]
        > 0
    )

    # Everything the decision depended on, gone.
    core_advice.advice_path(managed_home / "state", "flies", DAY).unlink()
    Path(paper_loop._advice_decision_path()).unlink()

    paper_loop.run_settle(config(enabled=False), conn, cache_path=str(chain), when=at(16, 25), price=5000.0)

    open_rows, settled_rows = conn.execute(
        "SELECT COALESCE(SUM(status = 'open'), 0), COALESCE(SUM(status = 'settled'), 0)"
        " FROM fly_positions WHERE arm = 'advised:control'"
    ).fetchone()
    assert open_rows == 0 and settled_rows > 0
    assert (
        conn.execute("SELECT status FROM fly_books WHERE arm = 'advised:control'").fetchone()[0] == "settled"
    )
