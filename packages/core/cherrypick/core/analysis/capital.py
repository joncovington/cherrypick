"""Capital for an arm, two ways: the risk it was observed to carry, and an allocation a person declares.

docs/strategy-analysis-plan.md, Phase 0. Return and drawdown mean little as dollars alone: $3,000 is
a disaster for an arm risking $500 and a rounding error for one risking $50,000. Two denominators,
because they answer different questions:

- **Observed peak risk** (`peak_risk`) -- the most money the arm had at risk at once, from the
  per-trade `capital` `core.ledgers` records (each position's max loss). It needs no declaration,
  applies to all of history, and follows a change of size on its own, because it is what the arm
  actually did. It answers "how hard was the money worked".
- **Declared allocation** (`load_allocations`, `allocation_reading`) -- the account size a person
  assigns an arm, dated, in `<home>/config/arm_capital.json`. It answers "what did this do to the
  account", and it is the only one a percentage return or a capital-to-drawdown multiple can be
  read against. It is dated because arm sizes change: one number for all of history would
  misstate every session before the last change.

Neither is ever invented. A session before the first declaration is counted as uncovered, not
filled with the nearest figure; a ledger whose rows carry no capital (flies records none per trade:
a legged book's risk depends on completion) reports its peak as unknown, with the reason.

The allocation file lives beside the module configs but is not one: no loop reads it, and nothing
in it changes what a module does. Its shape:

    {"<module>": {"<arm>": [{"from": "YYYY-MM-DD", "capital": 25000, "note": "why"}, ...]}}

A malformed file is refused whole, with the reason, rather than half-applied.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path

from cherrypick.core import home
from cherrypick.core.metrics import max_drawdown

ALLOCATION_FILE = "arm_capital.json"


def allocation_path() -> Path:
    """Where the declared allocations live: ``<home>/config/arm_capital.json``."""
    return home.config_dir() / ALLOCATION_FILE


# --------------------------------------------------------------------------- observed peak risk
def _ts(value) -> datetime | None:
    """An ISO timestamp, or None when it is absent or not one."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _comparable(a: datetime, b: datetime) -> bool:
    return (a.tzinfo is None) == (b.tzinfo is None)


def peak_risk(records: Sequence[Mapping]) -> dict:
    """The most capital an arm had at risk at once, over the records given.

    Each record's `capital` is its position's max loss. How positions are known to overlap depends
    on what the ledger records:

    - a multi-day position (`entry_session` set) is at risk on every session from its entry to its
      close, so on each session the open positions' capital is summed;
    - a same-session position with entry and exit times (MEIC's condors) is at risk between them,
      so a session is swept at each entry, and two condors that never overlapped are never summed;
    - a same-session position with neither is counted as open all session (`untimed`), which can
      only overstate the peak, never hide it.

    `basis` says which of those applied. Records without capital are left out and counted
    (`capital_known` of `trades`); with none at all the peak is None, with the reason."""
    trades = [r for r in records if r.get("net_pnl") is not None]
    known = [r for r in trades if r.get("capital")]
    out = {
        "peak": None,
        "session": None,
        "at": None,
        "trades": len(trades),
        "capital_known": len(known),
        "untimed": 0,
        "basis": None,
        "withheld": None,
    }
    if not known:
        out["withheld"] = "no trade in this window records its capital at risk"
        return out

    spanning = [r for r in known if r.get("entry_session")]
    same_day = [r for r in known if not r.get("entry_session")]
    by_session: dict[str, list[Mapping]] = {}
    for r in same_day:
        by_session.setdefault(r.get("session") or "", []).append(r)

    def spanning_on(day: str) -> float:
        return sum(r["capital"] for r in spanning if r["entry_session"] <= day <= (r.get("session") or day))

    days = {r["entry_session"] for r in spanning} | set(by_session)
    days.discard("")
    timed_any = untimed = 0
    best = (0.0, None, None)
    for day in sorted(days):
        base = spanning_on(day)
        rows = by_session.get(day, [])
        timed, flat = [], 0.0
        for r in rows:
            start, end = _ts(r.get("entry_time")), _ts(r.get("exit_time"))
            if start is not None and end is not None and _comparable(start, end) and end >= start:
                timed.append((start, end, r["capital"]))
            else:
                flat += r["capital"]
                untimed += 1
        timed_any += len(timed)
        # Swept at each entry: exposure only rises when a position opens. A position that closed at
        # the instant another opened is off the book.
        day_peak, day_at = 0.0, None
        for t, _, _ in timed:
            open_now = sum(c for s, e, c in timed if _comparable(s, t) and s <= t < e)
            if open_now > day_peak:
                day_peak, day_at = open_now, t
        total = base + flat + day_peak
        if total > best[0]:
            best = (total, day, day_at.isoformat() if day_at is not None else None)

    out.update(
        peak=round(best[0], 2) if best[1] is not None else None,
        session=best[1],
        at=best[2],
        untimed=untimed,
        basis=(
            "mixed"
            if spanning and same_day
            else "session"
            if spanning
            else "intraday"
            if timed_any
            else "session"
        ),
    )
    return out


