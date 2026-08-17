"""The deployment score's five signals, its honesty rules, and the blend.

The score is record-only, so what these tests pin is not "is the number right for trading" but
"does the number mean what the block says it means": unmeasured inputs stay unmeasured, the blend
renormalizes only over what it measured, and a thin morning refuses to produce a score at all.
"""

from cherrypick.overview import score, symbols

SECTORS = tuple(symbols.SECTOR_ETFS)


def _series(values, start="2025-01-02"):
    """Close series with synthetic ascending session dates -- the math only reads order."""
    from datetime import date, timedelta
    day = date.fromisoformat(start)
    return [{"session": (day + timedelta(days=i)).isoformat(), "close": float(v)}
            for i, v in enumerate(values)]


def _wobble(base, n=260):
    """A series with a little variance -- a perfectly flat ratio has zero standard deviation and
    the credit signal (correctly) refuses to z-score it, which is not what most tests are about."""
    return [base + (i % 5) * 0.1 for i in range(n)]


def _full_history(vix_closes=None, hyg=None, tlt=None, sectors_above=11):
    """A history dict rich enough for every signal to measure."""
    vix_closes = vix_closes if vix_closes is not None else [20.0] * 260
    history = {"VIX": _series(vix_closes)}
    history["HYG"] = _series(hyg if hyg is not None else _wobble(80.0))
    history["TLT"] = _series(tlt if tlt is not None else _wobble(90.0))
    for index, etf in enumerate(SECTORS):
        # 200 flat sessions at 100, then a final close above or below that average.
        last = 110.0 if index < sectors_above else 90.0
        history[etf] = _series([100.0] * 220 + [last])
    return history


def _readings(vix=20.0, vix3m=22.0):
    return {"vix": {"value": vix}, "vix3m": {"value": vix3m}}


# --------------------------------------------------------------------------- individual signals

def test_vix_percentile_scores_a_calm_tape_high():
    # Today's VIX under every one of 260 history closes -> 100th percentile calm, plus the bonus.
    block = score.evaluate(_readings(vix=12.0), _full_history(), SECTORS)
    signal = next(s for s in block["signals"] if s["id"] == "vix_level")
    assert signal["status"] == "measured"
    assert signal["score"] == 100.0
    assert "calm bonus" in signal["detail"]


def test_vix_percentile_scores_a_stressed_tape_low():
    block = score.evaluate(_readings(vix=45.0), _full_history(), SECTORS)
    signal = next(s for s in block["signals"] if s["id"] == "vix_level")
    assert signal["score"] == 0.0
    assert "stress penalty" in signal["detail"]


def test_thin_vix_history_leaves_the_percentile_unmeasured():
    history = _full_history()
    history["VIX"] = _series([20.0] * 50)
    block = score.evaluate(_readings(), history, SECTORS)
    signal = next(s for s in block["signals"] if s["id"] == "vix_level")
    assert signal["status"] == "unknown"
    assert signal["score"] is None


def test_term_structure_maps_contango_high_and_backwardation_low():
    calm = score.evaluate(_readings(vix=17.0, vix3m=20.0), _full_history(), SECTORS)
    stressed = score.evaluate(_readings(vix=23.0, vix3m=20.0), _full_history(), SECTORS)
    calm_signal = next(s for s in calm["signals"] if s["id"] == "term_structure")
    stress_signal = next(s for s in stressed["signals"] if s["id"] == "term_structure")
    assert calm_signal["value"] == 0.85 and calm_signal["score"] == 100.0
    assert stress_signal["value"] == 1.15 and stress_signal["score"] == 0.0


def test_breadth_counts_sectors_above_their_own_sma():
    block = score.evaluate(_readings(), _full_history(sectors_above=6), SECTORS)
    signal = next(s for s in block["signals"] if s["id"] == "breadth")
    assert signal["status"] == "measured"
    assert signal["value"] == 54.5  # 6 of 11
    assert "proxy" in signal["detail"]


def test_breadth_needs_enough_sectors_to_be_measured_at_all():
    history = _full_history()
    for etf in list(SECTORS)[4:]:  # leave only four with enough history
        history[etf] = _series([100.0] * 10)
    block = score.evaluate(_readings(), history, SECTORS)
    signal = next(s for s in block["signals"] if s["id"] == "breadth")
    assert signal["status"] == "unknown"


