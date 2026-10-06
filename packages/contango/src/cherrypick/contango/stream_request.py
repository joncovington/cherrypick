"""Declare this module's stream needs: every arm's two funds and VIX/VIX3M, all as quote-only legs.

Writes `~/.cherrypick/state/stream_requests/contango.json`. No `symbols`: nothing here is an
underlying -- no chain, no expiration, no window -- so declaring the funds as `symbols` would have
the producer maintain option chains this module never reads (the overview 2026-08-17 incident curve
cites). Legs are re-read by the producer every poll, so a new fund needs no restart.

Best-effort by design: a failed write must never break the paper loop.
"""

from __future__ import annotations

import logging
from pathlib import Path

from cherrypick.core import config as _cfg
from cherrypick.core import streamrequests as _sr

_MODULE = "contango"
_log = logging.getLogger("contango_paper_loop")


def legs(config: dict) -> list[str]:
    """Each declared arm's risk and cash fund (disabled arms too: an arm switched off mid-stint still
    holds shares someone may want marked), plus the two indexes."""
    defaults = config.get("defaults") or {}
    out = {"VIX", "VIX3M"}
    blocks = _cfg.registry(config, label="contango") or {"control": {}}
    for block in blocks.values():
        merged = {**defaults, **(block or {})}
        out.add(str(merged.get("risk_symbol", "SVXY")).upper())
        out.add(str(merged.get("cash_symbol", "SHV")).upper())
    return sorted(out)


def write(config: dict) -> Path:
    return _sr.write_request(_MODULE, [], legs=legs(config), history_days={"VIX": 270, "VIX3M": 270})


def register(config: dict) -> None:
    """Best-effort: never raises into the caller."""
    _sr.register_best_effort(write, config, log=_log)