# --------------------------------------------------------------------------- declared allocations
class AllocationError(ValueError):
    """The allocation file is malformed. Raised whole: a half-applied file is worse than none."""


def load_allocations(path: Path | None = None) -> dict:
    """{module: {arm: [{"from", "capital", "note"}, ...] oldest first}}, or {} when there is no file.

    Raises `AllocationError` naming the first fault: not JSON, a date that is not a date, a capital
    that is not a positive number, or two declarations on one date for one arm."""
    p = path or allocation_path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AllocationError(f"{p.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise AllocationError(f"{p.name}: the top level must map modules to arms")
    out: dict = {}
    for module, arms in raw.items():
        if module.startswith("_"):  # a `_note` key, the suite's comment convention
            continue
        if not isinstance(arms, dict):
            raise AllocationError(f"{p.name}: {module} must map arms to lists of declarations")
        for arm, entries in arms.items():
            where = f"{p.name}: {module}.{arm}"
            if not isinstance(entries, list) or not entries:
                raise AllocationError(f"{where} must be a non-empty list of declarations")
            clean = []
            for e in entries:
                if not isinstance(e, dict):
                    raise AllocationError(f"{where}: each declaration is an object")
                try:
                    start = date.fromisoformat(str(e.get("from"))).isoformat()
                except ValueError as exc:
                    raise AllocationError(f"{where}: 'from' {e.get('from')!r} is not a date") from exc
                cap = e.get("capital")
                if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap <= 0:
                    raise AllocationError(f"{where}: 'capital' {cap!r} is not a positive number")
                clean.append({"from": start, "capital": float(cap), "note": e.get("note")})
            clean.sort(key=lambda e: e["from"])
            dates = [e["from"] for e in clean]
            if len(set(dates)) != len(dates):
                raise AllocationError(f"{where}: two declarations share a date")
            out.setdefault(module, {})[arm] = clean
    return out


def _in_force(declarations: Sequence[Mapping], session: str) -> float | None:
    """The capital declared for `session`: the latest declaration dated on or before it."""
    current = None
    for d in declarations:
        if d["from"] <= session:
            current = d["capital"]
    return current


def allocation_reading(dated: Sequence[tuple[str, float]], declarations: Sequence[Mapping] | None) -> dict:
    """Return and drawdown against the allocation declared for each session.

    Each session's net is divided by the allocation in force that session, so a change of size
    mid-window is honoured session by session rather than averaged. Sessions before the first
    declaration are `uncovered`: they join neither figure, and the reading says how many there were.
    `capital_over_max_drawdown` is the latest allocation over the dollar max drawdown of the covered
    sessions -- the "capital is N times the worst drawdown" figure, given as a number, never judged."""
    declared = list(declarations or [])
    out = {
        "declared": declared,
        "sessions": len(dated),
        "covered": 0,
        "uncovered": 0,
        "return": None,
        "max_drawdown": None,
        "max_drawdown_dollars": None,
        "current": declared[-1]["capital"] if declared else None,
        "capital_over_max_drawdown": None,
    }
    if not declared:
        return out
    returns, dollars, uncovered = [], [], 0
    for session, net in dated:
        alloc = _in_force(declared, session)
        if alloc is None:
            uncovered += 1
            continue
        returns.append(net / alloc)
        dollars.append(net)
    out.update(covered=len(returns), uncovered=uncovered)
    if not returns:
        return out
    dd_dollars = max_drawdown(dollars)
    out.update(
        **{
            "return": round(sum(returns), 6),
            "max_drawdown": round(_max_drawdown_raw(returns), 6),
            "max_drawdown_dollars": dd_dollars,
            "capital_over_max_drawdown": round(declared[-1]["capital"] / dd_dollars, 2)
            if dd_dollars > 0
            else None,
        }
    )
    return out


def _max_drawdown_raw(values: Sequence[float]) -> float:
    """`metrics.max_drawdown` without its rounding to cents, for a path of fractions."""
    running = peak = dd = 0.0
    for v in values:
        running += v
        peak = max(peak, running)
        dd = max(dd, peak - running)
    return dd
