"""The underlyings added to the full set on this machine (`fullset`'s additions).

`additions.json` in the set's folder (`fullsetprogress.folder()`, under the licensed results):
`{symbol: {"from": first session, "added": the day it was first added}}`. It is machine state, never
in the repo: which names someone backtests is theirs. An addition's data goes into the three shared
stores (`fullset.ADDED_*`), keyed by its symbol, so adding one declares no new file.

Adding a symbol again with an earlier `from` extends it back; a later one changes nothing.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from cherrypick.core import jsonio

from . import fullset, fullsetprogress

FILE = "additions.json"


def path(where: Path | None = None) -> Path:
    return (fullsetprogress.folder() if where is None else where) / FILE


def load(where: Path | None = None) -> dict[str, dict]:
    """The additions, by symbol; none when the file is absent."""
    p = path(where)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def merged(current: dict[str, dict], symbol: str, start: str, today: date) -> dict[str, dict]:
    """`current` with `symbol` added from `start` (or extended back to it). Pure; refuses a symbol of
    the seven or one that cannot be a root, and a start before the set's own."""
    if symbol in fullset.SPEC:
        raise ValueError(f"{symbol} is one of the set's own underlyings")
    if not fullset.SYMBOL.match(symbol):
        raise ValueError(f"{symbol!r} is not an underlying's symbol")
    if start < fullset.START:
        raise ValueError(f"the set starts {fullset.START}; {start} is before it")
    out = {k: dict(v) for k, v in current.items()}
    entry = out.get(symbol)
    if entry is None:
        out[symbol] = {"from": start, "added": today.isoformat()}
    elif start < entry["from"]:
        entry["from"] = start
    return out


def add(symbol: str, start: str, today: date, where: Path | None = None) -> dict[str, dict]:
    """Record `symbol` as added from `start` and return the additions. Written atomically."""
    doc = merged(load(where), symbol, start, today)
    p = path(where)
    p.parent.mkdir(parents=True, exist_ok=True)
    jsonio.write_json_atomic(p, doc)
    return doc
