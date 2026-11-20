"""The Builder's words agree with the engine's tables both ways: every choice the schema offers is
worded, every slot is a key the choice takes, and every key it takes has a place in its line."""

from __future__ import annotations

import re

import pytest

from cherrypick.automations import builderform, words

SLOT = re.compile(r"\{([a-z0-9_.]+)\}")


def _slots(text: str) -> set[str]:
    return set(SLOT.findall(text))


def _families(s: dict) -> dict[str, tuple[dict, dict, dict, set[str]]]:
    """Each worded family: (its words, {choice: {key: required}}, {choice: {key: type}}, reserved slots)."""
    t = s["types"]
    gate_keys = {g["gate"]: {p["name"]: p["required"] for p in g["params"]} for g in s["gates"]}
    exit_keys = {
        e: ({f"{e}.{k}": r for k, r in words.EXIT_PARTS[e].items()} if e in words.EXIT_PARTS else {e: True})
        for e in s["exits"]
    }
    strat = s["strategy"]
    strat_keys = {
        k: {x: True for x in v["settings"] if x not in words.STRATEGY_EXPIRY_SETTINGS}
        for k, v in strat["strategies"].items()
    }
    sig_types = t["signals"]["kinds"]
    return {
        "entry": (words.ENTRY, s["entry"], t["entry"], set()),
        "trigger": (words.TRIGGER, s["trigger_forms"], {k: t["trigger"] for k in s["trigger_forms"]}, set()),
        "signals": (
            words.SIGNALS,
            {k: {p: v["required"] for p, v in kind.items()} for k, kind in sig_types.items()},
            sig_types,
            set(),
        ),
        "signal_rules": (
            words.SIGNAL_RULES,
            {r: {r: True} for r in s["signal_rules"]},
            {r: {r: v} for r, v in t["signals"]["rules"].items()},
            set(),
        ),
        "sources": (words.SOURCES, {k: dict.fromkeys(v, True) for k, v in s["sources"].items()}, {}, set()),
        "gates": (words.GATES, gate_keys, t["gates"], {words.RANGE}),
        "exits": (words.EXITS, exit_keys, {e: {e: t["exit"][e]} for e in s["exits"]}, set()),
        "strikes": (
            words.STRIKES,
            {k: v["keys"] for k, v in s["strike_rules"].items()},
            {k: t["strike"] for k in s["strike_rules"]},
            set(),
        ),
        "expiry": (
            words.EXPIRY,
            {**s["expiry"], "after_near": s["far_expiry"]["after_near"]},
            {k: t["expiry"] for k in [*s["expiry"], "after_near"]},
            set(),
        ),
        "sizes": (
            words.SIZES,
            {k: {x: x == k for x in keys if x != "progression"} for k, keys in s["sizes"].items()},
            {k: t["size"] for k in s["sizes"]},
            set(),
        ),
        "progression": (
            {"progression": words.PROGRESSION},
            {"progression": dict.fromkeys(s["progression"], True)},
            {},
            set(),
        ),
        "limits": (
            words.LIMITS,
            {k: {k: True} for k in s["limits"]},
            {k: t["limits"] for k in s["limits"]},
            set(),
        ),
        "strategies": (
            words.STRATEGIES,
            strat_keys,
            {k: strat["settings"] for k in strat_keys},
            {words.SYMBOL},
        ),
    }


def _covers(slot: str, key: str) -> bool:
    return slot == key or slot.startswith(key + ".")


def _problems(s: dict) -> list[str]:
    out: list[str] = []
    for fam, (table, offered, types, reserved) in _families(s).items():
        if set(table) != set(offered):
            out.append(f"{fam}: worded {sorted(set(table) ^ set(offered))} differ from the schema's")
        for name in set(table) & set(offered):
            w, keys, typed = table[name], offered[name], types.get(name, {})
            where = f"{fam}.{name}"
            said = _slots(w.get("sentence", ""))
            more = w.get("more", {})
            if not w.get("sentence"):
                out.append(f"{where}: no sentence")
            for slot in said | {sl for p in more.values() for sl in _slots(p)}:
                if slot not in reserved and not any(_covers(slot, k) for k in keys):
                    out.append(f"{where}: slot {{{slot}}} is no key it takes")
            for k, phrase in more.items():
                if not any(_covers(k, key) for key in keys):
                    out.append(f"{where}.more: {k} is no key it takes")
                if _slots(phrase) != {k}:
                    out.append(
                        f"{where}.more.{k}: its phrase holds {sorted(_slots(phrase))}, not its own slot"
                    )
            ranged = words.RANGE in reserved and {"min", "max"} <= set(keys)
            if ranged and ({"min", "max"} & said or words.RANGE not in said):
                out.append(f"{where}: a min and a max read as one {{range}}")
            in_line = said | ({"min", "max"} if words.RANGE in said else set())
            start = w.get("start", {})
            for k, required in keys.items():
                if not any(_covers(p, k) or _covers(k, p) for p in in_line | set(more)):
                    out.append(f"{where}: {k} has no place in its line")
                elif required and k not in start and not any(_covers(p, k) or _covers(k, p) for p in in_line):
                    out.append(f"{where}: {k} is required, so its slot is in the sentence or it has a start")
            for k in start:
                if k not in more:
                    out.append(f"{where}.start: {k} is not under More options")
            values = w.get("values", {})
            for k in values:
                if k not in keys:
                    out.append(f"{where}.values: {k} is no key it takes")
            for k, ty in typed.items():
                if k not in keys:
                    continue
                if ty.get("type") == "bool" and set(values.get(k, {})) != {"true", "false"}:
                    out.append(f"{where}: flag {k} reads both ways")
                if ty.get("type") == "enum":
                    odd = [o for o in ty["options"] if "_" in str(o) and o not in values.get(k, {})]
                    if odd:
                        out.append(f"{where}: {k}'s options {odd} are worded")
            for need in (
                ("label", "about")
                if fam in ("entry", "trigger", "signals", "gates", "exits", "strikes", "expiry", "sizes")
                else ("label",)
                if fam != "strategies"
                else ()
            ):
                if not w.get(need):
                    out.append(f"{where}: no {need}")
            if fam in ("signals", "sources", "gates", "exits", "strikes") and not w.get("group"):
                out.append(f"{where}: no group")
            if fam == "strategies" and w.get("group") not in words.STRATEGY_GROUPS:
                out.append(f"{where}: group {w.get('group')!r} is not a listed group")
    return out


