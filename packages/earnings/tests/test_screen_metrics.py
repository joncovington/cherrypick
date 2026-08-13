"""Analysis of the rejection trail. The load-bearing property here is that scan_log's four
accumulated vocabularies are never pooled — a number averaged across all of them describes none
of them, and would recommend moving thresholds that no longer exist."""

from cherrypick.earnings import screen_metrics as sm


def _row(**kw):
    base = {
        "scan_date": "2026-08-12",
        "symbol": "CSCO",
        "strategy": "iron_fly",
        "tier": "rejected",
        "outcome": "rejected",
        "reason": None,
        "stage": "screen",
        "reject_details": [],
        "profile": "strat_test:iron_fly",
    }
    base.update(kw)
    return base


def _rejected(reasons, details=None, **kw):
    return _row(reason="; ".join(reasons), reject_details=details or [], **kw)


# --------------------------------------------------------------------------- vocabularies


def test_the_legacy_tier_ladder_is_not_counted_as_screening():
    """Its `reason` is a bare criterion name ('iv_rv_ratio') that reads like a gate name and means
    something else — the graded ladder was replaced by a single binary bar."""
    legacy = _row(outcome="Tier 2", tier="Tier 2", reason="iv_rv_ratio")
    assert sm.classify(legacy) == "legacy"
    assert sm.strategy_rows([legacy]) == []


def test_a_position_close_is_not_a_screening_rejection():
    """The exit path logs to the same table. 'close_window' is an exit decision; counting it as a
    gate would invent a screening criterion that does not exist."""
    close = _row(tier="close_sweep", outcome="closed", reason="close_window")
    assert sm.classify(close) == "exit"
    assert sm.reason_frequency([close]) == []


def test_a_retired_strategys_decisions_are_excluded():
    """Real decisions, but tuning a threshold for a strategy that can no longer trade is wasted."""
    assert sm.classify(_rejected(["price_below_minimum"], strategy="short_strangle")) == "retired"


def test_bookkeeping_rows_are_excluded():
    assert sm.classify(_row(strategy="_ranked", outcome="selected")) == "bookkeeping"
    assert sm.classify(_row(strategy="_prefilter", stage="prefilter", outcome="skipped")) == "prefilter"


def test_current_screening_rows_are_kept():
    assert sm.classify(_rejected(["price_below_minimum"])) == "screen"
    assert sm.classify(_row(outcome="accepted", reason=None)) == "screen"


def test_exclusions_are_reported_rather_than_silent():
    """A report that quietly drops most of its input looks exactly like one that analysed all of
    it — which is how a tuning decision gets made on a sample nobody knew was filtered."""
    rows = [
        _rejected(["price_below_minimum"]),
        _row(outcome="Tier 1", tier="Tier 1", reason="iv_rv_ratio"),
        _row(tier="close_sweep", outcome="closed", reason="time_exit"),
    ]
    kinds = {e["kind"]: e["rows"] for e in sm.excluded_summary(rows)}
    assert kinds == {"legacy": 1, "exit": 1}


# --------------------------------------------------------------------------- sole blockers


def test_sole_blocker_is_the_only_rejection_a_threshold_can_rescue():
    rows = [
        _rejected(["avg_volume_below_minimum"]),
        _rejected(["avg_volume_below_minimum", "iv_rv_ratio_below_minimum"], symbol="AMAT"),
    ]
    freq = {f["reason"]: f for f in sm.reason_frequency(rows)}
    assert freq["avg_volume_below_minimum"]["total"] == 2
    assert freq["avg_volume_below_minimum"]["sole"] == 1
    assert freq["iv_rv_ratio_below_minimum"]["sole"] == 0


def test_a_gate_that_never_fires_alone_is_visible_as_shadowed():
    """Moving this threshold would change no outcome at all — the measured case is
    front_expiration_days_too_far_out, which in the real ledger has 226 rejections and 0 sole."""
    rows = [_rejected(["no_weekly_options", "front_expiration_days_too_far_out"]) for _ in range(5)]
    freq = {f["reason"]: f for f in sm.reason_frequency(rows)}
    assert freq["front_expiration_days_too_far_out"]["total"] == 5
    assert freq["front_expiration_days_too_far_out"]["sole"] == 0


def test_accepted_rows_contribute_no_reasons():
    assert sm.reason_frequency([_row(outcome="accepted", reason=None)]) == []


# --------------------------------------------------------------------------- distance and what-if


