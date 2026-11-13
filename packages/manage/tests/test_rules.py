import json
from pathlib import Path

from cherrypick.manage import rules as _rules

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"


def _strip(node):
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if not k.startswith("_")}
    return node


def test_example_documents_exactly_the_default():
    # The example is the operator's reference; a default changed in code but not here would mislead.
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert _strip(example) == _strip(_rules.DEFAULT_RULES)


def test_override_changes_one_threshold_and_keeps_the_rest():
    rules, warnings = _rules.effective({"credit": {"take_profit": {"capture": 0.6}}})
    assert warnings == []
    assert rules["credit"]["take_profit"]["capture"] == 0.6
    assert rules["credit"]["take_profit"]["evidence"] == "tested"
    assert rules["credit"]["stop"] == _rules.DEFAULT_RULES["credit"]["stop"]


def test_unknown_keys_and_rule_ids_are_reported_not_applied():
    rules, warnings = _rules.effective(
        {"credit": {"take_profitt": {"capture": 0.6}}, "positions": {"SPY": {"disable": ["debit.stopp"]}}}
    )
    assert "credit.take_profitt: unknown key, ignored" in warnings
    assert any("debit.stopp" in w for w in warnings)
    assert rules["credit"]["take_profit"]["capture"] == 0.5


def test_a_trades_entry_that_does_nothing_says_so():
    _, warnings = _rules.effective(
        {
            "trades": {
                "XYZ 280121C00040000": {"intent": "pmcc", "note": "ok"},
                "XYZ   280121C00040000": {"intent": "hedge", "disable": ["debit.stop"]},
                "XYZ": {"intent": "pmcc"},
                "XYZ 280121P00040000": {"intent": "pmcc"},
            }
        }
    )
    assert warnings == [
        "trades.XYZ   280121C00040000.disable: unknown key, ignored",
        "trades.XYZ   280121C00040000.intent: 'hedge' is not pmcc or diagonal, ignored",
        "trades.XYZ: neither a trade ID (e.g. 'XYZ-260401-1') nor a call contract "
        "(e.g. 'XYZ 280121C00040000'), ignored",
        "trades.XYZ 280121P00040000: neither a trade ID (e.g. 'XYZ-260401-1') nor a call contract "
        "(e.g. 'XYZ 280121C00040000'), ignored",
    ]


def test_a_trade_id_entry_takes_intent_disable_and_note():
    rules, warnings = _rules.effective(
        {
            "positions": {"XYZ": {"disable": ["credit.stop"], "note": "the name", "intent": "income"}},
            "trades": {
                "XYZ-260401-1": {"intent": "hedge", "disable": ["debit.stop"], "note": "this one"},
                "XYZ-260401-2": {"continues": "XYZ 280121C00040000", "disable": ["debit.stopp"]},
            },
        }
    )
    assert warnings == [
        "trades.XYZ-260401-2.continues: unknown key for a trade ID, ignored",
        "trades.XYZ-260401-2.disable: unknown rule 'debit.stopp'",
    ]
    own = _rules.override(rules, "XYZ", "XYZ-260401-1")
    assert own["intent"] == "hedge" and own["note"] == "this one"
    assert own["disable"] == ["credit.stop", "debit.stop"]  # the name's and the trade's add up
    assert _rules.disabled(rules, "XYZ", "debit.stop", "XYZ-260401-1")
    assert not _rules.disabled(rules, "XYZ", "debit.stop", "XYZ-260402-1")  # another trade on the name
    assert _rules.disabled(rules, "XYZ", "credit.stop", "XYZ-260402-1")  # the name's still applies
    assert _rules.override(rules, "XYZ")["intent"] == "income"


def test_a_continues_must_name_another_call_contract():
    _, warnings = _rules.effective(
        {
            "trades": {
                "XYZ 280616C00040000": {"continues": "XYZ 280121C00040000"},
                "XYZ 280617C00040000": {"continues": "XYZ  280617C00040000"},
                "XYZ 280618C00040000": {"continues": "XYZ"},
            }
        }
    )
    assert warnings == [
        "trades.XYZ 280617C00040000.continues: a long cannot continue itself, ignored",
        "trades.XYZ 280618C00040000.continues: not a call contract, ignored",
    ]


def test_a_shape_target_may_be_added_changed_or_misspelt():
    rules, warnings = _rules.effective(
        {
            "credit": {
                "take_profit": {
                    "by_shape": {
                        "iron condor": {"capture": 0.6},
                        "1-3-2": {"capture": 0.4, "evidence": "ours"},
                        "short straddle": {"capture": 0.3},
                        "iron condr": {"capture": 0.6},
                        "short strangle": {"captur": 0.6},
                    }
                }
            }
        }
    )
    by_shape = rules["credit"]["take_profit"]["by_shape"]
    assert by_shape["iron condor"] == {"capture": 0.6}
    assert by_shape["1-3-2"] == {"capture": 0.4, "evidence": "ours"}
    assert by_shape["short straddle"] == {"capture": 0.3, "evidence": "practice"}
    assert (
        by_shape["iron butterfly"]
        == _rules.DEFAULT_RULES["credit"]["take_profit"]["by_shape"]["iron butterfly"]
    )
    assert "iron condr" not in by_shape
    assert "credit.take_profit.by_shape.iron condr: unknown shape, ignored" in warnings
    assert "credit.take_profit.by_shape.short strangle.captur: unknown key, ignored" in warnings
    # A field the entry leaves out is the rule's own.
    assert _rules.for_shape(rules["credit"]["take_profit"], "iron condor", "capture") == (0.6, "tested", True)
    assert _rules.for_shape(rules["credit"]["take_profit"], "custom", "capture") == (0.5, "tested", False)


def test_unreadable_override_file_keeps_the_default(tmp_path):
    bad = tmp_path / "manage.json"
    bad.write_text("{not json", encoding="utf-8")
    rules, warnings, source = _rules.load(bad)
    assert rules == _rules.DEFAULT_RULES
    assert "unreadable" in warnings[0]
    assert source == str(bad)


def test_disabled_honours_section_switches_and_position_lists():
    rules, _ = _rules.effective(
        {"risk": {"enabled": False}, "positions": {"KO": {"disable": ["debit.stop"]}}}
    )
    assert _rules.disabled(rules, "ANY", "risk.oversize")
    assert _rules.disabled(rules, "KO", "debit.stop")
    assert not _rules.disabled(rules, "SPY", "debit.stop")
