"""The vol term-structure block: its refusals, and the one definition it must not fork.

The properties worth pinning here are all about NOT answering. A vol panel is read at a glance, so a
confidently-wrong percentile is worse than a visible gap -- and the whole vol complex genuinely had
zero stored closes until the producer's Summary subscription was repaired on 2026-08-25, so the
thin-sample path is the one that actually ran, not a hypothetical.
"""

import pytest

from cherrypick.overview import facts


def _readings(**over):
    base = {
        "vix9d": {"value": 13.5, "basis": "live"},
        "vix": {"value": 15.5, "basis": "live"},
        "vix3m": {"value": 18.3, "basis": "live"},
        "vix6m": {"value": 20.9, "basis": "live"},
        "vix1y": {"value": 22.6, "basis": "live"},
        "vvix": {"value": 86.5, "basis": "live"},
        "skew": {"value": 145.6, "basis": "live"},
    }
    base.update(over)
    return base


def _history(symbol, values):
    return {symbol: [{"session": f"2026-01-{i % 28 + 1:02d}", "close": v} for i, v in enumerate(values)]}


# --------------------------------------------------------------- the definition that must not fork
def test_contango_threshold_matches_the_curve_module():
    """The suite has ONE definition of contango and it lives in `curve.regime`: VIX/VIX3M below
    `contango_max`, buffered below 1.0 so a knife-edge 0.999 day is not read as the harvest regime.

    Overview restates the constant because no package here imports another, so this test is the only
    thing standing between a restatement and a second, quietly diverging answer to the same question
    -- the drift the shared GEX engine exists to prevent, which cost a ~75x error when it happened.
    """
    curve_regime = pytest.importorskip(
        "cherrypick.curve.regime", reason="curve not installed in this environment"
    )
    assert facts._CONTANGO_MAX == curve_regime.REGIME_DEFAULTS["contango_max"], (
        "overview and curve disagree about what contango means"
    )


def test_shape_reads_the_ratio_not_the_slope_sign():
    block = facts._vol_regime(_readings(), {}, "2026-08-25")
    assert block["shape"] == "contango"
    assert block["vix_vix3m_ratio"] == pytest.approx(15.5 / 18.3, abs=1e-4)

    backwardated = facts._vol_regime(
        _readings(vix={"value": 25.0, "basis": "live"}, vix3m={"value": 20.0, "basis": "live"}),
        {},
        "2026-08-25",
    )
    assert backwardated["shape"] == "backwardation"


def test_shape_is_refused_when_the_ratio_cannot_be_measured():
    block = facts._vol_regime(_readings(vix3m={"value": None, "basis": None}), {}, "2026-08-25")
    assert block["shape"] is None
    assert block["shape_reason"] == "vix_or_vix3m_unmeasured"


# --------------------------------------------------------------------------------- the refusals
def test_a_percentile_under_the_sample_floor_is_refused_not_reported():
    """This is the path that actually ran. Every vol reading but VIX and VIX3M had ZERO stored
    closes, and without the floor the panel's first week would have shown confident percentiles
    drawn from three days of history."""
    block = facts._vol_regime(_readings(), _history("VVIX", [80.0] * 10), "2026-08-25")

    entry = block["percentiles"]["vvix"]
    assert entry["percentile"] is None
    assert entry["reason"] == "too_few_closes"
    assert entry["samples"] == 10, "the sample count is reported so the gap is legible"


def test_a_percentile_with_enough_history_is_computed():
    """The window, floor and formula are all `score`'s -- 252 sessions, 200 minimum, fraction
    STRICTLY below. Restating any of them here would put a second VIX percentile on the same page as
    the deployment score's, disagreeing with it."""
    from cherrypick.overview import score

    series = [float(x) for x in range(1, 401)]  # 400 closes; only the last 252 may be used
    block = facts._vol_regime(
        _readings(vix={"value": 300.0, "basis": "live"}), _history("VIX", series), "2026-08-25"
    )

    entry = block["percentiles"]["vix"]
    assert entry["samples"] == score.PERCENTILE_LOOKBACK, "the window is score's, not a local one"
    # The last 252 closes are 149..400; strictly below 300 is 149..299 -> 151 of 252.
    assert entry["percentile"] == pytest.approx(round(151 / 252 * 100, 1), abs=0.2)


