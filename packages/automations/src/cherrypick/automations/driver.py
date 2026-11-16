"""The automation driver: arms replayed over stored history through the engine paper runs.

Every stored minute of a session, in paper's order: open positions are asked `engine.manage` and closed
at that minute's quotes when an exit is due, then the arms whose entry window holds the minute are
decided together through `paper.decide_all` (arm-name order, a later arm seeing an earlier one's
entry), each on a snapshot built by `snapshot.build` from the stored day exactly as `feed.snapshot_for`
builds one from the stream cache: the same reads (`feed.reads`), the same expiries (`feed.expiries_for`),
the same trail, earlier sessions, daily bars, vol index closes and events. A position still open at its
expiry's close settles at that session's close (`engine.settle_position`). So the engine cannot tell a
backtest from paper, and a backtest of an exit, a signal or a gate is the rule paper trades.

What is not here: choosing the store (the source registry), the licence, and where results go (the
backtest package's script); the fill model is the engine's, so a result's money is paper's arithmetic.
Two kinds of history: `StoreHistory` (the recorder's schema, sampled through the session, same-day
expiries) and `DailyChainHistory` (end-of-day chains, one tick a session at 15:59, positions held across
sessions and settled at their expiry's close). On the end-of-day one an arm whose entry window does not
hold that tick counts `needs_intraday` for the session: the source cannot price another time.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from cherrypick.core import calendar as cal
from cherrypick.core import volindex

from . import arms as arms_mod
from . import engine, feed, paper
from .history import StoreHistory
from .model import OpenPosition, Snapshot
from .snapshot import build


def _close_minute(day: date) -> int:
    h, m = cal.session_close_hhmm(day).split(":")
    return int(h) * 60 + int(m)


@dataclass
class Book:
    """What the driver holds across sessions: entries by arm, open positions, finished trades."""

    entries: dict[str, list[str]] = field(default_factory=dict)
    open: list[engine.Position] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    why: dict[str, Counter] = field(default_factory=dict)

    def open_positions(self) -> list[OpenPosition]:
        return [OpenPosition(p.arm, p.entry.session, p.entry.expiry) for p in self.open]


def snapshot_at(
    arms: list[dict],
    history: StoreHistory,
    session: str,
    minute: int,
    book: Book,
    *,
    events=None,
    vix_daily: dict[str, float] | None = None,
    expiries: list[str] | None = None,
    volumes: dict | None = None,
) -> Snapshot | None:
    """The snapshot `arms` read at `minute`, from the stored day; None when the store has no spot there
    (paper would refuse the tick as `no_spot`) or no close before the session (`no_prev_close`)."""
    day = date.fromisoformat(session)
    spot = history.spot_at(session, minute)
    prev = history.prev_close(session)
    if spot is None or prev is None:
        return None
    needs = [feed.reads(a) for a in arms]
    listed = history.listed(session, minute)
    wanted = set(expiries or [])
    for a in arms:
        wanted |= set(feed.expiries_for(a, day, listed))
    quotes = {e: rows for e in sorted(wanted) if (rows := history.rows(session, e, minute))}
    trail = history.trail(session, minute)
    day_open = history.day_open(session, minute)
    vix_prior = volindex.before(vix_daily or {}, session, 1)
    return build(
        session=session,
        minute=minute,
        symbol=history.symbol,
        spot=spot,
        prev_close=prev,
        day_open=day_open if day_open is not None else spot,
        quotes=quotes,
        trail=trail,
        closes=history.closes_before(session),
        vix=history.vix_at(session, minute) if any(n["vix"] for n in needs) else None,
        vix_prev_close=vix_prior[-1] if vix_prior and any(n["vix"] for n in needs) else None,
        events=events,
        entries={k: list(v) for k, v in book.entries.items()},
        open_positions=book.open_positions(),
        close_minute=_close_minute(day),
        with_gex=any(n["gex"] for n in needs),
        daily=history.daily_before(session) if any(n["daily"] for n in needs) else None,
        prior=history.prior_trails(session, arms_mod.PRIOR_SESSIONS)
        if any(n["prior"] for n in needs)
        else None,
        vol_closes=feed.vol_history(history.symbol, day, max(n["vol_closes"] for n in needs) if needs else 0),
        volumes={s: (volumes or {}).get(s, {}) for a in arms for s in arms_mod.volume_symbols(a)},
    )


def _manage(history, session, minute, book, arms, *, events, vix_daily, costs, volumes=None) -> None:
    for pos in list(book.open):
        if pos.entry.expiry < session:
            continue
        if pos.entry.session == session and minute <= pos.entry.minute:
            continue
        arm = arms[pos.arm]
        snap = snapshot_at(
            [arm],
            history,
            session,
            minute,
            book,
            events=events,
            vix_daily=vix_daily,
            expiries=[pos.entry.expiry],
            volumes=volumes,
        )
        if snap is None:
            continue
        sig = engine.manage(pos, snap)
        if sig is None:
            continue
        trade = engine.close_position(pos, sig[0], snap, costs=costs)
        if trade is not None:  # a close whose legs do not price waits for a minute that does, as paper's
            book.open.remove(pos)
            book.trades.append(trade)


def _settle(history, session, book) -> None:
    """Settle what expires today, and anything whose expiry passed on a session the history skipped
    (an end-of-day source sparse in its early years), at that expiry's own close."""
    for pos in list(book.open):
        if pos.entry.expiry <= session:
            price = history.settle(pos.entry.expiry)
            if price is not None:
                book.open.remove(pos)
                book.trades.append(engine.settle_position(pos, price, pos.entry.expiry))


