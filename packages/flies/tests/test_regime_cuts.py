"""The regime-cuts artifact writer (2026-09-19): flies' `analytics.regime_cuts` over the shared
`cherrypick.core.regimecuts` contract, and the `regime-cuts` CLI. Seeds rows with the same helper
`test_analytics.py` uses."""

import json

import pytest
from cherrypick.core import regimecuts as rc

from cherrypick.flies import analytics, cli
from cherrypick.flies import db as dbmod


def position(
    conn,
    position_id,
    *,
    day,
    arm,
    kind="fly",
    pnl=98.11,
    gross=105.0,
    regime=None,
    completion=None,
    completing_direction=None,
    latency=None,
    best_debit=None,
):
    """A settled legged row, the shape test_analytics.position() writes (tests dir is not a package)."""
    dbmod.save_position(
        conn,
        {
            "position_id": position_id,
            "book_id": f"{day}:{arm}:SPX",
            "trade_date": day,
            "arm": arm,
            "entry_mode": "legged",
            "symbol": "SPX",
            "kind": kind,
            "side": "put",
            "center": 6000.0,
            "wing_width": 5.0,
            "quantity": 1,
            "net": 1.05,
            "credit": 2.55,
            "fees": 6.89,
            "gross_pnl": gross,
            "pnl": pnl,
            "status": "settled",
            "underlying_at_entry": 6000.0,
            "risk_free": 1,
            "entry_time": f"{day}T12:00:00",
            "completing_direction": completing_direction,
            "completion_latency_min": latency,
            "best_completing_debit": best_debit,
            **{f"entry_{k}": v for k, v in (regime or {}).items()},
            **{f"completion_{k}": v for k, v in (completion or {}).items()},
        },
    )


DAY = "2026-09-18"


@pytest.fixture()
def conn(tmp_path):
    return dbmod.connect(str(tmp_path / "paper_trades.db"))


def _seed(conn):
    # control: three sessions, tagged gex + trend; one completed, one not
    for i, day in enumerate(("2026-09-14", "2026-09-15", "2026-09-16")):
        position(
            conn, f"c{i}", day=day, arm="control", regime={"gex_bucket": "diffuse", "trend_bucket": "flat"}
        )
    position(
        conn,
        "c-strand",
        day="2026-09-16",
        arm="control",
        kind="short_vertical",
        pnl=-300.0,
        gross=-293.0,
        regime={"gex_bucket": "diffuse", "trend_bucket": "up_from_open"},
    )
    # an advised twin, two sessions (thin)
    for i, day in enumerate(("2026-09-15", "2026-09-16")):
        position(
            conn,
            f"a{i}",
            day=day,
            arm="advised:x",
            regime={"gex_bucket": "clustered", "trend_bucket": "flat"},
        )
    # callwall with a pre-break row that must not count for it
    position(conn, "w-old", day="2026-08-25", arm="callwall", regime={"gex_bucket": "diffuse"})
    position(conn, "w-new", day="2026-09-15", arm="callwall", regime={"gex_bucket": "diffuse"})
    # an untagged control row
    position(conn, "c-untagged", day="2026-09-17", arm="control")
    # the flies-style fly rows above are "completed" only when completed_at is set
    conn.execute("UPDATE fly_positions SET completed_at = entry_time WHERE kind = 'fly'")
    conn.commit()
    dbmod.record_measurement_break(conn, break_date="2026-08-21", kind="advisor_era_cutover", reason="era")
    dbmod.record_measurement_break(
        conn, break_date="2026-08-31", scope="callwall", kind="arm_added", reason="added"
    )
    dbmod.record_measurement_break(conn, break_date="2026-12-18", kind="entry_rules", reason="future gate")


def test_by_regime_arm_filter_excludes_other_arms(conn):
    _seed(conn)
    rows = analytics.by_regime(conn, "gex", arm="advised:x")
    assert {r["bucket"] for r in rows} == {"clustered"} and rows[0]["trades"] == 2


