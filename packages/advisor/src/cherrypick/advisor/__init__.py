"""cherrypick.advisor — the deterministic half of the AI advisor.

The advisor observes the paper books through the day, proposes changes, and runs those proposals as
paper A/B experiments beside their controls. This package holds **everything deterministic** about
that: the fact packs the model reads, the parse-and-validate of what it replies, the experiment
lifecycle, and the nightly issuing of bounded advice artifacts through
:mod:`cherrypick.core.advice`.

It never invokes AI and never opens a socket. The one AI touchpoint is
``scripts/advisor_checkpoint.py``, outside every package — the same fence that holds
``scripts/eod_narrative.py``, for the same reason: ``packages/*`` is what the trading loops import.

It never touches a live account. Live facts are read (read-only, clearly labeled) so the model has
context; the only thing this package can emit toward a loop is a paper advice artifact.
"""

from __future__ import annotations

__all__ = ["PACKAGE"]

PACKAGE = "advisor"
