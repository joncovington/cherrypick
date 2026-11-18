"""A backtest result read as portfolio arms: one arm's trades, its net per session, its window.

Two result shapes carry engine trade records (`automations.engine`'s closed-trade dict):

- **A Builder run** (`scripts/automation_backtest.py`): `trades` is one list, each trade naming its
  `arm`; `sessions` is the run's [first, last] session; `licensed` says whether it read the vendor's
  store.
- **A study result** (for example item 75's): `arms.<name>.trades` per arm; `window` is [first, last];
  a `licence` note marks it derived from licensed data.

A trade's net is its `verdict` pricing kind (the suite's slippage model) when the net is a dict of
kinds, as the Builder card and paper read it, else the number as written. Each trade is dated by its
exit session (`exit_session`, else `session`), as a ledger's close date is.

Pure functions over a parsed result; reading the file is the CLI's job.
"""

from __future__ import annotations

from collections.abc import Mapping

#: The pricing kind a backtest trade's net is read at.
KIND = "verdict"


class ResultError(ValueError):
    """The result does not hold the arm asked for, or is not a backtest result."""


def trades_of(doc: Mapping, arm: str) -> list[dict]:
    """Arm `arm`'s closed trades in result `doc`."""
    arms = doc.get("arms")
    if isinstance(arms, Mapping) and isinstance(arms.get(arm), Mapping) and "trades" in arms[arm]:
        trades = arms[arm]["trades"]
    elif isinstance(doc.get("trades"), list):
        trades = [t for t in doc["trades"] if isinstance(t, Mapping) and t.get("arm", arm) == arm]
        names = arms if isinstance(arms, Mapping) else {}
        if not trades and names and arm not in names:
            raise ResultError(f"no arm {arm!r} in the result")
    else:
        raise ResultError(f"no arm {arm!r} in the result")
    if not isinstance(trades, list):
        raise ResultError(f"arm {arm!r} has no trade list")
    return [t for t in trades if isinstance(t, Mapping)]


def net_of(trade: Mapping) -> float | None:
    net = trade.get("net")
    if isinstance(net, Mapping):
        net = net.get(KIND)
    try:
        return None if net is None else float(net)
    except (TypeError, ValueError):
        return None


def exit_session(trade: Mapping) -> str | None:
    s = trade.get("exit_session") or trade.get("session")
    return str(s)[:10] if s else None


def session_nets(trades: list[Mapping]) -> dict[str, float]:
    """Net per exit session; a trade with no net or no session is left out."""
    out: dict[str, float] = {}
    for t in trades:
        s, net = exit_session(t), net_of(t)
        if s is None or net is None:
            continue
        out[s] = round(out.get(s, 0.0) + net, 2)
    return dict(sorted(out.items()))


def window_of(doc: Mapping) -> tuple[str, str] | None:
    """The sessions the run covered, [first, last]: a Builder run's `sessions`, a study's `window`."""
    for field in ("sessions", "window"):
        w = doc.get(field)
        if isinstance(w, (list, tuple)) and len(w) == 2 and all(isinstance(x, str) for x in w):
            return str(w[0])[:10], str(w[1])[:10]
    return None


def licensed(doc: Mapping) -> bool:
    """Whether the result says it was derived from licensed vendor data."""
    return bool(doc.get("licensed")) or bool(doc.get("licence"))
