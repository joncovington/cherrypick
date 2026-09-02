"""Data-home path resolution for cherrypick-advisor.

A thin facade over :mod:`cherrypick.core.home`, the suite-wide resolver, exactly as every other
module has. The advisor writes only into its own home (``~/.cherrypick/data/advisor`` by default, or
``ADVISOR_DATA_DIR``) plus the shared advice artifacts at ``<state>/advice/`` that the loops already
read. It reads every other module's data read-only and writes to none of it.

Portability guardrail: never hardcode an absolute path -- the home derives from ``Path.home()`` or
the documented overrides.
"""

from __future__ import annotations

from pathlib import Path

from cherrypick.core import advice as _advice
from cherrypick.core import home as _home

PACKAGE = "advisor"


def data_dir() -> Path:
    """The advisor's own store: advisor.db, fact packs, raw replies, checkpoint summaries."""
    return _home.data_dir(PACKAGE, env="ADVISOR_DATA_DIR")


def logs_dir() -> Path:
    return _home.logs_dir(PACKAGE, env="ADVISOR_LOGS_DIR")


def db_path() -> Path:
    """Mutable advisor state — checkpoints, proposals, experiments, journal."""
    return data_dir() / "advisor.db"


def packs_dir() -> Path:
    return data_dir() / "packs"


def checkpoints_dir() -> Path:
    return data_dir() / "checkpoints"


def pack_path(session: str, slot: str) -> Path:
    """One fact pack per (session, slot) — write-once, the input the model actually saw."""
    return packs_dir() / f"{session}-{slot}.json"


def raw_path(session: str, slot: str) -> Path:
    """The model's raw reply, kept beside the parsed summary so a parse failure stays diagnosable."""
    return checkpoints_dir() / f"{session}-{slot}.raw.txt"


def checkpoint_path(session: str, slot: str) -> Path:
    """The admitted summary for a slot. Its existence is what freezes the slot against a re-run."""
    return checkpoints_dir() / f"{session}-{slot}.json"


def state_dir() -> Path:
    """The orchestrator's state home — where ``advice/`` lives. The advisor writes exactly one kind
    of file under it (the per-module, per-session advice artifact) and nothing else."""
    return _home.state_dir()


def advice_path(module: str, session: str) -> Path:
    """``<state>/advice/<module>-<session>.json`` — resolved through core so producer and consumer
    can never disagree about where the artifact lives."""
    return _advice.advice_path(state_dir(), module, session)


def module_data_dir(module: str) -> Path:
    """Another module's data home, resolved through the same resolver that module writes it with.
    Read-only from here — see the package guardrails."""
    return _home.data_dir(module)


def module_config_path(module: str) -> Path:
    """A module's deployed config (``~/.cherrypick/config/<module>.json``). Read-only from here: the
    advisor reads its ``advice`` block for bounds and never writes a config."""
    return _home.config_path(module)
