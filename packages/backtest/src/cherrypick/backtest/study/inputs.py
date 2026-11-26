"""A study reads only the chain paths it declared (#1117).

The end-of-day chain store is shared: every study's fetches land in it. A study that read every path
the store holds for its names would read more as other studies fetched more, and its result would
move with no change to its code (the first reproduce run found item 70 doing exactly that). So a
study on the kit reads through `Inputs`, built from its own requests file (the plan step's output,
`fetch-chain-paths`' input): a path it did not request is refused, and a session outside a requested
path's dates is not read.

`fingerprint` records what the study read, as counts and a hash of them (never a price), so a
result says which inputs it rests on and the reproduce step can tell "the inputs changed" from "the
code changed": a study writes it into its result as `inputs`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

from cherrypick.core import calendar as cal

from cherrypick.backtest.chainfetch import read_path_requests

from .chains import Chain


def _trading_day(session: str) -> bool:
    """Whether a stored session is an exchange trading day; one that is not a date is kept, so a
    malformed row shows up downstream rather than vanishing here."""
    try:
        return cal.is_trading_day(date.fromisoformat(session[:10]))
    except ValueError:
        return True


class Undeclared(KeyError):
    """A path the study's requests file does not name."""


class Inputs:
    """The chain paths one study declared, read from `requests` (`{"requests": [{symbol, expiration,
    start, through}]}`)."""

    def __init__(self, requests: Path | str):
        self.file = Path(requests)
        self.keys = read_path_requests(self.file)
        self.windows: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for sym, exp, start, through in self.keys:
            self.windows.setdefault((sym, exp), []).append((start, through))

    def _window(self, symbol: str, expiry: str) -> list[tuple[str, str]]:
        w = self.windows.get((symbol.upper(), expiry[:10]))
        if w is None:
            raise Undeclared(f"{symbol} {expiry} is not in {self.file.name}: the study did not request it")
        return w

    def sessions(self, conn: sqlite3.Connection, symbol: str, expiry: str) -> list[str]:
        """The stored sessions of a declared path, inside its requested dates, in order. Only exchange
        trading days: the vendor answers a day it has none for (a Saturday, a holiday) with a stored
        row of its own, which a study would read as a session and mark a position on."""
        out: set[str] = set()
        for start, through in self._window(symbol, expiry):
            out.update(
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT session FROM eod_chain_quotes WHERE symbol = ? AND expiration = ? "
                    "AND session BETWEEN ? AND ?",
                    (symbol.upper(), expiry[:10], start, through),
                )
            )
        return sorted(s for s in out if _trading_day(s))

    def chain(self, conn: sqlite3.Connection, symbol: str, session: str, expiry: str) -> Chain | None:
        """One session's chain of a declared path, or None when the store holds none for it, or the day
        is not a trading day (the stored row is the vendor's placeholder, never a market)."""
        if not any(start <= session <= through for start, through in self._window(symbol, expiry)):
            raise Undeclared(f"{symbol} {expiry} on {session} is outside the dates the study requested")
        if not _trading_day(session):
            return None
        rows = conn.execute(
            "SELECT strike, right, bid, ask FROM eod_chain_quotes "
            "WHERE symbol = ? AND session = ? AND expiration = ?",
            (symbol.upper(), session, expiry[:10]),
        ).fetchall()
        return Chain(rows, session, expiry[:10]) if rows else None

    def fingerprint(self, conn: sqlite3.Connection) -> dict:
        """What the declared paths hold now: per path its recorded status, sessions and rows (from
        `eod_chain_paths`), summed, and a hash of the per-path tuples. Counts only. These are the
        fetch's own counts: a stored non-trading day is counted here though `sessions` leaves it out."""
        per = []
        for sym, exp, start, through in self.keys:
            r = conn.execute(
                "SELECT status, sessions, rows FROM eod_chain_paths WHERE symbol = ? AND expiration = ? "
                "AND start = ? AND through = ?",
                (sym, exp, start, through),
            ).fetchone()
            per.append([sym, exp, start, through, *(r if r else ("missing", 0, 0))])
        digest = hashlib.sha256(json.dumps(per, separators=(",", ":")).encode()).hexdigest()
        return {
            "requests": self.file.name,
            "paths": len(per),
            "complete": sum(1 for p in per if p[4] == "complete"),
            "sessions": sum(int(p[5]) for p in per),
            "rows": sum(int(p[6]) for p in per),
            "sha256": digest,
        }