def test_an_unmeasured_reading_is_distinguished_from_a_thin_sample():
    """Two different failures that a single None would conflate: the feed did not serve the reading,
    versus it did and we lack the history to place it."""
    block = facts._vol_regime(
        _readings(skew={"value": None, "basis": None}), _history("SKEW", [140.0] * 200), "2026-08-25"
    )
    assert block["percentiles"]["skew"]["reason"] == "reading_unmeasured"


def test_a_curve_point_the_feed_did_not_serve_is_still_a_row():
    """The curve keeps its shape when a point is missing -- a five-point structure rendered as four
    silently redraws the term structure rather than showing the hole."""
    block = facts._vol_regime(_readings(vix6m={"value": None, "basis": None}), {}, "2026-08-25")

    # Six points since VIX1D joined the front of the curve (2026-09-02); the fixture's readings may
    # or may not carry it, so measured is derived from the rows rather than pinned to a literal.
    assert block["total_points"] == 6
    assert block["measured_points"] == sum(1 for c in block["curve"] if c["value"] is not None)
    assert [c["symbol"] for c in block["curve"]] == ["VIX1D", "VIX9D", "VIX", "VIX3M", "VIX6M", "VIX1Y"]
    assert next(c for c in block["curve"] if c["symbol"] == "VIX6M")["value"] is None


# ------------------------------------------------------------------------------ the gate boundary
def test_the_block_feeds_no_gate():
    """The phase gates decide whether the suite deploys, and their semantics are a measurement
    boundary: adding an input would change what a GREEN means and make every prior session
    incomparable. Promoting any of this is a separate, journalled decision."""
    import inspect

    from cherrypick.overview import gates, score

    # Structural, not a source-text scan: the first version of this grepped both modules for
    # "vol_regime" and went red the moment score gained a docstring REFERENCE to it, which proves a
    # substring says nothing about what a function consumes. What matters is that neither evaluator
    # can even be handed the block -- so assert their declared parameters.
    for evaluate in (gates.evaluate, score.evaluate):
        params = list(inspect.signature(evaluate).parameters)
        assert not any("vol" in name for name in params), (
            f"{evaluate.__module__}.evaluate takes {params} -- a vol input changes what a phase means"
        )
    assert facts._vol_regime(_readings(), {}, "2026-08-25")["record_only"] is True


def test_a_reading_with_no_obtainable_daily_series_says_so(monkeypatch):
    """ "No history yet" and "no history ever" are different messages, and nothing in the data tells
    them apart -- so the second is declared.

    SKEW's 270-day backfill returned five scattered rows across seven months on the same connection
    that delivered a clean ~378 for every other vol reading. Reporting that as "only 5 closes on
    file" promises a gap that fills, and it never will; a permanent refusal dressed as a temporary
    one is exactly what teaches a reader to stop looking at the row.
    """
    block = facts._vol_regime(_readings(), _history("SKEW", [140.0] * 5), "2026-08-25")

    entry = block["percentiles"]["skew"]
    assert entry["percentile"] is None
    assert entry["reason"] == "no_daily_series"
    assert entry["value"] == 145.6, "the LIVE quote is fine and still reported"


def test_the_declaration_does_not_leak_onto_readings_that_have_a_series():
    block = facts._vol_regime(_readings(), _history("VVIX", [80.0 + i for i in range(300)]), "2026-08-25")
    assert block["percentiles"]["vvix"]["reason"] is None
    assert block["percentiles"]["vvix"]["percentile"] is not None