def run(
    arms: dict[str, dict],
    history: StoreHistory,
    sessions: list[str],
    *,
    events=None,
    vix_daily: dict[str, float] | None = None,
    costs: dict | None = None,
    book: Book | None = None,
    volumes: dict | None = None,
) -> Book:
    """Replay `arms` (enabled, on the history's symbol) over `sessions`. Returns the book: the trades,
    what is still open, and per arm the count of each session's last reason (or `entered`)."""
    book = book or Book()
    if vix_daily is None:
        vix_daily = volindex.closes("VIX")
    # Keyed by the arm's own name, which the engine records on every entry and position.
    mine = {
        a["name"]: a
        for a in arms.values()
        if a.get("enabled", True) and a["symbol"].upper() == history.symbol
    }
    for session in sessions:
        day = date.fromisoformat(session)
        todays = {n: a for n, a in mine.items() if day.weekday() in a["weekdays"]}
        pending = set(todays)
        last: dict[str, str] = {}
        if not history.minutes(session):
            # The source holds nothing that session (Dolt before 2025 is about three days a week).
            for n in todays:
                last[n] = "no_data"
        elif getattr(history, "close_only", False):
            # An end-of-day source prices one instant a session: an arm whose window does not hold it
            # cannot be backtested here, and is counted so rather than entered at another time.
            for n in sorted(pending):
                lo, hi = feed.entry_window(todays[n])
                if not any(lo <= m <= hi for m in history.minutes(session)):
                    pending.discard(n)
                    last[n] = "needs_intraday"
        for minute in history.minutes(session):
            _manage(
                history,
                session,
                minute,
                book,
                mine,
                events=events,
                vix_daily=vix_daily,
                costs=costs,
                volumes=volumes,
            )
            due = {
                n: todays[n]
                for n in sorted(pending)
                if feed.entry_window(todays[n])[0] <= minute <= feed.entry_window(todays[n])[1]
            }
            if not due:
                continue
            snap = snapshot_at(
                list(due.values()),
                history,
                session,
                minute,
                book,
                events=events,
                vix_daily=vix_daily,
                volumes=volumes,
            )
            if snap is None:
                for n in due:
                    last[n] = "no_snapshot"
                continue
            for d in paper.decide_all(due, snap, costs=costs):
                if d.enter and d.entry is not None:
                    pending.discard(d.arm)
                    book.entries.setdefault(d.arm, []).append(session)
                    book.open.append(engine.Position(d.entry, due[d.arm]))
                    last[d.arm] = "entered"
                else:
                    last[d.arm] = d.reason
        _settle(history, session, book)
        for n in todays:
            book.why.setdefault(n, Counter())[last.get(n, "no_tick_in_window")] += 1
    return book
