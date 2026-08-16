"""The canonical paper trade-schema set — one place, enforced by test, not by prose.

Every read surface dispatches on `paper.trade_schema`. The doc rule used to be "extend all
the registries together", which drifted the way prose rules do: the audit found FIVE
registries plus an alias where the doc said four, with eval_activity silently returning
None for a schema it never wired. This module is the single source of truth; the coverage
test (tests/test_schema_registry.py) asserts every surface accounts for every schema —
either with a reader or with an explicit not-applicable declaration. Adding a schema here
without extending a surface fails CI instead of vanishing silently from that surface.
"""

from __future__ import annotations

# One entry per paper-DB schema in the suite. Keys of every surface registry must match.
SCHEMAS = ("meic_ic", "earnings", "fly_book", "dc_week", "pmcc_99")