def test_by_regime_reports_completed_and_completion_rate(conn):
    _seed(conn)
    rows = {r["bucket"]: r for r in analytics.by_regime(conn, "trend", arm="control")}
    assert rows["flat"]["completed"] == 3 and rows["flat"]["completion_rate"] == 1.0
    assert rows["up_from_open"]["completed"] == 0 and rows["up_from_open"]["completion_rate"] == 0.0


def test_regime_cuts_scopes_the_era_and_each_arm_from_its_own_break(conn):
    _seed(conn)
    doc = analytics.regime_cuts(conn, session=DAY, generated_at="t")
    assert doc["era"]["start"] == "2026-08-21"
    assert [b["break_date"] for b in doc["era"]["ignored_future"]] == ["2026-12-18"]
    books = {b["arm"]: b for b in doc["arms"]}
    assert books["callwall"]["era_start"] == "2026-08-31" and books["callwall"]["trades"] == 1
    assert books["control"]["era_start"] == "2026-08-21" and books["control"]["trades"] == 5
    assert "advised:x" in books  # twins are books


def test_regime_cuts_stamps_thin_and_carries_completion(conn):
    _seed(conn)
    doc = analytics.regime_cuts(conn, session=DAY, generated_at="t")
    books = {b["arm"]: b for b in doc["arms"]}
    advised_gex = books["advised:x"]["dimensions"]["gex"]["buckets"]
    assert advised_gex[0]["bucket"] == "clustered" and advised_gex[0]["thin"] is True
    control_gex = {b["bucket"]: b for b in books["control"]["dimensions"]["gex"]["buckets"]}
    assert control_gex["diffuse"]["thin"] is False and control_gex["diffuse"]["completed"] == 3
    assert control_gex["untagged"]["trades"] == 1
    assert books["control"]["completion_rate"] == 0.8  # 4 flies of 5 rows; the strand is the fifth


def test_cross_tab_cells_pair_the_two_buckets(conn):
    _seed(conn)
    doc = analytics.regime_cuts(conn, session=DAY, generated_at="t")
    ct = doc["cross_tabs"][0]
    assert ct["dims"] == ["gex", "trend"]
    control = next(b for b in ct["arms"] if b["arm"] == "control")
    cells = {tuple(c["buckets"]): c for c in control["cells"]}
    assert cells[("diffuse", "flat")]["trades"] == 3 and cells[("diffuse", "flat")]["thin"] is False
    assert (
        cells[("diffuse", "up_from_open")]["completed"] == 0
        and cells[("diffuse", "up_from_open")]["thin"] is True
    )
    assert cells[("untagged", "untagged")]["trades"] == 1


def _seed_drift(conn):
    day = "2026-09-21"
    rows = [
        ("d-with-up", "up", {"trend_bucket": "up_from_open", "trend_value": 30.0}),
        ("d-with-down", "down", {"trend_bucket": "down_from_open", "trend_value": -30.0}),
        ("d-against", "up", {"trend_bucket": "down_from_open", "trend_value": -30.0}),
        ("d-flat", "up", {"trend_bucket": "flat", "trend_value": 2.0}),
        ("d-nodir", None, {"trend_bucket": "up_from_open", "trend_value": 30.0}),
    ]
    for pid, direction, regime in rows:
        position(conn, pid, day=day, arm="control", regime=regime, completing_direction=direction)
    conn.execute("UPDATE fly_positions SET completed_at = entry_time WHERE position_id LIKE 'd-with%'")
    conn.commit()
    dbmod.record_measurement_break(conn, break_date="2026-08-21", kind="advisor_era_cutover", reason="era")


