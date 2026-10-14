from test_evaluate import leg

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules


def snapshot(underlyings):
    return {
        "session": "2026-10-13",
        "positions": {"ok": True, "accounts": [{"balances": {}, "underlyings": underlyings}]},
        "metrics": {"SPX": {"last": 7805.45}, "XYZ": {"last": 100}},
    }


SPX_0DTE = [
    leg(-2, "P", 7795, "2026-10-13", 4.85, 0.025, root="SPXW", underlying="SPX"),
    leg(2, "P", 7800, "2026-10-13", 5.725, 0.025, root="SPXW", underlying="SPX"),
]


def test_spx_0dte_is_left_out_and_says_so():
    out = ev.evaluate(snapshot([{"underlying": "SPX", "legs": SPX_0DTE}]), _rules.effective(None)[0])
    account = out["accounts"][0]
    assert account["trades"] == []
    assert account["excluded"] == [{"underlying": "SPX", "legs": 2, "reason": "0DTE excluded by the ruleset"}]


def test_later_spx_expiries_are_still_evaluated():
    later = leg(-1, "P", 7700, "2026-11-20", 40.0, 30.0, root="SPXW", underlying="SPX")
    out = ev.evaluate(
        snapshot([{"underlying": "SPX", "legs": [*SPX_0DTE, later]}]), _rules.effective(None)[0]
    )
    (trade,) = out["accounts"][0]["trades"]
    assert [leg["label"] for leg in trade["legs"]] == ["-1 2026-11-20 7700P"]


def test_the_exclusion_list_is_configurable():
    rules, warnings = _rules.effective({"exclude": {"zero_dte_underlyings": []}})
    assert warnings == []
    out = ev.evaluate(snapshot([{"underlying": "SPX", "legs": SPX_0DTE}]), rules)
    assert out["accounts"][0]["excluded"] == []
    assert len(out["accounts"][0]["trades"]) == 1


def test_other_underlyings_are_untouched():
    xyz = [leg(-1, "P", 95, "2026-10-13", 1.0, 0.5)]
    out = ev.evaluate(snapshot([{"underlying": "XYZ", "legs": xyz}]), _rules.effective(None)[0])
    assert len(out["accounts"][0]["trades"]) == 1
