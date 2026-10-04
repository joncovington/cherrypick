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

from cherrypick.core.ledgers import SCHEMA_ALIASES, canonical_schema

# One entry per paper-DB schema in the suite. Keys of every surface registry must match. Ids only:
# a retired spelling (`pmcc_99`) is resolved by `canonical` and never keys a registry.
SCHEMAS = ("meic_ic", "earnings", "fly_book", "dc_week", "pmcc", "curve_vx", "bwb_132")


def canonical(schema: str | None) -> str | None:
    """The configured `paper.trade_schema` as the id the registries are keyed by. Every dispatch
    read of `trade_schema` goes through this (tests/test_schema_registry.py scans for it), so a
    config written before a rename keeps reading -- `pmcc_99`, every install before 2026-10-04."""
    return canonical_schema(schema)


__all__ = ["SCHEMAS", "SCHEMA_ALIASES", "canonical"]