def test_second_cross_tab_pairs_gex_with_drift_alignment(conn):
    """Shown to fail while flies wrote only the shared default cross-tab: the second pair is
    declared module-side because MEIC writes the same artifact and has no drift dimension."""
    _seed_drift(conn)
    conn.execute("UPDATE fly_positions SET entry_gex_bucket = 'diffuse' WHERE position_id LIKE 'd-%'")
    conn.commit()
    doc = analytics.regime_cuts(conn, session="2026-09-21", generated_at="t")
    assert [t["dims"] for t in doc["cross_tabs"]] == [["gex", "trend"], ["gex", "drift_alignment"]]
    control = next(b for b in doc["cross_tabs"][1]["arms"] if b["arm"] == "control")
    cells = {tuple(c["buckets"]): c for c in control["cells"]}
    assert cells[("diffuse", "with")]["trades"] == 2 and cells[("diffuse", "with")]["completed"] == 2
    assert cells[("diffuse", "against")]["trades"] == 1 and cells[("diffuse", "untagged")]["trades"] == 1


def test_drift_alignment_buckets_by_completing_direction_against_trend(conn):
    """Shown to fail before the dimension existed: `by_regime` raised on an unknown dimension."""
    _seed_drift(conn)
    rows = {r["bucket"]: r for r in analytics.by_regime(conn, "drift_alignment", arm="control")}
    assert rows["with"]["trades"] == 2 and rows["with"]["value_min"] == 30.0
    assert rows["with"]["completion_rate"] == 1.0
    assert rows["against"]["trades"] == 1 and rows["against"]["value_max"] == -30.0
    assert rows["flat"]["trades"] == 1
    assert rows["untagged"]["trades"] == 1  # a tagged day with no completing direction is not `flat`
    cov = analytics.regime_coverage(conn, arm="control")["dimensions"]["drift_alignment"]
    assert cov["buckets"] == {"with": 2, "against": 1, "flat": 1} and cov["untagged"] == 1
    doc = analytics.regime_cuts(conn, session="2026-09-21", generated_at="t")
    control = next(b for b in doc["arms"] if b["arm"] == "control")
    assert {b["bucket"] for b in control["dimensions"]["drift_alignment"]["buckets"]} >= {"with", "against"}


def test_drift_alignment_completion_phase_reads_completion_columns(conn):
    """Shown to fail with the count-1 `entry_` rename: only the first of the expression's two trend
    references moved to the completion phase, so a flat entry read back as flat at completion."""
    position(
        conn,
        "d-phase",
        day="2026-09-21",
        arm="control",
        regime={"trend_bucket": "flat", "trend_value": 2.0},
        completion={"trend_bucket": "up_from_open", "trend_value": 40.0},
        completing_direction="up",
    )
    entry = analytics.by_regime(conn, "drift_alignment", arm="control")
    done = analytics.by_regime(conn, "drift_alignment", arm="control", phase="completion")
    assert [r["bucket"] for r in entry] == ["flat"]
    assert [(r["bucket"], r["value_min"]) for r in done] == [("with", 40.0)]


def test_regime_cuts_summary_carries_latency_and_miss_gap_quantiles(conn):
    """Shown to fail with KeyError before the summaries existed. 17.5 pins linear interpolation
    over nearest-rank (which would read 20)."""
    day = "2026-09-21"
    for i, lat in enumerate((10.0, 20.0, 30.0, 40.0)):
        position(conn, f"q{i}", day=day, arm="control", latency=lat)
    conn.execute("UPDATE fly_positions SET completed_at = entry_time WHERE position_id LIKE 'q%'")
    for i, debit in enumerate((2.80, 3.00)):
        position(conn, f"m{i}", day=day, arm="control", kind="short_vertical", pnl=-300.0, best_debit=debit)
    position(conn, "m-noprice", day=day, arm="control", kind="short_vertical", pnl=-300.0)
    conn.commit()
    dbmod.record_measurement_break(conn, break_date="2026-08-21", kind="advisor_era_cutover", reason="era")
    doc = analytics.regime_cuts(conn, session=day, generated_at="t")
    control = next(b for b in doc["arms"] if b["arm"] == "control")
    assert control["completion_latency_min"] == {"n": 4, "p25": 17.5, "p50": 25.0, "p75": 32.5, "max": 40.0}
    assert control["miss_gap"] == {"n": 2, "min": -0.45, "p25": -0.4, "p50": -0.35, "p75": -0.3}


