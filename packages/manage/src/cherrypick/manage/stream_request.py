"""Declare what the account holds to the streamer, so its marks can be read live between evaluations.

Writes ``~/.cherrypick/state/stream_requests/manage.json`` from an evaluation (2026-10-22):

- ``legs`` -- every held option leg's streamer symbol, every underlying held, and the benchmark the
  portfolio is beta-weighted to. Static, quote-only legs: the producer quotes each one (an option
  carries greeks too) and keeps NO chain for any of them. A name the account holds is not a symbol
  this package trades, so it never goes in ``symbols``, which would have the producer maintain a
  chain and an ATM window for every one.
- No ``symbols``, ``leg_sources`` or windows. The legs come from the evaluation, which is rewritten at
  every run, so a closed position ages out at the next one.

This is the package's one write outside ``~/.cherrypick/data/manage``, and it is a declaration, not
data: the shared cache stays the streamer's alone. Best-effort: a failed write never fails an
evaluation.
"""

from __future__ import annotations

import logging
from pathlib import Path

from cherrypick.core import streamrequests as _sr
from cherrypick.core.streamcache import occ_to_streamer_symbol

_MODULE = "manage"
_log = logging.getLogger("manage")


def instance_of(account: dict, instances: set[str]) -> str:
    """The producer that quotes an account: its broker login's own (`stream_instances`), else main."""
    login = account.get("login") or "main"
    return login if login in instances else "main"


def held_symbols(result: dict, instance: str | None = None) -> list[str]:
    """Every streamer symbol an evaluation's marks depend on, sorted: the option legs, the
    underlyings (shares are their own streamer symbol) and each account's benchmark. With
    `instance`, only the accounts that producer quotes (`instance_of`)."""
    out: set[str] = set()
    instances = set(result.get("stream_instances") or [])
    for account in result.get("accounts") or []:
        if instance is not None and instance_of(account, instances) != instance:
            continue
        bench = (account.get("portfolio") or {}).get("benchmark")
        if bench:
            out.add(str(bench))
        for trade in account.get("trades") or []:
            if trade.get("underlying"):
                out.add(str(trade["underlying"]))
            for leg in trade.get("legs") or []:
                symbol = str(leg.get("symbol") or "")
                if leg.get("right"):
                    streamer = occ_to_streamer_symbol(symbol)
                    if streamer:
                        out.add(streamer)
                elif symbol:
                    out.add(symbol)
        # The 0DTE legs held by hand (`zerodte.py`, 2026-11-03): the ruleset leaves them out of the
        # trades, but the console marks them between runs too. The loops' own legs are theirs to declare.
        for leg in (account.get("zero_dte") or {}).get("open_legs") or []:
            streamer = occ_to_streamer_symbol(str(leg.get("symbol") or ""))
            if streamer:
                out.add(streamer)
    return sorted(out)


def write(result: dict) -> Path:
    """Main's request (`stream_requests/manage.json`, as always), then one per producer instance the
    evaluation names (`stream_requests/<login>/manage.json`) holding that login's accounts' legs. An
    instance whose accounts hold nothing gets an empty request, so legs closed since age out there
    too. Returns main's path."""
    instances = [str(i) for i in result.get("stream_instances") or []]
    path = _sr.write_request(_MODULE, [], legs=held_symbols(result, "main" if instances else None))
    for name in instances:
        _sr.write_request(_MODULE, [], legs=held_symbols(result, name), instance_name=name)
    return path


def register(result: dict) -> None:
    """Best-effort: never raises into the caller."""
    _sr.register_best_effort(write, result, log=_log)
