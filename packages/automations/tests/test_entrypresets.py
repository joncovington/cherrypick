"""The Builder's entry presets: every entry, alone and with every filter stacked on it, writes an entry
and gates the engine loads as they stand."""

from __future__ import annotations

import itertools

import pytest

from cherrypick.automations import builderform, config, entrypresets, strategies

LEGS = strategies.build("vertical")["legs"]


def _combos(table: dict, name: str):
    """Every value of the enum settings the preset takes, the rest at their defaults."""
    keys = [k for k in table[name]["settings"] if entrypresets.SETTINGS[k]["type"] == "enum"]
    for values in itertools.product(*(entrypresets.SETTINGS[k]["options"] for k in keys)):
        yield dict(zip(keys, values, strict=True))


def _load(entry: dict, gates: list[dict]) -> dict:
    spec = {**builderform.WIZARD_DEFAULTS, "legs": LEGS, "entry": entry, "gates": gates}
    return config.load_arms({"automations": {"try-it": spec}})["try-it"]


@pytest.mark.parametrize("name", list(entrypresets.ENTRIES))
def test_every_entry_at_every_setting_is_an_automation_the_engine_loads(name):
    for given in _combos(entrypresets.ENTRIES, name):
        built = entrypresets.build_entry(name, given)
        assert _load(built["entry"], [])["entry"] == built["entry"], (name, given)


@pytest.mark.parametrize("name", list(entrypresets.FILTERS))
def test_every_filter_stacks_on_every_entry(name):
    gates = entrypresets.build_filter(name)["gates"]
    for entry in entrypresets.ENTRIES:
        loaded = _load(entrypresets.build_entry(entry)["entry"], gates)
        assert [g["gate"] for g in loaded["gates"]] == [g["gate"] for g in gates], (name, entry)


def test_all_the_filters_stack_together():
    gates = [g for f in entrypresets.FILTERS for g in entrypresets.build_filter(f)["gates"]]
    loaded = _load(entrypresets.build_entry("rsi_pullback")["entry"], gates)
    assert len(loaded["gates"]) == len(gates)


def test_the_pullback_is_the_one_its_name_says():
    entry = entrypresets.build_entry("rsi_pullback")["entry"]
    t = entry["trigger"]
    assert entry["kind"] == "trigger" and t["daily"] and t["developing"] and t["within"] is None
    assert t["window"] == {"open": "15:50", "close": "15:59"}
    assert t["signal"]["a"] == {"name": "rsi", "n": 2} and (t["signal"]["low"], t["signal"]["high"]) == (
        0,
        10,
    )
    assert t["signal"]["only_when"][0]["b"] == {"name": "sma", "n": 200}
    assert t["signal"]["only_when"][0]["direction"] == "above"
    over = entrypresets.build_entry("rsi_overbought")["entry"]["trigger"]["signal"]
    assert (over["low"], over["high"], over["only_when"][0]["direction"]) == (90, 100, "below")


def test_a_filter_writes_the_gate_and_bounds_its_label_names():
    assert entrypresets.build_filter("vix_band", {"vix_min": 12, "vix_max": 25})["gates"] == [
        {"gate": "vix_level", "min": 12, "max": 25}
    ]
    assert entrypresets.build_filter("calm_open")["gates"] == [
        {"gate": "gap_pct", "min": -0.5, "max": 0.5},
        {"gate": "vix_change_pct", "max": 5},
    ]


@pytest.mark.parametrize(
    "kind,name,given,word",
    [
        ("entry", "nope", {}, "entry"),
        ("entry", "daily_time", {"at": "9:30"}, "at"),
        ("entry", "daily_time", {"at": "16:30"}, "at"),
        ("entry", "near_close", {"at": "15:58", "window": 5}, "window"),
        ("entry", "rsi_pullback", {"below": 101}, "below"),
        ("entry", "rsi_pullback", {"rsi_n": 2.5}, "rsi_n"),
        ("entry", "ma_cross", {"fast": 21, "slow": 9}, "slow"),
        ("entry", "ma_cross", {"from": "14:00", "until": "10:00"}, "until"),
        ("entry", "orb", {"minutes": 60, "latest": "10:00"}, "latest"),
        ("entry", "after_event", {"event": "Nope"}, "event"),
        ("filter", "vix_band", {"vix_min": 30, "vix_max": 15}, "vix_max"),
        ("filter", "iv_rank", {"rank_min": True}, "rank_min"),
        ("filter", "nope", {}, "filter"),
    ],
)
def test_a_setting_it_cannot_build_from_is_refused_by_name(kind, name, given, word):
    build = entrypresets.build_entry if kind == "entry" else entrypresets.build_filter
    with pytest.raises(entrypresets.Refused, match=word):
        build(name, given)


def test_the_schema_lists_every_preset_with_settings_it_describes():
    s = entrypresets.schema()
    assert set(s["entries"]) == set(entrypresets.ENTRIES) and set(s["filters"]) == set(entrypresets.FILTERS)
    for p in [*s["entries"].values(), *s["filters"].values()]:
        assert p["label"] and p["group"] and all(k in s["settings"] for k in p["settings"])
