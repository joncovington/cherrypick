"""The vol index map and the Cboe file reader."""

from __future__ import annotations

from cherrypick.core import volindex


def test_each_underlying_reads_its_own_index():
    assert volindex.index_for("spx") == "VIX" and volindex.index_for("QQQ") == "VXN"
    assert volindex.index_for("GLD") == "GVZ" and volindex.index_for("IWM") == "RVX"
    assert volindex.index_for("TSLA") is None


def test_closes_read_both_file_shapes_and_skip_bad_rows(tmp_path):
    (tmp_path / "VIX.csv").write_text(
        "DATE,OPEN,HIGH,LOW,CLOSE\n01/03/2025,17,18,16,17.5\nbad,1,1,1,1\n01/02/2025,16,17,15,16.5\n",
        encoding="utf-8",
    )
    (tmp_path / "SKEW.csv").write_text("DATE,SKEW\n01/02/2025,140.1\n", encoding="utf-8")
    assert volindex.closes("VIX", tmp_path) == {"2025-01-02": 16.5, "2025-01-03": 17.5}
    assert volindex.closes("SKEW", tmp_path) == {"2025-01-02": 140.1}
    assert volindex.closes("GVZ", tmp_path) == {}


def test_before_is_what_is_known_at_an_entry_that_day():
    series = {"2025-01-02": 1.0, "2025-01-03": 2.0, "2025-01-06": 3.0}
    assert volindex.before(series, "2025-01-06", 5) == [1.0, 2.0]
    assert volindex.before(series, "2025-01-07", 2) == [2.0, 3.0]
