"""The alias layer, and the distinction it exists to draw.

Every config read in the suite is `.get(key, default)`, so a key that moves produces no error --
the registry resolves empty, every arm falls through to its `enabled` default, and the loop runs
every arm on `defaults`. All arms become identical and the A/B measures nothing, with plausible
P&L. These pin the one thing that makes that detectable: absent and declared-empty are different
facts and only one of them is worth a warning.
"""

from __future__ import annotations

import pytest

from cherrypick.core import config


@pytest.fixture(autouse=True)
def _fresh_warnings():
    config._reset_warnings()
    yield
    config._reset_warnings()


class TestFirstPresent:
    def test_takes_the_first_spelling_that_is_present(self):
        assert config.first_present({"base_book": "control"}, *config.BASE_ARM_KEYS) == "control"
        assert config.first_present({"base_arm": "wide"}, *config.BASE_ARM_KEYS) == "wide"

    def test_prefers_the_canonical_spelling_when_a_config_carries_two(self):
        doc = {"base_book": "old", "base_arm": "new"}
        assert config.first_present(doc, *config.BASE_ARM_KEYS) == "new"

    def test_present_beats_truthy(self):
        # A config that says base_arm is "" has answered. Falling through to base_book would read
        # a different key than the operator wrote.
        assert config.first_present({"base_arm": "", "base_book": "control"}, *config.BASE_ARM_KEYS) == ""

    def test_missing_gives_the_default(self):
        assert config.first_present({}, *config.BASE_ARM_KEYS, default="control") == "control"
        assert config.first_present(None, *config.BASE_ARM_KEYS, default="control") == "control"


class TestRegistry:
    def test_reads_every_spelling_the_modules_ship(self):
        arms = {"control": {"enabled": True}}
        assert config.registry({"arms": arms}, label="flies") == arms
        assert config.registry({"books": arms}, label="bwb") == arms
        assert config.registry({"profiles": arms}, label="meic") == arms

    def test_declared_empty_is_silent(self):
        """An operator turning every arm off is an intention, not a defect."""
        said = []
        assert config.registry({"books": {}}, label="bwb", log=said.append) == {}
        assert said == []

    def test_absent_warns_and_names_the_spellings(self):
        """Nothing declared is the case where the caller proceeds on defaults believing it was told to."""
        said = []
        assert config.registry({"defaults": {}}, label="bwb", log=said.append) == {}
        assert len(said) == 1
        assert "bwb" in said[0]
        assert "arms" in said[0] and "books" in said[0]

    def test_warns_once_per_label_so_a_per_tick_loop_cannot_spam(self):
        said = []
        for _ in range(50):
            config.registry({}, label="bwb", log=said.append)
        assert len(said) == 1

    def test_a_non_mapping_registry_is_not_a_registry(self):
        assert config.registry({"books": ["control"]}, label="bwb") == {}
        assert config.registry(None, label="bwb") == {}

    def test_keys_are_strings_whatever_json_produced(self):
        out = config.registry({"arms": {1: {"enabled": True}}}, label="x")
        assert list(out) == ["1"]


def test_the_base_alias_list_matches_the_one_core_advice_has_always_used():
    """`core.advice._legacy_base` is the mechanism this generalises; if the two lists diverge, a
    decision written under one spelling stops being readable under the other."""
    from cherrypick.core import advice

    for key in config.BASE_ARM_KEYS:
        assert advice._legacy_base({key: "sentinel"}) == "sentinel", f"{key} not honoured by core.advice"
