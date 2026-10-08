"""Which names the user would trade options on: the options-tradable label.

A name is options-tradable when it lists weekly options (at least WEEKLY_EXPIRIES expiries in the
next 35 days, from the nightly market-metrics fetch, scripts/fetch_iv_rank.py) and its stock trades
at least MIN_DOLLAR_VOLUME a day (the 50-session median of close x volume, from our own bars). A
cash index has no stock volume; its options are judged by the weeklies alone.

Decided 2026-10-04, and revised the same day. The first definition used tastytrade's options
liquidity rating (3 or more of 4) where the dollar volume is now. It was dropped because the rating
marks a name down for its share price: among names with weeklies, the median close was $42 at
rating 4 and $189 at rating 2, and HD, LOW, APP, GS, CAT, LLY and COST were all rated 2. The study's
hindsight view (`rated_today`) keeps the rating, because plan v2 declared it that way.

The weeklies are read from ONE day, the one the file labels most names on. The planned hysteresis
(7 of the last 10 sessions) waits for that many sessions of labels to exist. Read-only here.
"""

from __future__ import annotations

import json

from . import paths, symbols

WEEKLY_EXPIRIES = 4
MIN_DOLLAR_VOLUME = 100e6
DECLARED_RATING = 3  # plan v2's hindsight view only


def _label_day() -> tuple[dict[str, dict], str | None]:
    """(the IV-rank file's rows for the day it labels most names, that day); empty and None when
    there is no file or no labelled day."""
    try:
        doc = json.loads(paths.tastytrade_iv_rank().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, None
    # The day the most names carry an expiry count: each row is filed under tastytrade's own update
    # time, so a weekend fetch leaves a few stragglers on a later day (18 on 2026-10-03, against
    # 2,651 on 2026-10-02), and "the newest day" would read almost nothing.
    labelled = {
        d: sum(1 for v in rows.values() if v.get("expiries_35d") is not None)
        for d, rows in doc.get("days", {}).items()
    }
    if not labelled or max(labelled.values()) == 0:
        return {}, None
    day = max(labelled, key=lambda d: (labelled[d], d))
    return doc["days"][day], day


# How old a name's own reading may be and still label it. Since 2026-10-07 the nightly fetch skips
# names judged illiquid (liquidity.py), so no single day labels every name any more; each name is
# read from its own latest reading instead, and one older than this is no reading at all.
MAX_READING_AGE_DAYS = 10


def latest_rows() -> tuple[dict[str, dict], str | None]:
    """(each name's most recent row that carries an expiry count, the newest day read). Rows more
    than MAX_READING_AGE_DAYS before the newest day are left out."""
    from datetime import date, timedelta

    try:
        doc = json.loads(paths.tastytrade_iv_rank().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, None
    days = sorted(
        d
        for d, rows in (doc.get("days") or {}).items()
        if any(v.get("expiries_35d") is not None for v in rows.values())
    )
    if not days:
        return {}, None
    oldest = (date.fromisoformat(days[-1]) - timedelta(days=MAX_READING_AGE_DAYS)).isoformat()
    out: dict[str, dict] = {}
    for d in days:
        if d < oldest:
            continue
        for s, v in doc["days"][d].items():
            if v.get("expiries_35d") is not None:
                out[s] = v
    return out, days[-1]


def weeklies() -> tuple[set[str], str | None]:
    """(names listing weekly options on their latest reading, the newest day read)."""
    rows, day = latest_rows()
    return {s for s, v in rows.items() if (v.get("expiries_35d") or 0) >= WEEKLY_EXPIRIES}, day


def tradable(symbol: str, weekly: set[str] | None, dollar_volume: float | None) -> bool | None:
    """The label for one name; None when there are no weeklies to read (no label at all)."""
    if weekly is None:
        return None
    if symbol in symbols.INDEXES:
        return symbol in weekly
    return symbol in weekly and (dollar_volume or 0.0) >= MIN_DOLLAR_VOLUME


def rated_today() -> tuple[set[str], str | None]:
    """Plan v2's hindsight view, as declared: weeklies AND a tastytrade liquidity rating of
    DECLARED_RATING or more, on the label's day. Not the watchlist's label (see the module note)."""
    rows, day = _label_day()
    names = {
        s
        for s, v in rows.items()
        if (v.get("expiries_35d") or 0) >= WEEKLY_EXPIRIES
        and (v.get("liquidity_rating") or 0) >= DECLARED_RATING
    }
    return names, day