def test_every_choice_the_form_offers_is_worded_and_every_slot_is_its_own_key():
    assert _problems(builderform.schema()) == []


def _source_choice_gaps() -> list[str]:
    """Each indicator key core holds to a few words, without every one of them worded."""
    from cherrypick.core import signals

    held = {
        "price": signals._PRICES,
        "weight": ("volume", "equal"),
        "period": signals._PERIODS,
        "level": signals._LEVELS,
    }
    out = []
    for name, keys in signals._SOURCES.items():
        sides = ("upper", "lower") if name == "vwap_band" else ("high", "low")
        for k in keys:
            want = signals._OUTPUTS[name] if k == "output" else sides if k == "side" else held.get(k)
            if want is not None and set(words.SOURCES[name].get("values", {}).get(k, {})) != set(want):
                out.append(f"{name}.{k}")
    return out


def test_an_indicator_key_held_to_a_few_words_offers_them_all():
    assert _source_choice_gaps() == []


def test_the_indicator_check_catches_a_missing_choice(monkeypatch):
    import copy

    monkeypatch.setattr(words, "SOURCES", copy.deepcopy(words.SOURCES))
    words.SOURCES["prior_period"]["values"].pop("period")
    assert _source_choice_gaps() == ["prior_period.period"]


def _row_gaps() -> list[str]:
    """A filter stage with no tag, or an exit row naming no exit group."""
    from cherrypick.automations import gates

    out = [f"stage {s}" for s in sorted(set(gates.CORE_STAGE.values()) - set(words.STAGES))]
    groups = {w["group"] for w in words.EXITS.values()}
    out += [f"row {r}" for r in words.EXIT_ROWS if r not in groups]
    return out


def test_every_filter_stage_has_its_tag_and_every_exit_row_is_a_group():
    assert _row_gaps() == []


def test_the_row_check_catches_a_missing_stage_and_a_stray_row(monkeypatch):
    monkeypatch.setattr(words, "STAGES", {k: v for k, v in words.STAGES.items() if k != "chain"})
    monkeypatch.setattr(words, "EXIT_ROWS", (*words.EXIT_ROWS, "Hedges"))
    assert _row_gaps() == ["stage chain", "row Hedges"]


def test_the_schema_carries_the_words_and_the_indicator_list():
    s = builderform.schema()
    assert s["words"] == words.schema()
    assert "sma" in s["sources"] and s["sources"]["sma"] == ["n"]
    assert set(s["words"]["sources"]) == set(s["sources"])


@pytest.mark.parametrize(
    "break_it, expect",
    [
        (lambda: words.GATES.pop("vix_level"), "gates: worded ['vix_level']"),
        (
            lambda: words.EXITS["close_at_dte"].update(sentence="Close at {close_at_days}"),
            "slot {close_at_days}",
        ),
        (lambda: words.GATES["rsi"].update(sentence="RSI({n}) is {min}"), "read as one {range}"),
        (lambda: words.STRIKES["delta"].update(sentence="close to the money"), "target has no place"),
        (
            lambda: words.STRIKES["delta"].update(
                sentence="close to the money", more={"target": "at {target}"}
            ),
            "target is required",
        ),
        (lambda: words.SIGNALS["cross"].pop("start"), "seed is required"),
        (lambda: words.SIGNALS["candle"]["values"]["pattern"].pop("inside_bar"), "['inside_bar'] are worded"),
        (lambda: words.LIMITS["one_per_expiry"].pop("values"), "flag one_per_expiry reads both ways"),
        (lambda: words.STRATEGIES["option"].update(group="Singles"), "not a listed group"),
    ],
)
def test_the_check_refuses_each_kind_of_gap(monkeypatch, break_it, expect):
    import copy

    for name in ("GATES", "EXITS", "STRIKES", "SIGNALS", "LIMITS", "STRATEGIES"):
        monkeypatch.setattr(words, name, copy.deepcopy(getattr(words, name)))
    break_it()
    assert any(expect in p for p in _problems(builderform.schema()))