def _measured(reason, criterion, measured, threshold, comparator, **kw):
    return _rejected(
        [reason],
        details=[
            {
                "reason": reason,
                "criterion": criterion,
                "measured": measured,
                "threshold": threshold,
                "comparator": comparator,
            }
        ],
        **kw,
    )


def test_distance_reports_how_much_of_the_sample_it_could_measure():
    """Rows predating reject_details have the reason and no number. Thinning the sample silently
    would make a 1-of-100 reading look like a 100-of-100 one."""
    rows = [
        _measured("avg_volume_below_minimum", "avg_volume", 900_000, 1_500_000, "<"),
        _rejected(["avg_volume_below_minimum"], symbol="OLD"),
    ]
    dist = sm.threshold_distances(rows, "avg_volume_below_minimum")
    assert dist["rows"] == 2 and dist["measured_rows"] == 1
    assert dist["threshold"] == 1_500_000


def test_what_if_counts_the_names_a_looser_floor_admits():
    rows = [
        _measured("avg_volume_below_minimum", "avg_volume", 1_200_000, 1_500_000, "<", symbol="A"),
        _measured("avg_volume_below_minimum", "avg_volume", 40_000, 1_500_000, "<", symbol="B"),
    ]
    cf = sm.counterfactual(rows, "avg_volume_below_minimum", 1_000_000)
    assert cf["measurable"] == 2 and cf["admitted"] == 1
    assert cf["symbols"] == ["A"]


def test_what_if_handles_a_ceiling_gate_in_the_right_direction():
    """A spread gate rejects values ABOVE the bar, so a looser bar is a bigger number."""
    rows = [
        _measured("bid_ask_spread_too_wide", "bid_ask_spread_pct", 0.20, 0.15, ">", symbol="A"),
        _measured("bid_ask_spread_too_wide", "bid_ask_spread_pct", 2.0, 0.15, ">", symbol="B"),
    ]
    cf = sm.counterfactual(rows, "bid_ask_spread_too_wide", 0.25)
    assert cf["admitted"] == 1 and cf["symbols"] == ["A"]


def test_what_if_reports_nothing_it_could_not_measure():
    rows = [_rejected(["avg_volume_below_minimum"])]
    cf = sm.counterfactual(rows, "avg_volume_below_minimum", 1_000_000)
    assert cf["measurable"] == 0 and cf["admitted"] == 0


# --------------------------------------------------------------------------- funnel and coverage


def test_the_funnel_names_accepted_candidates_with_no_execution_record():
    """The gap Phase 1 closed: acceptance used to be the last thing written down."""
    rows = [
        _row(outcome="accepted", reason=None),
        _row(outcome="accepted", reason=None, symbol="AMAT"),
        _row(stage="execution", outcome="opened", reason=None, symbol="AMAT"),
    ]
    f = sm.funnel(rows)
    assert f["accepted"] == 2 and f["opened"] == 1
    assert f["unexplained_accepted"] == 1


def test_the_funnel_explains_accepted_candidates_that_never_opened():
    rows = [
        _row(outcome="accepted", reason=None),
        _row(stage="execution", outcome="dropped", reason="order_build_failed: no strikes"),
    ]
    f = sm.funnel(rows)
    assert f["dropped"] == 1
    assert f["drop_reasons"] == [("order_build_failed: no strikes", 1)]


def test_unverified_reasons_are_separated_from_screening_results():
    """A name rejected 'iv_rv_ratio_unverified' was never judged against the bar. Reading it as a
    failure turns a data outage into an apparently reasoned decision."""
    rows = [_rejected(["iv_rv_ratio_unverified", "winrate_unverified"])]
    gaps = {g["reason"]: g["count"] for g in sm.coverage_gaps(rows)}
    assert gaps == {"iv_rv_ratio_unverified": 1, "winrate_unverified": 1}


def test_cooccurrence_shows_when_one_gate_never_fires_without_another():
    rows = [_rejected(["no_weekly_options", "front_expiration_days_too_far_out"]) for _ in range(3)]
    rows.append(_rejected(["no_weekly_options"], symbol="SOLO"))
    pair = sm.cooccurrence(rows)[0]
    assert (pair["a"], pair["b"]) == ("front_expiration_days_too_far_out", "no_weekly_options")
    assert pair["together"] == 3
    assert pair["a_alone"] == 0  # front_expiration never fires on its own
    assert pair["b_alone"] == 1
