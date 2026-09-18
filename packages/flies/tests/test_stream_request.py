"""flies declares its underlyings to the streamer via a stream-request file (best-effort, never fatal)."""

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cherrypick.flies import stream_request  # noqa: E402


def test_register_writes_deduped_upper_symbols(managed_home):
    stream_request.register({"symbols": ["spx", " xsp ", "spx"]})
    path = managed_home / "state" / "stream_requests" / "flies.json"
    payload = json.loads(path.read_text())
    # Field-by-field rather than whole-payload equality: the request schema is owned by
    # `cherrypick.core.streamrequests` and grows when ANOTHER module needs something (it gained
    # `expirations` for calendars and `history_days` since this was written), so an exact-match
    # assertion here fails on a change that has nothing to do with flies. What this test is about is
    # the normalizing — upper-cased, trimmed, deduped — and the empty defaults for what flies does
    # not declare.
    assert payload["symbols"] == ["SPX", "XSP"]
    assert payload["legs"] == []
    assert payload["window_hints"] == {}
    # Since 2026-09-17 the file carries ONE leg source: this loop's own ledger and the open-legs query.
    assert len(payload["leg_sources"]) == 1 and payload["leg_sources"][0]["query"] == stream_request.LEG_QUERY


def test_register_carries_window_hints(managed_home):
    stream_request.register({"symbols": ["XSP"]}, window_hints={"XSP": 90})
    path = managed_home / "state" / "stream_requests" / "flies.json"
    assert json.loads(path.read_text())["window_hints"] == {"XSP": [90, 90]}


def test_live_register_writes_a_separate_file(managed_home):
    stream_request.register({"symbols": ["XSP"]}, window_hints={"XSP": 40})
    stream_request.register({"symbols": ["XSP"]}, window_hints={"XSP": 90}, live=True)

    paper = json.loads((managed_home / "state" / "stream_requests" / "flies.json").read_text())
    live = json.loads((managed_home / "state" / "stream_requests" / "flies-live.json").read_text())
    assert paper["window_hints"] == {"XSP": [40, 40]}
    assert live["window_hints"] == {"XSP": [90, 90]}


def test_register_empty_symbols(managed_home):
    stream_request.register({})
    path = managed_home / "state" / "stream_requests" / "flies.json"
    assert json.loads(path.read_text())["symbols"] == []


def test_register_is_best_effort_never_raises(managed_home, monkeypatch):
    def _boom(_symbols, window_hints=None, live=False, db_path=None):
        raise OSError("disk full")

    monkeypatch.setattr(stream_request, "write", _boom)
    stream_request.register({"symbols": ["SPX"]})  # must not propagate — the loop keeps running


# --------------------------------------------------------------------------- open legs (2026-09-17)
def _ledger_with(tmp_path, rows):
    from cherrypick.flies import db as dbmod

    path = tmp_path / "ledger.db"
    conn = dbmod.connect(path)
    for r in rows:
        dbmod.save_position(
            conn,
            {
                "position_id": r["id"],
                "book_id": "b",
                "trade_date": "2026-09-17",
                "arm": "control",
                "entry_mode": "legged",
                "symbol": "SPX",
                "kind": "short_vertical",
                "side": "put",
                "center": 7500.0,
                "wing_width": 5,
                "quantity": 1,
                "net": 1.0,
                "fees": 3.44,
                "status": r["status"],
                "entry_time": "2026-09-17T10:00:00-04:00",
                **{k: v for k, v in r.items() if k.endswith("_leg_symbol")},
            },
        )
    conn.close()
    return path


def test_each_loop_declares_its_own_ledger_as_the_leg_source(managed_home, tmp_path):
    """Paper's file names the paper ledger; live's names the live ledger. A file that named the
    other loop's ledger would keep the wrong book's legs quoted and drop its own."""
    paper_db = tmp_path / "paper.db"
    stream_request.register({"symbols": ["SPX"]}, db_path=str(paper_db))
    stream_request.register({"symbols": ["SPX"]}, live=True)
    paper = json.loads((managed_home / "state" / "stream_requests" / "flies.json").read_text())
    live = json.loads((managed_home / "state" / "stream_requests" / "flies-live.json").read_text())
    from cherrypick.flies import db as dbmod

    assert Path(paper["leg_sources"][0]["db"]) == paper_db
    assert Path(live["leg_sources"][0]["db"]) == Path(dbmod.live_db_path())
    assert paper["leg_sources"][0]["query"] == live["leg_sources"][0]["query"] == stream_request.LEG_QUERY


def test_the_leg_query_returns_open_legs_only_and_tolerates_unstamped_rows(tmp_path):
    """Run the declared query the way the producer does -- read-only, every non-null cell a
    symbol. An open row's stamped legs come back; a settled row's do not; a row written before
    the columns existed (all NULL) contributes nothing rather than an empty string."""
    import sqlite3

    path = _ledger_with(
        tmp_path,
        [
            {
                "id": "OPEN",
                "status": "open",
                "center_leg_symbol": ".SPXW260917P7500",
                "wing_leg_symbol": ".SPXW260917P7495",
                "completing_leg_symbol": ".SPXW260917P7505",
            },
            {
                "id": "DONE",
                "status": "settled",
                "center_leg_symbol": ".SPXW260917P7400",
                "wing_leg_symbol": ".SPXW260917P7395",
            },
            {"id": "OLD", "status": "open"},
        ],
    )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    cells = [
        c for row in conn.execute(stream_request.LEG_QUERY) for c in row if isinstance(c, str) and c.strip()
    ]
    conn.close()
    assert sorted(cells) == [".SPXW260917P7495", ".SPXW260917P7500", ".SPXW260917P7505"]
