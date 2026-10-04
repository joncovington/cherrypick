"""The study end to end on a small synthetic history: every pass runs, and the result has its shape."""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

from cherrypick.technicals import history, setups, store, study


def _land(path, names=6, sessions=500):
    conn = history.connect(path)
    day0 = date(2012, 1, 2)
    days = []
    d = day0
    while len(days) < sessions:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    for k in range(names):
        rnd = random.Random(k)
        c = 50.0
        rows = []
        for i, day in enumerate(days):
            c = max(6.0, c * (1 + (0.8 * math.sin(i / (12 + k)) + rnd.gauss(0, 1.2)) / 100))
            o = c * (1 + rnd.gauss(0, 0.003))
            rows.append(
                (
                    f"S{k}",
                    day,
                    o,
                    max(o, c) * 1.01,
                    min(o, c) * 0.99,
                    c,
                    2e6 * (3 if rnd.random() < 0.05 else 1),
                )
            )
        store.upsert_bars(conn, rows)
    conn.commit()
    conn.close()


def test_the_study_runs_end_to_end_and_reports_every_setup(tmp_path):
    path = tmp_path / "history.db"
    _land(path)
    result = study.run(workers=2, path=path, progress=lambda m: None)
    assert set(result["setups"]) == set(setups.RUN)
    assert result["positions_counted"] > 0 and result["names_in_universe"] == 6
    for v in result["setups"].values():
        assert v["verdict"] in ("edge over baseline", "no edge over baseline", "not yet judged")
        if v["describe"]["entries"]:
            assert v["describe"]["baseline_r"] is not None  # every counted entry drew a baseline
    # Six names cannot clear 780 effective entries: nothing is judged on a sample this small.
    assert all(v["verdict"] == "not yet judged" for v in result["setups"].values())
