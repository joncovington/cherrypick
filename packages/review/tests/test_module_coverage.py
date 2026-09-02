"""Every module with a ledger reader appears in the review, or is excluded on purpose.

This is the check that would have caught the gap it was written for. bwb and curve had SQLite
ledgers, `cherrypick.core.ledgers` readers, console pages and watchdog entries from the day they
landed — and were never added to `facts.MODULES`. The suite's cross-module end-of-day review, the
thing whose whole reason for existing is that answering "what did the suite do today" per-package
produced six incomparable report families, did not know they existed. bwb was carrying twelve open
positions at the time.

Driven off `core.ledgers.READERS` rather than a list kept here: a module is covered the moment it
gains a normalised reader, which is the earliest point at which the review COULD have read it.
"""

from __future__ import annotations

import pytest
from cherrypick.core import ledgers as _ledgers

from cherrypick.review import facts

# Schemas deliberately outside the review, each with the reason. Empty today. A schema belongs here
# only when the review genuinely should not cover it — not merely because nobody wired it yet, which
# is the thing this file exists to stop.
EXCLUDED: dict[str, str] = {}


def test_every_ledger_schema_is_reviewed_or_explicitly_excluded():
    reviewed = {spec["schema"] for spec in facts.MODULES.values()}
    missing = sorted(set(_ledgers.READERS) - reviewed - set(EXCLUDED))
    assert missing == [], (
        f"these ledger schemas have a normalised reader and no review entry: {missing}. "
        "Add them to facts.MODULES, or to EXCLUDED with the reason they should not be reviewed."
    )


def test_every_reviewed_module_names_a_real_schema():
    """The other direction: a review entry pointing at a schema `core.ledgers` cannot read would
    fail at build time with a KeyError, per session, in production."""
    for module, spec in facts.MODULES.items():
        assert spec["schema"] in _ledgers.READERS, f"{module} names unknown schema {spec['schema']!r}"


@pytest.mark.parametrize("module", sorted(facts.MODULES))
def test_every_reviewed_module_has_its_health_and_expected_readers(module):
    """`build_module_facts` indexes both registries directly, so a module in MODULES without them
    raises KeyError for that module every session rather than degrading."""
    assert module in facts.HEALTH_READERS, f"{module} has no health reader"
    assert module in facts.EXPECTED_READERS, f"{module} has no expected-vs-observed reader"


def test_the_registries_do_not_carry_modules_the_review_does_not_know():
    """A reader for a module that was removed from MODULES is dead code that reads like coverage."""
    extra_health = sorted(set(facts.HEALTH_READERS) - set(facts.MODULES))
    extra_expected = sorted(set(facts.EXPECTED_READERS) - set(facts.MODULES))
    assert extra_health == [], f"health readers with no module entry: {extra_health}"
    assert extra_expected == [], f"expected readers with no module entry: {extra_expected}"