def test_credit_stress_pushes_the_ratio_down_and_the_score_to_zero():
    # High yield selling off against Treasuries IS the stress case, and it moves the ratio DOWN --
    # the opposite direction to the spread convention these endpoints are easy to copy from.
    hyg = _wobble(80.0)[:-1] + [70.0]
    block = score.evaluate(_readings(), _full_history(hyg=hyg), SECTORS)
    signal = next(s for s in block["signals"] if s["id"] == "credit")
    assert signal["status"] == "measured"
    assert signal["value"] < -2.0
    assert signal["score"] == 0.0
    assert "proxy" in signal["detail"]


def test_credit_risk_appetite_scores_high():
    hyg = _wobble(80.0)[:-1] + [90.0]   # high yield ripping against Treasuries
    block = score.evaluate(_readings(), _full_history(hyg=hyg), SECTORS)
    signal = next(s for s in block["signals"] if s["id"] == "credit")
    assert signal["value"] > 2.0
    assert signal["score"] == 100.0


def test_credit_unmeasured_when_the_ratio_has_no_variance():
    history = _full_history(hyg=[80.0] * 260, tlt=[90.0] * 260)
    block = score.evaluate(_readings(), history, SECTORS)
    signal = next(s for s in block["signals"] if s["id"] == "credit")
    assert signal["status"] == "unknown"


def test_credit_unmeasured_when_the_two_legs_do_not_overlap():
    history = _full_history()
    history["TLT"] = _series([90.0] * 260, start="2030-01-02")  # no shared sessions with HYG
    block = score.evaluate(_readings(), history, SECTORS)
    signal = next(s for s in block["signals"] if s["id"] == "credit")
    assert signal["status"] == "unknown"


def test_vix_roc_reads_twenty_sessions_back():
    closes = [10.0] * 240 + [20.0] * 20  # 20 sessions ago VIX was 10, now 20 -> +100%
    block = score.evaluate(_readings(vix=20.0), _full_history(vix_closes=closes), SECTORS)
    signal = next(s for s in block["signals"] if s["id"] == "vix_roc")
    assert signal["value"] == 100.0
    assert signal["score"] == 0.0


# --------------------------------------------------------------------------- the blend

def test_all_signals_measured_blends_without_renormalizing():
    block = score.evaluate(_readings(), _full_history(), SECTORS)
    assert block["signals_measured"] == 5
    assert block["weights_renormalized"] is False
    assert 0.0 <= block["score"] <= 100.0
    assert block["zone"] in {"full", "reduced", "defensive"}


def test_one_missing_signal_renormalizes_the_rest():
    history = _full_history()
    history.pop("HYG")  # credit unmeasured; the other four still measure
    block = score.evaluate(_readings(), history, SECTORS)
    assert block["signals_measured"] == 4
    assert block["weights_renormalized"] is True
    assert block["score"] is not None


def test_too_few_measured_signals_refuses_to_score():
    block = score.evaluate({}, {}, SECTORS)   # nothing measured at all
    assert block["score"] is None
    assert block["zone"] is None
    assert "measured" in block["reason"]


def test_zones_split_at_the_declared_cut_offs():
    assert score._zone(score.ZONE_FULL_MIN) == "full"
    assert score._zone(score.ZONE_FULL_MIN - 0.1) == "reduced"
    assert score._zone(score.ZONE_REDUCED_MIN) == "reduced"
    assert score._zone(score.ZONE_REDUCED_MIN - 0.1) == "defensive"


def test_the_block_says_it_governs_nothing():
    block = score.evaluate(_readings(), _full_history(), SECTORS)
    assert block["record_only"] is True
    assert "factor_crowding" in block["deferred"]


def test_declared_weights_leave_the_deferred_signal_its_seat():
    # The five live weights deliberately sum to 0.90 -- the missing tenth is factor crowding's,
    # left visible rather than redistributed, so adding it later does not silently reweight.
    assert round(sum(score.WEIGHTS.values()), 10) == 0.90
