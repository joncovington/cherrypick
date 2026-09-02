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


def _write_artifact(home: Path, proposals, session=DAY, hours=12):
    core_advice.write(
        core_advice.advice_path(home / "state", "flies", session),
        "flies",
        session,
        proposals,
        advisor="test",
        expires_at=(datetime.now(UTC) + timedelta(hours=hours)).isoformat(),
    )


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
    _write_artifact(managed_home, _proposal(0.15))
    paper_loop.run_once(config(), conn, cache_path=str(chain), when=at(12))

    arms = [r[0] for r in conn.execute("SELECT DISTINCT arm FROM fly_positions").fetchall()]
    assert sorted(arms) == ["advised:control", "control"]
    # An advised arm is a new book, not a measurement break in an existing one: control's own rows
    # are untouched and stay poolable with every session before this one.
    books = [r[0] for r in conn.execute("SELECT DISTINCT arm FROM fly_books").fetchall()]
    assert "advised:control" in books and "control" in books


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
