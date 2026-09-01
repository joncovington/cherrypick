"""Short and long trends over the written fact sets — and the discipline that a trend must not
cross a measurement break.

A break is a date on which the thing being measured changed: a rule, a threshold, an exit policy.
Results either side of one are not the same experiment, so a line drawn through a break is not a
trend, it is two trends averaged into a number that describes neither. Both earnings and MEIC have
breaks within days of the current session, so this bites immediately and visibly: most windows here
will report one or two usable sessions.

That thinness is the point. A five-session trend that silently spans a policy change looks more
informative than a two-session trend that stops where the evidence stops, and is worth less.

Reads only the fact sets. It never touches a module ledger, so a trend can never disagree with the
session it was built from.
"""

from __future__ import annotations

from cherrypick.review import facts as _facts
from cherrypick.review import paths as _paths

DEFAULT_WINDOW = 5


def available_sessions() -> list[str]:
    store = _paths.data_dir()
    if not store.exists():
        return []
    return sorted(p.stem.removeprefix("eod-") for p in store.glob("eod-*.json"))


def _module_slice(session: str, module: str) -> dict | None:
    facts = _facts.read(session)
    if not facts:
        return None
    entry = (facts.get("modules") or {}).get(module)
    return entry if entry and entry.get("ok") else None


def most_recent_break(module: str, on_or_before: str) -> str | None:
    """The latest journaled break for `module` at or before a session.

    Read from the fact set rather than the ledger so a trend and the session it covers always agree
    about what the breaks were. Returns None both when a module journals no breaks and when it does
    not track them at all — the difference is preserved in the fact set's `sample.breaks`, and the
    caller reports it.
    """
    entry = _module_slice(on_or_before, module)
    if not entry:
        return None
    breaks = (entry.get("sample") or {}).get("breaks")
    if not breaks:
        return None
    past = [b for b in breaks if b <= on_or_before]
    return max(past) if past else None


def trend(module: str, end_session: str, window: int = DEFAULT_WINDOW) -> dict:
    """Aggregate `module` over the sessions up to `end_session`, stopping at its most recent break.

    Reports what it actually covered, not what it was asked for: `sessions_requested` against
    `sessions_used`, and the break it stopped at. A window that collapsed to one session says so.
    """
    sessions = [s for s in available_sessions() if s <= end_session]
    cut = most_recent_break(module, end_session)
    if cut:
        # Sessions strictly after the break: the break date itself is the day the change landed.
        eligible = [s for s in sessions if s > cut]
    else:
        eligible = sessions
    used = eligible[-window:]

    slices = [(s, _module_slice(s, module)) for s in used]
    slices = [(s, e) for s, e in slices if e]

    closed = sum(e["results"]["closed"] for _, e in slices)
    net = sum(e["results"]["net"] for _, e in slices)
    wins = sum(e["results"]["wins"] for _, e in slices)
    effective = sum((e.get("sample") or {}).get("effective_n") or 0 for _, e in slices)
    capitals = [
        (e.get("return") or {}).get("capital_at_risk")
        for _, e in slices
        if (e.get("return") or {}).get("capital_at_risk")
    ]

    tracks_breaks = None
    if slices:
        tracks_breaks = (slices[-1][1].get("sample") or {}).get("breaks") is not None

    # Per arm across the same window. This is the number the arm experiments exist to produce, and
    # a single session of it is nearly worthless -- MEIC's `open` beat both width arms on all four
    # sessions so far, which is suggestive and nothing more until the window is longer than the
    # gap between breaks.
    by_profile: dict[str, dict] = {}
    for _, entry in slices:
        for arm, g in (entry.get("by_profile") or {}).items():
            acc = by_profile.setdefault(
                arm,
                {"closed": 0, "net": 0.0, "wins": 0, "sessions": 0, "capital": 0.0, "capital_seen": False},
            )
            acc["closed"] += g["closed"]
            acc["net"] += g["net"]
            acc["wins"] += g["wins"]
            acc["sessions"] += 1
            capital = (g.get("return") or {}).get("capital_at_risk")
            if capital:
                acc["capital"] += capital
                acc["capital_seen"] = True
    for acc in by_profile.values():
        acc["net"] = round(acc["net"], 2)
        acc["win_rate"] = round(acc["wins"] / acc["closed"], 4) if acc["closed"] else None
        acc["capital_at_risk"] = round(acc["capital"], 2) if acc["capital_seen"] else None
        acc["on_max_risk"] = (
            round(acc["net"] / acc["capital"], 6) if acc["capital_seen"] and acc["capital"] else None
        )
        del acc["capital"], acc["capital_seen"]

    return {
        "module": module,
        "end_session": end_session,
        "sessions_requested": window,
        "sessions_used": len(slices),
        "sessions": [s for s, _ in slices],
        "stopped_at_break": cut,
        "tracks_breaks": tracks_breaks,
        "closed": closed,
        "net": round(net, 2),
        "wins": wins,
        "win_rate": round(wins / closed, 4) if closed else None,
        "effective_n": effective,
        "capital_at_risk": round(sum(capitals), 2) if capitals else None,
        "on_max_risk": round(net / sum(capitals), 6) if capitals else None,
        "by_profile": by_profile,
    }


def for_session(end_session: str, window: int = DEFAULT_WINDOW) -> dict:
    """Every module's trend as of one session. No suite aggregate, deliberately — see facts.build."""
    return {m: trend(m, end_session, window) for m in _facts.MODULES}