def test_regime_cuts_summary_distributions_are_null_when_nothing_qualifies(conn):
    _seed(conn)
    doc = analytics.regime_cuts(conn, session=DAY, generated_at="t")
    control = next(b for b in doc["arms"] if b["arm"] == "control")
    assert control["completion_latency_min"] is None and control["miss_gap"] is None


def test_regime_cuts_is_byte_stable(conn):
    _seed(conn)
    a = json.dumps(analytics.regime_cuts(conn, session=DAY, generated_at="t"))
    b = json.dumps(analytics.regime_cuts(conn, session=DAY, generated_at="t"))
    assert a == b


def test_cli_write_leaves_the_dated_file_and_latest_and_no_tmp(conn, tmp_path, monkeypatch, capsys):
    _seed(conn)
    conn.close()
    monkeypatch.setattr(cli, "regime_cuts_dir", lambda: str(tmp_path / "out"))
    assert (
        cli.main(["--db", str(tmp_path / "paper_trades.db"), "regime-cuts", "--write", "--session", DAY]) == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["session"] == DAY and out["written"]["latest"]
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == [f"regime_cuts-{DAY}.json", "regime_cuts.json"]


def test_cli_backfill_writes_one_artifact_per_settled_session(conn, tmp_path, monkeypatch, capsys):
    _seed(conn)
    conn.close()
    monkeypatch.setattr(cli, "regime_cuts_dir", lambda: str(tmp_path / "out"))
    assert (
        cli.main(
            [
                "--db",
                str(tmp_path / "paper_trades.db"),
                "regime-cuts",
                "--write",
                "--backfill",
                "--since",
                "2026-09-15",
            ]
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["sessions"] == ["2026-09-15", "2026-09-16", "2026-09-17"]
    assert rc.latest_session(tmp_path / "out") == "2026-09-17"
    assert rc.dated_sessions(tmp_path / "out") == ["2026-09-15", "2026-09-16", "2026-09-17"]


@pytest.mark.parametrize("dimension", sorted(analytics.REGIME_DIMENSIONS))
def test_regime_parser_offers_every_declared_dimension(dimension, tmp_path):
    """Shown to fail before the fix on center_offset and trend: the choices list was hand-kept
    and had not been touched since the two dimensions were added on 2026-08-04."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["--db", str(tmp_path / "x.db"), "regime", "--dimension", dimension, "--help"])
    assert exc.value.code == 0


def test_session_totals_are_the_cells_own_net(conn):
    """flies' `pnl` is net (gross lives in `gross_pnl`). Shown to fail by pooling `gross_pnl`."""
    _seed(conn)
    for dim in analytics.REGIME_DIMENSIONS:
        for r in analytics.by_regime(conn, dim, arm="control", with_sessions=True):
            assert round(sum(v[1] for v in r["session_nets"].values()), 2) == r["net_pnl"], (dim, r["bucket"])
            assert sum(v[0] for v in r["session_nets"].values()) == r["trades"]
    assert "session_nets" not in analytics.by_regime(conn, "gex", arm="control")[0]


def test_regime_cuts_stamps_robustness_and_paired(conn):
    _seed(conn)
    doc = analytics.regime_cuts(conn, session=DAY, generated_at="t")
    control = next(b for b in doc["arms"] if b["arm"] == "control")
    diffuse = next(b for b in control["dimensions"]["gex"]["buckets"] if b["bucket"] == "diffuse")
    assert diffuse["sessions"] == 3 and diffuse["fragile"] is True  # the -300 strand sits on 09-16
    assert "paired" in control["dimensions"]["trend"]
    cell = doc["cross_tabs"][0]["arms"][0]["cells"][0]
    assert "fragile" in cell
    assert "session_nets" not in json.dumps(doc)


# --------------------------------------------------------------------------- gate replay (2026-09-28)
def _replay_row(hhmm, pnl, day="2026-09-15"):
    return {
        "trade_date": day,
        "kind": "fly",
        "entry_time": f"{day}T{hhmm}:00-04:00",
        "completed_at": f"{day}T15:00:00-04:00",
        "pnl": pnl,
        "entry_trend_bucket": "flat",
    }


def test_entry_window_replay_keeps_what_the_engine_window_admits():
    """Edges inclusive, like engine.in_entry_window. Shown to fail with a half-open window: the
    11:00 entry drops out of the first choice."""
    from cherrypick.flies import replay_gates

    rows = [
        _replay_row("10:05", 50.0),
        _replay_row("11:00", 20.0),
        _replay_row("12:10", -90.0),
        _replay_row("13:40", 70.0),
    ]
    skip_midday = replay_gates.replay_entry_windows(rows, [["10:00", "11:00"], ["13:00", "14:30"]])
    assert skip_midday["kept"] == 3 and skip_midday["net_pnl"] == 140.0
    # a window wider than anything recorded can only replay as the base, never add entries
    wide = replay_gates.replay_entry_windows(rows, [["09:30", "15:30"]])
    assert wide["kept"] == 4 and wide["net_pnl"] == 50.0
    out = replay_gates.without_per_day(replay_gates.sweep(rows, [[["10:00", "11:00"], ["13:00", "14:30"]]]))
    assert set(out["entry_windows"]) == {"10:00-11:00,13:00-14:30"}
    assert all(
        "per_day" not in b
        for fam in ("miss_stop", "trend_bucket", "entry_windows")
        for b in out[fam].values()
    )
    assert "per_day" not in out["base"]


def test_regime_cuts_carries_gate_replay_only_when_asked(conn):
    _seed(conn)
    plain = analytics.regime_cuts(conn, session=DAY, generated_at="t")
    assert "gate_replay" not in plain
    doc = analytics.regime_cuts(
        conn,
        session=DAY,
        generated_at="t",
        replay_arm="control",
        replay_windows=[[["10:00", "11:00"]]],
    )
    g = doc["gate_replay"]
    assert (g["arm"], g["start"], g["end"]) == ("control", "2026-08-21", DAY)
    assert (
        g["base"]["entries"] == 5
        and g["base"]["net_pnl"]
        == analytics.regime_cuts(conn, session=DAY, generated_at="t")["arms"][0]["net_pnl"]
    )
    assert g["entry_windows"]["10:00-11:00"]["kept"] == 0  # the fixture's entries are at 12:00


def test_gate_replay_scope_reads_the_config_own_bound():
    """The window choices come off the advice bound itself -- no second list to drift."""
    from cherrypick.flies import cli as flies_cli

    choices = [[["10:00", "14:30"]], [["10:00", "11:00"], ["13:00", "14:30"]]]
    cfg = {"advice": {"base_arm": "control", "bounds": {"entry_windows": {"choices": choices}}}}
    assert flies_cli._gate_replay_scope(cfg) == {"replay_arm": "control", "replay_windows": choices}
    assert flies_cli._gate_replay_scope({}) == {"replay_arm": "control", "replay_windows": []}


def test_the_shipped_entry_windows_bound_includes_control_own_window():
    """The bound must offer the base's own value, or the advisor could never run the lever as a
    no-op twin and every choice would change two things at once."""
    import pathlib

    cfg = json.loads((pathlib.Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8"))
    choices = cfg["advice"]["bounds"]["entry_windows"]["choices"]
    assert cfg["arms"]["control"]["entry_windows"] in choices
