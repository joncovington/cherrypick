"""The EMA trend-signal study (scripts/ema_trend_signal_history.py): weekly sampling, the state, and the
per-symbol pass test, which is shown to FAIL on a series with no signal.

Synthetic closes only: no store.
"""

from __future__ import annotations

import importlib.util
import random
from datetime import date, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ema_trend_signal_history.py"


def _module():
    spec = importlib.util.spec_from_file_location("ema_trend_signal_history", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


es = _module()


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_samples_fall_on_week_ends_and_read_the_ema_state():
    dates = weekdays(date(2015, 1, 5), 200)  # Mondays to Fridays
    closes = [100 + i for i in range(200)]  # a steady rise: the 8 EMA leads the 21
    s = es.weekly_samples(dates, closes)
    assert s and all(d.weekday() == 4 for d, _, _ in s)
    assert all(state == "up" for _, state, _ in s)
    assert all(r > 0 for _, _, r in s)


def test_a_persistent_trend_passes_and_noise_fails():
    rng = random.Random(7)
    dates = weekdays(date(2011, 1, 3), 3800)
    # momentum: regimes of drift that persist for months, so the EMA state carries into next week
    closes, x, drift = [], 100.0, 0.002
    for i in range(3800):
        if i % 120 == 0:
            drift = 0.003 if (i // 120) % 2 == 0 else -0.002
        x *= 1 + drift + rng.gauss(0, 0.004)
        closes.append(x)
    assert es.symbol_report(es.weekly_samples(dates, closes))["pass"] is True
    noise, y = [], 100.0
    for _ in range(3800):
        y *= 1 + rng.gauss(0, 0.01)
        noise.append(y)
    assert es.symbol_report(es.weekly_samples(dates, noise))["pass"] is False
