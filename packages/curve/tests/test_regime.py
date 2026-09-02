from cherrypick.curve import regime


def test_ratio_basic():
    assert regime.ratio(20.0, 22.0) == round(20.0 / 22.0, 6)


def test_ratio_non_positive_denominator():
    assert regime.ratio(20.0, 0.0) is None
    assert regime.ratio(20.0, None) is None


def test_classify_contango():
    assert regime.classify(0.85) == "contango"


def test_classify_backwardation():
    assert regime.classify(1.05) == "backwardation"


def test_classify_buffer_at_contango_max():
    # A knife-edge day right at the default contango_max (0.97) is NOT contango — the buffer
    # exists precisely so 0.999 doesn't count.
    assert regime.classify(0.999) == "backwardation"
    assert regime.classify(0.90) == "contango"


def test_hook_requires_prior_ratio():
    assert regime.hook_signal(1.20, None) is False


def test_hook_fires_on_decline_above_threshold():
    assert regime.hook_signal(1.15, 1.30) is True  # above threshold AND declining


def test_hook_does_not_fire_on_rise():
    assert regime.hook_signal(1.15, 1.05) is False  # above threshold but still climbing


def test_hook_does_not_fire_below_threshold():
    assert regime.hook_signal(1.05, 1.30) is False  # declining but never cleared the threshold


def test_reading_ok():
    r = regime.reading({"value": 18.0, "age_seconds": 5}, {"value": 20.0, "age_seconds": 5}, prior_ratio=None)
    assert r["ok"] is True
    assert r["regime"] == "contango"
    assert r["hook"] is False


def test_reading_refuses_missing_vix():
    r = regime.reading(None, {"value": 20.0}, prior_ratio=None)
    assert r["ok"] is False
    assert r["reason"] == "no_vix_quote"


def test_reading_refuses_stale_quote():
    r = regime.reading(
        {"value": 18.0, "age_seconds": 10000}, {"value": 20.0, "age_seconds": 5}, prior_ratio=None
    )
    assert r["ok"] is False
    assert r["reason"] == "stale_vix"


def test_reading_never_a_frozen_guess_on_staleness():
    # A stale reading must refuse outright, never fall back to a "last known" ratio.
    r = regime.reading(
        {"value": 18.0, "age_seconds": 99999}, {"value": 20.0, "age_seconds": 99999}, prior_ratio=1.5
    )
    assert r["ok"] is False
    assert "ratio" not in r
