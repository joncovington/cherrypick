"""Cherrypick orchestration package.

Cherrypick is an *umbrella orchestrator* that drives sibling trading modules (MEICAgent,
EarningsAgent) in place for unattended PAPER data collection. It never modifies a module's
internals, never touches live trading, and never sits on any module's loop decision path.

The prime directive: a user sets up paper plans, walks away, and trusts that any failure is
either *notified* or, at an absolute floor, *warned through logging*.
"""

ROOT_PACKAGE = "cherrypick"
